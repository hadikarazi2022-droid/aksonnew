// Akson Web Application - Client-side implementation
class AksonApp {
    constructor() {
        this.currentView = 'dashboard-view';
        this.decks = this.loadDecks();
        this.currentStudySession = null;
        this.currentCardIndex = 0;
        this.currentDeckId = null;
        
        this.initializeEventListeners();
        this.renderDashboard();
    }
    
    initializeEventListeners() {
        // Navigation
        document.getElementById('dashboard-btn').addEventListener('click', () => this.switchView('dashboard-view'));
        document.getElementById('decks-btn').addEventListener('click', () => this.switchView('decks-view'));
        document.getElementById('study-btn').addEventListener('click', () => this.switchView('study-view'));
        
        // Deck management
        document.getElementById('add-deck-btn').addEventListener('click', () => this.showModal('add-deck-modal'));
        document.getElementById('add-deck-form').addEventListener('submit', (e) => this.handleAddDeck(e));
        
        // Card management
        document.getElementById('add-card-form').addEventListener('submit', (e) => this.handleAddCard(e));
        
        // Study session
        document.getElementById('show-answer-btn').addEventListener('click', () => this.showAnswer());
        document.querySelectorAll('.rating-btn').forEach(btn => {
            btn.addEventListener('click', (e) => this.rateCard(e.target.dataset.rating));
        });
        
        // Modal close buttons
        document.querySelectorAll('.close').forEach(closeBtn => {
            closeBtn.addEventListener('click', () => {
                closeBtn.closest('.modal').style.display = 'none';
            });
        });
        
        // Close modals when clicking outside
        window.addEventListener('click', (e) => {
            document.querySelectorAll('.modal').forEach(modal => {
                if (e.target === modal) {
                    modal.style.display = 'none';
                }
            });
        });
    }
    
    switchView(viewName) {
        // Hide current view
        document.getElementById(this.currentView).classList.remove('active');
        // Remove active class from nav buttons
        document.querySelectorAll('.nav-btn').forEach(btn => btn.classList.remove('active'));
        
        // Show new view
        document.getElementById(viewName).classList.add('active');
        // Add active class to corresponding nav button
        document.getElementById(viewName.replace('-view', '-btn')).classList.add('active');
        
        this.currentView = viewName;
        
        // Load content for the view
        if (viewName === 'dashboard-view') {
            this.renderDashboard();
        } else if (viewName === 'decks-view') {
            this.renderDecks();
        } else if (viewName === 'study-view') {
            this.renderStudyView();
        }
    }
    
    showModal(modalId) {
        document.getElementById(modalId).style.display = 'block';
    }
    
    hideModal(modalId) {
        document.getElementById(modalId).style.display = 'none';
    }
    
    loadDecks() {
        const saved = localStorage.getItem('akson_decks');
        return saved ? JSON.parse(saved) : [];
    }
    
    saveDecks() {
        localStorage.setItem('akson_decks', JSON.stringify(this.decks));
    }
    
    handleAddDeck(e) {
        e.preventDefault();
        
        const name = document.getElementById('deck-name').value;
        const description = document.getElementById('deck-description').value;
        
        if (!name.trim()) return;
        
        const newDeck = {
            id: Date.now().toString(),
            name: name.trim(),
            description: description.trim(),
            createdAt: new Date().toISOString(),
            cards: []
        };
        
        this.decks.push(newDeck);
        this.saveDecks();
        
        document.getElementById('add-deck-form').reset();
        this.hideModal('add-deck-modal');
        
        if (this.currentView === 'decks-view') {
            this.renderDecks();
        }
    }
    
    handleAddCard(e) {
        e.preventDefault();
        
        const deckId = document.getElementById('card-deck-id').value;
        const question = document.getElementById('card-question').value;
        const answer = document.getElementById('card-answer').value;
        
        if (!question.trim() || !answer.trim()) return;
        
        const deck = this.decks.find(d => d.id === deckId);
        if (!deck) return;
        
        const newCard = {
            id: Date.now().toString(),
            question: question.trim(),
            answer: answer.trim(),
            createdAt: new Date().toISOString(),
            nextReview: new Date().toISOString(),
            interval: 0,
            easeFactor: 2.5,
            repetitions: 0
        };
        
        deck.cards.push(newCard);
        this.saveDecks();
        
        document.getElementById('add-card-form').reset();
        this.hideModal('add-card-modal');
        
        if (this.currentView === 'decks-view') {
            this.renderDecks();
        }
    }
    
