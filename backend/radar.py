"""CWA O-A0058-005 radar composite: collect, reproject and keep 2 hours of frames.

Only the fixed real-time objects are used: the PNG at PNG_URL and its sibling
metadata JSON at METADATA_URL, which carries the observation time
(dataset.DateTime). CWA replaces both every 10 minutes, and the JSON can
change slightly before or after the PNG. A frame is only stored when the
metadata did not change while the PNG downloaded and both objects carry
(nearly) the same S3 Last-Modified time, so an image is never stored under
another frame's timestamp.

Each frame is reprojected to Web Mercator (backend.radar_png) and written to
data/radar/; SQLite holds only its metadata (RadarFrame). Frames older than
RETENTION before the newest one are deleted after each new frame. A failed
download changes nothing on disk or in SQLite. One lock serialises the
startup run, the 10-minute background collector and request-triggered runs.
"""
import logging
import os
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

from backend import db, radar_png

log = logging.getLogger(__name__)

SOURCE = "O-A0058-005"
PNG_URL = "https://cwaopendata.s3.ap-northeast-1.amazonaws.com/Observation/O-A0058-005.png"
METADATA_URL = "https://cwaopendata.s3.ap-northeast-1.amazonaws.com/Observation/O-A0058-005.json"
REQUEST_TIMEOUT = 30  # seconds

# Plate Carrée extent of the source image, as published in the metadata.
SOUTH, NORTH, WEST, EAST = 17.75, 29.25, 115.00, 126.50
LONGITUDE_RANGE, LATITUDE_RANGE, IMAGE_DIMENSION = "115.00-126.50", "17.75-29.25", "3600x3600"
PROJECTION = "EPSG:3857"

RETENTION = timedelta(hours=2)
MAX_FRAMES = 13  # 2 hours of 10-minute frames, both ends included
COLLECT_INTERVAL = timedelta(minutes=10)
# After a failed attempt (or before CWA has published the next frame), wait
# this long before asking CWA again.
RETRY_COOLDOWN = timedelta(minutes=2)
# PNG and metadata JSON are uploaded together; a larger gap means one of them
# already belongs to another frame.
MAX_UPLOAD_SKEW = timedelta(minutes=2)

DATA_DIR = db.DB_PATH.parent
FRAME_DIR_NAME = "radar"


class RadarError(Exception):
    """The current radar frame could not be collected."""


@dataclass(frozen=True)
class Metadata:
    timestamp: str  # observation time, ISO 8601 with +08:00
    etag: str | None
    last_modified: datetime


@dataclass(frozen=True)
class CollectResult:
    added: bool  # a new frame was stored
    timestamp: str | None  # observation time CWA currently publishes
    error: str | None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _get(url: str, timeout: int) -> requests.Response:
    try:
        response = requests.get(url, timeout=timeout)
    except requests.RequestException as e:
        raise RadarError(f"Network error while downloading {SOURCE}: {type(e).__name__}") from None
    if response.status_code != 200:
        raise RadarError(f"CWA returned HTTP {response.status_code} for {url.rsplit('/', 1)[-1]}")
    return response


def _last_modified(response: requests.Response) -> datetime:
    try:
        return parsedate_to_datetime(response.headers["Last-Modified"])
    except (KeyError, TypeError, ValueError):
        raise RadarError("CWA response has no valid Last-Modified header") from None


