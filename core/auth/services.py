from firebase_admin import auth

from .firebase_app import initialize_firebase_app


def verify_firebase_token(token: str, check_revoked: bool = False):
    initialize_firebase_app()
    decoded_token = auth.verify_id_token(token, check_revoked=check_revoked)

    return decoded_token


def revoke_firebase_tokens(uid: str):
    """
    Revokes all refresh tokens for a given Firebase user UID.
    This should be called when an account is suspended, banned, deleted, or password reset.
    """
    initialize_firebase_app()
    auth.revoke_refresh_tokens(uid)

def disable_firebase_user(uid:str):
    initialize_firebase_app()
    auth.update_user(uid,disabled=True)
    auth.revoke_refresh_tokens(uid)

def enable_firebase_user(uid:str):
    initialize_firebase_app()
    auth.update_user(uid,disabled=False)

def update_firebase_password(uid:str,password:str):
    initialize_firebase_app()
    auth.update_user(uid,password=password)