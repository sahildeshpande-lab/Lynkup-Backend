"""Public service-layer compatibility facade."""

import sys
from types import ModuleType

from . import response_service as _service_module_0
from .response_service import _normalize_name_part, _compose_full_name, build_user_base_response

from . import interest_service as _service_module_1
from .interest_service import _resolve_academic_interest_ids

from . import completeness_service as _service_module_2
from .completeness_service import get_completeness_weights, calculate_completeness_score, update_completeness_weights

from . import onboarding_service as _service_module_3
from .onboarding_service import complete_onboarding, update_profile_me_form

from . import profile_service as _service_module_4
from .profile_service import _now, get_profile_me, update_profile_me, delete_user_me, update_profile, update_visibility, _build_user_base, get_me, get_me_completeness, get_my_profile_service, update_my_profile_service, update_profile_visibility_service, update_user_profile_by_admin_service

from . import social_service as _service_module_5
from .social_service import get_public_profile, follow_user, unfollow_user, block_user, unblock_user, report_user, request_lynkup, accept_lynkup, remove_lynkup

from core.images import upload_image_to_s3

from .profile_service import normalize_image_name

_SERVICE_MODULES = (_service_module_0, _service_module_1, _service_module_2, _service_module_3, _service_module_4, _service_module_5,)


class _ServicesModule(ModuleType):
    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        for service_module in _SERVICE_MODULES:
            if hasattr(service_module, name):
                setattr(service_module, name, value)


sys.modules[__name__].__class__ = _ServicesModule

__all__ = [
    "_normalize_name_part",
    "_compose_full_name",
    "build_user_base_response",
    "_resolve_academic_interest_ids",
    "get_completeness_weights",
    "calculate_completeness_score",
    "update_completeness_weights",
    "complete_onboarding",
    "update_profile_me_form",
    "_now",
    "get_profile_me",
    "update_profile_me",
    "delete_user_me",
    "update_profile",
    "update_visibility",
    "_build_user_base",
    "get_me",
    "get_me_completeness",
    "get_my_profile_service",
    "update_my_profile_service",
    "update_profile_visibility_service",
    "update_user_profile_by_admin_service",
    "get_public_profile",
    "follow_user",
    "unfollow_user",
    "block_user",
    "unblock_user",
    "report_user",
    "request_lynkup",
    "accept_lynkup",
    "remove_lynkup",
    "normalize_image_name",
    "upload_image_to_s3",
]
