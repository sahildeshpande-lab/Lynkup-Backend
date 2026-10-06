"""Recover when RedBeat's startup signal fails to acquire its Redis lock."""

import logging

from redis.exceptions import ConnectionError, TimeoutError
from redbeat.schedulers import LUA_EXTEND_TO_SCRIPT, RedBeatScheduler, get_redis

logger = logging.getLogger(__name__)


class RecoveringRedBeatScheduler(RedBeatScheduler):
    lock_retry_interval = 5.0

    def tick(self, **kwargs):
        # Celery swallows exceptions from beat_init. RedBeat can consequently
        # reach its first tick without a lock; never dispatch in that state.
        if self.lock_key and self.lock is None:
            try:
                client = get_redis(self.app)
                lock = client.lock(
                    self.lock_key,
                    timeout=self.lock_timeout,
                    sleep=self.max_interval,
                )
                lock.lua_extend = client.register_script(LUA_EXTEND_TO_SCRIPT)
                if not lock.acquire(blocking=False):
                    logger.info("Beat lock is held; retrying in %s seconds", self.lock_retry_interval)
                    return self.lock_retry_interval
            except (ConnectionError, TimeoutError):
                logger.warning("Beat lock acquisition failed; retrying in %s seconds", self.lock_retry_interval, exc_info=True)
                return self.lock_retry_interval

            self.lock = lock
            logger.info("Beat acquired scheduler lock after startup failure")

        # Preserve RedBeat's ownership checks: do not suppress lock-extension
        # failures or continue publishing after losing an acquired lock.
        return super().tick(**kwargs)
