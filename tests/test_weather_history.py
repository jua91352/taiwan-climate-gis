"""Historical weather data integrity: storage, duplicates, missing values,
history windows and county aggregation (/api/weather/history).

Every test uses its own temporary SQLite file, fed through the real
normalize -> save_observations path; data/weather.db is never touched.
Run: python -m unittest tests.test_weather_history
"""
import math
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from unittest import mock

os.environ.setdefault("RADAR_COLLECTOR", "0")  # importing backend.app must not start the radar collector

from backend import cwa_api, db, routes  # noqa: E402
from backend.weather_refresh import RefreshStatus  # noqa: E402

TAIPEI = timezone(timedelta(hours=8))
LATEST = datetime(2026, 10, 4, 1, 10, tzinfo=TAIPEI)
FIELDS = ("temperature", "humidity", "wind_speed", "wind_direction", "uv_index", "precipitation", "weather")


def raw_station(station_id: str, county: str, obs: datetime, **values) -> dict:
    """One O-A0003-001 Station entry with the real field names."""
    v = {"AirTemperature": "25.1", "RelativeHumidity": "80", "WindSpeed": "2.3", "WindDirection": "45",
         "UVIndex": "0", "Precipitation": "0.0", "Weather": "晴", **values}
    return {
        "StationId": station_id, "StationName": f"站{station_id}", "ObsTime": {"DateTime": obs.isoformat()},
        "GeoInfo": {"Coordinates": [{"CoordinateName": "WGS84", "StationLatitude": "24.0", "StationLongitude": "121.0"}],
                    "CountyName": county, "TownName": "測試區"},
        "WeatherElement": {"AirTemperature": v["AirTemperature"], "RelativeHumidity": v["RelativeHumidity"],
                           "WindSpeed": v["WindSpeed"], "WindDirection": v["WindDirection"], "UVIndex": v["UVIndex"],
                           "Now": {"Precipitation": v["Precipitation"]}, "Weather": v["Weather"]},
    }


class HistoryTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "weather.db"
        db.init_db(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, *stations: dict) -> dict:
        records, skipped = cwa_api.parse_stations({"cwaopendata": {"dataset": {"Station": list(stations)}}})
        self.assertEqual(skipped, 0)
        return db.save_observations(records, self.db_path)

    def rows(self) -> list[dict]:
        return db._query("SELECT station_id, observation_time, " + ", ".join(FIELDS) +
                         " FROM WeatherObservation ORDER BY station_id, observation_time", db_path=self.db_path)

    def history(self, county: str, days: int):
        """GET /api/weather/history against this test's database, CWA never called."""
        status = RefreshStatus(updated=False, stale=False, error=None, latest_observation_time=None)
        patches = [
            mock.patch.object(routes, "ensure_fresh", lambda *a, **k: status),
            mock.patch.object(cwa_api, "fetch_dataset", side_effect=AssertionError("history must not call CWA")),
            mock.patch.object(db, "county_exists", partial(db.county_exists, db_path=self.db_path)),
            mock.patch.object(db, "get_latest_observation_time", partial(db.get_latest_observation_time, db_path=self.db_path)),
            mock.patch.object(db, "get_county_history", partial(db.get_county_history, db_path=self.db_path)),
        ]
        for p in patches:
            p.start()
        try:
            from backend.app import create_app
            response = create_app().test_client().get(f"/api/weather/history?county={county}&days={days}")
        finally:
            for p in reversed(patches):
                p.stop()
        self.assertEqual(response.status_code, 200)
        return response.get_json()


