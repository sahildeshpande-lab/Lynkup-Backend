from __future__ import annotations

import io
import uuid
from types import SimpleNamespace
import pytest
import pytest_asyncio
from fastapi import HTTPException, UploadFile
from common.exceptions import ApiError
import httpx
from httpx import AsyncClient
from sqlmodel import select
from sqlalchemy import text

from entrypoints.api import app
from core.database.session import async_session_factory
from datetime import datetime, timezone, timedelta
from core.database.init import init_db
from core.security.auth import get_current_app_user, get_current_user, get_current_moderator, get_current_moderator_or_viewer
from apps.accounts.db_models import TransactionalEmailLog, User
from apps.accounts.services import assign_user_role
from apps.feed.db_models import Post, MediaAsset, PostAttachment
from apps.connections.db_models import Connection
from apps.profiles.db_models import Profile
from common.enums import MediaType, MediaAssetState, PostState
from apps.feed.schemas import SavePostRequest, EditPostRequest, MediaItem, PostContentPayload, EditPostContentPayload, DeletePostRequest
from apps.feed.services import (
    get_media_type,
    upload_post_media_service,
    save_post_service,
    edit_post_service,
    publish_post_service,
    admin_publish_post_service,
    get_post_service,
    delete_post_service,
    list_draft_posts_service,
    delete_draft_post_service,
    list_user_posts_service,
    list_reviewed_posts_by_state_service,
    list_processing_posts_service,
    get_feed_service,
    format_post_detail,
)


async def clean_feed_pytest_data(session):
    # Find test users
    result = await session.execute(
        select(User).where(User.email.in_([
            "pytest_feed_user@example.com",
            "pytest_feed_other@example.com",
            "pytest_feed_mod_b@example.com",
            "pytest_feed_superadmin@example.com",
            "pytest_visitor@example.com",
            "third@example.com",
        ]))
    )
    users = result.scalars().all()
    user_ids = [u.id for u in users]
    
    if user_ids:
        # Delete dependencies using PostgreSQL = ANY(:user_ids) syntax
        params = {"user_ids": list(user_ids)}
        
        await session.execute(text("""
            DELETE FROM post_attachments 
            WHERE media_asset_id IN (SELECT id FROM media_assets WHERE owner_user_id = ANY(:user_ids))
               OR post_id IN (SELECT id FROM posts WHERE author_user_id = ANY(:user_ids))
        """), params)
        
        await session.execute(text("""
            DELETE FROM post_reactions 
            WHERE user_id = ANY(:user_ids) 
               OR post_id IN (SELECT id FROM posts WHERE author_user_id = ANY(:user_ids))
        """), params)
        
        await session.execute(text("""
            DELETE FROM post_revisions 
            WHERE editor_user_id = ANY(:user_ids) 
               OR post_id IN (SELECT id FROM posts WHERE author_user_id = ANY(:user_ids))
        """), params)
        
        await session.execute(text("""
            DELETE FROM post_hashtags 
            WHERE post_id IN (SELECT id FROM posts WHERE author_user_id = ANY(:user_ids))
        """), params)
        
        await session.execute(text("""
            DELETE FROM post_topics 
            WHERE post_id IN (SELECT id FROM posts WHERE author_user_id = ANY(:user_ids))
        """), params)
        
        await session.execute(text("""
            DELETE FROM link_previews 
            WHERE post_id IN (SELECT id FROM posts WHERE author_user_id = ANY(:user_ids))
        """), params)
        
        await session.execute(text("""
            DELETE FROM posts 
            WHERE author_user_id = ANY(:user_ids)
               OR moderator_id = ANY(:user_ids)
        """), params)
        
        await session.execute(text("""
            DELETE FROM media_assets 
            WHERE owner_user_id = ANY(:user_ids)
        """), params)
        
        await session.execute(text("""
            DELETE FROM connections 
            WHERE user_low_id = ANY(:user_ids) 
               OR user_high_id = ANY(:user_ids)
        """), params)

        await session.execute(text("""
            DELETE FROM profiles
            WHERE user_id = ANY(:user_ids)
        """), params)

        # Role links must be removed before users (FK user_roles_user_id_fkey)
        await session.execute(text("""
            DELETE FROM user_roles
            WHERE user_id = ANY(:user_ids)
        """), params)

        await session.execute(text("""
            DELETE FROM security_events
            WHERE user_id = ANY(:user_ids)
        """), params)
        
        await session.execute(text("""
            DELETE FROM users 
            WHERE id = ANY(:user_ids)
        """), params)

        await session.execute(text("""
            DELETE FROM transactional_email_log
            WHERE "to" IN ('pytest_feed_user@example.com', 'pytest_feed_other@example.com')
               OR "to" LIKE 'pytest_feed_%@example.com'
        """))
        
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def feed_db_cleanup():
    async with async_session_factory() as session:
        await clean_feed_pytest_data(session)
    yield
    async with async_session_factory() as session:
        await clean_feed_pytest_data(session)


@pytest_asyncio.fixture
async def db_setup():
    await init_db()


@pytest_asyncio.fixture
async def test_users(db_setup, feed_db_cleanup):
    async with async_session_factory() as session:
        user = User(
            email="pytest_feed_user@example.com",
            role="user",
            firebase_uid=f"uid-feed-{uuid.uuid4()}",
            status="active"
        )
        other = User(
            email="pytest_feed_other@example.com",
            role="user",
            firebase_uid=f"uid-feed-{uuid.uuid4()}",
            status="active"
        )
        session.add(user)
        session.add(other)
        await session.commit()
        await session.refresh(user)
        await session.refresh(other)
    return user, other


async def _ensure_moderator_role(session, user) -> None:
    """Ensure user has moderator UserRole so repair won't reassign their posts."""
    db_user = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
    await assign_user_role(session, db_user, "moderator")


@pytest.mark.asyncio
async def test_upload_post_media_service_success(test_users) -> None:
    user, _ = test_users
    file_content = b"fake image content"
    file = UploadFile(
        filename="test_image.png",
        file=io.BytesIO(file_content),
        headers={"content-type": "image/png"}
    )

    async with async_session_factory() as session:
        res = await upload_post_media_service(
            user_id=user.id,
            files=[file],
            db=session,
        )

        assert len(res) == 1
        item = res[0]
        assert item["id"] is not None
        assert "posts/" in item["key"]
        assert item["key"].endswith(".png")
        assert item["type"] == "image"
        assert item["key"] in item["url"]

        media_id = item["id"]
        stmt = select(MediaAsset).where(MediaAsset.id == media_id)
        db_media = (await session.execute(stmt)).scalar_one_or_none()

        assert db_media is not None
        assert db_media.owner_user_id == user.id
        assert db_media.key == item["key"]
        assert db_media.original_filename == "test_image.png"
        assert db_media.mime_type == "image/png"
        assert db_media.file_size == len(file_content)
        assert db_media.state == MediaAssetState.published


