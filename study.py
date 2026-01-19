"""
Study session management for Akson Cards
"""

from datetime import datetime
from typing import List, Optional, Tuple
import uuid

from .models import Card, Note, Deck, Review
from .fsrs import FSRS, FSRSConfig, CardParams
from .store import AksonCardsStore


class StudySession:
    """Manages an active study session"""
    
    def __init__(self, store: AksonCardsStore, deck_id: Optional[str] = None):
        self.store = store
        self.deck_id = deck_id
        self.fsrs = FSRS()
        self.current_card_index = 0
        self.session_cards: List[Card] = []
        self.reviews_today: List[Review] = []
        
    def start(self, limit: Optional[int] = None, new_limit: Optional[int] = None) -> bool:
        """
        Start a study session
        
        Args:
            limit: Maximum total cards to study
            new_limit: Maximum new cards to introduce
        
        Returns:
            True if session started successfully
        """
        # Get due cards
        due_cards = self.store.get_due_cards(deck_id=self.deck_id, limit=limit)
        
        # Separate new vs review cards
        new_cards = [c for c in due_cards if c.state == "new"]
        review_cards = [c for c in due_cards if c.state != "new"]
        
        # Limit new cards if specified
        if new_limit and len(new_cards) > new_limit:
            new_cards = new_cards[:new_limit]
        
        # Interleave: one new, then reviews
        self.session_cards = []
        new_idx = 0
        review_idx = 0
        
        while len(self.session_cards) < (limit or 999999):
            # Add a new card if available
            if new_idx < len(new_cards):
                self.session_cards.append(new_cards[new_idx])
                new_idx += 1
            
            # Add a review card
            if review_idx < len(review_cards):
                self.session_cards.append(review_cards[review_idx])
                review_idx += 1
            
            # Break if we've exhausted both
            if new_idx >= len(new_cards) and review_idx >= len(review_cards):
                break
        
        self.current_card_index = 0
        return len(self.session_cards) > 0
    
    def get_current_card(self) -> Optional[Tuple[Card, Note]]:
        """Get current card and its note"""
        if self.current_card_index >= len(self.session_cards):
            return None
        
        card = self.session_cards[self.current_card_index]
        note = self.store.get_note(card.note_id)
        
        if not note:
            return None
        
        return (card, note)
    
    def answer_card(self, rating: int, response_time_ms: int = 0) -> Optional[Tuple[Card, Note]]:
        """
        Submit an answer and move to next card
        
        Args:
            rating: 1=Again, 2=Hard, 3=Good, 4=Easy
            response_time_ms: Time taken to answer
        
        Returns:
            Next card and note, or None if session complete
        """
        if self.current_card_index >= len(self.session_cards):
            return None
        
        card = self.session_cards[self.current_card_index]
        note = self.store.get_note(card.note_id)
        
        if not note:
            return None
        
        # Get deck config
        deck = self.store.get_deck(note.deck_id)
        config = FSRSConfig(request_retention=deck.request_retention if deck else 0.9)
        fsrs = FSRS(config)
        
        # Convert to FSRS params
        fsrs_params = card.to_fsrs_params()
        
        # Process review
        updated_params, next_due = fsrs.next_review(fsrs_params, rating, datetime.now())
        
        # Update card
        card.update_from_fsrs(updated_params)
        card.due = next_due
        self.store.save_card(card)
        
        # Save review record
        review = Review(
            id=str(uuid.uuid4()),
            card_id=card.id,
            timestamp=datetime.now(),
            rating=rating,
            response_time_ms=response_time_ms
        )
        self.store.save_review(review)
        self.reviews_today.append(review)
        
        # Move to next card
        self.current_card_index += 1
        
        # Return next card
        return self.get_current_card()
    
    def has_more(self) -> bool:
        """Check if more cards in session"""
        return self.current_card_index < len(self.session_cards)
    
    def get_progress(self) -> Tuple[int, int]:
        """Get progress (current, total)"""
        return (self.current_card_index, len(self.session_cards))
    
    def get_stats(self) -> dict:
        """Get session statistics"""
        if not self.reviews_today:
            return {
                "total": 0,
                "again": 0,
                "hard": 0,
                "good": 0,
                "easy": 0
            }
        
        ratings = [r.rating for r in self.reviews_today]
        return {
            "total": len(self.reviews_today),
            "again": ratings.count(1),
            "hard": ratings.count(2),
            "good": ratings.count(3),
            "easy": ratings.count(4)
        }

