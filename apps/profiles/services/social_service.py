from __future__ import annotations
from ..schemas import ReportUserRequest

def get_public_profile(email: str) -> dict:
    return {"email": email, "publicProfile": True}

def follow_user(user_id: str) -> dict:
    return {"userId": user_id, "followed": True}

def unfollow_user(user_id: str) -> dict:
    return {"userId": user_id, "unfollowed": True}

def block_user(user_id: str) -> dict:
    return {"userId": user_id, "blocked": True}

def unblock_user(user_id: str) -> dict:
    return {"userId": user_id, "unblocked": True}

def report_user(user_id: str, payload: ReportUserRequest) -> dict:
    return {"userId": user_id, "report": payload.model_dump(exclude_none=True)}

def request_lynkup(user_id: str) -> dict:
    return {"userId": user_id, "requestSent": True}

def accept_lynkup(user_id: str) -> dict:
    return {"userId": user_id, "accepted": True}

def remove_lynkup(user_id: str) -> dict:
    return {"userId": user_id, "removed": True}