@pytest.mark.asyncio
async def test_upload_post_media_service_validation_failures(test_users) -> None:
    user, _ = test_users

    # Empty file
    file_empty = UploadFile(
        filename="empty.png",
        file=io.BytesIO(b""),
        headers={"content-type": "image/png"},
    )
    async with async_session_factory() as session:
        with pytest.raises(ApiError) as exc_info:
            await upload_post_media_service(user.id, [file_empty], db=session)
        assert "empty" in exc_info.value.message

    too_many_files = [
        UploadFile(
            filename=f"image_{index}.png",
            file=io.BytesIO(b"image bytes"),
            headers={"content-type": "image/png"},
        )
        for index in range(6)
    ]
    async with async_session_factory() as session:
        with pytest.raises(ApiError) as exc_info:
            await upload_post_media_service(
                user.id,
                too_many_files,
                db=session,
            )
        assert "Maximum 5 files" in exc_info.value.message

    oversized_image = UploadFile(
        filename="large.png",
        file=io.BytesIO(b"x" * (5 * 1024 * 1024 + 1)),
        headers={"content-type": "image/png"},
    )
    async with async_session_factory() as session:
        with pytest.raises(ApiError) as exc_info:
            await upload_post_media_service(user.id, [oversized_image], db=session)
        assert "exceeds maximum upload size of 5 MB" in exc_info.value.message


def test_get_media_type_maps_content_types() -> None:
    assert get_media_type("image/gif") == "gif"
    assert get_media_type("image/png") == "image"
    assert get_media_type("video/mp4") == "video"
    assert get_media_type("audio/mpeg") == "audio"
    assert get_media_type("application/pdf") == "document"
    assert get_media_type("text/plain") == "document"
    assert get_media_type("application/octet-stream") == "document"
    assert get_media_type("chemical/x-mdl-molfile") == "other"
    assert get_media_type("") == "other"


@pytest.mark.asyncio
async def test_create_post_service_success(test_users) -> None:
    user, _ = test_users

    # First upload two media assets
    async with async_session_factory() as session:
        m1 = MediaAsset(
            owner_user_id=user.id,
            key=f"posts/{uuid.uuid4()}.jpg",
            type=MediaType.image,
            original_filename="img1.jpg",
            mime_type="image/jpeg",
            file_size=1234,
            state=MediaAssetState.published
        )
        m2 = MediaAsset(
            owner_user_id=user.id,
            key=f"posts/{uuid.uuid4()}.mp4",
            type=MediaType.video,
            original_filename="vid1.mp4",
            mime_type="video/mp4",
            file_size=5678,
            state=MediaAssetState.published
        )
        session.add(m1)
        session.add(m2)
        await session.commit()
        await session.refresh(m1)
        await session.refresh(m2)

    # Create post with those media assets
    payload = SavePostRequest(
        content=PostContentPayload(
            caption="A wonderful day",
            content_html="<p>Enjoying the sunshine!</p>",
            visibility="public"
        ),
        media=[
            MediaItem(id=m1.id, type=m1.type),
            MediaItem(id=m2.id, type=m2.type)
        ]
    )

    async with async_session_factory() as session:
        post = await save_post_service(user.id, payload, session)

        assert post.id is not None
        assert post.author_user_id == user.id
        assert post.caption == "A wonderful day"
        assert post.content_html == "<p>Enjoying the sunshine!</p>"
        assert post.state == PostState.published  # non-draft posts publish immediately

        # Check PostAttachment records
        stmt = select(PostAttachment).where(PostAttachment.post_id == post.id)
        attachments = (await session.execute(stmt)).scalars().all()
        assert len(attachments) == 2
        asset_ids = {a.media_asset_id for a in attachments}
        assert m1.id in asset_ids
        assert m2.id in asset_ids


@pytest.mark.asyncio
async def test_create_post_service_visibility_hidden(test_users) -> None:
    user, _ = test_users

    payload = SavePostRequest(
        content=PostContentPayload(
            caption="Hidden post",
            content_html="Invisible",
            visibility="hidden"
        ),
        media=[]
    )

    async with async_session_factory() as session:
        post = await save_post_service(user.id, payload, session)
        assert post.state == PostState.published


@pytest.mark.asyncio
async def test_create_post_service_saves_draft_when_requested(test_users) -> None:
    user, _ = test_users

    payload = SavePostRequest(
        is_draft=True,
        content=PostContentPayload(caption="Draft from create", visibility="public"),
        media=[],
    )

    async with async_session_factory() as session:
        post = await save_post_service(user.id, payload, session)
        assert post.state == PostState.draft

    async with async_session_factory() as session:
        drafts = await list_draft_posts_service(user.id, session)
        assert len(drafts) == 1
        assert drafts[0].caption == "Draft from create"


@pytest.mark.asyncio
async def test_create_draft_replaces_existing_draft(test_users) -> None:
    user, _ = test_users

    first_payload = SavePostRequest(
        is_draft=True,
        content=PostContentPayload(caption="First draft", visibility="public"),
        media=[],
    )
    second_payload = SavePostRequest(
        is_draft=True,
        content=PostContentPayload(caption="Second draft", visibility="public"),
        media=[],
    )

    async with async_session_factory() as session:
        first = await save_post_service(user.id, first_payload, session)
        first_id = first.id

    async with async_session_factory() as session:
        second = await save_post_service(user.id, second_payload, session)
        second_id = second.id

    async with async_session_factory() as session:
        drafts = await list_draft_posts_service(user.id, session)
        assert len(drafts) == 1
        assert drafts[0].id == second_id
        assert drafts[0].caption == "Second draft"

        old_draft = (await session.execute(select(Post).where(Post.id == first_id))).scalar_one_or_none()
        assert old_draft is None


@pytest.mark.asyncio
async def test_create_processing_post_leaves_existing_draft(test_users) -> None:
    user, _ = test_users

    draft_payload = SavePostRequest(
        is_draft=True,
        content=PostContentPayload(caption="Keep draft", visibility="public"),
        media=[],
    )
    processing_payload = SavePostRequest(
        is_draft=False,
        content=PostContentPayload(caption="Processing post", visibility="public"),
        media=[],
    )

    async with async_session_factory() as session:
        draft = await save_post_service(user.id, draft_payload, session)
        processing = await save_post_service(user.id, processing_payload, session)

        assert draft.state == PostState.draft
        assert processing.state == PostState.published

    async with async_session_factory() as session:
        drafts = await list_draft_posts_service(user.id, session)
        assert len(drafts) == 1
        assert drafts[0].caption == "Keep draft"


