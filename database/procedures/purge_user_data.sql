CREATE OR REPLACE PROCEDURE purge_user_data(
    p_user_id UUID
)
LANGUAGE plpgsql
AS $$
BEGIN

    -- Temporary ID sets
    CREATE TEMP TABLE tmp_post_ids ON COMMIT DROP AS
    SELECT id
    FROM posts
    WHERE author_user_id = p_user_id;

    CREATE TEMP TABLE tmp_media_asset_ids ON COMMIT DROP AS
    SELECT id
    FROM media_assets
    WHERE owner_user_id = p_user_id;

    -- User comments + descendants
    CREATE TEMP TABLE tmp_comment_ids ON COMMIT DROP AS
    WITH RECURSIVE comment_tree AS (
        SELECT c.id, c.level
        FROM comments c
        WHERE c.user_id = p_user_id

        UNION

        SELECT c.id, c.level
        FROM comments c
        INNER JOIN comment_tree ct
            ON c.parent_comment_id = ct.id
    )
    SELECT id
    FROM comment_tree

    UNION

    SELECT c.id
    FROM comments c
    WHERE c.post_id IN (
        SELECT id FROM tmp_post_ids
    );

    -- Social graph
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

    -- Engagements
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

    -- -------------------------------------------------------------------------
    -- Reports: remove queue rows for this user's content before it is deleted.
    -- reported_id is the reporter (who filed the report), not the target.
    -- -------------------------------------------------------------------------
    DELETE FROM reports
    WHERE entity_type = 'user'::reportentitytype
      AND entity_id = p_user_id;

    DELETE FROM reports
    WHERE entity_type = 'post'::reportentitytype
      AND entity_id IN (
            SELECT id FROM tmp_post_ids
       );

    DELETE FROM reports
    WHERE entity_type = 'comment'::reportentitytype
      AND entity_id IN (
            SELECT id FROM tmp_comment_ids
       );

    DELETE FROM reports
    WHERE reported_id = p_user_id;

    UPDATE reports
    SET post_revision_id = NULL
    WHERE post_revision_id IN (
        SELECT id
        FROM post_revisions
        WHERE post_id IN (
            SELECT id FROM tmp_post_ids
        )
           OR editor_user_id = p_user_id
    );

    UPDATE reports
    SET moderator_id = NULL
    WHERE moderator_id = p_user_id;

    -- -------------------------------------------------------------------------
    -- Comments: deepest levels first (NO ACTION parent FK)
    -- -------------------------------------------------------------------------
    DELETE FROM comments
    WHERE id IN (SELECT id FROM tmp_comment_ids)
      AND level = 3;

    DELETE FROM comments
    WHERE id IN (SELECT id FROM tmp_comment_ids)
      AND level = 2;

    DELETE FROM comments
    WHERE id IN (SELECT id FROM tmp_comment_ids)
      AND level = 1;

    DELETE FROM comments
    WHERE user_id = p_user_id;

    -- Post dependencies
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
    WHERE id IN (
        SELECT id FROM tmp_media_asset_ids
    );

    -- -------------------------------------------------------------------------
    -- Moderation assignment references
    -- -------------------------------------------------------------------------
    UPDATE moderation_assignment_state
    SET last_assigned_moderator_id = NULL
    WHERE last_assigned_moderator_id = p_user_id;

    -- Analytics
    DELETE FROM user_activity_logs
    WHERE user_id = p_user_id;

    -- Notifications
    DELETE FROM notifications
    WHERE recipient_user_id = p_user_id;

    DELETE FROM notification_preferences
    WHERE user_id = p_user_id;

    DELETE FROM notification_campaign_audience
    WHERE user_id = p_user_id;

    UPDATE notification_campaigns
    SET created_by_admin_id = NULL
    WHERE created_by_admin_id = p_user_id;

    -- Learning recommendations
    UPDATE learning_recommendation_settings
    SET updated_by = NULL
    WHERE updated_by = p_user_id;

    UPDATE learning_recommendation_settings_logs
    SET updated_by = NULL
    WHERE updated_by = p_user_id;

    DELETE FROM learning_recommendation_logs
    WHERE user_id = p_user_id;

    -- Profile
    DELETE FROM profiles
    WHERE user_id = p_user_id;

    -- Security / devices / roles / consent
    DELETE FROM refresh_tokens
    WHERE user_id = p_user_id;

    DELETE FROM password_reset_tokens
    WHERE user_id = p_user_id;

    DELETE FROM security_events
    WHERE user_id = p_user_id;

    DELETE FROM admin_signing_keys
    WHERE user_id = p_user_id;

    DELETE FROM admin_sessions
    WHERE user_id = p_user_id;

    DELETE FROM user_installations
    WHERE user_id = p_user_id;

    DELETE FROM consent_records
    WHERE user_id = p_user_id;

    DELETE FROM user_roles
    WHERE user_id = p_user_id;

    -- Invitations: preserve records, remove user references
    UPDATE invitations
    SET redeemed_by_user_id = NULL
    WHERE redeemed_by_user_id = p_user_id;

    UPDATE invitations
    SET deactivated_by = NULL
    WHERE deactivated_by = p_user_id;

    UPDATE invitations
    SET inviter_user_id = NULL
    WHERE inviter_user_id = p_user_id;

    -- Self-reference
    UPDATE users
    SET referred_by_user_id = NULL
    WHERE referred_by_user_id = p_user_id;

    -- Finally delete user
    DELETE FROM users
    WHERE id = p_user_id;

END;
$$;