class StorageTests(HistoryTestCase):
    def test_every_field_is_preserved_and_times_coexist(self):
        t1, t2 = LATEST - timedelta(minutes=10), LATEST
        self.save(raw_station("A1", "臺北市", t1, AirTemperature="21.5", RelativeHumidity="91", WindSpeed="1.2",
                              WindDirection="270", UVIndex="3", Precipitation="12.5", Weather="陰有雨"))
        self.save(raw_station("A1", "臺北市", t2, AirTemperature="21.3", Weather="陰"))
        self.assertEqual(self.rows(), [
            {"station_id": "A1", "observation_time": t1.isoformat(), "temperature": 21.5, "humidity": 91.0, "wind_speed": 1.2,
             "wind_direction": 270.0, "uv_index": 3.0, "precipitation": 12.5, "weather": "陰有雨"},
            {"station_id": "A1", "observation_time": t2.isoformat(), "temperature": 21.3, "humidity": 80.0, "wind_speed": 2.3,
             "wind_direction": 45.0, "uv_index": 0.0, "precipitation": 0.0, "weather": "陰"},
        ])

    def test_same_station_and_time_twice_is_one_row_and_history_is_kept(self):
        t1, t2 = LATEST - timedelta(minutes=10), LATEST
        self.save(raw_station("A1", "臺北市", t1, AirTemperature="21.5"), raw_station("A2", "臺北市", t1))
        self.save(raw_station("A1", "臺北市", t2))
        before = self.rows()
        # The same batch again, even with different values, adds nothing and changes nothing.
        stats = self.save(raw_station("A1", "臺北市", t1, AirTemperature="99.9"), raw_station("A2", "臺北市", t1))
        self.assertEqual(stats["observations_inserted"], 0)
        self.assertEqual(stats["observations_skipped_duplicate"], 2)
        self.assertEqual(self.rows(), before)
        self.assertEqual(len(before), 3)


class WeatherFieldTests(HistoryTestCase):
    def weather(self) -> str | None:
        return self.rows()[0]["weather"]

    def test_valid_text_is_stored(self):
        self.save(raw_station("A1", "臺北市", LATEST, Weather=" 多雲 "))
        self.assertEqual(self.weather(), "多雲")

    def test_missing_text_is_null(self):
        for missing in ("-99", "", "X"):
            with self.subTest(missing=missing):
                self.tearDown(); self.setUp()
                self.save(raw_station("A1", "臺北市", LATEST, Weather=missing))
                self.assertIsNone(self.weather())

    def test_null_is_filled_by_a_later_valid_value(self):
        self.save(raw_station("A1", "臺北市", LATEST, Weather="-99"))
        self.save(raw_station("A1", "臺北市", LATEST, Weather="晴"))
        self.assertEqual(self.weather(), "晴")
        self.assertEqual(len(self.rows()), 1)

    def test_valid_value_is_never_overwritten(self):
        self.save(raw_station("A1", "臺北市", LATEST, Weather="晴"))
        self.save(raw_station("A1", "臺北市", LATEST, Weather="-99"))  # NULL
        self.assertEqual(self.weather(), "晴")
        self.save(raw_station("A1", "臺北市", LATEST, Weather="陰"))  # another valid value
        self.assertEqual(self.weather(), "晴")
        self.assertEqual(len(self.rows()), 1)