def parse_metadata(payload: dict) -> str:
    """Validate the metadata JSON and return its observation time."""
    try:
        dataset = payload["cwaopendata"]["dataset"]
        params = dataset["datasetInfo"]["parameterSet"]
        raw_time = dataset["DateTime"]
    except (KeyError, TypeError):
        raise RadarError("Unexpected O-A0058-005 metadata structure") from None
    # The reprojection assumes this exact extent; refuse to guess if it changes.
    extent = (params.get("LongitudeRange"), params.get("LatitudeRange"), params.get("ImageDimension"))
    if extent != (LONGITUDE_RANGE, LATITUDE_RANGE, IMAGE_DIMENSION):
        raise RadarError(f"O-A0058-005 extent changed: {extent}")
    try:
        observed = datetime.fromisoformat(raw_time)
    except (TypeError, ValueError):
        raise RadarError(f"Invalid O-A0058-005 DateTime: {raw_time!r}") from None
    if observed.tzinfo is None:
        raise RadarError(f"O-A0058-005 DateTime has no UTC offset: {raw_time!r}")
    return observed.isoformat()


def fetch_metadata(timeout: int = REQUEST_TIMEOUT) -> Metadata:
    response = _get(METADATA_URL, timeout)
    try:
        payload = response.json()
    except ValueError:
        raise RadarError("O-A0058-005 metadata is not valid JSON") from None
    return Metadata(parse_metadata(payload), response.headers.get("ETag"), _last_modified(response))


def fetch_png(timeout: int = REQUEST_TIMEOUT) -> tuple[bytes, datetime]:
    response = _get(PNG_URL, timeout)
    return response.content, _last_modified(response)


def frame_file_name(timestamp: str) -> str:
    return f"radar_{datetime.fromisoformat(timestamp).strftime('%Y%m%d%H%M')}.png"


def frame_dir(data_dir: Path) -> Path:
    return data_dir / FRAME_DIR_NAME


def available_frames(db_path: Path = db.DB_PATH, data_dir: Path = DATA_DIR) -> list[dict]:
    """Stored frames within the retention window whose file exists, oldest first."""
    frames = [f for f in db.get_radar_frames(db_path) if (data_dir / f["file_path"]).is_file()]
    return _retained(frames)


def _retained(frames: list[dict]) -> list[dict]:
    if not frames:
        return []
    cutoff = datetime.fromisoformat(frames[-1]["timestamp"]) - RETENTION
    return [f for f in frames if datetime.fromisoformat(f["timestamp"]) >= cutoff][-MAX_FRAMES:]


def prune(db_path: Path = db.DB_PATH, data_dir: Path = DATA_DIR) -> list[str]:
    """Delete frames outside the retention window, and stray files. Returns removed timestamps."""
    frames = db.get_radar_frames(db_path)
    keep = {f["timestamp"] for f in _retained(frames)}
    removed = [f for f in frames if f["timestamp"] not in keep]
    db.delete_radar_frames([f["timestamp"] for f in removed], db_path)
    kept_files = {Path(f["file_path"]).name for f in frames if f["timestamp"] in keep}
    directory = frame_dir(data_dir)
    if directory.is_dir():
        for path in directory.iterdir():
            if path.is_file() and path.name not in kept_files and path.name.startswith("radar_"):
                # A file can be briefly locked (e.g. being served on Windows);
                # it is no longer listed and the next prune retries it.
                try:
                    path.unlink(missing_ok=True)
                except OSError as e:
                    log.warning("Could not delete old radar file %s: %s", path.name, type(e).__name__)
    return [f["timestamp"] for f in removed]


def _collect(db_path: Path, data_dir: Path,
             fetch_metadata: Callable[[], Metadata], fetch_png: Callable[[], tuple[bytes, datetime]]) -> CollectResult:
    meta = fetch_metadata()
    relative = f"{FRAME_DIR_NAME}/{frame_file_name(meta.timestamp)}"
    path = data_dir / relative
    # Already stored on both sides. A row whose PNG went missing is downloaded
    # again (the row is kept), so the frame becomes usable once more.
    if db.radar_frame_exists(meta.timestamp, db_path) and path.is_file():
        return CollectResult(added=False, timestamp=meta.timestamp, error=None)

    png, png_modified = fetch_png()
    after = fetch_metadata()
    if after != meta or abs(png_modified - meta.last_modified) > MAX_UPLOAD_SKEW:
        raise RadarError("CWA was replacing the radar frame during the download; will retry")

    try:
        mercator = radar_png.reproject_to_mercator(png, SOUTH, NORTH, WEST, EAST)
    except radar_png.PNGError as e:
        raise RadarError(f"Invalid O-A0058-005 PNG: {e}") from None

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(mercator)
    os.replace(tmp, path)  # readers never see a half-written file
    try:
        added = db.insert_radar_frame(meta.timestamp, relative, SOURCE, db_path)
    except sqlite3.Error:
        path.unlink(missing_ok=True)
        raise
    prune(db_path, data_dir)
    return CollectResult(added=added, timestamp=meta.timestamp, error=None)


