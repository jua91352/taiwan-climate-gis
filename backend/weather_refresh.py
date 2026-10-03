"""Keep SQLite's O-A0003-001 observations fresh when the API reads them.

Freshness is judged from the newest observation_time stored in SQLite (CWA's
own +08:00 timestamps), never from server start time. When it is
FRESHNESS_THRESHOLD old or older, the existing ingest() fetches CWA and appends
the new batch (history is kept; duplicates are skipped). One lock makes
concurrent requests share a single refresh. If CWA fails, the stored data is
served unchanged and flagged as stale. CWA publishes each batch a few minutes
after its observation time, so "10+ minutes old but CWA has nothing newer"
is normal: it is not stale, and CWA is asked again after RETRY_COOLDOWN.
"""
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend import cwa_api, db

FRESHNESS_THRESHOLD = timedelta(minutes=10)  # CWA updates O-A0003-001 about every 10 minutes
# After an attempt that did not produce fresh data (CWA error, or CWA has not
# published the next batch yet), wait this long before asking CWA again.
RETRY_COOLDOWN = timedelta(minutes=2)


@dataclass(frozen=True)
class RefreshStatus:
    updated: bool  # this request stored a new batch from CWA
    stale: bool  # >= FRESHNESS_THRESHOLD old because the last CWA refresh failed
    error: str | None  # why the last CWA refresh failed (never contains the API key)
    latest_observation_time: str | None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def is_fresh(latest_observation_time: str | None, now: datetime) -> bool:
    if latest_observation_time is None:
        return False
    # Stored times carry their +08:00 offset, so this is an aware-vs-aware comparison.
    return now - datetime.fromisoformat(latest_observation_time) < FRESHNESS_THRESHOLD


_lock = threading.Lock()
_last_attempt: dict[Path, datetime] = {}
_last_error: dict[Path, str | None] = {}


def ensure_fresh(db_path: Path = db.DB_PATH, now: Callable[[], datetime] = utc_now) -> RefreshStatus:
    """Refresh SQLite from CWA if its newest observation is too old; never raises for CWA errors."""
    latest = db.get_latest_observation_time(db_path)
    if is_fresh(latest, now()):
        return RefreshStatus(updated=False, stale=False, error=None, latest_observation_time=latest)

    with _lock:
        # Another request may have refreshed while this one waited for the lock.
        latest = db.get_latest_observation_time(db_path)
        if is_fresh(latest, now()):
            return RefreshStatus(updated=False, stale=False, error=None, latest_observation_time=latest)

        last = _last_attempt.get(db_path)
        if last is not None and now() - last < RETRY_COOLDOWN:
            error = _last_error.get(db_path)
            return RefreshStatus(updated=False, stale=error is not None, error=error, latest_observation_time=latest)

        _last_attempt[db_path] = now()
        updated, error = False, None
        try:
            stats = cwa_api.ingest(db_path)
            updated = stats["observations_inserted"] > 0
        except cwa_api.CWAError as e:
            error = str(e)
        except sqlite3.Error as e:  # save_observations rolled back; stored data is intact
            error = f"SQLite error while saving CWA data: {type(e).__name__}"
        _last_error[db_path] = error

        latest = db.get_latest_observation_time(db_path)
        return RefreshStatus(
            updated=updated,
            stale=error is not None and not is_fresh(latest, now()),
            error=error,
            latest_observation_time=latest,
        )
