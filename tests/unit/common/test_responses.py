from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from common.responses import serialize_response, success_response


def test_serialize_response_converts_nested_uuid_and_datetime() -> None:
    university_id = uuid.uuid4()
    created_at = datetime(2026, 7, 13, 12, 0, 0, tzinfo=timezone.utc)
    payload = {
        "user": {
            "id": str(uuid.uuid4()),
            "university_details": {"id": university_id, "university_name": "Test U"},
            "createdAt": created_at,
        },
        "emailSent": False,
        "needsOtp": False,
    }

    content = serialize_response(success_response("Login successful", payload))

    json.dumps(content)
    assert content["status"] is True
    assert content["message"] == "Login successful"
    assert content["data"]["user"]["university_details"]["id"] == str(university_id)
    assert isinstance(content["data"]["user"]["createdAt"], str)
