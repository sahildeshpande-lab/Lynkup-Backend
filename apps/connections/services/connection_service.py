from __future__ import annotations
from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy import or_, and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from apps.connections.db_models import Block, Connection, ConnectionRequest
from apps.profiles.db_models.profile_db_model import Profile
from apps.profiles.db_models import Profile
from ..schemas import ApiResponse

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def build_connection_pair(user_id_1: UUID, user_id_2: UUID) -> tuple[UUID, UUID]:
    if user_id_1 < user_id_2:
        return user_id_1, user_id_2
    return user_id_2, user_id_1

async def is_blocked(db: AsyncSession, user_id_1: UUID, user_id_2: UUID) -> bool:
    stmt = select(Block).where(
        Block.is_active == True,
        or_(
            and_(Block.blocker_user_id == user_id_1, Block.blocked_user_id == user_id_2),
            and_(Block.blocker_user_id == user_id_2, Block.blocked_user_id == user_id_1)
        )
    )
    result = await db.execute(stmt)
    return result.scalars().first() is not None

async def are_connected(db: AsyncSession, user_id_1: UUID, user_id_2: UUID) -> bool:
    low_id, high_id = build_connection_pair(user_id_1, user_id_2)
    stmt = select(Connection).where(
        Connection.user_low_id == low_id,
        Connection.user_high_id == high_id,
        Connection.is_active == True
    )
    result = await db.execute(stmt)
    return result.scalars().first() is not None

async def has_pending_request(db: AsyncSession, user_id_1: UUID, user_id_2: UUID) -> bool:
    stmt = select(ConnectionRequest).where(
        ConnectionRequest.status == "pending",
        or_(
            and_(ConnectionRequest.sender_user_id == user_id_1, ConnectionRequest.receiver_user_id == user_id_2),
            and_(ConnectionRequest.sender_user_id == user_id_2, ConnectionRequest.receiver_user_id == user_id_1)
        )
    )
    result = await db.execute(stmt)
    return result.scalars().first() is not None

async def send_connection_request(db: AsyncSession, sender_id: UUID, receiver_id: UUID) -> ApiResponse:
    if sender_id == receiver_id:
        return ApiResponse(status=True, message="Cannot send request to self.", data=[])

    if await is_blocked(db, sender_id, receiver_id):
        return ApiResponse(status=True, message="Cannot send request due to block.", data=[])

    if await are_connected(db, sender_id, receiver_id):
        return ApiResponse(status=True, message="Already connected.", data=[])

    if await has_pending_request(db, sender_id, receiver_id):
        return ApiResponse(status=True, message="Connection request already sent..", flags={
        "active_request_exists": True
    }, data=[])

    request = ConnectionRequest(sender_user_id=sender_id, receiver_user_id=receiver_id, status="pending")
    db.add(request)
    await db.commit()
    await db.refresh(request)
    return ApiResponse(status=True, message="Connection request sent successfully.", data=request)

async def respond_connection_request(db: AsyncSession, user_id: UUID, other_user_id: UUID, response: str) -> ApiResponse:
    if response not in ["accepted", "declined"]:
        return ApiResponse(status=True, message="Invalid response.", data=[])

    stmt = select(ConnectionRequest).where(
        ConnectionRequest.status == "pending",
        or_(
            and_(ConnectionRequest.sender_user_id == other_user_id, ConnectionRequest.receiver_user_id == user_id),
            and_(ConnectionRequest.sender_user_id == user_id, ConnectionRequest.receiver_user_id == other_user_id)
        )
    )
    result = await db.execute(stmt)
    req = result.scalars().first()

    if not req:
        return ApiResponse(status=True, message="Pending request not found.", data=[])

    req.status = response

    if response == "accepted":
        low_id, high_id = build_connection_pair(req.sender_user_id, req.receiver_user_id)
        # Check if they are already connected before creating
        conn_check = select(Connection).where(Connection.user_low_id == low_id, Connection.user_high_id == high_id)
        conn_res = await db.execute(conn_check)
        existing_conn = conn_res.scalars().first()

        if existing_conn:
            existing_conn.is_active = True
        else:
            new_conn = Connection(user_low_id=low_id, user_high_id=high_id)
            db.add(new_conn)

    await db.commit()
    await db.refresh(req)

    # Queue email to the person who originally sent the request
    from apps.accounts.db_models import User
    from apps.profiles.db_models import Profile
    from core.email_service import send_lynkup_response_email
    import logging
    logger = logging.getLogger(__name__)
    logger.info("Attempting to send Lynkup response email for request %s", req.id)
    try:
        sender_stmt = select(User, Profile).join(Profile, Profile.user_id == User.id).where(User.id == req.sender_user_id)
        sender_result = await db.execute(sender_stmt)
        sender_row = sender_result.first()
        if sender_row:
            sender_user, sender_profile = sender_row
            full_name = f"{sender_profile.first_name} {sender_profile.last_name}".strip() if sender_profile else None
            await send_lynkup_response_email(sender_user.email, response, full_name)
            logger.info("Lynkup response email queued for %s", sender_user.email)
        else:
            logger.warning("Sender user not found for Lynkup response email, user_id=%s", req.sender_user_id)
    except Exception as e:
        logger.exception("Failed to queue Lynkup response email: %s", e)

    return ApiResponse(status=True, message=f"Request {response} successfully.", data=req)

async def get_pending_requests(
    db: AsyncSession,
    user_id: UUID,
    page: int | None = None,
    page_size: int | None = None,
    search: str | None = None,
) -> ApiResponse:
    from common.pagination import paginate_items

    stmt = select(ConnectionRequest, Profile).join(
        Profile, Profile.user_id == ConnectionRequest.sender_user_id
    ).where(
        ConnectionRequest.receiver_user_id == user_id,
        ConnectionRequest.status == "pending"
    )

    if search:
        search_pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                Profile.first_name.ilike(search_pattern),
                Profile.last_name.ilike(search_pattern)
            )
        )

    result = await db.execute(stmt)
    rows = result.all()

    data = [
        {
            "lynkup_id": req.id,
            "user_id": req.sender_user_id,
            "status": req.status,
            "first_name": profile.first_name,
            "last_name": profile.last_name,
            "profile_photo_key": profile.profile_photo_url,
        }
        for req, profile in rows
    ]

    if page is None and page_size is None:
        # if not provided: ALL
        return ApiResponse(status=True, message="Lynkup Request pending", data=data)

    p = page or 1
    ps = page_size or 20
    paginated = paginate_items(data, page=p, page_size=ps)
    return ApiResponse(status=True, message="Lynkup Request pending", data=paginated.model_dump())
