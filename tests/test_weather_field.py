"""Tests for the O-A0003-001 Weather text field (parser, SQLite migration, storage).

Run: python -m unittest tests.test_weather_field
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend import cwa_api, db

OLD_SCHEMA = """
CREATE TABLE Station (station_id TEXT PRIMARY KEY, station_name TEXT NOT NULL, county_name TEXT,
    town_name TEXT, latitude REAL, longitude REAL);
CREATE TABLE WeatherObservation (id INTEGER PRIMARY KEY AUTOINCREMENT, station_id TEXT NOT NULL,
    observation_time TEXT NOT NULL, temperature REAL, humidity REAL, wind_speed REAL,
    wind_direction REAL, uv_index REAL, precipitation REAL,
    FOREIGN KEY (station_id) REFERENCES Station(station_id));
INSERT INTO Station VALUES ('466920', '臺北', '臺北市', '中正區', 25.0, 121.5);
INSERT INTO WeatherObservation (station_id, observation_time, temperature)
    VALUES ('466920', '2026-10-02T22:20:00+08:00', 25.1);
"""


def station(weather, obs="2026-10-03T22:30:00+08:00"):
    return {
        "StationName": "臺北", "StationId": "466920", "ObsTime": {"DateTime": obs},
        "GeoInfo": {"Coordinates": [{"CoordinateName": "WGS84", "StationLatitude": "25.0", "StationLongitude": "121.5"}],
                    "CountyName": "臺北市", "TownName": "中正區"},
        "WeatherElement": {"Weather": weather, "AirTemperature": "25.1"},
    }


class WeatherFieldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "weather.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_parser_keeps_text_and_drops_missing_values(self):
        self.assertEqual(cwa_api.normalize_station(station("晴"))["weather"], "晴")
        self.assertEqual(cwa_api.normalize_station(station(" 陰有雨 "))["weather"], "陰有雨")
        for missing in ("-99", "-99.0", "", "X", None):
            self.assertIsNone(cwa_api.normalize_station(station(missing))["weather"], missing)

    def test_migration_adds_nullable_column_and_keeps_history(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript(OLD_SCHEMA)
        conn.close()
        db.init_db(self.db_path)
        db.init_db(self.db_path)  # idempotent
        rows = db._query("SELECT observation_time, temperature, weather FROM WeatherObservation", db_path=self.db_path)
        self.assertEqual(rows, [{"observation_time": "2026-10-02T22:20:00+08:00", "temperature": 25.1, "weather": None}])
        latest = db.get_latest_observations(self.db_path)
        self.assertIn("weather", latest[0])

    def test_init_db_indexes_latest_observation_lookup(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript(OLD_SCHEMA)  # existing database without the index
        conn.close()
        db.init_db(self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
            plan = " ".join(str(r) for r in conn.execute("EXPLAIN QUERY PLAN " + db._LATEST_PER_STATION))
        finally:
            conn.close()
        self.assertIn("idx_observation_station_time", names)
        self.assertIn("idx_observation_station_time", plan)

    def test_new_batch_stores_weather_and_duplicates_only_fill_missing(self):
        db.init_db(self.db_path)
        rec = cwa_api.normalize_station(station(None))
        db.save_observations([rec], self.db_path)
        self.assertIsNone(db.get_latest_observations(self.db_path)[0]["weather"])
        # Same station/time again, now with weather: the missing text is filled in.
        stats = db.save_observations([cwa_api.normalize_station(station("多雲"))], self.db_path)
        self.assertEqual(stats["observations_inserted"], 0)
        self.assertEqual(db.get_latest_observations(self.db_path)[0]["weather"], "多雲")
        # An existing text is never overwritten.
        db.save_observations([cwa_api.normalize_station(station("晴"))], self.db_path)
        self.assertEqual(db.get_latest_observations(self.db_path)[0]["weather"], "多雲")
        self.assertEqual(db._query("SELECT COUNT(*) n FROM WeatherObservation", db_path=self.db_path)[0]["n"], 1)


if __name__ == "__main__":
    unittest.main()
