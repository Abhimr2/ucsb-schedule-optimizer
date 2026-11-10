import os
import secrets
import sqlite3
from pathlib import Path

from flask import Flask, abort, current_app, g, jsonify, request, send_from_directory

from api_service import ApiError, CatalogService
from catalog_database import connect_catalog


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "instance" / "catalog.db"
DEFAULT_FRONTEND_DIST_PATH = PROJECT_ROOT / "frontend" / "dist"
DEFAULT_CORS_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:5180",
    "http://localhost:5180",
)


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        DATABASE_PATH=os.environ.get(
            "SCHEDULER_DATABASE_PATH",
            str(DEFAULT_DATABASE_PATH),
        ),
        FRONTEND_DIST_PATH=os.environ.get(
            "FRONTEND_DIST_PATH",
            str(DEFAULT_FRONTEND_DIST_PATH),
        ),
        ACTIVE_QUARTER="20271",
        API_TERM="2027-winter",
        TERM_LABEL="Winter 2027",
        COMBINATION_LIMIT=1_000_000,
        CORS_ORIGINS=DEFAULT_CORS_ORIGINS,
    )

    if config:
        app.config.update(config)

    app.json.sort_keys = False

    @app.before_request
    def assign_request_id():
        g.request_id = f"req_{secrets.token_hex(12)}"

    @app.teardown_appcontext
    def close_database(_error):
        connection = g.pop("catalog_connection", None)
        if connection is not None:
            connection.close()

    @app.after_request
    def add_cors_headers(response):
        origin = request.headers.get("Origin")
        allowed_origins = current_app.config["CORS_ORIGINS"]

        if origin in allowed_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers.add("Vary", "Origin")

        response.headers["X-Request-ID"] = g.get("request_id", "")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )

        if request.path.startswith("/assets/") and response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif not request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache"

        return response

    @app.errorhandler(ApiError)
    def handle_api_error(error):
        return jsonify(_error_payload(error)), error.status_code

    @app.errorhandler(sqlite3.Error)
    def handle_database_error(_error):
        error = ApiError(
            503,
            "DATA_UNAVAILABLE",
            "Course data is temporarily unavailable.",
            retryable=True,
        )
        return jsonify(_error_payload(error)), error.status_code

    @app.errorhandler(404)
    def handle_not_found(_error):
        error = ApiError(
            404,
            "NOT_FOUND",
            "The requested API resource was not found.",
        )
        return jsonify(_error_payload(error)), error.status_code

    @app.errorhandler(405)
    def handle_method_not_allowed(_error):
        error = ApiError(
            405,
            "METHOD_NOT_ALLOWED",
            "The requested method is not supported for this resource.",
        )
        return jsonify(_error_payload(error)), error.status_code

    @app.errorhandler(Exception)
    def handle_unexpected_error(_error):
        current_app.logger.exception("Unhandled request failure")
        error = ApiError(
            500,
            "INTERNAL_ERROR",
            "The server could not complete the request.",
            retryable=True,
        )
        return jsonify(_error_payload(error)), error.status_code

    @app.get("/api/v1/status")
    def status():
        return jsonify(_service().status_payload())

    @app.get("/api/v1/courses")
    def search_courses():
        _validate_term(request.args.get("term"))
        limit = _query_integer("limit", default=20)
        payload = _service().search_courses(
            request.args.get("q", ""),
            limit=limit,
            cursor=request.args.get("cursor"),
        )
        return jsonify(payload)

    @app.get("/api/v1/courses/<path:course_code>")
    def course_detail(course_code):
        _validate_term(request.args.get("term"))
        return jsonify(_service().course_detail(course_code))

    @app.post("/api/v1/schedules/generate")
    def generate_schedules():
        payload = request.get_json(silent=True)
        if payload is None:
            raise ApiError(
                400,
                "INVALID_REQUEST",
                "The request body must contain a JSON object.",
                {"body": "Provide a valid JSON object."},
            )
        return jsonify(_service().generate_schedules(payload))

    @app.get("/")
    def frontend_index():
        return _frontend_file("index.html")

    @app.get("/<path:frontend_path>")
    def frontend_route(frontend_path):
        if frontend_path.startswith("api/"):
            abort(404)

        frontend_dist = Path(current_app.config["FRONTEND_DIST_PATH"]).resolve()
        requested_path = (frontend_dist / frontend_path).resolve()

        if frontend_dist not in requested_path.parents:
            abort(404)
        if requested_path.is_file():
            return send_from_directory(frontend_dist, frontend_path)
        if requested_path.suffix:
            abort(404)

        return _frontend_file("index.html")

    return app


def _frontend_file(filename):
    frontend_dist = Path(current_app.config["FRONTEND_DIST_PATH"])
    return send_from_directory(frontend_dist, filename)


def _database():
    connection = g.get("catalog_connection")
    if connection is not None:
        return connection

    database_path = Path(current_app.config["DATABASE_PATH"]).expanduser()
    if not database_path.is_file():
        raise ApiError(
            503,
            "DATA_UNAVAILABLE",
            "Course data is temporarily unavailable.",
            retryable=True,
        )

    connection = connect_catalog(database_path)
    g.catalog_connection = connection
    return connection


def _service():
    return CatalogService(
        _database(),
        quarter=current_app.config["ACTIVE_QUARTER"],
        term=current_app.config["API_TERM"],
        term_label=current_app.config["TERM_LABEL"],
        combination_limit=current_app.config["COMBINATION_LIMIT"],
    )


def _validate_term(term):
    if term is None:
        return
    if term != current_app.config["API_TERM"]:
        raise ApiError(
            409,
            "TERM_UNAVAILABLE",
            "The requested term is not available.",
            {"term": f"Use {current_app.config['API_TERM']}."},
        )


def _query_integer(name, default):
    value = request.args.get(name)
    if value is None:
        return default

    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ApiError(
            400,
            "INVALID_REQUEST",
            "One or more query parameters are invalid.",
            {name: "Must be an integer."},
        ) from error


def _error_payload(error):
    return {
        "error": {
            "code": error.code,
            "message": error.message,
            "field_errors": error.field_errors,
            "request_id": g.get("request_id"),
            "retryable": error.retryable,
        }
    }


app = create_app()


if __name__ == "__main__":
    app.run(
        host=os.environ.get("API_HOST", "127.0.0.1"),
        port=int(os.environ.get("API_PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )
