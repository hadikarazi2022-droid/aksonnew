/**
 * Akson Web Application - Main JavaScript
 * Matching the desktop app functionality
 */

// ============================================
// STATE
// ============================================

const state = {
    // Document
    documentLoaded: false,
    documentText: '',
    currentDocTitle: '',
    currentPage: 1,
    totalPages: 1,

    // Sidebar
    sidebarVisible: true,
    thumbnailsVisible: true,
    currentSidebarTab: 'akson',

    // Sections collapsed state
    sectionsCollapsed: {
        summary: false,
        explainer: false,
        flashcards: false
    },

    // Flashcards
    flashcards: [],
    currentCardIndex: 0,
    isCardFlipped: false,

    // AI context
    lastSummary: '',
    lastExplanation: '',

    // Settings
    settings: {
        animationSpeed: 'normal',
        sidebarState: 'visible',
        autoSave: true,
        duplicateBehavior: 'ask',
        theme: 'default',
        fontSize: 'medium',
        themeMode: 'custom'
    }
};

// ============================================
// DOM ELEMENTS
// ============================================

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

// ============================================
// UTILITIES
// ============================================

function showLoading(msg = 'Processing...') {
    $('#loading-text').textContent = msg;
    $('#loading-overlay').classList.remove('hidden');
}

function hideLoading() {
    $('#loading-overlay').classList.add('hidden');
}

function showToast(message, type = 'success') {
    const icons = { success: '✅', error: '❌', warning: '⚠️', info: 'ℹ️' };
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
    <span class="toast-icon">${icons[type]}</span>
    <span class="toast-message">${message}</span>
  `;
    $('#toast-container').appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
}

function markdownToHtml(text) {
    if (!text) return '';
    return text
        .replace(/^### (.*$)/gim, '<h3>$1</h3>')
        .replace(/^## (.*$)/gim, '<h2>$1</h2>')
        .replace(/^# (.*$)/gim, '<h1>$1</h1>')
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.*?)\*/g, '<em>$1</em>')
        .replace(/`(.*?)`/g, '<code>$1</code>')
        .replace(/^\s*[-•]\s+(.*)$/gim, '<li>$1</li>')
        .replace(/\n/g, '<br>')
        .replace(/(<li>.*<\/li>)+/g, '<ul>$&</ul>');
}

// Storage
function saveToStorage(key, value) {
    try {
        localStorage.setItem(`akson_${key}`, JSON.stringify(value));
    } catch (e) { console.error(e); }
}

function loadFromStorage(key, defaultValue = null) {
    try {
        const v = localStorage.getItem(`akson_${key}`);
        return v ? JSON.parse(v) : defaultValue;
    } catch (e) { return defaultValue; }
}

// ============================================
// SIDEBAR SECTIONS
// ============================================

function toggleSection(sectionId) {
    const section = $(`#${sectionId}-section`);
    if (!section) return;

    state.sectionsCollapsed[sectionId] = !state.sectionsCollapsed[sectionId];
    section.classList.toggle('collapsed', state.sectionsCollapsed[sectionId]);
}

function updateFlashcardCount() {
    $('#flashcard-count').textContent = state.flashcards.length;
    $('#study-btn').disabled = state.flashcards.length === 0;
    $('#export-btn').disabled = state.flashcards.length === 0;
}

function renderFlashcardsList() {
    const list = $('#flashcards-list');
    if (state.flashcards.length === 0) {
        list.innerHTML = '<div class="output-placeholder">No flashcards yet</div>';
    } else {
        list.innerHTML = state.flashcards.slice(0, 5).map((card, i) => `
      <div class="flashcard-item" data-index="${i}">
        <span>${card.question.substring(0, 40)}${card.question.length > 40 ? '...' : ''}</span>
      </div>
    `).join('');

        if (state.flashcards.length > 5) {
            list.innerHTML += `<div class="flashcard-item">...and ${state.flashcards.length - 5} more</div>`;
        }
    }
    updateFlashcardCount();
}

// ============================================
// FILE HANDLING
// ============================================

async function handleFileUpload(file) {
    if (!file) return;

    const ext = '.' + file.name.split('.').pop().toLowerCase();
    if (!['.pdf', '.pptx', '.ppt'].includes(ext)) {
        showToast('Please upload a PDF or PPTX file', 'error');
        return;
    }

    showLoading('Uploading document...');

    try {
        const formData = new FormData();
        formData.append('file', file);

        const res = await fetch('/api/upload', { method: 'POST', body: formData });
        if (!res.ok) throw new Error('Upload failed');

        const data = await res.json();

        state.documentLoaded = true;
        state.documentText = data.text;
        state.currentDocTitle = file.name;

        if (ext === '.pdf') {
            const pdfUrl = `/uploads/${file.name}`;
            const pdfFrame = $('#pdf-frame');
            pdfFrame.src = `/pdfjs/web/viewer.html?file=${encodeURIComponent(pdfUrl)}`;
            pdfFrame.classList.remove('hidden');
            $('#upload-zone').classList.add('hidden');

            // Wait for load then hide internal toolbar
            pdfFrame.onload = () => {
                try {
                    const doc = pdfFrame.contentDocument;
                    if (doc) {
                        const style = doc.createElement('style');
                        style.textContent = `
                            .toolbar { display: none !important; }
                            #viewerContainer { top: 0 !important; }
                        `;
                        doc.head.appendChild(style);
                        // Initialize bridge
                        initPDFBridge();
                    }
                } catch (e) { console.error('Cannot access iframe content', e); }
            };
        }

        showToast('Document uploaded');
        hideLoading();
    } catch (e) {
        console.error(e);
        showToast('Upload failed', 'error');
        hideLoading();
    }
}

// ============================================
// AI API CALLS
// ============================================

async function streamAI(endpoint, data, outputEl) {
    outputEl.innerHTML = '<div class="output-placeholder">Thinking...</div>';

    try {
        const res = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });

        if (!res.ok) throw new Error('API error');

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let content = '';

        outputEl.innerHTML = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            const chunk = decoder.decode(value);
            for (const line of chunk.split('\n')) {
                if (line.startsWith('data: ')) {
                    const d = line.slice(6);
                    if (d === '[DONE]') continue;
                    try {
                        const parsed = JSON.parse(d);
                        if (parsed.content) {
                            content += parsed.content;
                            outputEl.innerHTML = markdownToHtml(content);
                        }
                    } catch (e) { }
                }
            }
        }

        return content;
    } catch (e) {
        outputEl.innerHTML = `<div class="output-placeholder" style="color: var(--error);">Error: ${e.message}</div>`;
        throw e;
    }
}

async function generateSummary() {
    const text = state.documentText || $('#summary-instruction').value;
    if (!text) {
        showToast('No content to summarize', 'warning');
        return;
    }

    const instruction = $('#summary-instruction').value;
    const result = await streamAI('/api/summarize', {
        text,
        custom_prompt: instruction
    }, $('#summary-output'));

    state.lastSummary = result;
}

async function generateFlashcards() {
    const text = state.documentText || state.lastSummary;
    if (!text) {
        showToast('No content for flashcards', 'warning');
        return;
    }

    showLoading('Generating flashcards...');

    try {
        const res = await fetch('/api/flashcards', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text })
        });

        if (!res.ok) throw new Error('Failed');

        const data = await res.json();
        if (data.cards?.length > 0) {
            state.flashcards.push(...data.cards);
            saveToStorage('flashcards', state.flashcards);
            renderFlashcardsList();
            showToast(`Generated ${data.cards.length} flashcards!`);
        }

        hideLoading();
    } catch (e) {
        showToast('Failed to generate flashcards', 'error');
        hideLoading();
    }
}

async function sendChatMessage() {
    const input = $('#ask-ai-input');
    const question = input.value.trim();
    if (!question) return;

    input.value = '';

    const chatOutput = $('#chat-output');
    chatOutput.innerHTML += `<div class="chat-bubble user"><span>${question}</span></div>`;

    const context = state.lastSummary || state.documentText;

    try {
        const res = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question, context })
        });

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let response = '';

        const responseBubble = document.createElement('div');
        responseBubble.className = 'chat-bubble system';
        responseBubble.innerHTML = '<span class="chat-icon">🤖</span><span class="chat-text"></span>';
        chatOutput.appendChild(responseBubble);

        const textSpan = responseBubble.querySelector('.chat-text');

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            const chunk = decoder.decode(value);
            for (const line of chunk.split('\n')) {
                if (line.startsWith('data: ')) {
                    const d = line.slice(6);
                    if (d === '[DONE]') continue;
                    try {
                        const parsed = JSON.parse(d);
                        if (parsed.content) {
                            response += parsed.content;
                            textSpan.textContent = response;
                        }
                    } catch (e) { }
                }
            }
        }

        chatOutput.scrollTop = chatOutput.scrollHeight;
    } catch (e) {
        showToast('Chat error', 'error');
    }
}

