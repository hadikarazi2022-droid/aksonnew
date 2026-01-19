#!/usr/bin/env python3
"""Dump Akson Cards metadata for web migration."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from akson_cards.store import AksonCardsStore

CACHE_ROOT = Path.home() / ".cache" / "pdfjs_viewer"
AKSON_DATA_DIR = CACHE_ROOT / "akson_cards"
OUTPUT_DIR = Path("metadata")
OUTPUT_FILE = OUTPUT_DIR / "library-metadata.json"


def collect_metadata() -> Dict[str, dict]:
    store = AksonCardsStore(AKSON_DATA_DIR)
    notes = store.get_notes()
    cards = store.get_cards()
    decks = store.get_decks()

    metadata = []
    for note in notes.values():
        related_cards = [card for card in cards.values() if card.note_id == note.id]
        deck = decks.get(note.deck_id)
        metadata.append({
            "noteId": note.id,
            "deckId": note.deck_id,
            "deckName": deck.name if deck else None,
            "fields": note.fields,
            "tags": note.tags,
            "cardCount": len(related_cards),
            "cards": [card.to_dict() for card in related_cards],
            "createdAt": note.created_at.isoformat(),
            "updatedAt": note.updated_at.isoformat()
        })

    return {
        "files": metadata,
        "summary": {
            "noteCount": len(notes),
            "cardCount": len(cards)
        }
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    data = collect_metadata()
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Library metadata exported to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