@pytest.mark.asyncio
async def test_create_post_service_unauthorized_media(test_users) -> None:
    user, other = test_users

    # Upload media asset owned by other_user
    async with async_session_factory() as session:
        other_media = MediaAsset(
            owner_user_id=other.id,
            key=f"posts/{uuid.uuid4()}.jpg",
            type=MediaType.image,
            original_filename="other_img.jpg",
            mime_type="image/jpeg",
            file_size=1000,
            state=MediaAssetState.published
        )
        session.add(other_media)
        await session.commit()
        await session.refresh(other_media)

    payload = SavePostRequest(
        content=PostContentPayload(
            caption="Try to steal media",
            content_html="testing",
            visibility="public"
        ),
        media=[MediaItem(id=other_media.id, type=other_media.type)]
    )

    async with async_session_factory() as session:
        with pytest.raises(ApiError) as exc_info:
            await save_post_service(user.id, payload, session)
        assert "does not belong to the authenticated user" in exc_info.value.message


@pytest.mark.asyncio
async def test_create_post_service_nonexistent_media(test_users) -> None:
    user, _ = test_users

    payload = SavePostRequest(
        content=PostContentPayload(
            caption="Fake media ID",
            content_html="testing",
            visibility="public"
        ),
        media=[MediaItem(id=uuid.uuid4(), type=MediaType.image)]
    )

    async with async_session_factory() as session:
        with pytest.raises(ApiError) as exc_info:
            await save_post_service(user.id, payload, session)
        assert "not found" in exc_info.value.message


@pytest.mark.asyncio
async def test_routes_endpoints_via_test_client(test_users) -> None:
    user, _ = test_users

    async def _override_get_current_user():
        return user

    import httpx
    app.dependency_overrides[get_current_user] = _override_get_current_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            # Test 1: POST /postupload
            file_data = b"image content"
            files = [
                ("files", ("test.jpg", io.BytesIO(file_data), "image/jpeg")),
            ]
            response = await ac.post("/api/v1/postupload", files=files)
            assert response.status_code == 200
            body = response.json()
            assert body["status"] is True
            assert "media file(s) uploaded" in body["message"]
            assert isinstance(body["data"], list)
            media_id = body["data"][0]["id"]

            # Test 2: POST /post
            post_payload = {
                "content": {
                    "caption": "Test Post via Client",
                    "content_html": "Hello world!",
                    "visibility": "public"
                },
                "media": [
                    {
                        "id": media_id,
                        "type": "image"
                    }
                ]
            }
            response_post = await ac.post("/api/v1/post", json=post_payload)
            assert response_post.status_code == 200
            body_post = response_post.json()
            assert body_post["status"] is True
            assert body_post["message"] == "Post created successfully"
            assert "id" in body_post["data"]
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_postupload_accepts_multiple_files_and_infers_types(test_users) -> None:
    user, _ = test_users

    async def _override_get_current_user():
        return user

    app.dependency_overrides[get_current_user] = _override_get_current_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            files = [
                ("files", ("beach.jpg", io.BytesIO(b"image one"), "image/jpeg")),
                ("files", ("animation.gif", io.BytesIO(b"gif bytes"), "image/gif")),
                ("files", ("reel.mp4", io.BytesIO(b"video bytes"), "video/mp4")),
                ("files", ("notes.txt", io.BytesIO(b"text bytes"), "text/plain")),
            ]
            response = await ac.post("/api/v1/postupload", files=files)
            assert response.status_code == 200
            body = response.json()
            assert body["status"] is True
            assert "media file(s) uploaded" in body["message"]
            assert [item["type"] for item in body["data"]] == ["image", "gif", "video", "document"]
            assert [item["key"].split(".")[-1] for item in body["data"]] == ["jpg", "gif", "mp4", "txt"]
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_postupload_rejects_too_many_files(test_users) -> None:
    user, _ = test_users

    async def _override_get_current_user():
        return user

    app.dependency_overrides[get_current_user] = _override_get_current_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            files = [
                ("files", (f"image_{index}.jpg", io.BytesIO(b"image bytes"), "image/jpeg"))
                for index in range(6)
            ]
            response = await ac.post("/api/v1/postupload", files=files)
            assert response.status_code == 200
            body = response.json()
            assert body["status"] is False
            assert body["message"] == "Maximum 5 files allowed per request"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_update_post_service_success(test_users) -> None:
    user, _ = test_users
    payload = SavePostRequest(
        content=PostContentPayload(caption="Original", content_html="Original text", visibility="public")
    )
    async with async_session_factory() as session:
        post = await save_post_service(user.id, payload, session)
        post_id = post.id
        assert post.state == PostState.published

    # Edit via general edit service (edit_post_service)
    edit_payload = EditPostRequest(
        id=post_id,
        content=EditPostContentPayload(caption="Updated Caption", content_html="Updated text", visibility="hidden")
    )
    async with async_session_factory() as session:
        post = await edit_post_service(user.id, edit_payload, session)
        assert post.caption == "Updated Caption"
        assert post.content_html == "Updated text"
        assert post.state == PostState.hidden
        assert post.is_edited is True
        assert post.revision_number >= 2

    # Update draft via save_post_service with id and is_draft=True
    update_payload_draft = SavePostRequest(
        id=post_id,
        is_draft=True,
        content=PostContentPayload(caption="Draft Caption", content_html="Draft text", visibility="public")
    )
    async with async_session_factory() as session:
        post = await save_post_service(user.id, update_payload_draft, session)
        assert post.caption == "Draft Caption"
        assert post.content_html == "Draft text"
        assert post.state == PostState.draft

    # Update post via save_post_service with id and is_draft=False
    update_payload_processing = SavePostRequest(
        id=post_id,
        is_draft=False,
        content=PostContentPayload(caption="Processing Caption", content_html="Processing text", visibility="public")
    )
    async with async_session_factory() as session:
        post = await save_post_service(user.id, update_payload_processing, session)
        assert post.caption == "Processing Caption"
        assert post.content_html == "Processing text"
        assert post.state == PostState.published