    renderDashboard() {
        // Update stats
        document.getElementById('total-decks').textContent = this.decks.length;
        
        // Calculate cards studied today
        const today = new Date().toDateString();
        let cardsStudiedToday = 0;
        this.decks.forEach(deck => {
            deck.cards.forEach(card => {
                if (new Date(card.lastReviewed).toDateString() === today) {
                    cardsStudiedToday++;
                }
            });
        });
        document.getElementById('cards-studied').textContent = cardsStudiedToday;
        
        // Calculate retention rate (simplified calculation)
        let totalReviews = 0;
        let successfulReviews = 0;
        this.decks.forEach(deck => {
            deck.cards.forEach(card => {
                totalReviews += card.repetitions || 0;
                if (card.easeFactor > 2.0) {
                    successfulReviews++;
                }
            });
        });
        const retentionRate = totalReviews > 0 ? Math.round((successfulReviews / totalReviews) * 100) : 0;
        document.getElementById('retention-rate').textContent = `${retentionRate}%`;
        
        // Show recent decks
        const recentDecksContainer = document.getElementById('recent-decks-list');
        recentDecksContainer.innerHTML = '';
        
        if (this.decks.length === 0) {
            recentDecksContainer.innerHTML = '<p>No decks created yet. <a href="#" onclick="app.switchView(\'decks-view\')">Create your first deck!</a></p>';
            return;
        }
        
        const recentDecks = [...this.decks].sort((a, b) => 
            new Date(b.createdAt) - new Date(a.createdAt)
        ).slice(0, 3);
        
        recentDecks.forEach(deck => {
            const deckElement = document.createElement('div');
            deckElement.className = 'deck-card';
            deckElement.innerHTML = `
                <div class="deck-info">
                    <h4>${deck.name}</h4>
                    <p>${deck.description || 'No description'}</p>
                    <small>${deck.cards.length} cards</small>
                </div>
                <div class="deck-actions">
                    <button class="btn-secondary" onclick="app.startStudySession('${deck.id}')">Study</button>
                    <button class="btn-primary" onclick="app.openAddCardModal('${deck.id}', '${deck.name}')">Add Card</button>
                </div>
            `;
            recentDecksContainer.appendChild(deckElement);
        });
    }
    
    renderDecks() {
        const decksContainer = document.getElementById('decks-container');
        decksContainer.innerHTML = '';
        
        if (this.decks.length === 0) {
            decksContainer.innerHTML = `
                <div class="empty-state">
                    <h3>No Decks Yet</h3>
                    <p>Create your first deck to get started with your studies!</p>
                    <button id="first-deck-btn" class="btn-primary">Create First Deck</button>
                </div>
            `;
            document.getElementById('first-deck-btn').addEventListener('click', () => {
                this.showModal('add-deck-modal');
            });
            return;
        }
        
        this.decks.forEach(deck => {
            const deckElement = document.createElement('div');
            deckElement.className = 'deck-card';
            deckElement.innerHTML = `
                <div class="deck-info">
                    <h4>${deck.name}</h4>
                    <p>${deck.description || 'No description'}</p>
                    <small>${deck.cards.length} cards • Created ${new Date(deck.createdAt).toLocaleDateString()}</small>
                </div>
                <div class="deck-actions">
                    <button class="btn-secondary" onclick="app.startStudySession('${deck.id}')">Study</button>
                    <button class="btn-primary" onclick="app.openAddCardModal('${deck.id}', '${deck.name}')">Add Card</button>
                    <button class="btn-secondary" onclick="app.deleteDeck('${deck.id}')">Delete</button>
                </div>
                ${deck.cards.length > 0 ? `
                <div class="card-list">
                    ${deck.cards.slice(0, 3).map(card => `
                        <div class="card-item">
                            <strong>Q:</strong> ${card.question.substring(0, 60)}${card.question.length > 60 ? '...' : ''}
                        </div>
                    `).join('')}
                    ${deck.cards.length > 3 ? `<small>+${deck.cards.length - 3} more cards</small>` : ''}
                </div>
                ` : ''}
            `;
            decksContainer.appendChild(deckElement);
        });
    }
    
