"""RadarFrame metadata storage (backend.db) on the configured backend.

Runs on SQLite by default and on PostgreSQL through tests/run_postgres.py.
Run: python -m unittest tests.test_radar_frame_db
"""
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from tests.test_radar import FakeCWA  # also sets RADAR_COLLECTOR=0
from backend import db, radar
from tests.db_support import POSTGRES, fresh_db
from tests.radar_support import RadarStorageMixin

TAIPEI = timezone(timedelta(hours=8))
T0 = datetime(2026, 10, 3, 20, 0, tzinfo=TAIPEI)
SOURCE = "O-A0058-005"


def ts(minutes: int) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


class RadarFrameStoreTestCase(RadarStorageMixin, unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.db_path = self.data_dir / "weather.db"
        fresh_db(self.db_path)
        self.start_storage()

    def tearDown(self):
        self.tmp.cleanup()

    def insert(self, minutes: int) -> bool:
        return db.insert_radar_frame(ts(minutes), f"radar/{radar.frame_file_name(ts(minutes))}", SOURCE, self.db_path)

    def timestamps(self) -> list[str]:
        return [f["timestamp"] for f in db.get_radar_frames(self.db_path)]


class SchemaTests(RadarFrameStoreTestCase):
    def test_table_is_created_on_the_configured_backend(self):
        if POSTGRES:
            columns = db._query(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'radarframe' ORDER BY ordinal_position")
            self.assertEqual([(c["column_name"], c["data_type"], c["is_nullable"]) for c in columns], [
                ("timestamp", "text", "NO"), ("file_path", "text", "NO"), ("source", "text", "NO")])
            key = db._query(
                "SELECT a.attname AS name FROM pg_index i JOIN pg_attribute a "
                "ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
                "WHERE i.indrelid = 'radarframe'::regclass AND i.indisprimary")
            self.assertEqual(key, [{"name": "timestamp"}])
            self.assertFalse(self.db_path.exists())  # postgres mode creates no SQLite file
        else:
            conn = sqlite3.connect(self.db_path)
            try:
                columns = [(r[1], r[2], r[3], r[5]) for r in conn.execute("PRAGMA table_info(RadarFrame)")]
            finally:
                conn.close()
            self.assertEqual(columns, [("timestamp", "TEXT", 0, 1), ("file_path", "TEXT", 1, 0), ("source", "TEXT", 1, 0)])

    def test_init_db_is_repeatable_and_keeps_frames(self):
        self.insert(0)
        db.init_db(self.db_path)
        db.init_db(self.db_path)
        self.assertEqual(self.timestamps(), [ts(0)])


class CrudTests(RadarFrameStoreTestCase):
    def test_insert_stores_all_fields(self):
        self.assertTrue(self.insert(0))
        self.assertTrue(db.radar_frame_exists(ts(0), self.db_path))
        self.assertFalse(db.radar_frame_exists(ts(10), self.db_path))
        self.assertEqual(db.get_radar_frames(self.db_path), [
            {"timestamp": ts(0), "file_path": "radar/radar_202610032000.png", "source": SOURCE}])

    def test_history_is_oldest_first_and_latest_is_last(self):
        for minutes in (20, 0, 40, 10, 30):
            self.insert(minutes)
        self.assertEqual(self.timestamps(), [ts(m) for m in (0, 10, 20, 30, 40)])
        self.assertEqual(db.get_radar_frames(self.db_path)[-1]["timestamp"], ts(40))

    def test_duplicate_timestamp_is_ignored_and_keeps_the_first_row(self):
        self.assertTrue(self.insert(0))
        self.assertFalse(db.insert_radar_frame(ts(0), "radar/other.png", "other", self.db_path))
        self.assertEqual(db.get_radar_frames(self.db_path), [
            {"timestamp": ts(0), "file_path": "radar/radar_202610032000.png", "source": SOURCE}])

    def test_concurrent_inserts_of_one_timestamp_store_one_row_without_errors(self):
        # Like several instances storing the same CWA frame at once, each with its own connection.
        workers = 8
        barrier = threading.Barrier(workers)
        results, errors = [], []

        def store():
            barrier.wait()
            try:
                results.append(self.insert(0))
            except Exception as e:  # noqa: BLE001 - any error fails the test below
                errors.append(e)

        threads = [threading.Thread(target=store) for _ in range(workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), [False] * (workers - 1) + [True])
        self.assertEqual(self.timestamps(), [ts(0)])

    def test_delete_removes_only_the_given_frames(self):
        for minutes in (0, 10, 20):
            self.insert(minutes)
        db.delete_radar_frames([ts(0), ts(20), ts(99)], self.db_path)  # unknown timestamps are ignored
        self.assertEqual(self.timestamps(), [ts(10)])
        db.delete_radar_frames([], self.db_path)
        self.assertEqual(self.timestamps(), [ts(10)])

    def test_prune_deletes_frames_older_than_two_hours_before_the_newest(self):
        for minutes in range(0, 190, 10):  # 0 .. 3h00, 19 frames
            self.insert(minutes)
        removed = radar.prune(self.db_path, self.data_dir)
        self.assertEqual(removed, [ts(m) for m in range(0, 60, 10)])
        self.assertEqual(self.timestamps(), [ts(m) for m in range(60, 190, 10)])  # 13 frames: 1h00 .. 3h00


class CollectErrorTests(RadarFrameStoreTestCase):
    def test_database_error_is_reported_and_leaves_no_png(self):
        cwa = FakeCWA(T0)
        error = db.DBError[-1]("database unavailable")  # psycopg.Error on PostgreSQL, sqlite3.Error on SQLite
        with mock.patch.object(db, "insert_radar_frame", side_effect=error):
            result = radar.collect(self.db_path, self.data_dir, fetch_metadata=cwa.fetch_metadata,
                                   fetch_png=cwa.fetch_png, now=lambda: T0 + timedelta(minutes=8))
        self.assertFalse(result.added)
        self.assertEqual(result.error, f"Could not store radar frame: {type(error).__name__}")
        self.assertEqual(self.stored_names(), [])
        self.assertEqual(self.timestamps(), [])


if __name__ == "__main__":
    unittest.main()