_lock = threading.Lock()
_last_attempt: dict[Path, datetime] = {}
_last_error: dict[Path, str | None] = {}


def collect(db_path: Path = db.DB_PATH, data_dir: Path = DATA_DIR, *,
            fetch_metadata: Callable[[], Metadata] = fetch_metadata,
            fetch_png: Callable[[], tuple[bytes, datetime]] = fetch_png,
            now: Callable[[], datetime] = utc_now) -> CollectResult:
    """Store CWA's current frame if it is new. Never raises; failures leave stored frames untouched."""
    with _lock:
        return _collect_locked(db_path, data_dir, fetch_metadata, fetch_png, now)


def _collect_locked(db_path, data_dir, fetch_metadata, fetch_png, now) -> CollectResult:
    _last_attempt[db_path] = now()
    try:
        result = _collect(db_path, data_dir, fetch_metadata, fetch_png)
    except RadarError as e:
        result = CollectResult(added=False, timestamp=None, error=str(e))
    except (sqlite3.Error, OSError) as e:
        result = CollectResult(added=False, timestamp=None, error=f"Could not store radar frame: {type(e).__name__}")
    _last_error[db_path] = result.error
    if result.error:
        log.warning("Radar collection failed: %s", result.error)
    elif result.added:
        log.info("Stored radar frame %s", result.timestamp)
    return result


def _newest_is_current(db_path: Path, data_dir: Path, now: datetime) -> bool:
    frames = available_frames(db_path, data_dir)
    return bool(frames) and now - datetime.fromisoformat(frames[-1]["timestamp"]) < COLLECT_INTERVAL


def ensure_recent(db_path: Path = db.DB_PATH, data_dir: Path = DATA_DIR, *,
                  fetch_metadata: Callable[[], Metadata] = fetch_metadata,
                  fetch_png: Callable[[], tuple[bytes, datetime]] = fetch_png,
                  now: Callable[[], datetime] = utc_now) -> str | None:
    """Collect on read when the newest frame is 10+ minutes old, at most once per
    RETRY_COOLDOWN. Returns the last collection error, if any."""
    if _newest_is_current(db_path, data_dir, now()):
        return None
    with _lock:
        # Another request may have collected while this one waited for the lock.
        if _newest_is_current(db_path, data_dir, now()):
            return None
        last = _last_attempt.get(db_path)
        if last is not None and now() - last < RETRY_COOLDOWN:
            return _last_error.get(db_path)
        return _collect_locked(db_path, data_dir, fetch_metadata, fetch_png, now).error


_collector_started = False


def start_collector(db_path: Path = db.DB_PATH, data_dir: Path = DATA_DIR) -> bool:
    """Collect now, then every COLLECT_INTERVAL (RETRY_COOLDOWN after a failure),
    in a daemon thread. Only the first call starts a thread."""
    global _collector_started
    with _lock:
        if _collector_started:
            return False
        _collector_started = True

    def run():
        while True:
            result = collect(db_path, data_dir)
            delay = RETRY_COOLDOWN if result.error else COLLECT_INTERVAL
            threading.Event().wait(delay.total_seconds())

    threading.Thread(target=run, name="radar-collector", daemon=True).start()
    return True
