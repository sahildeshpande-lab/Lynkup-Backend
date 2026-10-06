from __future__ import annotations

from apps.imports.enums import ImportType

# Conventional master-data cache keys. Catalog GET APIs currently read from the
# database; these keys are invalidated after import so any Redis catalog cache
# stays consistent when it is enabled.
CATALOG_CACHE_KEYS: dict[ImportType, tuple[str, ...]] = {
    ImportType.COUNTRY: ("catalog:countries", "search:countries"),
    ImportType.UNIVERSITY: ("catalog:universities", "search:universities"),
    ImportType.MAJOR: ("catalog:majors", "search:majors"),
    ImportType.MINOR: ("catalog:minors", "search:minors"),
    ImportType.INTEREST: (
        "catalog:interests",
        "search:interests",
        "catalog:academic_interests",
        "catalog:majors",
        "search:majors",
        "catalog:minors",
        "search:minors",
    ),
    ImportType.PROFANITY_WORD: ("moderation:words", "catalog:moderation_words"),
}


async def invalidate_catalog_cache(import_type: ImportType) -> None:
    from core.cache.redis_client import delete_keys

    await delete_keys(CATALOG_CACHE_KEYS.get(import_type, ()))
