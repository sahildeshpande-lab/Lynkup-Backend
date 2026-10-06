from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from apps.accounts.db_models import ConsentRecord
from apps.accounts.db_models.consent_record_db_model import utc_now

logger = logging.getLogger(__name__)

CONSENT_SOURCE_SIGNUP = "signup"
CONSENT_SOURCE_ADMIN_CREATION = "admin_creation"
CONSENT_TYPE_TERMS_AND_CONDITIONS = "terms_and_conditions"


async def save_current_consent(
    db: AsyncSession,
    user_id: UUID,
    *,
    source: str,
) -> ConsentRecord:
    """Persist Terms and Conditions consent for a local PostgreSQL user.

    Inserts one ``consent_records`` row:

    * ``consent_type`` = ``terms_and_conditions``
    * ``granted`` = ``True``
    * ``ip_address`` = ``NULL``
    * ``consented_at`` = current UTC timestamp

    Does not commit; the caller owns the transaction so user creation and
    consent can be committed together.
    """
    if user_id is None:
        raise ValueError("user_id is required to store consent")

    record = ConsentRecord(
        user_id=user_id,
        consent_type=CONSENT_TYPE_TERMS_AND_CONDITIONS,
        granted=True,
        ip_address=None,
        consented_at=utc_now(),
    )
    logger.info(
        "Storing consent user_id=%s source=%s consent_type=%s",
        user_id,
        source,
        CONSENT_TYPE_TERMS_AND_CONDITIONS,
    )
    db.add(record)
    try:
        await db.flush()
    except Exception:
        logger.exception(
            "PostgreSQL consent write failed user_id=%s source=%s",
            user_id,
            source,
        )
        raise
    logger.info(
        "Stored consent user_id=%s source=%s consent_id=%s",
        user_id,
        source,
        record.id,
    )
    return record