class MissingValueTests(HistoryTestCase):
    def test_cwa_sentinels_are_stored_as_null_not_zero(self):
        for raw in ("-99", "-99.0", "-990", "-998", "-999", "X", "", "NA"):
            with self.subTest(raw=raw):
                self.tearDown(); self.setUp()
                self.save(raw_station("A1", "臺北市", LATEST, AirTemperature=raw, RelativeHumidity=raw, WindSpeed=raw,
                                      WindDirection=raw, UVIndex=raw, Precipitation=raw))
                row = self.rows()[0]
                for f in FIELDS[:-1]:
                    self.assertIsNone(row[f], f)

    def test_real_zero_precipitation_stays_zero(self):
        self.save(raw_station("A1", "臺北市", LATEST, Precipitation="0.0"))
        self.assertEqual(self.rows()[0]["precipitation"], 0.0)

    def test_history_averages_only_valid_values(self):
        t = LATEST
        self.save(raw_station("A1", "臺北市", t, AirTemperature="20.0", RelativeHumidity="90", WindSpeed="2.0", Precipitation="4.0"),
                  raw_station("A2", "臺北市", t, AirTemperature="-99", RelativeHumidity="-99", WindSpeed="-99", Precipitation="-99"))
        point = self.history("臺北市", 1)["data"][0]
        self.assertEqual(point["station_count"], 2)
        self.assertEqual(point["temperature_count"], 1)
        # The missing station is left out; it is not counted as 0 (which would give 10.0 / 45 / 1.0 / 2.0).
        self.assertEqual(point["avg_temperature"], 20.0)
        self.assertEqual((point["min_temperature"], point["max_temperature"]), (20.0, 20.0))
        self.assertEqual(point["avg_humidity"], 90.0)
        self.assertEqual(point["avg_wind_speed"], 2.0)
        self.assertEqual(point["avg_precipitation"], 4.0)

    def test_all_missing_gives_null_not_zero(self):
        older = LATEST - timedelta(minutes=10)
        self.save(raw_station("A1", "臺北市", older, AirTemperature="-99", RelativeHumidity="-99", WindSpeed="-99", Precipitation="-99"))
        self.save(raw_station("A1", "臺北市", LATEST))
        body = self.history("臺北市", 1)
        missing = body["data"][0]
        self.assertEqual(missing["observation_time"], older.isoformat())
        self.assertEqual(missing["temperature_count"], 0)
        for f in ("avg_temperature", "min_temperature", "max_temperature", "avg_humidity", "avg_wind_speed", "avg_precipitation"):
            self.assertIsNone(missing[f], f)  # NULL precipitation is not 0 mm
        self.assertEqual(body["data"][1]["avg_temperature"], 25.1)  # other points are unaffected

    def test_non_finite_stored_value_is_returned_as_null(self):
        self.save(raw_station("A1", "臺北市", LATEST))
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE WeatherObservation SET temperature = 9e999")  # +Infinity
        conn.commit()
        conn.close()
        point = self.history("臺北市", 1)["data"][0]
        self.assertIsNone(point["avg_temperature"])
        self.assertTrue(all(not (isinstance(v, float) and not math.isfinite(v)) for v in point.values()))


class WindowAndCountyTests(HistoryTestCase):
    def test_days_windows_end_at_latest_and_never_fill_gaps(self):
        offsets = [timedelta(0), timedelta(hours=23, minutes=50), timedelta(days=1),  # 1 day ago: excluded from 24H
                   timedelta(days=6), timedelta(days=7),  # 7 days ago: excluded from 7D
                   timedelta(days=29), timedelta(days=30), timedelta(days=31)]
        for off in offsets:
            self.save(raw_station("A1", "臺北市", LATEST - off))
        expected = {1: offsets[:2], 7: offsets[:4], 30: offsets[:6]}
        for days, offs in expected.items():
            body = self.history("臺北市", days)
            times = [p["observation_time"] for p in body["data"]]
            self.assertEqual(times, [(LATEST - o).isoformat() for o in sorted(offs, reverse=True)], days)  # chronological
            self.assertEqual(len(times), len(set(times)))
            self.assertEqual(body["until"], LATEST.isoformat())  # no point after the newest stored observation
            self.assertEqual(body["since"], (LATEST - timedelta(days=days)).isoformat())
            self.assertEqual(body["count"], len(offs))

    def test_county_uses_only_its_own_stations(self):
        self.save(raw_station("A1", "臺北市", LATEST, AirTemperature="20.0"),
                  raw_station("A2", "臺北市", LATEST, AirTemperature="22.0"),
                  raw_station("B1", "宜蘭縣", LATEST, AirTemperature="30.0"))
        taipei = self.history("臺北市", 1)["data"]
        yilan = self.history("宜蘭縣", 1)["data"]
        self.assertEqual((taipei[0]["station_count"], taipei[0]["avg_temperature"], taipei[0]["max_temperature"]), (2, 21.0, 22.0))
        self.assertEqual((yilan[0]["station_count"], yilan[0]["avg_temperature"]), (1, 30.0))


if __name__ == "__main__":
    unittest.main()