    renderStudyView() {
        if (!this.currentDeckId) {
            document.getElementById('question-text').textContent = 'Select a deck to begin studying';
            document.getElementById('current-deck-name').textContent = 'Select a deck to start studying';
            document.getElementById('progress-fill').style.width = '0%';
            document.getElementById('progress-text').textContent = '0/0';
            return;
        }
        
        const deck = this.decks.find(d => d.id === this.currentDeckId);
        if (!deck || deck.cards.length === 0) {
            document.getElementById('question-text').textContent = 'This deck has no cards. Add some cards to start studying.';
            document.getElementById('current-deck-name').textContent = deck ? deck.name : 'Unknown Deck';
            document.getElementById('progress-fill').style.width = '0%';
            document.getElementById('progress-text').textContent = '0/0';
            return;
        }
        
        // Start study session if not already started
        if (!this.currentStudySession) {
            this.startStudySession(this.currentDeckId);
        }
        
        this.nextCard();
    }
    
    startStudySession(deckId) {
        const deck = this.decks.find(d => d.id === deckId);
        if (!deck || deck.cards.length === 0) return;
        
        this.currentDeckId = deckId;
        this.currentCardIndex = 0;
        
        // Filter cards that need review (for demo purposes, use all cards)
        this.currentStudySession = {
            deckId: deckId,
            cards: [...deck.cards],
            currentIndex: 0
        };
        
        document.getElementById('current-deck-name').textContent = deck.name;
        this.switchView('study-view');
        this.nextCard();
    }
    
    nextCard() {
        if (!this.currentStudySession || this.currentStudySession.currentIndex >= this.currentStudySession.cards.length) {
            // Study session completed
            document.getElementById('question-text').textContent = 'Study session completed!';
            document.getElementById('answer-container').style.display = 'none';
            document.getElementById('show-answer-btn').style.display = 'none';
            document.getElementById('rating-buttons').style.display = 'none';
            return;
        }
        
        const card = this.currentStudySession.cards[this.currentStudySession.currentIndex];
        
        document.getElementById('question-text').textContent = card.question;
        document.getElementById('answer-text').textContent = card.answer;
        
        // Reset UI elements
        document.getElementById('answer-container').style.display = 'none';
        document.getElementById('show-answer-btn').style.display = 'block';
        document.getElementById('rating-buttons').style.display = 'none';
        
        // Update progress
        const progress = ((this.currentStudySession.currentIndex) / this.currentStudySession.cards.length) * 100;
        document.getElementById('progress-fill').style.width = `${progress}%`;
        document.getElementById('progress-text').textContent = `${this.currentStudySession.currentIndex}/${this.currentStudySession.cards.length}`;
    }
    
    showAnswer() {
        document.getElementById('answer-container').style.display = 'block';
        document.getElementById('show-answer-btn').style.display = 'none';
        document.getElementById('rating-buttons').style.display = 'flex';
    }
    
    rateCard(rating) {
        if (!this.currentStudySession) return;
        
        // In a real implementation, we would update the card's scheduling info
        // based on the rating using the FSRS algorithm
        const card = this.currentStudySession.cards[this.currentStudySession.currentIndex];
        
        // Update card review info
        card.lastReviewed = new Date().toISOString();
        
        // Move to next card
        this.currentStudySession.currentIndex++;
        this.nextCard();
    }
    
    openAddCardModal(deckId, deckName) {
        document.getElementById('card-deck-id').value = deckId;
        document.querySelector('#add-card-modal h3').textContent = `Add Card to "${deckName}"`;
        this.showModal('add-card-modal');
    }
    
    deleteDeck(deckId) {
        if (confirm('Are you sure you want to delete this deck? All cards will be permanently removed.')) {
            this.decks = this.decks.filter(deck => deck.id !== deckId);
            this.saveDecks();
            
            if (this.currentView === 'decks-view') {
                this.renderDecks();
            }
        }
    }
}

// Initialize the app when the page loads
let app;
document.addEventListener('DOMContentLoaded', () => {
    app = new AksonApp();
});