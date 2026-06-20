from __future__ import annotations
from typing import Any, Optional
from fastapi import APIRouter, status, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.database.session import get_session
from core.security.auth import get_current_user
from apps.accounts.db_models import User
from common.enums import SocialProvider

from .schemas import (
    # AdminSigninRequest,
    # AdminSignupRequest,
    # AdminEducationRequest,
    ApiResponse,
    UserAuthResponse,
    EmailSignupRequest,
    LogoutRequest,
    RefreshTokenRequest,
    ResendOtpRequest,
    LoginRequest,
    SocialAuthRequest,
    OtpVerifyRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
)
from . import services


router = APIRouter(prefix="/auth", tags=["1] User Registration, Authentication & Onboarding"])


from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import JSONResponse
from apps.accounts.services import AccountExistsException, social_auth as social_auth_service

bearer_scheme = HTTPBearer(
    scheme_name="BearerAuth",
    bearerFormat="JWT",
    description="Send the JWT access token as: Bearer <token>",
    auto_error=False,
)


@router.post( "/social", response_model=UserAuthResponse, status_code=status.HTTP_200_OK,)
async def social_auth(
    payload: SocialAuthRequest,
    db: AsyncSession = Depends(get_session),
) -> Any:
    try:
        data, created = await social_auth_service(payload, db)

        msg = "Signup successful" if created else "Login successful"
        status_code = (
            status.HTTP_201_CREATED
            if created
            else status.HTTP_200_OK
        )

        return JSONResponse(
            status_code=status_code,
            content={
                "status": True,
                "message": msg,
                "data": data,
            },
        )

    except AccountExistsException as exc:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "success": False,
                "error_code": "ACCOUNT_EXISTS",
                "message": (
                    "Account already exists. Please login "
                    "using your registered method."
                ),
                "registration_type": exc.registration_type,
            },
        )


from core.auth.firebase import get_current_firebase_user, get_firebase_user_from_payload

@router.post("/signup", response_model=UserAuthResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    payload: EmailSignupRequest,
    firebase_user: dict = Depends(get_firebase_user_from_payload),
    db: AsyncSession = Depends(get_session),
) -> UserAuthResponse:
    return await services.signup(payload, firebase_user, db)


@router.post("/session", response_model=UserAuthResponse)
async def firebase_session(
    firebase_user: dict = Depends(get_current_firebase_user),
    db: AsyncSession = Depends(get_session),
) -> UserAuthResponse:
    user = await services.complete_firebase_registration(firebase_user, db)
    return UserAuthResponse(
        message="Firebase session verified",
        data=await services.build_firebase_session_response(user, db),
    )


@router.post("/login", response_model=UserAuthResponse)
async def login(
    payload: LoginRequest,
    firebase_user: dict = Depends(get_firebase_user_from_payload),
    db: AsyncSession = Depends(get_session),
) -> UserAuthResponse:
    return await services.login(payload, firebase_user, db)


@router.post("/verify-otp")
async def verify_otp(payload: OtpVerifyRequest, db: AsyncSession = Depends(get_session), firebase_user: dict = Depends(get_firebase_user_from_payload)):
    return await services.verify_otp(payload, firebase_user, db)



@router.post("/resend-otp", response_model=ApiResponse)
async def resend_otp(payload: ResendOtpRequest, db: AsyncSession = Depends(get_session), firebase_user: dict = Depends(get_firebase_user_from_payload)) -> ApiResponse:
    return await services.resend_otp(payload, firebase_user, db)




# @router.post("/logout", response_model=ApiResponse)
# async def logout(
#     payload: LogoutRequest,
#     current_user: User = Depends(get_current_user),
#     db: AsyncSession = Depends(get_session),
# ) -> ApiResponse:
#     return ApiResponse(message="logged out", data=await services.logout(payload, db, current_user))


@router.post("/logout-all", response_model=ApiResponse)
async def logout_all(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    return ApiResponse(message="logged out from all devices", data=await services.logout_all(current_user, db))


@router.get("/me", response_model=ApiResponse)
async def me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> ApiResponse:
    profile = await services._fetch_user_profile(db, current_user)
    return ApiResponse(message="current user fetched", data=services._build_auth_user_response(current_user, profile).model_dump())


@router.post("/forgot-password", response_model=ApiResponse)
async def forgot_password(payload: ForgotPasswordRequest, db: AsyncSession = Depends(get_session), firebase_user: dict = Depends(get_firebase_user_from_payload)) -> ApiResponse:
    if firebase_user.get("email") and firebase_user.get("email").lower() != payload.email.lower():
        raise HTTPException(status_code=400, detail="Firebase token email does not match payload email")
    return await services.forgot_password(payload, db)


@router.post("/reset-password", response_model=ApiResponse)
async def reset_password(payload: ResetPasswordRequest, db: AsyncSession = Depends(get_session), firebase_user: dict = Depends(get_firebase_user_from_payload)) -> ApiResponse:
    return await services.reset_password(payload, db)



# @router.post("/admin/signup", response_model=ApiResponse, status_code=status.HTTP_201_CREATED)
# def admin_signup(payload: AdminSignupRequest) -> ApiResponse:
#     return ApiResponse(message="admin signup processed", data=services.admin_signup(payload))
# 
# 
# @router.post("/admin/education", response_model=ApiResponse, status_code=status.HTTP_201_CREATED)
# def admin_education(payload: AdminEducationRequest) -> ApiResponse:
#     return ApiResponse(message="admin education saved", data=services.admin_education(payload))
# 
# 
# @router.post("/admin/signin", response_model=ApiResponse)
# def admin_signin(payload: AdminSigninRequest) -> ApiResponse:
#     return ApiResponse(message="admin signin processed", data=services.admin_signin(payload))
