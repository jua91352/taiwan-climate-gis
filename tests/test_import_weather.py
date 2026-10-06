"""Tests for backend.import_weather_to_postgres (SQLite -> PostgreSQL history import).

PostgreSQL only: run through tests/run_postgres.py (skipped on SQLite).
The source is a temporary SQLite file built here; data/weather.db is never read.
"""
import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend import cwa_api, db
from backend import import_weather_to_postgres as importer
from tests.db_support import POSTGRES, fresh_db

T1, T2 = "2026-10-04T14:50:00+08:00", "2026-10-04T15:00:00+08:00"


def source_row(station_id: str, county: str, obs: str, temperature: float | None, weather: str | None):
    return {"station_id": station_id, "station_name": f"站{station_id}", "county_name": county, "town_name": "測試區",
            "latitude": 23.5, "longitude": 119.5, "observation_time": obs, "temperature": temperature,
            "humidity": 80.0, "wind_speed": 2.3, "wind_direction": 45.0, "uv_index": 0.0, "precipitation": 0.0,
            "weather": weather}


@unittest.skipUnless(POSTGRES, "imports into PostgreSQL; run tests/run_postgres.py")
class ImportTests(unittest.TestCase):
    ROWS = [
        source_row("P1", "澎湖縣", T1, 27.1, "晴"),
        source_row("P1", "澎湖縣", T2, 27.4, None),
        source_row("P2", "澎湖縣", T2, None, "多雲"),
        source_row("K1", "高雄市", T2, 29.0, "晴"),
    ]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        fresh_db(Path(self.tmp.name) / "unused.db")
        self.source = Path(self.tmp.name) / "source.db"
        conn = sqlite3.connect(self.source)
        conn.executescript(db.SQLITE_WEATHER_SCHEMA + db.RADAR_SCHEMA)
        for r in self.ROWS:
            conn.execute("INSERT OR IGNORE INTO Station VALUES (?, ?, ?, ?, ?, ?)",
                         (r["station_id"], r["station_name"], r["county_name"], r["town_name"], r["latitude"], r["longitude"]))
            conn.execute("INSERT INTO WeatherObservation (station_id, observation_time, temperature, humidity, wind_speed,"
                         " wind_direction, uv_index, precipitation, weather) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (r["station_id"], r["observation_time"], r["temperature"], r["humidity"], r["wind_speed"],
                          r["wind_direction"], r["uv_index"], r["precipitation"], r["weather"]))
        conn.execute("INSERT INTO RadarFrame VALUES (?, ?, ?)", (T2, "radar/x.png", "O-A0058-005"))
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def digest(self) -> str:
        return hashlib.sha256(self.source.read_bytes()).hexdigest()

    def target_rows(self) -> list[dict]:
        return db._query("SELECT station_id, observation_time, temperature, weather FROM WeatherObservation"
                         " ORDER BY observation_time, station_id")

    def test_dry_run_counts_and_writes_nothing(self):
        before = self.digest()
        report = importer.run(self.source)
        self.assertEqual(report["source"]["stations"], 3)
        self.assertEqual(report["source"]["observations"], 4)
        self.assertEqual((report["target_before"]["stations"], report["target_before"]["observations"]), (0, 0))
        self.assertEqual(report["plan"]["observations_to_insert"], 4)
        self.assertEqual(report["plan"]["batches"], 2)
        self.assertNotIn("target_after", report)
        self.assertEqual(self.target_rows(), [])
        self.assertEqual(self.digest(), before)

    def test_import_copies_every_row_and_leaves_the_source_unchanged(self):
        before = self.digest()
        report = importer.run(self.source, execute=True)
        self.assertTrue(report["verification"]["ok"])
        self.assertEqual((report["target_after"]["stations"], report["target_after"]["observations"]), (3, 4))
        self.assertEqual(self.target_rows(), [
            {"station_id": "P1", "observation_time": T1, "temperature": 27.1, "weather": "晴"},
            {"station_id": "K1", "observation_time": T2, "temperature": 29.0, "weather": "晴"},
            {"station_id": "P1", "observation_time": T2, "temperature": 27.4, "weather": None},
            {"station_id": "P2", "observation_time": T2, "temperature": None, "weather": "多雲"},
        ])
        self.assertTrue(db.county_exists("澎湖縣"))
        self.assertEqual(self.digest(), before)
        # The source's RadarFrame row is not imported.
        self.assertEqual(db.get_radar_frames(), [])

    def test_running_again_adds_nothing(self):
        importer.run(self.source, execute=True)
        first = self.target_rows()
        report = importer.run(self.source, execute=True)
        self.assertEqual(report["plan"]["observations_to_insert"], 0)
        self.assertEqual(report["plan"]["observations_already_present"], 4)
        self.assertEqual(report["saved"]["observations_inserted"], 0)
        self.assertEqual(report["target_after"]["observations"], 4)
        self.assertEqual(report["target_after"]["duplicate_pairs"], 0)
        self.assertEqual(self.target_rows(), first)

    def test_rows_already_in_postgres_keep_their_values(self):
        # The live app already stored P1@T2 (other temperature, no weather text) and P2@T2 (with text).
        live = [cwa_api.normalize_station({
            "StationId": sid, "StationName": f"站{sid}", "ObsTime": {"DateTime": T2},
            "GeoInfo": {"Coordinates": [{"CoordinateName": "WGS84", "StationLatitude": "23.5", "StationLongitude": "119.5"}],
                        "CountyName": "澎湖縣", "TownName": "測試區"},
            "WeatherElement": {"AirTemperature": temp, "Weather": text},
        }) for sid, temp, text in (("P1", "26.0", "-99"), ("P2", "25.0", "陰"))]
        db.save_observations(live)
        report = importer.run(self.source, execute=True)
        self.assertEqual(report["plan"]["observations_to_insert"], 2)
        rows = {(r["station_id"], r["observation_time"]): r for r in self.target_rows()}
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[("P1", T2)]["temperature"], 26.0)  # existing measurement kept
        self.assertEqual(rows[("P2", T2)]["weather"], "陰")  # existing text never overwritten


if __name__ == "__main__":
    unittest.main()
