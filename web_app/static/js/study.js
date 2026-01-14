// Study page JavaScript functionality
let currentSession = null;
let isCardFlipped = false;

document.addEventListener('DOMContentLoaded', function() {
    // Start study session
    startStudySession(window.DECK_ID);
    
    // Event listeners for buttons
    document.getElementById('flip-card-btn').addEventListener('click', flipCard);
    document.getElementById('end-session-btn').addEventListener('click', endSession);
    
    // Rating buttons
    document.querySelectorAll('.btn-rating').forEach(button => {
        button.addEventListener('click', function() {
            const rating = parseInt(this.getAttribute('data-rating'));
            answerCard(rating);
        });
    });
});

function startStudySession(deckId) {
    // Show loading
    document.getElementById('card-content').innerHTML = '<div class="loading">Starting study session...</div>';
    
    // Start session via API
    fetch(`/api/decks/${deckId}/study/start`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            currentSession = data;
            displayCard(data.card);
            updateProgress(data.progress);
        } else {
            document.getElementById('card-content').innerHTML = 
                `<div class="loading">Error starting session: ${data.error || 'Unknown error'}</div>`;
        }
    })
    .catch(error => {
        console.error('Error:', error);
        document.getElementById('card-content').innerHTML = 
            `<div class="loading">Error starting session: ${error.message}</div>`;
    });
}

function displayCard(card) {
    const cardContent = document.getElementById('card-content');
    
    // Initially show only the front of the card
    cardContent.innerHTML = `<div class="card-front">${card.front}</div>`;
    
    // Reset card state
    isCardFlipped = false;
    document.getElementById('flip-card-btn').textContent = 'Show Answer';
    document.getElementById('rating-buttons').style.display = 'none';
}

function flipCard() {
    if (!currentSession) return;
    
    const cardContent = document.getElementById('card-content');
    const card = currentSession.card;
    
    if (!isCardFlipped) {
        // Show the back of the card
        cardContent.innerHTML = `
            <div class="card-front">${card.front}</div>
            <div class="card-divider"></div>
            <div class="card-back">${card.back}</div>
        `;
        document.getElementById('flip-card-btn').textContent = 'Hide Answer';
        isCardFlipped = true;
    } else {
        // Hide the back of the card
        cardContent.innerHTML = `<div class="card-front">${card.front}</div>`;
        document.getElementById('flip-card-btn').textContent = 'Show Answer';
        isCardFlipped = false;
    }
}

function answerCard(rating) {
    if (!currentSession) return;
    
    // Disable rating buttons during submission
    document.querySelectorAll('.btn-rating').forEach(btn => {
        btn.disabled = true;
    });
    
    // Submit answer via API
    fetch(`/api/study/${currentSession.session_id}/answer`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            rating: rating
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            if (data.complete) {
                // Session is complete
                showSessionComplete(data.stats);
            } else {
                // Move to next card
                currentSession = data;
                displayCard(data.card);
                updateProgress(data.progress);
                
                // Re-enable rating buttons
                document.querySelectorAll('.btn-rating').forEach(btn => {
                    btn.disabled = false;
                });
            }
        } else {
            alert('Error answering card: ' + (data.error || 'Unknown error'));
            // Re-enable rating buttons
            document.querySelectorAll('.btn-rating').forEach(btn => {
                btn.disabled = false;
            });
        }
    })
    .catch(error => {
        console.error('Error:', error);
        alert('Error answering card: ' + error.message);
        // Re-enable rating buttons
        document.querySelectorAll('.btn-rating').forEach(btn => {
            btn.disabled = false;
        });
    });
}

function updateProgress(progress) {
    const percent = progress.total > 0 ? (progress.current / progress.total) * 100 : 0;
    document.getElementById('progress-fill').style.width = `${percent}%`;
    document.getElementById('progress-text').textContent = `${progress.current}/${progress.total}`;
}

function showSessionComplete(stats) {
    document.getElementById('card-content').innerHTML = `
        <div class="session-complete">
            <h3>Session Complete!</h3>
            <div class="stats">
                <p>Total Cards: ${stats.total}</p>
                <p>Again: ${stats.again}</p>
                <p>Hard: ${stats.hard}</p>
                <p>Good: ${stats.good}</p>
                <p>Easy: ${stats.easy}</p>
            </div>
            <p class="completion-message">Great job! Your progress has been saved.</p>
        </div>
    `;
    
    // Hide rating buttons and flip button
    document.getElementById('rating-buttons').style.display = 'none';
    document.getElementById('flip-card-btn').style.display = 'none';
}

function endSession() {
    if (confirm('Are you sure you want to end this study session?')) {
        // Redirect back to decks page
        window.location.href = '/decks';
    }
}