// ============================================
// FLASHCARD STUDY MODE
// ============================================

function openFlashcardModal() {
    if (state.flashcards.length === 0) {
        showToast('No flashcards to study', 'warning');
        return;
    }

    state.currentCardIndex = 0;
    state.isCardFlipped = false;

    $('#flashcard-modal').classList.remove('hidden');
    $('#deck-title').textContent = state.currentDocTitle || 'untitled';

    updateStudyCard();
}

function closeFlashcardModal() {
    $('#flashcard-modal').classList.add('hidden');
}

function updateStudyCard() {
    const card = state.flashcards[state.currentCardIndex];
    if (!card) return;

    $('#card-front-text').textContent = card.question;
    $('#card-back-text').textContent = card.answer;
    $('#card-progress').textContent = `${state.currentCardIndex + 1} / ${state.flashcards.length}`;

    $('#prev-card').disabled = state.currentCardIndex === 0;
    $('#next-card').disabled = state.currentCardIndex === state.flashcards.length - 1;

    state.isCardFlipped = false;
    $('#study-card').classList.remove('flipped');
}

function flipStudyCard() {
    state.isCardFlipped = !state.isCardFlipped;
    $('#study-card').classList.toggle('flipped', state.isCardFlipped);
}

function nextCard() {
    if (state.currentCardIndex < state.flashcards.length - 1) {
        state.currentCardIndex++;
        updateStudyCard();
    }
}

function prevCard() {
    if (state.currentCardIndex > 0) {
        state.currentCardIndex--;
        updateStudyCard();
    }
}

// ============================================
// SETTINGS MODAL
// ============================================

function openSettingsModal() {
    $('#settings-modal').classList.remove('hidden');
}

function closeSettingsModal() {
    $('#settings-modal').classList.add('hidden');
}

function switchSettingsPanel(panelId) {
    $$('.settings-nav-item').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.panel === panelId);
    });

    $$('.settings-panel').forEach(panel => {
        panel.classList.toggle('active', panel.id === `panel-${panelId}`);
    });
}

function selectTheme(theme) {
    state.settings.theme = theme;
    $$('.theme-card').forEach(card => {
        card.classList.toggle('active', card.dataset.theme === theme);
    });

    // Apply theme colors based on selection
    const themes = {
        default: { accent: '#8b5cf6', secondary: '#a78bfa' },
        ocean: { accent: '#0ea5e9', secondary: '#38bdf8' },
        forest: { accent: '#10b981', secondary: '#34d399' },
        sunset: { accent: '#f59e0b', secondary: '#fbbf24' },
        lavender: { accent: '#a78bfa', secondary: '#c4b5fd' },
        minimal: { accent: '#6b7280', secondary: '#9ca3af' }
    };

    const t = themes[theme];
    if (t) {
        document.documentElement.style.setProperty('--accent-primary', t.accent);
        document.documentElement.style.setProperty('--accent-secondary', t.secondary);
    }

    saveToStorage('settings', state.settings);
}

// ============================================
// ANKI EXPORT
// ============================================

