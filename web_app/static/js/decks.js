// Decks JavaScript functionality
document.addEventListener('DOMContentLoaded', function() {
    // Load decks
    loadDecks();
    
    // Modal elements
    const createDeckModal = document.getElementById('create-deck-modal');
    const addCardModal = document.getElementById('add-card-modal');
    const createDeckBtn = document.getElementById('create-deck-btn');
    const closeModalSpans = document.querySelectorAll('.close');
    const createDeckForm = document.getElementById('create-deck-form');
    const addCardForm = document.getElementById('add-card-form');
    
    // Open create deck modal
    createDeckBtn.addEventListener('click', function() {
        createDeckModal.style.display = 'block';
    });
    
    // Close modals
    closeModalSpans.forEach(span => {
        span.addEventListener('click', function() {
            createDeckModal.style.display = 'none';
            addCardModal.style.display = 'none';
        });
    });
    
    // Close modals when clicking outside
    window.addEventListener('click', function(event) {
        if (event.target === createDeckModal) {
            createDeckModal.style.display = 'none';
        }
        if (event.target === addCardModal) {
            addCardModal.style.display = 'none';
        }
    });
    
    // Form submissions
    createDeckForm.addEventListener('submit', handleCreateDeck);
    addCardForm.addEventListener('submit', handleAddCard);
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
                    <button onclick="addCardToDeck('${deck.id}', '${deck.name}')" class="btn-secondary">Add Card</button>
                    <button onclick="viewDeck('${deck.id}')" class="btn-secondary">View Cards</button>
                    <button onclick="deleteDeck('${deck.id}', '${deck.name}')" class="btn-secondary">Delete</button>
                </div>
            </div>
        `;
    });
    
    decksList.innerHTML = html;
}

function handleCreateDeck(e) {
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
            document.getElementById('create-deck-modal').style.display = 'none';
            document.getElementById('create-deck-form').reset();
            loadDecks();
        } else {
            alert('Error creating deck: ' + (data.error || 'Unknown error'));
        }
    })
    .catch(error => {
        console.error('Error:', error);
        alert('Error creating deck: ' + error.message);
    });
}

function handleAddCard(e) {
    e.preventDefault();
    
    const deckId = document.getElementById('card-deck-id').value;
    const front = document.getElementById('card-front').value;
    const back = document.getElementById('card-back').value;
    
    // Add card via API
    fetch('/api/cards', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            deck_id: deckId,
            front: front,
            back: back
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            // Close modal and refresh decks
            document.getElementById('add-card-modal').style.display = 'none';
            document.getElementById('add-card-form').reset();
            loadDecks();
        } else {
            alert('Error adding card: ' + (data.error || 'Unknown error'));
        }
    })
    .catch(error => {
        console.error('Error:', error);
        alert('Error adding card: ' + error.message);
    });
}

function studyDeck(deckId) {
    window.location.href = `/study/${deckId}`;
}

function addCardToDeck(deckId, deckName) {
    // Set the deck ID in the form
    document.getElementById('card-deck-id').value = deckId;
    
    // Update modal title
    const modalTitle = document.querySelector('#add-card-modal h2');
    modalTitle.textContent = `Add Card to "${deckName}"`;
    
    // Show the modal
    document.getElementById('add-card-modal').style.display = 'block';
}

function viewDeck(deckId) {
    // For now, we'll just reload the page with a filter, or we could load the cards in a modal
    alert(`Viewing cards for deck ${deckId} would be implemented here`);
}

function deleteDeck(deckId, deckName) {
    if (confirm(`Are you sure you want to delete the deck "${deckName}"? This action cannot be undone.`)) {
        fetch(`/api/decks/${deckId}`, {
            method: 'DELETE'
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                loadDecks();
            } else {
                alert('Error deleting deck: ' + (data.error || 'Unknown error'));
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('Error deleting deck: ' + error.message);
        });
    }
}