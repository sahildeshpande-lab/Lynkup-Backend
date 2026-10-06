from __future__ import annotations

from sqlalchemy import Index
from sqlalchemy.sql.schema import CheckConstraint

from apps.invitations.config import INVITATION_CODE_MAX_LENGTH
from apps.invitations.db_models import Invitation
from common.enums import AdminInvitationListStatus, InvitationStatus


def test_invitation_status_enum_values():
    assert InvitationStatus.active.value == "ACTIVE"
    assert InvitationStatus.expired.value == "EXPIRED"
    assert InvitationStatus.redeemed.value == "REDEEMED"
    assert InvitationStatus.deactivated.value == "DEACTIVATED"


def test_admin_invitation_list_status_filter_values():
    assert AdminInvitationListStatus.active.value == "Active"
    assert AdminInvitationListStatus.redeemed.value == "Redeemed"
    assert AdminInvitationListStatus.deleted.value == "Deleted"
    assert AdminInvitationListStatus.expired.value == "Expired"
    assert not hasattr(AdminInvitationListStatus, "deactivated")


def test_invitation_model_table_indexes_and_columns():
    assert Invitation.__tablename__ == "invitations"
    assert Invitation.__table__.c.code.type.length == INVITATION_CODE_MAX_LENGTH

    check_constraints = [
        arg for arg in Invitation.__table_args__ if isinstance(arg, CheckConstraint)
    ]
    assert not any(c.name == "ck_invitations_code_format" for c in check_constraints)

    index_names = {arg.name for arg in Invitation.__table_args__ if isinstance(arg, Index)}
    assert index_names == {
        "ix_invitations_code",
        "ix_invitations_inviter_user_id",
        "ix_invitations_status",
        "ix_invitations_expires_at",
        "ix_invitations_deleted_at",
        "ix_invitations_created_at",
        "ix_invitations_inviter_created_at",
        "ix_invitations_redeemed_by_user_id",
        "ix_invitations_deactivated_by",
    }

    code_index = next(
        arg for arg in Invitation.__table_args__ if isinstance(arg, Index) and arg.name == "ix_invitations_code"
    )
    assert code_index.unique is True


def test_user_invitation_relationship_attributes():
    from apps.accounts.db_models import User

    assert "referred_by_user_id" in User.__table__.columns
    assert hasattr(User, "referred_by")
    assert hasattr(User, "referrals")
    assert hasattr(User, "invitations_created")
    assert hasattr(User, "invitations_redeemed")
    assert hasattr(User, "invitations_deactivated")


def test_user_invitation_mappers_configure_without_name_error():
    from sqlalchemy.orm import configure_mappers

    from apps.accounts.db_models import User
    from apps.invitations.db_models import Invitation

    configure_mappers()

    user_mapper = User.__mapper__
    invitation_mapper = Invitation.__mapper__
    assert "invitations_created" in user_mapper.relationships
    assert "invitations_redeemed" in user_mapper.relationships
    assert "invitations_deactivated" in user_mapper.relationships
    assert invitation_mapper.relationships["inviter"].back_populates == "invitations_created"
    assert invitation_mapper.relationships["redeemed_by"].back_populates == "invitations_redeemed"
    assert invitation_mapper.relationships["deactivator"].back_populates == "invitations_deactivated"
