"""SQLAlchemy cursor instrumentation for hydrate_user_posts_timeline()."""

from __future__ import annotations

import contextvars
import logging
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from apps.feed.perf.posts_perf import perf_ms, posts_perf_enabled

logger = logging.getLogger(__name__)

_active_tracker: contextvars.ContextVar[HydrateTimelineSqlTracker | None] = contextvars.ContextVar(
    "hydrate_timeline_sql_tracker",
    default=None,
)

_listeners_registered = False

_UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


@dataclass
class _QueryTiming:
    duration_ms: float = 0.0
    query_type: str = "SELECT"
    count: int = 0


@dataclass(frozen=True)
class HydrateQueryRecord:
    query_index: int
    bucket: str
    operation: str
    likely_source: str
    duration_ms: float
    query_type: str
    phase: str
    sql_fingerprint: str


@dataclass
class _PendingQuery:
    bucket: str
    operation: str
    likely_source: str
    query_type: str
    sql_fingerprint: str
    phase: str
    started: float


@dataclass
class HydrateTimelineSqlTracker:
    """Accumulates per-operation SQL timings via engine cursor events."""

    unique_post_count: int = 0
    post_count: int = 0
    attachment_count: int = 0
    media_asset_count: int = 0
    phase: str = "posts_load"
    _stack: list[_PendingQuery] = field(default_factory=list)
    _timings: dict[str, _QueryTiming] = field(default_factory=dict)
    _all_queries: list[HydrateQueryRecord] = field(default_factory=list)
    _query_index: int = 0

    def set_phase(self, phase: str) -> None:
        self.phase = phase

    def on_before_cursor(self, statement: str) -> None:
        bucket, operation, likely_source = identify_hydrate_query(statement)
        self._stack.append(
            _PendingQuery(
                bucket=bucket,
                operation=operation,
                likely_source=likely_source,
                query_type=_query_type_from_statement(statement),
                sql_fingerprint=sql_fingerprint(statement),
                phase=self.phase,
                started=time.perf_counter(),
            )
        )

    def on_after_cursor(self) -> None:
        if not self._stack:
            return
        pending = self._stack.pop()
        duration_ms = perf_ms(pending.started)
        timing = self._timings.setdefault(pending.bucket, _QueryTiming())
        timing.duration_ms += duration_ms
        timing.query_type = pending.query_type
        timing.count += 1

        self._query_index += 1
        self._all_queries.append(
            HydrateQueryRecord(
                query_index=self._query_index,
                bucket=pending.bucket,
                operation=pending.operation,
                likely_source=pending.likely_source,
                duration_ms=duration_ms,
                query_type=pending.query_type,
                phase=pending.phase,
                sql_fingerprint=pending.sql_fingerprint,
            )
        )

        if posts_perf_enabled():
            log_hydrate_db_execute(
                pending.bucket,
                duration_ms,
                query_index=self._query_index,
                operation=pending.operation,
                likely_source=pending.likely_source,
                query_type=pending.query_type,
                phase=pending.phase,
                sql_fingerprint=pending.sql_fingerprint,
            )

    def set_loaded_counts(
        self,
        *,
        attachment_count: int,
        media_asset_count: int,
    ) -> None:
        self.attachment_count = attachment_count
        self.media_asset_count = media_asset_count

    def emit_db_execute_logs(self) -> None:
        for operation in (
            "hydrate_posts_main",
            "hydrate_posts_attachments",
            "hydrate_posts_media_assets",
            "hydrate_posts_other",
        ):
            timing = self._timings.get(operation)
            if timing is None or timing.count == 0:
                continue
            extra: dict[str, Any] = {
                "query_type": timing.query_type,
                "operation": operation,
                "query_count": timing.count,
                "post_count": self.post_count,
                "unique_post_count": self.unique_post_count,
            }
            if operation == "hydrate_posts_attachments":
                extra["attachment_count"] = self.attachment_count
            if operation == "hydrate_posts_media_assets":
                extra["media_asset_count"] = self.media_asset_count
            log_hydrate_db_execute(operation, timing.duration_ms, **extra)

        self._emit_other_query_report()

    def _emit_other_query_report(self) -> None:
        other_records = [
            record for record in self._all_queries if record.bucket == "hydrate_posts_other"
        ]
        if not other_records:
            return

        total_ms = sum(record.duration_ms for record in other_records)
        avg_ms = total_ms / len(other_records)
        slowest = max(other_records, key=lambda record: record.duration_ms)

        log_hydrate_stage(
            "hydrate_posts_other_summary",
            total_ms,
            query_count=len(other_records),
            avg_ms=round(avg_ms, 2),
            slowest_ms=round(slowest.duration_ms, 2),
            slowest_query_index=slowest.query_index,
            slowest_operation=slowest.operation,
            slowest_likely_source=slowest.likely_source,
        )

        for record in other_records:
            logger.info(
                "[POSTS PERF] hydrate_posts_other_row "
                "query_index=%s duration_ms=%.2f operation=%s likely_source=%s phase=%s "
                "sql_fingerprint=%s",
                record.query_index,
                record.duration_ms,
                record.operation,
                record.likely_source,
                record.phase,
                record.sql_fingerprint,
            )


