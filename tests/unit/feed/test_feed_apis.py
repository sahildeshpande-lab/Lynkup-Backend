from __future__ import annotations

import io
import uuid
import pytest
import pytest_asyncio
from fastapi import UploadFile
from common.exceptions import ApiError
import httpx
from httpx import AsyncClient
from sqlmodel import select
from sqlalchemy import text

from entrypoints.api import app
from core.database.session import async_session_factory
from datetime import datetime, timezone, timedelta
from core.database.init import init_db
from core.security.auth import get_current_user, get_current_moderator
from apps.accounts.db_models import User
from apps.feed.db_models import Post, MediaAsset, PostAttachment
from apps.connections.db_models import Connection
from common.enums import MediaType, MediaAssetState, PostState
from apps.feed.schemas import SavePostRequest, EditPostRequest, MediaItem, PostContentPayload, EditPostContentPayload, DeletePostRequest
from apps.feed.services import (
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
    get_feed_service,
)


async def clean_feed_pytest_data(session):
    # Find test users
    result = await session.execute(
        select(User).where(User.email.in_(["pytest_feed_user@example.com", "pytest_feed_other@example.com", "third@example.com"]))
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
            DELETE FROM users 
            WHERE id = ANY(:user_ids)
        """), params)
        
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
            file=file,
            media_type=MediaType.image,
            db=session,
        )

        assert res["id"] is not None
        assert "posts/" in res["key"]
        assert res["key"].endswith(".png")
        assert res["type"] == MediaType.image
        assert "/static/uploads/" in res["url"]

        media_id = res["id"]
        stmt = select(MediaAsset).where(MediaAsset.id == media_id)
        db_media = (await session.execute(stmt)).scalar_one_or_none()

        assert db_media is not None
        assert db_media.owner_user_id == user.id
        assert db_media.key == res["key"]
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
            await upload_post_media_service(user.id, file_empty, MediaType.image, session)
        assert "empty" in exc_info.value.message

    # Mismatch media type
    file_mismatch = UploadFile(
        filename="video.mp4",
        file=io.BytesIO(b"some video bytes"),
        headers={"content-type": "video/mp4"},
    )
    async with async_session_factory() as session:
        with pytest.raises(ApiError) as exc_info:
            await upload_post_media_service(user.id, file_mismatch, MediaType.image, session)
        assert "Invalid file type" in exc_info.value.message


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
        assert post.state == PostState.processing  # public defaults to processing

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
        assert post.state == PostState.processing


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
                ("file", ("test.jpg", io.BytesIO(file_data), "image/jpeg")),
            ]
            data = {
                "type": "image",
            }
            response = await ac.post("/api/v1/postupload", files=files, data=data)
            assert response.status_code == 200
            body = response.json()
            assert body["status"] is True
            assert body["message"] == "image uploaded"
            assert isinstance(body["data"], dict)
            media_id = body["data"]["id"]

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
async def test_update_post_service_success(test_users) -> None:
    user, _ = test_users
    payload = SavePostRequest(
        content=PostContentPayload(caption="Original", content_html="Original text", visibility="public")
    )
    async with async_session_factory() as session:
        post = await save_post_service(user.id, payload, session)
        post_id = post.id
        assert post.state == PostState.processing

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
        assert post.state == PostState.processing


@pytest.mark.asyncio
async def test_publish_post_service_success(test_users) -> None:
    user, _ = test_users
    payload = SavePostRequest(
        content=PostContentPayload(caption="Original", content_html="Original text", visibility="public")
    )
    async with async_session_factory() as session:
        post = await save_post_service(user.id, payload, session)
        post_id = post.id

    async with async_session_factory() as session:
        post = await admin_publish_post_service(post_id, "publish", user.id, session)
        assert post.state == PostState.published

@pytest.mark.asyncio
async def test_flag_post_service_success(test_users) -> None:
    user, _ = test_users
    payload = SavePostRequest(
        content=PostContentPayload(caption="Original", content_html="Original text", visibility="public")
    )
    async with async_session_factory() as session:
        post = await save_post_service(user.id, payload, session)
        post_id = post.id

    async with async_session_factory() as session:
        post = await admin_publish_post_service(post_id, "flag", user.id, session)
        assert post.state == PostState.flagged


@pytest.mark.asyncio
async def test_get_post_service_visibility(test_users) -> None:
    user, other = test_users
    payload = SavePostRequest(
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
        post = await save_post_service(user.id, payload, session)
        post_id = post.id

    async with async_session_factory() as session:
        await delete_post_service(post_id, user.id, session)

    async with async_session_factory() as session:
        post = await get_post_service(post_id, user.id, session)
        assert post.state == PostState.deleted


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
        posts_owner = await list_user_posts_service(user.id, user.id, session)
        assert len(posts_owner) == 4
        captions = {p.caption for p in posts_owner}
        assert captions == {"Draft Post", "Published Post", "Flagged Post", "Deleted Post"}
        
        posts_other = await list_user_posts_service(user.id, other.id, session)
        assert len(posts_other) == 1
        assert posts_other[0].caption == "Published Post"


@pytest.mark.asyncio
async def test_get_feed_service_success(test_users) -> None:
    user, other = test_users
    
    async with async_session_factory() as session:
        p1 = Post(author_user_id=user.id, content={"caption": "Draft Post"}, state=PostState.draft)
        p2 = Post(author_user_id=user.id, content={"caption": "Published Post 1"}, state=PostState.published)
        p3 = Post(author_user_id=other.id, content={"caption": "Published Post 2"}, state=PostState.published)
        p4 = Post(author_user_id=other.id, content={"caption": "Hidden Post"}, state=PostState.hidden)
        session.add_all([p1, p2, p3, p4])
        await session.commit()
        
    async with async_session_factory() as session:
        feed = await get_feed_service(user.id, session)
        assert len(feed) == 2
        captions = {f.caption for f in feed}
        assert "Published Post 1" in captions
        assert "Published Post 2" in captions


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
        feed = await get_feed_service(user.id, session)
        assert len(feed) >= 2
        # Connection post must appear first despite being older
        assert feed[0].caption == "Connection Post"
        assert feed[1].caption == "Non-connection Post"


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
            # 1. Create a post
            create_res = await ac.post("/api/v1/post", json={
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
                "content": {
                    "caption": "Draft Updated Caption",
                    "content_html": "Draft updated text",
                    "visibility": "public"
                }
            })
            assert draft_res.status_code == 200
            # save_post endpoint returns SavePostData (id, revision_number)
            assert draft_res.json()["data"]["revision_number"] == 2

            # 3. POST /posts/publish (admin route)
            publish_res = await ac.post(
                "/api/v1/posts/publish",
                json={
                    "post_id": str(post_id),
                    "action": "publish"
                }
            )
            assert publish_res.status_code == 200
            assert "published" in publish_res.json()["message"]
            assert publish_res.json()["data"]["state"] == "published"

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
                    "visibility": "hidden"
                }
            })
            assert patch_res.status_code == 200
            assert patch_res.json()["data"]["state"] == "hidden"

            # 6. GET /posts (user posts)
            list_res = await ac.get("/api/v1/posts")
            assert list_res.status_code == 200
            assert len(list_res.json()["data"]) >= 1

            # 7. GET /feed
            feed_res = await ac.get("/api/v1/feed")
            assert feed_res.status_code == 200

            # 8. DELETE /posts
            delete_res = await ac.request("DELETE", "/api/v1/posts", json={"id": post_id})
            assert delete_res.status_code == 200

            # Verify deleted
            get_deleted = await ac.get(f"/api/v1/posts/{post_id}")
            assert get_deleted.status_code == 200
            assert get_deleted.json()["data"]["state"] == "deleted"
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
        post = await get_post_service(draft_id, user.id, session)
        assert post.state == PostState.deleted


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
