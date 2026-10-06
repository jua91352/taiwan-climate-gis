"""Radar PNG storage (backend.radar_storage): local files and Vercel Blob.

vercel.blob.put / delete are always replaced (tests.radar_support.FakeBlobStore
or mocks); nothing reaches Vercel and no real token is needed. The lifecycle
tests force Blob storage on whichever database backend the run uses, so the
SQLite run covers Blob + SQLite and tests/run_postgres.py covers Blob + Neon.
Run: python -m unittest tests.test_radar_storage
"""
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from vercel.blob import BlobNotFoundError

from tests.test_radar import FakeCWA, T0, metadata  # also sets RADAR_COLLECTOR=0
from backend import db, radar, radar_png, radar_storage
from backend.radar_storage import LocalRadarStorage, StorageError, VercelBlobRadarStorage
from tests.db_support import fresh_db
from tests.radar_support import FAKE_BASE_URL, FAKE_TOKEN, FakeBlobStore, RadarStorageMixin

PATH = "radar/radar_202610061530.png"
PNG = b"\x89PNG\r\n\x1a\n" + b"frame-bytes"


class LocalStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.storage = LocalRadarStorage(self.data_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_writes_the_file_atomically(self):
        self.storage.save(PATH, PNG)
        self.assertEqual((self.data_dir / PATH).read_bytes(), PNG)
        self.assertEqual(sorted(p.name for p in self.storage.frame_dir.iterdir()), ["radar_202610061530.png"])  # no .tmp left

    def test_exists(self):
        self.assertFalse(self.storage.exists(PATH))
        self.storage.save(PATH, PNG)
        self.assertTrue(self.storage.exists(PATH))

    def test_delete(self):
        self.storage.save(PATH, PNG)
        self.storage.delete([PATH, "radar/radar_209901010000.png"])  # a missing file is fine
        self.assertFalse(self.storage.exists(PATH))
        self.storage.delete([])

    def test_location_is_the_data_directory(self):
        # /api/radar/frames/<file> serves files from frame_dir (send_from_directory).
        self.assertEqual(self.storage.frame_dir, self.data_dir / "radar")
        self.assertEqual(self.storage.path(PATH), self.data_dir / "radar" / "radar_202610061530.png")

    def test_sweep_removes_only_unkept_radar_files(self):
        for name in ("radar_1.png", "radar_2.png"):
            self.storage.save(f"radar/{name}", PNG)
        (self.storage.frame_dir / "notes.txt").write_text("keep")
        self.storage.sweep({"radar_2.png"})
        self.assertEqual(sorted(p.name for p in self.storage.frame_dir.iterdir()), ["notes.txt", "radar_2.png"])

    def test_write_error_becomes_storage_error(self):
        with mock.patch.object(Path, "write_bytes", side_effect=PermissionError("denied")):
            with self.assertRaises(StorageError) as caught:
                self.storage.save(PATH, PNG)
        self.assertEqual(str(caught.exception), "Could not store radar frame: PermissionError")


class BlobStorageTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeBlobStore()
        self.put = mock.Mock(side_effect=self.store.put)
        self.delete = mock.Mock(side_effect=self.store.delete)
        for p in (mock.patch("vercel.blob.put", self.put), mock.patch("vercel.blob.delete", self.delete)):
            p.start()
            self.addCleanup(p.stop)
        self.storage = VercelBlobRadarStorage(token=FAKE_TOKEN)

    def test_put_arguments(self):
        self.storage.save(PATH, PNG)
        self.put.assert_called_once_with(PATH, PNG, access="public", content_type="image/png",
                                         add_random_suffix=False, overwrite=True, token=FAKE_TOKEN)
        self.assertEqual(self.store.blobs, {PATH: PNG})

    def test_token_comes_from_the_environment(self):
        with mock.patch.dict(os.environ, {"BLOB_READ_WRITE_TOKEN": FAKE_TOKEN}):
            VercelBlobRadarStorage().save(PATH, PNG)
        self.assertEqual(self.put.call_args.kwargs["token"], FAKE_TOKEN)

    def test_public_url_matches_the_url_put_returns(self):
        result = self.store.put(PATH, PNG)
        self.assertEqual(self.storage.public_url(PATH), result.url)
        self.assertEqual(self.storage.public_url(PATH), FAKE_BASE_URL + PATH)

    def test_put_url_is_checked_against_the_derived_url(self):
        other = FakeBlobStore(base_url="https://elsewhere.public.blob.vercel-storage.com/")
        self.put.side_effect = other.put
        with self.assertLogs("backend.radar_storage", "WARNING") as logs:
            self.storage.save(PATH, PNG)
        self.assertIn("https://elsewhere.public.blob.vercel-storage.com/" + PATH, logs.output[0])

    def test_delete_arguments(self):
        self.storage.save(PATH, PNG)
        self.storage.delete([PATH])
        self.delete.assert_called_once_with([PATH], token=FAKE_TOKEN)
        self.assertEqual(self.store.blobs, {})

    def test_delete_nothing_makes_no_call(self):
        self.storage.delete([])
        self.delete.assert_not_called()

    def test_delete_of_a_missing_blob_is_fine(self):
        self.delete.side_effect = BlobNotFoundError()
        self.storage.delete([PATH])

    def test_exists_and_sweep_make_no_blob_calls(self):
        self.assertTrue(self.storage.exists(PATH))
        self.storage.sweep(set())
        self.put.assert_not_called()
        self.delete.assert_not_called()

    def test_errors_are_storage_errors_without_the_token(self):
        self.put.side_effect = RuntimeError(f"401 for {FAKE_TOKEN}")
        with self.assertRaises(StorageError) as caught:
            self.storage.save(PATH, PNG)
        self.assertNotIn(FAKE_TOKEN, str(caught.exception))
        self.assertIn("Could not upload radar frame to Vercel Blob (RuntimeError: 401 for ***)", str(caught.exception))
        self.delete.side_effect = ConnectionError("reset")
        with self.assertRaises(StorageError) as caught:
            self.storage.delete([PATH])
        self.assertIn("Could not delete old radar frames from Vercel Blob (ConnectionError: reset)", str(caught.exception))

    def test_missing_or_malformed_token(self):
        missing = VercelBlobRadarStorage(token="")
        for call in (lambda: missing.save(PATH, PNG), lambda: missing.delete([PATH]), lambda: missing.public_url(PATH)):
            with self.assertRaisesRegex(StorageError, "BLOB_READ_WRITE_TOKEN is missing"):
                call()
        self.put.assert_not_called()
        self.delete.assert_not_called()
        with self.assertRaisesRegex(StorageError, "unexpected format"):
            VercelBlobRadarStorage(token="not-a-blob-token").public_url(PATH)


class SelectionTests(unittest.TestCase):
    def kind(self, backend: str, env: dict) -> str:
        with mock.patch.object(db, "DATABASE_BACKEND", backend), mock.patch.dict(os.environ, env, clear=True):
            return radar_storage._storage_kind()

    def test_defaults_follow_the_database_backend(self):
        self.assertEqual(self.kind("sqlite", {}), "local")
        self.assertEqual(self.kind("postgres", {}), "blob")

    def test_explicit_setting_wins(self):
        self.assertEqual(self.kind("postgres", {"RADAR_STORAGE": "local"}), "local")
        self.assertEqual(self.kind("sqlite", {"RADAR_STORAGE": " Blob "}), "blob")
        with self.assertRaisesRegex(RuntimeError, "RADAR_STORAGE"):
            self.kind("sqlite", {"RADAR_STORAGE": "s3"})

    def test_get_storage(self):
        data_dir = Path("x")
        with mock.patch.object(radar_storage, "RADAR_STORAGE", "local"):
            self.assertIsInstance(radar_storage.get_storage(data_dir), LocalRadarStorage)
        with mock.patch.object(radar_storage, "RADAR_STORAGE", "blob"):
            self.assertIsInstance(radar_storage.get_storage(data_dir), VercelBlobRadarStorage)


class BlobLifecycleTests(RadarStorageMixin, unittest.TestCase):
    """Collect / list / serve / prune with Blob storage and the run's database."""

    blob = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.db_path = self.data_dir / "weather.db"
        fresh_db(self.db_path)
        self.start_storage()
        self.cwa = FakeCWA(T0)

    def tearDown(self):
        self.tmp.cleanup()

    def collect(self, ts=T0):
        self.cwa.meta = [metadata(ts)]
        self.cwa.png_modified = self.cwa.meta[0].last_modified
        return radar.collect(self.db_path, self.data_dir, fetch_metadata=self.cwa.fetch_metadata,
                             fetch_png=self.cwa.fetch_png, now=lambda: ts + timedelta(minutes=8))

    def stamps(self) -> list[str]:
        return [f["timestamp"] for f in db.get_radar_frames(self.db_path)]

    def client(self):
        from backend.app import create_app
        patches = [mock.patch.object(db, "DB_PATH", self.db_path), mock.patch.object(radar, "DATA_DIR", self.data_dir),
                   mock.patch.object(radar, "ensure_recent", lambda *a, **k: None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return create_app().test_client()

    def test_collect_uploads_and_records_without_local_files(self):
        result = self.collect()
        self.assertEqual(result, radar.CollectResult(added=True, timestamp=T0.isoformat(), error=None))
        self.assertEqual(db.get_radar_frames(self.db_path), [
            {"timestamp": T0.isoformat(), "file_path": "radar/radar_202610032100.png", "source": radar.SOURCE}])
        self.assertTrue(radar_png.decode_rgba(self.stored_bytes("radar_202610032100.png")))
        self.assertFalse(radar.frame_dir(self.data_dir).exists())  # nothing under data_dir (/tmp on Vercel)

    def test_available_frames_do_not_depend_on_local_files(self):
        db.insert_radar_frame(T0.isoformat(), "radar/radar_202610032100.png", radar.SOURCE, self.db_path)
        self.assertEqual([f["timestamp"] for f in radar.available_frames(self.db_path, self.data_dir)], [T0.isoformat()])

    def test_same_frame_is_not_uploaded_again(self):
        self.collect()
        self.assertFalse(self.collect().added)
        self.assertEqual(len(self.blob_store.put_calls), 1)

    def test_upload_failure_records_nothing(self):
        self.blob_store.put_error = ConnectionError("Blob unavailable")
        result = self.collect()
        self.assertFalse(result.added)
        self.assertIn("Could not upload radar frame to Vercel Blob", result.error)
        self.assertEqual(self.stamps(), [])
        self.assertEqual(self.stored_names(), [])

    def test_database_failure_removes_the_upload(self):
        with mock.patch.object(db, "insert_radar_frame", side_effect=db.DBError[-1]("down")):
            result = self.collect()
        self.assertIn("Could not store radar frame", result.error)
        self.assertEqual(self.stored_names(), [])
        self.assertEqual(self.stamps(), [])

    def test_upload_is_kept_when_another_instance_recorded_the_frame(self):
        real_insert = db.insert_radar_frame

        def recorded_elsewhere_then_lost(*args):
            real_insert(*args)  # stands in for another instance's row for the same frame
            raise db.DBError[-1]("connection lost")

        with mock.patch.object(db, "insert_radar_frame", side_effect=recorded_elsewhere_then_lost):
            self.collect()
        self.assertEqual(self.stamps(), [T0.isoformat()])
        self.assertEqual(self.stored_names(), ["radar_202610032100.png"])  # the recorded frame keeps its PNG

    def test_upload_is_kept_when_the_database_cannot_tell(self):
        error = db.DBError[-1]("down")
        with mock.patch.object(db, "insert_radar_frame", side_effect=error), \
                mock.patch.object(db, "radar_frame_exists", side_effect=[False, error]):
            self.collect()
        self.assertEqual(self.stored_names(), ["radar_202610032100.png"])  # stray, but no row without PNG
        self.assertEqual(self.stamps(), [])

    def test_prune_deletes_blob_and_metadata(self):
        self.collect(T0)
        later = T0 + timedelta(hours=2, minutes=10)
        self.assertIsNone(self.collect(later).error)
        self.assertEqual(self.stamps(), [later.isoformat()])
        self.assertEqual(self.stored_names(), ["radar_202610032310.png"])
        self.assertEqual(self.blob_store.delete_calls[-1], (["radar/radar_202610032100.png"], FAKE_TOKEN))

    def test_blob_delete_failure_keeps_metadata_and_is_reported(self):
        self.collect(T0)
        later = T0 + timedelta(hours=2, minutes=10)
        self.blob_store.delete_error = ConnectionError("Blob unavailable")
        result = self.collect(later)
        self.assertTrue(result.added)  # the new frame is stored
        self.assertIn("Could not prune old radar frames: Could not delete old radar frames from Vercel Blob", result.error)
        self.assertEqual(self.stamps(), [T0.isoformat(), later.isoformat()])  # old row kept with its blob
        self.assertEqual(self.stored_names(), ["radar_202610032100.png", "radar_202610032310.png"])
        self.assertEqual([f["timestamp"] for f in radar.available_frames(self.db_path, self.data_dir)], [later.isoformat()])
        self.blob_store.delete_error = None
        self.assertEqual(radar.prune(self.db_path, self.data_dir), [T0.isoformat()])  # next prune finishes it
        self.assertEqual(self.stored_names(), ["radar_202610032310.png"])

    def test_missing_token_is_a_radar_error_not_a_crash(self):
        with mock.patch.dict(os.environ, {"BLOB_READ_WRITE_TOKEN": ""}):
            result = self.collect()
            self.assertEqual(result.error, "BLOB_READ_WRITE_TOKEN is missing")
            self.assertEqual(self.stamps(), [])
            db.insert_radar_frame(T0.isoformat(), "radar/radar_202610032100.png", radar.SOURCE, self.db_path)
            response = self.client().get("/api/radar/frames/radar_202610032100.png")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json(), {"error": "BLOB_READ_WRITE_TOKEN is missing"})

    def test_image_request_redirects_to_blob(self):
        self.collect()
        client = self.client()
        body = client.get("/api/radar/history").get_json()
        self.assertEqual(body["frames"], [{"timestamp": T0.isoformat(), "image_url": "/api/radar/frames/radar_202610032100.png",
                                           "source": radar.SOURCE}])
        response = client.get(body["frames"][0]["image_url"])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], FAKE_BASE_URL + "radar/radar_202610032100.png")
        self.assertIn("max-age=7200", response.headers["Cache-Control"])
        self.assertNotEqual(response.mimetype, "image/png")  # no PNG passes through Flask
        self.assertEqual(client.get("/api/radar/frames/radar_209901010000.png").status_code, 404)


if __name__ == "__main__":
    unittest.main()
