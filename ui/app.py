# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Flask UI for PolicyDriftChecker.

A thin presentation layer that forwards uploaded documents to the
megaservice gateway and renders the structured drift report.
"""

import os

import requests
from flask import Flask, render_template, request

app = Flask(__name__)

# Gateway (megaservice) base URL.
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8888")
DRIFT_ENDPOINT = f"{BACKEND_URL}/v1/drift_check"
UI_PORT = int(os.getenv("UI_PORT", "5173"))


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/check", methods=["POST"])
def check():
    global_doc = request.files.get("global_doc")
    sop_doc = request.files.get("sop_doc")
    regional_doc = request.files.get("regional_doc")

    if not global_doc or not global_doc.filename:
        return render_template("index.html", error="Please upload a Global policy document.")
    if not sop_doc or not sop_doc.filename:
        return render_template("index.html", error="Please upload an SOP document.")

    files = {
        "global_doc": (global_doc.filename, global_doc.stream, global_doc.mimetype),
        "sop_doc": (sop_doc.filename, sop_doc.stream, sop_doc.mimetype),
    }
    if regional_doc and regional_doc.filename:
        files["regional_doc"] = (
            regional_doc.filename,
            regional_doc.stream,
            regional_doc.mimetype,
        )

    try:
        resp = requests.post(DRIFT_ENDPOINT, files=files, timeout=900)
    except requests.RequestException as exc:
        return render_template("index.html", error=f"Could not reach backend: {exc}")

    if resp.status_code != 200:
        try:
            detail = resp.json().get("error", resp.text)
        except ValueError:
            detail = resp.text
        return render_template("index.html", error=detail)

    result = resp.json()
    return render_template(
        "result.html",
        result=result,
        global_name=global_doc.filename,
        sop_name=sop_doc.filename,
        regional_name=regional_doc.filename if (regional_doc and regional_doc.filename) else None,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=UI_PORT)