def log_hydrate_db_execute(name: str, duration_ms: float, **extra: Any) -> None:
    if not posts_perf_enabled():
        return
    suffix = ""
    if extra:
        suffix = " " + " ".join(f"{key}={value}" for key, value in extra.items())
    logger.info(
        "[POSTS PERF] db_execute name=%s duration_ms=%.2f%s",
        name,
        duration_ms,
        suffix,
    )


def log_hydrate_stage(stage: str, duration_ms: float, **extra: Any) -> None:
    if not posts_perf_enabled():
        return
    suffix = ""
    if extra:
        suffix = " " + " ".join(f"{key}={value}" for key, value in extra.items())
    logger.info(
        "[POSTS PERF] stage=%s duration_ms=%.2f%s",
        stage,
        duration_ms,
        suffix,
    )


def sql_fingerprint(statement: str, max_len: int = 180) -> str:
    sql = " ".join(statement.split())
    sql = _UUID_PATTERN.sub("?", sql)
    sql = re.sub(r"'[^']*'", "'?'", sql)
    sql = re.sub(r"\b\d+\b", "?", sql)
    if len(sql) > max_len:
        return sql[:max_len] + "..."
    return sql


def _query_type_from_statement(statement: str) -> str:
    stripped = statement.lstrip()
    if not stripped:
        return "UNKNOWN"
    return stripped.split(None, 1)[0].upper()


def _primary_from_table(sql: str) -> str:
    match = re.search(r"\bfrom\s+([a-z_][a-z0-9_]*)", sql)
    if match:
        return match.group(1)
    return "unknown"


def _where_mentions(sql: str, column: str) -> bool:
    where_clause = sql.split("where", 1)[-1] if "where" in sql else sql
    return column in where_clause


