from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from apps.learningspotlight.services.cycle_service import get_cycle_day, get_spotlight_type
from apps.learningspotlight.services.daily_generation_service import (
    DailyGenerationResult,
    LearningSpotlightDailyGenerationService,
    NotImplementedSpotlightPaperGenerator,
    STANDARD_USER_ROLE_ID,
    _active_standard_user_profile_stmt,
    has_spotlight_for_cycle_day,
)
from common.enums import SpotlightType


START = date(2026, 8, 23)


@pytest.mark.parametrize(
    ("offset", "expected_day", "expected_type"),
    [
        (0, 1, SpotlightType.leading_thinker),
        (1, 2, SpotlightType.country_perspective),
        (4, 5, SpotlightType.beyond_your_field),
        (5, 1, SpotlightType.leading_thinker),
    ],
)
def test_global_cycle_day_calculations(offset, expected_day, expected_type) -> None:
    today = START + timedelta(days=offset)
    assert get_cycle_day(START, today) == expected_day
    assert get_spotlight_type(expected_day) is expected_type


def test_new_and_existing_users_share_day_3_category() -> None:
    """Day 3 is global — both 'existing' and 'new' users use the same category."""
    today = START + timedelta(days=2)
    assert get_cycle_day(START, today) == 3
    assert get_spotlight_type(3) is SpotlightType.influential_research


def test_default_user_timeout_allows_semantic_scholar_retries() -> None:
    from apps.learningspotlight.config import LearningSpotlightSettings

    cfg = LearningSpotlightSettings()
    assert cfg.learning_spotlight_user_timeout_seconds == 180.0
    assert cfg.learning_spotlight_batch_size == 50
    assert (
        LearningSpotlightSettings.model_fields["learning_spotlight_candidate_limit"].default
        == 50
    )


def test_has_spotlight_for_cycle_day_idempotency() -> None:
    today = date(2026, 8, 24)
    snapshot = {
        "version": 2,
        "cycle_day": 2,
        "generated_at": "2026-08-24T10:00:00+00:00",
    }
    assert has_spotlight_for_cycle_day(snapshot, cycle_day=2, today=today) is True
    assert has_spotlight_for_cycle_day(snapshot, cycle_day=3, today=today) is False
    assert (
        has_spotlight_for_cycle_day(
            {**snapshot, "generated_at": "2026-08-23T10:00:00+00:00"},
            cycle_day=2,
            today=today,
        )
        is False
    )

    typed = {**snapshot, "spotlight_type": "country_perspective"}
    assert (
        has_spotlight_for_cycle_day(
            typed,
            cycle_day=2,
            today=today,
            spotlight_type=SpotlightType.country_perspective,
        )
        is True
    )
    assert (
        has_spotlight_for_cycle_day(
            typed,
            cycle_day=2,
            today=today,
            spotlight_type=SpotlightType.leading_thinker,
        )
        is False
    )
    assert (
        has_spotlight_for_cycle_day(
            snapshot,
            cycle_day=2,
            today=today,
            spotlight_type=SpotlightType.country_perspective,
        )
        is False
    )


class _FakeSettings:
    def __init__(
        self,
        *,
        is_enabled: bool = True,
        cycle_start_date: date | None = START,
        cycle_configuration: dict | None = None,
    ) -> None:
        self.is_enabled = is_enabled
        self.cycle_start_date = cycle_start_date
        self.cycle_configuration = cycle_configuration


class _FakeSettingsService:
    def __init__(self, settings: _FakeSettings | None) -> None:
        self._settings = settings

    async def get_persisted_settings(self, session):
        return self._settings


class _RecordingGenerator:
    def __init__(self, *, fail_for: set | None = None) -> None:
        self.calls: list[tuple] = []
        self.fail_for = fail_for or set()

    async def generate_for_user(self, session, user_id, *, spotlight_type, cycle_day, today, **kwargs):
        self.calls.append((user_id, spotlight_type, cycle_day, today))
        if user_id in self.fail_for:
            raise RuntimeError(f"boom for {user_id}")
        return True