@pytest.mark.asyncio
async def test_publish_post_service_success(test_users) -> None:
    user, _ = test_users
    payload = SavePostRequest(
        content=PostContentPayload(caption="Original", content_html="Original text", visibility="public")
    )
    async with async_session_factory() as session:
        session.add(Profile(user_id=user.id, first_name="Post", last_name="Author", posts_count=0))
        await session.commit()

        post = await save_post_service(user.id, payload, session)
        post_id = post.id

    async with async_session_factory() as session:
        post = await admin_publish_post_service(post_id, "published", user.id, session)
        assert post.state == PostState.published
        assert post.moderator_id == user.id
        assert post.is_moderator_reviewed is True
        profile = (await session.execute(select(Profile).where(Profile.user_id == user.id))).scalar_one()
        assert profile.posts_count == 1

    # Publish notification emails are currently disabled.

@pytest.mark.asyncio
async def test_flag_post_service_success(test_users) -> None:
    user, _ = test_users
    payload = SavePostRequest(
        content=PostContentPayload(caption="Original", content_html="Original text", visibility="public")
    )
    async with async_session_factory() as session:
        session.add(Profile(user_id=user.id, first_name="Post", last_name="Author", posts_count=0))
        await session.commit()

        post = await save_post_service(user.id, payload, session)
        post_id = post.id

    async with async_session_factory() as session:
        post = await admin_publish_post_service(post_id, "flagged", user.id, session)
        assert post.state == PostState.flagged
        assert post.moderator_id == user.id
        profile = (await session.execute(select(Profile).where(Profile.user_id == user.id))).scalar_one()
        assert profile.posts_count == 0

    # Flag notification emails are currently disabled.


@pytest.mark.asyncio
async def test_list_reviewed_posts_service_filters_by_moderator_and_action(test_users) -> None:
    author, moderator_a = test_users
    moderator_b = User(
        email="pytest_feed_mod_b@example.com",
        role="moderator",
        firebase_uid=f"uid-feed-mod-b-{uuid.uuid4()}",
        status="active",
    )

    async with async_session_factory() as session:
        session.add(moderator_b)
        await session.commit()
        await session.refresh(moderator_b)
        await _ensure_moderator_role(session, moderator_a)
        await _ensure_moderator_role(session, moderator_b)
        await session.commit()

        published_by_a = Post(
            author_user_id=author.id,
            content={"caption": "Published by A", "visibility": "public"},
            state=PostState.published,
            is_moderator_reviewed=True,
            moderator_id=moderator_a.id,
        )
        flagged_by_a = Post(
            author_user_id=author.id,
            content={"caption": "Flagged by A", "visibility": "public"},
            state=PostState.flagged,
            is_moderator_reviewed=True,
            moderator_id=moderator_a.id,
        )
        published_by_b = Post(
            author_user_id=author.id,
            content={"caption": "Published by B", "visibility": "public"},
            state=PostState.published,
            is_moderator_reviewed=True,
            moderator_id=moderator_b.id,
        )
        session.add_all([published_by_a, flagged_by_a, published_by_b])
        await session.commit()

    async with async_session_factory() as session:
        # No status defaults to published for this moderator.
        published_for_a = await list_reviewed_posts_by_state_service(session, moderator_a.id)
        assert len(published_for_a["items"]) == 1
        assert published_for_a["items"][0]["caption"] == "Published by A"
        assert published_for_a["items"][0]["status"] == "published"

        publish_only = await list_reviewed_posts_by_state_service(
            session, moderator_a.id, status="published"
        )
        assert len(publish_only["items"]) == 1
        assert publish_only["items"][0]["status"] == "published"

        flag_only = await list_reviewed_posts_by_state_service(
            session, moderator_a.id, status="flagged"
        )
        assert len(flag_only["items"]) == 1
        assert flag_only["items"][0]["status"] == "flagged"
        assert flag_only["items"][0]["caption"] == "Flagged by A"

        publish_for_a = await list_reviewed_posts_by_state_service(
            session, moderator_a.id, status="published"
        )
        assert all(item["caption"] != "Published by B" for item in publish_for_a["items"])


@pytest.mark.asyncio
async def test_list_reviewed_posts_route_via_test_client(test_users) -> None:
    author, moderator = test_users
    other_moderator = User(
        email="pytest_feed_mod_b@example.com",
        role="moderator",
        firebase_uid=f"uid-feed-mod-b-{uuid.uuid4()}",
        status="active",
    )

    async with async_session_factory() as session:
        session.add(other_moderator)
        await session.commit()
        await session.refresh(other_moderator)
        await _ensure_moderator_role(session, moderator)
        await _ensure_moderator_role(session, other_moderator)
        await session.commit()

        reviewed_post = Post(
            author_user_id=author.id,
            content={"caption": "Reviewed via route", "visibility": "public"},
            state=PostState.published,
            is_moderator_reviewed=True,
            moderator_id=moderator.id,
        )
        other_reviewed_post = Post(
            author_user_id=author.id,
            content={"caption": "Other moderator reviewed", "visibility": "public"},
            state=PostState.published,
            is_moderator_reviewed=True,
            moderator_id=other_moderator.id,
        )
        session.add_all([reviewed_post, other_reviewed_post])
        await session.commit()

    async def _override_get_current_moderator():
        return moderator

    app.dependency_overrides[get_current_moderator] = _override_get_current_moderator
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get("/api/v1/admin/posts/reviewed", params={"status": "published"})
            assert response.status_code == 200
            body = response.json()
            assert body["status"] is True
            assert body["message"] == "Posts fetched successfully"
            captions = {item["caption"] for item in body["data"]["items"]}
            assert {"Reviewed via route", "Other moderator reviewed"}.issubset(captions)

            filtered_response = await ac.get(
                "/api/v1/admin/posts/reviewed",
                params={"status": "published", "moderator_id": str(other_moderator.id)},
            )
            assert filtered_response.status_code == 200
            filtered_body = filtered_response.json()
            filtered_captions = {item["caption"] for item in filtered_body["data"]["items"]}
            assert "Other moderator reviewed" in filtered_captions
            assert "Reviewed via route" not in filtered_captions
    finally:
        app.dependency_overrides.pop(get_current_moderator, None)


