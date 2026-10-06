"""Learning Spotlight – shared query helpers for V2 strategies.

Reuses V1 query-building logic from
``apps.recommendations.services.recommendation_query_builder`` without
modifying V1 code.

Provides:

* ``build_spotlight_query`` — builds a Boolean SS query from user keywords,
  transforming each interest/category into a small AND-group of concepts
  and OR-combining those groups.
* ``build_country_perspective_query`` — Country Perspective-only builder that
  preserves profile phrases and AND-pairs each with the user's country.
* ``build_semantic_scholar_query`` / ``build_topic_search_condition`` —
  reusable concept-group helpers (optional country AND inside each group).
* ``search_spotlight_candidates`` — staged Semantic Scholar retrieval with a
  bounded fallback (full groups → simpler groups). Too-many-hits never
  broadens into unigram OR soup.
* ``raw_papers_to_candidates`` — maps raw Semantic Scholar paper dicts to
  ``SpotlightCandidate`` Pydantic models, skipping papers without a usable
  open-access PDF (url, status, and license).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import quote_plus
from uuid import UUID

from apps.learningspotlight.config import settings as spotlight_settings
from apps.learningspotlight.schemas import (
    LearningSpotlightAuthor,
    SpotlightCandidate,
    SpotlightUserContext,
)
from apps.learningspotlight.services.keyword_normalization_service import (
    basic_cleanup,
    extract_topic_concepts,
)
from apps.learningspotlight.services.semantic_scholar_adapter import (
    is_placeholder_fields_of_study_label,
    is_too_many_hits_error,
)
from apps.recommendations.services.recommendation_query_builder import (
    collect_topics,
)
from common.enums import SpotlightType

logger = logging.getLogger(__name__)

MAX_QUERY_ATTEMPTS = 3
MIN_RESULTS_TO_ACCEPT = 1

SearchFn = Callable[..., Awaitable[tuple[dict[str, Any], int | None]]]


@dataclass(frozen=True, slots=True)
class SpotlightQueryPlan:
    raw_keywords: tuple[str, ...]
    derived_terms: tuple[str, ...]
    stage_queries: tuple[str, ...]
    extra_terms: tuple[str, ...]
    stage_term_lists: tuple[tuple[str, ...], ...] = ()
    stage_groups: tuple[tuple[tuple[str, ...], ...], ...] = ()
    pair_extra_terms: bool = False


def encode_spotlight_query(query: str) -> str:
    """URL-encode a complete Semantic Scholar query with ``quote_plus``."""
    return quote_plus(query or "")


def build_topic_search_condition(
    topic: str,
    country: str | None = None,
) -> str:
    """Build one parenthesized AND-group from a single interest/category."""
    concepts = extract_topic_concepts(topic)
    return _format_concept_group(concepts, country=country)


def build_semantic_scholar_query(
    topics: list[str] | tuple[str, ...] | None,
    country: str | None = None,
) -> str:
    """OR-combine per-topic concept groups, optionally AND-ing country in each.

    Example::

        ("multi word concept"+unigram)|(other+concepts)
    """
    groups = [extract_topic_concepts(topic) for topic in (topics or [])]
    return _compose_group_query(
        [tuple(concepts) for concepts in groups if concepts],
        extra_terms=(country.strip(),) if country and str(country).strip() else (),
        pair_extra_terms=bool(country and str(country).strip()),
    )


def build_spotlight_query(
    extracted_keywords: dict[str, Any],
    *,
    exclude_fields: list[str] | None = None,
    extra_terms: list[str] | None = None,
    pair_extra_terms: bool = False,
) -> str:
    """Build a Boolean Semantic Scholar query from user keywords.

    Each collected topic becomes an AND-group of generated concepts; groups
    are combined with OR.  Raw verbose category labels are not sent as
    exact phrases.

    Parameters
    ----------
    extracted_keywords:
        The ``profile.extracted_keywords`` JSONB dict.  Original values are
        not modified.
    exclude_fields:
        Top-level keys to remove from ``extracted_keywords`` before
        collecting topics.  Used by *Beyond Your Field* to drop ``major``
        and ``minor`` so the query targets adjacent domains.
    extra_terms:
        Additional terms.  When ``pair_extra_terms`` is True, each extra
        term is AND-ed inside every topic group.  Otherwise extra terms are
        appended as a suffix.
    pair_extra_terms:
        If True, compose ``(concepts+"United States")|...`` instead of
        appending extra terms as a suffix.

    Returns
    -------
    str
        A query string ready for ``search_papers_v2``.  May be empty if
        the user has no searchable keywords.  This is stage 1 of the
        staged retrieval plan.
    """
    plan = build_spotlight_query_plan(
        extracted_keywords,
        exclude_fields=exclude_fields,
        extra_terms=extra_terms,
        pair_extra_terms=pair_extra_terms,
    )
    if plan.stage_queries:
        return plan.stage_queries[0]
    return ""


def build_country_perspective_query(
    topics: list[str] | tuple[str, ...] | None,
    *,
    country: str | None = None,
) -> str:
    """Build a Country Perspective Boolean query from already-selected concepts.

    Each concept becomes one OR-clause, optionally AND-ed with the user's
    country name.  Callers should pass concepts from
    ``select_country_perspective_concepts`` (which applies hierarchical
    extraction and the concept cap).

    Example::

        ("Information Studies"+Ghana)|("Political Science and Government"+Ghana)
    """
    country_clean = basic_cleanup(country) if country else ""
    clauses: list[str] = []
    seen: set[str] = set()

    for raw in topics or []:
        if raw is None:
            continue
        cleaned = basic_cleanup(str(raw))
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        clause = _format_concept_group((cleaned,), country=country_clean or None)
        if clause:
            clauses.append(clause)

    if not clauses and country_clean:
        country_clause = _format_concept_group((), country=country_clean)
        if country_clause:
            return country_clause
    return "|".join(clauses)


_CP_IN_SPLIT_RE = re.compile(r"\s+in\s+", re.IGNORECASE)
_CP_LEVEL_MODIFIER_SUFFIXES = ("ed", "ive", "ory", "ary", "able")


def select_country_perspective_concepts(
    extracted_keywords: dict[str, Any] | None,
    *,
    max_concepts: int | None = None,
) -> list[str]:
    """Select bounded, prioritized Country Perspective search concepts.

    Priority: major → minor → interests (list order).  Engagement / content /
    hashtag signals are intentionally excluded to keep queries academic and
    bounded.

    * Major / minor are preserved with basic cleanup only.
    * Interests apply structural hierarchical extraction (core subject before
      ``In …`` hierarchy tails); no academic dictionary and no ``and``-split.
    """
    cap = (
        max_concepts
        if max_concepts is not None
        else spotlight_settings.learning_spotlight_country_max_concepts
    )
    kw = dict(extracted_keywords or {})
    selected: list[str] = []
    seen: set[str] = set()

    def _add(concept: str | None) -> None:
        if not concept or len(selected) >= cap:
            return
        key = concept.casefold()
        if key in seen:
            return
        seen.add(key)
        selected.append(concept)

    for raw in _as_profile_label_list(kw.get("major")):
        _add(extract_country_perspective_concept(raw, preserve_full=True))
    for raw in _as_profile_label_list(kw.get("minor")):
        _add(extract_country_perspective_concept(raw, preserve_full=True))
    for raw in _as_profile_label_list(kw.get("interests")):
        _add(extract_country_perspective_concept(raw, preserve_full=False))

    return selected


def extract_country_perspective_concept(
    topic: str,
    *,
    preserve_full: bool = False,
) -> str | None:
    """Derive one Country Perspective search concept from a profile label.

    When ``preserve_full`` is True (major/minor), only basic cleanup is applied.

    Otherwise hierarchical interest labels such as
    ``Applied Constitutional Law In American/US Law/...`` become
    ``Constitutional Law`` by taking the head before ``In`` when the tail looks
    hierarchical (``/`` or ``,``), then stripping leading level modifiers
    (Applied / Advanced / …).  Phrases without a hierarchy tail are preserved
    intact (including ``and``-joined titles and ``… Studies``).
    """
    cleaned = basic_cleanup(topic)
    if not cleaned:
        return None
    if preserve_full:
        return cleaned

    head, tail = _cp_split_head_and_tail(cleaned)
    working = cleaned
    if tail and _cp_looks_like_hierarchy(tail) and head:
        working = head

    words = working.split()
    while len(words) >= 2 and _cp_is_level_modifier(words[0]):
        words = words[1:]
    result = " ".join(words).strip()
    return result or None


def _as_profile_label_list(value: Any) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    result: list[str] = []
    for item in items:
        if item is None or isinstance(item, bool):
            continue
        if isinstance(item, (int, float)):
            continue
        text = str(item).strip()
        if not text or text.isdigit():
            continue
        result.append(text)
    return result


def _cp_split_head_and_tail(text: str) -> tuple[str, str]:
    match = _CP_IN_SPLIT_RE.search(text)
    if not match:
        return text, ""
    return text[: match.start()].strip(), text[match.end() :].strip()


def _cp_looks_like_hierarchy(tail: str) -> bool:
    return "/" in tail or "," in tail


def _cp_is_level_modifier(token: str) -> bool:
    lowered = token.casefold()
    if len(lowered) < 6:
        return False
    return lowered.endswith(_CP_LEVEL_MODIFIER_SUFFIXES)


def build_spotlight_query_plan(
    extracted_keywords: dict[str, Any] | None,
    *,
    exclude_fields: list[str] | None = None,
    extra_terms: list[str] | None = None,
    pair_extra_terms: bool = False,
) -> SpotlightQueryPlan:
    """Build up to three deterministic Semantic Scholar query stages.

    Stage 1 uses full per-topic concept groups. Later stages only simplify
    those groups (fewer AND concepts). They never explode phrases into
    unigram OR terms.
    """
    kw = dict(extracted_keywords or {})

    if exclude_fields:
        for field_name in exclude_fields:
            kw.pop(field_name, None)

    raw_topics = collect_topics(kw)
    groups: list[tuple[str, ...]] = []
    for topic in raw_topics:
        concepts = tuple(extract_topic_concepts(topic))
        if concepts:
            groups.append(concepts)

    cap = spotlight_settings.learning_spotlight_max_query_terms
    groups = groups[:cap]

    cleaned_extra = tuple(
        term.strip() for term in (extra_terms or []) if term and str(term).strip()
    )

    stage_source_groups = (
        tuple(groups),
        tuple(_simplify_topic_groups(groups, max_concepts=2)),
        tuple(_simplify_topic_groups(groups, max_concepts=1)),
    )
    stage_queries: list[str] = []
    stage_term_lists: list[tuple[str, ...]] = []
    stage_groups: list[tuple[tuple[str, ...], ...]] = []
    seen: set[str] = set()
    for candidate_groups in stage_source_groups:
        if not candidate_groups:
            continue
        query = _compose_group_query(
            candidate_groups, cleaned_extra, pair_extra_terms=pair_extra_terms
        )
        if not query or query in seen:
            continue
        seen.add(query)
        stage_queries.append(query)
        flat = tuple(concept for group in candidate_groups for concept in group)
        stage_term_lists.append(flat)
        stage_groups.append(candidate_groups)
        if len(stage_queries) >= MAX_QUERY_ATTEMPTS:
            break

    if not stage_queries and cleaned_extra:
        extra_query = _compose_group_query(
            (), cleaned_extra, pair_extra_terms=pair_extra_terms
        )
        if extra_query:
            stage_queries.append(extra_query)
            stage_term_lists.append(())
            stage_groups.append(())

    derived_terms = tuple(
        concept for group in groups for concept in group
    )
    logger.debug(
        "[spotlight-query] raw_keywords=%r derived_terms=%r stage_queries=%r "
        "encoded_queries=%r query_term_counts=%r pair_extra_terms=%s",
        list(raw_topics),
        list(derived_terms),
        stage_queries,
        [encode_spotlight_query(query) for query in stage_queries],
        [len(terms) for terms in stage_term_lists],
        pair_extra_terms,
    )
    return SpotlightQueryPlan(
        raw_keywords=tuple(raw_topics),
        derived_terms=derived_terms,
        stage_queries=tuple(stage_queries),
        extra_terms=cleaned_extra,
        stage_term_lists=tuple(stage_term_lists),
        stage_groups=tuple(stage_groups),
        pair_extra_terms=pair_extra_terms,
    )


def fields_of_study_from_user_profile(
    context: SpotlightUserContext | None = None,
    *,
    extracted_keywords: dict[str, Any] | None = None,
    major: str | None = None,
    minor: str | None = None,
) -> list[str]:
    """Build `fieldsOfStudy` from the user's profile major and minor.

    Used only by Country Perspective. Country and interests are not included.
    Values are the profile labels themselves — there is no static Semantic
    Scholar vocabulary mapping. Placeholder sentinels such as ``Na``,
    ``N/A``, and ``None`` are dropped.
    """
    keywords = _extracted_keywords(context, extracted_keywords)
    resolved_major = major if major is not None else (context.major if context else None)
    resolved_minor = minor if minor is not None else (context.minor if context else None)
    return _unique_profile_labels(
        keywords.get("major"),
        resolved_major,
        keywords.get("minor"),
        resolved_minor,
    )


def fields_of_study_from_user_interests(
    context: SpotlightUserContext | None = None,
    *,
    extracted_keywords: dict[str, Any] | None = None,
) -> list[str]:
    """Build `fieldsOfStudy` from profile interests (Beyond Your Field).

    Major and minor are excluded so retrieval can leave the user's primary field.
    """
    keywords = _extracted_keywords(context, extracted_keywords)
    return _unique_profile_labels(
        keywords.get("interests"),
        context.interests if context is not None else None,
    )


def _extracted_keywords(
    context: SpotlightUserContext | None,
    extracted_keywords: dict[str, Any] | None,
) -> dict[str, Any]:
    if extracted_keywords is not None:
        return dict(extracted_keywords)
    if context is not None:
        return dict(context.extracted_keywords or {})
    return {}


def _unique_profile_labels(*groups: Any) -> list[str]:
    """Dedupe profile labels; skip empty values and numeric interest IDs."""
    seen: set[str] = set()
    result: list[str] = []
    for group in groups:
        if group is None:
            continue
        items = group if isinstance(group, (list, tuple, set)) else [group]
        for item in items:
            if item is None or isinstance(item, bool):
                continue
            if isinstance(item, (int, float)):
                continue
            cleaned = " ".join(str(item).split())
            if (
                not cleaned
                or cleaned.isdigit()
                or is_placeholder_fields_of_study_label(cleaned)
            ):
                continue
            key = cleaned.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(cleaned)
    return result


def _quote_query_term(topic: str) -> str:
    escaped = str(topic).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _format_concept(concept: str) -> str:
    """Quote multi-word concepts; leave single tokens unquoted."""
    cleaned = str(concept).strip()
    if not cleaned:
        return ""
    if any(ch.isspace() for ch in cleaned) or any(ch in cleaned for ch in '+|()'):
        return _quote_query_term(cleaned)
    return cleaned


def _format_concept_group(
    concepts: tuple[str, ...] | list[str],
    country: str | None = None,
) -> str:
    parts = [_format_concept(concept) for concept in concepts]
    parts = [part for part in parts if part]
    if country and str(country).strip():
        country_part = _format_concept(str(country).strip())
        if country_part:
            parts.append(country_part)
    if not parts:
        return ""
    joined = "+".join(parts)
    return f"({joined})"


def _simplify_topic_groups(
    groups: list[tuple[str, ...]] | tuple[tuple[str, ...], ...],
    *,
    max_concepts: int,
) -> list[tuple[str, ...]]:
    """Keep the strongest concepts in each group; never add broader unigrams."""
    simplified: list[tuple[str, ...]] = []
    for concepts in groups:
        phrases = [concept for concept in concepts if " " in str(concept)]
        unigrams = [concept for concept in concepts if " " not in str(concept)]
        chosen = tuple((phrases + unigrams)[:max_concepts])
        if chosen:
            simplified.append(chosen)
    return simplified


def _compose_group_query(
    groups: tuple[tuple[str, ...], ...] | list[tuple[str, ...]],
    extra_terms: tuple[str, ...],
    *,
    pair_extra_terms: bool = False,
) -> str:
    country = extra_terms[0] if pair_extra_terms and extra_terms else None
    suffix_terms = extra_terms if extra_terms and not pair_extra_terms else ()
    clauses = [
        _format_concept_group(group, country=country)
        for group in groups
    ]
    clauses = [clause for clause in clauses if clause]
    if not clauses and country:
        country_clause = _format_concept_group((), country=country)
        if country_clause:
            clauses.append(country_clause)
    query = "|".join(clauses)
    if suffix_terms:
        suffix = " ".join(suffix_terms)
        query = f"{query} {suffix}" if query else suffix
    return query


async def search_spotlight_candidates(
    extracted_keywords: dict[str, Any] | None,
    *,
    search_fn: SearchFn,
    exclude_fields: list[str] | None = None,
    extra_terms: list[str] | None = None,
    pair_extra_terms: bool = False,
    fields_of_study: list[str] | None = None,
    year: str | None = None,
    limit: int | None = None,
    user_id: UUID | str | None = None,
    log_prefix: str = "spotlight",
    override_queries: list[str] | None = None,
    country: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Run staged Semantic Scholar search with a bounded fallback.

    Stage 1 uses full per-topic concept groups. If that returns too few
    papers (HTTP 200, empty data), later stages only simplify those groups.
    A "too many hits" error never falls back to a broader unigram-OR query;
    OR-groups are dropped instead, up to
    ``learning_spotlight_query_narrow_max_attempts``.
    """
    extra: tuple[str, ...] = ()
    pair_extras = pair_extra_terms
    stage_groups: tuple[tuple[tuple[str, ...], ...], ...] = ()
    if override_queries is not None:
        queries = [query for query in override_queries if query and query.strip()][
            :MAX_QUERY_ATTEMPTS
        ]
        stage_groups = tuple(_groups_from_boolean_query(query) for query in queries)
        raw_keywords: tuple[str, ...] = ()
        derived_terms: tuple[str, ...] = ()
    else:
        plan = build_spotlight_query_plan(
            extracted_keywords,
            exclude_fields=exclude_fields,
            extra_terms=extra_terms,
            pair_extra_terms=pair_extras,
        )
        queries = list(plan.stage_queries)
        stage_groups = plan.stage_groups
        extra = plan.extra_terms
        pair_extras = plan.pair_extra_terms
        raw_keywords = plan.raw_keywords
        derived_terms = plan.derived_terms

    if not queries:
        logger.debug(
            "[%s] user=%s no searchable query raw=%r fields_of_study=%r country=%r",
            log_prefix,
            user_id,
            list(raw_keywords),
            fields_of_study,
            country,
        )
        return [], ""

    last_query = queries[0]
    max_narrow = spotlight_settings.learning_spotlight_query_narrow_max_attempts
    for attempt, query in enumerate(queries, start=1):
        last_query = query
        payload, status = await _invoke_search(
            search_fn,
            query,
            limit=limit,
            year=year,
            fields_of_study=fields_of_study,
        )
        papers: list[dict[str, Any]] = payload.get("data") or []
        result_count = len(papers)
        logger.debug(
            "[%s] user=%s attempt=%d/%d query=%r fields_of_study=%r country=%r "
            "encoded_query=%r semantic_scholar_result_count=%d status=%s "
            "raw_keywords=%r derived_terms=%r",
            log_prefix,
            user_id,
            attempt,
            len(queries),
            query,
            fields_of_study,
            country,
            encode_spotlight_query(query),
            result_count,
            status,
            list(raw_keywords),
            list(derived_terms),
        )
        if is_too_many_hits_error(payload, status):
            groups = (
                stage_groups[attempt - 1]
                if attempt - 1 < len(stage_groups)
                else _groups_from_boolean_query(query)
            )
            papers, last_query = await _search_narrowing(
                search_fn,
                groups,
                extra_terms=extra,
                pair_extra_terms=pair_extras,
                fields_of_study=fields_of_study,
                year=year,
                limit=limit,
                max_narrow=max_narrow,
                log_prefix=log_prefix,
                user_id=user_id,
                country=country,
            )
            return papers, last_query
        if result_count >= MIN_RESULTS_TO_ACCEPT:
            return papers, query

    return [], last_query


