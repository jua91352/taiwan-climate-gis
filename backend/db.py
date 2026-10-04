"""Storage for weather observations (Station, WeatherObservation) and radar
frame metadata (RadarFrame).

DATABASE_BACKEND selects where the weather tables live:
  sqlite (default)  the SQLite file at DB_PATH, as before.
  postgres          the PostgreSQL database at DATABASE_URL (e.g. Neon); the
                    weather tables are never written to the SQLite file.
RadarFrame always stays in the SQLite file at DB_PATH, next to the PNGs it
describes (backend.radar). Every function keeps its db_path parameter: it
names that SQLite file, and in postgres mode the weather functions ignore it.
"""
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

DATABASE_BACKEND = os.environ.get("DATABASE_BACKEND", "").strip().lower() or "sqlite"
if DATABASE_BACKEND not in ("sqlite", "postgres"):
    raise RuntimeError(f"DATABASE_BACKEND must be 'sqlite' or 'postgres', not {DATABASE_BACKEND!r}")

if DATABASE_BACKEND == "postgres":
    import psycopg
    from psycopg.rows import dict_row

    DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
    if not DATABASE_URL:
        # Never fall back to the SQLite file: on Vercel that is /tmp, which loses history.
        raise RuntimeError("DATABASE_BACKEND=postgres requires DATABASE_URL")
    # Errors either backend raises; callers catch this instead of sqlite3.Error.
    DBError: tuple[type[Exception], ...] = (sqlite3.Error, psycopg.Error)
else:
    DATABASE_URL = None
    DBError = (sqlite3.Error,)

SQLITE_WEATHER_SCHEMA = """
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

-- One observation per station and time (save_observations relies on it for
-- ON CONFLICT). It also serves "latest observation per station", which looks
-- up MAX(observation_time) per station for every row; without it that is a
-- full scan per row and grows quadratically with history. Databases created
-- with the earlier non-unique index are upgraded by init_db().
CREATE UNIQUE INDEX IF NOT EXISTS idx_observation_station_time
    ON WeatherObservation (station_id, observation_time);
"""

# Same tables in PostgreSQL. DOUBLE PRECISION matches SQLite's 8-byte REAL
# (PostgreSQL's REAL is 4 bytes). Times stay ISO 8601 TEXT with CWA's fixed
# +08:00 offset, so string comparison orders them on both backends.
POSTGRES_WEATHER_SCHEMA = """
CREATE TABLE IF NOT EXISTS Station (
    station_id TEXT PRIMARY KEY,
    station_name TEXT NOT NULL,
    county_name TEXT,
    town_name TEXT,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS WeatherObservation (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    station_id TEXT NOT NULL REFERENCES Station(station_id),
    observation_time TEXT NOT NULL,
    temperature DOUBLE PRECISION,
    humidity DOUBLE PRECISION,
    wind_speed DOUBLE PRECISION,
    wind_direction DOUBLE PRECISION,
    uv_index DOUBLE PRECISION,
    precipitation DOUBLE PRECISION,
    weather TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_observation_station_time
    ON WeatherObservation (station_id, observation_time);
"""

# Radar frames: metadata only, the PNG lives on disk (file_path is relative
# to the data directory). Always in the SQLite file.
RADAR_SCHEMA = """
CREATE TABLE IF NOT EXISTS RadarFrame (
    timestamp TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    source TEXT NOT NULL
);
"""

# Columns added to the SQLite schema after the first release, applied to
# existing databases by init_db(). Additive and nullable only: older rows keep
# NULL, nothing is rewritten. (PostgreSQL tables were created with them.)
MIGRATIONS = (("WeatherObservation", "weather", "TEXT"),)

# Serialises schema creation when several instances start at once
# (concurrent CREATE ... IF NOT EXISTS can collide in PostgreSQL).
_SCHEMA_LOCK_ID = 7_340_001
_postgres_ready = False


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Open a SQLite connection with foreign key enforcement enabled."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


