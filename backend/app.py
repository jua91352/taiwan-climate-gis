from flask import Flask, jsonify
from flask_cors import CORS


def create_app() -> Flask:
    app = Flask(__name__)
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    @app.get("/api/health")
    def health():
        return jsonify(status="ok", service="Taiwan Climate GIS Dashboard")

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True, port=5000)
