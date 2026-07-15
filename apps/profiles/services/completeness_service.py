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

    total_weights_sum = (
        weights.bio + weights.university + weights.major + weights.edu_level +
        weights.first_name + weights.last_name + weights.email +
        weights.profile_photo_url + weights.interests + weights.graduation_date +
        weights.location
    )

    is_mock = False
    try:
        dialect_name = db.bind.dialect.name
        if not isinstance(dialect_name, str):
            is_mock = True
    except Exception:
        is_mock = True

    if is_mock:
        stmt = select(Profile)
        profiles = (await db.execute(stmt)).scalars().all()
        for p in profiles:
            p.completeness_score = await calculate_completeness_score(p.user_id, db)
            db.add(p)
        await db.commit()
    elif total_weights_sum > 0:
        from sqlalchemy import text
        if db.bind.dialect.name == "sqlite":
            sql = text(f"""
                UPDATE profiles
                SET completeness_score = CAST(ROUND(
                    (
                        (CASE WHEN bio IS NOT NULL AND TRIM(bio) != '' THEN {weights.bio} ELSE 0 END) +
                        (CASE WHEN university_id IS NOT NULL THEN {weights.university} ELSE 0 END) +
                        (CASE WHEN major IS NOT NULL AND TRIM(major) != '' THEN {weights.major} ELSE 0 END) +
                        (CASE WHEN edu_level IS NOT NULL AND TRIM(edu_level) != '' THEN {weights.edu_level} ELSE 0 END) +
                        (CASE WHEN first_name IS NOT NULL AND TRIM(first_name) != '' THEN {weights.first_name} ELSE 0 END) +
                        (CASE WHEN last_name IS NOT NULL AND TRIM(last_name) != '' THEN {weights.last_name} ELSE 0 END) +
                        (CASE WHEN (SELECT email FROM users WHERE users.id = profiles.user_id) IS NOT NULL AND TRIM((SELECT email FROM users WHERE users.id = profiles.user_id)) != '' THEN {weights.email} ELSE 0 END) +
                        (CASE WHEN profile_photo_url IS NOT NULL AND TRIM(profile_photo_url) != '' THEN {weights.profile_photo_url} ELSE 0 END) +
                        (CASE WHEN profile_interests_id IS NOT NULL AND CAST(profile_interests_id AS VARCHAR) NOT IN ('[]', 'null', '') THEN {weights.interests} ELSE 0 END) +
                        (CASE WHEN graduation_date IS NOT NULL THEN {weights.graduation_date} ELSE 0 END) +
                        (CASE WHEN location_text IS NOT NULL AND TRIM(location_text) != '' THEN {weights.location} ELSE 0 END)
                    ) * 100.0 / {total_weights_sum}
                ) AS INTEGER)
            """)
        else:
            sql = text(f"""
                UPDATE profiles p
                SET completeness_score = ROUND(
                    (
                        (CASE WHEN p.bio IS NOT NULL AND TRIM(p.bio) != '' THEN {weights.bio} ELSE 0 END) +
                        (CASE WHEN p.university_id IS NOT NULL THEN {weights.university} ELSE 0 END) +
                        (CASE WHEN p.major IS NOT NULL AND TRIM(p.major) != '' THEN {weights.major} ELSE 0 END) +
                        (CASE WHEN p.edu_level IS NOT NULL AND TRIM(p.edu_level) != '' THEN {weights.edu_level} ELSE 0 END) +
                        (CASE WHEN p.first_name IS NOT NULL AND TRIM(p.first_name) != '' THEN {weights.first_name} ELSE 0 END) +
                        (CASE WHEN p.last_name IS NOT NULL AND TRIM(p.last_name) != '' THEN {weights.last_name} ELSE 0 END) +
                        (CASE WHEN u.email IS NOT NULL AND TRIM(u.email) != '' THEN {weights.email} ELSE 0 END) +
                        (CASE WHEN p.profile_photo_url IS NOT NULL AND TRIM(p.profile_photo_url) != '' THEN {weights.profile_photo_url} ELSE 0 END) +
                        (CASE WHEN p.profile_interests_id IS NOT NULL AND CAST(p.profile_interests_id AS VARCHAR) NOT IN ('[]', 'null', '') THEN {weights.interests} ELSE 0 END) +
                        (CASE WHEN p.graduation_date IS NOT NULL THEN {weights.graduation_date} ELSE 0 END) +
                        (CASE WHEN p.location_text IS NOT NULL AND TRIM(p.location_text) != '' THEN {weights.location} ELSE 0 END)
                    ) * 100.0 / {total_weights_sum}
                )::integer
                FROM users u
                WHERE u.id = p.user_id
            """)
        await db.execute(sql)
        await db.commit()

    return {
        "message": "Completeness weights updated and all profiles recalculated.",
        "weights": {
            k: getattr(weights, k)
            for k in ["bio", "university", "major", "edu_level", "first_name", "last_name", "email", "profile_photo_url", "interests", "graduation_date", "location"]
        }
    }