def _candidate(
    *,
    keywords: dict | None = None,
    spotlight: dict | None = None,
    learning_spotlight_updated_at: datetime | None = None,
    keywords_updated_at: datetime | None = None,
    user_id=None,
):
    return SimpleNamespace(
        user_id=user_id or uuid4(),
        extracted_keywords=keywords if keywords is not None else {"major": ["AI"]},
        learning_spotlight=spotlight,
        learning_spotlight_updated_at=learning_spotlight_updated_at,
        keywords_updated_at=keywords_updated_at,
    )


@pytest.mark.asyncio
async def test_semantic_scholar_external_failure_counts_as_failed_not_generated() -> None:
    """429/5xx must not mark the user as successfully generated for today."""
    from apps.learningspotlight.services.semantic_scholar_adapter import (
        SemanticScholarExternalError,
    )

    user_id = uuid4()

    class _FailingGenerator:
        calls = 0

        async def generate_for_user(self, session, uid, **kwargs):
            self.calls += 1
            raise SemanticScholarExternalError(
                "Semantic Scholar rate limit exceeded",
                status_code=429,
                retryable=True,
            )

    generator = _FailingGenerator()
    service = LearningSpotlightDailyGenerationService(
        settings_service=_FakeSettingsService(_FakeSettings()),
        paper_generator=generator,
    )

    async def fake_candidates(session):
        # Eligible user with no today's spotlight snapshot.
        return [_candidate(user_id=user_id, spotlight=None)]

    service._get_active_profile_candidates = fake_candidates  # type: ignore[method-assign]

    with patch(
        "apps.learningspotlight.services.daily_generation_service.async_session_factory"
    ) as factory:
        factory.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        factory.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await service._run_daily_generation(today=START)

    assert result.ran is True
    assert generator.calls == 1
    assert result.failed_users == 1
    assert result.generated_users == 0
    assert result.candidates_not_found == 0


@pytest.mark.asyncio
async def test_disabled_settings_skips_run() -> None:
    service = LearningSpotlightDailyGenerationService(
        settings_service=_FakeSettingsService(_FakeSettings(is_enabled=False)),
        paper_generator=_RecordingGenerator(),
    )
    with patch(
        "apps.learningspotlight.services.daily_generation_service.async_session_factory"
    ) as factory:
        factory.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        factory.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await service._run_daily_generation(today=START)

    assert result.ran is False
    assert "disabled" in (result.reason or "")


@pytest.mark.asyncio
async def test_enabled_null_cycle_start_safe_stop() -> None:
    service = LearningSpotlightDailyGenerationService(
        settings_service=_FakeSettingsService(
            _FakeSettings(is_enabled=True, cycle_start_date=None)
        ),
        paper_generator=_RecordingGenerator(),
    )
    with patch(
        "apps.learningspotlight.services.daily_generation_service.async_session_factory"
    ) as factory:
        factory.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        factory.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await service._run_daily_generation(today=START)

    assert result.ran is False
    assert "cycle_start_date" in (result.reason or "")


@pytest.mark.asyncio
async def test_skips_users_without_searchable_keywords() -> None:
    generator = _RecordingGenerator()
    user_id = uuid4()
    service = LearningSpotlightDailyGenerationService(
        settings_service=_FakeSettingsService(_FakeSettings()),
        paper_generator=generator,
    )

    async def fake_candidates(session):
        return [
            _candidate(user_id=user_id, keywords={}),
        ]

    service._get_active_profile_candidates = fake_candidates  # type: ignore[method-assign]

    with patch(
        "apps.learningspotlight.services.daily_generation_service.async_session_factory"
    ) as factory:
        factory.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        factory.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await service._run_daily_generation(today=START)

    assert result.ran is True
    assert result.skipped_users == 1
    assert result.skip_reasons.get("no searchable keywords") == 1
    assert generator.calls == []


