"""CWA tropical cyclone tracks: W-C0034-005, fetched live (not stored).

Source: W-C0034-005 「颱風消息與警報-熱帶氣旋路徑」 from the REST datastore
(/api/v1/rest/datastore/), which returns every active tropical cyclone in the
western North Pacific / South China Sea with its past + current positions
(AnalysisData.Fix) and its latest forecast (ForecastData.Fix). Each response
already holds the whole track, so nothing is stored in SQLite.

CWA states no units or coordinate system for this dataset. Values are passed
through as decimal-degree lat/lon (as on every other CWA dataset used here);
from the data itself: wind speeds m/s, pressure hPa, moving speed km/h,
radii km. All times keep CWA's own +08:00 offset.

The API key stays server-side (see backend.cwa_api) and is never returned,
logged, or included in error messages.
"""
import math
import threading
import time
from datetime import datetime, timedelta

import requests

from backend.cwa_api import REQUEST_TIMEOUT, CWAError, get_api_key, normalize_time, to_number

TYPHOON_DATASET_ID = "W-C0034-005"
DATASTORE_URL = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/{TYPHOON_DATASET_ID}"
# Bulletins change every few hours (6-hourly fixes), so 10 minutes is plenty fresh.
CACHE_SECONDS = 600
# After a failed refresh, wait this long before asking CWA again.
RETRY_COOLDOWN_SECONDS = 120

QUADRANTS = ("NE", "SE", "SW", "NW")


def fetch_typhoon_data(timeout: int = REQUEST_TIMEOUT) -> dict:
    """Download W-C0034-005 from the REST datastore as JSON."""
    params = {"Authorization": get_api_key(), "format": "JSON"}
    # requests' exception messages can contain the full URL (and thus the key),
    # so only the exception type is surfaced.
    try:
        response = requests.get(DATASTORE_URL, params=params, timeout=timeout)
    except requests.RequestException as e:
        raise CWAError(f"Network error while calling CWA: {type(e).__name__}") from None

    if response.status_code != 200:
        raise CWAError(f"CWA returned HTTP {response.status_code}")

    try:
        payload = response.json()
    except ValueError:
        raise CWAError("CWA response is not valid JSON") from None
    if not isinstance(payload, dict) or str(payload.get("success")).lower() != "true":
        raise CWAError("CWA reported the request as unsuccessful")
    return payload


# ---------------------------------------------------------------------------
# Normalization. Every value CWA sends is a string; numbers become float/int
# or None (missing, sentinel or unparsable), never a guessed value. A point
# that cannot be placed (no valid time or position) is dropped on its own.
# ---------------------------------------------------------------------------

def _as_list(value) -> list:
    """CWA repeats elements as a list, but a single element may come as an object."""
    if isinstance(value, list):
        return value
    return [value] if isinstance(value, dict) else []


def _number(value) -> float | None:
    """CWA numeric string -> float; missing, sentinel, unparsable or non-finite -> None."""
    number = to_number(value)
    return number if number is not None and math.isfinite(number) else None


def _to_int(value) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number.is_integer() else None


def _text(value) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def _time(value) -> str | None:
    """ISO time with its UTC offset kept; a time without an offset is ambiguous and rejected."""
    text = normalize_time(value)
    return text if text is not None and datetime.fromisoformat(text).tzinfo is not None else None


def _coordinates(raw: dict) -> tuple[float, float] | None:
    lat, lon = _number(raw.get("CoordinateLatitude")), _number(raw.get("CoordinateLongitude"))
    # Longitude may run past 180 for a track crossing the date line.
    if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 360:
        return None
    return lat, lon


def _multilingual(value) -> dict[str, str] | None:
    """[{"value": "...", "lang": "zh-hant"}, ...] -> {"zh-hant": "...", ...}."""
    texts = {}
    for item in _as_list(value):
        if not isinstance(item, dict):
            continue
        lang, text = _text(item.get("lang")), _text(item.get("value"))
        if lang and text:
            texts[lang] = text
    return texts or None


def _wind_circle(value) -> dict | None:
    """Circle15ms / Circle25ms -> {"radius": km, "quadrants": {"NE": km, ...} | None}."""
    if not isinstance(value, dict):
        return None
    radius = _number(value.get("Radius"))
    quadrant_radii = value.get("QuadrantRadii")
    items = _as_list(quadrant_radii.get("Radius")) if isinstance(quadrant_radii, dict) else []
    quadrants = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        direction, km = _text(item.get("dir")), _number(item.get("value"))
        if direction in QUADRANTS and km is not None:
            quadrants[direction] = km
    if radius is None and not quadrants:
        return None
    # Only the quadrants CWA actually sent; none sent -> null.
    return {"radius": radius, "quadrants": quadrants or None}