@pytest.mark.asyncio
async def test_reviewed_posts_status_filter_without_moderator_id_returns_all(test_users) -> None:
    author, moderator_a = test_users
    moderator_b = User(
        email="pytest_feed_mod_b@example.com",
        role="moderator",
        firebase_uid=f"uid-feed-mod-b-{uuid.uuid4()}",
        status="active",
    )
    async with async_session_factory() as session:
        session.add(moderator_b)
        await session.commit()
        await session.refresh(moderator_b)
        await _ensure_moderator_role(session, moderator_a)
        await _ensure_moderator_role(session, moderator_b)
        await session.commit()

        published_by_a = Post(
            author_user_id=author.id,
            content={"caption": "Published by A", "visibility": "public"},
            state=PostState.published,
            is_moderator_reviewed=True,
            moderator_id=moderator_a.id,
        )
        published_by_b = Post(
            author_user_id=author.id,
            content={"caption": "Published by B", "visibility": "public"},
            state=PostState.published,
            is_moderator_reviewed=True,
            moderator_id=moderator_b.id,
        )
        flagged_by_b = Post(
            author_user_id=author.id,
            content={"caption": "Flagged by B", "visibility": "public"},
            state=PostState.flagged,
            is_moderator_reviewed=True,
            moderator_id=moderator_b.id,
        )
        session.add_all([published_by_a, published_by_b, flagged_by_b])
        await session.commit()

    async def _override_get_current_moderator():
        return SimpleNamespace(id=moderator_a.id, role="moderator")

    app.dependency_overrides[get_current_moderator] = _override_get_current_moderator
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get("/api/v1/admin/posts/reviewed", params={"status": "published"})
            assert response.status_code == 200
            body = response.json()
            assert body["status"] is True
            assert body["data"]["totalItems"] >= 2
            captions = {item["caption"] for item in body["data"]["items"]}
            assert {"Published by A", "Published by B"}.issubset(captions)
    finally:
        app.dependency_overrides.pop(get_current_moderator, None)


@pytest.mark.asyncio
async def test_processing_posts_service_filters_by_assigned_moderator(test_users) -> None:
    author, moderator_a = test_users
    moderator_b = User(
        email="pytest_feed_mod_b@example.com",
        role="moderator",
        firebase_uid=f"uid-feed-mod-b-{uuid.uuid4()}",
        status="active",
    )

    async with async_session_factory() as session:
        session.add(moderator_b)
        await session.commit()
        await session.refresh(moderator_b)
        await _ensure_moderator_role(session, moderator_a)
        await _ensure_moderator_role(session, moderator_b)
        await session.commit()

        post_for_a = Post(
            author_user_id=author.id,
            content={"caption": "Assigned to A", "visibility": "public"},
            state=PostState.processing,
            moderator_id=moderator_a.id,
        )
        post_for_b = Post(
            author_user_id=author.id,
            content={"caption": "Assigned to B", "visibility": "public"},
            state=PostState.processing,
            moderator_id=moderator_b.id,
        )
        session.add_all([post_for_a, post_for_b])
        await session.commit()

    async with async_session_factory() as session:
        only_a = await list_processing_posts_service(session, moderator_id=moderator_a.id)
        assert only_a["totalItems"] == 1
        assert only_a["items"][0]["caption"] == "Assigned to A"
        assert only_a["items"][0]["moderator_id"] == moderator_a.id

        all_processing = await list_processing_posts_service(session, moderator_id=None)
        captions = {item["caption"] for item in all_processing["items"]}
        assert {"Assigned to A", "Assigned to B"}.issubset(captions)


@pytest.mark.asyncio
async def test_processing_posts_route_filters_for_moderator_and_allows_superadmin(test_users) -> None:
    author, moderator_a = test_users
    moderator_b = User(
        email="pytest_feed_mod_b@example.com",
        role="moderator",
        firebase_uid=f"uid-feed-mod-b-{uuid.uuid4()}",
        status="active",
    )
    superadmin_id = uuid.uuid4()

    async with async_session_factory() as session:
        session.add(moderator_b)
        await session.commit()
        await session.refresh(moderator_b)
        await _ensure_moderator_role(session, moderator_a)
        await _ensure_moderator_role(session, moderator_b)
        await session.commit()

        post_for_a = Post(
            author_user_id=author.id,
            content={"caption": "Route assigned to A", "visibility": "public"},
            state=PostState.processing,
            moderator_id=moderator_a.id,
        )
        post_for_b = Post(
            author_user_id=author.id,
            content={"caption": "Route assigned to B", "visibility": "public"},
            state=PostState.processing,
            moderator_id=moderator_b.id,
        )
        session.add_all([post_for_a, post_for_b])
        await session.commit()

    app.dependency_overrides[get_current_moderator_or_viewer] = (
        lambda: SimpleNamespace(id=moderator_a.id, role="moderator")
    )
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            moderator_response = await ac.get("/api/v1/posts/processing")
            assert moderator_response.status_code == 200
            moderator_body = moderator_response.json()
            assert moderator_body["data"]["totalItems"] == 1
            assert moderator_body["data"]["items"][0]["caption"] == "Route assigned to A"
            assert moderator_body["data"]["items"][0]["moderator_name"] == moderator_a.email
    finally:
        app.dependency_overrides.pop(get_current_moderator_or_viewer, None)

    app.dependency_overrides[get_current_moderator_or_viewer] = (
        lambda: SimpleNamespace(id=superadmin_id, role="superadmin")
    )
    app.dependency_overrides[get_current_moderator] = (
        lambda: SimpleNamespace(id=superadmin_id, role="superadmin")
    )
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            superadmin_response = await ac.get("/api/v1/posts/processing")
            assert superadmin_response.status_code == 200
            superadmin_body = superadmin_response.json()
            captions = {item["caption"] for item in superadmin_body["data"]["items"]}
            assert {"Route assigned to A", "Route assigned to B"}.issubset(captions)
    finally:
        app.dependency_overrides.pop(get_current_moderator_or_viewer, None)
        app.dependency_overrides.pop(get_current_moderator, None)


@pytest.mark.asyncio
async def test_get_post_service_visibility(test_users) -> None:
    user, other = test_users
    payload = SavePostRequest(
        is_draft=True,
        content=PostContentPayload(caption="Draft", content_html="Original text", visibility="public")
    )
    async with async_session_factory() as session:
        post = await save_post_service(user.id, payload, session)
        post_id = post.id

    async with async_session_factory() as session:
        p = await get_post_service(post_id, user.id, session)
        assert p.id == post_id

        with pytest.raises(ApiError) as exc_info:
            await get_post_service(post_id, other.id, session)
        assert "not accessible" in exc_info.value.message


