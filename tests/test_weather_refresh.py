"""Tests for backend.weather_refresh (O-A0003-001 auto refresh on read).

Each test uses its own temporary SQLite file (or emptied local PostgreSQL
tables, see tests.db_support) and a fake CWA payload in place
of the network call; the real ingest -> parse -> save path is exercised.
Run: python -m unittest tests.test_weather_refresh
"""
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from backend import cwa_api, db
from tests.db_support import fresh_db
from backend.weather_refresh import FRESHNESS_THRESHOLD, RETRY_COOLDOWN, ensure_fresh, is_fresh

TAIPEI = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 3, 21, 30, tzinfo=TAIPEI)


def cwa_payload(obs_time: datetime, stations=(("466920", "臺北", "臺北市"), ("467490", "臺中", "臺中市"))) -> dict:
    """Minimal O-A0003-001 JSON with the real field names."""
    return {"cwaopendata": {"dataset": {"Station": [
        {
            "StationName": name,
            "StationId": sid,
            "ObsTime": {"DateTime": obs_time.isoformat()},
            "GeoInfo": {
                "Coordinates": [{"CoordinateName": "WGS84", "StationLatitude": "25.0", "StationLongitude": "121.5"}],
                "CountyName": county,
                "TownName": "測試區",
            },
            "WeatherElement": {"AirTemperature": "25.1", "RelativeHumidity": "80", "WindSpeed": "2.3",
                               "WindDirection": "45", "UVIndex": "0", "Now": {"Precipitation": "0.0"}},
        }
        for sid, name, county in stations
    ]}}}


class RefreshTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "weather.db"
        fresh_db(self.db_path)
        self.fetch_calls = 0
        self.next_payload = cwa_payload(NOW)

    def tearDown(self):
        self.tmp.cleanup()

    def seed(self, obs_time: datetime):
        records, _ = cwa_api.parse_stations(cwa_payload(obs_time))
        db.save_observations(records, self.db_path)

    def fake_fetch(self, timeout=30):
        self.fetch_calls += 1
        return self.next_payload

    def refresh(self, now=NOW):
        with mock.patch.object(cwa_api, "fetch_weather_data", self.fake_fetch):
            return ensure_fresh(self.db_path, now=lambda: now)

    def observation_count(self) -> int:
        return db._query("SELECT COUNT(*) AS n FROM WeatherObservation", db_path=self.db_path)[0]["n"]


