import logging
import os

from flask import Flask, jsonify, request
from flask_cors import CORS
from werkzeug.serving import is_running_from_reloader

from backend import radar
from backend.db import init_db
from backend.routes import api


def create_app(start_radar_collector: bool = False) -> Flask:
    app = Flask(__name__)
    app.json.ensure_ascii = False
    CORS(app, resources={r"/api/*": {"origins": "*"}})
    init_db()

    @app.get("/api/health")
    def health():
        return jsonify(status="ok", service="Taiwan Climate GIS Dashboard")

    app.register_blueprint(api)

    if start_radar_collector:
        # Fetches the current radar frame now, then every 10 minutes.
        radar.start_collector()

    @app.errorhandler(404)
    def not_found(e):
        if request.path.startswith("/api/"):
            return jsonify(error="Not found"), 404
        return e

    @app.errorhandler(405)
    def method_not_allowed(e):
        if request.path.startswith("/api/"):
            return jsonify(error="Method not allowed"), 405
        return e

    return app


def should_start_radar_collector() -> bool:
    """RADAR_COLLECTOR=0 disables it. Under `python -m backend.app` (debug
    reloader) only the reloader's child process, which serves requests, runs it."""
    if os.environ.get("RADAR_COLLECTOR", "1") == "0":
        return False
    return __name__ != "__main__" or is_running_from_reloader()


logging.basicConfig(level=logging.INFO)
app = create_app(start_radar_collector=should_start_radar_collector())


if __name__ == "__main__":
    app.run(debug=True, port=5000)
