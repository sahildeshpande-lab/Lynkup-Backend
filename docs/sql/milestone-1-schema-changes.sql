-- Milestone 1 schema change summary
-- Source of truth: alembic/versions/9b5c1d7a4f2e_add_is_active_to_user_installations.py

ALTER TABLE user_installations
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

COMMENT ON COLUMN user_installations.is_active IS
    'Marks whether the installation is currently active for the user session.';
