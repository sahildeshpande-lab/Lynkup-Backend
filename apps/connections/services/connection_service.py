from __future__ import annotations
from datetime import datetime, timezone
import logging
from uuid import UUID
from sqlalchemy import or_, and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from apps.accounts.db_models import User
from apps.connections.db_models import Block, Connection, ConnectionRequest, Follow
from apps.notifications.services import create_notification
from apps.profiles.db_models import Profile
from ..schemas import ApiResponse
from common.enums import UserStatus
from common.responses import error_response, success_response
from common.user_visibility import visible_user_filters
from core.images import generate_profile_image_url

logger = logging.getLogger(__name__)


DEFAULT_RELATIONSHIP_FLAGS: dict[str, bool] = {
    "is_connected": False,
    "is_followed": False,
    "is_blocked": False,
    "request_sent": False,
    "request_received": False,
}


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

async def _clear_conflicting_connection_requests(
    db: AsyncSession,
    *,
    sender_user_id: UUID,
    receiver_user_id: UUID,
    status: str,
    exclude_request_id: UUID,
) -> None:
    """
    Remove prior rows with the same sender/receiver/status.

    The connection_requests table enforces uniqueness on (sender, receiver, status).
    Re-decline or re-accept after a prior outcome leaves a historical row that would
    cause IntegrityError when updating the current pending request.
    """
    stmt = select(ConnectionRequest).where(
        ConnectionRequest.sender_user_id == sender_user_id,
        ConnectionRequest.receiver_user_id == receiver_user_id,
        ConnectionRequest.status == status,
        ConnectionRequest.id != exclude_request_id,
    )
    conflicting = (await db.execute(stmt)).scalars().all()
    for row in conflicting:
        await db.delete(row)
    if conflicting:
        await db.flush()
        logger.info(
            "Removed %s conflicting connection request row(s) sender=%s receiver=%s status=%s",
            len(conflicting),
            sender_user_id,
            receiver_user_id,
            status,
        )

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
        return error_response("Cannot send request to self.", response_cls=ApiResponse)

    receiver = (await db.execute(select(User).where(User.id == receiver_id))).scalar_one_or_none()
    if receiver is None:
        return error_response("User not found.", response_cls=ApiResponse)
    if receiver.deleted_at or receiver.is_deleted:
        return error_response("Cannot send request. User account is deleted.", response_cls=ApiResponse)
    if receiver.status == UserStatus.suspended:
        return error_response("Cannot send request. User account is suspended.", response_cls=ApiResponse)
    if receiver.status == UserStatus.banned:
        return error_response("Cannot send request. User account is banned.", response_cls=ApiResponse)
    if receiver.status == UserStatus.deleting:
        return error_response("Cannot send request. User account is deleting.", response_cls=ApiResponse)
    if receiver.status != UserStatus.active:
        return error_response("Cannot send request. User account is not active.", response_cls=ApiResponse)

    if await is_blocked(db, sender_id, receiver_id):
        return error_response("Cannot send request due to block.", response_cls=ApiResponse)

    if await are_connected(db, sender_id, receiver_id):
        return error_response("Already connected.", response_cls=ApiResponse)

    if await has_pending_request(db, sender_id, receiver_id):
        return error_response(
            "Connection request already sent.",
            response_cls=ApiResponse,
        )

    request = ConnectionRequest(sender_user_id=sender_id, receiver_user_id=receiver_id, status="pending")
    db.add(request)
    await db.commit()
    await db.refresh(request)

    try:
        sender_profile = (
            await db.execute(select(Profile).where(Profile.user_id == sender_id))
        ).scalar_one_or_none()
        sender_name = "Someone"
        if sender_profile is not None:
            sender_name = (
                f"{sender_profile.first_name or ''} {sender_profile.last_name or ''}".strip()
                or "Someone"
            )
        await create_notification(
            db,
            recipient_user_id=receiver_id,
            notification_type="CONNECTION_REQUEST",
            title="Connection Request",
            body=f"{sender_name} sent you a connection request.",
            sender_user_id=sender_id,
        )
    except Exception:
        logger.exception(
            "Failed to send CONNECTION_REQUEST notification sender=%s receiver=%s",
            sender_id,
            receiver_id,
        )

    return success_response(
        "Connection request sent successfully.",
        {
            "lynkup_id": request.id,
            "sender_user_id": request.sender_user_id,
            "receiver_user_id": request.receiver_user_id,
            "status": request.status,
            "request_sent": True,
            "request_received": False,
        },
        response_cls=ApiResponse,
    )

