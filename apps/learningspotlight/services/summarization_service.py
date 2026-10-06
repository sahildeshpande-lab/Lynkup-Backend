"""Learning Spotlight – Extractive Paper Summarization Service (Non-LLM).

Uses sentence splitting, TF-IDF importance, title overlap, and position scoring
to generate deterministic extractive summaries of academic research papers.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any, Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.learningspotlight.db_models.learning_content_db_model import LearningContent

logger = logging.getLogger(__name__)

# Standard English stop words
STOP_WORDS: frozenset[str] = frozenset(
    {
        "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
        "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
        "below", "between", "both", "but", "by", "can", "can't", "cannot", "could",
        "couldn't", "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down",
        "during", "each", "few", "for", "from", "further", "had", "hadn't", "has",
        "hasn't", "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her",
        "here", "here's", "hers", "herself", "him", "himself", "his", "how", "how's",
        "i", "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it",
        "it's", "its", "itself", "let's", "me", "more", "most", "mustn't", "my",
        "myself", "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other",
        "ought", "our", "ours", "ourselves", "out", "over", "own", "same", "shan't",
        "she", "she'd", "she'll", "she's", "should", "shouldn't", "so", "some", "such",
        "than", "that", "that's", "the", "their", "theirs", "them", "themselves",
        "then", "there", "there's", "these", "they", "they'd", "they'll", "they're",
        "they've", "this", "those", "through", "to", "too", "under", "until", "up",
        "very", "was", "wasn't", "we", "we'd", "we'll", "we're", "we've", "were",
        "weren't", "what", "what's", "when", "when's", "where", "where's", "which",
        "while", "who", "who's", "whom", "why", "why's", "with", "won't", "would",
        "wouldn't", "you", "you'd", "you'll", "you're", "you've", "your", "yours",
        "yourself", "yourselves",
    }
)

TARGET_MIN_WORDS: int = 100
TARGET_MAX_WORDS: int = 180

WEIGHT_TFIDF: float = 0.70
WEIGHT_TITLE_OVERLAP: float = 0.20
WEIGHT_POSITION: float = 0.10


def split_into_sentences(text: str | None) -> list[str]:
    """Split abstract text into clean sentence segments while avoiding abbreviations."""
    if not text or not text.strip():
        return []
    cleaned = " ".join(text.strip().split())
    # Regex split on sentence terminators (. ! ?) followed by whitespace and capital letter or quote
    pattern = (
        r'(?<!\be\.g)(?<!\bi\.e)(?<!\bet al)(?<!\bFig)(?<!\bEq)(?<!\bDr)'
        r'(?<!\bProf)(?<!\bvs)(?<=[.!?])\s+(?=[A-Z0-9"\'\(\[])'
    )
    raw_sentences = re.split(pattern, cleaned)
    sentences = [s.strip() for s in raw_sentences if s.strip()]
    if not sentences and cleaned:
        sentences = [cleaned]
    return sentences


def tokenize(text: str | None) -> list[str]:
    """Lowercase, remove punctuation, and extract non-stopword alphanumeric tokens."""
    if not text or not text.strip():
        return []
    tokens = re.findall(r"\b[a-zA-Z0-9_\-]+\b", text.lower())
    return [t for t in tokens if t not in STOP_WORDS and len(t) > 1]


def count_words(text: str) -> int:
    """Return the number of whitespace-delimited words in the text."""
    return len(text.strip().split()) if text and text.strip() else 0


def generate_extractive_summary(
    title: str | None,
    abstract: str | None,
    *,
    min_words: int = TARGET_MIN_WORDS,
    max_words: int = TARGET_MAX_WORDS,
) -> str:
    """Produce a deterministic, extractive summary from paper title and abstract.

    Algorithm:
    1. Split abstract into sentences.
    2. Compute TF-IDF representation and sentence importance.
    3. Compute title overlap score.
    4. Compute position score.
    5. Score sentences: 70% TF-IDF + 20% Title Overlap + 10% Position.
    6. Select top sentences fitting the target word limit (100–180 words).
    7. Restore original sentence order and join.
    """
    if not abstract or not abstract.strip():
        return ""

    sentences = split_into_sentences(abstract)
    if not sentences:
        return ""

    # If the abstract is already within the target maximum length, return complete abstract
    total_words = sum(count_words(s) for s in sentences)
    if total_words <= max_words:
        return " ".join(sentences)

    n_sentences = len(sentences)
    if n_sentences == 1:
        return sentences[0]

    # Tokenize sentences and title
    sentence_tokens = [tokenize(s) for s in sentences]
    title_tokens = set(tokenize(title)) if title else set()

    # 1. Compute Document Frequency (DF) across sentences
    df: dict[str, int] = {}
    for tokens in sentence_tokens:
        unique_tokens = set(tokens)
        for t in unique_tokens:
            df[t] = df.get(t, 0) + 1

    # 2. Compute IDF: ln((N + 1) / (DF + 1)) + 1.0
    idf: dict[str, float] = {}
    for t, count in df.items():
        idf[t] = math.log((n_sentences + 1.0) / (count + 1.0)) + 1.0

    # 3. Compute raw TF-IDF score for each sentence
    raw_tfidf_scores: list[float] = []
    for tokens in sentence_tokens:
        if not tokens:
            raw_tfidf_scores.append(0.0)
            continue
        token_counts: dict[str, int] = {}
        for t in tokens:
            token_counts[t] = token_counts.get(t, 0) + 1

        total_tokens = len(tokens)
        s_score = 0.0
        for t, count in token_counts.items():
            tf = count / total_tokens
            s_score += tf * idf.get(t, 1.0)
        raw_tfidf_scores.append(s_score)

    max_tfidf = max(raw_tfidf_scores) if raw_tfidf_scores else 1.0
    norm_tfidf_scores = [
        (s / max_tfidf) if max_tfidf > 0 else 0.0 for s in raw_tfidf_scores
    ]

    # 4. Compute Title Overlap and Position Scores
    scored_sentences: list[dict[str, Any]] = []
    for i, sentence in enumerate(sentences):
        tokens = sentence_tokens[i]
        token_set = set(tokens)

        # Title Overlap (0.0 - 1.0)
        if title_tokens:
            overlap_count = len(title_tokens.intersection(token_set))
            title_score = overlap_count / len(title_tokens)
        else:
            title_score = 0.0

        # Position Score: early sentences get higher bonus
        position_score = (n_sentences - 1 - i) / (n_sentences - 1) if n_sentences > 1 else 1.0

        # Composite score
        composite_score = (
            norm_tfidf_scores[i] * WEIGHT_TFIDF
            + title_score * WEIGHT_TITLE_OVERLAP
            + position_score * WEIGHT_POSITION
        )

        scored_sentences.append(
            {
                "index": i,
                "text": sentence,
                "word_count": count_words(sentence),
                "score": composite_score,
            }
        )

    # Sort by score descending (tie-breaker: earlier original index)
    sorted_candidates = sorted(
        scored_sentences,
        key=lambda item: (item["score"], -item["index"]),
        reverse=True,
    )

    # Greedily select top sentences up to max_words
    selected_items: list[dict[str, Any]] = []
    current_word_count = 0

    for item in sorted_candidates:
        w = item["word_count"]
        if current_word_count + w <= max_words:
            selected_items.append(item)
            current_word_count += w
        elif not selected_items:
            # Always select at least the top-1 sentence even if it slightly exceeds max_words
            selected_items.append(item)
            current_word_count += w
            break

    # Restore original chronological order of sentences in abstract
    selected_items.sort(key=lambda item: item["index"])

    return " ".join(item["text"] for item in selected_items)


async def load_paper_metadata_for_user(
    session: AsyncSession,
    user_id: UUID,
    paper_id: str,
) -> tuple[str | None, str | None]:
    """Find title and abstract for paper_id from local user spotlight or history."""
    from apps.profiles.db_models.learning_recommendation_log_db_model import (
        LearningRecommendationLog,
    )
    from apps.profiles.db_models.profile_db_model import Profile

    # 1. Check Profile.learning_spotlight
    profile_stmt = select(Profile).where(Profile.user_id == user_id)
    profile_res = await session.execute(profile_stmt)
    profile = profile_res.scalar_one_or_none()

    if profile and isinstance(profile.learning_spotlight, dict):
        ls = profile.learning_spotlight
        paper = ls.get("paper") if isinstance(ls.get("paper"), dict) else {}
        pid = paper.get("paper_id") or ls.get("paper_id")
        if pid and str(pid).strip() == paper_id.strip():
            return paper.get("title"), paper.get("abstract")

    # 2. Check LearningRecommendationLog
    logs_stmt = (
        select(LearningRecommendationLog)
        .where(LearningRecommendationLog.user_id == user_id)
        .order_by(LearningRecommendationLog.created_at.desc())
    )
    logs_res = await session.execute(logs_stmt)
    logs = logs_res.scalars().all()

    for log in logs:
        rec = log.learning_recommendations
        if isinstance(rec, dict):
            paper = rec.get("paper") if isinstance(rec.get("paper"), dict) else {}
            pid = paper.get("paper_id") or rec.get("paper_id")
            if pid and str(pid).strip() == paper_id.strip():
                return paper.get("title"), paper.get("abstract")

    return None, None


class PaperSummarizationService:
    """Service handling paper summarization logic and PostgreSQL persistence."""

    @classmethod
    async def get_or_create_summary(
        cls,
        session: AsyncSession,
        *,
        user_id: UUID,
        paper_id: str,
        title: str | None = None,
        abstract: str | None = None,
    ) -> tuple[str, bool]:
        """Fetch existing summary from learning_content or generate and persist a new one.

        Returns
        -------
        tuple[str, bool]
            (summary_text, is_newly_generated)
        """
        # 1. Check for existing summary
        stmt = select(LearningContent).where(
            LearningContent.user_id == user_id,
            LearningContent.paper_id == paper_id,
            LearningContent.content_type == "SUMMARY",
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing is not None:
            logger.info(
                "[summarization] Returning cached summary user_id=%s paper_id=%s",
                user_id,
                paper_id,
            )
            return existing.content, False

        # 2. If title or abstract are missing, attempt local retrieval
        if not abstract:
            local_title, local_abstract = await load_paper_metadata_for_user(
                session, user_id, paper_id
            )
            if not title:
                title = local_title
            if not abstract:
                abstract = local_abstract

        # 3. Validate abstract presence
        if not abstract or not abstract.strip():
            logger.warning(
                "[summarization] Cannot summarize paper without abstract paper_id=%s",
                paper_id,
            )
            return "", False

        # 4. Generate extractive summary
        summary_text = generate_extractive_summary(title=title, abstract=abstract)
        if not summary_text:
            return "", False

        # 5. Persist to learning_content
        record = LearningContent(
            user_id=user_id,
            paper_id=paper_id,
            content_type="SUMMARY",
            content=summary_text,
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)

        logger.info(
            "[summarization] Generated and persisted new summary user_id=%s paper_id=%s words=%s",
            user_id,
            paper_id,
            count_words(summary_text),
        )
        return summary_text, True