class _PostgresConnection:
    """psycopg connection with the sqlite3-style calls used here: SQL is
    written once with ? placeholders and rows come back as dicts."""

    def __init__(self):
        # prepare_threshold=None: no server-side prepared statements, which a
        # transaction-mode pooler (Neon's -pooler endpoint) cannot keep.
        self._conn = psycopg.connect(DATABASE_URL, row_factory=dict_row, prepare_threshold=None, connect_timeout=10)

    @staticmethod
    def _sql(sql: str) -> str:
        return sql.replace("%", "%%").replace("?", "%s")

    def execute(self, sql: str, params=()):
        return self._conn.execute(self._sql(sql), params)

    def executemany(self, sql: str, params_seq) -> None:
        with self._conn.cursor() as cursor:
            cursor.executemany(self._sql(sql), params_seq)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


def _weather_connection(db_path: Path = DB_PATH):
    """Connection to wherever the weather tables live."""
    if DATABASE_BACKEND == "postgres":
        return _PostgresConnection()
    return get_connection(db_path)


def _init_sqlite_weather(conn: sqlite3.Connection) -> None:
    conn.executescript(SQLITE_WEATHER_SCHEMA)
    for table, column, sql_type in MIGRATIONS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
    # Databases from before the unique index have a plain one under the same
    # name. Rebuild it as unique; should a station/time pair have been stored
    # twice, the first stored row is kept.
    indexes = {row["name"]: row["unique"] for row in conn.execute("PRAGMA index_list(WeatherObservation)")}
    if not indexes.get("idx_observation_station_time"):
        conn.execute("DROP INDEX IF EXISTS idx_observation_station_time")
        conn.execute(
            "DELETE FROM WeatherObservation WHERE id NOT IN "
            "(SELECT MIN(id) FROM WeatherObservation GROUP BY station_id, observation_time)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX idx_observation_station_time "
            "ON WeatherObservation (station_id, observation_time)"
        )


def _init_postgres_weather() -> None:
    """Create the PostgreSQL weather tables once per process."""
    global _postgres_ready
    if _postgres_ready:
        return
    conn = _PostgresConnection()
    try:
        conn.execute("SELECT pg_advisory_xact_lock(?)", (_SCHEMA_LOCK_ID,))
        for statement in POSTGRES_WEATHER_SCHEMA.split(";"):
            if statement.strip():
                conn.execute(statement)
        conn.commit()
    finally:
        conn.close()
    _postgres_ready = True


def init_db(db_path: Path = DB_PATH) -> Path:
    """Create the database file and tables if missing. Safe to run repeatedly."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path)
    try:
        conn.executescript(RADAR_SCHEMA)
        if DATABASE_BACKEND == "sqlite":
            _init_sqlite_weather(conn)
        conn.commit()
    finally:
        conn.close()
    if DATABASE_BACKEND == "postgres":
        _init_postgres_weather()
    return db_path


_UPSERT_STATION = """
    INSERT INTO Station
        (station_id, station_name, county_name, town_name, latitude, longitude)
    VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT (station_id) DO UPDATE SET
        station_name = excluded.station_name,
        county_name = excluded.county_name,
        town_name = excluded.town_name,
        latitude = excluded.latitude,
        longitude = excluded.longitude
"""

# A station/time pair that is already stored is left as it is, except that a
# missing weather text is filled in (e.g. rows stored before that column existed).
_INSERT_OBSERVATION = """
    INSERT INTO WeatherObservation
        (station_id, observation_time, temperature, humidity,
         wind_speed, wind_direction, uv_index, precipitation, weather)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (station_id, observation_time) DO UPDATE SET
        weather = excluded.weather
    WHERE WeatherObservation.weather IS NULL AND excluded.weather IS NOT NULL
