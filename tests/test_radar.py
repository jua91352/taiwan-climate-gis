"""Tests for the O-A0058-005 radar collector, PNG reprojection and /api/radar API.

Network calls are replaced by fakes; each test uses its own temporary SQLite
file and frame directory.
Run: python -m unittest tests.test_radar
"""
import os
import random
import struct
import tempfile
import threading
import time
import unittest
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

os.environ["RADAR_COLLECTOR"] = "0"  # importing backend.app must not start the real collector

from backend import db, radar, radar_png  # noqa: E402
from tests.db_support import fresh_db  # noqa: E402
from tests.radar_support import RadarStorageMixin, local_only  # noqa: E402

TAIPEI = timezone(timedelta(hours=8))
T0 = datetime(2026, 10, 3, 21, 0, tzinfo=TAIPEI)


def encode_with_filters(width: int, rows: list[bytes], filters: list[int]) -> bytes:
    """Reference PNG encoder applying the given filter type to each row."""
    raw, prev = bytearray(), bytes(len(rows[0]))
    for row, kind in zip(rows, filters):
        out = bytearray()
        for i, x in enumerate(row):
            a = row[i - 4] if i >= 4 else 0
            b = prev[i]
            c = prev[i - 4] if i >= 4 else 0
            if kind == 0:
                pred = 0
            elif kind == 1:
                pred = a
            elif kind == 2:
                pred = b
            elif kind == 3:
                pred = (a + b) >> 1
            else:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
            out.append((x - pred) & 0xFF)
        raw += bytes([kind]) + out
        prev = row
    header = struct.pack(">IIBBBBB", width, len(rows), 8, 6, 0, 0, 0)
    return (radar_png.PNG_SIGNATURE + radar_png._chunk(b"IHDR", header)
            + radar_png._chunk(b"IDAT", zlib.compress(bytes(raw))) + radar_png._chunk(b"IEND", b""))


def sparse_rows(width: int, height: int, seed: int) -> list[bytes]:
    """Mostly transparent rows with scattered blobs, like the radar image."""
    rng = random.Random(seed)
    rows = []
    for _ in range(height):
        row = bytearray(width * 4)
        for _ in range(rng.randint(0, 3)):
            start = rng.randrange(width)
            for x in range(start, min(width, start + rng.randint(1, 6))):
                row[4 * x:4 * x + 4] = bytes(rng.randrange(256) for _ in range(4))
        rows.append(bytes(row))
    return rows


