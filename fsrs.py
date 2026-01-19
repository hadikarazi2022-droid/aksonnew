"""
FSRS (Free Spaced Repetition Scheduler) Implementation
Based on: https://github.com/open-spaced-repetition/fsrs

Core FSRS algorithm for computing next intervals and updating card parameters.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
import math


@dataclass
class CardParams:
    """FSRS card state parameters"""
    stability: float = 0.0  # Memory stability (days)
    difficulty: float = 8.0  # Memory difficulty (0-10 scale)
    reps: int = 0  # Number of successful reviews
    lapses: int = 0  # Number of failures
    elapsed_days: int = 0  # Days since last review
    last_review: Optional[datetime] = None
    due: Optional[datetime] = None
    state: str = "new"  # new, learning, review, relearning


@dataclass
class FSRSConfig:
    """FSRS algorithm parameters (default optimized values)"""
    # Request retention (target retention rate)
    request_retention: float = 0.9
    
    # Weights (from FSRS-4.5 optimized parameters)
    w: list[float] = None
    
    # Interval modifiers
    maximum_interval: int = 36500  # 100 years
    
    # Learning steps (minutes)
    learning_steps: list[int] = None
    
    # Relearning steps (minutes)  
    relearning_steps: list[int] = None
    
    # Graduating interval (days)
    graduating_interval: int = 1
    
    # Easy interval (days)
    easy_interval: int = 4
    
    def __post_init__(self):
        if self.w is None:
            # Default FSRS-4.5 weights (simplified set)
            self.w = [
                0.4, 1.6, 5.0, -0.5, -0.5, 0.2, 1.4, -0.12, 0.8, 2.0,
                -0.2, 0.2, 1.0, -0.2, -0.2, -0.12, 0.0, 1.1, 1.0, -0.2,
                0.0, 0.5, -0.25, 0.0, 0.0, 0.1, -0.25, -0.5, 0.0, 0.0,
                0.0, 0.0, 0.0, 0.25
            ]
        if self.learning_steps is None:
            self.learning_steps = [1, 10]  # 1 min, 10 min
        if self.relearning_steps is None:
            self.relearning_steps = [10]  # 10 min


class FSRS:
    """
    Free Spaced Repetition Scheduler
    
    Implements the FSRS algorithm for calculating next review intervals
    based on card difficulty, stability, and review outcomes.
    """
    
    def __init__(self, config: Optional[FSRSConfig] = None):
        self.config = config or FSRSConfig()
        self.w = self.config.w
    
    def _init_difficulty(self, rating: int) -> float:
        """Initialize difficulty based on first rating"""
        return self.w[2] + (3 - rating) * self.w[3]
    
    def _init_stability(self, rating: int) -> float:
        """Initialize stability based on first rating"""
        if rating == 4:  # Easy
            return self.w[4] + self.w[5] * 4
        return self.w[4] + self.w[5] * (rating - 1)
    
    def _next_difficulty(self, d: float, rating: int) -> float:
        """Calculate next difficulty after a review"""
        if rating == 1:  # Again
            d = d - self.w[6]
        elif rating == 2:  # Hard
            d = d - self.w[7]
        elif rating == 3:  # Good
            d = d  # No change
        elif rating == 4:  # Easy
            d = d + self.w[8]
        
        # Clamp difficulty
        d = max(1.0, min(10.0, d))
        return d
    
    def _next_recall_stability(self, s: float, d: float, elapsed_days: int, rating: int) -> float:
        """Calculate stability after a successful recall"""
        if rating == 4:  # Easy
            hard_penalty = self.w[9]
        else:
            hard_penalty = self.w[10]
        
        if rating == 2:  # Hard
            easy_bonus = self.w[11]
        else:
            easy_bonus = self.w[12]
        
        # New stability formula
        new_s = (
            s * (1 + math.exp(self.w[13]) * 
                 (11 - d) * math.exp(-self.w[14] * elapsed_days) - 
                 hard_penalty * (rating == 2) - 
                 easy_bonus * (rating == 4))
        )
        
        return max(0.1, new_s)
    
    def _next_forget_stability(self, s: float, d: float, elapsed_days: int) -> float:
        """Calculate stability after a failure"""
        return (
            self.w[15] * 
            (s ** self.w[16]) * 
            ((d + 1) ** self.w[17]) * 
            math.exp(-self.w[18] * elapsed_days) *
            (self.w[19] if elapsed_days > s else 1)
        )
    
    def _review_stability(self, s: float, d: float, elapsed_days: int, rating: int) -> float:
        """Calculate new stability based on review outcome"""
        if rating == 1:  # Again - forgot
            return self._next_forget_stability(s, d, elapsed_days)
        else:  # Hard, Good, Easy - recalled
            return self._next_recall_stability(s, d, elapsed_days, rating)
    
    def _next_interval(self, s: float, request_retention: float) -> int:
        """Calculate next interval in days"""
        ivl = s * 9 * (1 / request_retention - 1)
        ivl = max(1, min(self.config.maximum_interval, int(ivl + 0.5)))
        return ivl
    
    def next_review(
        self, 
        card: CardParams, 
        rating: int, 
        now: Optional[datetime] = None
    ) -> tuple[CardParams, datetime]:
        """
        Process a review and return updated card params and next due date.
        
        Args:
            card: Current card parameters
            rating: Review rating (1=Again, 2=Hard, 3=Good, 4=Easy)
            now: Current timestamp (defaults to now)
        
        Returns:
            Tuple of (updated_card_params, next_due_datetime)
        """
        if now is None:
            now = datetime.now()
        
        # Calculate elapsed days
        if card.last_review:
            elapsed_days = max(0, (now - card.last_review).days)
        else:
            elapsed_days = 0
        
        # Update based on state
        updated = CardParams(
            stability=card.stability,
            difficulty=card.difficulty,
            reps=card.reps,
            lapses=card.lapses,
            elapsed_days=elapsed_days,
            last_review=now
        )
        
        if card.state == "new" or (card.state == "learning" and rating == 1):
            # First review or failed while learning
            if rating == 1:  # Again
                updated.difficulty = self._init_difficulty(1)
                updated.stability = self._init_stability(1)
                updated.lapses += 1
                updated.state = "relearning"
                # Next due in relearning step
                next_due = now + timedelta(minutes=self.config.relearning_steps[0])
            else:
                updated.difficulty = self._init_difficulty(rating)
                updated.stability = self._init_stability(rating)
                updated.reps += 1
                if rating == 4:  # Easy
                    updated.state = "review"
                    updated.due = now + timedelta(days=self.config.easy_interval)
                    next_due = updated.due
                else:
                    # Continue in learning steps
                    updated.state = "learning"
                    step_idx = min(updated.reps - 1, len(self.config.learning_steps) - 1)
                    next_due = now + timedelta(minutes=self.config.learning_steps[step_idx])
                    updated.due = next_due
            
        elif card.state == "learning":
            # In learning steps
            if rating == 1:  # Again
                updated.stability = self._init_stability(1)
                updated.difficulty = self._init_difficulty(1)
                updated.lapses += 1
                updated.reps = 0
                updated.state = "relearning"
                next_due = now + timedelta(minutes=self.config.relearning_steps[0])
            else:
                updated.reps += 1
                if updated.reps > len(self.config.learning_steps):
                    # Graduate to review
                    updated.state = "review"
                    if rating == 4:
                        interval = self.config.easy_interval
                    else:
                        interval = self.config.graduating_interval
                    next_due = now + timedelta(days=interval)
                    updated.due = next_due
                else:
                    # Continue learning steps
                    step_idx = min(updated.reps - 1, len(self.config.learning_steps) - 1)
                    next_due = now + timedelta(minutes=self.config.learning_steps[step_idx])
                    updated.due = next_due
        
        elif card.state == "relearning":
            if rating == 1:  # Again
                updated.stability = self._next_forget_stability(
                    card.stability, card.difficulty, elapsed_days
                )
                updated.lapses += 1
                next_due = now + timedelta(minutes=self.config.relearning_steps[0])
            else:
                updated.stability = self._next_recall_stability(
                    card.stability, card.difficulty, elapsed_days, rating
                )
                updated.difficulty = self._next_difficulty(card.difficulty, rating)
                updated.reps += 1
                updated.state = "review"
                interval = self._next_interval(updated.stability, self.config.request_retention)
                next_due = now + timedelta(days=interval)
                updated.due = next_due
        
        else:  # state == "review"
            if rating == 1:  # Again - failed
                updated.stability = self._next_forget_stability(
                    card.stability, card.difficulty, elapsed_days
                )
                updated.difficulty = self._next_difficulty(card.difficulty, rating)
                updated.lapses += 1
                updated.reps = 0
                updated.state = "relearning"
                next_due = now + timedelta(minutes=self.config.relearning_steps[0])
            else:  # Hard, Good, Easy
                updated.stability = self._review_stability(
                    card.stability, card.difficulty, elapsed_days, rating
                )
                updated.difficulty = self._next_difficulty(card.difficulty, rating)
                updated.reps += 1
                interval = self._next_interval(updated.stability, self.config.request_retention)
                next_due = now + timedelta(days=interval)
                updated.due = next_due
        
        updated.elapsed_days = 0  # Reset after review
        return updated, next_due
    
    def preview_workload(
        self, 
        cards: list[CardParams], 
        horizon_days: int = 30
    ) -> dict[str, int]:
        """
        Estimate workload for next N days.
        
        Returns:
            Dict mapping date strings to estimated card counts
        """
        now = datetime.now()
        workload = {}
        
        for card in cards:
            if card.due and card.due <= now + timedelta(days=horizon_days):
                date_str = card.due.strftime("%Y-%m-%d")
                workload[date_str] = workload.get(date_str, 0) + 1
        
        return workload