@pytest.mark.asyncio
async def test_delete_post_service_success(test_users) -> None:
    user, _ = test_users
    payload = SavePostRequest(
        content=PostContentPayload(caption="Delete me", content_html="Bye", visibility="public")
    )
    async with async_session_factory() as session:
        session.add(Profile(user_id=user.id, first_name="Post", last_name="Author", posts_count=0))
        await session.commit()
        post = await save_post_service(user.id, payload, session)
        post_id = post.id
        profile = (await session.execute(select(Profile).where(Profile.user_id == user.id))).scalar_one()
        assert profile.posts_count == 1

    async with async_session_factory() as session:
        await delete_post_service(post_id, user.id, session)

    async with async_session_factory() as session:
        with pytest.raises(ApiError) as exc_info:
            await get_post_service(post_id, user.id, session)
        assert "not found" in exc_info.value.message.lower()
        profile = (await session.execute(select(Profile).where(Profile.user_id == user.id))).scalar_one()
        assert profile.posts_count == 0


@pytest.mark.asyncio
async def test_list_user_posts_service_privacy(test_users) -> None:
    user, other = test_users
    
    async with async_session_factory() as session:
        p1 = Post(author_user_id=user.id, content={"caption": "Draft Post"}, state=PostState.draft)
        p2 = Post(author_user_id=user.id, content={"caption": "Published Post"}, state=PostState.published)
        p3 = Post(author_user_id=user.id, content={"caption": "Flagged Post"}, state=PostState.flagged)
        p4 = Post(author_user_id=user.id, content={"caption": "Deleted Post"}, state=PostState.deleted)
        session.add(p1)
        session.add(p2)
        session.add(p3)
        session.add(p4)
        await session.commit()
    
    async with async_session_factory() as session:
        published_posts, total = await list_user_posts_service(user, session, include_total=True)
        assert len(published_posts) == 1
        assert published_posts[0].caption == "Published Post"

        draft_posts, total = await list_user_posts_service(user, session, state="draft", include_total=True)
        assert len(draft_posts) == 1
        assert draft_posts[0].caption == "Draft Post"

        flagged_posts, total = await list_user_posts_service(user, session, state="flagged", include_total=True)
        assert len(flagged_posts) == 1
        assert flagged_posts[0].caption == "Flagged Post"
        
        superadmin = SimpleNamespace(id=other.id, role="superadmin")
        posts_superadmin, total = await list_user_posts_service(
            superadmin,
            session,
            target_user_id=user.id,
            state="published",
            include_total=True,
        )
        assert len(posts_superadmin) == 1
        assert posts_superadmin[0].caption == "Published Post"


@pytest.mark.asyncio
async def test_posts_route_filters_state_for_current_user(test_users) -> None:
    user, _ = test_users

    async with async_session_factory() as session:
        session.add_all([
            Post(author_user_id=user.id, content={"caption": "Route Draft"}, state=PostState.draft),
            Post(author_user_id=user.id, content={"caption": "Route Published"}, state=PostState.published),
        ])
        await session.commit()

    async def _override_get_current_user():
        return user

    app.dependency_overrides[get_current_user] = _override_get_current_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get("/api/v1/posts", params={"state": "draft"})
            assert response.status_code == 200
            body = response.json()
            assert body["status"] is True
            items = body["data"]["items"] if isinstance(body["data"], dict) else body["data"]
            captions = {item["content"]["caption"] for item in items}
            assert captions == {"Route Draft"}
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_posts_route_allows_normal_user_for_other_user(test_users) -> None:
    user, other = test_users

    async def _override_get_current_user():
        return user

    app.dependency_overrides[get_current_user] = _override_get_current_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get("/api/v1/posts", params={"user_id": str(other.id)})
            assert response.status_code == 200
            assert response.json()["status"] is True
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_posts_route_superadmin_can_filter_any_user_or_all(test_users) -> None:
    user, other = test_users
    superadmin = SimpleNamespace(id=uuid.uuid4(), role="superadmin")

    async with async_session_factory() as session:
        session.add_all([
            Post(author_user_id=user.id, content={"caption": "User Published"}, state=PostState.published),
            Post(author_user_id=other.id, content={"caption": "Other Published"}, state=PostState.published),
            Post(author_user_id=other.id, content={"caption": "Other Processing"}, state=PostState.processing),
        ])
        await session.commit()

    async def _override_get_current_user():
        return superadmin

    app.dependency_overrides[get_current_user] = _override_get_current_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            all_response = await ac.get("/api/v1/posts", params={"state": "published"})
            assert all_response.status_code == 200
            all_data = all_response.json()["data"]
            all_items = all_data["items"] if isinstance(all_data, dict) else all_data
            all_captions = {item["content"]["caption"] for item in all_items}
            assert {"User Published", "Other Published"}.issubset(all_captions)

            user_response = await ac.get(
                "/api/v1/posts",
                params={"user_id": str(other.id), "state": "processing"},
            )
            assert user_response.status_code == 200
            user_data = user_response.json()["data"]
            user_items = user_data["items"] if isinstance(user_data, dict) else user_data
            user_captions = {item["content"]["caption"] for item in user_items}
            assert user_captions == {"Other Processing"}
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_posts_route_validation_errors(test_users) -> None:
    user, _ = test_users
    missing_user_id = uuid.uuid4()

    async def _override_get_current_user():
        return SimpleNamespace(id=uuid.uuid4(), role="superadmin")

    app.dependency_overrides[get_current_user] = _override_get_current_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            invalid_state = await ac.get("/api/v1/posts", params={"state": "hidden"})
            assert invalid_state.status_code == 400
            assert invalid_state.json()["status"] is False

            missing_user = await ac.get("/api/v1/posts", params={"user_id": str(missing_user_id)})
            assert missing_user.status_code == 404
            assert missing_user.json()["message"] == "User not found"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_get_feed_service_success(test_users) -> None:
    user, other = test_users
    visitor = User(
        email="pytest_visitor@example.com",
        role="user",
        firebase_uid="visitor-uid",
        status="active"
    )
    
    async with async_session_factory() as session:
        session.add(visitor)
        user_profile = Profile(
            user_id=user.id,
            first_name="Feed",
            last_name="User",
            profile_photo_url="profiles/feed-user.jpg",
        )
        other_profile = Profile(
            user_id=other.id,
            first_name="Other",
            last_name="User",
            profile_photo_url="profiles/other-user.jpg",
        )
        p1 = Post(author_user_id=user.id, content={"caption": "Draft Post"}, state=PostState.draft)
        p2 = Post(author_user_id=user.id, content={"caption": "Published Post 1"}, state=PostState.published)
        p3 = Post(author_user_id=other.id, content={"caption": "Published Post 2"}, state=PostState.published)
        p4 = Post(author_user_id=other.id, content={"caption": "Hidden Post"}, state=PostState.hidden)
        session.add_all([user_profile, other_profile, p1, p2, p3, p4])
        await session.commit()
        
    async with async_session_factory() as session:
        feed, total, _next_cursor = await get_feed_service(visitor.id, session, include_total=True)
        assert len(feed) >= 2
        captions = {item["content"]["caption"] for item in feed}
        assert "Published Post 1" in captions
        assert "Published Post 2" in captions
        by_caption = {item["content"]["caption"]: item for item in feed}
        assert by_caption["Published Post 1"]["first_name"] == "Feed"
        assert by_caption["Published Post 1"]["last_name"] == "User"
        assert by_caption["Published Post 1"]["profilePhoto_url"].endswith("profiles/feed-user.jpg")
        assert by_caption["Published Post 2"]["first_name"] == "Other"


