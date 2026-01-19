from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from typing import Dict, Any
import json

app = FastAPI(title="Akson Metadata API")
BASE_DIR = Path(__file__).resolve().parent.parent
METADATA_FILE = BASE_DIR / "metadata" / "library-metadata.json"
FRONTEND_DIR = BASE_DIR / "frontend"
PDFJS_DIR = BASE_DIR / "pdfjs"


def _load_metadata() -> Dict[str, Any]:
    if not METADATA_FILE.exists():
        raise HTTPException(status_code=404, detail="Metadata file not found")
    with open(METADATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


@app.get("/library")
def list_library():
    data = _load_metadata()
    return {"items": data.get("files", []), "summary": data.get("summary", {})}


@app.get("/library/{note_id}")
def get_note(note_id: str):
    data = _load_metadata()
    for note in data.get("files", []):
        if note.get("noteId") == note_id:
            return note
    raise HTTPException(status_code=404, detail="Note not found")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def root():
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="Frontend not built")


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

# Expose bundled PDF.js viewer so the web UI can load PDFs directly in-browser.
if PDFJS_DIR.exists():
    app.mount("/pdfjs", StaticFiles(directory=PDFJS_DIR), name="pdfjs")