class FreshnessTests(RefreshTestCase):
    def test_case1_5_minutes_old_uses_sqlite(self):
        self.seed(NOW - timedelta(minutes=5))
        status = self.refresh()
        self.assertEqual(self.fetch_calls, 0)
        self.assertFalse(status.updated)
        self.assertFalse(status.stale)

    def test_case2_9_minutes_old_uses_sqlite(self):
        self.seed(NOW - timedelta(minutes=9, seconds=59))
        status = self.refresh()
        self.assertEqual(self.fetch_calls, 0)
        self.assertFalse(status.stale)

    def test_case3_10_minutes_old_refreshes(self):
        self.seed(NOW - timedelta(minutes=10))
        status = self.refresh()
        self.assertEqual(self.fetch_calls, 1)
        self.assertTrue(status.updated)
        self.assertFalse(status.stale)
        self.assertEqual(status.latest_observation_time, NOW.isoformat())

    def test_case4_30_minutes_old_refreshes_and_keeps_history(self):
        self.seed(NOW - timedelta(minutes=30))
        status = self.refresh()
        self.assertEqual(self.fetch_calls, 1)
        self.assertTrue(status.updated)
        self.assertEqual(self.observation_count(), 4)  # 2 old + 2 new rows: history kept

    def test_case5_empty_database_fetches_first_batch(self):
        status = self.refresh()
        self.assertEqual(self.fetch_calls, 1)
        self.assertTrue(status.updated)
        self.assertEqual(len(db.get_stations(self.db_path)), 2)
        self.assertEqual(status.latest_observation_time, NOW.isoformat())

    def test_case6_cwa_failure_keeps_old_data_and_flags_stale(self):
        self.seed(NOW - timedelta(minutes=40))
        before = self.observation_count()

        def failing_fetch(timeout=30):
            self.fetch_calls += 1
            raise cwa_api.CWAError("CWA returned HTTP 503")

        with mock.patch.object(cwa_api, "fetch_weather_data", failing_fetch):
            status = ensure_fresh(self.db_path, now=lambda: NOW)
            self.assertFalse(status.updated)
            self.assertTrue(status.stale)
            self.assertEqual(status.error, "CWA returned HTTP 503")
            self.assertEqual(status.latest_observation_time, (NOW - timedelta(minutes=40)).isoformat())
            self.assertEqual(self.observation_count(), before)
            # Within the cooldown the next request does not hit CWA again.
            again = ensure_fresh(self.db_path, now=lambda: NOW + timedelta(seconds=30))
            self.assertEqual(self.fetch_calls, 1)
            self.assertTrue(again.stale)
            self.assertEqual(again.error, "CWA returned HTTP 503")
        # After the cooldown it tries again (and succeeds here).
        later = NOW + RETRY_COOLDOWN
        self.next_payload = cwa_payload(later)
        status = self.refresh(now=later)
        self.assertEqual(self.fetch_calls, 2)
        self.assertTrue(status.updated)
        self.assertFalse(status.stale)

    def test_cwa_not_published_yet_waits_for_cooldown(self):
        # CWA still serves the batch SQLite already has (next one not published
        # yet): nothing new, not an error, so not stale; retry after the cooldown.
        old = NOW - timedelta(minutes=11)
        self.seed(old)
        self.next_payload = cwa_payload(old)
        status = self.refresh()
        self.assertEqual(self.fetch_calls, 1)
        self.assertFalse(status.updated)
        self.assertFalse(status.stale)
        self.assertIsNone(status.error)
        waiting = self.refresh(now=NOW + timedelta(minutes=1))
        self.assertEqual(self.fetch_calls, 1)
        self.assertFalse(waiting.stale)
        self.next_payload = cwa_payload(NOW + RETRY_COOLDOWN - timedelta(minutes=1))
        published = self.refresh(now=NOW + RETRY_COOLDOWN)
        self.assertEqual(self.fetch_calls, 2)
        self.assertTrue(published.updated)

    def test_case7_concurrent_requests_share_one_refresh(self):
        self.seed(NOW - timedelta(minutes=20))

        def slow_fetch(timeout=30):
            self.fetch_calls += 1
            time.sleep(0.3)
            return self.next_payload

        barrier = threading.Barrier(8)
        results = []

        def worker():
            barrier.wait()
            results.append(ensure_fresh(self.db_path, now=lambda: NOW))

        with mock.patch.object(cwa_api, "fetch_weather_data", slow_fetch):
            threads = [threading.Thread(target=worker) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(self.fetch_calls, 1)
        self.assertEqual(sum(r.updated for r in results), 1)
        self.assertTrue(all(r.latest_observation_time == NOW.isoformat() and not r.stale for r in results))

    def test_case8_all_layers_read_the_new_batch(self):
        self.seed(NOW - timedelta(minutes=15))
        self.refresh()
        latest = db.get_latest_observations(self.db_path)  # /api/weather/latest: 氣溫, 測站點位, 風速風向
        self.assertEqual({r["observation_time"] for r in latest}, {NOW.isoformat()})
        county = db.get_latest_observations_by_county("臺北市", self.db_path)  # county badges / panel
        self.assertEqual({r["observation_time"] for r in county}, {NOW.isoformat()})


class TimezoneTests(unittest.TestCase):
    def test_utc_now_against_taipei_observation(self):
        stored = "2026-10-03T21:20:00+08:00"  # = 13:20 UTC
        self.assertTrue(is_fresh(stored, datetime(2026, 10, 3, 13, 29, 59, tzinfo=timezone.utc)))
        self.assertFalse(is_fresh(stored, datetime(2026, 10, 3, 13, 30, tzinfo=timezone.utc)))

    def test_threshold_is_ten_minutes(self):
        self.assertEqual(FRESHNESS_THRESHOLD, timedelta(minutes=10))

    def test_empty_is_not_fresh(self):
        self.assertFalse(is_fresh(None, NOW))


if __name__ == "__main__":
    unittest.main()