async def respond_connection_request(db: AsyncSession, user_id: UUID, other_user_id: UUID, response: str) -> ApiResponse:
    if response not in ["accepted", "declined"]:
        return error_response("Invalid response.", response_cls=ApiResponse)

    stmt = select(ConnectionRequest).where(
        ConnectionRequest.status == "pending",
        ConnectionRequest.sender_user_id == other_user_id,
        ConnectionRequest.receiver_user_id == user_id,
    )
    result = await db.execute(stmt)
    req = result.scalars().first()

    if not req:
        return error_response("Pending request not found.", response_cls=ApiResponse)

    request_id = req.id
    sender_user_id = req.sender_user_id
    receiver_user_id = req.receiver_user_id
    await _clear_conflicting_connection_requests(
        db,
        sender_user_id=sender_user_id,
        receiver_user_id=receiver_user_id,
        status=response,
        exclude_request_id=request_id,
    )
    req.status = response

    if response == "accepted":
        low_id, high_id = build_connection_pair(sender_user_id, receiver_user_id)
        conn_check = select(Connection).where(Connection.user_low_id == low_id, Connection.user_high_id == high_id)
        conn_res = await db.execute(conn_check)
        existing_conn = conn_res.scalars().first()

        should_increment = False
        if existing_conn:
            if not existing_conn.is_active:
                existing_conn.is_active = True
                should_increment = True
        else:
            new_conn = Connection(user_low_id=low_id, user_high_id=high_id)
            db.add(new_conn)
            should_increment = True

        if should_increment:
            from apps.profiles.services.profile_stats_service import increment_connection_counts_for_users
            await increment_connection_counts_for_users(
                db, sender_user_id, receiver_user_id
            )

    try:
        await db.commit()
        await db.refresh(req)
    except IntegrityError:
        await db.rollback()
        logger.exception(
            "Failed to persist connection request response request_id=%s sender=%s receiver=%s response=%s",
            request_id,
            sender_user_id,
            receiver_user_id,
            response,
        )
        return error_response("Failed to update connection request.", response_cls=ApiResponse)

    # Temporarily disabled: connection request accepted/declined email
    # # Queue email to the person who originally sent the request
    # from apps.accounts.db_models import User
    # from apps.profiles.db_models import Profile
    # from core.email_service import send_lynkup_response_email
    # import logging
    # logger = logging.getLogger(__name__)
    # logger.info("Attempting to send Lynkup response email for request %s", req.id)
    # try:
    #     sender_stmt = select(User, Profile).join(Profile, Profile.user_id == User.id).where(User.id == req.sender_user_id)
    #     sender_result = await db.execute(sender_stmt)
    #     sender_row = sender_result.first()
    #     if sender_row:
    #         sender_user, sender_profile = sender_row
    #         full_name = f"{sender_profile.first_name} {sender_profile.last_name}".strip() if sender_profile else None
    #         await send_lynkup_response_email(sender_user.email, response, full_name)
    #         logger.info("Lynkup response email queued for %s", sender_user.email)
    #     else:
    #         logger.warning("Sender user not found for Lynkup response email, user_id=%s", req.sender_user_id)
    # except Exception as e:
    #     logger.exception("Failed to queue Lynkup response email: %s", e)

    if response in ("accepted", "declined"):
        notification_type = (
            "CONNECTION_ACCEPTED" if response == "accepted" else "CONNECTION_DECLINED"
        )
        try:
            receiver_profile = (
                await db.execute(select(Profile).where(Profile.user_id == user_id))
            ).scalar_one_or_none()
            receiver_name = "Someone"
            if receiver_profile is not None:
                receiver_name = (
                    f"{receiver_profile.first_name or ''} {receiver_profile.last_name or ''}".strip()
                    or "Someone"
                )
            if response == "accepted":
                title = "Connection Accepted"
                body = f"{receiver_name} accepted your connection request."
            else:
                title = "Connection Declined"
                body = f"{receiver_name} declined your connection request."
            logger.info(
                "Sending %s push notification request_id=%s recipient=%s responder=%s title=%r",
                notification_type,
                request_id,
                sender_user_id,
                user_id,
                title,
            )
            await create_notification(
                db,
                recipient_user_id=sender_user_id,
                notification_type=notification_type,
                title=title,
                body=body,
                sender_user_id=user_id,
            )
        except Exception:
            logger.exception(
                "Failed to send %s push notification request_id=%s recipient=%s responder=%s",
                notification_type,
                request_id,
                sender_user_id,
                user_id,
            )

    is_accepted = response == "accepted"
    return success_response(
        "You are now connected." if is_accepted else "Request declined successfully.",
        {
            "lynkup_id": req.id,
            "sender_user_id": sender_user_id,
            "receiver_user_id": receiver_user_id,
            "status": req.status,
            "is_connected": is_accepted,
            "request_sent": False,
            "request_received": False,
            "is_sent": False,
            "is_request": False,
        },
        response_cls=ApiResponse,
    )


