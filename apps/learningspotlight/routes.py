"""Learning Spotlight HTTP endpoints (V2).

Exposes:
* GET /learning-spotlight — Retrieves the current user's active Learning Spotlight.
* PATCH /learning-spotlight/read — Marks the current spotlight as read.
* PATCH /learning-spotlight/save — Saves/unsaves a paper (current or previous cycle).
* PATCH /learning-spotlight/feedback — Submits rating feedback (useful / not_useful).
* GET /admin/learning-spotlight/users — Admin list of users with Learning Spotlight fields.
* GET /admin/learning-spotlight-logs — Admin list of grouped Learning Spotlight user/paper logs.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import User
from apps.administration.services.user_management_service import (
    list_learning_spotlight_users,
)
from apps.learningspotlight.schemas import (
    PaperSummarizeRequest,
    PaperSynthesizeRequest,
    SpotlightFeedbackRequest,
    SpotlightReadRequest,
    SpotlightSaveRequest,
)
from apps.learningspotlight.services.admin_logs_service import (
    GroupedLogFilters,
    list_learning_spotlight_logs,
    parse_is_like_query_param,
)
from apps.learningspotlight.services.spotlight_persistence_service import (
    SpotlightPersistenceService,
)
from common.enums import UserListStatus
from common.pagination import OptionalPaginationParams
from common.schemas import ApiResponse
from apps.administration.dependencies import require_signed_admin
from core.database.session import get_session
from core.security.auth import get_current_user

logger = logging.getLogger(__name__)


router = APIRouter(tags=["Learning Spotlight"])


@router.get("/learning-spotlight", response_model=ApiResponse)
async def get_current_learning_spotlight(
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_session),
    service: SpotlightPersistenceService = Depends(SpotlightPersistenceService),
) -> ApiResponse:
    """Return the authenticated user's active Learning Spotlight snapshot.

    Reads ``profiles.learning_spotlight`` only when ``generated_at`` is today
    (UTC). Does not call Semantic Scholar or trigger paper generation. If no
    active (today) spotlight exists, returns an ApiResponse with ``data: None``.
    """
    spotlight = await service.get_current_spotlight(db, current_user.id)
    if spotlight is None:
        return ApiResponse(
            status=True,
            message="No active learning spotlight found.",
            data=None,
        )

    return ApiResponse(
        status=True,
        message="Learning spotlight retrieved successfully.",
        data=spotlight.model_dump(mode="json"),
    )


@router.patch("/learning-spotlight/read", response_model=ApiResponse)
async def mark_learning_spotlight_read(
    payload: SpotlightReadRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_session),
    service: SpotlightPersistenceService = Depends(SpotlightPersistenceService),
) -> ApiResponse:
    """Update engagement.is_read on the active Learning Spotlight paper."""
    has_start = payload.start_time is not None
    has_end = payload.end_time is not None
    if has_start != has_end:
        raise HTTPException(
            status_code=400,
            detail="start_time and end_time must both be provided or both omitted.",
        )
    if has_start and has_end and payload.end_time < payload.start_time:
        raise HTTPException(
            status_code=400,
            detail="end_time must not be before start_time.",
        )

    read_time_seconds: int | None = None
    if has_start and has_end:
        read_time_seconds = int((payload.end_time - payload.start_time).total_seconds())

    updated = await service.update_read_status(
        db,
        current_user.id,
        is_read=payload.is_read if payload.is_read is not None else True,
        paper_id=payload.paper_id,
        read_time_seconds=read_time_seconds,
    )
    return ApiResponse(
        status=True,
        message="Learning spotlight marked as read.",
        data=updated.model_dump(mode="json"),
    )


@router.patch("/learning-spotlight/save", response_model=ApiResponse)
async def update_learning_spotlight_saved_status(
    payload: SpotlightSaveRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_session),
    service: SpotlightPersistenceService = Depends(SpotlightPersistenceService),
) -> ApiResponse:
    """Save or unsave a Learning Spotlight paper.

    ``paper_id`` may be on the current cycle or a previously saved paper from
    an earlier cycle. Unsaving a historical paper records an UNSAVE event so
    it is removed from GET /learning-spotlight/saved.
    """
    updated = await service.update_save_status(
        db,
        current_user.id,
        is_saved=payload.is_saved,
        paper_id=payload.paper_id,
    )
    msg = "saved" if payload.is_saved else "unsaved"
    return ApiResponse(
        status=True,
        message=f"Learning spotlight {msg} successfully.",
        data=updated.model_dump(mode="json"),
    )


@router.patch("/learning-spotlight/feedback", response_model=ApiResponse)
async def submit_learning_spotlight_feedback(
    payload: SpotlightFeedbackRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_session),
    service: SpotlightPersistenceService = Depends(SpotlightPersistenceService),
) -> ApiResponse:
    """Update engagement.feedback on the active Learning Spotlight paper."""
    updated = await service.submit_feedback(
        db,
        current_user.id,
        feedback=payload.feedback,
        paper_id=payload.paper_id,
    )
    return ApiResponse(
        status=True,
        message="Learning spotlight feedback submitted successfully.",
        data=updated.model_dump(mode="json"),
    )



@router.get("/learning-spotlight/saved", response_model=ApiResponse)
async def get_saved_learning_spotlight_papers(
    current_user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_session),
    service: SpotlightPersistenceService = Depends(SpotlightPersistenceService),
) -> ApiResponse:
    """Retrieve all papers currently saved by the authenticated user."""
    saved_papers = await service.get_saved_papers(db, current_user.id)
    return ApiResponse(
        status=True,
        message="Saved learning spotlight papers retrieved successfully.",
        data=[p.model_dump(mode="json") for p in saved_papers],
    )


@router.post("/papers/{paper_id}/summarize", response_model=ApiResponse)
async def summarize_paper_endpoint(
    paper_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    payload: PaperSummarizeRequest | None = None,
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    """Generate or retrieve an extractive non-LLM summary for a research paper."""
    title = payload.title if payload else None
    abstract = payload.abstract if payload else None

    from apps.learningspotlight.services.summarization_service import (
        PaperSummarizationService,
    )

    summary, is_new = await PaperSummarizationService.get_or_create_summary(
        db,
        user_id=current_user.id,
        paper_id=paper_id,
        title=title,
        abstract=abstract,
    )

    if not summary:
        return ApiResponse(
            status=False,
            message="Unable to summarize paper: abstract is missing or empty.",
            data=None,
        )

    return ApiResponse(
        status=True,
        message="Paper summarized successfully.",
        data={
            "paper_id": paper_id,
            "content_type": "SUMMARY",
            "content": summary,
        },
    )


@router.post("/papers/{paper_id}/synthesize", response_model=ApiResponse)
async def synthesize_paper_endpoint(
    paper_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    payload: PaperSynthesizeRequest | None = None,
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    """Generate or retrieve structured comparative synthesis notes across related research."""
    title = payload.title if payload else None
    abstract = payload.abstract if payload else None

    from apps.learningspotlight.services.synthesis_service import (
        PaperSynthesisService,
    )

    data, _is_new, error_msg = await PaperSynthesisService.get_or_create_synthesis(
        db,
        user_id=current_user.id,
        paper_id=paper_id,
        title=title,
        abstract=abstract,
    )

    if data is not None and data.get("searching"):
        return ApiResponse(
            status=True,
            message="Searching for related research.",
            data=data,
        )

    if error_msg or data is None:
        return ApiResponse(
            status=False,
            message=error_msg or "Unable to generate paper synthesis.",
            data=None,
        )

    return ApiResponse(
        status=True,
        message="Paper synthesized successfully.",
        data=data,
    )


@router.get("/admin/learning-spotlight/users", response_model=ApiResponse)
async def list_learning_spotlight_users_endpoint(
    current_admin: Annotated[User, Depends(require_signed_admin)],
    db: AsyncSession = Depends(get_session),
    page: int | None = Query(default=None, ge=1),
    pageSize: int | None = Query(default=None, ge=1, le=200),
    search: str | None = Query(
        default=None,
        description="Search across university, name, or email",
    ),
    status: UserListStatus | None = Query(
        default=None,
        description="Filter users by status: Pending, Active, Suspended, Banned, Deleted",
    ),
    is_learning_spotlight_recommended: bool | None = Query(
        default=None,
        description=(
            "Filter by whether Learning Spotlight was recommended today (UTC). "
            "true = recommended today, false = not recommended today"
        ),
    ),
) -> ApiResponse:
    """Return admin user list items plus Learning Spotlight profile fields."""
    _ = current_admin
    data = await list_learning_spotlight_users(
        page,
        pageSize,
        db,
        search=search,
        status=status,
        is_learning_spotlight_recommended=is_learning_spotlight_recommended,
    )
    return ApiResponse(
        status=True,
        message="learning spotlight users listed",
        data=data,
    )


@router.get("/admin/learning-spotlight-logs", response_model=ApiResponse)
async def list_learning_spotlight_logs_endpoint(
    current_admin: Annotated[User, Depends(require_signed_admin)],
    db: AsyncSession = Depends(get_session),
    user_id: UUID | None = Query(default=None, description="Filter logs by user id."),
    search: str | None = Query(
        default=None,
        description="Search by paper title, first name, last name, university, country, or major.",
    ),
    is_saved: bool | None = Query(default=None, description="Filter by latest save state."),
    is_summarizes: bool | None = Query(default=None, description="Filter by summary state."),
    is_sythesis: bool | None = Query(default=None, description="Filter by synthesis state."),
    is_read: bool | None = Query(default=None, description="Filter by read state."),
    is_like: str | None = Query(
        default=None,
        description='Filter by like state: "true", "false", or "null".',
    ),
    is_skip: bool | None = Query(default=None, description="Filter by skip state."),
    sort_by: str | None = Query(
        default=None,
        description="Sort grouped logs. Supported: read_time_seconds.",
    ),
    order: str = Query(
        default="desc",
        description="Sort direction when sort_by is set: asc or desc.",
    ),
    pagination: OptionalPaginationParams = Depends(),
) -> ApiResponse:
    """Return grouped Learning Spotlight interaction logs for admin review.

    State filters (is_saved, is_summarizes, is_sythesis, is_read, is_like,
    is_skip) are combined with OR. user_id and search still narrow the set.
    """
    _ = current_admin
    try:
        is_like_filter = parse_is_like_query_param(is_like)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    data = await list_learning_spotlight_logs(
        db,
        user_id=user_id,
        search=search,
        filters=GroupedLogFilters(
            is_saved=is_saved,
            is_summarizes=is_summarizes,
            is_sythesis=is_sythesis,
            is_read=is_read,
            is_like=is_like_filter,
            is_skip=is_skip,
        ),
        page=pagination.page,
        page_size=pagination.pageSize,
        sort_by=sort_by,
        order=order,
    )
    return ApiResponse(
        status=True,
        message="Learning spotlight logs retrieved successfully.",
        data=data,
    )


@router.post("/admin/spotlight/runcron", response_model=ApiResponse, status_code=202)
async def manual_run_learning_spotlight_cron(
    current_admin: Annotated[User, Depends(require_signed_admin)],
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    """Queue learning spotlight generation for a Celery worker."""
    from fastapi import HTTPException

    from apps.recommendations.services.recommendation_settings_service import (
        RecommendationSettingsService,
    )
    from core.celery_worker.config import CeleryTaskQueue
    from core.jobs.publishing import publish_admin_task

    settings_service = RecommendationSettingsService()
    if not await settings_service.try_claim_manual_spotlight_run(db):
        raise HTTPException(
            status_code=409,
            detail="Learning Spotlight generation is already running.",
        )

    try:
        task_id = await publish_admin_task(
            "kampulynk.spotlight.tick",
            CeleryTaskQueue.SPOTLIGHTS_QUEUE,
            run_mode="manual",
            triggered_by_user_id=str(current_admin.id),
            triggered_by_role=str(current_admin.role),
        )
    except HTTPException:
        await settings_service.set_is_running(db, is_running=False)
        raise
    return ApiResponse(
        message="Learning Spotlight generation queued.",
        data={"task_id": task_id, "status": "queued"},
    )



