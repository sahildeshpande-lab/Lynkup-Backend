"""Public service-layer compatibility facade."""

import sys
from types import ModuleType

from . import auth_service as _service_module_0
from .auth_service import _generate_admin_tokens, admin_me, admin_token, admin_signin

from . import user_management_service as _service_module_1
from .user_management_service import (
    PASSWORD_HASHER,
    _coerce_uuid,
    _fetch_users_with_details,
    _generate_temporary_password,
    admin_create_user,
    admin_delete_user,
    admin_delete_users,
    admin_edit_profile,
    admin_get_user,
    admin_update_user_status,
    export_users,
    list_moderators,
    list_users,
    list_viewer,
)

from . import password_service as _service_module_2
from .password_service import admin_forgot_password, admin_reset_password, change_password

from . import feature_flag_service as _service_module_3
from .feature_flag_service import (
    create_feature_flag,
    ensure_default_feature_flags,
    is_feature_enabled,
    list_feature_flags,
    update_feature_flag,
)

_SERVICE_MODULES = (
    _service_module_0,
    _service_module_1,
    _service_module_2,
    _service_module_3,
)


class _ServicesModule(ModuleType):
    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        for service_module in _SERVICE_MODULES:
            if hasattr(service_module, name):
                setattr(service_module, name, value)


sys.modules[__name__].__class__ = _ServicesModule

__all__ = [
    "_generate_admin_tokens",
    "admin_me",
    "admin_token",
    "_generate_temporary_password",
    "_coerce_uuid",
    "_fetch_users_with_details",
    "list_users",
    "list_moderators",
    "list_viewer",
    "export_users",
    "admin_create_user",
    "admin_get_user",
    "admin_delete_user",
    "admin_delete_users",
    "admin_edit_profile",
    "admin_update_user_status",
    "admin_forgot_password",
    "admin_reset_password",
    "change_password",
    "PASSWORD_HASHER",
    "list_feature_flags",
    "create_feature_flag",
    "update_feature_flag",
    "ensure_default_feature_flags",
    "is_feature_enabled",
]
