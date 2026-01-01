"""
Storage layer for Akson Cards
Persists decks, notes, cards, and reviews to JSON files
"""

import json
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime
import uuid

from .models import Deck, Note, Card, Review, NoteModel


class AksonCardsStore:
    """JSON-based storage for Akson Cards"""
    
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # File paths
        self.decks_file = self.data_dir / "decks.json"
        self.notes_file = self.data_dir / "notes.json"
        self.cards_file = self.data_dir / "cards.json"
        self.reviews_file = self.data_dir / "reviews.json"
        self.models_file = self.data_dir / "models.json"
    
    def _load_json(self, filepath: Path, default: dict = None) -> dict:
        """Load JSON file or return default"""
        if not filepath.exists():
            return default or {}
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return default or {}
    
    def _save_json(self, filepath: Path, data: dict) -> None:
        """Save JSON file"""
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    
    # Decks
    def get_decks(self) -> Dict[str, Deck]:
        """Get all decks"""
        data = self._load_json(self.decks_file, {})
        return {
            deck_id: Deck.from_dict(deck_data)
            for deck_id, deck_data in data.items()
        }
    
    def get_deck(self, deck_id: str) -> Optional[Deck]:
        """Get a single deck"""
        decks = self.get_decks()
        return decks.get(deck_id)
    
    def save_deck(self, deck: Deck) -> None:
        """Save or update a deck"""
        decks = self.get_decks()
        decks[deck.id] = deck
        self._save_json(self.decks_file, {
            deck_id: deck.to_dict()
            for deck_id, deck in decks.items()
        })
    
    def delete_deck(self, deck_id: str) -> None:
        """Delete a deck and all its notes/cards"""
        decks = self.get_decks()
        if deck_id in decks:
            del decks[deck_id]
            self._save_json(self.decks_file, {
                deck_id: deck.to_dict()
                for deck_id, deck in decks.items()
            })
            
            # Also delete associated notes and cards
            notes = self.get_notes()
            cards = self.get_cards()
            
            note_ids_to_delete = [
                note_id for note_id, note in notes.items()
                if note.deck_id == deck_id
            ]
            
            for note_id in note_ids_to_delete:
                del notes[note_id]
                # Delete cards for this note
                card_ids_to_delete = [
                    card_id for card_id, card in cards.items()
                    if card.note_id == note_id
                ]
                for card_id in card_ids_to_delete:
                    del cards[card_id]
            
            self._save_json(self.notes_file, {
                note_id: note.to_dict()
                for note_id, note in notes.items()
            })
            self._save_json(self.cards_file, {
                card_id: card.to_dict()
                for card_id, card in cards.items()
            })
    
    # Notes
    def get_notes(self, deck_id: Optional[str] = None) -> Dict[str, Note]:
        """Get all notes, optionally filtered by deck"""
        data = self._load_json(self.notes_file, {})
        notes = {
            note_id: Note.from_dict(note_data)
            for note_id, note_data in data.items()
        }
        
        if deck_id:
            notes = {
                note_id: note
                for note_id, note in notes.items()
                if note.deck_id == deck_id
            }
        
        return notes
    
    def get_note(self, note_id: str) -> Optional[Note]:
        """Get a single note"""
        notes = self.get_notes()
        return notes.get(note_id)
    
    def save_note(self, note: Note) -> None:
        """Save or update a note"""
        notes = self.get_notes()
        notes[note.id] = note
        self._save_json(self.notes_file, {
            note_id: note.to_dict()
            for note_id, note in notes.items()
        })
    
    # Cards
    def get_cards(self, note_id: Optional[str] = None, deck_id: Optional[str] = None) -> Dict[str, Card]:
        """Get all cards, optionally filtered by note or deck"""
        data = self._load_json(self.cards_file, {})
        cards = {
            card_id: Card.from_dict(card_data)
            for card_id, card_data in data.items()
        }
        
        if note_id:
            cards = {
                card_id: card
                for card_id, card in cards.items()
                if card.note_id == note_id
            }
        elif deck_id:
            # Get notes in deck first
            notes = self.get_notes(deck_id)
            note_ids = set(notes.keys())
            cards = {
                card_id: card
                for card_id, card in cards.items()
                if card.note_id in note_ids
            }
        
        return cards
    
    def get_card(self, card_id: str) -> Optional[Card]:
        """Get a single card"""
        cards = self.get_cards()
        return cards.get(card_id)
    
    def save_card(self, card: Card) -> None:
        """Save or update a card"""
        cards = self.get_cards()
        cards[card.id] = card
        self._save_json(self.cards_file, {
            card_id: card.to_dict()
            for card_id, card in cards.items()
        })
    
    def get_due_cards(self, deck_id: Optional[str] = None, limit: Optional[int] = None) -> List[Card]:
        """Get cards due for review"""
        cards = self.get_cards(deck_id=deck_id)
        now = datetime.now()
        
        due_cards = [
            card for card in cards.values()
            if card.due and card.due <= now
        ]
        
        # Sort by due date (earliest first)
        due_cards.sort(key=lambda c: c.due or datetime.max)
        
        if limit:
            due_cards = due_cards[:limit]
        
        return due_cards
    
    # Reviews
    def get_reviews(self, card_id: Optional[str] = None) -> List[Review]:
        """Get all reviews, optionally filtered for a card"""
        data = self._load_json(self.reviews_file, {})
        reviews = [
            Review.from_dict(review_data)
            for review_data in data.values()
        ]
        
        if card_id:
            reviews = [r for r in reviews if r.card_id == card_id]
        
        # Sort by timestamp (newest first)
        reviews.sort(key=lambda r: r.timestamp, reverse=True)
        return reviews
    
    def save_review(self, review: Review) -> None:
        """Save a review"""
        reviews = self.get_reviews()
        reviews.append(review)
        
        # Convert to dict with IDs as keys
        reviews_dict = {r.id: r.to_dict() for r in reviews}
        self._save_json(self.reviews_file, reviews_dict)
    
    # Note Models
    def get_models(self) -> Dict[str, NoteModel]:
        """Get all note models"""
        data = self._load_json(self.models_file, {})
        
        # Initialize default models if empty
        if not data:
            default_models = self._get_default_models()
            for model in default_models:
                data[model.id] = model.to_dict()
            self._save_json(self.models_file, data)
        
        return {
            model_id: NoteModel.from_dict(model_data)
            for model_id, model_data in data.items()
        }
    
    def _get_default_models(self) -> List[NoteModel]:
        """Create default note models"""
        basic_model = NoteModel(
            id="basic",
            name="Basic",
            fields=["Front", "Back"],
            templates=[{
                "id": "basic-1",
                "name": "Card 1",
                "front": "{{Front}}",
                "back": "{{FrontSide}}\n\n<hr id=answer>\n\n{{Back}}"
            }],
            css="""card {
    font-family: arial;
    font-size: 20px;
    text-align: center;
    color: black;
    background-color: white;
}"""
        )
        return [basic_model]

