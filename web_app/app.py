"""
Web application for Akson - AI-Powered Study Companion
Flask-based web interface for the desktop application
"""
import os
import sys
import json
from datetime import datetime

# Add parent directory to Python path to import akson_cards
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS

# Import Akson Cards modules
from akson_cards.store import AksonCardsStore
from akson_cards.models import Deck, Note, Card, Review, NoteModel
from akson_cards.study import StudySession
from akson_cards.fsrs import FSRS, FSRSConfig

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
CORS(app)  # Enable CORS for API endpoints

# Initialize store
DATA_DIR = os.path.join(os.getcwd(), 'web_data')
store = AksonCardsStore(DATA_DIR)

@app.route('/')
def index():
    """Main page of the application"""
    return render_template('index.html')

@app.route('/decks')
def decks():
    """Page to manage decks"""
    return render_template('decks.html')

@app.route('/study/<deck_id>')
def study(deck_id):
    """Page to study a specific deck"""
    return render_template('study.html', deck_id=deck_id)

@app.route('/api/decks', methods=['GET'])
def get_decks():
    """Get all decks"""
    try:
        decks = store.get_decks()
        result = []
        for deck_id, deck in decks.items():
            cards = store.get_cards(deck_id=deck_id)
            due_cards = store.get_due_cards(deck_id=deck_id)
            result.append({
                'id': deck.id,
                'name': deck.name,
                'description': deck.description,
                'total_cards': len(cards),
                'due_cards': len(due_cards),
                'created_at': deck.created_at.isoformat(),
                'updated_at': deck.updated_at.isoformat()
            })
        
        return jsonify({'success': True, 'decks': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/decks', methods=['POST'])
def create_deck():
    """Create a new deck"""
    try:
        data = request.json
        deck_name = data.get('name', '').strip()
        description = data.get('description', '').strip()
        
        if not deck_name:
            return jsonify({'success': False, 'error': 'Deck name is required'}), 400
        
        deck_id = str(len(store.get_decks()) + 1)  # Simple ID generation
        deck = Deck(
            id=deck_id,
            name=deck_name,
            description=description
        )
        store.save_deck(deck)
        
        return jsonify({'success': True, 'deck': {
            'id': deck.id,
            'name': deck.name,
            'description': deck.description
        }})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/decks/<deck_id>', methods=['DELETE'])
def delete_deck(deck_id):
    """Delete a deck"""
    try:
        store.delete_deck(deck_id)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/decks/<deck_id>/cards', methods=['GET'])
def get_deck_cards(deck_id):
    """Get all cards in a deck"""
    try:
        notes = store.get_notes(deck_id=deck_id)
        cards = store.get_cards(deck_id=deck_id)
        
        result = []
        for note in notes.values():
            note_cards = [c for c in cards.values() if c.note_id == note.id]
            for card in note_cards:
                result.append({
                    'id': card.id,
                    'note_id': card.note_id,
                    'front': note.fields.get('Front', ''),
                    'back': note.fields.get('Back', ''),
                    'state': card.state,
                    'due': card.due.isoformat() if card.due else None,
                    'reps': card.reps,
                    'lapses': card.lapses
                })
        
        return jsonify({'success': True, 'cards': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/decks/<deck_id>/study/start', methods=['POST'])
def start_study_session(deck_id):
    """Start a study session for a deck"""
    try:
        # Check if deck exists
        deck = store.get_deck(deck_id)
        if not deck:
            return jsonify({'success': False, 'error': 'Deck not found'}), 404
        
        # Create a new study session
        session_id = f"{deck_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        study_session = StudySession(store, deck_id=deck_id)
        
        # Start the session (limit to 50 cards max, 20 new cards max)
        started = study_session.start(limit=50, new_limit=20)
        
        if not started:
            return jsonify({'success': False, 'error': 'No cards to study'}), 400
        
        # Store session in memory (in a real app, you'd use Redis or DB)
        if 'study_sessions' not in session:
            session['study_sessions'] = {}
        session['study_sessions'][session_id] = study_session
        
        # Get first card
        current = study_session.get_current_card()
        if not current:
            return jsonify({'success': False, 'error': 'No cards available'}), 400
        
        card, note = current
        progress = study_session.get_progress()
        
        return jsonify({
            'success': True,
            'session_id': session_id,
            'card': {
                'id': card.id,
                'front': note.fields.get('Front', ''),
                'back': note.fields.get('Back', ''),
                'state': card.state
            },
            'progress': {
                'current': progress[0],
                'total': progress[1]
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/study/<session_id>/answer', methods=['POST'])
def answer_card(session_id):
    """Answer a card in study session"""
    try:
        data = request.json
        rating = data.get('rating')  # 1=Again, 2=Hard, 3=Good, 4=Easy
        
        if rating not in [1, 2, 3, 4]:
            return jsonify({'success': False, 'error': 'Rating must be 1, 2, 3, or 4'}), 400
        
        # Get the study session
        if 'study_sessions' not in session or session_id not in session['study_sessions']:
            return jsonify({'success': False, 'error': 'Session not found'}), 404
        
        study_session = session['study_sessions'][session_id]
        
        # Answer the current card
        result = study_session.answer_card(rating)
        
        if not result:
            # Session complete
            if session_id in session['study_sessions']:
                del session['study_sessions'][session_id]
            
            stats = study_session.get_stats()
            return jsonify({
                'success': True,
                'complete': True,
                'stats': stats
            })
        
        # Get next card
        card, note = result
        progress = study_session.get_progress()
        
        return jsonify({
            'success': True,
            'complete': False,
            'card': {
                'id': card.id,
                'front': note.fields.get('Front', ''),
                'back': note.fields.get('Back', ''),
                'state': card.state
            },
            'progress': {
                'current': progress[0],
                'total': progress[1]
            },
            'stats': study_session.get_stats()
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/cards', methods=['POST'])
def add_card():
    """Add a new card to a deck"""
    try:
        data = request.json
        deck_id = data.get('deck_id')
        front = data.get('front', '').strip()
        back = data.get('back', '').strip()
        
        if not deck_id or not front or not back:
            return jsonify({'success': False, 'error': 'Deck ID, front, and back are required'}), 400
        
        # Check if deck exists
        deck = store.get_deck(deck_id)
        if not deck:
            return jsonify({'success': False, 'error': 'Deck not found'}), 404
        
        # Get default model
        models = store.get_models()
        model = models.get("basic")
        if not model:
            model = list(models.values())[0] if models else None
        
        if not model:
            return jsonify({'success': False, 'error': 'No note model available'}), 500
        
        # Create note
        note_id = f"note_{len(store.get_notes()) + 1}"
        note = Note(
            id=note_id,
            deck_id=deck_id,
            model_id=model.id,
            fields={
                "Front": front,
                "Back": back
            }
        )
        store.save_note(note)
        
        # Create card
        card_id = f"card_{len(store.get_cards()) + 1}"
        template_id = model.templates[0]["id"] if model.templates else "basic-1"
        card = Card(
            id=card_id,
            note_id=note_id,
            template_id=template_id,
            state="new"
        )
        store.save_card(card)
        
        return jsonify({
            'success': True,
            'card': {
                'id': card.id,
                'front': front,
                'back': back
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    # Create data directory if it doesn't exist
    os.makedirs(DATA_DIR, exist_ok=True)
    
    # Run the application
    app.run(debug=True, host='0.0.0.0', port=5000)