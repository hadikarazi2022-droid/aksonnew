# Akson Web Viewer

AI-powered study companion for PDF processing, flashcard generation, and document analysis.

## Overview
This is a web-based PDF viewer with a library sidebar for managing notes and flashcards. The application uses FastAPI to serve both a REST API and the frontend.

## Architecture
- **Backend**: FastAPI (Python) - serves API endpoints and static files
- **Frontend**: Static HTML/CSS/JS in `frontend/`
- **PDF Viewer**: PDF.js library bundled in `pdfjs/`
- **Data**: JSON-based metadata stored in `metadata/library-metadata.json`

## Project Structure
```
api/main.py          - FastAPI application
frontend/index.html  - Web UI
pdfjs/               - PDF.js viewer library
metadata/            - Library metadata JSON
scripts/             - Helper scripts
```

## Running the Application
The application runs on port 5000 via uvicorn:
```bash
bash scripts/run-local.sh
```

## API Endpoints
- `GET /` - Serves the frontend
- `GET /library` - Lists all notes/flashcards
- `GET /library/{note_id}` - Gets a specific note
- `GET /health` - Health check
- `GET /pdfjs/*` - PDF.js viewer static files

## Environment Variables (Optional)
- `OPENAI_API_KEY` - For AI features
- `DATABASE_URL` - Database connection
- `STRIPE_SECRET_KEY` - Payment processing
