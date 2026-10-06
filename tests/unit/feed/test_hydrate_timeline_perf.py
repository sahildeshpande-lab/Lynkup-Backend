from __future__ import annotations

from types import SimpleNamespace

from apps.feed.perf.hydrate_timeline_perf import (
    HydrateTimelineSqlTracker,
    count_loaded_attachment_media,
    identify_hydrate_query,
    sql_fingerprint,
)
from apps.feed.perf.posts_perf import posts_perf_context


def test_identify_post_lazy_revisions() -> None:
    bucket, operation, source = identify_hydrate_query(
        "SELECT post_revisions.id FROM post_revisions WHERE post_revisions.post_id IN ($1)"
    )
    assert bucket == "hydrate_posts_other"
    assert operation == "post_lazy_revisions"
    assert "Post.revisions" in source


def test_identify_post_lazy_bookmarks() -> None:
    bucket, operation, _source = identify_hydrate_query(
        "SELECT bookmarks.id FROM bookmarks WHERE bookmarks.post_id IN ($1)"
    )
    assert bucket == "hydrate_posts_other"
    assert operation == "post_lazy_bookmarks"


def test_identify_user_selectin_roles() -> None:
    bucket, operation, _source = identify_hydrate_query(
        "SELECT user_roles.user_id FROM user_roles WHERE user_roles.user_id IN ($1)"
    )
    assert bucket == "hydrate_posts_other"
    assert operation == "user_selectin_user_roles"


def test_identify_combined_repost_profile_hydration() -> None:
    bucket, operation, _source = identify_hydrate_query(
        "SELECT r.id FROM reposts r JOIN profiles pr ON pr.id = r.profile_id "
        "WHERE r.id IN ($1)"
    )
    assert bucket == "hydrate_posts_reposts"
    assert operation == "repost_with_profile_hydration"


def test_identify_explicit_attachments_selectinload() -> None:
    bucket, operation, _source = identify_hydrate_query(
        "SELECT post_attachments.id FROM post_attachments WHERE post_attachments.post_id IN ($1)"
    )
    assert bucket == "hydrate_posts_attachments"
    assert operation == "post_selectin_attachments"


def test_sql_fingerprint_redacts_literals() -> None:
    sql = (
        "SELECT posts.id FROM posts WHERE posts.id IN "
        "('11111111-1111-1111-1111-111111111111')"
    )
    assert "11111111" not in sql_fingerprint(sql)
    assert "?" in sql_fingerprint(sql)


def test_identify_timeline_raw_hydration() -> None:
    bucket, operation, _source = identify_hydrate_query(
        "SELECT posts.id, post_attachments.id FROM posts "
        "LEFT JOIN post_attachments ON post_attachments.post_id = posts.id"
    )
    assert bucket == "hydrate_posts_main"
    assert operation == "timeline_raw_post_hydration"


def test_identify_media_assets_and_posts_main() -> None:
    bucket, operation, _ = identify_hydrate_query(
        "SELECT media_assets.id FROM media_assets WHERE media_assets.id IN ($1)"
    )
    assert bucket == "hydrate_posts_media_assets"
    assert operation == "post_attachment_selectin_media_asset"

    bucket, operation, _ = identify_hydrate_query("SELECT posts.id FROM posts")
    assert bucket == "hydrate_posts_main"
    assert operation == "hydrate_posts_main"


def test_identify_other_lazy_load_branches() -> None:
    cases = [
        ("SELECT post_hashtags.id FROM post_hashtags", "post_lazy_hashtags"),
        ("SELECT post_topics.id FROM post_topics", "post_lazy_topics"),
        ("SELECT post_reactions.id FROM post_reactions", "post_lazy_reactions"),
        ("SELECT link_previews.id FROM link_previews", "post_lazy_link_previews"),
        ("SELECT comments.id FROM comments WHERE comments.post_id IN ($1)", "post_lazy_comments"),
        ("SELECT comments.id FROM comments WHERE comments.user_id IN ($1)", "user_selectin_comments"),
        ("SELECT share_events.id FROM share_events WHERE share_events.post_id IN ($1)", "post_lazy_share_events"),
        ("SELECT comment_reactions.id FROM comment_reactions WHERE comment_reactions.comment_id IN ($1)", "comment_selectin_reactions"),
        ("SELECT hashtags.id FROM hashtags", "hashtag_selectin_backref"),
        ("SELECT topics.id FROM topics", "topic_selectin_backref"),
        ("SELECT role_permissions.id FROM role_permissions", "role_selectin_role_permissions"),
        ("SELECT roles.id FROM roles", "user_role_selectin_roles"),
        ("SELECT permissions.id FROM permissions", "role_selectin_permissions"),
        ("SELECT users.id FROM users", "user_lookup"),
        ("SELECT profiles.id FROM profiles", "profile_lookup"),
        ("SELECT widgets.id FROM widgets", "unclassified_widgets"),
    ]
    for sql, operation in cases:
        bucket, op, _ = identify_hydrate_query(sql)
        assert bucket == "hydrate_posts_other"
        assert op == operation


def test_count_loaded_attachment_media() -> None:
    post = SimpleNamespace(
        attachments=[
            SimpleNamespace(media_asset=SimpleNamespace(id=1)),
            SimpleNamespace(media_asset=None),
        ]
    )
    counts = count_loaded_attachment_media({1: (post, None, None, None)})
    assert counts == (2, 1)


def test_hydrate_tracker_records_queries(caplog) -> None:
    import logging

    caplog.set_level(logging.INFO)
    tracker = HydrateTimelineSqlTracker(unique_post_count=1, post_count=1)
    tracker.set_phase("posts_load")
    tracker.on_before_cursor("SELECT posts.id FROM posts")
    tracker.on_after_cursor()
    tracker.set_loaded_counts(attachment_count=1, media_asset_count=1)
    with posts_perf_context():
        tracker.emit_db_execute_logs()
    assert "[POSTS PERF] db_execute name=hydrate_posts_main" in caplog.text
