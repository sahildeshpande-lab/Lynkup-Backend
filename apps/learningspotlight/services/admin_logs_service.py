"""Admin listing of Learning Spotlight user actions and generated content."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence
from uuid import UUID

from sqlalchemy import Integer, and_, case, cast, func, or_, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from apps.accounts.db_models import User
from apps.learningspotlight.db_models.learning_content_db_model import LearningContent
from apps.learningspotlight.db_models.learning_paper_interaction_db_model import (
    LearningPaperInteraction,
)
from apps.learningspotlight.schemas import (
    SpotlightAdminLogItem,
    SpotlightAdminLogLikeSummary,
    SpotlightAdminLogSummary,
    extract_recommended_cycle_name,
)
from apps.profiles.db_models import (
    Country,
    LearningRecommendationLog,
    Major,
    Minor,
    Profile,
    University,
)
from common.enums import EducationLevel, LearningPaperAction
from common.pagination import paginate_or_all

INTERACTION_LOG_ACTIONS = frozenset(action.value for action in LearningPaperAction)
CONTENT_LOG_ACTIONS = frozenset({"SUMMARY", "SYNTHESIS"})
ALL_LOG_ACTIONS = INTERACTION_LOG_ACTIONS | CONTENT_LOG_ACTIONS
SAVE_LOG_ACTIONS = frozenset(
    {LearningPaperAction.save.value, LearningPaperAction.unsave.value}
)
LIKE_LOG_ACTIONS = frozenset(
    {LearningPaperAction.like.value, LearningPaperAction.dislike.value}
)


class _IsLikeFilterUnset:
    pass


IS_LIKE_FILTER_UNSET = _IsLikeFilterUnset()
IsLikeFilter = bool | None | _IsLikeFilterUnset


@dataclass(frozen=True)
class GroupedLogFilters:
    is_saved: bool | None = None
    is_summarizes: bool | None = None
    is_sythesis: bool | None = None
    is_read: bool | None = None
    is_like: IsLikeFilter = IS_LIKE_FILTER_UNSET
    is_skip: bool | None = None


def parse_is_like_query_param(value: str | None) -> IsLikeFilter:
    """Parse ``is_like`` query param: ``true``, ``false``, ``null``, or omitted."""
    if value is None:
        return IS_LIKE_FILTER_UNSET
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    if normalized == "null":
        return None
    raise ValueError('is_like must be "true", "false", or "null"')


def collect_paper_titles(payload: Any) -> dict[str, str]:
    """Extract paper_id -> title from a spotlight or recommendation snapshot."""
    titles: dict[str, str] = {}
    if not isinstance(payload, dict):
        return titles

    def _add(paper: Any) -> None:
        if not isinstance(paper, dict):
            return
        paper_id = paper.get("paper_id")
        title = paper.get("title")
        if not paper_id or not isinstance(title, str):
            return
        pid = str(paper_id).strip()
        cleaned = title.strip()
        if pid and cleaned and pid not in titles:
            titles[pid] = cleaned

    papers = payload.get("papers")
    if isinstance(papers, list):
        for paper in papers:
            _add(paper)
    _add(payload.get("paper"))
    return titles


def collect_paper_cycle_names(payload: Any) -> dict[str, str]:
    """Extract paper_id -> recommended cycle name from a spotlight snapshot."""
    names: dict[str, str] = {}
    if not isinstance(payload, dict):
        return names
    cycle_name = extract_recommended_cycle_name(payload)
    if not cycle_name:
        return names

    def _add(paper: Any) -> None:
        if not isinstance(paper, dict):
            return
        paper_id = paper.get("paper_id")
        if not paper_id:
            return
        pid = str(paper_id).strip()
        if pid and pid not in names:
            names[pid] = cycle_name

    papers = payload.get("papers")
    if isinstance(papers, list):
        for paper in papers:
            _add(paper)
    _add(payload.get("paper"))
    return names


def derive_spotlight_admin_log_group(
    actions: Sequence[tuple[str, datetime]],
) -> dict[str, Any]:
    """Derive grouped admin log state from chronological action records."""
    chronological = sorted(actions, key=lambda item: item[1])
    action_names = [action for action, _ in chronological]

    is_read = LearningPaperAction.read.value in action_names
    is_summarizes = "SUMMARY" in action_names
    is_sythesis = "SYNTHESIS" in action_names

    save_actions = [
        (action, created_at)
        for action, created_at in chronological
        if action in SAVE_LOG_ACTIONS
    ]
    is_saved = (
        save_actions[-1][0] == LearningPaperAction.save.value if save_actions else False
    )

    like_actions = [
        (action, created_at)
        for action, created_at in chronological
        if action in LIKE_LOG_ACTIONS
    ]
    if like_actions:
        is_like = like_actions[-1][0] == LearningPaperAction.like.value
    else:
        is_like = None

    is_skip = is_read and is_like is None
    created_at = max(created_at for _, created_at in chronological)

    return {
        "is_saved": is_saved,
        "is_summarizes": is_summarizes,
        "is_sythesis": is_sythesis,
        "is_read": is_read,
        "is_like": is_like,
        "is_skip": is_skip,
        "created_at": created_at,
    }


async def _resolve_paper_titles(
    session: AsyncSession,
    *,
    user_ids: Sequence[UUID],
    paper_ids: Sequence[str],
) -> dict[str, str]:
    titles, _cycle_names = await _resolve_paper_metadata(
        session, user_ids=user_ids, paper_ids=paper_ids
    )
    return titles


async def _resolve_paper_metadata(
    session: AsyncSession,
    *,
    user_ids: Sequence[UUID],
    paper_ids: Sequence[str],
) -> tuple[dict[str, str], dict[str, str]]:
    """Resolve paper titles and recommended cycle names for the given paper ids."""
    needed = {str(pid).strip() for pid in paper_ids if str(pid).strip()}
    if not needed or not user_ids:
        return {}, {}

    titles: dict[str, str] = {}
    cycle_names: dict[str, str] = {}
    profile_stmt = select(Profile.user_id, Profile.learning_spotlight).where(
        Profile.user_id.in_(list(user_ids))
    )
    profile_rows = (await session.execute(profile_stmt)).all()
    for row in profile_rows:
        spotlight = row[1] if not hasattr(row, "learning_spotlight") else row.learning_spotlight
        for paper_id, title in collect_paper_titles(spotlight).items():
            if paper_id in needed and paper_id not in titles:
                titles[paper_id] = title
        for paper_id, cycle_name in collect_paper_cycle_names(spotlight).items():
            if paper_id in needed and paper_id not in cycle_names:
                cycle_names[paper_id] = cycle_name

    missing_titles = needed - set(titles)
    missing_cycles = needed - set(cycle_names)
    if not missing_titles and not missing_cycles:
        return titles, cycle_names

    logs_stmt = select(LearningRecommendationLog).where(
        LearningRecommendationLog.user_id.in_(list(user_ids))
    )
    logs = (await session.execute(logs_stmt)).scalars().all()
    for log in logs:
        if missing_titles:
            for paper_id, title in collect_paper_titles(log.learning_recommendations).items():
                if paper_id in missing_titles and paper_id not in titles:
                    titles[paper_id] = title
            missing_titles = needed - set(titles)
        if missing_cycles:
            for paper_id, cycle_name in collect_paper_cycle_names(
                log.learning_recommendations
            ).items():
                if paper_id in missing_cycles and paper_id not in cycle_names:
                    cycle_names[paper_id] = cycle_name
            missing_cycles = needed - set(cycle_names)
        if not missing_titles and not missing_cycles:
            break
    return titles, cycle_names


async def _find_paper_ids_by_title_search(
    session: AsyncSession,
    search: str,
    *,
    user_id: UUID | None = None,
) -> set[str]:
    """Return paper ids whose resolved title contains the search term."""
    needle = search.strip().lower()
    if not needle:
        return set()

    matches: set[str] = set()
    profile_stmt = select(Profile.learning_spotlight).where(Profile.learning_spotlight.is_not(None))
    if user_id is not None:
        profile_stmt = profile_stmt.where(Profile.user_id == user_id)
    for spotlight in (await session.execute(profile_stmt)).scalars().all():
        for paper_id, title in collect_paper_titles(spotlight).items():
            if needle in title.lower():
                matches.add(paper_id)

    logs_stmt = select(LearningRecommendationLog.learning_recommendations)
    if user_id is not None:
        logs_stmt = logs_stmt.where(LearningRecommendationLog.user_id == user_id)
    for recommendations in (await session.execute(logs_stmt)).scalars().all():
        for paper_id, title in collect_paper_titles(recommendations).items():
            if needle in title.lower():
                matches.add(paper_id)
    return matches


def _row_attr(row: Any, name: str, index: int) -> Any:
    if hasattr(row, name):
        return getattr(row, name)
    mapping = getattr(row, "_mapping", None)
    if mapping is not None and name in mapping:
        return mapping[name]
    try:
        return row[index]
    except (IndexError, KeyError, TypeError):
        return None


def _stringify_id(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _education_level_payload(edu_level: str | None) -> tuple[str | None, dict[str, Any] | None]:
    if not edu_level:
        return None, None
    try:
        level = EducationLevel(edu_level)
        return level.value, {"id": level.id, "edu_level": level.value}
    except ValueError:
        return edu_level, {"id": None, "edu_level": edu_level}


async def _resolve_academic_catalogs(
    session: AsyncSession,
    *,
    university_ids: set[UUID],
    country_ids: set[UUID],
    major_ids: set[int],
    minor_ids: set[int],
) -> tuple[dict[UUID, Any], dict[UUID, Any], dict[int, Any], dict[int, Any]]:
    universities: dict[UUID, Any] = {}
    if university_ids:
        uni_rows = (
            await session.execute(
                select(University.id, University.name, University.website).where(
                    University.id.in_(list(university_ids))
                )
            )
        ).all()
        universities = {row.id: row for row in uni_rows}

    countries: dict[UUID, Any] = {}
    if country_ids:
        country_rows = (
            await session.execute(
                select(Country.id, Country.name).where(Country.id.in_(list(country_ids)))
            )
        ).all()
        countries = {row.id: row for row in country_rows}

    majors: dict[int, Any] = {}
    if major_ids:
        major_rows = (
            await session.execute(
                select(Major.id, Major.name).where(Major.id.in_(list(major_ids)))
            )
        ).all()
        majors = {row.id: row for row in major_rows}

    minors: dict[int, Any] = {}
    if minor_ids:
        minor_rows = (
            await session.execute(
                select(Minor.id, Minor.name).where(Minor.id.in_(list(minor_ids)))
            )
        ).all()
        minors = {row.id: row for row in minor_rows}

    return universities, countries, majors, minors


def _academic_fields_for_row(
    row: Any,
    *,
    universities: dict[UUID, Any],
    countries: dict[UUID, Any],
    majors: dict[int, Any],
    minors: dict[int, Any],
) -> dict[str, Any]:
    university_id = _row_attr(row, "university_id", 13)
    country_id = _row_attr(row, "country_id", 18)
    major_id = _row_attr(row, "major_id", 15)
    minor_id = _row_attr(row, "minor_id", 17)
    raw_major = _row_attr(row, "major", 14)
    raw_minor = _row_attr(row, "minor", 16)
    edu_level = _row_attr(row, "edu_level", 19)

    uni_row = universities.get(university_id) if university_id is not None else None
    uni_name = getattr(uni_row, "name", None) if uni_row is not None else None
    uni_website = getattr(uni_row, "website", None) if uni_row is not None else None

    country_row = countries.get(country_id) if country_id is not None else None
    country_name = getattr(country_row, "name", None) if country_row is not None else None

    major_row = majors.get(major_id) if major_id is not None else None
    major_name = getattr(major_row, "name", None) if major_row is not None else raw_major
    resolved_major_id = getattr(major_row, "id", None) if major_row is not None else major_id

    minor_row = minors.get(minor_id) if minor_id is not None else None
    minor_name = getattr(minor_row, "name", None) if minor_row is not None else raw_minor
    resolved_minor_id = getattr(minor_row, "id", None) if minor_row is not None else minor_id

    education_level, education_level_details = _education_level_payload(edu_level)

    return {
        "university": uni_name if uni_name is not None else _stringify_id(university_id),
        "university_details": {
            "id": _stringify_id(university_id),
            "university_name": uni_name,
            "university_website": uni_website,
        },
        "major": major_name,
        "major_details": {
            "id": resolved_major_id,
            "major_name": major_name,
        },
        "minor": minor_name,
        "minor_details": {
            "id": resolved_minor_id,
            "minor_name": minor_name,
        },
        "country": _stringify_id(country_id),
        "country_details": {
            "id": _stringify_id(country_id),
            "country_name": country_name,
        },
        "educationLevel": education_level,
        "educationLevel_details": education_level_details,
    }


def _empty_admin_log_summary() -> dict[str, Any]:
    return SpotlightAdminLogSummary().model_dump()


def _build_logs_union_parts(*, user_id: UUID | None) -> list:
    interaction_stmt = select(
        LearningPaperInteraction.id.label("id"),
        LearningPaperInteraction.user_id.label("user_id"),
        LearningPaperInteraction.paper_id.label("paper_id"),
        LearningPaperInteraction.action.label("action"),
        LearningPaperInteraction.created_at.label("created_at"),
        LearningPaperInteraction.read_time_seconds.label("read_time_seconds"),
    ).where(LearningPaperInteraction.action.in_(sorted(INTERACTION_LOG_ACTIONS)))
    if user_id is not None:
        interaction_stmt = interaction_stmt.where(LearningPaperInteraction.user_id == user_id)

    content_stmt = select(
        LearningContent.id.label("id"),
        LearningContent.user_id.label("user_id"),
        LearningContent.paper_id.label("paper_id"),
        LearningContent.content_type.label("action"),
        LearningContent.created_at.label("created_at"),
        cast(None, Integer).label("read_time_seconds"),
    ).where(LearningContent.content_type.in_(sorted(CONTENT_LOG_ACTIONS)))
    if user_id is not None:
        content_stmt = content_stmt.where(LearningContent.user_id == user_id)

    return [interaction_stmt, content_stmt]


def _logs_union_subquery(parts: list):
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0].subquery("spotlight_logs")
    return union_all(*parts).subquery("spotlight_logs")


def _latest_action_subquery(logs_subq, *, actions: frozenset[str], label: str):
    ranked = (
        select(
            logs_subq.c.user_id.label("user_id"),
            logs_subq.c.paper_id.label("paper_id"),
            logs_subq.c.action.label("action"),
            func.row_number()
            .over(
                partition_by=(logs_subq.c.user_id, logs_subq.c.paper_id),
                order_by=logs_subq.c.created_at.desc(),
            )
            .label("rn"),
        )
        .where(logs_subq.c.action.in_(sorted(actions)))
        .subquery(f"{label}_ranked")
    )
    return (
        select(
            ranked.c.user_id,
            ranked.c.paper_id,
            ranked.c.action.label("latest_action"),
        )
        .where(ranked.c.rn == 1)
        .subquery(label)
    )


def _build_grouped_logs_subquery(logs_subq):
    grouped_base = (
        select(
            logs_subq.c.user_id.label("user_id"),
            logs_subq.c.paper_id.label("paper_id"),
            func.max(logs_subq.c.created_at).label("created_at"),
            func.bool_or(logs_subq.c.action == LearningPaperAction.read.value).label("is_read"),
            func.bool_or(logs_subq.c.action == "SUMMARY").label("is_summarizes"),
            func.bool_or(logs_subq.c.action == "SYNTHESIS").label("is_sythesis"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            logs_subq.c.action == LearningPaperAction.read.value,
                            logs_subq.c.read_time_seconds,
                        ),
                        else_=None,
                    )
                ),
                0,
            ).label("read_time_seconds"),
        )
        .group_by(logs_subq.c.user_id, logs_subq.c.paper_id)
        .subquery("grouped_base")
    )

    latest_save = _latest_action_subquery(
        logs_subq,
        actions=SAVE_LOG_ACTIONS,
        label="latest_save",
    )
    latest_like = _latest_action_subquery(
        logs_subq,
        actions=LIKE_LOG_ACTIONS,
        label="latest_like",
    )

    is_saved = case(
        (latest_save.c.latest_action == LearningPaperAction.save.value, True),
        else_=False,
    ).label("is_saved")
    is_like = case(
        (latest_like.c.latest_action == LearningPaperAction.like.value, True),
        (latest_like.c.latest_action == LearningPaperAction.dislike.value, False),
        else_=None,
    ).label("is_like")
    is_skip = case(
        (
            and_(
                grouped_base.c.is_read.is_(True),
                latest_like.c.latest_action.is_(None),
            ),
            True,
        ),
        else_=False,
    ).label("is_skip")

    return (
        select(
            grouped_base.c.user_id,
            grouped_base.c.paper_id,
            grouped_base.c.created_at,
            is_saved,
            grouped_base.c.is_summarizes,
            grouped_base.c.is_sythesis,
            grouped_base.c.is_read,
            is_like,
            is_skip,
            grouped_base.c.read_time_seconds,
        )
        .select_from(grouped_base)
        .outerjoin(
            latest_save,
            and_(
                latest_save.c.user_id == grouped_base.c.user_id,
                latest_save.c.paper_id == grouped_base.c.paper_id,
            ),
        )
        .outerjoin(
            latest_like,
            and_(
                latest_like.c.user_id == grouped_base.c.user_id,
                latest_like.c.paper_id == grouped_base.c.paper_id,
            ),
        )
        .subquery("grouped_logs")
    )


def _joined_grouped_logs_query(grouped_subq):
    return (
        select(
            grouped_subq.c.user_id,
            grouped_subq.c.paper_id,
            grouped_subq.c.created_at,
            grouped_subq.c.is_saved,
            grouped_subq.c.is_summarizes,
            grouped_subq.c.is_sythesis,
            grouped_subq.c.is_read,
            grouped_subq.c.is_like,
            grouped_subq.c.is_skip,
            grouped_subq.c.read_time_seconds,
            User.email,
            Profile.first_name,
            Profile.last_name,
            Profile.university_id,
            Profile.major,
            Profile.major_id,
            Profile.minor,
            Profile.minor_id,
            Profile.country_id,
            Profile.edu_level,
        )
        .select_from(grouped_subq)
        .join(User, User.id == grouped_subq.c.user_id)
        .outerjoin(Profile, Profile.user_id == User.id)
    )


async def _apply_grouped_search_filter(
    session: AsyncSession,
    base,
    grouped_subq,
    *,
    search: str | None,
    user_id: UUID | None,
):
    if not search or not search.strip():
        return base

    term = search.strip()
    matching_paper_ids = await _find_paper_ids_by_title_search(
        session,
        term,
        user_id=user_id,
    )
    pattern = f"%{term}%"
    title_conditions = [grouped_subq.c.paper_id.ilike(pattern)]
    if matching_paper_ids:
        title_conditions.append(grouped_subq.c.paper_id.in_(list(matching_paper_ids)))
    name_conditions = [
        Profile.first_name.ilike(pattern),
        Profile.last_name.ilike(pattern),
        func.concat_ws(" ", Profile.first_name, Profile.last_name).ilike(pattern),
    ]
    academic_conditions = [
        University.name.ilike(pattern),
        Country.name.ilike(pattern),
        Major.name.ilike(pattern),
        Profile.major.ilike(pattern),
    ]
    base = (
        base.outerjoin(University, University.id == Profile.university_id)
        .outerjoin(Country, Country.id == Profile.country_id)
        .outerjoin(Major, Major.id == Profile.major_id)
    )
    return base.where(or_(*title_conditions, *name_conditions, *academic_conditions))


def _apply_grouped_state_filters(base, grouped_subq, filters: GroupedLogFilters):
    """Apply state filters with OR so any matching flag includes the row."""
    conditions = []
    if filters.is_saved is not None:
        conditions.append(grouped_subq.c.is_saved.is_(filters.is_saved))
    if filters.is_summarizes is not None:
        conditions.append(grouped_subq.c.is_summarizes.is_(filters.is_summarizes))
    if filters.is_sythesis is not None:
        conditions.append(grouped_subq.c.is_sythesis.is_(filters.is_sythesis))
    if filters.is_read is not None:
        conditions.append(grouped_subq.c.is_read.is_(filters.is_read))
    if filters.is_skip is not None:
        conditions.append(grouped_subq.c.is_skip.is_(filters.is_skip))
    if filters.is_like is not IS_LIKE_FILTER_UNSET:
        if filters.is_like is None:
            conditions.append(grouped_subq.c.is_like.is_(None))
        else:
            conditions.append(grouped_subq.c.is_like.is_(filters.is_like))
    if not conditions:
        return base
    return base.where(or_(*conditions))


def _summary_from_filtered_subquery(filtered_subq):
    return select(
        func.coalesce(
            func.sum(case((filtered_subq.c.is_saved.is_(True), 1), else_=0)),
            0,
        ).label("is_saved"),
        func.coalesce(
            func.sum(case((filtered_subq.c.is_summarizes.is_(True), 1), else_=0)),
            0,
        ).label("is_summarizes"),
        func.coalesce(
            func.sum(case((filtered_subq.c.is_sythesis.is_(True), 1), else_=0)),
            0,
        ).label("is_sythesis"),
        func.coalesce(
            func.sum(case((filtered_subq.c.is_read.is_(True), 1), else_=0)),
            0,
        ).label("is_read"),
        func.coalesce(
            func.sum(case((filtered_subq.c.is_like.is_(True), 1), else_=0)),
            0,
        ).label("is_like_true"),
        func.coalesce(
            func.sum(case((filtered_subq.c.is_like.is_(False), 1), else_=0)),
            0,
        ).label("is_like_false"),
        func.coalesce(
            func.sum(case((filtered_subq.c.is_like.is_(None), 1), else_=0)),
            0,
        ).label("is_like_null"),
        func.coalesce(
            func.sum(case((filtered_subq.c.is_skip.is_(True), 1), else_=0)),
            0,
        ).label("is_skip"),
    ).select_from(filtered_subq)


def _summary_row_to_dict(row: Any) -> dict[str, Any]:
    return SpotlightAdminLogSummary(
        is_saved=int(_row_attr(row, "is_saved", 0) or 0),
        is_summarizes=int(_row_attr(row, "is_summarizes", 1) or 0),
        is_sythesis=int(_row_attr(row, "is_sythesis", 2) or 0),
        is_read=int(_row_attr(row, "is_read", 3) or 0),
        is_like=SpotlightAdminLogLikeSummary(
            true=int(_row_attr(row, "is_like_true", 4) or 0),
            false=int(_row_attr(row, "is_like_false", 5) or 0),
            null=int(_row_attr(row, "is_like_null", 6) or 0),
        ),
        is_skip=int(_row_attr(row, "is_skip", 7) or 0),
    ).model_dump()


def _paginated_response_with_summary(
    items: list[dict[str, Any]],
    *,
    summary: dict[str, Any],
    page: int | None,
    page_size: int | None,
    total_items: int,
) -> dict[str, Any]:
    paginated = paginate_or_all(
        items,
        page=page if page is not None and page_size is not None else None,
        page_size=page_size if page is not None and page_size is not None else None,
        total_items=total_items,
    ).model_dump()
    paginated["summary"] = summary
    return paginated


def _grouped_logs_order_by(filtered_subq, *, sort_by: str | None, order: str):
    descending = order.strip().lower() != "asc"
    if sort_by == "read_time_seconds":
        sort_column = filtered_subq.c.read_time_seconds
        if descending:
            return (
                sort_column.desc(),
                filtered_subq.c.created_at.desc(),
                filtered_subq.c.user_id.desc(),
                filtered_subq.c.paper_id.desc(),
            )
        return (
            sort_column.asc(),
            filtered_subq.c.created_at.desc(),
            filtered_subq.c.user_id.desc(),
            filtered_subq.c.paper_id.desc(),
        )
    return (
        filtered_subq.c.created_at.desc(),
        filtered_subq.c.user_id.desc(),
        filtered_subq.c.paper_id.desc(),
    )


async def list_learning_spotlight_logs(
    session: AsyncSession,
    *,
    user_id: UUID | None = None,
    search: str | None = None,
    filters: GroupedLogFilters | None = None,
    page: int | None = None,
    page_size: int | None = None,
    sort_by: str | None = None,
    order: str = "desc",
) -> dict[str, Any]:
    """Return grouped Learning Spotlight logs keyed by user_id + paper_id."""
    active_filters = filters or GroupedLogFilters()
    parts = _build_logs_union_parts(user_id=user_id)
    logs_subq = _logs_union_subquery(parts)
    if logs_subq is None:
        summary = _empty_admin_log_summary()
        return _paginated_response_with_summary(
            [],
            summary=summary,
            page=page,
            page_size=page_size,
            total_items=0,
        )

    grouped_subq = _build_grouped_logs_subquery(logs_subq)
    base = _joined_grouped_logs_query(grouped_subq)
    base = await _apply_grouped_search_filter(
        session,
        base,
        grouped_subq,
        search=search,
        user_id=user_id,
    )
    base = _apply_grouped_state_filters(base, grouped_subq, active_filters)

    filtered_subq = base.subquery("filtered_grouped_logs")

    total_items = int(
        (await session.execute(select(func.count()).select_from(filtered_subq))).scalar_one()
        or 0
    )
    summary_result = await session.execute(_summary_from_filtered_subquery(filtered_subq))
    summary_row = summary_result.first()
    summary = _summary_row_to_dict(summary_row) if summary_row is not None else _empty_admin_log_summary()

    rows_stmt = select(filtered_subq).order_by(
        *_grouped_logs_order_by(filtered_subq, sort_by=sort_by, order=order)
    )
    if page is not None and page_size is not None:
        offset = (page - 1) * page_size
        rows_stmt = rows_stmt.offset(offset).limit(page_size)

    rows = (await session.execute(rows_stmt)).all()
    user_ids: list[UUID] = []
    paper_ids: list[str] = []
    seen_users: set[UUID] = set()
    for row in rows:
        uid = _row_attr(row, "user_id", 0)
        pid = str(_row_attr(row, "paper_id", 1) or "").strip()
        if uid is not None and uid not in seen_users:
            seen_users.add(uid)
            user_ids.append(uid)
        if pid:
            paper_ids.append(pid)

    titles, cycle_names = await _resolve_paper_metadata(
        session, user_ids=user_ids, paper_ids=paper_ids
    )

    university_ids = {
        uid for uid in (_row_attr(row, "university_id", 13) for row in rows) if uid is not None
    }
    country_ids = {
        cid for cid in (_row_attr(row, "country_id", 18) for row in rows) if cid is not None
    }
    major_ids = {
        mid for mid in (_row_attr(row, "major_id", 15) for row in rows) if mid is not None
    }
    minor_ids = {
        nid for nid in (_row_attr(row, "minor_id", 17) for row in rows) if nid is not None
    }
    universities, countries, majors, minors = await _resolve_academic_catalogs(
        session,
        university_ids=university_ids,
        country_ids=country_ids,
        major_ids=major_ids,
        minor_ids=minor_ids,
    )

    items = []
    for row in rows:
        paper_id = str(_row_attr(row, "paper_id", 1) or "").strip()
        is_like_value = _row_attr(row, "is_like", 7)
        read_time_seconds = int(_row_attr(row, "read_time_seconds", 9) or 0)
        academic = _academic_fields_for_row(
            row,
            universities=universities,
            countries=countries,
            majors=majors,
            minors=minors,
        )
        items.append(
            SpotlightAdminLogItem(
                user_id=_row_attr(row, "user_id", 0),
                first_name=_row_attr(row, "first_name", 11),
                last_name=_row_attr(row, "last_name", 12),
                email=_row_attr(row, "email", 10),
                paper_title=titles.get(paper_id) or paper_id,
                paper_id=paper_id,
                is_saved=bool(_row_attr(row, "is_saved", 3)),
                is_summarizes=bool(_row_attr(row, "is_summarizes", 4)),
                is_sythesis=bool(_row_attr(row, "is_sythesis", 5)),
                is_read=bool(_row_attr(row, "is_read", 6)),
                is_like=is_like_value if is_like_value is not None else None,
                is_skip=bool(_row_attr(row, "is_skip", 8)),
                read_time_seconds=read_time_seconds,
                created_at=_row_attr(row, "created_at", 2),
                recommended_cycle_name=cycle_names.get(paper_id),
                **academic,
            ).model_dump(mode="json")
        )

    return _paginated_response_with_summary(
        items,
        summary=summary,
        page=page,
        page_size=page_size,
        total_items=total_items,
    )
