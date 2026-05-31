# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""PolicyDriftCheck megaservice.

Follows the OPEA GenAIExamples pattern: a thin gateway that orchestrates the
document loader and the OpenAI-compatible LLM microservice (served by
GenAIComps, e.g. TGI or vLLM). Exposes a single REST endpoint.
"""

import os
import tempfile

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from drift.engine import check_drift
from drift.loader import extract_text

MEGA_SERVICE_PORT = int(os.getenv("MEGA_SERVICE_PORT", "8888"))

app = FastAPI(title="PolicyDriftCheck", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _save_upload(upload: UploadFile) -> str:
    suffix = os.path.splitext(upload.filename or "")[1]
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as fh:
        fh.write(upload.file.read())
    return path


@app.get("/v1/health")
def health():
    return {"status": "ok"}


@app.post("/v1/drift_check")
async def drift_check(
    global_doc: UploadFile = File(...),
    sop_doc: UploadFile = File(...),
    regional_doc: UploadFile = File(None),
):
    paths = []
    try:
        gpath = _save_upload(global_doc)
        spath = _save_upload(sop_doc)
        paths += [gpath, spath]
        global_text = extract_text(gpath)
        sop_text = extract_text(spath)

        regional_text = None
        if regional_doc is not None and regional_doc.filename:
            rpath = _save_upload(regional_doc)
            paths.append(rpath)
            regional_text = extract_text(rpath)

        result = check_drift(global_text, sop_text, regional_text)
        return JSONResponse(result)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"Drift check failed: {exc}"}, status_code=500)
    finally:
        for p in paths:
            try:
                os.remove(p)
            except OSError:
                pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=MEGA_SERVICE_PORT)
