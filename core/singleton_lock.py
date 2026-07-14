"""
core/singleton_lock.py — Runtime singleton instance lock (S48).

Prevents duplicate live runner processes from connecting to Bybit.
Records PID, command, start time, and app version in a lock file.
Refuses startup if another live runner is active.

Usage::

    lock = SingletonLock("state/quantluna.lock")
    if not lock.acquire(app_version="0.33.0"):
        print("Another runner is already active. Exiting.")
        sys.exit(1)
    # ... run ...
    lock.release()
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger


@dataclass
class LockInfo:
    pid: int
    command: str
    start_time: float
    app_version: str
    mode: str  # "live" | "paper" | "sync_only"


class SingletonLock:
    """File-based singleton lock for runner processes."""

    def __init__(self, lock_path: str = "state/quantluna.lock") -> None:
        self._path = Path(lock_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._acquired = False

    def acquire(
        self,
        app_version: str = "0.33.0",
        mode: str = "live",
        force: bool = False,
    ) -> bool:
        """
        Try to acquire the singleton lock.

        Returns True if lock acquired (or forced), False if another runner is active.
        """
        if self._path.exists():
            try:
                info = json.loads(self._path.read_text())
                existing_pid = info.get("pid", 0)
                if existing_pid and self._pid_is_alive(existing_pid):
                    if force:
                        logger.warning(
                            "SingletonLock: FORCE TAKEOVER — killing PID {} ({})",
                            existing_pid, info.get("command", "?"),
                        )
                        try:
                            os.kill(existing_pid, 9)
                        except Exception:
                            pass
                        time.sleep(1)
                    else:
                        logger.error(
                            "SingletonLock: RUNNER_ALREADY_ACTIVE — "
                            "PID {} ({}) started at {} v{}",
                            existing_pid,
                            info.get("command", "?"),
                            info.get("start_time", 0),
                            info.get("app_version", "?"),
                        )
                        return False
                else:
                    logger.info("SingletonLock: Stale lock removed (PID {} gone)", existing_pid)
            except (json.JSONDecodeError, KeyError):
                logger.warning("SingletonLock: Corrupt lock file — overwriting")

        # Write fresh lock
        info = LockInfo(
            pid=os.getpid(),
            command=" ".join(sys.argv),
            start_time=time.time(),
            app_version=app_version,
            mode=mode,
        )
        self._path.write_text(json.dumps(info.__dict__, indent=2))
        self._acquired = True
        logger.info(
            "SingletonLock: ACQUIRED — PID {} mode={} v{}",
            info.pid, info.mode, info.app_version,
        )
        return True

    def release(self) -> None:
        """Release the singleton lock."""
        if self._acquired and self._path.exists():
            try:
                self._path.unlink()
                logger.info("SingletonLock: RELEASED")
            except Exception as exc:
                logger.warning("SingletonLock: release failed: {}", exc)
        self._acquired = False

    def get_owner(self) -> Optional[LockInfo]:
        """Return info about the lock owner, or None if no lock."""
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text())
            return LockInfo(**data)
        except Exception:
            return None

    @property
    def is_acquired(self) -> bool:
        return self._acquired

    @staticmethod
    def _pid_is_alive(pid: int) -> bool:
        """Check if a process with given PID exists."""
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