def identify_hydrate_query(statement: str) -> tuple[str, str, str]:
    """Return (bucket, operation, likely_source)."""
    sql = statement.lower()

    # Combined timeline raw hydration: posts + profiles + attachments + media in one trip.
    if "post_attachments" in sql and re.search(r"\bfrom\s+posts\b", sql):
        return (
            "hydrate_posts_main",
            "timeline_raw_post_hydration",
            "timeline_post_hydration raw SQL (posts+profiles+attachments+media)",
        )

    if "post_attachments" in sql:
        if _where_mentions(sql, "post_attachments.post_id") or _where_mentions(sql, "post_id"):
            return (
                "hydrate_posts_attachments",
                "post_selectin_attachments",
                "Post.attachments selectinload (explicit options)",
            )
        return (
            "hydrate_posts_attachments",
            "media_asset_backref_post_attachments",
            "MediaAsset.posts lazy=selectin backref",
        )

    if "media_assets" in sql:
        return (
            "hydrate_posts_media_assets",
            "post_attachment_selectin_media_asset",
            "PostAttachment.media_asset selectinload (explicit options)",
        )

    if "reposts" in sql:
        if "profiles" in sql or re.search(r"\bjoin\s+profiles\b", sql):
            return (
                "hydrate_posts_reposts",
                "repost_with_profile_hydration",
                "Combined repost + reposter profile raw SQL in hydrate_user_posts_timeline",
            )
        return (
            "hydrate_posts_reposts",
            "repost_lookup",
            "Explicit repost query in hydrate_user_posts_timeline",
        )

    if re.search(r"\bfrom\s+posts\b", sql) or re.search(r"join\s+posts\b", sql):
        return (
            "hydrate_posts_main",
            "hydrate_posts_main",
            "Main Post + profile/moderator join query",
        )

    if "post_revisions" in sql:
        return (
            "hydrate_posts_other",
            "post_lazy_revisions",
            "Post.revisions lazy=selectin on Post model",
        )

    if "post_hashtags" in sql:
        return (
            "hydrate_posts_other",
            "post_lazy_hashtags",
            "Post.hashtags lazy=selectin on Post model",
        )

    if "post_topics" in sql:
        return (
            "hydrate_posts_other",
            "post_lazy_topics",
            "Post.topics lazy=selectin on Post model",
        )

    if "post_reactions" in sql:
        return (
            "hydrate_posts_other",
            "post_lazy_reactions",
            "Post.reactions lazy=selectin on Post model",
        )

    if "link_previews" in sql:
        return (
            "hydrate_posts_other",
            "post_lazy_link_previews",
            "Post.link_previews lazy=selectin on Post model",
        )

    if "bookmarks" in sql:
        if _where_mentions(sql, "bookmarks.post_id") or (
            "post_id" in sql and "bookmarks.user_id" not in sql
        ):
            return (
                "hydrate_posts_other",
                "post_lazy_bookmarks",
                "Post.bookmarks lazy=selectin on Post model",
            )
        return (
            "hydrate_posts_other",
            "user_selectin_bookmarks",
            "User.bookmarks lazy=selectin on User model (moderator User rows)",
        )

    if "share_events" in sql:
        if _where_mentions(sql, "share_events.post_id") or (
            "post_id" in sql and "share_events.user_id" not in sql
        ):
            return (
                "hydrate_posts_other",
                "post_lazy_share_events",
                "Post.share_events lazy=selectin on Post model",
            )
        return (
            "hydrate_posts_other",
            "user_selectin_share_events",
            "User.share_events lazy=selectin on User model (moderator User rows)",
        )

    if "comment_reactions" in sql:
        if _where_mentions(sql, "comment_reactions.comment_id"):
            return (
                "hydrate_posts_other",
                "comment_selectin_reactions",
                "Comment.reactions lazy=selectin (via Post.comments cascade)",
            )
        return (
            "hydrate_posts_other",
            "user_selectin_comment_reactions",
            "User.comment_reactions lazy=selectin on User model",
        )

    if "comments" in sql:
        if _where_mentions(sql, "comments.post_id") or (
            "post_id" in sql and "comments.user_id" not in sql
        ):
            return (
                "hydrate_posts_other",
                "post_lazy_comments",
                "Post.comments lazy=selectin on Post model",
            )
        return (
            "hydrate_posts_other",
            "user_selectin_comments",
            "User.comments lazy=selectin on User model (moderator User rows)",
        )

    if "hashtags" in sql:
        return (
            "hydrate_posts_other",
            "hashtag_selectin_backref",
            "Hashtag.post_hashtags lazy=selectin (via Post.hashtags)",
        )

    if "topics" in sql and "post_topics" not in sql:
        return (
            "hydrate_posts_other",
            "topic_selectin_backref",
            "Topic.post_topics lazy=selectin (via Post.topics)",
        )

    if "user_roles" in sql:
        return (
            "hydrate_posts_other",
            "user_selectin_user_roles",
            "User.roles lazy=selectin on User model (moderator User rows)",
        )

    if "role_permissions" in sql:
        return (
            "hydrate_posts_other",
            "role_selectin_role_permissions",
            "Role.role_permissions lazy=selectin (via User.roles.role)",
        )

    if re.search(r"\bfrom\s+roles\b", sql) or re.search(r"join\s+roles\b", sql):
        return (
            "hydrate_posts_other",
            "user_role_selectin_roles",
            "UserRole.role lazy=selectin (via User.roles)",
        )

    if "permissions" in sql:
        return (
            "hydrate_posts_other",
            "role_selectin_permissions",
            "Role.permissions lazy=selectin (via User.roles.role)",
        )

    if re.search(r"\bfrom\s+users\b", sql) or re.search(r"join\s+users\b", sql):
        return (
            "hydrate_posts_other",
            "user_lookup",
            "User table access during hydration",
        )

    if "profiles" in sql:
        return (
            "hydrate_posts_other",
            "profile_lookup",
            "Profile table access during hydration",
        )

    table = _primary_from_table(sql)
    return (
        "hydrate_posts_other",
        f"unclassified_{table}",
        f"Unclassified query touching table {table}",
    )


def _register_engine_listeners(sync_engine: Any) -> None:
    global _listeners_registered
    if _listeners_registered:
        return

    @event.listens_for(sync_engine, "before_cursor_execute")
    def _hydrate_before_cursor_execute(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        tracker = _active_tracker.get()
        if tracker is None:
            return
        tracker.on_before_cursor(statement)

    @event.listens_for(sync_engine, "after_cursor_execute")
    def _hydrate_after_cursor_execute(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        tracker = _active_tracker.get()
        if tracker is None:
            return
        tracker.on_after_cursor()

    _listeners_registered = True


@contextmanager
def hydrate_timeline_sql_tracking(
    db: AsyncSession,
    *,
    unique_post_count: int,
    post_count: int,
) -> Iterator[HydrateTimelineSqlTracker]:
    if not posts_perf_enabled():
        yield HydrateTimelineSqlTracker(
            unique_post_count=unique_post_count,
            post_count=post_count,
        )
        return

    bind = db.get_bind()
    sync_engine = getattr(bind, "sync_engine", bind)
    _register_engine_listeners(sync_engine)

    tracker = HydrateTimelineSqlTracker(
        unique_post_count=unique_post_count,
        post_count=post_count,
    )
    token = _active_tracker.set(tracker)
    try:
        yield tracker
    finally:
        _active_tracker.reset(token)


def count_loaded_attachment_media(post_rows: dict[Any, tuple]) -> tuple[int, int]:
    attachment_count = 0
    media_asset_count = 0
    for post, *_rest in post_rows.values():
        attachments = getattr(post, "attachments", None) or []
        attachment_count += len(attachments)
        for attachment in attachments:
            if getattr(attachment, "media_asset", None) is not None:
                media_asset_count += 1
    return attachment_count, media_asset_count
