"""Weather REST API. All data is read from SQLite; CWA is never called here."""
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

from backend import db

api = Blueprint("api", __name__, url_prefix="/api")

DATA_SOURCE = "CWA O-A0003-001"
TW_TZ = timezone(timedelta(hours=8))
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


def average(rows: list[dict], field: str) -> float | None:
    values = [r[field] for r in rows if r[field] is not None]
    return round(sum(values) / len(values), 1) if values else None


@api.get("/weather/latest")
def weather_latest():
    rows = db.get_latest_observations()
    return jsonify(
        source=DATA_SOURCE,
        latest_observation_time=latest_time(rows),
        count=len(rows),
        data=rows,
    )


@api.get("/weather/county/<county_name>")
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

    since = (datetime.now(TW_TZ) - timedelta(days=days)).isoformat(timespec="seconds")
    rows = db.get_county_history(county, since)
    return jsonify(
        source=DATA_SOURCE,
        county=county,
        days=days,
        since=since,
        count=len(rows),
        data=rows,
    )


@api.get("/stations")
def stations():
    rows = db.get_stations()
    return jsonify(count=len(rows), data=rows)


@api.get("/weather/station/<station_id>")
def weather_station(station_id: str):
    station = db.get_station(station_id.strip())
    if station is None:
        return error(f"Station not found: {station_id}", 404)

    latest = db.get_latest_observation_for_station(station["station_id"])
    observation = {k: latest[k] for k in OBSERVATION_FIELDS} if latest else None
    return jsonify(source=DATA_SOURCE, station=station, observation=observation)
