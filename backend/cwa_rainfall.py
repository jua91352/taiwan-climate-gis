"""CWA automatic rain gauge data: past-1-hour rainfall, fetched live (not stored).

Source: O-A0002-001 「自動雨量站-雨量觀測資料」, field
RainfallElement.Past1hr.Precipitation (mm). The API key stays server-side;
see backend.cwa_api.
"""
import threading
import time

from backend.cwa_api import CWAError, fetch_dataset, normalize_time, pick_wgs84

RAINFALL_DATASET_ID = "O-A0002-001"
UNIT = "mm"
CACHE_SECONDS = 300  # CWA refreshes every 10 minutes; avoid a 1.7 MB download per request

# Past1hr special values -> rainfall_status. All of them yield rainfall=None so
# they are never mistaken for a measured amount.
TRACE_STRINGS = {"T"}  # 微量降雨 (trace, below the gauge's resolution)
MALFUNCTION_STRINGS = {"X"}  # 儀器故障
MISSING_STRINGS = {"", "NA", "N/A", "-", "NULL", "NONE"}
MISSING_NUMBERS = {-99.0, -990.0, -998.0, -999.0}  # 缺測 / 無資料 sentinels


def parse_rainfall_value(value) -> tuple[float | None, str]:
    """Return (rainfall in mm or None, status: ok | trace | malfunction | missing)."""
    if value is None:
        return None, "missing"
    if isinstance(value, str):
        text = value.strip().upper()
        if text in TRACE_STRINGS:
            return None, "trace"
        if text in MALFUNCTION_STRINGS:
            return None, "malfunction"
        if text in MISSING_STRINGS:
            return None, "missing"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, "missing"
    if number != number or number in (float("inf"), float("-inf")):  # NaN / Infinity
        return None, "missing"
    if number in MISSING_NUMBERS or number < 0:
        return None, "missing"
    return number, "ok"


def normalize_rain_station(raw: dict) -> dict | None:
    """Map one CWA rain gauge to a flat record, or None if it fails validation."""
    station_id = (raw.get("StationId") or "").strip()
    station_name = (raw.get("StationName") or "").strip()
    observation_time = normalize_time((raw.get("ObsTime") or {}).get("DateTime"))
    if not station_id or not station_name or not observation_time:
        return None

    geo = raw.get("GeoInfo") or {}
    lat, lon = pick_wgs84(geo.get("Coordinates"))
    past1hr = ((raw.get("RainfallElement") or {}).get("Past1hr") or {}).get("Precipitation")
    rainfall, status = parse_rainfall_value(past1hr)

    return {
        "station_id": station_id,
        "station_name": station_name,
        "county_name": (geo.get("CountyName") or "").strip() or None,
        "town_name": (geo.get("TownName") or "").strip() or None,
        "latitude": lat,
        "longitude": lon,
        "rainfall": rainfall,
        "rainfall_status": status,
        "unit": UNIT,
        "observation_time": observation_time,
    }


def parse_rainfall(payload: dict) -> tuple[list[dict], int]:
    """Return (valid records, number of skipped invalid entries)."""
    try:
        stations = payload["cwaopendata"]["dataset"]["Station"]
    except (KeyError, TypeError):
        raise CWAError("Unexpected CWA JSON structure: cwaopendata.dataset.Station not found") from None
    if not isinstance(stations, list):
        raise CWAError("Unexpected CWA JSON structure: Station is not a list")

    records, skipped = [], 0
    for raw in stations:
        record = normalize_rain_station(raw) if isinstance(raw, dict) else None
        if record is None:
            skipped += 1
        else:
            records.append(record)
    return records, skipped


_cache: dict = {"at": 0.0, "records": None}
_lock = threading.Lock()


def get_latest_rainfall() -> list[dict]:
    """Latest past-1-hour rainfall per gauge, cached for CACHE_SECONDS."""
    with _lock:
        if _cache["records"] is not None and time.monotonic() - _cache["at"] < CACHE_SECONDS:
            return _cache["records"]
        records, _ = parse_rainfall(fetch_dataset(RAINFALL_DATASET_ID))
        _cache.update(at=time.monotonic(), records=records)
        return records
