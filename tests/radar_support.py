"""Lets the radar tests run on either PNG storage (backend.radar_storage).

The storage follows the backend, as in production: local files on the SQLite
run, Vercel Blob on the PostgreSQL run (tests/run_postgres.py). For Blob,
vercel.blob.put / delete are replaced by FakeBlobStore, an in-memory store,
so no request ever reaches Vercel; the tests then check the same things
through the fake store instead of the data directory.
"""
import os
import threading
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

from backend import radar, radar_storage

BLOB = radar_storage.RADAR_STORAGE == "blob"
local_only = unittest.skipIf(BLOB, "tests local PNG files")

FAKE_STORE_ID = "teststore"
FAKE_TOKEN = f"vercel_blob_rw_{FAKE_STORE_ID}_FakeSecretValue123"
FAKE_BASE_URL = f"https://{FAKE_STORE_ID}.public.blob.vercel-storage.com/"


@dataclass(frozen=True)
class FakePutResult:
    """Same fields as vercel.blob.PutBlobResult."""
    url: str
    download_url: str
    pathname: str
    content_type: str | None
    content_disposition: str


class FakeBlobStore:
    """In-memory stand-in for a public Vercel Blob store."""

    def __init__(self, base_url: str = FAKE_BASE_URL):
        self.base_url = base_url
        self.blobs: dict[str, bytes] = {}
        self.put_calls: list[tuple[str, dict]] = []
        self.delete_calls: list[tuple[list[str], str | None]] = []
        self.put_error: Exception | None = None
        self.delete_error: Exception | None = None
        self._lock = threading.Lock()

    def put(self, path: str, body: bytes, **kwargs) -> FakePutResult:
        with self._lock:
            self.put_calls.append((path, kwargs))
            if self.put_error:
                raise self.put_error
            self.blobs[path] = bytes(body)
        url = self.base_url + path
        return FakePutResult(url, url + "?download=1", path, kwargs.get("content_type"), "inline")

    def delete(self, url_or_path, *, token: str | None = None) -> None:
        paths = [url_or_path] if isinstance(url_or_path, str) else list(url_or_path)
        with self._lock:
            self.delete_calls.append((paths, token))
            if self.delete_error:
                raise self.delete_error
            for p in paths:
                self.blobs.pop(p.removeprefix(self.base_url), None)

    def patches(self) -> list:
        return [mock.patch("vercel.blob.put", self.put),
                mock.patch("vercel.blob.delete", self.delete),
                mock.patch.dict(os.environ, {"BLOB_READ_WRITE_TOKEN": FAKE_TOKEN})]


class RadarStorageMixin:
    """For radar TestCases with self.data_dir: call start_storage() in setUp."""

    blob = BLOB

    def start_storage(self) -> None:
        self.blob_store = FakeBlobStore()
        patches = self.blob_store.patches() if self.blob else []
        if self.blob and not BLOB:
            patches.append(mock.patch.object(radar_storage, "RADAR_STORAGE", "blob"))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def stored_names(self) -> list[str]:
        """File names of the stored PNGs."""
        if self.blob:
            return sorted(Path(p).name for p in self.blob_store.blobs)
        directory = radar.frame_dir(self.data_dir)
        return sorted(p.name for p in directory.iterdir()) if directory.exists() else []

    def stored_bytes(self, name: str) -> bytes:
        if self.blob:
            return self.blob_store.blobs[f"radar/{name}"]
        return (radar.frame_dir(self.data_dir) / name).read_bytes()

    def storage_snapshot(self):
        """Something that changes whenever a PNG is written again."""
        if self.blob:
            return dict(self.blob_store.blobs), len(self.blob_store.put_calls)
        return {p.name: p.stat().st_mtime_ns for p in radar.frame_dir(self.data_dir).iterdir()}

    def fetch_image(self, client, image_url: str) -> bytes:
        """GET an image_url the way a browser does: served by Flask, or a 302 to Blob."""
        response = client.get(image_url)
        try:
            if self.blob:
                self.assertEqual(response.status_code, 302)
                location = response.headers["Location"]
                self.assertTrue(location.startswith(self.blob_store.base_url), location)
                return self.blob_store.blobs[location.removeprefix(self.blob_store.base_url)]
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "image/png")
            return response.data
        finally:
            response.close()