@pytest.mark.asyncio
async def test_skips_user_already_generated_today_and_is_idempotent() -> None:
    generator = _RecordingGenerator()
    user_id = uuid4()
    today = START + timedelta(days=1)  # day 2
    existing = {
        "version": 2,
        "cycle_day": 2,
        "spotlight_type": "country_perspective",
        "generated_at": datetime(2026, 8, 24, 8, 0, tzinfo=timezone.utc).isoformat(),
    }
    service = LearningSpotlightDailyGenerationService(
        settings_service=_FakeSettingsService(_FakeSettings()),
        paper_generator=generator,
    )

    async def fake_candidates(session):
        return [
            _candidate(
                user_id=user_id,
                spotlight=existing,
                learning_spotlight_updated_at=datetime(
                    2026, 8, 24, 8, 0, tzinfo=timezone.utc
                ),
            )
        ]

    service._get_active_profile_candidates = fake_candidates  # type: ignore[method-assign]

    with patch(
        "apps.learningspotlight.services.daily_generation_service.async_session_factory"
    ) as factory:
        factory.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        factory.return_value.__aexit__ = AsyncMock(return_value=None)
        first = await service._run_daily_generation(today=today)
        second = await service._run_daily_generation(today=today)

    assert first.ran is True and second.ran is True
    assert first.skipped_users == 1 and second.skipped_users == 1
    assert first.skip_reasons.get("already generated for today") == 1
    assert generator.calls == []


@pytest.mark.asyncio
async def test_regenerates_when_cycle_type_changes_same_day() -> None:
    """Admin remapped today's cycle type: runcron must generate again, not skip."""
    generator = _RecordingGenerator()
    user_id = uuid4()
    today = START + timedelta(days=1)  # day 2
    existing = {
        "version": 2,
        "cycle_day": 2,
        "spotlight_type": "country_perspective",
        "generated_at": datetime(2026, 8, 24, 8, 0, tzinfo=timezone.utc).isoformat(),
    }
    remapped_cycle = {
        "cycle": [
            SpotlightType.country_perspective.value,
            SpotlightType.leading_thinker.value,
            SpotlightType.influential_research.value,
            SpotlightType.latest_research.value,
            SpotlightType.beyond_your_field.value,
        ]
    }
    service = LearningSpotlightDailyGenerationService(
        settings_service=_FakeSettingsService(
            _FakeSettings(cycle_configuration=remapped_cycle)
        ),
        paper_generator=generator,
    )

    async def fake_candidates(session):
        return [_candidate(user_id=user_id, spotlight=existing)]

    service._get_active_profile_candidates = fake_candidates  # type: ignore[method-assign]

    with patch(
        "apps.learningspotlight.services.daily_generation_service.async_session_factory"
    ) as factory:
        factory.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        factory.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await service._run_daily_generation(today=today)

    assert result.ran is True
    assert result.skipped_users == 0
    assert result.generated_users == 1
    assert generator.calls == [
        (user_id, SpotlightType.leading_thinker, 2, today),
    ]


@pytest.mark.asyncio
async def test_one_user_failure_does_not_stop_others() -> None:
    ok_id = uuid4()
    bad_id = uuid4()
    generator = _RecordingGenerator(fail_for={bad_id})
    service = LearningSpotlightDailyGenerationService(
        settings_service=_FakeSettingsService(_FakeSettings()),
        paper_generator=generator,
    )

    async def fake_candidates(session):
        return [
            _candidate(user_id=bad_id),
            _candidate(user_id=ok_id),
        ]

    service._get_active_profile_candidates = fake_candidates  # type: ignore[method-assign]

    with patch(
        "apps.learningspotlight.services.daily_generation_service.async_session_factory"
    ) as factory:
        factory.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        factory.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await service._run_daily_generation(today=START)

    assert result.failed_users == 1
    assert result.generated_users == 1
    assert result.eligible_users == 2
    assert len(generator.calls) == 2
    assert {call[0] for call in generator.calls} == {ok_id, bad_id}
    assert all(call[2] == 1 for call in generator.calls)
    assert all(call[1] is SpotlightType.leading_thinker for call in generator.calls)


@pytest.mark.asyncio
async def test_notifies_user_when_spotlight_is_generated() -> None:
    user_id = uuid4()
    generator = _RecordingGenerator()
    service = LearningSpotlightDailyGenerationService(
        settings_service=_FakeSettingsService(_FakeSettings()),
        paper_generator=generator,
    )

    async def fake_candidates(session):
        return [_candidate(user_id=user_id)]

    service._get_active_profile_candidates = fake_candidates  # type: ignore[method-assign]

    with (
        patch(
            "apps.learningspotlight.services.daily_generation_service.async_session_factory"
        ) as factory,
        patch(
            "apps.learningspotlight.services.spotlight_notification_service.notify_learning_spotlight_recommended_best_effort",
            new=AsyncMock(),
        ) as notify,
    ):
        factory.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        factory.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await service._run_daily_generation(today=START)

    assert result.generated_users == 1
    notify.assert_awaited_once_with(
        user_id,
        spotlight_type=SpotlightType.leading_thinker,
        cycle_day=1,
    )


