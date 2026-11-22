import importlib
from flask import Flask, jsonify, redirect, render_template, session, url_for

from app.config import Config
from app.extensions.database import db
from app.extensions.redis_client import redis_client
from app.services.auth_factory import role_required


BLUEPRINT_SPECS = [
    ("app.controllers.auth_controller", "auth_bp"),
    ("app.controllers.admin_controller", "admin_bp"),
    ("app.controllers.lecturer_controller", "lecturer_bp"),
    ("app.controllers.student_controller", "student_bp"),
    ("app.controllers.attendance_controller", "attendance_bp"),
]


def _register_blueprints(app: Flask) -> None:
    """Register all available controller blueprints safely."""
    for module_path, bp_name in BLUEPRINT_SPECS:
        try:
            module = importlib.import_module(module_path)
            blueprint = getattr(module, bp_name, None)
            if blueprint is not None:
                app.register_blueprint(blueprint)
        except ImportError:
            continue


def create_app(config_class=Config) -> Flask:
    """Application factory for the Student Attendance Management System."""
    app = Flask(
        __name__,
        template_folder="views/templates",
        static_folder="views/static",
    )
    app.config.from_object(config_class)

    db.init_app(app)
    redis_client.init_app(app)

    with app.app_context():
        importlib.import_module("app.models")

    _register_blueprints(app)

    @app.route("/")
    def index():
        if "user_id" in session:
            role = session.get("role", "")
            dashboards = {
                "admin": "/admin",
                "lecturer": "/lecturer",
                "student": "/student",
            }
            return redirect(dashboards.get(role, "/login"))
        if "auth.login" in app.view_functions:
            return redirect(url_for("auth.login"))
        return jsonify({"status": "running", "message": "Attendance System API"}), 200

    @app.route("/health")
    def health_check():
        return jsonify({"status": "ok"}), 200

    # Fallback dashboard routes if role controllers haven't registered them yet
    registered_rules = {rule.rule for rule in app.url_map.iter_rules()}

    if "/admin" not in registered_rules:
        @app.route("/admin")
        @role_required("admin")
        def admin_dashboard_fallback():
            return render_template("admin/semester_overview.html")

    if "/lecturer" not in registered_rules:
        @app.route("/lecturer")
        @role_required("lecturer")
        def lecturer_dashboard_fallback():
            return render_template("lecturer/assigned_sections.html")

    if "/student" not in registered_rules:
        @app.route("/student")
        @role_required("student")
        def student_dashboard_fallback():
            return render_template("student/courses_checkin.html")

    return app