from __future__ import annotations
from sqlalchemy.ext.asyncio import AsyncSession
from apps.accounts.db_models import User

async def get_completeness_weights(db: AsyncSession) -> CompletenessWeight:
    from apps.profiles.db_models import CompletenessWeight
    from sqlmodel import select

    stmt = select(CompletenessWeight).where(CompletenessWeight.id == 1)
    weights = (await db.execute(stmt)).scalar_one_or_none()
    if not weights:
        weights = CompletenessWeight()
        db.add(weights)
        await db.commit()
        await db.refresh(weights)
    return weights

async def calculate_completeness_score(user_id, db: AsyncSession) -> int:
    from apps.accounts.db_models import User
    from apps.profiles.db_models import Profile
    from sqlmodel import select
    from uuid import UUID

    user_uuid = UUID(str(user_id)) if not isinstance(user_id, UUID) else user_id

    stmt_user = select(User).where(User.id == user_uuid)
    user = (await db.execute(stmt_user)).scalar_one_or_none()
    if not user:
        return 0

    stmt_profile = select(Profile).where(Profile.user_id == user_uuid)
    profile = (await db.execute(stmt_profile)).scalar_one_or_none()
    if not profile:
        return 0

    weights = await get_completeness_weights(db)
    filled_fields = []

    if profile.bio and profile.bio.strip():
        filled_fields.append(("bio", weights.bio))

    if profile.university_id:
        filled_fields.append(("university", weights.university))

    if profile.major and profile.major.strip():
        filled_fields.append(("major", weights.major))

    if profile.edu_level and profile.edu_level.strip():
        filled_fields.append(("edu_level", weights.edu_level))

    if profile.first_name and profile.first_name.strip():
        filled_fields.append(("first_name", weights.first_name))
    if profile.last_name and profile.last_name.strip():
        filled_fields.append(("last_name", weights.last_name))

    if user.email and user.email.strip():
        filled_fields.append(("email", weights.email))

    if profile.profile_photo_url and profile.profile_photo_url.strip():
        filled_fields.append(("profile_photo_url", weights.profile_photo_url))

    if profile.profile_interests_id and len(profile.profile_interests_id) > 0:
        filled_fields.append(("interests", weights.interests))

    if profile.graduation_date:
        filled_fields.append(("graduation_date", weights.graduation_date))

    if profile.location_text and profile.location_text.strip():
        filled_fields.append(("location", weights.location))

    sum_of_weights = sum(item[1] for item in filled_fields)
    total_weights_sum = (
        weights.bio + weights.university + weights.major + weights.edu_level +
        weights.first_name + weights.last_name + weights.email +
        weights.profile_photo_url + weights.interests + weights.graduation_date +
        weights.location
    )
    if total_weights_sum == 0:
        return 0
    calculated_score = (sum_of_weights * 100) / total_weights_sum
    return min(int(round(calculated_score)), 100)

async def update_completeness_weights(payload, db: AsyncSession) -> dict:
    from apps.profiles.db_models import Profile
    from sqlmodel import select

    weights = await get_completeness_weights(db)
    update_data = payload.model_dump(exclude_unset=True)
    for field, val in update_data.items():
        if val is not None:
            setattr(weights, field, val)
    db.add(weights)
    await db.commit()
    await db.refresh(weights)

    # Recalculate completeness score for all profiles
    stmt = select(Profile)
    profiles = (await db.execute(stmt)).scalars().all()
    for profile in profiles:
        profile.completeness_score = await calculate_completeness_score(profile.user_id, db)
        db.add(profile)
    await db.commit()

    return {
        "message": "Completeness weights updated and all profiles recalculated.",
        "weights": {
            k: getattr(weights, k)
            for k in ["bio", "university", "major", "edu_level", "first_name", "last_name", "email", "profile_photo_url", "interests", "graduation_date", "location"]
        }
    }
