from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import firebase_admin
from firebase_admin import credentials

from .config import settings

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CREDENTIALS_FILE = _REPO_ROOT / "credentials" / "firebase-adminsdk.json"


def initialize_firebase_app() -> None:
    if firebase_admin._apps:
        return

    firebase_json = os.getenv("FIREBASE_CREDENTIALS_JSON")

    if firebase_json:
        data = json.loads(firebase_json)
        logger.info(
            "Initializing Firebase from FIREBASE_CREDENTIALS_JSON project_id=%s service_account=%s",
            data.get("project_id"),
            data.get("client_email"),
        )
        cred = credentials.Certificate(data)
    else:
        credential_path = settings.firebase_credential_path
        if credential_path:
            cred_file = Path(credential_path)
            if not cred_file.is_absolute():
                cred_file = _REPO_ROOT / cred_file
        else:
            cred_file = _DEFAULT_CREDENTIALS_FILE
            logger.info(
                "FIREBASE_CREDENTIALS_JSON and FIREBASE_SERVICE_ACCOUNT_PATH not set; "
                "falling back to %s",
                cred_file,
            )

        if not cred_file.is_file():
            raise RuntimeError(
                "Firebase credentials not found. Set FIREBASE_CREDENTIALS_JSON, "
                "FIREBASE_SERVICE_ACCOUNT_PATH, or place firebase-adminsdk.json in credentials/"
            )

        logger.info("Initializing Firebase from credentials file path=%s", cred_file)
        cred = credentials.Certificate(str(cred_file))

    options = {}
    if settings.firebase_project_id:
        options["projectId"] = settings.firebase_project_id

    firebase_admin.initialize_app(cred, options or None)
