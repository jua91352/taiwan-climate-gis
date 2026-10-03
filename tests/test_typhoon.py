"""Tests for backend.cwa_typhoon (CWA W-C0034-005) and GET /api/typhoon/latest.

Fixtures are trimmed from a real W-C0034-005 REST response (typhoon CHOI-WAN /
彩雲, 2026-10-04) and the real empty response. CWA, the API key and the cache
clock are faked; nothing here calls the network.
Run: python -m unittest tests.test_typhoon
"""
import copy
import json
import os
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import requests

os.environ.setdefault("RADAR_COLLECTOR", "0")  # importing backend.app must not start the radar collector

from backend import cwa_typhoon  # noqa: E402
from backend.cwa_api import CWAError  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
CHOI_WAN = json.loads((FIXTURES / "cwa_w_c0034_005_choi_wan.json").read_text(encoding="utf-8"))
EMPTY = json.loads((FIXTURES / "cwa_w_c0034_005_empty.json").read_text(encoding="utf-8"))
KEY = "TEST-KEY-123"


def cyclones(payload: dict):
    return payload["records"]["TropicalCyclones"]["TropicalCyclone"]


class FakeResponse:
    def __init__(self, status: int = 200, payload=None, invalid_json: bool = False):
        self.status_code = status
        self._payload = payload
        self._invalid = invalid_json

    def json(self):
        if self._invalid:
            raise ValueError("not JSON")
        return self._payload


class FakeCWA:
    """Stands in for requests.get; counts calls and records the params sent."""

    def __init__(self, payload=CHOI_WAN):
        self.payload = payload
        self.calls = 0
        self.params = []
        self.error = None  # exception to raise, or FakeResponse to return
        self.delay = 0.0
        self.lock = threading.Lock()

    def get(self, url, params=None, timeout=None):
        with self.lock:
            self.calls += 1
            self.params.append((url, dict(params or {})))
        time.sleep(self.delay)
        if isinstance(self.error, BaseException):
            raise self.error
        if isinstance(self.error, FakeResponse):
            return self.error
        return FakeResponse(200, copy.deepcopy(self.payload))