async function exportToAnki() {
    if (state.flashcards.length === 0) {
        showToast('No flashcards to export', 'warning');
        return;
    }

    showLoading('Exporting to Anki...');

    try {
        const res = await fetch('/api/export-anki', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                deck_name: state.currentDocTitle || 'Akson Deck',
                cards: state.flashcards
            })
        });

        if (!res.ok) throw new Error('Export failed');

        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${state.currentDocTitle || 'akson_deck'}.apkg`;
        a.click();
        URL.revokeObjectURL(url);

        showToast('Exported successfully!');
        hideLoading();
    } catch (e) {
        showToast('Export failed', 'error');
        hideLoading();
    }
}

// ============================================
// SIDEBAR TABS
// ============================================

function switchSidebarTab(tab) {
    state.currentSidebarTab = tab;

    $$('.sidebar-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.tab === tab);
    });

    $('#sidebar-akson').classList.toggle('hidden', tab !== 'akson');
    $('#sidebar-library').classList.toggle('hidden', tab !== 'library');
}

// ============================================
// TOGGLE PANELS
// ============================================

function toggleSidebar() {
    state.sidebarVisible = !state.sidebarVisible;
    $('#ai-sidebar').classList.toggle('hidden', !state.sidebarVisible);
}

function toggleThumbnails() {
    // Toggle PDF.js internal thumbnail sidebar only
    pdfAction('sidebar');
}

// ============================================
// PDF VIEWER BRIDGE
// ============================================

function initPDFBridge() {
    const frame = $('#pdf-frame');
    if (!frame || !frame.contentWindow) return;

    // Check if app is ready
    const app = frame.contentWindow.PDFViewerApplication;

    if (!app || !app.initialized) {
        setTimeout(initPDFBridge, 200);
        return;
    }

    console.log('🔗 PDF Bridge connected');

    // Sync Page Number
    app.eventBus.on('pagechanging', (e) => {
        $('#page-info').textContent = `${e.pageNumber} of ${app.pagesCount}`;
        state.currentPage = e.pageNumber;
        state.totalPages = app.pagesCount;
    });

    // Sync Zoom Slider
    app.eventBus.on('scalechanging', (e) => {
        const val = Math.round(e.scale * 100);
        $('#zoom-select').value = app.pdfViewer.currentScaleValue;
        // If it's a number, sync it approximately
        if (!isNaN(val)) {
            // Find closest option if needed, or just let it be
        }
    });

    // Initial sync
    $('#page-info').textContent = `${app.page} of ${app.pagesCount}`;
}

// Toolbar controls
function setZoom(val) {
    const frame = $('#pdf-frame');
    if (!frame.contentWindow || !frame.contentWindow.PDFViewerApplication) return;
    const app = frame.contentWindow.PDFViewerApplication;

    if (val === 'in') {
        app.zoomIn();
    } else if (val === 'out') {
        app.zoomOut();
    } else {
        app.pdfViewer.currentScaleValue = val;
    }
}

function pdfAction(action) {
    const frame = $('#pdf-frame');
    if (!frame.contentWindow || !frame.contentWindow.PDFViewerApplication) return;
    const app = frame.contentWindow.PDFViewerApplication;

    // Update active state for toolbar buttons
    const toolButtons = ['select-btn', 'highlight-btn', 'draw-btn', 'text-btn'];
    toolButtons.forEach(id => {
        const btn = $(`#${id}`);
        if (btn) btn.classList.remove('active');
    });

    switch (action) {
        case 'print': window.print(); break;
        case 'download': app.download(); break;
        case 'sidebar':
            // Toggle PDF.js internal thumbnail sidebar
            app.pdfSidebar.toggle();
            break;
        case 'select':
            // Disable annotation mode, return to normal cursor
            app.eventBus.dispatch('switchannotationeditormode', {
                source: this,
                mode: 0  // AnnotationEditorType.NONE
            });
            $('#select-btn').classList.add('active');
            break;
        case 'highlight':
            // Enable/disable highlight annotation mode (editor mode 14 for highlight)
            app.eventBus.dispatch('switchannotationeditormode', {
                source: this,
                mode: 14  // AnnotationEditorType.HIGHLIGHT
            });
            $('#highlight-btn').classList.add('active');
            break;
        case 'draw':
            // Enable ink/draw annotation mode (editor mode 15)
            app.eventBus.dispatch('switchannotationeditormode', {
                source: this,
                mode: 15  // AnnotationEditorType.INK
            });
            $('#draw-btn').classList.add('active');
            break;
        case 'text':
            // Enable freetext annotation mode (editor mode 3)
            app.eventBus.dispatch('switchannotationeditormode', {
                source: this,
                mode: 3   // AnnotationEditorType.FREETEXT
            });
            $('#text-btn').classList.add('active');
            break;
    }
}


