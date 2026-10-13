from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from flask import (
    Flask,
    jsonify,
    request,
    render_template
)
from werkzeug.utils import secure_filename

from pdf_validator import build_report, compare_documents, parse_document

BASE_DIR = Path(__file__).resolve().parent
app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static"
)
app.config["MAX_CONTENT_LENGTH"] = 80 * 1024 * 1024
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


@app.get("/")
def index():
    return render_template(
        "index.html"
    )


@app.get("/api/health")
def health():
    return jsonify({"ok": True})


@app.post("/api/validate")
def validate():
    app_file = request.files.get("application")
    ill_file = request.files.get("illustration")
    state = (request.form.get("state") or "").strip().upper()

    if not app_file or not ill_file:
        return jsonify({"error": "Both application and illustration PDFs are required."}), 400
    if not app_file.filename.lower().endswith(".pdf") or not ill_file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Both uploaded files must be PDFs."}), 400

    with tempfile.TemporaryDirectory(prefix="illustration-validator-") as folder:
        temp_dir = Path(folder)
        app_path = temp_dir / secure_filename(app_file.filename or "application.pdf")
        ill_path = temp_dir / secure_filename(ill_file.filename or "illustration.pdf")
        app_file.save(app_path)
        ill_file.save(ill_path)

        application = parse_document(str(app_path), "app")
        illustration = parse_document(str(ill_path), "ill")
        rows = compare_documents(application, illustration, state)
        report = build_report(application, illustration, rows, state)
        return jsonify(report)


@app.errorhandler(413)
def too_large(_error):
    return jsonify({"error": "The upload is too large. The combined limit is 80 MB."}), 413


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
