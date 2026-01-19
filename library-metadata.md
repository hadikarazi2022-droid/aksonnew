# Library Metadata Contract

This document summarizes how Akson stores summaries, flashcards, and related study data so a future web migration (or backend service) can read/write the same records.

## Core goals for the web migration
1. **Do not require uploading every PDF file.** The desktop app uses local files, but the web version should only store the metadata (summaries/cards) and a reference to the PDF name/path.
2. **Persist summaries/flashcards** with IDs so they can be fetched independently of the actual PDF binary.
3. **Link metadata to the original PDF** by storing the imported file name or a logical slug that matches the display name in the sidebar.

## Data sources inside the desktop app
- `akson_cards/models.py` defines the dataclasses that represent notes, cards, decks, reviews, and models.
- `akson_cards/store.py` serializes these objects to JSON (`akson_cards/data/<user>/*.json`). Those JSON blobs are what the website should read/write.
- `loadLibrary()` and `renderLectureFolders()` in `slides_working.py` consume `AppAPI.list_library_files()`, which returns:
  - `files`: an array of objects `{ name, path, module, summaries, flashcards, last_opened, lastModified }`.
  - `folders`: a mapping of module/folder name → metadata (used to group files).
  - `favorites`: an array of lecture names flagged as favorites.

## Required metadata fields for readiness
| Field | Description | Web notes |
| --- | --- | --- |
| `name` | Display name of the file (usually `stem` of the PDF) | Use to show in the sidebar buttons and link summaries/flashcards. |
| `path` | Path or identifier supplied during import | Web backend can store this as `sourcePath` or equivalent. |
| `summaries` | Number/s count; actual summary text stored separately in `akson_cards` | Store summary text files keyed by `sourcePath` or file ID. |
| `flashcards` | Counts per page | Real flashcards are stored as `Note` + `Card` objects; keep their IDs tied to `sourcePath`. |
| `module` | Folder/module name | Sidebar renders folders first (modules) then loose PDFs; mirror that grouping. |
| `last_opened` | ISO timestamp from desktop | Use for sorting in the UI (latest first). |

## Suggested storage schema for the web API
```json
{
  "fileId": "11030080-Gastrointestinal-Pathology",
  "name": "11030080 - Gastrointestinal Pathology",
  "sourcePath": "docs/11030080.pdf",
  "module": "Gastrointestinal",
  "summaries": [
    {
      "id": "sum-001",
      "text": "...",
      "created_at": "2026-01-05T09:00:00Z"
    }
  ],
  "flashcards": [
    {
      "id": "card-0001",
      "note_id": "note-0001",
      "question": "...",
      "answer": "..."
    }
  ],
  "last_opened": "2026-01-05T09:00:00Z",
  "favorites": true
}
```
The web backend can expose endpoints such as `/api/library` (returns this list) and `/api/notes?fileId=...` to fetch the detailed deck/card data if needed.

## Moving summaries/flashcards to the cloud
- The new site should persist JSON dumps of `Note`/`Card` objects (see `akson_cards/models.py`) in a normalized schema.
- Each object should store the original `fileId` so the UI can show which lecture it belongs to even if the PDF is stored elsewhere.
- On import, generate a stable `fileId` (slugify the file name) and keep it in the metadata stored in the database or object store.

## What the website UI will consume
1. `GET /library`: returns folders and files (mirrors the structure from `renderLectureFolders`).
2. `GET /library/:fileId/summary`: fetches the summary text.
3. `GET /library/:fileId/flashcards`: returns the deck notes/cards with FSRS metadata (see `Note.to_dict()`/`Card.to_dict()`).
4. `POST /library/:fileId/summary` and `POST /library/:fileId/flashcards`: allow updates/new entries.
5. `GET /api/authors` (optional) to expose metadata such as `module`/`last_opened` for sorting.

## Railway-ready hooks
- Provide a script (e.g. `scripts/persist-metadata.py`) that reads `akson_cards` JSON, attaches the `fileId`, and uploads it to a Railway database or object store.
- Expose expected env vars in `env.example`: `DATABASE_URL`, `OPENAI_API_KEY`, `RAILWAY_ENVIRONMENT`, `AI_WORKER_URL`.
- Document how to wire in Railway’s Postgres for metadata and specify which endpoints (e.g., `/api/library`) should connect to it.
