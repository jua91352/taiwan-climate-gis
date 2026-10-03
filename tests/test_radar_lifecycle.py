"""Radar data lifecycle and stability tests (retention, duplicates, restart,
file/database consistency, concurrency, CWA failures).

Unlike tests.test_radar, CWA is faked one level lower, at requests.get, so the
real metadata parsing, Last-Modified pairing and PNG decoding run too. Every
test uses its own temporary SQLite file and frame directory; production data
is never touched.
Run: python -m unittest tests.test_radar_lifecycle
"""
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest import mock

import requests

from tests.test_radar import encode_with_filters, sparse_rows  # also sets RADAR_COLLECTOR=0
from backend import db, radar, radar_png

TAIPEI = timezone(timedelta(hours=8))
T0 = datetime(2026, 10, 3, 20, 0, tzinfo=TAIPEI)
PNG = encode_with_filters(4, sparse_rows(4, 6, seed=7), [2] * 6)


class FakeResponse:
    def __init__(self, status: int = 200, body: bytes = b"", payload=None, headers: dict | None = None):
        self.status_code = status
        self.content = body
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("not JSON")
        return self._payload


class FakeS3:
    """The two fixed CWA objects (metadata JSON + PNG), uploaded together."""

    def __init__(self, ts: datetime):
        self.lock = threading.Lock()
        self.publish(ts)
        self.png_body = PNG
        self.png_downloads = 0
        self.png_delay = 0.0
        self.fail: str | None = None  # "network", "http", "json", "structure", "png_time", "no_last_modified"
        self.on_png = None  # called after the PNG is served (e.g. CWA publishes the next frame)

    def publish(self, ts: datetime, png_uploaded: datetime | None = None):
        self.ts = ts
        uploaded = (ts + timedelta(minutes=7)).astimezone(timezone.utc)
        self.json_modified = uploaded
        self.png_modified = png_uploaded or uploaded

    def metadata_payload(self) -> dict:
        return {"cwaopendata": {"dataid": "O-A0058-005", "dataset": {
            "datasetInfo": {"parameterSet": {
                "LongitudeRange": "115.00-126.50", "LatitudeRange": "17.75-29.25", "ImageDimension": "3600x3600"}},
            "DateTime": self.ts.isoformat(),
        }}}

    def get(self, url, timeout=None):
        if self.fail == "network":
            raise requests.ConnectionError("CWA unreachable")
        if self.fail == "http":
            return FakeResponse(503)
        if url == radar.METADATA_URL:
            headers = {"ETag": f'"{self.ts:%H%M}"', "Last-Modified": format_datetime(self.json_modified, usegmt=True)}
            if self.fail == "no_last_modified":
                headers.pop("Last-Modified")
            if self.fail == "json":
                return FakeResponse(200, b"<html>", None, headers)
            if self.fail == "structure":
                return FakeResponse(200, b"{}", {"cwaopendata": {}}, headers)
            return FakeResponse(200, b"{}", self.metadata_payload(), headers)
        if url == radar.PNG_URL:
            with self.lock:
                self.png_downloads += 1
            time.sleep(self.png_delay)
            response = FakeResponse(200, self.png_body, None,
                                    {"Last-Modified": format_datetime(self.png_modified, usegmt=True)})
            if self.on_png:
                self.on_png()
            return response
        raise AssertionError(f"unexpected URL {url}")


class LifecycleTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.db_path = self.data_dir / "weather.db"
        db.init_db(self.db_path)
        self.s3 = FakeS3(T0)
        patches = [mock.patch.object(radar.requests, "get", self.s3.get),
                   mock.patch.object(db, "DB_PATH", self.db_path),
                   mock.patch.object(radar, "DATA_DIR", self.data_dir)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def collect(self, ts: datetime | None = None) -> radar.CollectResult:
        if ts is not None:
            self.s3.publish(ts)
        # Real fetch_metadata / fetch_png (over the faked requests.get).
        return radar.collect(self.db_path, self.data_dir, now=lambda: self.s3.ts + timedelta(minutes=8))

    def client(self):
        from backend.app import create_app
        return create_app().test_client()

    def history(self, client=None) -> dict:
        response = (client or self.client()).get("/api/radar/history")
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def db_stamps(self) -> list[str]:
        return [f["timestamp"] for f in db.get_radar_frames(self.db_path)]

    def png_files(self) -> list[str]:
        directory = radar.frame_dir(self.data_dir)
        return sorted(p.name for p in directory.iterdir()) if directory.exists() else []

    def assert_consistent(self, expected: list[datetime]):
        """SQLite rows, PNG files and the history API all hold exactly these frames."""
        stamps = [t.isoformat() for t in expected]
        self.assertEqual(self.db_stamps(), stamps)
        self.assertEqual(self.png_files(), sorted(radar.frame_file_name(s) for s in stamps))
        body = self.history()
        self.assertEqual([f["timestamp"] for f in body["frames"]], stamps)
        for f in body["frames"]:
            self.assertTrue(radar_png.decode_rgba((radar.frame_dir(self.data_dir) / Path(f["image_url"]).name).read_bytes()))


class RetentionTests(LifecycleTestCase):
    def test_long_run_15_frames_keeps_latest_13_on_both_sides(self):
        # 20:00, 20:10, ... 22:20 (15 frames, one every 10 minutes).
        times = [T0 + timedelta(minutes=10 * k) for k in range(15)]
        for ts in times:
            self.assertTrue(self.collect(ts).added)
        kept = times[2:]  # 20:00 and 20:10 are more than 2 hours before 22:20
        self.assert_consistent(kept)
        body = self.history()
        self.assertEqual(body["count"], 13)
        self.assertEqual(len(body["frames"]), 13)
        self.assertEqual(len({f["timestamp"] for f in body["frames"]}), 13)
        self.assertEqual(body["latest_timestamp"], times[-1].isoformat())
        self.assertNotIn("radar_202610032000.png", self.png_files())
        self.assertNotIn("radar_202610032010.png", self.png_files())

    def test_case_e_old_frame_removed_from_db_and_disk(self):
        self.collect(T0)
        later = T0 + timedelta(hours=2, minutes=10)
        self.collect(later)
        self.assert_consistent([later])

    def test_fewer_than_13_frames_returns_only_real_frames(self):
        times = [T0, T0 + timedelta(minutes=10), T0 + timedelta(minutes=40)]  # 20:20, 20:30 never published
        for ts in times:
            self.collect(ts)
        self.assert_consistent(times)
        self.assertEqual(self.history()["count"], 3)

    def test_locked_old_file_does_not_fail_collection(self):
        times = [T0 + timedelta(minutes=10 * k) for k in range(14)]
        for ts in times[:13]:
            self.collect(ts)
        real_unlink = Path.unlink

        def locked(path, missing_ok=False):
            if path.name == "radar_202610032000.png":
                raise PermissionError("file in use")
            return real_unlink(path, missing_ok=missing_ok)

        with mock.patch.object(Path, "unlink", locked):
            result = self.collect(times[13])
        self.assertTrue(result.added)
        self.assertIsNone(result.error)
        self.assertEqual(self.db_stamps(), [t.isoformat() for t in times[1:]])  # row removed
        self.assertIn("radar_202610032000.png", self.png_files())  # file still locked
        self.assertNotIn("2026-10-03T20:00:00+08:00", [f["timestamp"] for f in self.history()["frames"]])
        self.collect(times[13] + timedelta(minutes=10))  # next prune retries the file
        self.assertNotIn("radar_202610032000.png", self.png_files())


class DuplicateAndConsistencyTests(LifecycleTestCase):
    def test_case_a_record_and_png_is_valid(self):
        self.collect(T0)
        self.assert_consistent([T0])
        url = self.history()["frames"][0]["image_url"]
        image = self.client().get(url)
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.mimetype, "image/png")
        image.close()

    def test_case_d_same_timestamp_twice_is_one_frame(self):
        self.assertTrue(self.collect(T0).added)
        second = self.collect(T0)
        self.assertFalse(second.added)
        self.assertIsNone(second.error)
        self.assertEqual(self.s3.png_downloads, 1)
        self.assert_consistent([T0])
        # The table itself refuses a second row for the same timestamp.
        self.assertFalse(db.insert_radar_frame(T0.isoformat(), "radar/x.png", radar.SOURCE, self.db_path))
        self.assertEqual(len(self.db_stamps()), 1)

    def test_case_b_record_without_png(self):
        self.collect(T0)
        self.collect(T0 + timedelta(minutes=10))
        (radar.frame_dir(self.data_dir) / "radar_202610032010.png").unlink()
        self.s3.fail = "network"  # CWA cannot restore it right now
        body = self.history()  # must not crash
        self.assertEqual([f["timestamp"] for f in body["frames"]], [T0.isoformat()])
        self.assertEqual(body["latest_timestamp"], T0.isoformat())
        self.assertEqual(self.client().get("/api/radar/frames/radar_202610032010.png").status_code, 404)

    def test_case_b_missing_png_is_restored_when_cwa_still_serves_it(self):
        self.collect(T0)
        (radar.frame_dir(self.data_dir) / "radar_202610032000.png").unlink()
        self.collect(T0)
        self.assertEqual(self.s3.png_downloads, 2)
        self.assert_consistent([T0])

    def test_case_c_png_without_record_is_not_a_frame(self):
        self.collect(T0 + timedelta(minutes=10))
        orphan = radar.frame_dir(self.data_dir) / "radar_202610032000.png"
        orphan.write_bytes(PNG)
        body = self.history()
        self.assertEqual([f["timestamp"] for f in body["frames"]], [(T0 + timedelta(minutes=10)).isoformat()])
        self.assertEqual(self.client().get("/api/radar/frames/radar_202610032000.png").status_code, 404)
        self.collect(T0 + timedelta(minutes=20))  # the next prune removes the stray file
        self.assertFalse(orphan.exists())

    def test_history_api_contract(self):
        times = [T0 + timedelta(minutes=10 * k) for k in range(4)]
        for ts in times:
            self.collect(ts)
        body = self.history()
        stamps = [f["timestamp"] for f in body["frames"]]
        self.assertEqual(stamps, sorted(stamps, key=datetime.fromisoformat))
        self.assertEqual(len(stamps), len(set(stamps)))
        self.assertEqual(body["latest_timestamp"], stamps[-1])
        self.assertEqual(body["count"], len(body["frames"]))
        self.assertEqual(body["source"], "O-A0058-005")
        self.assertEqual(body["projection"], "EPSG:3857")
        self.assertEqual(body["bounds"], [[17.75, 115.0], [29.25, 126.5]])
        client = self.client()
        for f in body["frames"]:
            self.assertEqual(f["source"], "O-A0058-005")
            self.assertEqual(f["image_url"], f"/api/radar/frames/{radar.frame_file_name(f['timestamp'])}")
            image = client.get(f["image_url"])
            self.assertEqual(image.status_code, 200)
            image.close()


