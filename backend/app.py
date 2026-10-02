from flask import Flask, jsonify, request
from flask_cors import CORS

from backend.db import init_db
from backend.routes import api


def create_app() -> Flask:
    app = Flask(__name__)
    app.json.ensure_ascii = False
    CORS(app, resources={r"/api/*": {"origins": "*"}})
    init_db()

    @app.get("/api/health")
    def health():
        return jsonify(status="ok", service="Taiwan Climate GIS Dashboard")

    app.register_blueprint(api)

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


app = create_app()


if __name__ == "__main__":
    app.run(debug=True, port=5000)