@pytest.mark.asyncio
async def test_feed_route_accessible_to_superadmin_and_includes_author_profile(test_users) -> None:
    user, other = test_users
    superadmin = SimpleNamespace(id=other.id, role="superadmin")

    async with async_session_factory() as session:
        profile = Profile(
            user_id=user.id,
            first_name="Admin",
            last_name="Feed",
            profile_photo_url="profiles/admin-feed.jpg",
        )
        post = Post(
            author_user_id=user.id,
            content={"caption": "Admin accessible feed", "visibility": "public"},
            state=PostState.published,
        )
        session.add_all([profile, post])
        await session.commit()

    async def _override_get_current_user():
        return superadmin

    async def _fail_if_app_user_dependency_runs():
        raise AssertionError("Feed route should not require app-user-only auth")

    app.dependency_overrides[get_current_user] = _override_get_current_user
    app.dependency_overrides[get_current_app_user] = _fail_if_app_user_dependency_runs
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get("/api/v1/feed")
            assert response.status_code == 200
            body = response.json()
            assert body["status"] is True
            feed_items = body["data"]["items"] if isinstance(body["data"], dict) else body["data"]
            matching = [
                item for item in feed_items
                if item["content"]["caption"] == "Admin accessible feed"
            ]
            assert len(matching) == 1
            assert matching[0]["first_name"] == "Admin"
            assert matching[0]["last_name"] == "Feed"
            assert matching[0]["profilePhoto_url"].endswith("profiles/admin-feed.jpg")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_app_user, None)


@pytest.mark.asyncio
async def test_feed_service_connection_priority(test_users) -> None:
    user, other = test_users

    # Connection low/high user order constraint
    low_id, high_id = (user.id, other.id) if user.id < other.id else (other.id, user.id)

    third_user = User(
        id=uuid.uuid4(),
        email="third@example.com",
        role="user",
        firebase_uid="third-uid",
    )

    async with async_session_factory() as session:
        session.add(third_user)
        await session.flush()
        session.add_all([
            Profile(user_id=other.id, first_name="Conn", last_name="Author"),
            Profile(user_id=third_user.id, first_name="Third", last_name="Author"),
        ])
        conn = Connection(user_low_id=low_id, user_high_id=high_id, is_active=True)
        session.add(conn)

        p_non_conn = Post(
            author_user_id=third_user.id,
            content={"caption": "Non-connection Post"},
            state=PostState.published,
            created_at=datetime.now(timezone.utc)
        )
        p_conn = Post(
            author_user_id=other.id,
            content={"caption": "Connection Post"},
            state=PostState.published,
            created_at=datetime.now(timezone.utc) - timedelta(hours=1)
        )
        session.add_all([p_non_conn, p_conn])
        await session.commit()

    async with async_session_factory() as session:
        feed, total, _next_cursor = await get_feed_service(user.id, session, include_total=True)
        assert len(feed) >= 2
        captions = [item["content"]["caption"] for item in feed]
        assert "Connection Post" in captions
        assert "Non-connection Post" in captions
        # Feed is ordered by event time descending (connection priority removed).
        assert feed[0]["content"]["caption"] == "Non-connection Post"
        assert feed[1]["content"]["caption"] == "Connection Post"


@pytest.mark.asyncio
async def test_routes_post_management_flow(test_users) -> None:
    user, other = test_users

    async def _override_get_current_user():
        return user

    async def _override_get_current_moderator():
        return User(id=user.id, email=user.email, role="moderator")

    app.dependency_overrides[get_current_user] = _override_get_current_user
    app.dependency_overrides[get_current_moderator] = _override_get_current_moderator
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Create a draft post
            create_res = await ac.post("/api/v1/post", json={
                "is_draft": True,
                "content": {
                    "caption": "Init Caption",
                    "content_html": "Init text",
                    "visibility": "public"
                }
            })
            assert create_res.status_code == 200
            post_id = create_res.json()["data"]["id"]

            # 2. POST /post with id (update draft)
            draft_res = await ac.post("/api/v1/post", json={
                "id": post_id,
                "is_draft": True,
                "content": {
                    "caption": "Draft Updated Caption",
                    "content_html": "Draft updated text",
                    "visibility": "public"
                }
            })
            assert draft_res.status_code == 200
            # save_post endpoint returns SavePostData (id, revision_number)
            assert draft_res.json()["data"]["revision_number"] == 2

            # 3. PATCH /admin/posts/reviewed (moderator action) — publish draft
            # First publish via user save as non-draft so admin can review published state
            publish_user_res = await ac.post("/api/v1/post", json={
                "id": post_id,
                "is_draft": False,
                "content": {
                    "caption": "Draft Updated Caption",
                    "content_html": "Draft updated text",
                    "visibility": "public"
                }
            })
            assert publish_user_res.status_code == 200

            publish_res = await ac.patch(
                "/api/v1/admin/posts/reviewed",
                json={
                    "post_id": str(post_id),
                    "status": "published"
                }
            )
            assert publish_res.status_code == 200
            assert publish_res.json()["status"] is True

            # 4. GET /posts/{id}
            get_res = await ac.get(f"/api/v1/posts/{post_id}")
            assert get_res.status_code == 200
            assert get_res.json()["data"]["content"]["caption"] == "Draft Updated Caption"

            # 5. PATCH /posts
            patch_res = await ac.patch("/api/v1/posts", json={
                "id": post_id,
                "content": {
                    "caption": "General Updated Caption",
                    "content_html": "General updated text",
                    "visibility": "public"
                }
            })
            assert patch_res.status_code == 200
            assert patch_res.json()["data"]["state"] == "published"

            # 6. GET /posts (user posts)
            list_res = await ac.get("/api/v1/posts")
            assert list_res.status_code == 200
            list_data = list_res.json()["data"]
            list_items = list_data["items"] if isinstance(list_data, dict) else list_data
            assert len(list_items) >= 1

            # 7. GET /feed
            feed_res = await ac.get("/api/v1/feed")
            assert feed_res.status_code == 200

            # 8. DELETE /posts
            delete_res = await ac.request("DELETE", "/api/v1/posts", json={"id": post_id})
            assert delete_res.status_code == 200

            # Verify hard-deleted
            get_deleted = await ac.get(f"/api/v1/posts/{post_id}")
            assert get_deleted.status_code == 200
            body = get_deleted.json()
            assert body["status"] is False
            assert "not found" in body["message"].lower()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_moderator, None)


