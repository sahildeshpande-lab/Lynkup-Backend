"""Public service-layer compatibility facade."""

import sys
from types import ModuleType

from . import media_service as _service_module_0
from .media_service import upload_post_media_service, _verify_and_attach_media

from . import revision_service as _service_module_1
from .revision_service import _build_content_dict, _build_media_snapshot, _create_revision, _sync_hashtags

from . import post_service as _service_module_2
from .post_service import utc_now, format_post_detail, save_post_service, edit_post_service, publish_post_service, admin_publish_post_service, get_post_service, delete_post_service, list_draft_posts_service, delete_draft_post_service, list_user_posts_service, list_processing_posts_service, list_reviewed_posts_service

from . import feed_service as _service_module_3
from .feed_service import get_feed_service

_SERVICE_MODULES = (_service_module_0, _service_module_1, _service_module_2, _service_module_3,)


class _ServicesModule(ModuleType):
    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        for service_module in _SERVICE_MODULES:
            if hasattr(service_module, name):
                setattr(service_module, name, value)


sys.modules[__name__].__class__ = _ServicesModule

__all__ = [
    "upload_post_media_service",
    "_verify_and_attach_media",
    "_build_content_dict",
    "_build_media_snapshot",
    "_create_revision",
    "_sync_hashtags",
    "utc_now",
    "format_post_detail",
    "save_post_service",
    "edit_post_service",
    "publish_post_service",
    "admin_publish_post_service",
    "get_post_service",
    "delete_post_service",
    "list_draft_posts_service",
    "delete_draft_post_service",
    "list_user_posts_service",
    "list_processing_posts_service",
    "list_reviewed_posts_service",
    "get_feed_service",
]