class PNGTests(unittest.TestCase):
    def test_decodes_every_filter_type(self):
        width, height = 37, 60
        rows = sparse_rows(width, height, seed=1)
        rows[5] = bytes(random.Random(2).randrange(256) for _ in range(width * 4))  # dense row
        filters = [y % 5 for y in range(height)]
        w, h, decoded = radar_png.decode_rgba(encode_with_filters(width, rows, filters))
        self.assertEqual((w, h), (width, height))
        self.assertEqual(decoded, rows)

    def test_paeth_rows_below_echo(self):
        width = 50
        rows = sparse_rows(width, 40, seed=3)
        _, _, decoded = radar_png.decode_rgba(encode_with_filters(width, rows, [4] * 40))
        self.assertEqual(decoded, rows)

    def test_encode_round_trip(self):
        rows = sparse_rows(20, 15, seed=4)
        self.assertEqual(radar_png.decode_rgba(radar_png.encode_rgba(20, rows)), (20, 15, rows))

    def test_rejects_unsupported_png(self):
        header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)  # RGB, not RGBA
        data = (radar_png.PNG_SIGNATURE + radar_png._chunk(b"IHDR", header)
                + radar_png._chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")))
        with self.assertRaises(radar_png.PNGError):
            radar_png.decode_rgba(data)
        with self.assertRaises(radar_png.PNGError):
            radar_png.decode_rgba(b"not a png")

    def test_mercator_row_map(self):
        mapping = radar_png.mercator_row_map(3600, 17.75, 29.25, 115.0, 126.5, 3600)
        self.assertEqual(len(mapping), 3935)  # taller than the source: square Mercator pixels
        self.assertEqual(mapping[0], 0)
        self.assertEqual(mapping[-1], 3599)
        self.assertEqual(mapping, sorted(mapping))
        # Output row at Taipei's latitude (25.03 N) samples the source row at that latitude.
        y_top, y_bottom = radar_png.mercator_y(29.25), radar_png.mercator_y(17.75)
        j = int((y_top - radar_png.mercator_y(25.03)) / (y_top - y_bottom) * 3935)
        self.assertAlmostEqual(mapping[j], (29.25 - 25.03) / 11.5 * 3600, delta=1.5)


def metadata(ts: datetime, etag: str | None = None) -> radar.Metadata:
    modified = (ts + timedelta(minutes=7)).astimezone(timezone.utc)
    return radar.Metadata(ts.isoformat(), etag or f'"{ts:%H%M}"', modified)


class FakeCWA:
    """Serves one frame; counts downloads."""

    def __init__(self, ts: datetime):
        self.meta = [metadata(ts)]  # successive fetch_metadata results (last one repeats)
        self.png = encode_with_filters(4, sparse_rows(4, 6, seed=5), [2] * 6)
        self.png_modified = self.meta[0].last_modified
        self.png_calls = 0
        self.fail = None

    def fetch_metadata(self):
        if self.fail:
            raise radar.RadarError(self.fail)
        return self.meta.pop(0) if len(self.meta) > 1 else self.meta[0]

    def fetch_png(self):
        self.png_calls += 1
        time.sleep(0.01)
        return self.png, self.png_modified


class CollectorTestCase(RadarStorageMixin, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.db_path = self.data_dir / "weather.db"
        fresh_db(self.db_path)
        self.start_storage()
        self.cwa = FakeCWA(T0)

    def tearDown(self):
        self.tmp.cleanup()

    def collect(self, now=T0 + timedelta(minutes=8)):
        return radar.collect(self.db_path, self.data_dir, fetch_metadata=self.cwa.fetch_metadata,
                             fetch_png=self.cwa.fetch_png, now=lambda: now)

    def frames(self):
        return db.get_radar_frames(self.db_path)

    def files(self):
        return self.stored_names()


class CollectTests(CollectorTestCase):
    def test_stores_reprojected_png_and_metadata(self):
        result = self.collect()
        self.assertEqual(result, radar.CollectResult(added=True, timestamp=T0.isoformat(), error=None))
        self.assertEqual(self.frames(), [{"timestamp": "2026-10-03T21:00:00+08:00",
                                          "file_path": "radar/radar_202610032100.png", "source": "O-A0058-005"}])
        _, _, source_rows = radar_png.decode_rgba(self.cwa.png)
        width, _, rows = radar_png.decode_rgba(self.stored_bytes("radar_202610032100.png"))
        mapping = radar_png.mercator_row_map(6, 17.75, 29.25, 115.0, 126.5, 4)
        self.assertEqual(width, 4)
        self.assertEqual(rows, [source_rows[i] for i in mapping])  # Web Mercator row remap

    def test_same_timestamp_is_not_downloaded_again(self):
        self.collect()
        result = self.collect()
        self.assertFalse(result.added)
        self.assertIsNone(result.error)
        self.assertEqual(self.cwa.png_calls, 1)
        self.assertEqual(len(self.frames()), 1)

    def test_failure_keeps_existing_frames(self):
        self.collect()
        self.cwa.fail = "CWA returned HTTP 503 for O-A0058-005.json"
        result = self.collect()
        self.assertEqual(result.error, "CWA returned HTTP 503 for O-A0058-005.json")
        self.assertEqual(len(self.frames()), 1)
        self.assertEqual(self.files(), ["radar_202610032100.png"])

    def test_invalid_png_stores_nothing(self):
        self.cwa.png = b"\x89PNG\r\n\x1a\nbroken"
        result = self.collect()
        self.assertIn("Invalid O-A0058-005 PNG", result.error)
        self.assertEqual(self.frames(), [])
        self.assertEqual(self.files(), [])
        self.assertFalse(radar.frame_dir(self.data_dir).exists())

    def test_metadata_changing_during_download_is_rejected(self):
        self.cwa.meta = [metadata(T0), metadata(T0 + timedelta(minutes=10))]
        result = self.collect()
        self.assertIsNotNone(result.error)
        self.assertEqual(self.frames(), [])

    def test_png_from_another_upload_is_rejected(self):
        # Metadata already announces the new frame but the PNG is still the old one.
        self.cwa.png_modified -= timedelta(minutes=10)
        result = self.collect()
        self.assertIn("will retry", result.error)
        self.assertEqual(self.frames(), [])

    def test_keeps_two_hours_and_at_most_13_frames(self):
        for k in range(16):
            ts = T0 + timedelta(minutes=10 * k)
            self.cwa.meta = [metadata(ts)]
            self.cwa.png_modified = self.cwa.meta[0].last_modified
            self.assertTrue(self.collect(now=ts + timedelta(minutes=8)).added)
        stamps = [f["timestamp"] for f in self.frames()]
        self.assertEqual(len(stamps), 13)
        self.assertEqual(stamps[0], (T0 + timedelta(minutes=30)).isoformat())
        self.assertEqual(stamps[-1], (T0 + timedelta(minutes=150)).isoformat())
        self.assertEqual(len(self.files()), 13)

    def test_gap_older_than_two_hours_is_pruned(self):
        self.collect()
        later = T0 + timedelta(hours=3)
        self.cwa.meta = [metadata(later)]
        self.cwa.png_modified = self.cwa.meta[0].last_modified
        self.collect(now=later + timedelta(minutes=8))
        self.assertEqual([f["timestamp"] for f in self.frames()], [later.isoformat()])
        self.assertEqual(self.files(), ["radar_202610040000.png"])

    def test_concurrent_collections_download_once(self):
        threads = [threading.Thread(target=self.collect) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(self.cwa.png_calls, 1)
        self.assertEqual(len(self.frames()), 1)


class EnsureRecentTests(CollectorTestCase):
    def ensure(self, now):
        return radar.ensure_recent(self.db_path, self.data_dir, fetch_metadata=self.cwa.fetch_metadata,
                                   fetch_png=self.cwa.fetch_png, now=lambda: now)

    def test_recent_frame_skips_cwa(self):
        self.collect()
        self.cwa.fail = "should not be called"
        self.assertIsNone(self.ensure(T0 + timedelta(minutes=9)))

    def test_old_frame_collects_with_cooldown(self):
        self.collect()
        self.cwa.fail = "CWA returned HTTP 503 for O-A0058-005.png"
        now = T0 + timedelta(minutes=15)
        self.assertEqual(self.ensure(now), "CWA returned HTTP 503 for O-A0058-005.png")
        self.cwa.fail = None
        self.cwa.meta = [metadata(T0 + timedelta(minutes=10))]
        self.cwa.png_modified = self.cwa.meta[0].last_modified
        # Within the cooldown: no new attempt, the last error is reported.
        self.assertIsNotNone(self.ensure(now + timedelta(seconds=30)))
        self.assertEqual(self.cwa.png_calls, 1)
        self.assertIsNone(self.ensure(now + radar.RETRY_COOLDOWN))
        self.assertEqual(len(self.frames()), 2)


class HistoryAPITests(CollectorTestCase):
    def setUp(self):
        super().setUp()
        from backend.app import create_app
        patches = [mock.patch.object(db, "DB_PATH", self.db_path),
                   mock.patch.object(radar, "DATA_DIR", self.data_dir),
                   mock.patch.object(radar, "ensure_recent", lambda *a, **k: None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.client = create_app().test_client()

    def test_empty_history(self):
        body = self.client.get("/api/radar/history").get_json()
        self.assertEqual(body["frames"], [])
        self.assertIsNone(body["latest_timestamp"])

    def test_history_lists_frames_and_serves_images(self):
        for k in range(2):
            ts = T0 + timedelta(minutes=10 * k)
            self.cwa.meta = [metadata(ts)]
            self.cwa.png_modified = self.cwa.meta[0].last_modified
            self.collect(now=ts)
        body = self.client.get("/api/radar/history").get_json()
        self.assertEqual(body["latest_timestamp"], "2026-10-03T21:10:00+08:00")
        self.assertEqual(body["bounds"], [[17.75, 115.0], [29.25, 126.5]])
        self.assertEqual(body["frames"], [
            {"timestamp": "2026-10-03T21:00:00+08:00", "image_url": "/api/radar/frames/radar_202610032100.png",
             "source": "O-A0058-005"},
            {"timestamp": "2026-10-03T21:10:00+08:00", "image_url": "/api/radar/frames/radar_202610032110.png",
             "source": "O-A0058-005"},
        ])
        image = self.fetch_image(self.client, body["frames"][0]["image_url"])
        self.assertTrue(image.startswith(radar_png.PNG_SIGNATURE))

    @local_only
    def test_missing_file_is_not_listed(self):
        self.collect()
        (self.data_dir / "radar/radar_202610032100.png").unlink()
        self.assertEqual(self.client.get("/api/radar/history").get_json()["frames"], [])
        self.assertEqual(self.client.get("/api/radar/frames/radar_202610032100.png").status_code, 404)

    def test_unknown_frame_is_404(self):
        self.assertEqual(self.client.get("/api/radar/frames/..%2Fweather.db").status_code, 404)
        self.assertEqual(self.client.get("/api/radar/frames/radar_209901010000.png").status_code, 404)


if __name__ == "__main__":
    unittest.main()
