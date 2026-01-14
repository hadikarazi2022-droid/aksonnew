// Dashboard JavaScript functionality
document.addEventListener('DOMContentLoaded', function() {
    // Load decks and update stats
    loadDecks();
    
    // Modal elements
    const createDeckModal = document.getElementById('create-deck-modal');
    const createDeckBtn = document.getElementById('create-deck-btn');
    const closeModalSpans = document.querySelectorAll('.close');
    const createDeckForm = document.getElementById('create-deck-form');
    
    // Open modal
    createDeckBtn.addEventListener('click', function() {
        createDeckModal.style.display = 'block';
    });
    
    // Close modal
    closeModalSpans.forEach(span => {
        span.addEventListener('click', function() {
            createDeckModal.style.display = 'none';
        });
    });
    
    // Close modal when clicking outside
    window.addEventListener('click', function(event) {
        if (event.target === createDeckModal) {
            createDeckModal.style.display = 'none';
        }
    });
    
    // Form submission
    createDeckForm.addEventListener('submit', function(e) {
        e.preventDefault();
        
        const deckName = document.getElementById('deck-name').value;
        const deckDescription = document.getElementById('deck-description').value;
        
        // Create deck via API
        fetch('/api/decks', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                name: deckName,
                description: deckDescription
            })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                // Close modal and refresh decks
                createDeckModal.style.display = 'none';
                createDeckForm.reset();
                loadDecks();
            } else {
                alert('Error creating deck: ' + (data.error || 'Unknown error'));
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('Error creating deck: ' + error.message);
        });
    });
    
    // Study now button
    document.getElementById('study-now-btn').addEventListener('click', function() {
        // Redirect to decks page to select a deck to study
        window.location.href = '/decks';
    });
});

function loadDecks() {
    // Show loading
    document.getElementById('decks-list').innerHTML = '<div class="loading">Loading decks...</div>';
    
    // Fetch decks from API
    fetch('/api/decks')
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                displayDecks(data.decks);
                updateStats(data.decks);
            } else {
                document.getElementById('decks-list').innerHTML = 
                    '<div class="loading">Error loading decks: ' + (data.error || 'Unknown error') + '</div>';
            }
        })
        .catch(error => {
            console.error('Error:', error);
            document.getElementById('decks-list').innerHTML = 
                '<div class="loading">Error loading decks: ' + error.message + '</div>';
        });
}

function displayDecks(decks) {
    const decksList = document.getElementById('decks-list');
    
    if (decks.length === 0) {
        decksList.innerHTML = `
            <div class="loading">
                <p>No decks yet. Create your first deck to get started!</p>
            </div>
        `;
        return;
    }
    
    let html = '';
    decks.forEach(deck => {
        html += `
            <div class="deck-card">
                <h3>${deck.name}</h3>
                <p>${deck.description || 'No description'}</p>
                <div class="deck-stats">
                    <span>${deck.total_cards} cards</span>
                    <span>${deck.due_cards} due</span>
                </div>
                <div class="deck-actions">
                    <button onclick="studyDeck('${deck.id}')" class="btn-secondary">Study</button>
                    <button onclick="viewDeck('${deck.id}')" class="btn-secondary">View</button>
                </div>
            </div>
        `;
    });
    
    decksList.innerHTML = html;
}

function updateStats(decks) {
    const totalDecks = decks.length;
    const totalCards = decks.reduce((sum, deck) => sum + deck.total_cards, 0);
    const dueToday = decks.reduce((sum, deck) => sum + deck.due_cards, 0);
    
    document.getElementById('total-decks').textContent = totalDecks;
    document.getElementById('total-cards').textContent = totalCards;
    document.getElementById('due-today').textContent = dueToday;
}

function studyDeck(deckId) {
    window.location.href = `/study/${deckId}`;
}

function viewDeck(deckId) {
    // For now, redirect to the decks page filtered by this deck
    window.location.href = `/decks`;
}