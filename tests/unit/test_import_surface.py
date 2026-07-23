from __future__ import annotations

import importlib


MODULES = [
    "apps.accounts.schemas",
    "apps.administration.schemas",
    "apps.connections.schemas",
    "apps.feed.config",
    "apps.feed.content_utils",
    "apps.feed.schemas",
    "apps.health_check.schemas",
    "apps.moderation.schemas",
    "apps.profiles.schemas",
    "apps.recommendation.config",
    "apps.recommendation.schemas",
    "apps.chat.config",
    "apps.chat.schemas",
    "apps.search.schemas",
    "apps.uploads.schemas",
    "core.auth.config",
    "core.database.config",
    "core.database.models",
    "core.database.session",
    "core.email.config",
    "core.images.config",
    "core.images.storage_service",
    "core.security.admin",
    "core.security.auth",
]


def test_all_apps_and_core_modules_import_without_side_effect_failures():
    imported = [importlib.import_module(module_name).__name__ for module_name in MODULES]

    assert "apps.accounts.schemas" in imported
    assert "apps.feed.content_utils" in imported
    assert "core.database.config" in imported