async def remove_connection(
    db: AsyncSession,
    current_user_id: UUID,
    other_user_id: UUID,
) -> ApiResponse:
    """Remove an accepted connection and/or any pending/accepted connection requests, then update connection counts."""
    if current_user_id == other_user_id:
        return error_response("Connection not found", response_cls=ApiResponse)

    from apps.connections.repositories import delete_connection, get_active_connection_between
    from apps.profiles.services.profile_stats_service import decrement_connection_counts_for_users

    connection = await get_active_connection_between(db, current_user_id, other_user_id)
    request_rows = (
        await db.execute(
            select(ConnectionRequest).where(
                ConnectionRequest.status.in_(["pending", "accepted"]),
                or_(
                    and_(
                        ConnectionRequest.sender_user_id == current_user_id,
                        ConnectionRequest.receiver_user_id == other_user_id,
                    ),
                    and_(
                        ConnectionRequest.sender_user_id == other_user_id,
                        ConnectionRequest.receiver_user_id == current_user_id,
                    ),
                ),
            )
        )
    ).scalars().all()

    if connection is None and not request_rows:
        return error_response("Connection not found", response_cls=ApiResponse)

    try:
        if connection is not None:
            await delete_connection(db, connection)
            await decrement_connection_counts_for_users(db, current_user_id, other_user_id)
        for request_row in request_rows:
            await db.delete(request_row)
        await db.commit()
    except Exception:
        await db.rollback()
        from core.errors import ApiError
        raise ApiError("Failed to remove connection")

    return success_response(
        "Connection removed successfully",
        {
            "is_connected": False,
            "request_sent": False,
            "request_received": False,
            "is_sent": False,
            "is_request": False,
        },
        response_cls=ApiResponse,
    )


