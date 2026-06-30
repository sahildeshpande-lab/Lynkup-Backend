"""Public service-layer compatibility facade."""

import sys
from types import ModuleType

from . import connection_service as _service_module_0
from .connection_service import utc_now, build_connection_pair, is_blocked, are_connected, has_pending_request, send_connection_request, respond_connection_request, get_pending_requests, get_relationship_flags, apply_relationship_flags, DEFAULT_RELATIONSHIP_FLAGS

from . import follow_service as _service_module_1
from .follow_service import follow_user, unfollow_user

from . import block_service as _service_module_2
from .block_service import block_user, unblock_user

from . import recommendation_service as _service_module_3
from .recommendation_service import get_interest_overlap, get_user_connections, get_mutual_connections_count, validate_visibility, calculate_recommendation_score, dismiss_recommendation, get_recommendations

_SERVICE_MODULES = (_service_module_0, _service_module_1, _service_module_2, _service_module_3,)


class _ServicesModule(ModuleType):
    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        for service_module in _SERVICE_MODULES:
            if hasattr(service_module, name):
                setattr(service_module, name, value)


sys.modules[__name__].__class__ = _ServicesModule

__all__ = [
    "utc_now",
    "build_connection_pair",
    "is_blocked",
    "are_connected",
    "has_pending_request",
    "send_connection_request",
    "respond_connection_request",
    "get_pending_requests",
    "get_relationship_flags",
    "apply_relationship_flags",
    "DEFAULT_RELATIONSHIP_FLAGS",
    "follow_user",
    "unfollow_user",
    "block_user",
    "unblock_user",
    "get_interest_overlap",
    "get_user_connections",
    "get_mutual_connections_count",
    "validate_visibility",
    "calculate_recommendation_score",
    "dismiss_recommendation",
    "get_recommendations",
]
