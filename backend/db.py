import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "weather.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS Station (
    station_id TEXT PRIMARY KEY,
    station_name TEXT NOT NULL,
    county_name TEXT,
    town_name TEXT,
    latitude REAL,
    longitude REAL
);

CREATE TABLE IF NOT EXISTS WeatherObservation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    station_id TEXT NOT NULL,
    observation_time TEXT NOT NULL,
    temperature REAL,
    humidity REAL,
    wind_speed REAL,
    wind_direction REAL,
    uv_index REAL,
    precipitation REAL,
    FOREIGN KEY (station_id) REFERENCES Station(station_id)
);
"""


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Open a connection with foreign key enforcement enabled."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: Path = DB_PATH) -> Path:
    """Create the database file and tables if missing. Safe to run repeatedly."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
    return db_path


def save_observations(records: list[dict], db_path: Path = DB_PATH) -> dict:
    """Upsert stations and insert new observations in a single transaction.

    Observations already stored for the same (station_id, observation_time)
    are skipped. Any database error rolls back the whole batch.
    """
    stats = {
        "stations_inserted": 0,
        "stations_updated": 0,
        "observations_inserted": 0,
        "observations_skipped_duplicate": 0,
    }
    conn = get_connection(db_path)
    try:
        with conn:  # commit on success, rollback on exception
            for r in records:
                exists = conn.execute(
                    "SELECT 1 FROM Station WHERE station_id = ?", (r["station_id"],)
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO Station
                        (station_id, station_name, county_name, town_name, latitude, longitude)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(station_id) DO UPDATE SET
                        station_name = excluded.station_name,
                        county_name = excluded.county_name,
                        town_name = excluded.town_name,
                        latitude = excluded.latitude,
                        longitude = excluded.longitude
                    """,
                    (r["station_id"], r["station_name"], r["county_name"],
                     r["town_name"], r["latitude"], r["longitude"]),
                )
                stats["stations_updated" if exists else "stations_inserted"] += 1

                duplicate = conn.execute(
                    "SELECT 1 FROM WeatherObservation WHERE station_id = ? AND observation_time = ?",
                    (r["station_id"], r["observation_time"]),
                ).fetchone()
                if duplicate:
                    stats["observations_skipped_duplicate"] += 1
                    continue
                conn.execute(
                    """
                    INSERT INTO WeatherObservation
                        (station_id, observation_time, temperature, humidity,
                         wind_speed, wind_direction, uv_index, precipitation)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (r["station_id"], r["observation_time"], r["temperature"],
                     r["humidity"], r["wind_speed"], r["wind_direction"],
                     r["uv_index"], r["precipitation"]),
                )
                stats["observations_inserted"] += 1
    finally:
        conn.close()
    return stats