class RestartTests(LifecycleTestCase):
    def test_restart_reuses_stored_frames(self):
        times = [T0, T0 + timedelta(minutes=10)]
        for ts in times:
            self.collect(ts)
        before = {p.name: p.stat().st_mtime_ns for p in radar.frame_dir(self.data_dir).iterdir()}
        # New process: module state is fresh, SQLite and files are what is on disk.
        radar._last_attempt.clear()
        radar._last_error.clear()
        db.init_db(self.db_path)
        result = self.collect()  # startup run; CWA still serves 20:10
        self.assertFalse(result.added)
        self.assertIsNone(result.error)
        self.assertEqual(self.s3.png_downloads, 2)  # nothing downloaded again
        self.assertEqual({p.name: p.stat().st_mtime_ns for p in radar.frame_dir(self.data_dir).iterdir()}, before)
        self.assert_consistent(times)


class ConcurrencyTests(LifecycleTestCase):
    def test_simultaneous_history_requests_and_collections_download_once(self):
        self.s3.png_delay = 0.2
        results, errors = [], []

        def request():
            try:
                results.append(self.history(self.client()))
            except Exception as e:  # surfaced below
                errors.append(e)

        def collect():
            try:
                results.append(self.collect())
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=request) for _ in range(6)] + [threading.Thread(target=collect) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(self.s3.png_downloads, 1)
        self.assert_consistent([T0])
        for r in results:
            if isinstance(r, dict):
                self.assertEqual([f["timestamp"] for f in r["frames"]], [T0.isoformat()])
            else:
                self.assertIsNone(r.error)


class CwaFailureTests(LifecycleTestCase):
    """A failed CWA request never removes stored frames; the API reports it."""

    def setUp(self):
        super().setUp()
        self.times = [T0, T0 + timedelta(minutes=10)]
        for ts in self.times:
            self.collect(ts)
        self.s3.publish(T0 + timedelta(minutes=20))  # the next frame is on CWA
        self.downloads = self.s3.png_downloads

    def assert_failure_keeps_frames(self, expected_error: str):
        result = self.collect()
        self.assertFalse(result.added)
        self.assertIn(expected_error, result.error)
        self.assert_consistent(self.times)
        # Through the API: ensure_recent tries CWA, fails, and says so.
        radar._last_attempt.clear()
        body = self.history()
        self.assertIn(expected_error, body["refresh_error"])
        self.assertEqual([f["timestamp"] for f in body["frames"]], [t.isoformat() for t in self.times])

    def test_cwa_unavailable(self):
        self.s3.fail = "network"
        self.assert_failure_keeps_frames("Network error")

    def test_cwa_http_error(self):
        self.s3.fail = "http"
        self.assert_failure_keeps_frames("HTTP 503")

    def test_invalid_json_metadata(self):
        self.s3.fail = "json"
        self.assert_failure_keeps_frames("not valid JSON")

    def test_unexpected_metadata_structure(self):
        self.s3.fail = "structure"
        self.assert_failure_keeps_frames("Unexpected O-A0058-005 metadata structure")

    def test_missing_last_modified(self):
        self.s3.fail = "no_last_modified"
        self.assert_failure_keeps_frames("Last-Modified")

    def test_invalid_png(self):
        self.s3.png_body = b"\x89PNG\r\n\x1a\ngarbage"
        self.assert_failure_keeps_frames("Invalid O-A0058-005 PNG")
        self.assertNotIn("radar_202610032020.png", self.png_files())

    def test_png_from_another_update(self):
        # JSON already says 20:20 but the PNG is still the 20:10 upload.
        self.s3.publish(T0 + timedelta(minutes=20), png_uploaded=self.s3.json_modified - timedelta(minutes=10))
        self.assert_failure_keeps_frames("will retry")

    def test_metadata_changes_during_download(self):
        # CWA publishes the next frame during every PNG download.
        self.s3.on_png = lambda: self.s3.publish(self.s3.ts + timedelta(minutes=10))
        self.assert_failure_keeps_frames("will retry")

    def test_recovers_after_failure(self):
        self.s3.fail = "network"
        self.assertIsNotNone(self.collect().error)
        self.s3.fail = None
        self.assertTrue(self.collect().added)
        self.assert_consistent(self.times + [T0 + timedelta(minutes=20)])


if __name__ == "__main__":
    unittest.main()
