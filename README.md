# Akson (Web migration ready)
AI-powered study companion for PDF processing, flashcard generation, and document analysis. The desktop app uses PyQt6 and OpenAI today, but this repository now includes documentation and scripts aimed at a web/AI migration with Railway deployment.

## Migration notes
1. **Metadata-first library**: PDFs stay local, but summaries/flashcards are stored in JSON (see `docs/library-metadata.md`). Any web frontend can hit `/api/library` (or similar) to read these records and link them to cloud-hosted PDFs.
2. **Environments**: Copy `.env.example` to `.env` and supply keys to run locally (`OPENAI_API_KEY`, `DATABASE_URL`, `STRIPE_*`, etc.).
3. **Railway readiness**: Add the services you plan to host on Railway (Postgres for metadata, a worker for AI summaries, and Stripe/Subscription services). The repo now includes `railway.json`, env guidance, and helper scripts for dev runs.

## Development setup
1. Copy `.env.example` to `.env` and fill in your API keys/URLs.
2. Install dependencies: `pip install -r requirements.txt`.
3. Run `python slides_working.py` to launch the desktop app.
4. Use `akson_cards/store.py` to inspect/save summaries/flashcards for the web backend.

## Railway deployment preparation
- **Postgres metadata service**: store summaries/flashcards with `fileId` references so the website can fetch them.
- **AI worker**: run summary/flashcard generation as a Railway background job; call this worker using `AI_WORKER_URL`.
- **Subscriptions service**: host Stripe/entitlement APIs on Railway; configure `STRIPE_SECRET_KEY` and `AKSON_PAYMENTS_URL` via env vars.
- **Railway config**: `railway.json` declares the required env vars so the platform can provision them automatically.
- **Local helper**: `scripts/run-local.sh` loads `.env` and runs `slides_working.py` so Railway can mimic production without bundling.

## Exporting metadata for the website
Run `scripts/export-library-metadata.py` to read the JSON store under `~/.cache/pdfjs_viewer/akson_cards`, marshal each note/card into a tidy record, and write `metadata/library-metadata.json`. That file is ready for a REST backend (e.g., `/api/library`, `/api/flashcards`) and keeps the summaries/flashcards linked to their original lecture IDs without needing the PDF binaries.

## Next steps for AI coders
- Use the API contract in `docs/library-metadata.md` to build web endpoints for `/library`, `/notes`, `/flashcards`, etc.
- Link summaries/flashcards to `fileId` values (PDF names) instead of depending on local filesystem paths.
- Keep using the `akson_cards` schemas so you can import/export decks/cards directly from the desktop format.

## API server for web front-end
- `api/main.py` now exposes `/library`, `/library/{note_id}`, and `/health` endpoints backed by `metadata/library-metadata.json`.
- Railway’s start command (`bash scripts/run-local.sh`) launches Uvicorn so the API is available as an HTTP service.
- The front-end (or a future static site) can call `/library` to list lectures and `/library/{note_id}` to retrieve summaries/flashcards linked to that lecture ID.

## Previewing the frontend
Now that the API serves `/library`, you can visit the Railway domain (or `http://localhost:8000` via `bash scripts/run-local.sh`) to see the simple static site in `frontend/index.html`. It fetches `/library` and lists all summaries/flashcards tied to their lecture IDs, so you get a quick in-browser UI without building a full React app.
