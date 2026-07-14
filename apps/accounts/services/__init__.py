"""Public service-layer compatibility facade."""

import sys
from types import ModuleType

from . import common_service as _service_module_0
from .common_service import (
    JWT_ALGORITHM,
    JWT_SECRET,
    _as_aware_utc,
    _build_auth_user_response,
    _display_name_from_firebase,
    _fetch_user_profile,
    _generate_otp,
    _generate_tokens,
    _hash_password,
    _hash_token,
    _log_email_event,
    _now,
    _refresh_token_payload,
    _registration_type_from_firebase,
    _revoke_refresh_token_row,
    _store_refresh_token,
    AccountExistsException,
    assign_user_role,
    get_user_by_firebase_uid,
    log_security_event,
)

from . import auth_service as _service_module_1
from .auth_service import _issue_auth_session, build_firebase_session_response, login, verify_otp, verify_email, resend_otp, _build_user_base

from . import registration_service as _service_module_2
from .registration_service import complete_firebase_registration, social_auth, signup

from . import session_service as _service_module_3
from .session_service import logout, logout_all

from . import password_service as _service_module_4
from .password_service import forgot_password, change_password

from .session_service import revoke_firebase_tokens
from .auth_service import send_otp_email
from .password_service import send_reset_password_email

_SERVICE_MODULES = (_service_module_0, _service_module_1, _service_module_2, _service_module_3, _service_module_4,)


class _ServicesModule(ModuleType):
    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        for service_module in _SERVICE_MODULES:
            if hasattr(service_module, name):
                setattr(service_module, name, value)


sys.modules[__name__].__class__ = _ServicesModule

__all__ = [
    "_now",
    "JWT_SECRET",
    "JWT_ALGORITHM",
    "_fetch_user_profile",
    "_hash_password",
    "_generate_otp",
    "_log_email_event",
    "_generate_tokens",
    "_hash_token",
    "_store_refresh_token",
    "_revoke_refresh_token_row",
    "_refresh_token_payload",
    "_build_auth_user_response",
    "_registration_type_from_firebase",
    "_display_name_from_firebase",
    "_as_aware_utc",
    "log_security_event",
    "AccountExistsException",
    "assign_user_role",
    "get_user_by_firebase_uid",
    "_issue_auth_session",
    "build_firebase_session_response",
    "login",
    "verify_otp",
    "verify_email",
    "resend_otp",
    "_build_user_base",
    "complete_firebase_registration",
    "social_auth",
    "signup",
    "logout",
    "logout_all",
    "forgot_password",
    "change_password",
    "revoke_firebase_tokens",
    "send_otp_email",
    "send_reset_password_email",
]