async def get_pending_requests(
    db: AsyncSession,
    user_id: UUID,
    page: int | None = None,
    page_size: int | None = None,
    search: str | None = None,
) -> ApiResponse:
    from common.pagination import paginate_items, build_paginated_response
    from sqlalchemy import func

    base_stmt = select(ConnectionRequest, Profile).join(
        Profile,
        Profile.user_id == ConnectionRequest.sender_user_id,
    ).join(
        User,
        User.id == ConnectionRequest.sender_user_id,
    ).where(
        ConnectionRequest.status == "pending",
        ConnectionRequest.receiver_user_id == user_id,
        *visible_user_filters(User),
    )

    if search:
        search_pattern = f"%{search.strip()}%"
        base_stmt = base_stmt.where(
            or_(
                Profile.first_name.ilike(search_pattern),
                Profile.last_name.ilike(search_pattern)
            )
        )

    def _build_pending_item(req: ConnectionRequest, profile: Profile) -> dict:
        request_sent = req.sender_user_id == user_id
        request_received = req.receiver_user_id == user_id
        other_user_id = req.receiver_user_id if request_sent else req.sender_user_id
        return {
            "lynkup_id": req.id,
            "user_id": other_user_id,
            "status": req.status,
            "first_name": profile.first_name,
            "last_name": profile.last_name,
            "profilePhoto_url": generate_profile_image_url(profile.profile_photo_url) if profile.profile_photo_url else None,
            "request_sent": request_sent,
            "request_received": request_received,
            "is_sent": request_sent,
            "is_request": request_received,
        }

    if page is None and page_size is None:
        result = await db.execute(base_stmt.order_by(ConnectionRequest.created_at.desc()))
        rows = result.all()
        data = [_build_pending_item(req, profile) for req, profile in rows]
        return success_response("Lynkup Request pending", data, response_cls=ApiResponse)

    p = page or 1
    ps = page_size or 20

    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    total_items = int((await db.execute(count_stmt)).scalar_one())

    stmt = base_stmt.order_by(ConnectionRequest.created_at.desc()).offset((p - 1) * ps).limit(ps)
    result = await db.execute(stmt)
    rows = result.all()

    data = [_build_pending_item(req, profile) for req, profile in rows]

    paginated = build_paginated_response(data, p, ps, total_items)
    return success_response("Lynkup Request pending", paginated.model_dump(), response_cls=ApiResponse)


async def get_connections_service(
    db: AsyncSession,
    user_id: UUID,
    page: int | None = None,
    page_size: int | None = None,
) -> ApiResponse:
    from common.pagination import build_paginated_response
    from sqlalchemy import func

    base_stmt = (
        select(Connection, Profile)
        .join(
            Profile,
            or_(
                and_(Connection.user_low_id == user_id, Profile.user_id == Connection.user_high_id),
                and_(Connection.user_high_id == user_id, Profile.user_id == Connection.user_low_id),
            ),
        )
        .join(
            User,
            User.id == Profile.user_id,
        )
        .where(
            or_(Connection.user_low_id == user_id, Connection.user_high_id == user_id),
            Connection.is_active == True,
            *visible_user_filters(User),
        )
    )

    async def _build_item(conn: Connection, profile: Profile) -> dict:
        other_user_id = conn.user_high_id if conn.user_low_id == user_id else conn.user_low_id
        lynkup_stmt = (
            select(ConnectionRequest.id)
            .where(
                ConnectionRequest.status == "accepted",
                or_(
                    and_(
                        ConnectionRequest.sender_user_id == user_id,
                        ConnectionRequest.receiver_user_id == other_user_id,
                    ),
                    and_(
                        ConnectionRequest.sender_user_id == other_user_id,
                        ConnectionRequest.receiver_user_id == user_id,
                    ),
                ),
            )
            .order_by(ConnectionRequest.updated_at.desc())
            .limit(1)
        )
        lynkup_id = (await db.execute(lynkup_stmt)).scalar_one_or_none() or conn.id
        return {
            "lynkup_id": lynkup_id,
            "user_id": other_user_id,
            "status": "accepted",
            "first_name": profile.first_name,
            "last_name": profile.last_name,
            "profilePhoto_url": generate_profile_image_url(profile.profile_photo_url) if profile.profile_photo_url else None,
        }

    if page is None and page_size is None:
        result = await db.execute(base_stmt.order_by(Connection.connected_at.desc()))
        rows = result.all()
        data = [await _build_item(conn, profile) for conn, profile in rows]
        return success_response("Connections fetched successfully", data, response_cls=ApiResponse)

    p = page or 1
    ps = page_size or 20

    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    total_items = int((await db.execute(count_stmt)).scalar_one())

    stmt = base_stmt.order_by(Connection.connected_at.desc()).offset((p - 1) * ps).limit(ps)
    rows = (await db.execute(stmt)).all()
    data = [await _build_item(conn, profile) for conn, profile in rows]

    paginated = build_paginated_response(data, p, ps, total_items)
    return success_response(
        "Connections fetched successfully",
        paginated.model_dump(),
        response_cls=ApiResponse,
    )


