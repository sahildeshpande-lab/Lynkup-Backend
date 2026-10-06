"""Learning Spotlight – Non-LLM Research Paper Synthesis Service.

Produces deterministic, source-grounded structured comparative notes comparing
a focal research paper with 1–3 top related papers using TF-IDF cosine similarity.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any, Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.learningspotlight.db_models.learning_content_db_model import LearningContent
from apps.learningspotlight.schemas import (
    SynthesisArticleNotes,
    SynthesisRelatedArticleNotes,
)
from apps.learningspotlight.services.semantic_scholar_adapter import (
    search_papers_v2,
)
from apps.learningspotlight.services.summarization_service import (
    count_words,
    generate_extractive_summary,
    load_paper_metadata_for_user,
    split_into_sentences,
    tokenize,
)

logger = logging.getLogger(__name__)

MAX_RELATED_PAPERS: int = 3
MIN_RELATED_PAPERS: int = 1
RELATED_SEARCH_LIMIT: int = 50
SEARCH_HTTP_OK: int = 200

_SYNTHESIS_STOPWORDS: set[str] = {
    "a", "an", "the", "and", "or", "in", "on", "at", "to", "for", "of", "with", "by",
    "from", "as", "is", "are", "was", "were", "be", "been", "being", "have", "has",
    "had", "do", "does", "did", "but", "if", "then", "else", "when", "up", "down",
    "into", "through", "after", "before", "toward", "towards", "based", "using",
    "study", "approach", "novel", "new", "faster", "easier", "development",
}


def _extract_topical_query(title: str, max_terms: int = 5) -> str:
    normalized = (
        title.replace("\u2010", " ")
        .replace("\u2013", " ")
        .replace("\u2014", " ")
        .replace(":", " ")
    )
    tokens = tokenize(normalized)
    informative = [
        w for w in tokens if len(w) > 2 and w.lower() not in _SYNTHESIS_STOPWORDS
    ]
    return " ".join(informative[:max_terms])


def _is_search_incomplete(status_code: int | None) -> bool:
    """True when related-paper search did not finish with a successful 200."""
    return status_code != SEARCH_HTTP_OK


def _searching_payload(paper_id: str) -> dict[str, Any]:
    return {
        "paper_id": paper_id,
        "content_type": "SYNTHESIS",
        "content": None,
        "searching": True,
    }


def _encode_synthesis_content(content: dict[str, Any]) -> str:
    return json.dumps(content, ensure_ascii=False)


def _decode_synthesis_content(raw: Any) -> Any:
    """Return structured content for the API; leave legacy plaintext as-is."""
    if isinstance(raw, dict):
        cleaned = dict(raw)
        cleaned.pop("common_themes_from_related_articles", None)
        return cleaned
    if not isinstance(raw, str):
        return raw
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(parsed, dict):
        parsed.pop("common_themes_from_related_articles", None)
        return parsed
    return raw


def _article_key_points(title: str, abstract: str, *, max_points: int = 3) -> list[str]:
    summary = generate_extractive_summary(title, abstract, max_words=80)
    source = summary or abstract
    points: list[str] = []
    seen: set[str] = set()
    for sentence in split_into_sentences(source):
        cleaned = sentence.strip()
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        points.append(cleaned)
        if len(points) >= max_points:
            break
    return points or ["Not provided."]


def _paper_theme_terms(paper: dict[str, Any]) -> set[str]:
    text = f"{paper.get('title') or ''} {paper.get('abstract') or ''}"
    return {
        token
        for token in tokenize(text)
        if token not in _SYNTHESIS_STOPWORDS and len(token) > 2 and not token.isdigit()
    }


def _paper_term_sets(
    original_paper: dict[str, Any],
    related_papers: list[dict[str, Any]],
) -> tuple[set[str], list[set[str]], set[str]]:
    orig_terms = _paper_theme_terms(original_paper)
    related_term_sets = [_paper_theme_terms(paper) for paper in related_papers]
    related_union = set().union(*related_term_sets) if related_term_sets else set()
    return orig_terms, related_term_sets, related_union


def _informative_sentences(paper: dict[str, Any], *, max_sentences: int = 3) -> list[str]:
    title = (paper.get("title") or "").strip()
    abstract = (paper.get("abstract") or "").strip()
    points = _article_key_points(title, abstract, max_points=max_sentences)
    return [point for point in points if point and point != "Not provided."]


def _sentence_term_set(sentence: str) -> set[str]:
    return {
        token
        for token in tokenize(sentence)
        if token not in _SYNTHESIS_STOPWORDS and len(token) > 2 and not token.isdigit()
    }


def _term_jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _dedupe_statements(statements: list[str], *, max_items: int) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for statement in statements:
        cleaned = " ".join(statement.split()).strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(cleaned)
        if len(unique) >= max_items:
            break
    return unique


def _best_overlapping_pair(
    original_sentences: list[str],
    related_sentences: list[str],
) -> tuple[str, str, float] | None:
    best_pair: tuple[str, str, float] | None = None
    best_score = 0.0
    for original in original_sentences:
        original_terms = _sentence_term_set(original)
        for related in related_sentences:
            score = _term_jaccard(original_terms, _sentence_term_set(related))
            if score > best_score:
                best_score = score
                best_pair = (original, related, score)
    if best_pair is None or best_score <= 0.0:
        return None
    return best_pair


def _similarity_statements(
    original_paper: dict[str, Any],
    related_papers: list[dict[str, Any]],
    *,
    max_items: int = 4,
) -> list[str]:
    """Extract overlapping claims from the original and related abstracts."""
    original_sentences = _informative_sentences(original_paper)
    orig_terms = _paper_theme_terms(original_paper)
    statements: list[str] = []

    for paper in related_papers:
        related_sentences = _informative_sentences(paper)
        pair = _best_overlapping_pair(original_sentences, related_sentences)
        if pair is not None:
            original_sent, related_sent, _score = pair
            if original_sent not in statements:
                statements.append(original_sent)
            if related_sent not in statements:
                statements.append(related_sent)
            continue
        for sentence in related_sentences:
            if _sentence_term_set(sentence) & orig_terms:
                statements.append(sentence)

    if not statements:
        related_union = set().union(
            *(_paper_theme_terms(paper) for paper in related_papers)
        ) if related_papers else set()
        for sentence in original_sentences:
            if _sentence_term_set(sentence) & related_union:
                statements.append(sentence)

    return _dedupe_statements(statements, max_items=max_items)


def _is_distinctive(sentence: str, other_terms: set[str]) -> bool:
    terms = _sentence_term_set(sentence)
    if not terms:
        return False
    unique = terms - other_terms
    shared = terms & other_terms
    return len(unique) > len(shared)


def _lowest_overlap_sentence(
    sentences: list[str],
    other_terms: set[str],
) -> str | None:
    ranked: list[tuple[float, str]] = []
    for sentence in sentences:
        terms = _sentence_term_set(sentence)
        if not terms:
            continue
        ranked.append((_term_jaccard(terms, other_terms), sentence))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0])
    return ranked[0][1]


def _difference_statements(
    original_paper: dict[str, Any],
    related_papers: list[dict[str, Any]],
    *,
    max_items: int = 6,
) -> list[str]:
    """Extract claims that are distinctive to the original or a related paper."""
    orig_terms, _related_term_sets, related_union = _paper_term_sets(
        original_paper, related_papers
    )
    original_sentences = _informative_sentences(original_paper)
    statements: list[str] = [
        sentence
        for sentence in original_sentences
        if _is_distinctive(sentence, related_union)
    ]

    for paper in related_papers:
        related_sentences = _informative_sentences(paper)
        distinctive = [
            sentence
            for sentence in related_sentences
            if _is_distinctive(sentence, orig_terms)
        ]
        if distinctive:
            statements.extend(distinctive)
            continue
        fallback = _lowest_overlap_sentence(related_sentences, orig_terms)
        if fallback:
            statements.append(fallback)

    if not statements:
        original_fallback = _lowest_overlap_sentence(original_sentences, related_union)
        if original_fallback:
            statements.append(original_fallback)

    return _dedupe_statements(statements, max_items=max_items)


def compute_tfidf_cosine_similarity(
    doc1: str,
    doc2: str,
    *,
    background_corpus: Sequence[str] | None = None,
) -> float:
    """Compute deterministic TF-IDF cosine similarity between two text documents."""
    t1 = tokenize(doc1)
    t2 = tokenize(doc2)
    if not t1 or not t2:
        return 0.0

    all_docs = [t1, t2]
    if background_corpus:
        for bg in background_corpus:
            tokens = tokenize(bg)
            if tokens:
                all_docs.append(tokens)

    n_docs = len(all_docs)
    df: dict[str, int] = {}
    for doc_tokens in all_docs:
        for term in set(doc_tokens):
            df[term] = df.get(term, 0) + 1

    idf: dict[str, float] = {
        term: math.log((n_docs + 1.0) / (count + 1.0)) + 1.0
        for term, count in df.items()
    }

    # Vector 1
    v1: dict[str, float] = {}
    for term in t1:
        v1[term] = (t1.count(term) / len(t1)) * idf.get(term, 1.0)

    # Vector 2
    v2: dict[str, float] = {}
    for term in t2:
        v2[term] = (t2.count(term) / len(t2)) * idf.get(term, 1.0)

    vocab = set(v1.keys()).union(set(v2.keys()))
    dot = sum(v1.get(w, 0.0) * v2.get(w, 0.0) for w in vocab)
    norm1 = math.sqrt(sum(val**2 for val in v1.values()))
    norm2 = math.sqrt(sum(val**2 for val in v2.values()))

    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0

    return dot / (norm1 * norm2)


def _format_sentence(text: str) -> str:
    cleaned = " ".join(text.strip().split())
    if not cleaned:
        return ""
    cleaned = cleaned[0].upper() + cleaned[1:] if len(cleaned) > 1 else cleaned.upper()
    if not cleaned.endswith((".", "!", "?")):
        cleaned += "."
    return cleaned


def _normalize_point_for_summary(point: str) -> str:
    s = _format_sentence(point)
    lower = s.lower()
    prefixes = [
        ("we propose ", "the authors propose "),
        ("we introduce ", "the authors introduce "),
        ("we present ", "the authors present "),
        ("we show ", "the authors show "),
        ("we find ", "the authors find "),
        ("we evaluate ", "the authors evaluate "),
        ("we investigate ", "the authors investigate "),
        ("we develop ", "the authors develop "),
        ("we demonstrate ", "the authors demonstrate "),
    ]
    for prefix, repl in prefixes:
        if lower.startswith(prefix):
            return repl + s[len(prefix):]
    return s


_BOILERPLATE_PREFIXES: tuple[str, ...] = (
    "this is a summary",
    "this is a talk",
    "this talk",
    "this paper is",
    "this article is",
    "based on",
    "presented at",
)

_LEADING_CONNECTIVES: tuple[str, ...] = (
    "additionally, ",
    "moreover, ",
    "furthermore, ",
    "however, ",
    "in addition, ",
    "meanwhile, ",
    "notably, ",
    "importantly, ",
)


def _lower_first(text: str) -> str:
    """Lowercase only the first character of a sentence fragment."""
    cleaned = text.strip()
    if len(cleaned) <= 1:
        return cleaned.lower()
    return cleaned[0].lower() + cleaned[1:]


def _join_theme_terms(terms: list[str], *, max_terms: int = 3) -> str:
    """Join existing theme terms into 'a, b, and c' phrasing."""
    selected = [t.strip() for t in terms[:max_terms] if t and t.strip()]
    if not selected:
        return ""
    if len(selected) == 1:
        return selected[0]
    if len(selected) == 2:
        return f"{selected[0]} and {selected[1]}"
    return f"{', '.join(selected[:-1])}, and {selected[-1]}"


def _to_third_person(sentence: str) -> str:
    """Rewrite a leading 'We ...' / 'Our ...' into third person."""
    cleaned = sentence.strip()
    lower = cleaned.lower()
    if lower.startswith("we "):
        return "the authors " + cleaned[3:]
    if lower.startswith("our "):
        return "the authors' " + cleaned[4:]
    return cleaned


def _strip_leading_connective(sentence: str) -> str:
    """Remove redundant leading discourse markers."""
    cleaned = sentence.strip()
    lower = cleaned.lower()
    for conn in _LEADING_CONNECTIVES:
        if lower.startswith(conn):
            return cleaned[len(conn):]
    return cleaned


def _narrative_form(sentence: str) -> str:
    """Convert an extractive sentence into a takeaway-ready sentence."""
    return _format_sentence(_to_third_person(sentence))


def _sentence_content_score(sentence: str, theme_terms: set[str]) -> float:
    """Score an existing sentence using only deterministic source text signals."""
    cleaned = sentence.strip()
    word_count = len(cleaned.split())
    if word_count < 4:
        return -1.0

    terms = _sentence_term_set(cleaned)
    if not terms:
        return -1.0

    score = float(len(terms))
    score += 2.0 * len(terms & theme_terms)

    if cleaned.lower().startswith(_BOILERPLATE_PREFIXES):
        score -= 3.0

    if word_count > 45:
        score -= 1.0

    return score


def _pick_best_sentence(
    candidates: list[str],
    theme_terms: set[str],
    *,
    exclude: set[str] | None = None,
) -> str | None:
    """Select the strongest existing source sentence without fabricating content."""
    exclude_keys = {item.strip().lower() for item in (exclude or set())}
    scored: list[tuple[float, str]] = []

    for candidate in candidates:
        cleaned = candidate.strip()
        if not cleaned or cleaned.lower() in exclude_keys:
            continue

        score = _sentence_content_score(cleaned, theme_terms)
        if score < 0:
            continue

        scored.append((score, cleaned))

    if scored:
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]

    remaining = [
        candidate.strip()
        for candidate in candidates
        if candidate.strip() and candidate.strip().lower() not in exclude_keys
    ]
    return max(remaining, key=len) if remaining else None


def _information_terms(text: str) -> set[str]:
    """Return meaningful terms used for deterministic repetition detection."""
    return {
        word.lower()
        for word in tokenize(text)
        if len(word) > 3
        and word.lower() not in _SYNTHESIS_STOPWORDS
        and not word.isdigit()
    }


def _is_duplicate_information(
    candidate: str,
    existing: Sequence[str],
    *,
    overlap_threshold: float = 0.60,
    candidate_coverage_threshold: float = 0.75,
) -> bool:
    """Detect substantial repeated information, not only exact duplicate text.

    This is intentionally conservative. If most meaningful terms in a new
    candidate are already present in an earlier takeaway sentence/source point,
    the candidate is rejected rather than shown to the user again.
    """
    candidate_terms = _information_terms(candidate)
    if not candidate_terms:
        return True

    for previous in existing:
        previous_terms = _information_terms(previous)
        if not previous_terms:
            continue

        intersection = candidate_terms & previous_terms
        union = candidate_terms | previous_terms

        overlap = len(intersection) / len(union)
        candidate_coverage = len(intersection) / len(candidate_terms)

        if (
            overlap >= overlap_threshold
            or candidate_coverage >= candidate_coverage_threshold
        ):
            return True

    return False


def _sentence_information_density(sentence: str) -> float:
    """Ratio of informative (non-stopword, non-trivial) tokens to total tokens."""
    tokens = tokenize(sentence)
    if not tokens:
        return 0.0

    informative = [
        token
        for token in tokens
        if token not in _SYNTHESIS_STOPWORDS
        and len(token) > 2
        and not token.isdigit()
    ]
    return len(informative) / len(tokens)


def _score_takeaway_candidate(
    sentence: str,
    *,
    position: int,
    total: int,
    theme_terms: set[str],
    title_terms: set[str],
) -> float:
    """Deterministically score a similarity/difference candidate.

    Signals are derived only from the supplied key-similarity and
    key-difference statements:
      - information density
      - overlap with the original article's terms
      - position in the extracted statements
      - boilerplate/length penalties
    """
    cleaned = sentence.strip()
    word_count = len(cleaned.split())
    if word_count < 4:
        return -1.0

    terms = _sentence_term_set(cleaned)
    if not terms:
        return -1.0

    density = _sentence_information_density(cleaned)
    theme_overlap = len(terms & theme_terms)
    title_overlap = len(terms & title_terms)
    position_score = 1.0 - (position / max(total, 1))

    score = (
        density * 3.0
        + theme_overlap * 2.0
        + title_overlap * 1.5
        + position_score
    )

    if cleaned.lower().startswith(_BOILERPLATE_PREFIXES):
        score -= 3.0
    if word_count > 45:
        score -= 1.0

    return score


def _remove_cross_field_duplicates(
    similarities: list[str],
    differences: list[str],
) -> tuple[list[str], list[str]]:
    """Remove materially repeated statements across similarity/difference fields."""
    clean_similarities: list[str] = []
    clean_differences: list[str] = []

    for statement in similarities:
        cleaned = " ".join(statement.split()).strip()
        if not cleaned:
            continue
        if _is_duplicate_information(
            cleaned,
            clean_similarities,
            overlap_threshold=0.60,
            candidate_coverage_threshold=0.75,
        ):
            continue
        clean_similarities.append(cleaned)

    for statement in differences:
        cleaned = " ".join(statement.split()).strip()
        if not cleaned:
            continue
        if _is_duplicate_information(
            cleaned,
            clean_similarities,
            overlap_threshold=0.45,
            candidate_coverage_threshold=0.60,
        ):
            continue
        if _is_duplicate_information(
            cleaned,
            clean_differences,
            overlap_threshold=0.60,
            candidate_coverage_threshold=0.75,
        ):
            continue
        clean_differences.append(cleaned)

    return clean_similarities, clean_differences


def _build_overall_takeaway(
    orig_title: str,
    key_similarities: list[str],
    key_differences: list[str],
) -> str:
    """Build the overall takeaway from key similarities and key differences.

    The takeaway intentionally uses the comparative synthesis output rather
    than the original article abstract/key points.

    Source priority:
      1. Key similarities -> shared findings/areas of agreement.
      2. Key differences -> distinctive findings, scope, or approach.

    No related article is queried directly here and no new factual claim is
    invented. The function selects existing source-grounded statements,
    removes repeated information, and adds only deterministic connective text.
    """
    invalid_values = {
        "",
        "not provided.",
        "not provided",
        "string",
        "none",
        "null",
    }

    valid_similarities = _dedupe_statements(
        [
            item.strip()
            for item in key_similarities
            if item
            and item.strip()
            and item.strip().lower() not in invalid_values
        ],
        max_items=max(len(key_similarities), 1),
    )

    valid_differences = _dedupe_statements(
        [
            item.strip()
            for item in key_differences
            if item
            and item.strip()
            and item.strip().lower() not in invalid_values
        ],
        max_items=max(len(key_differences), 1),
    )

    if not valid_similarities and not valid_differences:
        return (
            f'The focal article "{orig_title}" did not yield enough '
            "comparative information for an overall takeaway."
        )

    # The original article title is used only as a relevance signal. The
    # actual takeaway content comes from key similarities/differences.
    title_terms = _sentence_term_set(orig_title)

    all_source_statements = valid_similarities + valid_differences
    all_terms = set().union(
        *(_sentence_term_set(item) for item in all_source_statements)
    ) if all_source_statements else set()

    scored_similarities = [
        (
            _score_takeaway_candidate(
                statement,
                position=index,
                total=len(valid_similarities),
                theme_terms=all_terms,
                title_terms=title_terms,
            ),
            statement,
        )
        for index, statement in enumerate(valid_similarities)
    ]
    scored_differences = [
        (
            _score_takeaway_candidate(
                statement,
                position=index,
                total=len(valid_differences),
                theme_terms=all_terms,
                title_terms=title_terms,
            ),
            statement,
        )
        for index, statement in enumerate(valid_differences)
    ]

    scored_similarities = [
        item for item in scored_similarities if item[0] >= 0
    ]
    scored_differences = [
        item for item in scored_differences if item[0] >= 0
    ]

    scored_similarities.sort(key=lambda item: item[0], reverse=True)
    scored_differences.sort(key=lambda item: item[0], reverse=True)

    selected_similarity: str | None = None
    selected_difference: str | None = None

    # Prefer one strong similarity and one strong difference so the takeaway
    # actually represents both comparative dimensions.
    if scored_similarities:
        selected_similarity = scored_similarities[0][1]

    if scored_differences:
        for _score, candidate in scored_differences:
            if (
                selected_similarity is None
                or not _is_duplicate_information(
                    candidate,
                    [selected_similarity],
                )
            ):
                selected_difference = candidate
                break

    # If there is no usable difference, select a second non-redundant
    # similarity. Likewise, if there is no similarity, use differences.
    selected_additional: str | None = None

    if selected_similarity is not None and selected_difference is None:
        for _score, candidate in scored_similarities[1:]:
            if not _is_duplicate_information(candidate, [selected_similarity]):
                selected_additional = candidate
                break

    if selected_similarity is None and selected_difference is not None:
        for _score, candidate in scored_differences[1:]:
            if not _is_duplicate_information(candidate, [selected_difference]):
                selected_additional = candidate
                break

    sentences: list[str] = []

    if selected_similarity is not None:
        similarity_clause = _lower_first(
            _strip_leading_connective(
                _to_third_person(selected_similarity)
            )
        )
        similarity_sentence = (
            f"Key similarities across the research indicate that "
            f"{similarity_clause}"
        )
        sentences.append(_format_sentence(similarity_sentence))

    if selected_difference is not None:
        difference_clause = _lower_first(
            _strip_leading_connective(
                _to_third_person(selected_difference)
            )
        )
        difference_sentence = (
            f"Key differences are reflected in that {difference_clause}"
        )

        if not _is_duplicate_information(
            difference_sentence,
            sentences,
        ):
            sentences.append(_format_sentence(difference_sentence))

    if selected_additional is not None and len(sentences) < 3:
        additional_clause = _lower_first(
            _strip_leading_connective(
                _to_third_person(selected_additional)
            )
        )

        if selected_similarity is None:
            prefix = "The comparison further shows that "
        else:
            prefix = "The comparison also shows that "

        additional_sentence = f"{prefix}{additional_clause}"

        if not _is_duplicate_information(
            additional_sentence,
            sentences,
        ):
            sentences.append(_format_sentence(additional_sentence))

    if not sentences:
        return (
            f'The focal article "{orig_title}" did not yield enough '
            "comparative information for an overall takeaway."
        )

    return " ".join(sentences[:3])


def build_structured_synthesis_notes(
    original_paper: dict[str, Any],
    related_papers: list[dict[str, Any]],
) -> dict[str, Any]:
    """Generate structured Article Synthesis notes from source titles and abstracts.

    The public synthesis response intentionally contains only the source-grounded
    article notes, key similarities, key differences, and overall takeaway.
    Low-level theme-token extraction is not exposed as an API field.
    """
    orig_title = (original_paper.get("title") or "Original Article").strip()
    orig_abstract = (original_paper.get("abstract") or "").strip()
    orig_key_points = _article_key_points(orig_title, orig_abstract)
    key_similarities = _similarity_statements(original_paper, related_papers)
    key_differences = _difference_statements(original_paper, related_papers)

    # Similarity and difference statements can independently be valid while
    # still expressing the same underlying information. Remove that semantic
    # repetition before building the final takeaway.
    key_similarities, key_differences = _remove_cross_field_duplicates(
        key_similarities,
        key_differences,
    )

    related_articles = [
        SynthesisRelatedArticleNotes(
            label=f"Related Article {idx}",
            title=(paper.get("title") or f"Related Article {idx}").strip(),
            key_points=_article_key_points(
                (paper.get("title") or "").strip(),
                (paper.get("abstract") or "").strip(),
            ),
        )
        for idx, paper in enumerate(related_papers, start=1)
    ]

    overall_takeaway = _build_overall_takeaway(
        orig_title=orig_title,
        key_similarities=key_similarities,
        key_differences=key_differences,
    )

    # Build the public payload explicitly so deprecated theme-token fields
    # cannot leak back into the API response.
    content = {
        "heading": "Article Synthesis",
        "original_article": SynthesisArticleNotes(
            title=orig_title,
            key_points=orig_key_points,
        ).model_dump(),
        "related_articles": [
            article.model_dump() if hasattr(article, "model_dump") else article
            for article in related_articles
        ],
        "key_similarities": key_similarities,
        "key_differences": key_differences,
        "overall_takeaway": overall_takeaway,
    }
    return content


class PaperSynthesisService:
    """Service handling multi-paper comparative synthesis and persistence."""

    @classmethod
    async def get_or_create_synthesis(
        cls,
        session: AsyncSession,
        *,
        user_id: UUID,
        paper_id: str,
        title: str | None = None,
        abstract: str | None = None,
    ) -> tuple[dict[str, Any] | None, bool, str | None]:
        """Fetch existing synthesis or generate and persist new structured notes.

        Returns
        -------
        tuple[dict | None, bool, str | None]
            (synthesis_data_dict, is_newly_generated, error_message_if_any)

            When related-paper search has not finished (timeout, 429, 5xx),
            ``synthesis_data_dict`` includes ``searching=True`` and no error.
            ``status=False`` is reserved for a completed search that found
            nothing usable.
        """
        # 1. Check for existing synthesis
        stmt = select(LearningContent).where(
            LearningContent.user_id == user_id,
            LearningContent.paper_id == paper_id,
            LearningContent.content_type == "SYNTHESIS",
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing is not None:
            logger.info(
                "[synthesis] Returning cached synthesis user_id=%s paper_id=%s",
                user_id,
                paper_id,
            )
            return (
                {
                    "paper_id": existing.paper_id,
                    "content_type": existing.content_type,
                    "content": _decode_synthesis_content(existing.content),
                    "source_papers": existing.source_papers or [existing.paper_id],
                    "searching": False,
                },
                False,
                None,
            )

        # 2. If title or abstract are missing, attempt local retrieval
        if not abstract or not title:
            local_title, local_abstract = await load_paper_metadata_for_user(
                session, user_id, paper_id
            )
            if not title:
                title = local_title
            if not abstract:
                abstract = local_abstract

        if not title or not title.strip():
            return None, False, "Paper title is required to search for related research."
        if not abstract or not abstract.strip():
            return None, False, "Paper abstract is required to perform synthesis."

        original_doc = f"{title.strip()}. {abstract.strip()}"
        original_paper_dict = {
            "paper_id": paper_id,
            "title": title.strip(),
            "abstract": abstract.strip(),
        }

        # 3. Find related candidate papers via Semantic Scholar adapter
        search_payload, status_code = await search_papers_v2(
            query=title, limit=RELATED_SEARCH_LIMIT
        )
        if _is_search_incomplete(status_code):
            logger.info(
                "[synthesis] Related-paper search still in progress user_id=%s paper_id=%s status=%s",
                user_id,
                paper_id,
                status_code,
            )
            return _searching_payload(paper_id), False, None

        raw_candidates = search_payload.get("data", []) or []

        # 4. Filter related candidates (excluding the focal paper itself)
        valid_candidates: list[dict[str, Any]] = []
        seen_ids: set[str] = {paper_id.strip().lower()}

        def _collect_valid(candidates: list[dict[str, Any]]) -> None:
            for cand in candidates:
                c_id = str(cand.get("paperId") or cand.get("paper_id") or "").strip()
                c_title = str(cand.get("title") or "").strip()
                c_abstract = str(cand.get("abstract") or "").strip()

                if not c_id or not c_title or not c_abstract:
                    continue

                norm_id = c_id.lower()
                if norm_id in seen_ids:
                    continue
                seen_ids.add(norm_id)

                valid_candidates.append(
                    {
                        "paper_id": c_id,
                        "title": c_title,
                        "abstract": c_abstract,
                        "year": cand.get("year"),
                    }
                )

        _collect_valid(raw_candidates)

        # Fallbacks only after a completed 200 — never after timeout/429.
        if not valid_candidates:
            prefix = title.split(":")[0].strip()
            if prefix and prefix.lower() != title.lower():
                fb_payload, fb_status = await search_papers_v2(
                    query=prefix, limit=RELATED_SEARCH_LIMIT
                )
                if _is_search_incomplete(fb_status):
                    return _searching_payload(paper_id), False, None
                _collect_valid(fb_payload.get("data", []) or [])

        if not valid_candidates:
            topical_query = _extract_topical_query(title)
            if topical_query and topical_query.lower() != title.lower():
                fb_payload, fb_status = await search_papers_v2(
                    query=topical_query, limit=RELATED_SEARCH_LIMIT
                )
                if _is_search_incomplete(fb_status):
                    return _searching_payload(paper_id), False, None
                _collect_valid(fb_payload.get("data", []) or [])

        if not valid_candidates:
            return None, False, "Not enough related research found with usable abstracts."


        # 5. Compute TF-IDF Cosine Similarity for each candidate
        corpus_texts = [
            f"{c['title']}. {c['abstract']}" for c in valid_candidates
        ] + [original_doc]

        scored_candidates: list[tuple[float, dict[str, Any]]] = []
        for cand in valid_candidates:
            cand_doc = f"{cand['title']}. {cand['abstract']}"
            sim = compute_tfidf_cosine_similarity(
                original_doc, cand_doc, background_corpus=corpus_texts
            )
            scored_candidates.append((sim, cand))

        # Sort by similarity descending
        scored_candidates.sort(key=lambda item: item[0], reverse=True)

        # Select top 1 to 3 related papers
        selected_related = [
            item[1] for item in scored_candidates[:MAX_RELATED_PAPERS]
        ]
        if not selected_related:
            return None, False, "Not enough related research found."

        # 6. Build structured synthesis notes
        synthesis_content = build_structured_synthesis_notes(
            original_paper_dict, selected_related
        )

        source_paper_ids = [paper_id] + [p["paper_id"] for p in selected_related]

        # 7. Persist to learning_content
        record = LearningContent(
            user_id=user_id,
            paper_id=paper_id,
            content_type="SYNTHESIS",
            content=_encode_synthesis_content(synthesis_content),
            source_papers=source_paper_ids,
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)

        logger.info(
            "[synthesis] Generated and persisted new synthesis user_id=%s paper_id=%s sources=%s",
            user_id,
            paper_id,
            source_paper_ids,
        )

        return (
            {
                "paper_id": paper_id,
                "content_type": "SYNTHESIS",
                "content": synthesis_content,
                "source_papers": source_paper_ids,
                "searching": False,
            },
            True,
            None,
        )
