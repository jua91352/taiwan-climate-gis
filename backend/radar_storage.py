"""Where the reprojected radar PNGs live (backend.radar). Their metadata, the
RadarFrame rows, stays in backend.db.

RADAR_STORAGE selects the storage:
  local  files under <data dir>/radar/ (default with DATABASE_BACKEND=sqlite)
  blob   a public Vercel Blob store (default with DATABASE_BACKEND=postgres)
A PNG is addressed by its RadarFrame.file_path, e.g. radar/radar_202610061530.png,
which is both its path under the data directory and its blob pathname.

The Blob store is reached with BLOB_READ_WRITE_TOKEN only (the Python SDK has
no OIDC support). A missing token does not stop the app: radar collection and
image requests report it, everything else keeps working. The token is never
logged or returned.
"""
import logging
import os
from collections.abc import Iterable
from pathlib import Path

from backend import db

log = logging.getLogger(__name__)

FRAME_DIR_NAME = "radar"


def _storage_kind() -> str:
    kind = os.environ.get("RADAR_STORAGE", "").strip().lower()
    if not kind:
        return "blob" if db.DATABASE_BACKEND == "postgres" else "local"
    if kind not in ("local", "blob"):
        raise RuntimeError(f"RADAR_STORAGE must be 'local' or 'blob', not {kind!r}")
    return kind


RADAR_STORAGE = _storage_kind()


class StorageError(Exception):
    """A PNG could not be saved, deleted or addressed. The message is safe to show."""


class LocalRadarStorage:
    """PNGs as files under <data_dir>/radar/, served by Flask."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    @property
    def frame_dir(self) -> Path:
        return self.data_dir / FRAME_DIR_NAME

    def path(self, file_path: str) -> Path:
        return self.data_dir / file_path

    def save(self, file_path: str, png: bytes) -> None:
        path = self.path(file_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(png)
            os.replace(tmp, path)  # readers never see a half-written file
        except OSError as e:
            raise StorageError(f"Could not store radar frame: {type(e).__name__}") from None

    def exists(self, file_path: str) -> bool:
        return self.path(file_path).is_file()

    def delete(self, file_paths: Iterable[str]) -> None:
        for file_path in file_paths:
            self._unlink(self.path(file_path))

    def sweep(self, keep: set[str]) -> None:
        """Delete stray radar_*.png files whose name is not in keep."""
        if self.frame_dir.is_dir():
            for path in self.frame_dir.iterdir():
                if path.is_file() and path.name.startswith("radar_") and path.name not in keep:
                    self._unlink(path)

    @staticmethod
    def _unlink(path: Path) -> None:
        # A file can be briefly locked (e.g. being served on Windows); it is
        # no longer listed, and the next sweep retries it.
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            log.warning("Could not delete old radar file %s: %s", path.name, type(e).__name__)


class VercelBlobRadarStorage:
    """PNGs as public blobs; browsers load them straight from the Blob CDN.

    The RadarFrame row is the record that a frame's blob exists: exists() does
    not ask Blob (that would cost an operation per frame per request), and
    there is no sweep, which would need list().
    """

    def __init__(self, token: str | None = None):
        self._token = (os.environ.get("BLOB_READ_WRITE_TOKEN", "") if token is None else token).strip()

    def _require_token(self) -> str:
        if not self._token:
            raise StorageError("BLOB_READ_WRITE_TOKEN is missing")
        return self._token

    def _safe(self, e: Exception) -> str:
        text = str(e).replace(self._token, "***") if self._token else str(e)
        return f"{type(e).__name__}: {text}"[:200]

    def public_url(self, file_path: str) -> str:
        """https://<store id>.public.blob.vercel-storage.com/<file_path>. The store
        id is the fourth _-separated part of the token (vercel_blob_rw_<id>_...),
        the same rule the SDK uses to address blobs by pathname."""
        parts = self._require_token().split("_")
        if len(parts) < 5 or not parts[3]:
            raise StorageError("BLOB_READ_WRITE_TOKEN has an unexpected format")
        return f"https://{parts[3]}.public.blob.vercel-storage.com/{file_path}"

    def save(self, file_path: str, png: bytes) -> None:
        token = self._require_token()
        from vercel.blob import put

        try:
            blob = put(file_path, png, access="public", content_type="image/png",
                       add_random_suffix=False, overwrite=True, token=token)
        except Exception as e:
            raise StorageError(f"Could not upload radar frame to Vercel Blob ({self._safe(e)})") from None
        if blob.url != self.public_url(file_path):
            # Image requests redirect to the derived URL; it must match the real one.
            log.warning("Vercel Blob returned %s for %s, expected %s", blob.url, file_path, self.public_url(file_path))

    def exists(self, file_path: str) -> bool:
        return True

    def delete(self, file_paths: Iterable[str]) -> None:
        paths = list(file_paths)
        if not paths:
            return
        token = self._require_token()
        from vercel.blob import BlobNotFoundError, delete

        try:
            delete(paths, token=token)
        except BlobNotFoundError:
            pass  # already gone, e.g. a retry after the metadata delete failed
        except Exception as e:
            raise StorageError(f"Could not delete old radar frames from Vercel Blob ({self._safe(e)})") from None

    def sweep(self, keep: set[str]) -> None:
        pass


def get_storage(data_dir: Path) -> LocalRadarStorage | VercelBlobRadarStorage:
    """The configured storage; local files live under data_dir."""
    if RADAR_STORAGE == "blob":
        return VercelBlobRadarStorage()
    return LocalRadarStorage(data_dir)
