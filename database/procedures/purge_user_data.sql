-- Permanent PostgreSQL purge for a single user.
-- Application code must collect external identifiers (Firebase, Stream, Spaces)
-- BEFORE calling this procedure, then run external cleanup AFTER COMMIT.
--
-- Keep synchronized with:
--   alembic/versions/20260809_add_user_purge_procedure.py

CREATE OR REPLACE PROCEDURE purge_user_data(
    p_user_id UUID
)
LANGUAGE plpgsql
AS $$
BEGIN
    -- -------------------------------------------------------------------------
    -- Temp ID sets (session-scoped; dropped at commit/rollback end of txn)
    -- -------------------------------------------------------------------------
    CREATE TEMP TABLE tmp_post_ids ON COMMIT DROP AS
    SELECT id FROM posts WHERE author_user_id = p_user_id;

    CREATE TEMP TABLE tmp_media_asset_ids ON COMMIT DROP AS
    SELECT id FROM media_assets WHERE owner_user_id = p_user_id;

    -- User's comments + all descendants (levels 1..3, one-based in schema)
    CREATE TEMP TABLE tmp_comment_ids ON COMMIT DROP AS
    WITH RECURSIVE comment_tree AS (
        SELECT c.id, c.level
        FROM comments c
        WHERE c.user_id = p_user_id
        UNION
        SELECT c.id, c.level
        FROM comments c
        INNER JOIN comment_tree ct ON c.parent_comment_id = ct.id
    )
    SELECT id FROM comment_tree
    UNION
    SELECT c.id
    FROM comments c
    WHERE c.post_id IN (SELECT id FROM tmp_post_ids);

    -- -------------------------------------------------------------------------
    -- Social graph (both sides)
    -- -------------------------------------------------------------------------
    DELETE FROM blocks
    WHERE blocker_user_id = p_user_id
       OR blocked_user_id = p_user_id;

    DELETE FROM follows
    WHERE follower_user_id = p_user_id
       OR following_user_id = p_user_id;

    DELETE FROM connection_requests
    WHERE sender_user_id = p_user_id
       OR receiver_user_id = p_user_id;

    DELETE FROM connections
    WHERE user_low_id = p_user_id
       OR user_high_id = p_user_id;

    DELETE FROM connection_recommendation_snapshots
    WHERE user_id = p_user_id;

    -- -------------------------------------------------------------------------
    -- Engagement on other users' content
    -- -------------------------------------------------------------------------
    DELETE FROM comment_reactions
    WHERE user_id = p_user_id
       OR comment_id IN (SELECT id FROM tmp_comment_ids);

    DELETE FROM post_reactions
    WHERE user_id = p_user_id
       OR post_id IN (SELECT id FROM tmp_post_ids);

    DELETE FROM bookmarks
    WHERE user_id = p_user_id
       OR post_id IN (SELECT id FROM tmp_post_ids);

    DELETE FROM share_events
    WHERE user_id = p_user_id
       OR post_id IN (SELECT id FROM tmp_post_ids);

    DELETE FROM reposts
    WHERE user_id = p_user_id
       OR post_id IN (SELECT id FROM tmp_post_ids);

    -- Comments: deepest levels first (NO ACTION parent FK)
    DELETE FROM comments
    WHERE id IN (SELECT id FROM tmp_comment_ids)
      AND level = 3;

    DELETE FROM comments
    WHERE id IN (SELECT id FROM tmp_comment_ids)
      AND level = 2;

    DELETE FROM comments
    WHERE id IN (SELECT id FROM tmp_comment_ids)
      AND level = 1;

    -- Safety net for any remaining rows still owned by the user
    DELETE FROM comments WHERE user_id = p_user_id;

    -- -------------------------------------------------------------------------
    -- Posts and dependents
    -- -------------------------------------------------------------------------
    DELETE FROM link_previews
    WHERE post_id IN (SELECT id FROM tmp_post_ids);

    DELETE FROM post_hashtags
    WHERE post_id IN (SELECT id FROM tmp_post_ids);

    DELETE FROM post_topics
    WHERE post_id IN (SELECT id FROM tmp_post_ids);

    DELETE FROM post_revisions
    WHERE post_id IN (SELECT id FROM tmp_post_ids)
       OR editor_user_id = p_user_id;

    DELETE FROM post_attachments
    WHERE post_id IN (SELECT id FROM tmp_post_ids)
       OR media_asset_id IN (SELECT id FROM tmp_media_asset_ids);

    UPDATE posts
    SET moderator_id = NULL
    WHERE moderator_id = p_user_id;

    DELETE FROM posts
    WHERE id IN (SELECT id FROM tmp_post_ids);

    DELETE FROM media_assets
    WHERE id IN (SELECT id FROM tmp_media_asset_ids);

    -- -------------------------------------------------------------------------
    -- Reports / moderation references
    -- -------------------------------------------------------------------------
    DELETE FROM reports
    WHERE reported_id = p_user_id;

    UPDATE reports
    SET moderator_id = NULL
    WHERE moderator_id = p_user_id;

    UPDATE moderation_assignment_state
    SET last_assigned_moderator_id = NULL
    WHERE last_assigned_moderator_id = p_user_id;

    -- -------------------------------------------------------------------------
    -- Notifications (user-specific only; keep campaigns)
    -- -------------------------------------------------------------------------
    DELETE FROM notifications
    WHERE recipient_user_id = p_user_id;

    DELETE FROM notification_preferences
    WHERE user_id = p_user_id;

    DELETE FROM notification_campaign_audience
    WHERE user_id = p_user_id;

    UPDATE notification_campaigns
    SET created_by_admin_id = NULL
    WHERE created_by_admin_id = p_user_id;

    -- -------------------------------------------------------------------------
    -- Learning recommendations (logs cascade from profiles.user_id)
    -- -------------------------------------------------------------------------
    UPDATE learning_recommendation_settings
    SET updated_by = NULL
    WHERE updated_by = p_user_id;

    UPDATE learning_recommendation_settings_logs
    SET updated_by = NULL
    WHERE updated_by = p_user_id;

    DELETE FROM learning_recommendation_logs
    WHERE user_id = p_user_id;

    -- -------------------------------------------------------------------------
    -- Profile
    -- -------------------------------------------------------------------------
    DELETE FROM profile_stats
    WHERE profile_id IN (SELECT id FROM profiles WHERE user_id = p_user_id);

    DELETE FROM profiles
    WHERE user_id = p_user_id;

    -- -------------------------------------------------------------------------
    -- Security / devices / roles / consent
    -- -------------------------------------------------------------------------
    DELETE FROM refresh_tokens WHERE user_id = p_user_id;
    DELETE FROM password_reset_tokens WHERE user_id = p_user_id;
    DELETE FROM security_events WHERE user_id = p_user_id;
    DELETE FROM user_installations WHERE user_id = p_user_id;
    DELETE FROM consent_records WHERE user_id = p_user_id;
    DELETE FROM user_roles WHERE user_id = p_user_id;

    -- -------------------------------------------------------------------------
    -- Invitations: preserve rows; null user refs (inviter nullable after migration)
    -- -------------------------------------------------------------------------
    UPDATE invitations
    SET redeemed_by_user_id = NULL
    WHERE redeemed_by_user_id = p_user_id;

    UPDATE invitations
    SET deactivated_by = NULL
    WHERE deactivated_by = p_user_id;

    UPDATE invitations
    SET inviter_user_id = NULL
    WHERE inviter_user_id = p_user_id;

    -- -------------------------------------------------------------------------
    -- Self-referential users
    -- -------------------------------------------------------------------------
    UPDATE users
    SET referred_by_user_id = NULL
    WHERE referred_by_user_id = p_user_id;

    -- -------------------------------------------------------------------------
    -- Finally remove the user row
    -- -------------------------------------------------------------------------
    DELETE FROM users
    WHERE id = p_user_id;
END;
$$;