async def get_relationship_flags(
    db: AsyncSession,
    current_user_id: UUID,
    target_user_ids: list[UUID]
) -> dict[UUID, dict[str, bool]]:
    if not target_user_ids:
        return {}

    # 1. Fetch active connections
    conn_stmt = select(Connection).where(
        Connection.is_active == True,
        or_(
            and_(Connection.user_low_id == current_user_id, Connection.user_high_id.in_(target_user_ids)),
            and_(Connection.user_high_id == current_user_id, Connection.user_low_id.in_(target_user_ids))
        )
    )
    conn_res = await db.execute(conn_stmt)
    connections = conn_res.scalars().all()
    connected_user_ids = set()
    for conn in connections:
        connected_user_ids.add(conn.user_high_id if conn.user_low_id == current_user_id else conn.user_low_id)

    # 2. Fetch active follows
    follow_stmt = select(Follow).where(
        Follow.follower_user_id == current_user_id,
        Follow.following_user_id.in_(target_user_ids),
        Follow.is_active == True
    )
    follow_res = await db.execute(follow_stmt)
    follows = follow_res.scalars().all()
    followed_user_ids = {f.following_user_id for f in follows}

    # 3. Fetch active blocks
    block_stmt = select(Block).where(
        Block.is_active == True,
        or_(
            and_(Block.blocker_user_id == current_user_id, Block.blocked_user_id.in_(target_user_ids)),
            and_(Block.blocked_user_id == current_user_id, Block.blocker_user_id.in_(target_user_ids))
        )
    )
    block_res = await db.execute(block_stmt)
    blocks = block_res.scalars().all()
    blocked_user_ids = {b.blocked_user_id for b in blocks if b.blocker_user_id == current_user_id}

    # 4. Fetch pending connection requests
    req_stmt = select(ConnectionRequest).where(
        ConnectionRequest.status == "pending",
        or_(
            and_(ConnectionRequest.sender_user_id == current_user_id, ConnectionRequest.receiver_user_id.in_(target_user_ids)),
            and_(ConnectionRequest.receiver_user_id == current_user_id, ConnectionRequest.sender_user_id.in_(target_user_ids))
        )
    )
    req_res = await db.execute(req_stmt)
    requests = req_res.scalars().all()
    request_sent_ids = set()
    request_received_ids = set()
    for r in requests:
        if r.sender_user_id == current_user_id:
            request_sent_ids.add(r.receiver_user_id)
        else:
            request_received_ids.add(r.sender_user_id)

    # Build response map
    result = {}
    for uid in target_user_ids:
        result[uid] = {
            "is_connected": uid in connected_user_ids,
            "is_followed": uid in followed_user_ids,
            "is_blocked": uid in blocked_user_ids,
            "request_sent": uid in request_sent_ids,
            "request_received": uid in request_received_ids,
        }
    return result




def apply_relationship_flags(
    item: dict,
    flags_map: dict[UUID, dict[str, bool]],
    user_id: UUID,
) -> None:
    item.update(flags_map.get(user_id, DEFAULT_RELATIONSHIP_FLAGS))