@pytest.mark.asyncio
async def test_does_not_notify_when_generation_returns_false() -> None:
    user_id = uuid4()

    class _NoOpGenerator:
        async def generate_for_user(self, session, user_id, *, spotlight_type, cycle_day, today, **kwargs):
            return False

    service = LearningSpotlightDailyGenerationService(
        settings_service=_FakeSettingsService(_FakeSettings()),
        paper_generator=_NoOpGenerator(),
    )

    async def fake_candidates(session):
        return [_candidate(user_id=user_id)]

    service._get_active_profile_candidates = fake_candidates  # type: ignore[method-assign]

    with (
        patch(
            "apps.learningspotlight.services.daily_generation_service.async_session_factory"
        ) as factory,
        patch(
            "apps.learningspotlight.services.spotlight_notification_service.notify_learning_spotlight_recommended_best_effort",
            new=AsyncMock(),
        ) as notify,
    ):
        factory.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        factory.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await service._run_daily_generation(today=START)

    assert result.generated_users == 0
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_day_3_run_passes_influential_research_to_generator() -> None:
    user_id = uuid4()
    generator = _RecordingGenerator()
    service = LearningSpotlightDailyGenerationService(
        settings_service=_FakeSettingsService(_FakeSettings()),
        paper_generator=generator,
    )

    async def fake_candidates(session):
        return [_candidate(user_id=user_id)]

    service._get_active_profile_candidates = fake_candidates  # type: ignore[method-assign]
    today = START + timedelta(days=2)

    with patch(
        "apps.learningspotlight.services.daily_generation_service.async_session_factory"
    ) as factory:
        factory.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        factory.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await service._run_daily_generation(today=today)

    assert result.cycle_day == 3
    assert result.spotlight_type is SpotlightType.influential_research
    assert generator.calls[0][1] is SpotlightType.influential_research
    assert generator.calls[0][2] == 3


def test_active_users_query_requires_standard_user_role() -> None:
    compiled = str(
        _active_standard_user_profile_stmt().compile(compile_kwargs={"literal_binds": True})
    ).lower()
    assert "user_roles" in compiled
    assert "role_id" in compiled
    assert STANDARD_USER_ROLE_ID.hex in compiled.replace("-", "")
    assert "users.status" in compiled
    assert "'active'" in compiled


@pytest.mark.asyncio
async def test_placeholder_generator_raises() -> None:
    gen = NotImplementedSpotlightPaperGenerator()
    with pytest.raises(NotImplementedError):
        await gen.generate_for_user(
            MagicMock(),
            uuid4(),
            spotlight_type=SpotlightType.leading_thinker,
            cycle_day=1,
            today=START,
        )


@pytest.mark.asyncio
async def test_slow_user_still_generates_and_batch_continues() -> None:
    """Completed generation is persisted; a slow user must not be discarded."""
    slow_id = uuid4()
    ok_id = uuid4()

    class _SlowThenOk:
        def __init__(self) -> None:
            self.calls: list = []

        async def generate_for_user(
            self, session, user_id, *, spotlight_type, cycle_day, today, **kwargs
        ):
            self.calls.append(user_id)
            if user_id == slow_id:
                await asyncio.sleep(0.05)
            return True

    generator = _SlowThenOk()
    service = LearningSpotlightDailyGenerationService(
        settings_service=_FakeSettingsService(_FakeSettings()),
        paper_generator=generator,
    )

    async def fake_candidates(session):
        return [
            _candidate(user_id=slow_id),
            _candidate(user_id=ok_id),
        ]

    service._get_active_profile_candidates = fake_candidates  # type: ignore[method-assign]

    with patch(
        "apps.learningspotlight.services.daily_generation_service.async_session_factory"
    ) as factory:
        factory.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        factory.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await service._run_daily_generation(today=START)

    assert result.failed_users == 0
    assert result.generated_users == 2
    assert slow_id in generator.calls
    assert ok_id in generator.calls