async def _search_narrowing(
    search_fn: SearchFn,
    groups: tuple[tuple[str, ...], ...] | list[tuple[str, ...]],
    *,
    extra_terms: tuple[str, ...],
    pair_extra_terms: bool,
    fields_of_study: list[str] | None,
    year: str | None,
    limit: int | None,
    max_narrow: int,
    log_prefix: str,
    user_id: UUID | str | None,
    country: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Retry a too-many-hits query by dropping OR-groups; never add broader terms."""
    current = list(groups)
    last_query = _compose_group_query(
        current, extra_terms, pair_extra_terms=pair_extra_terms
    )
    for narrow_attempt in range(1, max_narrow + 1):
        narrowed = _narrow_topic_groups(current)
        if not narrowed or narrowed == current:
            logger.info(
                "[%s] user=%s too-many-hits cannot narrow further query=%r "
                "fields_of_study=%r country=%r",
                log_prefix,
                user_id,
                last_query,
                fields_of_study,
                country,
            )
            return [], last_query
        current = narrowed
        last_query = _compose_group_query(
            current, extra_terms, pair_extra_terms=pair_extra_terms
        )
        payload, status = await _invoke_search(
            search_fn,
            last_query,
            limit=limit,
            year=year,
            fields_of_study=fields_of_study,
        )
        papers: list[dict[str, Any]] = payload.get("data") or []
        logger.info(
            "[%s] user=%s too-many-hits narrow_attempt=%d/%d groups=%d query=%r "
            "fields_of_study=%r country=%r status=%s result_count=%d",
            log_prefix,
            user_id,
            narrow_attempt,
            max_narrow,
            len(current),
            last_query,
            fields_of_study,
            country,
            status,
            len(papers),
        )
        if len(papers) >= MIN_RESULTS_TO_ACCEPT:
            return papers, last_query
        if not is_too_many_hits_error(payload, status):
            return [], last_query
    return [], last_query


def _groups_from_boolean_query(query: str) -> tuple[tuple[str, ...], ...]:
    """Best-effort parse of ``(a+b)|(c+d)`` / legacy ``("a"|"b")`` into groups."""
    text = (query or "").strip()
    if not text:
        return ()
    parts = [part.strip() for part in re.split(r"\|", text) if part.strip()]
    groups: list[tuple[str, ...]] = []
    for part in parts:
        body = part[1:-1] if part.startswith("(") and part.endswith(")") else part
        concepts: list[str] = []
        remainder = body
        for quoted in re.findall(r'"([^"]+)"', body):
            concepts.append(quoted)
            remainder = remainder.replace(f'"{quoted}"', " ", 1)
        for item in remainder.replace("(", " ").replace(")", " ").split("+"):
            token = item.strip(" |")
            if token:
                concepts.append(token)
        if concepts:
            groups.append(tuple(concepts))
    return tuple(groups)


def _narrow_topic_groups(
    groups: list[tuple[str, ...]],
) -> list[tuple[str, ...]]:
    """Keep the first half of OR-groups. Never introduce broader unigram terms."""
    if len(groups) <= 1:
        return []
    keep = max(1, len(groups) // 2)
    if keep >= len(groups):
        return groups[:-1]
    return list(groups[:keep])


async def _invoke_search(
    search_fn: SearchFn,
    query: str,
    *,
    limit: int | None,
    year: str | None,
    fields_of_study: list[str] | None = None,
) -> tuple[dict[str, Any], int | None]:
    kwargs: dict[str, Any] = {
        "limit": limit,
        "fields_of_study": fields_of_study or None,
    }
    if year is not None:
        kwargs["year"] = year
    return await search_fn(query, **kwargs)


def _parse_authors(raw_authors: list[dict[str, Any]] | None) -> list[LearningSpotlightAuthor]:
    """Convert raw SS author dicts to ``LearningSpotlightAuthor`` models."""
    if not raw_authors:
        return []
    return [
        LearningSpotlightAuthor(
            author_id=a.get("authorId"),
            name=a.get("name"),
        )
        for a in raw_authors
        if isinstance(a, dict)
    ]


def _non_empty_text(value: Any) -> bool:
    """True when ``value`` is a non-blank string (after strip)."""
    return isinstance(value, str) and bool(value.strip())


def has_usable_open_access_pdf(paper: dict[str, Any]) -> bool:
    """True when Semantic Scholar ``openAccessPdf`` has url, status, and license.

    Papers with a missing/null ``openAccessPdf`` object, an empty PDF ``url``,
    or null/empty ``status`` / ``license`` are not usable for Learning Spotlight.
    """
    pdf = paper.get("openAccessPdf")
    if not isinstance(pdf, dict):
        return False
    return (
        _non_empty_text(pdf.get("url"))
        and _non_empty_text(pdf.get("status"))
        and _non_empty_text(pdf.get("license"))
    )


def raw_papers_to_candidates(
    papers: list[dict[str, Any]],
    *,
    query: str,
    spotlight_type: SpotlightType,
    extra_metadata: dict[str, Any] | None = None,
) -> list[SpotlightCandidate]:
    """Map raw Semantic Scholar paper dicts to ``SpotlightCandidate`` models.

    Papers missing ``paperId``, a non-empty ``abstract``, or a usable
    ``openAccessPdf`` (non-empty ``url``, ``status``, and ``license``) are
    silently skipped.

    Parameters
    ----------
    papers:
        List of raw paper dicts from SS ``data`` array.
    query:
        The query string that produced these results (preserved for
        auditing).
    spotlight_type:
        The spotlight category that generated these candidates.
    extra_metadata:
        Optional dict merged into every candidate's ``metadata``.
    """
    candidates: list[SpotlightCandidate] = []
    base_meta = extra_metadata or {}

    for paper in papers:
        if not isinstance(paper, dict):
            continue
        paper_id = paper.get("paperId")
        if not paper_id:
            continue

        abstract = paper.get("abstract")
        if not abstract or not str(abstract).strip():
            continue

        if not has_usable_open_access_pdf(paper):
            continue

        # Extract year from either 'year' field or 'publicationDate' string.
        year = paper.get("year")
        if year is None:
            pub_date = paper.get("publicationDate") or ""
            if pub_date and len(pub_date) >= 4:
                try:
                    year = int(pub_date[:4])
                except (ValueError, TypeError):
                    year = None

        candidates.append(
            SpotlightCandidate(
                paper_id=paper_id,
                title=paper.get("title"),
                authors=_parse_authors(paper.get("authors")),
                abstract=str(abstract).strip(),
                venue=paper.get("venue"),
                year=year,
                citation_count=paper.get("citationCount"),
                url=paper.get("url"),
                query=query,
                spotlight_type=spotlight_type,
                metadata={
                    **base_meta,
                    **(
                        {"fields_of_study": paper.get("fieldsOfStudy")}
                        if paper.get("fieldsOfStudy")
                        else {}
                    ),
                    **(
                        {"s2_fields_of_study": paper.get("s2FieldsOfStudy")}
                        if paper.get("s2FieldsOfStudy")
                        else {}
                    ),
                },
            )
        )

    return candidates