class TyphoonTestCase(unittest.TestCase):
    def setUp(self):
        cwa_typhoon._state.update(data=None, fetched_at=0.0, attempted_at=None, error=None)
        self.cwa = FakeCWA()
        self.clock = [1000.0]
        patches = [
            mock.patch.object(cwa_typhoon.requests, "get", self.cwa.get),
            mock.patch.object(cwa_typhoon, "get_api_key", lambda: KEY),
            mock.patch.object(cwa_typhoon.time, "monotonic", lambda: self.clock[0]),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        from backend.app import create_app
        self.client = create_app().test_client()

    def get(self):
        response = self.client.get("/api/typhoon/latest")
        body = response.get_data(as_text=True)
        self.assertNotIn(KEY, body)
        self.assertNotIn("Traceback", body)
        return response.status_code, response.get_json()


class NormalizationTests(unittest.TestCase):
    def setUp(self):
        self.data = cwa_typhoon.parse_typhoons(copy.deepcopy(CHOI_WAN))
        self.ty = self.data["typhoons"][0]

    def test_cyclone_identity(self):
        self.assertEqual(self.data["source"], "W-C0034-005")
        self.assertEqual(len(self.data["typhoons"]), 1)
        self.assertEqual({k: self.ty[k] for k in ("year", "typhoon_name", "cwa_typhoon_name", "cwa_td_no", "cwa_ty_no")},
                         {"year": 2026, "typhoon_name": "CHOI-WAN", "cwa_typhoon_name": "彩雲", "cwa_td_no": 30, "cwa_ty_no": 26})

    def test_analysis_points(self):
        a = self.ty["analysis"]
        self.assertEqual([p["datetime"] for p in a],
                         ["2026-09-30T08:00:00+08:00", "2026-10-03T20:00:00+08:00", "2026-10-04T02:00:00+08:00"])
        self.assertEqual(a[0], {
            "datetime": "2026-09-30T08:00:00+08:00", "latitude": 15.5, "longitude": 152.0,
            "max_wind_speed": 12.0, "max_gust_speed": 20.0, "pressure": 1006.0, "moving_speed": 30.0, "moving_direction": "SE",
            "circle15ms": None, "circle25ms": None, "moving_prediction": None,
        })
        self.assertEqual(a[1]["circle15ms"], {"radius": 280.0, "quadrants": {"NE": 300.0, "SE": 300.0, "SW": 250.0, "NW": 280.0}})
        self.assertEqual(a[1]["circle25ms"], {"radius": 90.0, "quadrants": {"NE": 90.0, "SE": 90.0, "SW": 90.0, "NW": 90.0}})
        self.assertEqual(a[2]["moving_prediction"], {"zh-hant": "以每小時15轉19公里速度，向北北東轉北進行",
                                                     "en-us": "NNE TURNING NORTH 15 KM/HR BECOMING 19 KM/HR"})
        self.assertEqual((a[2]["latitude"], a[2]["longitude"], a[2]["max_wind_speed"], a[2]["pressure"]), (20.9, 146.9, 43.0, 945.0))

    def test_forecast_points(self):
        f = self.ty["forecast"]
        self.assertEqual([p["forecast_hour"] for p in f], [6, 24, 96])
        self.assertEqual(f[0], {
            "initial_time": "2026-10-04T02:00:00+08:00", "forecast_hour": 6, "valid_time": "2026-10-04T08:00:00+08:00",
            "latitude": 21.7, "longitude": 147.1, "max_wind_speed": 45.0, "max_gust_speed": 55.0, "pressure": 940.0,
            "moving_speed": 15.0, "moving_direction": "NNE",
            "circle15ms": {"radius": 280.0, "quadrants": None}, "circle25ms": {"radius": 90.0, "quadrants": None},
            "radius70_probability": 30.0, "state_transfer": None,
        })

    def test_96h_point_keeps_missing_intensity_as_null(self):
        last = self.ty["forecast"][-1]
        self.assertEqual(last["valid_time"], "2026-10-08T02:00:00+08:00")
        self.assertIsNone(last["max_wind_speed"])
        self.assertIsNone(last["max_gust_speed"])
        self.assertIsNone(last["circle15ms"])
        self.assertEqual(last["radius70_probability"], 390.0)
        self.assertEqual(last["state_transfer"], {"zh-hant": "變性為溫帶氣旋", "en-us": "BECOMING EXTRATROPICAL LOW"})

    def test_updated_at_is_newest_analysis_time(self):
        self.assertEqual(self.data["updated_at"], "2026-10-04T02:00:00+08:00")

    def test_valid_time_keeps_offset(self):
        for hour, expected in [(6, "2026-10-04T08:00:00+08:00"), (12, "2026-10-04T14:00:00+08:00"),
                               (24, "2026-10-05T02:00:00+08:00"), (48, "2026-10-06T02:00:00+08:00"),
                               (72, "2026-10-07T02:00:00+08:00"), (96, "2026-10-08T02:00:00+08:00"),
                               (120, "2026-10-09T02:00:00+08:00")]:
            with self.subTest(hour=hour):
                self.assertEqual(cwa_typhoon.valid_time("2026-10-04T02:00:00+08:00", hour), expected)

    def test_forecast_hour_beyond_96_is_accepted(self):
        payload = copy.deepcopy(CHOI_WAN)
        cyclones(payload)[0]["ForecastData"]["Fix"][-1]["ForecastHour"] = "120"
        last = cwa_typhoon.parse_typhoons(payload)["typhoons"][0]["forecast"][-1]
        self.assertEqual((last["forecast_hour"], last["valid_time"]), (120, "2026-10-09T02:00:00+08:00"))

    def test_points_are_returned_in_time_order(self):
        payload = copy.deepcopy(CHOI_WAN)
        ty = cyclones(payload)[0]
        ty["AnalysisData"]["Fix"].reverse()
        ty["ForecastData"]["Fix"].reverse()
        out = cwa_typhoon.parse_typhoons(payload)["typhoons"][0]
        self.assertEqual(out, self.ty)


class VariantTests(TyphoonTestCase):
    def test_no_active_cyclone_is_200_with_empty_list(self):
        self.cwa.payload = EMPTY
        status, body = self.get()
        self.assertEqual(status, 200)
        self.assertEqual(body["typhoons"], [])
        self.assertEqual(body["count"], 0)
        self.assertIsNone(body["updated_at"])  # no CWA time available; never the local clock
        self.assertEqual(body["source"], "W-C0034-005")

    def test_single_object_instead_of_list(self):
        payload = copy.deepcopy(CHOI_WAN)
        payload["records"]["TropicalCyclones"]["TropicalCyclone"] = cyclones(payload)[0]
        self.assertEqual(cwa_typhoon.parse_typhoons(payload), cwa_typhoon.parse_typhoons(copy.deepcopy(CHOI_WAN)))
        self.cwa.payload = payload
        status, body = self.get()
        self.assertEqual(status, 200)
        self.assertEqual(len(body["typhoons"]), 1)

    def test_single_fix_object_instead_of_list(self):
        payload = copy.deepcopy(CHOI_WAN)
        ty = cyclones(payload)[0]
        ty["AnalysisData"]["Fix"] = ty["AnalysisData"]["Fix"][-1]
        out = cwa_typhoon.parse_typhoons(payload)["typhoons"][0]
        self.assertEqual([p["datetime"] for p in out["analysis"]], ["2026-10-04T02:00:00+08:00"])

    def test_response_contract(self):
        status, body = self.get()
        self.assertEqual(status, 200)
        self.assertEqual(set(body), {"success", "source", "updated_at", "typhoons", "count", "refresh_error"})
        self.assertTrue(body["success"])
        self.assertIsNone(body["refresh_error"])
        self.assertEqual(body["typhoons"], cwa_typhoon.parse_typhoons(copy.deepcopy(CHOI_WAN))["typhoons"])


class MalformedDataTests(unittest.TestCase):
    def parse(self, mutate):
        payload = copy.deepcopy(CHOI_WAN)
        mutate(cyclones(payload)[0])
        return cwa_typhoon.parse_typhoons(payload)["typhoons"]

    def test_malformed_numbers_become_null_and_keep_the_point(self):
        def mutate(ty):
            fix = ty["AnalysisData"]["Fix"][0]
            fix.update(MaxWindSpeed="abc", MaxGustSpeed="-99", Pressure="nan", MovingSpeed="inf", MovingDirection="")
        point = self.parse(mutate)[0]["analysis"][0]
        self.assertEqual(point["datetime"], "2026-09-30T08:00:00+08:00")
        for field in ("max_wind_speed", "max_gust_speed", "pressure", "moving_speed", "moving_direction"):
            self.assertIsNone(point[field], field)
        json.dumps(point, allow_nan=False)  # still strict JSON

    def test_unplaceable_points_are_dropped_alone(self):
        def mutate(ty):
            a, f = ty["AnalysisData"]["Fix"], ty["ForecastData"]["Fix"]
            a[0]["CoordinateLatitude"] = "abc"                 # no position
            a[1]["DateTime"] = "2026-10-03T20:00:00"           # no UTC offset: ambiguous
            a.append("not a point")
            f[0]["ForecastHour"] = "six"                       # no forecast hour
            f[1]["CoordinateLongitude"] = "999"                # out of range
        ty = self.parse(mutate)[0]
        self.assertEqual([p["datetime"] for p in ty["analysis"]], ["2026-10-04T02:00:00+08:00"])
        self.assertEqual([p["forecast_hour"] for p in ty["forecast"]], [96])

    def test_malformed_circles_and_texts(self):
        def mutate(ty):
            fix = ty["AnalysisData"]["Fix"][1]
            fix["Circle15ms"] = {"Radius": "x", "QuadrantRadii": {"Radius": [{"value": "300", "dir": "NE"}, {"value": "y", "dir": "SE"}, "junk"]}}
            fix["Circle25ms"] = "junk"
            ty["AnalysisData"]["Fix"][2]["MovingPrediction"] = ["junk", {"lang": "en-us"}]
        a = self.parse(mutate)[0]["analysis"]
        self.assertEqual(a[1]["circle15ms"], {"radius": None, "quadrants": {"NE": 300.0}})  # only what CWA sent
        self.assertIsNone(a[1]["circle25ms"])
        self.assertIsNone(a[2]["moving_prediction"])

    def test_cyclone_without_any_valid_point_is_skipped(self):
        def mutate(ty):
            ty["AnalysisData"] = {"Fix": []}
            ty["ForecastData"] = "junk"
        self.assertEqual(self.parse(mutate), [])

    def test_unusable_structure_raises_cwa_error(self):
        for label, payload in [
            ("missing TropicalCyclones", {"success": "true", "records": {"dataid": "C0034-005"}}),
            ("missing TropicalCyclone", {"success": "true", "records": {"TropicalCyclones": {}}}),
            ("TropicalCyclone not a list", {"success": "true", "records": {"TropicalCyclones": {"TropicalCyclone": "x"}}}),
            ("records not an object", {"success": "true", "records": []}),
        ]:
            with self.subTest(label):
                with self.assertRaises(CWAError):
                    cwa_typhoon.parse_typhoons(payload)


class ErrorTests(TyphoonTestCase):
    def assert_controlled(self, error, message_part: str):
        self.cwa.error = error
        status, body = self.get()
        self.assertEqual(status, 502)
        self.assertEqual(body["success"], False)
        self.assertIn(message_part, body["error"])

    def test_http_401(self):
        self.assert_controlled(FakeResponse(401), "HTTP 401")

    def test_http_500(self):
        self.assert_controlled(FakeResponse(500), "HTTP 500")

    def test_timeout(self):
        self.assert_controlled(requests.Timeout(), "Timeout")

    def test_network_error_message_with_key_is_not_passed_on(self):
        self.assert_controlled(requests.ConnectionError(f"https://opendata.cwa.gov.tw/...?Authorization={KEY}"), "ConnectionError")

    def test_invalid_json(self):
        self.assert_controlled(FakeResponse(200, invalid_json=True), "not valid JSON")

    def test_unsuccessful_flag(self):
        self.assert_controlled(FakeResponse(200, {"success": "false"}), "unsuccessful")

    def test_missing_tropical_cyclones(self):
        self.assert_controlled(FakeResponse(200, {"success": "true", "records": {}}), "TropicalCyclone not found")

    def test_missing_tropical_cyclone(self):
        self.assert_controlled(FakeResponse(200, {"success": "true", "records": {"TropicalCyclones": {}}}), "TropicalCyclone not found")

    def test_missing_api_key(self):
        def no_key():
            raise CWAError("CWA_API_KEY is missing. Set it in the project root .env file.")
        with mock.patch.object(cwa_typhoon, "get_api_key", no_key):
            status, body = self.get()
        self.assertEqual((status, body["success"]), (502, False))
        self.assertIn("CWA_API_KEY is missing", body["error"])


class CacheTests(TyphoonTestCase):
    def test_cached_for_ten_minutes(self):
        self.assertEqual(self.get()[0], 200)
        self.clock[0] += cwa_typhoon.CACHE_SECONDS - 1
        self.assertEqual(self.get()[0], 200)
        self.assertEqual(self.cwa.calls, 1)
        self.clock[0] += 1
        self.assertEqual(self.get()[0], 200)
        self.assertEqual(self.cwa.calls, 2)

    def test_failed_refresh_keeps_last_good_data(self):
        good = self.get()[1]
        self.clock[0] += cwa_typhoon.CACHE_SECONDS
        self.cwa.error = FakeResponse(500)
        status, body = self.get()
        self.assertEqual(status, 200)
        self.assertEqual(body["typhoons"], good["typhoons"])
        self.assertEqual(body["refresh_error"], "CWA returned HTTP 500")
        # Within the retry cooldown CWA is not asked again.
        self.clock[0] += 30
        self.assertEqual(self.get()[1]["refresh_error"], "CWA returned HTTP 500")
        self.assertEqual(self.cwa.calls, 2)
        # After it, CWA is asked again and recovery clears the error.
        self.clock[0] += cwa_typhoon.RETRY_COOLDOWN_SECONDS
        self.cwa.error = None
        status, body = self.get()
        self.assertEqual((status, body["refresh_error"]), (200, None))
        self.assertEqual(self.cwa.calls, 3)

    def test_failure_without_cache_is_retried_only_after_cooldown(self):
        self.cwa.error = FakeResponse(500)
        self.assertEqual(self.get()[0], 502)
        self.clock[0] += 30
        self.assertEqual(self.get()[0], 502)
        self.assertEqual(self.cwa.calls, 1)
        self.clock[0] += cwa_typhoon.RETRY_COOLDOWN_SECONDS
        self.cwa.error = None
        self.assertEqual(self.get()[0], 200)
        self.assertEqual(self.cwa.calls, 2)

    def test_concurrent_requests_fetch_once(self):
        self.cwa.delay = 0.2
        results, errors = [], []

        def request():
            try:
                from backend.app import create_app
                results.append(self.get_with(create_app().test_client()))
            except Exception as e:  # surfaced below
                errors.append(e)

        threads = [threading.Thread(target=request) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(self.cwa.calls, 1)
        self.assertEqual(len(results), 10)
        for status, body in results:
            self.assertEqual(status, 200)
            self.assertEqual(len(body["typhoons"]), 1)

    def get_with(self, client):
        response = client.get("/api/typhoon/latest")
        self.assertNotIn(KEY, response.get_data(as_text=True))
        return response.status_code, response.get_json()


class SecurityTests(TyphoonTestCase):
    def test_key_is_sent_to_cwa_only(self):
        status, body = self.get()  # get() asserts the key is not in the response
        self.assertEqual(status, 200)
        url, params = self.cwa.params[0]
        self.assertEqual(url, "https://opendata.cwa.gov.tw/api/v1/rest/datastore/W-C0034-005")
        self.assertEqual(params, {"Authorization": KEY, "format": "JSON"})



class KeySourceTests(unittest.TestCase):
    def test_key_comes_from_the_shared_env_loader(self):
        from backend import cwa_api
        # Unpatched: the module uses the same .env loader as every other CWA call.
        self.assertIs(cwa_typhoon.get_api_key, cwa_api.get_api_key)
        self.assertEqual(cwa_api.ENV_PATH.name, ".env")


if __name__ == "__main__":
    unittest.main()