function initEventListeners() {
    // File upload
    $('#file-input').addEventListener('change', e => handleFileUpload(e.target.files[0]));

    $('#upload-zone').addEventListener('click', () => $('#file-input').click());

    $('#upload-zone').addEventListener('dragover', e => {
        e.preventDefault();
        $('#upload-zone').style.borderColor = 'var(--accent-primary)';
    });

    $('#upload-zone').addEventListener('dragleave', () => {
        $('#upload-zone').style.borderColor = '';
    });

    $('#upload-zone').addEventListener('drop', e => {
        e.preventDefault();
        $('#upload-zone').style.borderColor = '';
        handleFileUpload(e.dataTransfer.files[0]);
    });

    // Section headers collapse
    $$('.section-header').forEach(header => {
        header.addEventListener('click', e => {
            if (e.target.closest('.ai-btn')) return;
            const section = header.dataset.section;
            if (section) toggleSection(section);
        });
    });

    // AI buttons
    $('#generate-summary-btn').addEventListener('click', generateSummary);
    $('#generate-flashcards-btn').addEventListener('click', generateFlashcards);
    $('#refresh-summary').addEventListener('click', generateSummary);

    // Study and export
    $('#study-btn').addEventListener('click', openFlashcardModal);
    $('#export-btn').addEventListener('click', exportToAnki);

    // Chat
    $('#send-ask-ai').addEventListener('click', sendChatMessage);
    $('#ask-ai-input').addEventListener('keypress', e => {
        if (e.key === 'Enter') sendChatMessage();
    });

    // Sidebar tabs
    $$('.sidebar-tab').forEach(tab => {
        tab.addEventListener('click', () => switchSidebarTab(tab.dataset.tab));
    });

    // Toolbar buttons
    $('#toggle-thumbnails').addEventListener('click', toggleThumbnails);

    // PDF Controls
    $('#zoom-in').addEventListener('click', () => setZoom('in'));
    $('#zoom-out').addEventListener('click', () => setZoom('out'));
    $('#zoom-select').addEventListener('change', (e) => setZoom(e.target.value));

    $('#print-btn').addEventListener('click', () => pdfAction('print'));
    $('#download-btn').addEventListener('click', () => pdfAction('download'));
    $('#select-btn').addEventListener('click', () => pdfAction('select'));
    $('#highlight-btn').addEventListener('click', () => pdfAction('highlight'));
    $('#draw-btn').addEventListener('click', () => pdfAction('draw'));
    $('#text-btn').addEventListener('click', () => pdfAction('text'));

    $('#toggle-sidebar').addEventListener('click', toggleSidebar);
    $('#settings-btn').addEventListener('click', openSettingsModal);
    $('#sidebar-settings-btn').addEventListener('click', openSettingsModal);

    // Flashcard modal
    $('#close-flashcard-modal').addEventListener('click', closeFlashcardModal);
    $('#study-card').addEventListener('click', flipStudyCard);
    $('#prev-card').addEventListener('click', prevCard);
    $('#next-card').addEventListener('click', nextCard);

    // Settings modal
    $('#close-settings-modal').addEventListener('click', closeSettingsModal);

    $$('.settings-nav-item').forEach(item => {
        item.addEventListener('click', () => switchSettingsPanel(item.dataset.panel));
    });

    $$('.theme-card').forEach(card => {
        card.addEventListener('click', () => selectTheme(card.dataset.theme));
    });

    // Modal backdrops
    $$('.modal-backdrop').forEach(backdrop => {
        backdrop.addEventListener('click', () => {
            backdrop.closest('.modal').classList.add('hidden');
        });
    });

    // Keyboard shortcuts
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') {
            $$('.modal:not(.hidden)').forEach(m => m.classList.add('hidden'));
        }

        if ((e.ctrlKey || e.metaKey) && e.key === 'b') {
            e.preventDefault();
            toggleSidebar();
        }

        // Arrow keys in flashcard modal
        if (!$('#flashcard-modal').classList.contains('hidden')) {
            if (e.key === 'ArrowLeft') prevCard();
            if (e.key === 'ArrowRight') nextCard();
            if (e.key === ' ') { e.preventDefault(); flipStudyCard(); }
        }
    });
}

// ============================================
// INITIALIZATION
// ============================================

function init() {
    // Load saved data
    state.flashcards = loadFromStorage('flashcards', []);
    state.settings = { ...state.settings, ...loadFromStorage('settings', {}) };

    // Apply saved theme
    selectTheme(state.settings.theme);

    // Render flashcards
    renderFlashcardsList();

    // Init event listeners
    initEventListeners();

    // Load Trace Monkey by default if no sessions/docs
    const defaultPdf = '/pdfjs/web/compressed.tracemonkey-pldi-09.pdf';
    const pdfFrame = $('#pdf-frame');
    pdfFrame.src = `/pdfjs/web/viewer.html?file=${encodeURIComponent(defaultPdf)}`;
    pdfFrame.classList.remove('hidden');
    $('#upload-zone').classList.add('hidden');

    // Wire bridge for the default PDF
    pdfFrame.onload = () => {
        try {
            const doc = pdfFrame.contentDocument;
            if (doc) {
                const style = doc.createElement('style');
                style.textContent = `
                    .toolbar { display: none !important; }
                    #viewerContainer { top: 0 !important; }
                `;
                doc.head.appendChild(style);
                initPDFBridge();
            }
        } catch (e) { console.error('Cannot access iframe content', e); }
    };

    console.log('🚀 Akson Web App initialized');
}

document.addEventListener('DOMContentLoaded', init);
