"""CWA O-A0003-001 fetching, parsing and ingestion.

The API key is read from the server-side .env file and is never logged,
returned, or included in error messages.
"""
import os
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

from backend.db import DB_PATH, init_db, save_observations

DATASET_ID = "O-A0003-001"
CWA_FILEAPI_URL = "https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/{dataset_id}"
REQUEST_TIMEOUT = 30  # seconds

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

# CWA uses these numeric sentinels for unavailable measurements.
SENTINEL_VALUES = {-99.0, -990.0, -998.0, -999.0}
MISSING_STRINGS = {"", "NA", "N/A", "X", "-", "NULL", "NONE"}


class CWAError(Exception):
    """Raised when CWA data cannot be fetched or has an unexpected structure."""


def get_api_key() -> str:
    load_dotenv(ENV_PATH)
    key = os.getenv("CWA_API_KEY", "").strip()
    if not key or key == "your_cwa_api_key_here":
        raise CWAError("CWA_API_KEY is missing. Set it in the project root .env file.")
    return key


def fetch_dataset(dataset_id: str, timeout: int = REQUEST_TIMEOUT) -> dict:
    """Download one CWA Open Data file-API dataset as JSON."""
    params = {"Authorization": get_api_key(), "downloadType": "WEB", "format": "JSON"}
    url = CWA_FILEAPI_URL.format(dataset_id=dataset_id)
    # Exception messages from requests can contain the full URL (and thus the key),
    # so only the exception type is surfaced.
    try:
        response = requests.get(url, params=params, timeout=timeout)
    except requests.RequestException as e:
        raise CWAError(f"Network error while calling CWA: {type(e).__name__}") from None

    if response.status_code != 200:
        raise CWAError(f"CWA returned HTTP {response.status_code}")

    try:
        return response.json()
    except ValueError:
        raise CWAError("CWA response is not valid JSON") from None


def fetch_weather_data(timeout: int = REQUEST_TIMEOUT) -> dict:
    """Download the O-A0003-001 JSON payload."""
    return fetch_dataset(DATASET_ID, timeout)


def to_number(value) -> float | None:
    """Convert a CWA measurement to float; missing/invalid/sentinel values become None."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if value.upper() in MISSING_STRINGS:
            return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number in SENTINEL_VALUES:
        return None
    return number


def to_text(value) -> str | None:
    """CWA text measurement (e.g. Weather "晴"); missing markers / sentinels become None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.upper() in MISSING_STRINGS:
        return None
    try:
        float(text)  # descriptive fields are never numbers; this catches -99 / -99.0 etc.
        return None
    except ValueError:
        return text


def pick_wgs84(coordinates: list) -> tuple[float | None, float | None]:
    """Return (lat, lon) from the WGS84 entry only; never fall back to another datum."""
    for c in coordinates or []:
        if c.get("CoordinateName") == "WGS84":
            lat = to_number(c.get("StationLatitude"))
            lon = to_number(c.get("StationLongitude"))
            if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
                return lat, lon
    return None, None


def normalize_time(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip()).isoformat()
    except ValueError:
        return None


def normalize_station(raw: dict) -> dict | None:
    """Map one CWA station entry to a flat record, or None if it fails validation."""
    station_id = (raw.get("StationId") or "").strip()
    station_name = (raw.get("StationName") or "").strip()
    observation_time = normalize_time((raw.get("ObsTime") or {}).get("DateTime"))
    if not station_id or not station_name or not observation_time:
        return None

    geo = raw.get("GeoInfo") or {}
    weather = raw.get("WeatherElement") or {}
    lat, lon = pick_wgs84(geo.get("Coordinates"))

    return {
        "station_id": station_id,
        "station_name": station_name,
        "county_name": (geo.get("CountyName") or "").strip() or None,
        "town_name": (geo.get("TownName") or "").strip() or None,
        "latitude": lat,
        "longitude": lon,
        "observation_time": observation_time,
        "temperature": to_number(weather.get("AirTemperature")),
        "humidity": to_number(weather.get("RelativeHumidity")),
        "wind_speed": to_number(weather.get("WindSpeed")),
        "wind_direction": to_number(weather.get("WindDirection")),
        "uv_index": to_number(weather.get("UVIndex")),
        "precipitation": to_number((weather.get("Now") or {}).get("Precipitation")),
        "weather": to_text(weather.get("Weather")),
    }


def parse_stations(payload: dict) -> tuple[list[dict], int]:
    """Return (valid records, number of skipped invalid entries)."""
    try:
        stations = payload["cwaopendata"]["dataset"]["Station"]
    except (KeyError, TypeError):
        raise CWAError("Unexpected CWA JSON structure: cwaopendata.dataset.Station not found") from None
    if not isinstance(stations, list):
        raise CWAError("Unexpected CWA JSON structure: Station is not a list")

    records, skipped = [], 0
    for raw in stations:
        record = normalize_station(raw) if isinstance(raw, dict) else None
        if record is None:
            skipped += 1
        else:
            records.append(record)
    return records, skipped


def ingest(db_path: Path = DB_PATH) -> dict:
    """Fetch O-A0003-001, validate it, and store it (backend.db)."""
    payload = fetch_weather_data()
    records, skipped = parse_stations(payload)
    init_db(db_path)
    stats = save_observations(records, db_path)
    return {"fetched_records": len(records) + skipped, "invalid_skipped": skipped, **stats}


if __name__ == "__main__":
    print(ingest())
