"""
Data models for Akson Cards SRS system
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, Dict, List, Any
import json
from pathlib import Path


@dataclass
class Note:
    """A note contains multiple cards (e.g., basic, cloze, reverse)"""
    id: str
    deck_id: str
    model_id: str
    fields: Dict[str, str]  # Field name -> Field content
    tags: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return {
            "id": self.id,
            "deck_id": self.deck_id,
            "model_id": self.model_id,
            "fields": self.fields,
            "tags": self.tags,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Note":
        """Create Note from dictionary"""
        return cls(
            id=data["id"],
            deck_id=data["deck_id"],
            model_id=data["model_id"],
            fields=data.get("fields", {}),
            tags=data.get("tags", []),
            created_at=datetime.fromisoformat(data.get("created_at", datetime.now().isoformat())),
            updated_at=datetime.fromisoformat(data.get("updated_at", datetime.now().isoformat()))
        )


@dataclass
class Card:
    """A card represents one side of a note (front/back pair)"""
    id: str
    note_id: str
    template_id: str  # Which template to use for rendering
    
    # FSRS parameters
    stability: float = 0.0
    difficulty: float = 8.0
    reps: int = 0
    lapses: int = 0
    elapsed_days: int = 0
    
    # Scheduling
    due: Optional[datetime] = None
    last_review: Optional[datetime] = None
    state: str = "new"  # new, learning, review, relearning
    
    # Metadata
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "note_id": self.note_id,
            "template_id": self.template_id,
            "stability": self.stability,
            "difficulty": self.difficulty,
            "reps": self.reps,
            "lapses": self.lapses,
            "elapsed_days": self.elapsed_days,
            "due": self.due.isoformat() if self.due else None,
            "last_review": self.last_review.isoformat() if self.last_review else None,
            "state": self.state,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Card":
        return cls(
            id=data["id"],
            note_id=data["note_id"],
            template_id=data.get("template_id", "default"),
            stability=data.get("stability", 0.0),
            difficulty=data.get("difficulty", 8.0),
            reps=data.get("reps", 0),
            lapses=data.get("lapses", 0),
            elapsed_days=data.get("elapsed_days", 0),
            due=datetime.fromisoformat(data["due"]) if data.get("due") else None,
            last_review=datetime.fromisoformat(data["last_review"]) if data.get("last_review") else None,
            state=data.get("state", "new"),
            created_at=datetime.fromisoformat(data.get("created_at", datetime.now().isoformat())),
            updated_at=datetime.fromisoformat(data.get("updated_at", datetime.now().isoformat()))
        )
    
    def to_fsrs_params(self):
        """Convert to FSRS CardParams"""
        from .fsrs import CardParams
        return CardParams(
            stability=self.stability,
            difficulty=self.difficulty,
            reps=self.reps,
            lapses=self.lapses,
            elapsed_days=self.elapsed_days,
            last_review=self.last_review,
            due=self.due,
            state=self.state
        )
    
    def update_from_fsrs(self, fsrs_params) -> None:
        """Update from FSRS CardParams"""
        self.stability = fsrs_params.stability
        self.difficulty = fsrs_params.difficulty
        self.reps = fsrs_params.reps
        self.lapses = fsrs_params.lapses
        self.elapsed_days = fsrs_params.elapsed_days
        self.last_review = fsrs_params.last_review
        self.due = fsrs_params.due
        self.state = fsrs_params.state
        self.updated_at = datetime.now()


@dataclass
class Deck:
    """A deck contains notes and cards"""
    id: str
    name: str
    description: str = ""
    parent_deck_id: Optional[str] = None
    sort_order: int = 0
    
    # FSRS configuration
    request_retention: float = 0.9
    daily_new: int = 20
    daily_review_cap: int = 200
    
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "parent_deck_id": self.parent_deck_id,
            "sort_order": self.sort_order,
            "request_retention": self.request_retention,
            "daily_new": self.daily_new,
            "daily_review_cap": self.daily_review_cap,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Deck":
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            parent_deck_id=data.get("parent_deck_id"),
            sort_order=data.get("sort_order", 0),
            request_retention=data.get("request_retention", 0.9),
            daily_new=data.get("daily_new", 20),
            daily_review_cap=data.get("daily_review_cap", 200),
            created_at=datetime.fromisoformat(data.get("created_at", datetime.now().isoformat())),
            updated_at=datetime.fromisoformat(data.get("updated_at", datetime.now().isoformat()))
        )


@dataclass
class Review:
    """A review record (for analytics)"""
    id: str
    card_id: str
    timestamp: datetime
    rating: int  # 1=Again, 2=Hard, 3=Good, 4=Easy
    response_time_ms: int = 0
    scheduler_version: str = "fsrs"
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "card_id": self.card_id,
            "timestamp": self.timestamp.isoformat(),
            "rating": self.rating,
            "response_time_ms": self.response_time_ms,
            "scheduler_version": self.scheduler_version
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Review":
        return cls(
            id=data["id"],
            card_id=data["card_id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            rating=data["rating"],
            response_time_ms=data.get("response_time_ms", 0),
            scheduler_version=data.get("scheduler_version", "fsrs")
        )


@dataclass
class NoteModel:
    """A note model defines fields and templates"""
    id: str
    name: str
    fields: List[str]  # Field names
    templates: List[dict] = field(default_factory=list)  # Template definitions
    css: str = ""
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "fields": self.fields,
            "templates": self.templates,
            "css": self.css
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "NoteModel":
        return cls(
            id=data["id"],
            name=data["name"],
            fields=data.get("fields", []),
            templates=data.get("templates", []),
            css=data.get("css", "")
        )


