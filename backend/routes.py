"""Weather REST API. Weather data is read from SQLite, refreshed from CWA
O-A0003-001 first when it is 10+ minutes old (backend.weather_refresh).
/rainfall/latest calls CWA O-A0002-001 live (cached, not stored).
/radar/history serves stored O-A0058-005 frames (backend.radar).
/typhoon/latest calls CWA W-C0034-005 live (cached, not stored)."""
import math
from datetime import datetime, timedelta
from functools import wraps

from flask import Blueprint, abort, g, jsonify, request, send_from_directory, url_for

from backend import db, radar
from backend.cwa_api import CWAError
from backend.cwa_rainfall import RAINFALL_DATASET_ID, UNIT, get_latest_rainfall
from backend.cwa_typhoon import get_latest_typhoons
from backend.weather_refresh import ensure_fresh

api = Blueprint("api", __name__, url_prefix="/api")

DATA_SOURCE = "CWA O-A0003-001"
MIN_DAYS, MAX_DAYS, DEFAULT_DAYS = 1, 30, 7

OBSERVATION_FIELDS = (
    "observation_time", "temperature", "humidity", "wind_speed",
    "wind_direction", "uv_index", "precipitation",
)


def error(message: str, status: int):
    return jsonify(error=message), status


def normalize_county(name: str) -> str:
    """Accept the common variant 台 for 臺 (e.g. 台中市 -> 臺中市)."""
    return name.strip().replace("台", "臺")


def latest_time(rows: list[dict]) -> str | None:
    return max((r["observation_time"] for r in rows), default=None)


def finite_row(row: dict) -> dict:
    """Replace NaN/Infinity with None so responses are always valid JSON numbers."""
    return {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in row.items()}


def average(rows: list[dict], field: str) -> float | None:
    values = [r[field] for r in rows if r[field] is not None]
    return round(sum(values) / len(values), 1) if values else None


def fresh_observations(view):
    """Make sure SQLite holds current O-A0003-001 data before the view reads it."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        g.refresh = ensure_fresh()
        return view(*args, **kwargs)
    return wrapper


@api.get("/weather/latest")
@fresh_observations
def weather_latest():
    rows = db.get_latest_observations()
    return jsonify(
        source=DATA_SOURCE,
        latest_observation_time=latest_time(rows),
        count=len(rows),
        data=rows,
        # Freshness metadata (additive; existing fields unchanged).
        data_updated=g.refresh.updated,
        data_stale=g.refresh.stale,
        refresh_error=g.refresh.error,
    )


@api.get("/weather/county/<county_name>")
@fresh_observations
def weather_county(county_name: str):
    county = normalize_county(county_name)
    if not db.county_exists(county):
        return error(f"County not found: {county_name}", 404)

    rows = db.get_latest_observations_by_county(county)
    return jsonify(
        source=DATA_SOURCE,
        county=county,
        latest_observation_time=latest_time(rows),
        station_count=len(rows),
        summary={
            "avg_temperature": average(rows, "temperature"),
            "avg_humidity": average(rows, "humidity"),
            "avg_wind_speed": average(rows, "wind_speed"),
            "temperature_station_count": sum(r["temperature"] is not None for r in rows),
        },
        data=rows,
    )


@api.get("/weather/history")
@fresh_observations
def weather_history():
    county_param = request.args.get("county", "").strip()
    if not county_param:
        return error("Query parameter 'county' is required", 400)

    days_param = request.args.get("days", str(DEFAULT_DAYS)).strip()
    if not days_param.isdecimal() or not MIN_DAYS <= int(days_param) <= MAX_DAYS:
        return error(f"Query parameter 'days' must be an integer between {MIN_DAYS} and {MAX_DAYS}", 400)
    days = int(days_param)

    county = normalize_county(county_param)
    if not db.county_exists(county):
        return error(f"County not found: {county_param}", 404)

    # The window ends at the newest observation in SQLite rather than the
    # system clock, so `days=7` means "the 7 days of data leading up to the
    # latest stored batch" even when ingestion has not run recently.
    # Stored times share CWA's fixed +08:00 offset, so `since` is formatted
    # the same way and compared as a string.
    latest = db.get_latest_observation_time()
    if latest is None:
        since, rows = None, []
    else:
        since = (datetime.fromisoformat(latest) - timedelta(days=days)).isoformat(timespec="seconds")
        rows = [finite_row(r) for r in db.get_county_history(county, since, latest)]

    return jsonify(
        source=DATA_SOURCE,
        county=county,
        days=days,
        latest_observation_time=latest,
        since=since,
        until=latest,
        available_points=len(rows),
        count=len(rows),
        data=rows,
    )


@api.get("/stations")
def stations():
    rows = db.get_stations()
    return jsonify(count=len(rows), data=rows)


@api.get("/weather/station/<station_id>")
@fresh_observations
def weather_station(station_id: str):
    station = db.get_station(station_id.strip())
    if station is None:
        return error(f"Station not found: {station_id}", 404)

    latest = db.get_latest_observation_for_station(station["station_id"])
    observation = {k: latest[k] for k in OBSERVATION_FIELDS} if latest else None
    return jsonify(source=DATA_SOURCE, station=station, observation=observation)


@api.get("/rainfall/latest")
def rainfall_latest():
    try:
        rows = get_latest_rainfall()
    except CWAError as e:
        # CWAError messages never contain the API key (see backend.cwa_api).
        return jsonify(success=False, error=str(e)), 502
    return jsonify(
        success=True,
        source=f"CWA {RAINFALL_DATASET_ID}",
        unit=UNIT,
        latest_observation_time=latest_time(rows),
        count=len(rows),
        valid_count=sum(r["rainfall"] is not None for r in rows),
        data=rows,
    )


@api.get("/typhoon/latest")
def typhoon_latest():
    try:
        data, refresh_error = get_latest_typhoons()
    except CWAError as e:
        # CWAError messages never contain the API key (see backend.cwa_api).
        return jsonify(success=False, error=str(e)), 502
    # No active cyclone is a normal state: 200 with an empty list.
    return jsonify(
        success=True,
        **data,
        count=len(data["typhoons"]),
        # Set when CWA could not be refreshed and the last good data is served.
        refresh_error=refresh_error,
    )


@api.get("/radar/history")
def radar_history():
    refresh_error = radar.ensure_recent(db.DB_PATH, radar.DATA_DIR)
    frames = radar.available_frames(db.DB_PATH, radar.DATA_DIR)
    return jsonify(
        source=radar.SOURCE,
        projection=radar.PROJECTION,
        # Leaflet ImageOverlay bounds: [[south, west], [north, east]].
        bounds=[[radar.SOUTH, radar.WEST], [radar.NORTH, radar.EAST]],
        latest_timestamp=frames[-1]["timestamp"] if frames else None,
        count=len(frames),
        frames=[
            {
                "timestamp": f["timestamp"],
                "image_url": url_for("api.radar_frame", filename=radar.frame_file_name(f["timestamp"])),
                "source": f["source"],
            }
            for f in frames
        ],
        refresh_error=refresh_error,
    )


@api.get("/radar/frames/<filename>")
def radar_frame(filename: str):
    # Only files of frames that are currently listed are served.
    if filename not in {radar.frame_file_name(f["timestamp"]) for f in radar.available_frames(db.DB_PATH, radar.DATA_DIR)}:
        abort(404)
    # A frame's image never changes once stored.
    return send_from_directory(radar.frame_dir(radar.DATA_DIR), filename, mimetype="image/png", max_age=7200)