@pytest.mark.asyncio
async def test_list_draft_posts_service_returns_only_drafts(test_users) -> None:
    user, other = test_users

    async with async_session_factory() as session:
        draft = Post(
            author_user_id=user.id,
            content={"caption": "My Draft", "visibility": "public"},
            state=PostState.draft,
        )
        published = Post(
            author_user_id=user.id,
            content={"caption": "My Published", "visibility": "public"},
            state=PostState.published,
        )
        other_draft = Post(
            author_user_id=other.id,
            content={"caption": "Other Draft", "visibility": "public"},
            state=PostState.draft,
        )
        session.add_all([draft, published, other_draft])
        await session.commit()

    async with async_session_factory() as session:
        drafts = await list_draft_posts_service(user.id, session)
        assert len(drafts) == 1
        assert drafts[0].caption == "My Draft"
        assert drafts[0].state == PostState.draft


@pytest.mark.asyncio
async def test_delete_draft_post_service_success(test_users) -> None:
    user, _ = test_users

    async with async_session_factory() as session:
        draft = Post(
            author_user_id=user.id,
            content={"caption": "Delete Draft", "visibility": "public"},
            state=PostState.draft,
        )
        session.add(draft)
        await session.commit()
        await session.refresh(draft)
        draft_id = draft.id

    async with async_session_factory() as session:
        await delete_draft_post_service(draft_id, user.id, session)

    async with async_session_factory() as session:
        with pytest.raises(ApiError) as exc_info:
            await get_post_service(draft_id, user.id, session)
        assert "not found" in exc_info.value.message.lower()


@pytest.mark.asyncio
async def test_delete_draft_post_service_rejects_non_draft(test_users) -> None:
    user, _ = test_users

    async with async_session_factory() as session:
        published = Post(
            author_user_id=user.id,
            content={"caption": "Published", "visibility": "public"},
            state=PostState.published,
        )
        session.add(published)
        await session.commit()
        await session.refresh(published)
        post_id = published.id

    async with async_session_factory() as session:
        with pytest.raises(ApiError) as exc_info:
            await delete_draft_post_service(post_id, user.id, session)
        assert "Only draft posts" in exc_info.value.message


@pytest.mark.asyncio
async def test_draftpost_routes_via_test_client(test_users) -> None:
    user, other = test_users

    async with async_session_factory() as session:
        draft = Post(
            author_user_id=user.id,
            content={"caption": "Route Draft", "content_html": "<p>draft</p>", "visibility": "public"},
            state=PostState.draft,
        )
        published = Post(
            author_user_id=user.id,
            content={"caption": "Route Published", "visibility": "public"},
            state=PostState.published,
        )
        session.add_all([draft, published])
        await session.commit()
        await session.refresh(draft)
        await session.refresh(published)
        draft_id = draft.id
        published_id = published.id

    async def _override_get_current_user():
        return user

    app.dependency_overrides[get_current_user] = _override_get_current_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            list_res = await ac.get("/api/v1/draftpost")
            assert list_res.status_code == 200
            body = list_res.json()
            assert body["status"] is True
            assert len(body["data"]) == 1
            assert body["data"][0]["id"] == str(draft_id)
            assert body["data"][0]["state"] == "draft"

            delete_res = await ac.request("DELETE", "/api/v1/draftpost", json={"id": str(draft_id)})
            assert delete_res.status_code == 200
            assert delete_res.json()["message"] == "Draft post deleted successfully"

            delete_published = await ac.request(
                "DELETE", "/api/v1/draftpost", json={"id": str(published_id)}
            )
            assert delete_published.status_code == 200
            assert delete_published.json()["status"] is False
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_create_draft_route_returns_draftpost(test_users) -> None:
    user, _ = test_users

    async def _override_get_current_user():
        return user

    app.dependency_overrides[get_current_user] = _override_get_current_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            create_res = await ac.post("/api/v1/post", json={
                "is_draft": True,
                "content": {
                    "caption": "Route created draft",
                },
            })
            assert create_res.status_code == 200
            assert create_res.json()["message"] == "Post created and saved as draft"
            draft_id = create_res.json()["data"]["id"]

            list_res = await ac.get("/api/v1/draftpost")
            assert list_res.status_code == 200
            body = list_res.json()
            assert len(body["data"]) == 1
            assert body["data"][0]["id"] == draft_id
            assert body["data"][0]["state"] == "draft"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_list_other_user_posts_visibility(test_users) -> None:
    user, other = test_users

    async with async_session_factory() as session:
        # Create other profile (visibility: public)
        profile_public = Profile(
            user_id=other.id,
            first_name="Public",
            last_name="User",
            profile_visibility="public",
        )
        post_public = Post(
            author_user_id=other.id,
            content={"caption": "Other public post", "visibility": "public"},
            state=PostState.published,
        )
        session.add_all([profile_public, post_public])
        await session.commit()

    async def _override_get_current_user():
        return user

    app.dependency_overrides[get_current_user] = _override_get_current_user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get(f"/api/v1/posts?user_id={other.id}")
            assert res.status_code == 200
            body = res.json()
            assert body["status"] is True
            items = body["data"]["items"] if isinstance(body["data"], dict) else body["data"]
            assert len(items) == 1
            assert items[0]["content"]["caption"] == "Other public post"

            # Update other profile visibility to private
            async with async_session_factory() as session:
                other_profile_db = (
                    await session.execute(select(Profile).where(Profile.user_id == other.id))
                ).scalar_one()
                other_profile_db.profile_visibility = "private"
                await session.commit()

            res_blocked = await ac.get(f"/api/v1/posts?user_id={other.id}")
            assert res_blocked.status_code == 200
            body_blocked = res_blocked.json()
            assert body_blocked["status"] is True
            assert "account is private" in body_blocked["message"].lower()
            blocked_items = (
                body_blocked["data"]["items"]
                if isinstance(body_blocked["data"], dict)
                else body_blocked["data"]
            )
            assert len(blocked_items) == 0
    finally:
        app.dependency_overrides.pop(get_current_user, None)

