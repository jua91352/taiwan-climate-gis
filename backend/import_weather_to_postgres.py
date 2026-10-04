"""One-off import of the local SQLite weather history into PostgreSQL.

Copies Station and WeatherObservation from a SQLite file (default
data/weather.db, opened read-only) into the PostgreSQL database at
DATABASE_URL. Rows go through backend.db.save_observations, one transaction
per observation time, so the UNIQUE(station_id, observation_time) index skips
pairs that are already there: running it again, or after an interruption,
adds no duplicates. RadarFrame is never read or written.

Needs DATABASE_BACKEND=postgres and DATABASE_URL in the environment.
Dry run (default, writes nothing):  python -m backend.import_weather_to_postgres
Import:                             python -m backend.import_weather_to_postgres --execute
"""
import argparse
import sqlite3
import sys
from itertools import groupby
from pathlib import Path
from urllib.parse import urlsplit

from backend import db

DEFAULT_SOURCE = Path(__file__).resolve().parent.parent / "data" / "weather.db"

_SOURCE_ROWS = """
    SELECT s.station_id, s.station_name, s.county_name, s.town_name, s.latitude, s.longitude,
           w.observation_time, w.temperature, w.humidity, w.wind_speed, w.wind_direction,
           w.uv_index, w.precipitation, w.weather
    FROM WeatherObservation w
    JOIN Station s ON s.station_id = w.station_id
    ORDER BY w.observation_time, w.station_id
"""


def read_source(source: Path) -> tuple[dict, list[dict]]:
    """(counts, observation records joined with their station), read-only."""
    conn = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        count = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
        counts = {
            "stations": count("SELECT COUNT(*) FROM Station"),
            "observations": count("SELECT COUNT(*) FROM WeatherObservation"),
            # save_observations stores stations together with an observation,
            # so these two kinds of rows cannot be carried over.
            "stations_without_observations": count(
                "SELECT COUNT(*) FROM Station WHERE station_id NOT IN (SELECT station_id FROM WeatherObservation)"),
            "observations_without_station": count(
                "SELECT COUNT(*) FROM WeatherObservation WHERE station_id NOT IN (SELECT station_id FROM Station)"),
        }
        return counts, [dict(row) for row in conn.execute(_SOURCE_ROWS)]
    finally:
        conn.close()


def read_target() -> tuple[dict, dict]:
    """(counts, {(station_id, observation_time): weather is NULL}) in PostgreSQL."""
    conn = db._weather_connection()
    try:
        if conn.execute("SELECT to_regclass('weatherobservation') AS t").fetchone()["t"] is None:
            return {"stations": 0, "observations": 0, "duplicate_pairs": 0, "tables_exist": False}, {}
        count = lambda sql: conn.execute(sql).fetchone()["n"]  # noqa: E731
        counts = {
            "stations": count("SELECT COUNT(*) AS n FROM Station"),
            "observations": count("SELECT COUNT(*) AS n FROM WeatherObservation"),
            "duplicate_pairs": count(
                "SELECT COUNT(*) AS n FROM (SELECT 1 FROM WeatherObservation "
                "GROUP BY station_id, observation_time HAVING COUNT(*) > 1) d"),
            "tables_exist": True,
        }
        stored = {
            (row["station_id"], row["observation_time"]): row["weather_missing"]
            for row in conn.execute(
                "SELECT station_id, observation_time, weather IS NULL AS weather_missing FROM WeatherObservation")
        }
        return counts, stored
    finally:
        conn.close()


def plan(records: list[dict], stored: dict) -> dict:
    """What an import would do, judged from what PostgreSQL holds now."""
    new = [r for r in records if (r["station_id"], r["observation_time"]) not in stored]
    fill = [r for r in records
            if stored.get((r["station_id"], r["observation_time"])) and r["weather"] is not None]
    return {
        "observations_to_insert": len(new),
        "observations_already_present": len(records) - len(new),
        "weather_texts_to_fill": len(fill),
        "stations_to_insert": len({r["station_id"] for r in new} - {key[0] for key in stored}),
        "batches": len({r["observation_time"] for r in records}),
    }


def run(source: Path = DEFAULT_SOURCE, execute: bool = False) -> dict:
    """Count, plan, and with execute=True import and verify. Returns the report."""
    if db.DATABASE_BACKEND != "postgres":
        raise RuntimeError("Set DATABASE_BACKEND=postgres and DATABASE_URL to the target database")
    if not source.is_file():
        raise FileNotFoundError(f"SQLite source not found: {source}")

    source_counts, records = read_source(source)
    before, stored = read_target()
    report = {"source": source_counts, "target_before": before, "plan": plan(records, stored), "executed": execute}
    if not execute:
        return report

    db._init_postgres_weather()  # PostgreSQL tables only; the SQLite file is not touched
    totals: dict[str, int] = {}
    for _, batch in groupby(records, key=lambda r: r["observation_time"]):
        for key, value in db.save_observations(list(batch)).items():
            totals[key] = totals.get(key, 0) + value
    report["saved"] = totals

    report["source_after"], _ = read_source(source)
    report["target_after"], stored = read_target()
    missing = [r for r in records if (r["station_id"], r["observation_time"]) not in stored]
    report["verification"] = {
        "source_pairs_missing_in_target": len(missing),
        "duplicate_pairs_in_target": report["target_after"]["duplicate_pairs"],
        "ok": not missing and report["target_after"]["duplicate_pairs"] == 0,
    }
    return report


def _target_label() -> str:
    """Host and database name only; the password is never printed."""
    url = urlsplit(db.DATABASE_URL or "")
    return f"{url.hostname}:{url.port or 5432}{url.path}"


def _print_report(report: dict) -> None:
    src, before = report["source"], report["target_before"]
    print(f"Target PostgreSQL: {_target_label()}")
    print("Before import:")
    print(f"  SQLite     Station={src['stations']:>6}  WeatherObservation={src['observations']:>7}")
    print(f"  PostgreSQL Station={before['stations']:>6}  WeatherObservation={before['observations']:>7}"
          + ("" if before["tables_exist"] else "  (tables not created yet)"))
    if src["stations_without_observations"] or src["observations_without_station"]:
        print(f"  WARNING: not importable: {src['stations_without_observations']} station(s) without observations, "
              f"{src['observations_without_station']} observation(s) without a station")
    print("Plan:")
    for key, value in report["plan"].items():
        print(f"  {key}: {value}")
    if not report["executed"]:
        print("Dry run: nothing was written. Re-run with --execute to import.")
        return
    after, src_after = report["target_after"], report["source_after"]
    print(f"Saved: {report['saved']}")
    print("After import:")
    print(f"  SQLite     Station={src_after['stations']:>6}  WeatherObservation={src_after['observations']:>7}")
    print(f"  PostgreSQL Station={after['stations']:>6}  WeatherObservation={after['observations']:>7}")
    print(f"Verification: {report['verification']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="SQLite file (default: data/weather.db)")
    parser.add_argument("--execute", action="store_true", help="write to PostgreSQL (default: dry run)")
    args = parser.parse_args(argv)
    try:
        report = run(args.source, args.execute)
    except (RuntimeError, FileNotFoundError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    _print_report(report)
    return 0 if not args.execute or report["verification"]["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
