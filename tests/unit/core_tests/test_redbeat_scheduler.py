from unittest.mock import Mock, patch

import pytest
from redis.exceptions import ConnectionError, LockNotOwnedError, TimeoutError

from core.celery_worker.scheduler import RecoveringRedBeatScheduler


@pytest.fixture
def scheduler():
    # Avoid schedule setup/network I/O; exercise only tick and lock recovery.
    scheduler = object.__new__(RecoveringRedBeatScheduler)
    scheduler.app = Mock()
    scheduler.lock_key = 'redbeat:lock'
    scheduler.lock_timeout = 1500
    scheduler.max_interval = 300
    scheduler.lock = None
    return scheduler


@pytest.mark.parametrize('error', [ConnectionError('closed'), TimeoutError('timeout')])
def test_startup_lock_failure_retries_then_recovers(scheduler, error):
    client = Mock()
    client.lock.return_value.acquire.side_effect = [error, True]
    with (
        patch('core.celery_worker.scheduler.get_redis', return_value=client),
        patch('redbeat.schedulers.RedBeatScheduler.tick', return_value=10) as tick,
    ):
        assert scheduler.tick() == 5
        assert scheduler.lock is None
        tick.assert_not_called()
        assert scheduler.tick() == 10
        assert scheduler.lock is client.lock.return_value
        tick.assert_called_once()
        client.lock.return_value.acquire.assert_called_with(blocking=False)
        assert scheduler.lock.lua_extend is client.register_script.return_value


def test_another_beat_holds_lock_so_no_tasks_are_dispatched(scheduler):
    client = Mock()
    client.lock.return_value.acquire.return_value = False
    with (
        patch('core.celery_worker.scheduler.get_redis', return_value=client),
        patch('redbeat.schedulers.RedBeatScheduler.tick') as tick,
    ):
        assert scheduler.tick() == 5
        assert scheduler.lock is None
        tick.assert_not_called()


def test_existing_lock_uses_normal_tick(scheduler):
    scheduler.lock = Mock()
    with (
        patch('core.celery_worker.scheduler.get_redis') as get_redis,
        patch('redbeat.schedulers.RedBeatScheduler.tick', return_value=12) as tick,
    ):
        assert scheduler.tick() == 12
        get_redis.assert_not_called()
        tick.assert_called_once()


def test_lost_lock_still_stops_scheduling(scheduler):
    scheduler.lock = Mock()
    scheduler.lock.extend.side_effect = LockNotOwnedError('lost lock')
    with pytest.raises(LockNotOwnedError):
        scheduler.tick()
