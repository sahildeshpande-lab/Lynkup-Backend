from __future__ import annotations

from pathlib import Path

import firebase_admin
from firebase_admin import credentials

from .config import settings


def initialize_firebase_app() -> None:
    if firebase_admin._apps:
        return

    credential_path = settings.firebase_credential_path
    if not credential_path:
        raise RuntimeError("FIREBASE_CREDENTIALS or FIREBASE_SERVICE_ACCOUNT_PATH must be set")

    cred_file = Path(credential_path)
    if not cred_file.is_absolute():
        cred_file = Path(__file__).resolve().parents[2] / cred_file

    cred = credentials.Certificate(str(cred_file))
    options = {}
    if settings.firebase_project_id:
        options["projectId"] = settings.firebase_project_id
    firebase_admin.initialize_app(cred, options or None)
