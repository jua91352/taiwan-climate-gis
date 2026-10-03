import os
import sqlite3
import tempfile
from pathlib import Path


def _default_db_path() -> Path:
    """data/weather.db locally. On Vercel (VERCEL is set) the deployment is
    read-only and only the temp directory (/tmp) is writable, so the database
    lives there: it starts empty on each new instance and is not kept or shared.
    WEATHER_DB_PATH overrides both."""
    if os.environ.get("WEATHER_DB_PATH"):
        return Path(os.environ["WEATHER_DB_PATH"])
    if os.environ.get("VERCEL"):
        return Path(tempfile.gettempdir()) / "weather.db"
    return Path(__file__).resolve().parent.parent / "data" / "weather.db"


DB_PATH = _default_db_path()

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
    weather TEXT,
    FOREIGN KEY (station_id) REFERENCES Station(station_id)
);

-- "Latest observation per station" looks up MAX(observation_time) per station
-- for every row; without this index that is a full scan per row and grows
-- quadratically with history. Created on existing databases by init_db().
CREATE INDEX IF NOT EXISTS idx_observation_station_time
    ON WeatherObservation (station_id, observation_time);

-- Radar frames: metadata only, the PNG lives on disk (file_path is relative
-- to the data directory).
CREATE TABLE IF NOT EXISTS RadarFrame (
    timestamp TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    source TEXT NOT NULL
);
"""

# Columns added after the first release, applied to existing databases by
# init_db(). Additive and nullable only: older rows keep NULL, nothing is rewritten.
MIGRATIONS = (("WeatherObservation", "weather", "TEXT"),)


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
        for table, column, sql_type in MIGRATIONS:
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
        conn.commit()
    finally:
        conn.close()
    return db_path


def save_observations(records: list[dict], db_path: Path = DB_PATH) -> dict:
    """Upsert stations and insert new observations in a single transaction.

    Observations already stored for the same (station_id, observation_time)
    are skipped (only a missing weather text is filled in, e.g. for rows stored
    before that column existed). Any database error rolls back the whole batch.
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
                    if r.get("weather") is not None:
                        conn.execute(
                            "UPDATE WeatherObservation SET weather = ? "
                            "WHERE station_id = ? AND observation_time = ? AND weather IS NULL",
                            (r["weather"], r["station_id"], r["observation_time"]),
                        )
                    continue
                conn.execute(
                    """
                    INSERT INTO WeatherObservation
                        (station_id, observation_time, temperature, humidity,
                         wind_speed, wind_direction, uv_index, precipitation, weather)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (r["station_id"], r["observation_time"], r["temperature"],
                     r["humidity"], r["wind_speed"], r["wind_direction"],
                     r["uv_index"], r["precipitation"], r.get("weather")),
                )
                stats["observations_inserted"] += 1
    finally:
        conn.close()
    return stats


# ---------------------------------------------------------------------------
# Read-only queries used by the Flask API. All values are bound as parameters.
# observation_time is stored as an ISO 8601 string with a fixed +08:00 offset,
# so string comparison orders it chronologically.
# ---------------------------------------------------------------------------

_OBSERVATION_COLUMNS = """
    s.station_id, s.station_name, s.county_name, s.town_name, s.latitude, s.longitude,
    w.observation_time, w.temperature, w.humidity, w.wind_speed, w.wind_direction,
    w.uv_index, w.precipitation, w.weather
"""

_LATEST_PER_STATION = f"""
    SELECT {_OBSERVATION_COLUMNS}
    FROM WeatherObservation w
    JOIN Station s ON s.station_id = w.station_id
    WHERE w.observation_time = (
        SELECT MAX(w2.observation_time) FROM WeatherObservation w2
        WHERE w2.station_id = w.station_id
    )
"""


def _query(sql: str, params: tuple = (), db_path: Path = DB_PATH) -> list[dict]:
    conn = get_connection(db_path)
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def get_stations(db_path: Path = DB_PATH) -> list[dict]:
    return _query(
        "SELECT station_id, station_name, county_name, town_name, latitude, longitude "
        "FROM Station ORDER BY station_id",
        db_path=db_path,
    )


def get_station(station_id: str, db_path: Path = DB_PATH) -> dict | None:
    rows = _query(
        "SELECT station_id, station_name, county_name, town_name, latitude, longitude "
        "FROM Station WHERE station_id = ?",
        (station_id,),
        db_path,
    )
    return rows[0] if rows else None


def get_latest_observations(db_path: Path = DB_PATH) -> list[dict]:
    """Most recent observation of every station."""
    return _query(_LATEST_PER_STATION + " ORDER BY s.station_id", db_path=db_path)


def get_latest_observations_by_county(county_name: str, db_path: Path = DB_PATH) -> list[dict]:
    return _query(
        _LATEST_PER_STATION + " AND s.county_name = ? ORDER BY s.station_id",
        (county_name,),
        db_path,
    )


def get_latest_observation_for_station(station_id: str, db_path: Path = DB_PATH) -> dict | None:
    rows = _query(
        _LATEST_PER_STATION + " AND s.station_id = ?",
        (station_id,),
        db_path,
    )
    return rows[0] if rows else None


def county_exists(county_name: str, db_path: Path = DB_PATH) -> bool:
    return bool(_query("SELECT 1 FROM Station WHERE county_name = ? LIMIT 1", (county_name,), db_path))


def get_latest_observation_time(db_path: Path = DB_PATH) -> str | None:
    """Newest observation_time stored in SQLite, or None if there is no data."""
    rows = _query("SELECT MAX(observation_time) AS latest FROM WeatherObservation", db_path=db_path)
    return rows[0]["latest"]


def get_county_history(county_name: str, since: str, until: str, db_path: Path = DB_PATH) -> list[dict]:
    """County-level aggregates per observation time in the window (since, until].

    AVG/MIN/MAX ignore NULL measurements and yield NULL when every station's
    value at that time point is NULL, so no value is invented.
    """
    return _query(
        """
        SELECT w.observation_time,
               COUNT(*) AS station_count,
               COUNT(w.temperature) AS temperature_count,
               ROUND(AVG(w.temperature), 1) AS avg_temperature,
               MIN(w.temperature) AS min_temperature,
               MAX(w.temperature) AS max_temperature,
               ROUND(AVG(w.humidity), 1) AS avg_humidity,
               ROUND(AVG(w.wind_speed), 1) AS avg_wind_speed,
               ROUND(AVG(w.precipitation), 1) AS avg_precipitation
        FROM WeatherObservation w
        JOIN Station s ON s.station_id = w.station_id
        WHERE s.county_name = ? AND w.observation_time > ? AND w.observation_time <= ?
        GROUP BY w.observation_time
        ORDER BY w.observation_time
        """,
        (county_name, since, until),
        db_path,
    )


# ---------------------------------------------------------------------------
# Radar frame metadata. timestamp is CWA's ISO 8601 time with its fixed +08:00
# offset, so string comparison orders it chronologically.
# ---------------------------------------------------------------------------

def radar_frame_exists(timestamp: str, db_path: Path = DB_PATH) -> bool:
    return bool(_query("SELECT 1 FROM RadarFrame WHERE timestamp = ?", (timestamp,), db_path))


def insert_radar_frame(timestamp: str, file_path: str, source: str, db_path: Path = DB_PATH) -> bool:
    """Store one frame's metadata; False if that timestamp is already stored."""
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO RadarFrame (timestamp, file_path, source) VALUES (?, ?, ?)",
                (timestamp, file_path, source),
            )
            return cursor.rowcount == 1
    finally:
        conn.close()


def get_radar_frames(db_path: Path = DB_PATH) -> list[dict]:
    """All stored frames, oldest first."""
    return _query("SELECT timestamp, file_path, source FROM RadarFrame ORDER BY timestamp", db_path=db_path)


def delete_radar_frames(timestamps: list[str], db_path: Path = DB_PATH) -> None:
    conn = get_connection(db_path)
    try:
        with conn:
            conn.executemany("DELETE FROM RadarFrame WHERE timestamp = ?", [(t,) for t in timestamps])
    finally:
        conn.close()