"""


def save_observations(records: list[dict], db_path: Path = DB_PATH) -> dict:
    """Upsert stations and insert new observations in a single transaction.

    Observations already stored for the same (station_id, observation_time)
    are skipped (only a missing weather text is filled in, e.g. for rows stored
    before that column existed). Any database error rolls back the whole batch.
    The unique index guarantees one row per station and time even when several
    instances save at once; the counts come from what was stored beforehand.
    """
    stats = {
        "stations_inserted": 0,
        "stations_updated": 0,
        "observations_inserted": 0,
        "observations_skipped_duplicate": 0,
    }
    if not records:
        return stats
    conn = _weather_connection(db_path)
    try:
        stations = {row["station_id"] for row in conn.execute("SELECT station_id FROM Station").fetchall()}
        times = sorted({r["observation_time"] for r in records})
        stored = {
            (row["station_id"], row["observation_time"])
            for row in conn.execute(
                "SELECT station_id, observation_time FROM WeatherObservation "
                f"WHERE observation_time IN ({', '.join('?' * len(times))})",
                times,
            ).fetchall()
        }
        for r in records:
            station_id = r["station_id"]
            stats["stations_updated" if station_id in stations else "stations_inserted"] += 1
            stations.add(station_id)
            key = (station_id, r["observation_time"])
            stats["observations_skipped_duplicate" if key in stored else "observations_inserted"] += 1
            stored.add(key)

        conn.executemany(_UPSERT_STATION, [
            (r["station_id"], r["station_name"], r["county_name"], r["town_name"], r["latitude"], r["longitude"])
            for r in records
        ])
        conn.executemany(_INSERT_OBSERVATION, [
            (r["station_id"], r["observation_time"], r["temperature"], r["humidity"], r["wind_speed"],
             r["wind_direction"], r["uv_index"], r["precipitation"], r.get("weather"))
            for r in records
        ])
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
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
    """Run a read query against the weather tables."""
    conn = _weather_connection(db_path)
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _sqlite_query(sql: str, params: tuple = (), db_path: Path = DB_PATH) -> list[dict]:
    conn = get_connection(db_path)
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _round1(expression: str) -> str:
    """ROUND(expression, 1) returning a float. PostgreSQL has no round(double
    precision, int); the numeric result is cast back so it stays a JSON number."""
    if DATABASE_BACKEND == "postgres":
        return f"ROUND(({expression})::numeric, 1)::double precision"
    return f"ROUND({expression}, 1)"


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
    """Newest observation_time stored, or None if there is no data."""
    rows = _query("SELECT MAX(observation_time) AS latest FROM WeatherObservation", db_path=db_path)
    return rows[0]["latest"]


def get_county_history(county_name: str, since: str, until: str, db_path: Path = DB_PATH) -> list[dict]:
    """County-level aggregates per observation time in the window (since, until].

    AVG/MIN/MAX ignore NULL measurements and yield NULL when every station's
    value at that time point is NULL, so no value is invented.
    """
    return _query(
        f"""
        SELECT w.observation_time,
               COUNT(*) AS station_count,
               COUNT(w.temperature) AS temperature_count,
               {_round1("AVG(w.temperature)")} AS avg_temperature,
               MIN(w.temperature) AS min_temperature,
               MAX(w.temperature) AS max_temperature,
               {_round1("AVG(w.humidity)")} AS avg_humidity,
               {_round1("AVG(w.wind_speed)")} AS avg_wind_speed,
               {_round1("AVG(w.precipitation)")} AS avg_precipitation
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
# Radar frame metadata, always in the SQLite file. timestamp is CWA's ISO 8601
# time with its fixed +08:00 offset, so string comparison orders it chronologically.
# ---------------------------------------------------------------------------

def radar_frame_exists(timestamp: str, db_path: Path = DB_PATH) -> bool:
    return bool(_sqlite_query("SELECT 1 FROM RadarFrame WHERE timestamp = ?", (timestamp,), db_path))


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
    return _sqlite_query("SELECT timestamp, file_path, source FROM RadarFrame ORDER BY timestamp", db_path=db_path)


def delete_radar_frames(timestamps: list[str], db_path: Path = DB_PATH) -> None:
    conn = get_connection(db_path)
    try:
        with conn:
            conn.executemany("DELETE FROM RadarFrame WHERE timestamp = ?", [(t,) for t in timestamps])
    finally:
        conn.close()
