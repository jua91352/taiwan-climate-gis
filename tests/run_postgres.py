"""Run the backend tests with DATABASE_BACKEND=postgres against a throwaway
local PostgreSQL server, started and removed by pgserver.

pgserver is a development tool only (not in requirements.txt):
    uv pip install pgserver
Run: python tests/run_postgres.py   (extra arguments go to unittest, e.g. -v)
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    try:
        import pgserver
    except ImportError:
        sys.exit("pgserver is not installed: uv pip install pgserver")

    modules = sorted(f"tests.{path.stem}" for path in (ROOT / "tests").glob("test_*.py"))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as data_dir:
        server = pgserver.get_server(data_dir, cleanup_mode="stop")
        try:
            env = {**os.environ, "DATABASE_BACKEND": "postgres", "DATABASE_URL": server.get_uri()}
            return subprocess.call([sys.executable, "-m", "unittest", *modules, *sys.argv[1:]], cwd=ROOT, env=env)
        finally:
            server.cleanup()


if __name__ == "__main__":
    sys.exit(main())