def _intensity(raw: dict) -> dict:
    return {
        "max_wind_speed": _number(raw.get("MaxWindSpeed")),
        "max_gust_speed": _number(raw.get("MaxGustSpeed")),
        "pressure": _number(raw.get("Pressure")),
        "moving_speed": _number(raw.get("MovingSpeed")),
        "moving_direction": _text(raw.get("MovingDirection")),
        "circle15ms": _wind_circle(raw.get("Circle15ms")),
        "circle25ms": _wind_circle(raw.get("Circle25ms")),
    }


def normalize_analysis_point(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    when, position = _time(raw.get("DateTime")), _coordinates(raw)
    if when is None or position is None:
        return None
    return {
        "datetime": when,
        "latitude": position[0],
        "longitude": position[1],
        **_intensity(raw),
        "moving_prediction": _multilingual(raw.get("MovingPrediction")),
    }


def valid_time(initial_time: str, forecast_hour: int) -> str:
    """InitialTime + ForecastHour, keeping InitialTime's own UTC offset (CWA gives no valid time)."""
    return (datetime.fromisoformat(initial_time) + timedelta(hours=forecast_hour)).isoformat()


def normalize_forecast_point(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    initial, hour, position = _time(raw.get("InitialTime")), _to_int(raw.get("ForecastHour")), _coordinates(raw)
    if initial is None or hour is None or hour < 0 or position is None:
        return None
    return {
        "initial_time": initial,
        "forecast_hour": hour,
        "valid_time": valid_time(initial, hour),
        "latitude": position[0],
        "longitude": position[1],
        **_intensity(raw),
        "radius70_probability": _number(raw.get("Radius70PercentProbability")),
        "state_transfer": _multilingual(raw.get("StateTransfer")),
    }


def _points(section, normalize) -> list[dict]:
    fixes = _as_list(section.get("Fix")) if isinstance(section, dict) else []
    points = []
    for raw in fixes:
        try:
            point = normalize(raw)
        except (TypeError, ValueError, AttributeError, OverflowError):
            point = None  # one malformed fix never drops the whole response
        if point is not None:
            points.append(point)
    return points


def normalize_typhoon(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    analysis = sorted(_points(raw.get("AnalysisData"), normalize_analysis_point), key=lambda p: datetime.fromisoformat(p["datetime"]))
    forecast = sorted(_points(raw.get("ForecastData"), normalize_forecast_point), key=lambda p: p["forecast_hour"])
    if not analysis and not forecast:
        return None  # nothing that can be drawn
    return {
        "year": _to_int(raw.get("Year")),
        "typhoon_name": _text(raw.get("TyphoonName")),
        "cwa_typhoon_name": _text(raw.get("CwaTyphoonName")),
        "cwa_td_no": _to_int(raw.get("CwaTdNo")),
        "cwa_ty_no": _to_int(raw.get("CwaTyNo")),  # null for a tropical depression
        "analysis": analysis,
        "forecast": forecast,
    }


def parse_typhoons(payload: dict) -> dict:
    """Normalize a W-C0034-005 REST response; raise CWAError if its structure is unusable."""
    try:
        cyclones = payload["records"]["TropicalCyclones"]["TropicalCyclone"]
    except (KeyError, TypeError):
        raise CWAError("Unexpected CWA JSON structure: records.TropicalCyclones.TropicalCyclone not found") from None
    if not isinstance(cyclones, (list, dict)):
        raise CWAError("Unexpected CWA JSON structure: TropicalCyclone is not a list")

    typhoons = [t for t in map(normalize_typhoon, _as_list(cyclones)) if t is not None]
    # The REST response carries no bulletin issue time, so updated_at is the
    # newest analysis (observed position) time across all cyclones. With no
    # cyclones there is no CWA time to report: null, never the local clock.
    times = [p["datetime"] for t in typhoons for p in t["analysis"]]
    updated_at = max(times, key=datetime.fromisoformat) if times else None
    return {"source": TYPHOON_DATASET_ID, "updated_at": updated_at, "typhoons": typhoons}


# ---------------------------------------------------------------------------
# Cache: one CWA request per CACHE_SECONDS, shared by concurrent requests.
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_state: dict = {"data": None, "fetched_at": 0.0, "attempted_at": None, "error": None}


def get_latest_typhoons() -> tuple[dict, str | None]:
    """(normalized data, refresh error or None).

    A failed refresh keeps serving the last good data with its error; with no
    good data yet the CWAError is raised. After a failure CWA is asked again
    only once RETRY_COOLDOWN_SECONDS have passed.
    """
    with _lock:
        now = time.monotonic()
        data = _state["data"]
        if data is not None and now - _state["fetched_at"] < CACHE_SECONDS:
            return data, None
        if _state["error"] and _state["attempted_at"] is not None and now - _state["attempted_at"] < RETRY_COOLDOWN_SECONDS:
            if data is None:
                raise CWAError(_state["error"])
            return data, _state["error"]

        _state["attempted_at"] = now
        try:
            fresh = parse_typhoons(fetch_typhoon_data())
        except CWAError as e:
            _state["error"] = str(e)
            if data is None:
                raise
            return data, _state["error"]
        _state.update(data=fresh, fetched_at=now, error=None)
        return fresh, None
