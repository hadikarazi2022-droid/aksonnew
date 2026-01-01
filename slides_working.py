#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PDF.js desktop viewer with:
- selectable text,
- left thumbnails,
- RIGHT SIDEBAR (stacked):
    1) Current Page Summary (auto on page change; appended to bottom Notes),
    2) Term Explainer (single-word selections → concise definition),
    3) Flashcards (local quick list + export),
- BOTTOM NOTES panel (smaller by default, resizable & persisted) — no auto "selected text" logging,
- DRAW + TEXT annotation tools and "Save (Flatten)" (burns on a new PDF),
- top bar with three buttons: Compact Mode, Slides, Akson Flashcards.

Requirements:
    pip install pywebview requests pypdf reportlab openai pymupdf
Environment:
    export OPENAI_API_KEY="sk-..."
    export OPENAI_MODEL="gpt-4o-mini"    # optional; defaults to gpt-4o-mini

Run:
    python pdfjs_viewer_app.py [optional.pdf]
"""
from __future__ import annotations

import io
import os
import json
import sys
import zipfile
import shutil
import socket
import threading
import tempfile
import urllib.parse
import re
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

import requests
import webview
import uuid
from datetime import datetime

# Akson Cards imports
from akson_cards.store import AksonCardsStore
from akson_cards.models import Deck, Note, Card, Review, NoteModel
from akson_cards.study import StudySession
from akson_cards.fsrs import FSRSConfig
from dotenv import load_dotenv
load_dotenv()

# ---- Settings ----
PDFJS_VERSION = "3.11.174"
PDFJS_ZIP_URL = (
    f"https://github.com/mozilla/pdf.js/releases/download/v{PDFJS_VERSION}/pdfjs-{PDFJS_VERSION}-dist.zip"
)
APP_TITLE = "PDF.js Viewer"
from version import __version__ as APP_VERSION
CWD_BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
CACHE_ROOT = Path.home() / ".cache" / "pdfjs_viewer"
PDFJS_DIR = CACHE_ROOT / f"pdfjs-{PDFJS_VERSION}-dist"
DOCS_DIR = CACHE_ROOT / "docs"
WRAPPER_HTML = PDFJS_DIR / "app_wrapper.html"  # served from same origin as PDF.js
ICONS_DIR = CWD_BASE / "icons"
MANIFEST_URL = os.environ.get("AKSON_UPDATE_MANIFEST", "https://raw.githubusercontent.com/Sheikh-Hamoodi/akson/main/manifest.json")


OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
# Import API key from config
from akson.config import OPENAI_API_KEY


# ---- Utilities ----
def ensure_dirs() -> None:
    PDFJS_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)


def download_pdfjs_if_needed() -> None:
    """Download PDF.js in background if not present."""
    if (PDFJS_DIR / "web" / "viewer.html").exists() and (PDFJS_DIR / "build" / "pdf.js").exists():
        # Patch viewer.html and viewer.css
        patch_viewer_html()
        patch_viewer_css()
        return True
    print(f"Downloading pdf.js {PDFJS_VERSION} in background...")
    try:
        r = requests.get(PDFJS_ZIP_URL, timeout=10)  # Reduced from 30s to 10s
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            zf.extractall(PDFJS_DIR)
        print("PDF.js download completed.")
        # Patch viewer.html and viewer.css
        patch_viewer_html()
        patch_viewer_css()
        return True
    except Exception as e:
        print(f"PDF.js download failed: {e}")
        return False


def patch_viewer_css() -> None:
    """Patch viewer.css to match sidebar styling - modern macOS aesthetic."""
    viewer_css_path = PDFJS_DIR / "web" / "viewer.css"
    if not viewer_css_path.exists():
        return
    
    try:
        content = viewer_css_path.read_text(encoding="utf-8")
        import re
        
        # Always remove old patch if exists (allows re-patching for updates)
        if '/* SIDEBAR-STYLE MATCHING' in content:
            # Remove everything from our marker to end of file
            content = re.sub(r'/\* ============================================\s*\n\s*\* SIDEBAR-STYLE MATCHING.*$', '', content, flags=re.DOTALL)
            # Also remove any standalone border-radius replacements we might have made
            # (we'll re-apply them below)
        
        # Directly replace PDF.js button border-radius:2px with 8px
        # Match the exact pattern from viewer.css line 3085
        content = re.sub(
            r'(\.toolbarButton,\s*\.dropdownToolbarButton,\s*\.secondaryToolbarButton,\s*\.dialogButton\{[^\}]*border-radius:)2px',
            r'\1 8px',
            content
        )
        
        # Replace font:message-box shorthand - add font-family before it
        # This preserves the shorthand but adds our font-family
        content = re.sub(
            r'font:message-box',
            r'font-family: -apple-system, BlinkMacSystemFont, \'SF Pro Display\', \'Segoe UI\', Roboto, sans-serif; font: message-box',
            content
        )
        
        # Also replace in .toolbar rule
        content = re.sub(
            r'(\.toolbar\{[^\}]*font:)message-box',
            r'\1 -apple-system, BlinkMacSystemFont, \'SF Pro Display\', \'Segoe UI\', Roboto, sans-serif',
            content
        )
        
        # Append our custom override styles at the end
        custom_css = '''

/* ============================================
   SIDEBAR-STYLE MATCHING: Modern macOS Aesthetic
   Added by Akson - Overrides PDF.js default styles
   ============================================ */

/* Typography - Match sidebar font - Override PDF.js font:message-box */
body, html {
  font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Segoe UI', Roboto, sans-serif !important;
  -webkit-font-smoothing: antialiased !important;
  -moz-osx-font-smoothing: grayscale !important;
}

.toolbar, #toolbarContainer, #toolbarSidebar {
  font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Segoe UI', Roboto, sans-serif !important;
  -webkit-font-smoothing: antialiased !important;
}

/* Override PDF.js font:message-box shorthand */
:is(.toolbar, .editorParamsToolbar, .findbar, #sidebarContainer) :is(input, button, select),
.secondaryToolbar :is(input, button, a, select),
.toolbarButton, .secondaryToolbarButton, .dialogButton, .dropdownToolbarButton {
  font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Segoe UI', Roboto, sans-serif !important;
  -webkit-font-smoothing: antialiased !important;
  -moz-osx-font-smoothing: grayscale !important;
}

.treeItem, .treeItemContent, .toolbarField, input, select, textarea {
  font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Segoe UI', Roboto, sans-serif !important;
  -webkit-font-smoothing: antialiased !important;
}

/* Toolbar buttons - Override border-radius:2px to 8px */
.toolbarButton, .secondaryToolbarButton, .dialogButton, .dropdownToolbarButton {
  border-radius: 8px !important;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
}

/* Hover states with transforms */
.toolbarButton:is(:hover, :focus-visible),
.splitToolbarButton > .toolbarButton:is(:hover, :focus-visible),
.dropdownToolbarButton:hover,
.secondaryToolbarButton:is(:hover, :focus-visible),
.dialogButton:is(:hover, :focus-visible) {
  transform: translateY(-1px) !important;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15) !important;
  border-radius: 8px !important;
}

.toolbarButton:active, .secondaryToolbarButton:active, .dialogButton:active {
  transform: translateY(0) !important;
  transition: all 0.1s ease !important;
}

/* Toolbar shadows */
.toolbar, #toolbarContainer, #toolbarSidebar {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1) !important;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
}

/* Dropdowns and doorhangers - Rounded corners */
.doorhanger, .doorHanger {
  border-radius: 10px !important;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2) !important;
  backdrop-filter: blur(20px) !important;
  -webkit-backdrop-filter: blur(20px) !important;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
  border: 1px solid rgba(255, 255, 255, 0.1) !important;
}

.doorhanger > .doorhangerItem, .doorHanger > .doorhangerItem {
  border-radius: 6px !important;
  margin: 2px 4px !important;
  transition: all 0.15s cubic-bezier(0.4, 0, 0.2, 1) !important;
}

.doorhanger > .doorhangerItem:hover, .doorHanger > .doorhangerItem:hover {
  transform: translateX(2px) !important;
}

/* Dialogs */
.dialog, .dialogContainer {
  border-radius: 12px !important;
  box-shadow: 0 12px 48px rgba(0, 0, 0, 0.3) !important;
  backdrop-filter: blur(20px) !important;
  -webkit-backdrop-filter: blur(20px) !important;
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) !important;
}

.dialogButton {
  border-radius: 8px !important;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
  font-weight: 500 !important;
}

.dialogButton:hover {
  transform: translateY(-1px) !important;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15) !important;
}

/* Input fields */
.toolbarField, input[type="text"], input[type="number"], select, textarea {
  border-radius: 8px !important;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
}

.toolbarField:focus, input:focus, select:focus, textarea:focus {
  outline: none !important;
  box-shadow: 0 0 0 3px rgba(139, 92, 246, 0.2) !important;
  border-color: rgba(139, 92, 246, 0.5) !important;
}

/* Findbar */
.findbar {
  border-radius: 0 0 10px 10px !important;
}

/* Tree items */
.treeItem, .treeItemContent {
  border-radius: 6px !important;
  transition: all 0.15s cubic-bezier(0.4, 0, 0.2, 1) !important;
  margin: 1px 4px !important;
}

.treeItem:hover {
  transform: translateX(2px) !important;
}

/* Thumbnails */
.thumbnail {
  border-radius: 8px !important;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1) !important;
}

.thumbnail:hover {
  transform: translateY(-2px) scale(1.02) !important;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.2) !important;
}

.thumbnail.selected {
  box-shadow: 0 0 0 2px rgba(139, 92, 246, 0.5), 0 4px 16px rgba(0, 0, 0, 0.2) !important;
}

/* Progress bar */
#loadingBar, .progressBar {
  border-radius: 4px !important;
}

/* Scrollbars - Match sidebar */
::-webkit-scrollbar {
  width: 10px !important;
  height: 10px !important;
}

::-webkit-scrollbar-track {
  background: var(--scrollbar-bg-color) !important;
  border-radius: 5px !important;
}

::-webkit-scrollbar-thumb {
  background: var(--scrollbar-color) !important;
  border-radius: 5px !important;
  border: 2px solid var(--scrollbar-bg-color) !important;
  transition: background 0.2s ease !important;
}

::-webkit-scrollbar-thumb:hover {
  background: var(--button-hover-color) !important;
}

:root.is-light ::-webkit-scrollbar-thumb {
  background: rgba(0, 0, 0, 0.2) !important;
}

:root.is-light ::-webkit-scrollbar-thumb:hover {
  background: rgba(0, 0, 0, 0.3) !important;
}

:root.is-dark ::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.2) !important;
}

:root.is-dark ::-webkit-scrollbar-thumb:hover {
  background: rgba(255, 255, 255, 0.3) !important;
}

/* Separators */
.verticalToolbarSeparator, .horizontalToolbarSeparator {
  opacity: 0.3 !important;
  transition: opacity 0.2s ease !important;
}

/* Secondary toolbar */
#secondaryToolbar {
  border-radius: 0 0 10px 10px !important;
}

/* Annotation editor toolbar */
.editorParamsToolbar {
  border-radius: 8px !important;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.15) !important;
}

/* Focus states */
*:focus-visible {
  outline: 2px solid rgba(139, 92, 246, 0.5) !important;
  outline-offset: 2px !important;
  border-radius: 4px !important;
}

/* Toggled states */
.toolbarButton.toggled, .secondaryToolbarButton.toggled {
  box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.1) !important;
}
'''
        
        # Append to end of file
        content += custom_css
        viewer_css_path.write_text(content, encoding="utf-8")
        print("✓ Patched viewer.css with sidebar-style matching")
    except Exception as e:
        print(f"⚠️  Error patching viewer.css: {e}")


def patch_viewer_html(initial_theme: str = 'light') -> None:
    """Add theme toggle button and highlight button in PDF.js viewer.html toolbar.
    
    Args:
        initial_theme: The initial theme to use ('light' or 'dark'). Defaults to 'light'.
    """
    viewer_html_path = PDFJS_DIR / "web" / "viewer.html"
    if not viewer_html_path.exists():
        return
    
    try:
        content = viewer_html_path.read_text(encoding="utf-8")
        
        import re
        
        # Remove existing theme toggle button and separator if they exist (to reposition them)
        if 'id="themeTogglePdf"' in content:
            # Remove theme button and any preceding/following separator
            content = re.sub(r'<div id="editorModeSeparator" class="verticalToolbarSeparator"></div>\s*<button id="themeTogglePdf"[^>]*>.*?</button>', '', content, flags=re.DOTALL)
            content = re.sub(r'<button id="themeTogglePdf"[^>]*>.*?</button>\s*<div id="editorModeSeparator" class="verticalToolbarSeparator"></div>', '', content, flags=re.DOTALL)
            content = re.sub(r'<button id="themeTogglePdf"[^>]*>.*?</button>', '', content, flags=re.DOTALL)
            content = re.sub(r'<div id="editorModeSeparator" class="verticalToolbarSeparator"></div>', '', content)
        
        # Add highlight button if it doesn't exist
        if 'id="editorHighlight"' not in content and 'id="editorModeButtons"' in content:
            # Find the editorModeButtons div and add highlight button as first button
            highlight_button = '''                  <button id="editorHighlight" class="toolbarButton" disabled="disabled" title="Highlight" role="radio" aria-checked="false" tabindex="33" data-l10n-id="editor_highlight2">
                    <span data-l10n-id="editor_highlight2_label">Highlight</span>
                  </button>
'''
            # Insert highlight button right after the opening of editorModeButtons
            content = re.sub(
                r'(<div id="editorModeButtons"[^>]*>\s*)',
                r'\1' + highlight_button,
                content
            )
            print("✓ Added highlight button to viewer.html")
        
        # Ensure theme toggle button and separator are positioned after editorModeButtons
        if 'id="editorModeButtons"' in content and 'id="themeTogglePdf"' not in content:
            # Insert theme toggle button and separator after the entire editorModeButtons block
            theme_button_and_separator = '''
                <div id="editorModeSeparator" class="verticalToolbarSeparator"></div>
                <button id="themeTogglePdf" class="toolbarButton" title="Toggle Light/Dark Mode" tabindex="34">
                  <span id="themeIconPdf">🌙</span>
                </button>
'''
            # Match the closing </div> of editorModeButtons and insert after it
            content = re.sub(
                r'(<div id="editorModeButtons"[^>]*>.*?</div>)',
                r'\1' + theme_button_and_separator,
                content,
                flags=re.DOTALL
            )
            print("✓ Added theme toggle button after editorModeButtons")
        
        # If theme button already exists, update it with animation
        if 'id="themeTogglePdf"' in content:
            # Replace existing button with animated version - match PDF.js toolbar button style
            old_button_pattern = r'<button id="themeTogglePdf"[^>]*>.*?</button>'
            new_button_html = '''<button id="themeTogglePdf" class="toolbarButton" title="Toggle Light/Dark Mode" tabindex="34">
                  <span id="themeIconPdf">🌙</span>
                </button>'''
            content = re.sub(old_button_pattern, new_button_html, content, flags=re.DOTALL)
            
            # Add CSS override to make icon visible (PDF.js hides span by default), white moon, black sun, smaller moon
            # Also add CSS to keep PDF canvas/images in original colors
            # Also add CSS for highlight button
            css_override = '''
  <style>
    /* Highlight button icon */
    #editorHighlight::before {
      -webkit-mask-image: url(images/highlighter.svg);
      mask-image: url(images/highlighter.svg);
    }
    
    /* Elegant highlight color picker */
    #highlightColorPicker {
      display: none;
      position: absolute;
      top: calc(100% + 8px);
      left: 50%;
      transform: translateX(-50%);
      background: rgba(30, 30, 35, 0.98);
      border: 1px solid rgba(255, 255, 255, 0.2);
      border-radius: 12px;
      padding: 12px;
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
      z-index: 100000;
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
    }
    
    #highlightColorPicker.show {
      display: flex;
      flex-direction: column;
      gap: 8px;
      animation: slideDown 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
    }
    
    @keyframes slideDown {
      from {
        opacity: 0;
        transform: translateX(-50%) translateY(-10px);
      }
      to {
        opacity: 1;
        transform: translateX(-50%) translateY(0);
      }
    }
    
    .colorPickerRow {
      display: flex;
      gap: 8px;
      align-items: center;
    }
    
    .highlightColorSwatch {
      width: 32px;
      height: 32px;
      border-radius: 8px;
      cursor: pointer;
      border: 2px solid rgba(255, 255, 255, 0.2);
      transition: all 0.25s cubic-bezier(0.34, 1.56, 0.64, 1);
      position: relative;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
    }
    
    .highlightColorSwatch:hover {
      transform: scale(1.15) translateY(-2px);
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.5);
      border-color: rgba(255, 255, 255, 0.5);
    }
    
    .highlightColorSwatch.selected {
      border: 3px solid rgba(255, 255, 255, 0.9);
      box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.3), 0 4px 16px rgba(0, 0, 0, 0.6);
      transform: scale(1.1);
    }
    
    .highlightColorSwatch::after {
      content: '';
      position: absolute;
      inset: 0;
      border-radius: 6px;
      background: linear-gradient(135deg, rgba(255,255,255,0.3) 0%, transparent 100%);
      pointer-events: none;
    }
    
    /* Beautiful text highlight styles */
    .textLayer .pdf-highlight {
      padding: 2px 0;
      border-radius: 3px;
      margin: -2px 0;
      transition: all 0.3s ease;
      box-shadow: 0 1px 4px rgba(0, 0, 0, 0.1);
      animation: highlightPulse 0.5s cubic-bezier(0.34, 1.56, 0.64, 1);
    }
    
    @keyframes highlightPulse {
      0% {
        opacity: 0;
        transform: scale(0.95);
      }
      50% {
        opacity: 0.7;
      }
      100% {
        opacity: 1;
        transform: scale(1);
      }
    }
    
    .textLayer .pdf-highlight:hover {
      filter: brightness(1.1);
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
    }
    
    /* Highlight color classes with elegant opacity */
    .pdf-highlight.yellow { background-color: rgba(255, 235, 59, 0.5) !important; }
    .pdf-highlight.green { background-color: rgba(76, 175, 80, 0.5) !important; }
    .pdf-highlight.blue { background-color: rgba(33, 150, 243, 0.5) !important; }
    .pdf-highlight.pink { background-color: rgba(255, 64, 129, 0.5) !important; }
    .pdf-highlight.orange { background-color: rgba(255, 152, 0, 0.5) !important; }
    .pdf-highlight.purple { background-color: rgba(156, 39, 176, 0.5) !important; }
    .pdf-highlight.red { background-color: rgba(244, 67, 54, 0.5) !important; }
    .pdf-highlight.cyan { background-color: rgba(0, 188, 212, 0.5) !important; }
    
    #themeTogglePdf {
      display: flex !important;
      align-items: center !important;
      justify-content: center !important;
      position: relative !important;
    }
    #themeTogglePdf::before {
      display: none !important;
    }
    #themeTogglePdf > #themeIconPdf,
    #themeTogglePdf span#themeIconPdf,
    button#themeTogglePdf > span#themeIconPdf {
      display: inline-block !important;
      width: auto !important;
      height: auto !important;
      overflow: visible !important;
      font-size: 13px !important;
      line-height: 1 !important;
      transition: transform 0.4s cubic-bezier(0.68, -0.55, 0.27, 1.55), opacity 0.3s ease, filter 0.3s ease;
      margin: 0 !important;
      padding: 0 !important;
      text-align: center !important;
      transform-origin: center center;
      opacity: 0.85;
    }
    /* Moon (crescent) - make it light/white for dark mode */
    button#themeTogglePdf[data-theme="dark"] > #themeIconPdf,
    button#themeTogglePdf[data-theme="dark"] > span#themeIconPdf {
      filter: grayscale(1) brightness(2) contrast(1.2);
    }
    /* Sun - make it dark gray */
    button#themeTogglePdf[data-theme="light"] > #themeIconPdf,
    button#themeTogglePdf[data-theme="light"] > span#themeIconPdf {
      filter: grayscale(1) brightness(0.4) contrast(1.3);
    }
    #themeTogglePdf:hover > #themeIconPdf,
    #themeTogglePdf:hover span#themeIconPdf,
    button#themeTogglePdf:hover > span#themeIconPdf {
      opacity: 1;
    }
    /* Hover state for moon - keep it light */
    button#themeTogglePdf[data-theme="dark"]:hover > #themeIconPdf,
    button#themeTogglePdf[data-theme="dark"]:hover > span#themeIconPdf {
      filter: grayscale(1) brightness(2.2) contrast(1.2);
    }
    /* Hover state for sun - slightly brighter but still dark gray */
    button#themeTogglePdf[data-theme="light"]:hover > #themeIconPdf,
    button#themeTogglePdf[data-theme="light"]:hover > span#themeIconPdf {
      filter: grayscale(1) brightness(0.5) contrast(1.3);
    }
    /* Override PDF.js automatic theme detection - force it to respect app theme */
    /* PDF.js uses @media (prefers-color-scheme: dark) - we completely override it */
    /* When is-dark is set, force ALL dark theme variables */
    :root.is-dark {
      --main-color: rgb(249 249 250) !important;
      --body-bg-color: rgb(42 42 46) !important;
      --progressBar-color: rgb(0 96 223) !important;
      --progressBar-bg-color: rgb(40 40 43) !important;
      --progressBar-blend-color: rgb(20 68 133) !important;
      --scrollbar-color: rgb(121 121 123) !important;
      --scrollbar-bg-color: rgb(35 35 39) !important;
      --toolbar-icon-bg-color: rgb(255 255 255) !important;
      --toolbar-icon-hover-bg-color: rgb(255 255 255) !important;
      --sidebar-narrow-bg-color: rgb(42 42 46 / 0.9) !important;
      --sidebar-toolbar-bg-color: rgb(50 50 52) !important;
      --toolbar-bg-color: rgb(56 56 61) !important;
      --toolbar-border-color: rgb(12 12 13) !important;
      --button-hover-color: rgb(102 102 103) !important;
      --toggled-btn-color: rgb(255 255 255) !important;
      --toggled-btn-bg-color: rgb(0 0 0 / 0.3) !important;
      --toggled-hover-active-btn-color: rgb(0 0 0 / 0.4) !important;
      --dropdown-btn-bg-color: rgb(74 74 79) !important;
      --separator-color: rgb(0 0 0 / 0.3) !important;
      --field-color: rgb(250 250 250) !important;
      --field-bg-color: rgb(64 64 68) !important;
      --field-border-color: rgb(115 115 115) !important;
      --treeitem-color: rgb(255 255 255 / 0.8) !important;
      --treeitem-bg-color: rgb(255 255 255 / 0.15) !important;
      --treeitem-hover-color: rgb(255 255 255 / 0.9) !important;
      --treeitem-selected-color: rgb(255 255 255 / 0.9) !important;
      --treeitem-selected-bg-color: rgb(255 255 255 / 0.25) !important;
      --thumbnail-hover-color: rgb(255 255 255 / 0.1) !important;
      --thumbnail-selected-color: rgb(255 255 255 / 0.2) !important;
      --doorhanger-bg-color: rgb(74 74 79) !important;
      --doorhanger-border-color: rgb(39 39 43) !important;
      --doorhanger-hover-color: rgb(249 249 250) !important;
      --doorhanger-hover-bg-color: rgb(93 94 98) !important;
      --doorhanger-separator-color: rgb(92 92 97) !important;
      --dialog-button-bg-color: rgb(92 92 97) !important;
      --dialog-button-hover-bg-color: rgb(115 115 115) !important;
    }
    /* When is-light is set, force ALL light theme variables */
    :root.is-light {
      --main-color: rgb(12 12 13) !important;
      --body-bg-color: rgb(212 212 215) !important;
      --progressBar-color: rgb(10 132 255) !important;
      --progressBar-bg-color: rgb(221 221 222) !important;
      --progressBar-blend-color: rgb(116 177 239) !important;
      --scrollbar-color: auto !important;
      --scrollbar-bg-color: auto !important;
      --toolbar-icon-bg-color: rgb(0 0 0) !important;
      --toolbar-icon-hover-bg-color: rgb(0 0 0) !important;
      --sidebar-narrow-bg-color: rgb(212 212 215 / 0.9) !important;
      --sidebar-toolbar-bg-color: rgb(245 246 247) !important;
      --toolbar-bg-color: rgb(249 249 250) !important;
      --toolbar-border-color: rgb(184 184 184) !important;
      --button-hover-color: rgb(221 222 223) !important;
      --toggled-btn-color: rgb(0 0 0) !important;
      --toggled-btn-bg-color: rgb(0 0 0 / 0.3) !important;
      --toggled-hover-active-btn-color: rgb(0 0 0 / 0.4) !important;
      --dropdown-btn-bg-color: rgb(215 215 219) !important;
      --separator-color: rgb(0 0 0 / 0.3) !important;
      --field-color: rgb(12 12 13) !important;
      --field-bg-color: rgb(255 255 255) !important;
      --field-border-color: rgb(115 115 115) !important;
      --treeitem-color: rgb(0 0 0 / 0.8) !important;
      --treeitem-bg-color: rgb(0 0 0 / 0.15) !important;
      --treeitem-hover-color: rgb(0 0 0 / 0.9) !important;
      --treeitem-selected-color: rgb(0 0 0 / 0.9) !important;
      --treeitem-selected-bg-color: rgb(0 0 0 / 0.25) !important;
      --thumbnail-hover-color: rgb(0 0 0 / 0.1) !important;
      --thumbnail-selected-color: rgb(0 0 0 / 0.2) !important;
      --doorhanger-bg-color: rgb(255 255 255) !important;
      --doorhanger-border-color: rgb(184 184 184) !important;
      --doorhanger-hover-color: rgb(12 12 13) !important;
      --doorhanger-hover-bg-color: rgb(221 222 223) !important;
      --doorhanger-separator-color: rgb(184 184 184) !important;
      --dialog-button-bg-color: rgb(215 215 219) !important;
      --dialog-button-hover-bg-color: rgb(184 184 184) !important;
    }
    /* Disable PDF.js media query - force it to use our classes only */
    @media (prefers-color-scheme: dark) {
      :root.is-light {
        /* When is-light is set, ignore system dark preference */
        --main-color: rgb(12 12 13) !important;
        --body-bg-color: rgb(212 212 215) !important;
      }
    }
    @media (prefers-color-scheme: light) {
      :root.is-dark {
        /* When is-dark is set, ignore system light preference */
        --main-color: rgb(249 249 250) !important;
        --body-bg-color: rgb(42 42 46) !important;
      }
    }
    /* CRITICAL: Keep PDF document pages in ORIGINAL colors by default - NO inversion */
    /* PDF page theme inversion is controlled by user setting, applied via JavaScript */
    /* This CSS ensures no default inversion happens */
    body .pdfViewer .page canvas,
    body .pdfViewer .page img,
    body .pdfViewer .page svg,
    body .thumbnailView canvas,
    body .thumbnailView img,
    body .thumbnail canvas,
    body .thumbnail img,
    body .thumbnailImage canvas,
    body .thumbnailImage img,
    body #thumbnailView canvas,
    body #thumbnailView img,
    .pdfViewer .page canvas,
    .pdfViewer .page img,
    .pdfViewer .page svg,
    .pdfViewer .page,
    .thumbnailView canvas,
    .thumbnailView img {
      filter: none !important;
      -webkit-filter: none !important;
    }
    /* Ensure no parent elements are applying filters by default */
    body[data-dark-mode="true"] .pdfViewer .page,
    body[data-dark-mode="false"] .pdfViewer .page,
    body[data-dark-mode="true"] .pdfViewer .page canvas,
    body[data-dark-mode="false"] .pdfViewer .page canvas,
    .pdfViewer .page {
      filter: none !important;
      -webkit-filter: none !important;
    }
    
    /* ============================================
       SIDEBAR-STYLE MATCHING: Modern macOS Aesthetic
       ============================================ */
    
    /* Typography - Match sidebar font */
    body, .toolbar, .toolbarButton, .secondaryToolbarButton,
    .doorhanger, .doorHanger, .dialog, .dialogContainer, .findbar, #sidebarContainer,
    .treeItem, .treeItemContent, input, select, textarea, .toolbarField {
      font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Segoe UI', Roboto, sans-serif !important;
      -webkit-font-smoothing: antialiased !important;
      -moz-osx-font-smoothing: grayscale !important;
    }
    
    /* Toolbar - Modern shadows */
    .toolbar {
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1) !important;
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
    }
    
    /* Toolbar buttons - Rounded corners, smooth transitions */
    .toolbarButton, .secondaryToolbarButton {
      border-radius: 8px !important;
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
      margin: 2px 1px !important;
    }
    
    .toolbarButton:hover, .secondaryToolbarButton:hover {
      transform: translateY(-1px) !important;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15) !important;
    }
    
    .toolbarButton:active, .secondaryToolbarButton:active {
      transform: translateY(0) !important;
      transition: all 0.1s ease !important;
    }
    
    /* Split toolbar buttons */
    .splitToolbarButton > .toolbarButton {
      border-radius: 8px !important;
    }
    
    /* Dropdowns and doorhangers - Rounded corners, modern shadows */
    .doorhanger, .doorHanger {
      border-radius: 10px !important;
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2) !important;
      backdrop-filter: blur(20px) !important;
      -webkit-backdrop-filter: blur(20px) !important;
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
      border: 1px solid rgba(255, 255, 255, 0.1) !important;
    }
    
    .doorhanger::before {
      border-radius: 10px !important;
    }
    
    /* Doorhanger menu items */
    .doorhanger > .doorhangerItem, .doorHanger > .doorhangerItem {
      border-radius: 6px !important;
      margin: 2px 4px !important;
      transition: all 0.15s cubic-bezier(0.4, 0, 0.2, 1) !important;
    }
    
    .doorhanger > .doorhangerItem:hover, .doorHanger > .doorhangerItem:hover {
      transform: translateX(2px) !important;
    }
    
    /* Dialogs - Rounded corners, modern styling */
    .dialog, .dialogContainer {
      border-radius: 12px !important;
      box-shadow: 0 12px 48px rgba(0, 0, 0, 0.3) !important;
      backdrop-filter: blur(20px) !important;
      -webkit-backdrop-filter: blur(20px) !important;
      transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) !important;
    }
    
    /* Dialog buttons */
    .dialogButton {
      border-radius: 8px !important;
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
      font-weight: 500 !important;
    }
    
    .dialogButton:hover {
      transform: translateY(-1px) !important;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15) !important;
    }
    
    /* Input fields - Rounded corners */
    .toolbarField, input[type="text"], input[type="number"],
    select, textarea {
      border-radius: 8px !important;
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
      border: 1px solid var(--field-border-color) !important;
    }
    
    .toolbarField:focus, input:focus, select:focus, textarea:focus {
      outline: none !important;
      box-shadow: 0 0 0 3px rgba(139, 92, 246, 0.2) !important;
      border-color: rgba(139, 92, 246, 0.5) !important;
    }
    
    /* Findbar - Rounded corners */
    .findbar {
      border-radius: 0 0 10px 10px !important;
    }
    
    /* Sidebar - Match sidebar styling */
    #sidebarContainer {
      box-shadow: inset -1px 0 0 rgba(0, 0, 0, 0.1) !important;
    }
    
    /* Tree items - Rounded corners, smooth transitions */
    .treeItem, .treeItemContent {
      border-radius: 6px !important;
      transition: all 0.15s cubic-bezier(0.4, 0, 0.2, 1) !important;
      margin: 1px 4px !important;
    }
    
    .treeItem:hover {
      transform: translateX(2px) !important;
    }
    
    /* Thumbnails - Rounded corners */
    .thumbnail {
      border-radius: 8px !important;
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1) !important;
    }
    
    .thumbnail:hover {
      transform: translateY(-2px) scale(1.02) !important;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.2) !important;
    }
    
    .thumbnail.selected {
      box-shadow: 0 0 0 2px rgba(139, 92, 246, 0.5), 0 4px 16px rgba(0, 0, 0, 0.2) !important;
    }
    
    /* Progress bar - Rounded */
    #loadingBar, .progressBar {
      border-radius: 4px !important;
    }
    
    /* Scrollbars - Match sidebar custom scrollbars */
    ::-webkit-scrollbar {
      width: 10px !important;
      height: 10px !important;
    }
    
    ::-webkit-scrollbar-track {
      background: var(--scrollbar-bg-color) !important;
      border-radius: 5px !important;
    }
    
    ::-webkit-scrollbar-thumb {
      background: var(--scrollbar-color) !important;
      border-radius: 5px !important;
      border: 2px solid var(--scrollbar-bg-color) !important;
      transition: background 0.2s ease !important;
    }
    
    ::-webkit-scrollbar-thumb:hover {
      background: var(--button-hover-color) !important;
    }
    
    /* Light mode scrollbar adjustments */
    :root.is-light ::-webkit-scrollbar-thumb {
      background: rgba(0, 0, 0, 0.2) !important;
    }
    
    :root.is-light ::-webkit-scrollbar-thumb:hover {
      background: rgba(0, 0, 0, 0.3) !important;
    }
    
    /* Dark mode scrollbar adjustments */
    :root.is-dark ::-webkit-scrollbar-thumb {
      background: rgba(255, 255, 255, 0.2) !important;
    }
    
    :root.is-dark ::-webkit-scrollbar-thumb:hover {
      background: rgba(255, 255, 255, 0.3) !important;
    }
    
    /* Separators - Subtle styling */
    .verticalToolbarSeparator, .horizontalToolbarSeparator {
      opacity: 0.3 !important;
      transition: opacity 0.2s ease !important;
    }
    
    /* Secondary toolbar - Rounded corners */
    #secondaryToolbar {
      border-radius: 0 0 10px 10px !important;
    }
    
    /* Annotation editor toolbar */
    .editorParamsToolbar {
      border-radius: 8px !important;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.15) !important;
    }
    
    /* Color picker - Ensure consistency */
    .colorPicker {
      border-radius: 10px !important;
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2) !important;
    }
    
    /* Page rotation indicator */
    .page {
      transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
    }
    
    /* Smooth transitions for all interactive elements */
    button, .toolbarButton, .secondaryToolbarButton,
    .doorhangerItem, .treeItem, .thumbnail,
    input, select, textarea {
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
    }
    
    /* Focus states - macOS style */
    *:focus-visible {
      outline: 2px solid rgba(139, 92, 246, 0.5) !important;
      outline-offset: 2px !important;
      border-radius: 4px !important;
    }
    
    /* Disabled states */
    .toolbarButton[disabled], .secondaryToolbarButton[disabled],
    button[disabled] {
      opacity: 0.5 !important;
      cursor: not-allowed !important;
    }
    
    /* Toggled/active states */
    .toolbarButton.toggled, .secondaryToolbarButton.toggled {
      box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.1) !important;
    }
  </style>'''
            
            # Replace existing CSS if present, otherwise add it
            import re
            # Remove any existing custom style blocks (including our sidebar matching styles)
            content = re.sub(r'<style>.*?SIDEBAR-STYLE MATCHING.*?</style>', '', content, flags=re.DOTALL)
            content = re.sub(r'<style>.*?themeIconPdf.*?</style>', '', content, flags=re.DOTALL)
            
            # CRITICAL: Inject CSS AFTER viewer.css link so it loads after and can override
            # This ensures our styles take precedence over viewer.css
            if '<link rel="stylesheet" href="viewer.css">' in content:
                # Inject right after viewer.css link tag
                content = content.replace(
                    '<link rel="stylesheet" href="viewer.css">',
                    '<link rel="stylesheet" href="viewer.css">\n' + css_override
                )
            elif '</head>' in content:
                # Fallback: add before closing head tag
                content = content.replace('</head>', css_override + '\n</head>')
            
            # Replace entire script block if it exists - remove ALL old theme scripts
            if 'Theme toggle functionality' in content or 'initThemeToggle' in content or 'CRITICAL: Override matchMedia' in content:
                # Remove old script - match any script containing theme toggle code
                old_script_pattern = r'<script>[\s\S]*?// Theme toggle functionality[\s\S]*?</script>'
                content = re.sub(old_script_pattern, '', content, flags=re.DOTALL)
                # Also remove any script with initThemeToggle
                old_script_pattern2 = r'<script>[\s\S]*?function initThemeToggle[\s\S]*?</script>'
                content = re.sub(old_script_pattern2, '', content, flags=re.DOTALL)
                # Also remove matchMedia override script (CRITICAL section)
                old_script_pattern3 = r'<script>[\s\S]*?// CRITICAL: Override matchMedia[\s\S]*?window\.updateMatchMediaTheme[\s\S]*?</script>'
                content = re.sub(old_script_pattern3, '', content, flags=re.DOTALL)
            
            # Add new script before closing body
            if '</body>' in content:
                theme_script = '''
  <script>
    // CRITICAL: Override matchMedia IMMEDIATELY before PDF.js loads
    // This must happen before any PDF.js code runs
    (function() {
      const originalMatchMedia = window.matchMedia;
      let currentThemeOverride = '{initial_theme}'; // Use detected system theme
      
      window.matchMedia = function(query) {
        if (query === '(prefers-color-scheme: dark)') {
          // Return a mock that matches our current theme override
          return {
            matches: currentThemeOverride === 'dark',
            media: query,
            onchange: null,
            addListener: function() {},
            removeListener: function() {},
            addEventListener: function() {},
            removeEventListener: function() {},
            dispatchEvent: function() { return true; }
          };
        }
        return originalMatchMedia.call(window, query);
      };
      
      // Expose function to update theme override
      window.updateMatchMediaTheme = function(theme) {
        currentThemeOverride = theme;
      };
    })();
    
    // Theme toggle functionality for PDF viewer
    (function() {
      function initThemeToggle() {
        const themeBtn = document.getElementById('themeTogglePdf');
        const themeIcon = document.getElementById('themeIconPdf');
        if (!themeBtn || !themeIcon) {
          setTimeout(initThemeToggle, 100);
          return;
        }
        
        let currentTheme = 'dark';
        
        // Function to apply theme to PDF.js viewer
        function applyPdfTheme(theme) {
          const root = document.documentElement;
          const body = document.body;
          
          console.log('PDF viewer applying theme:', theme);
          
          // Store theme globally AND update matchMedia override
          window.pdfViewerTheme = theme;
          if (window.updateMatchMediaTheme) {
            window.updateMatchMediaTheme(theme);
          }
          
          // Force remove both classes first - do this synchronously
          root.classList.remove('is-dark', 'is-light');
          root.removeAttribute('data-theme');
          
          if (theme === 'dark') {
            // Dark mode: add is-dark class
            root.classList.add('is-dark');
            root.setAttribute('data-theme', 'dark');
            body.setAttribute('data-dark-mode', 'true');
            body.classList.add('is-dark');
            body.classList.remove('is-light');
            console.log('PDF viewer: Added is-dark class');
          } else {
            // Light mode: add is-light class  
            root.classList.add('is-light');
            root.setAttribute('data-theme', 'light');
            body.setAttribute('data-dark-mode', 'false');
            body.classList.add('is-light');
            body.classList.remove('is-dark');
            console.log('PDF viewer: Added is-light class');
          }
          
          // Force multiple reflows to ensure CSS applies
          void root.offsetHeight;
          void body.offsetHeight;
          
          // Also directly set CSS variables AND body background as ultimate override
          if (theme === 'dark') {
            root.style.setProperty('--body-bg-color', 'rgb(42 42 46)', 'important');
            root.style.setProperty('--toolbar-bg-color', 'rgb(56 56 61)', 'important');
            root.style.setProperty('--main-color', 'rgb(249 249 250)', 'important');
            body.style.setProperty('background-color', 'rgb(42 42 46)', 'important');
          } else {
            root.style.setProperty('--body-bg-color', 'rgb(212 212 215)', 'important');
            root.style.setProperty('--toolbar-bg-color', 'rgb(249 249 250)', 'important');
            root.style.setProperty('--main-color', 'rgb(12 12 13)', 'important');
            body.style.setProperty('background-color', 'rgb(212 212 215)', 'important');
          }
          
          // Force another reflow after setting CSS variables
          void root.offsetHeight;
          
          // Watch for any attempts to change body background and revert them
          if (!window.pdfThemeProtectionObserver) {
            window.pdfThemeProtectionObserver = new MutationObserver((mutations) => {
              mutations.forEach((mutation) => {
                if (mutation.type === 'attributes' && mutation.attributeName === 'style') {
                  const target = mutation.target;
                  if (target === body || target === root) {
                    const bgColor = window.getComputedStyle(body).backgroundColor;
                    const expectedDark = 'rgb(42, 42, 46)';
                    const expectedLight = 'rgb(212, 212, 215)';
                    const isDark = window.pdfViewerTheme === 'dark';
                    const expected = isDark ? expectedDark : expectedLight;
                    
                    // If background was changed to wrong color, fix it
                    if (bgColor !== expected && bgColor !== 'rgba(0, 0, 0, 0)') {
                      console.log('PDF viewer: Detected background color change, fixing:', bgColor, 'to', expected);
                      body.style.setProperty('background-color', isDark ? 'rgb(42 42 46)' : 'rgb(212 212 215)', 'important');
                    }
                  }
                }
              });
            });
            window.pdfThemeProtectionObserver.observe(body, {
              attributes: true,
              attributeFilter: ['style', 'class']
            });
            window.pdfThemeProtectionObserver.observe(root, {
              attributes: true,
              attributeFilter: ['style', 'class']
            });
          }
        }
        
        let pdfPageThemeEnabled = false;
        
        // Function to remove ALL filters from PDF elements
        const removeAllFilters = () => {
          // Remove from canvases, images, SVGs
          const elements = document.querySelectorAll('.pdfViewer .page canvas, .pdfViewer .page img, .pdfViewer .page svg, .thumbnailView canvas, .thumbnailView img, .thumbnail canvas, .thumbnail img, .pdfViewer .page');
          elements.forEach(el => {
            // Remove inline styles
            if (el.style.filter && el.style.filter !== 'none') {
              el.style.filter = 'none';
              el.style.removeProperty('filter');
            }
            if (el.style.webkitFilter && el.style.webkitFilter !== 'none') {
              el.style.webkitFilter = 'none';
              el.style.removeProperty('-webkit-filter');
            }
            // Remove computed filters by setting style directly
            el.style.setProperty('filter', 'none', 'important');
            el.style.setProperty('-webkit-filter', 'none', 'important');
          });
          
          // Also remove filters from parent containers
          const containers = document.querySelectorAll('.pdfViewer .page, body[data-dark-mode]');
          containers.forEach(el => {
            const computed = window.getComputedStyle(el);
            if (computed.filter && computed.filter !== 'none') {
              el.style.setProperty('filter', 'none', 'important');
            }
          });
        };
        
        // Function to apply PDF page inversion
        function applyPdfPageInversion(enabled) {
          const styleId = 'pdfPageThemeStyle';
          let styleEl = document.getElementById(styleId);
          
          console.log('PDF viewer applyPdfPageInversion called with enabled:', enabled);
          
          // Store the enabled state globally
          window.pdfPageThemeEnabled = enabled;
          
          // ALWAYS remove the style element first
          if (styleEl) {
            styleEl.remove();
          }
          
          if (enabled) {
            // Apply inversion filter to PDF pages ONLY if explicitly enabled
            styleEl = document.createElement('style');
            styleEl.id = styleId;
            document.head.appendChild(styleEl);
            styleEl.textContent = `
              body .pdfViewer .page canvas,
              body .pdfViewer .page img,
              body .pdfViewer .page svg,
              body .thumbnailView canvas,
              body .thumbnailView img,
              body .thumbnail canvas,
              body .thumbnail img {
                filter: invert(1) hue-rotate(180deg) !important;
                -webkit-filter: invert(1) hue-rotate(180deg) !important;
              }
            `;
          } else {
            // DISABLED: Remove ALL filters aggressively - this is the default state
            // Remove filters immediately and continuously
            removeAllFilters();
            setTimeout(removeAllFilters, 10);
            setTimeout(removeAllFilters, 50);
            setTimeout(removeAllFilters, 100);
            setTimeout(removeAllFilters, 200);
            setTimeout(removeAllFilters, 500);
            setTimeout(removeAllFilters, 1000);
            setTimeout(removeAllFilters, 2000);
            
            // Set up a MutationObserver to ALWAYS remove filters when disabled
            if (!window.pdfInversionObserver) {
              window.pdfInversionObserver = new MutationObserver((mutations) => {
                // ALWAYS remove filters if inversion is disabled
                if (!window.pdfPageThemeEnabled) {
                  removeAllFilters();
                }
              });
              window.pdfInversionObserver.observe(document.body, {
                childList: true,
                subtree: true,
                attributes: true,
                attributeFilter: ['style', 'class']
              });
            }
          }
        }
        
        // CRITICAL: Start with NO inversion - call this immediately
        applyPdfPageInversion(false);
        
        // Intercept canvas filter application - override CanvasRenderingContext2D.filter setter
        if (!window.canvasFilterIntercepted) {
          window.canvasFilterIntercepted = true;
          const originalCanvasFilter = Object.getOwnPropertyDescriptor(CanvasRenderingContext2D.prototype, 'filter');
          if (originalCanvasFilter && originalCanvasFilter.set) {
            Object.defineProperty(CanvasRenderingContext2D.prototype, 'filter', {
              set: function(value) {
                // Only allow filter if inversion is explicitly enabled
                if (!window.pdfPageThemeEnabled && value && value !== 'none') {
                  // Block filter application
                  return;
                }
                originalCanvasFilter.set.call(this, value);
              },
              get: originalCanvasFilter.get,
              configurable: true
            });
          }
        }
        
        // Function to apply theme colors from parent
        function applyThemeColors(colors) {
          const root = document.documentElement;
          if (!colors) return;
          
          // Apply all PDF viewer CSS variables (using --pdf- prefix to avoid conflicts)
          root.style.setProperty('--pdf-body-bg-color', colors.bodyBg, 'important');
          root.style.setProperty('--pdf-toolbar-bg-color', colors.toolbarBg, 'important');
          root.style.setProperty('--pdf-sidebar-bg-color', colors.sidebarBg, 'important');
          root.style.setProperty('--pdf-main-color', colors.mainColor, 'important');
          root.style.setProperty('--pdf-button-hover-color', colors.buttonHover, 'important');
          root.style.setProperty('--pdf-field-bg-color', colors.fieldBg, 'important');
          root.style.setProperty('--pdf-field-border-color', colors.fieldBorder, 'important');
          root.style.setProperty('--pdf-treeitem-bg-color', colors.treeitemBg, 'important');
          root.style.setProperty('--pdf-treeitem-hover-bg-color', colors.treeitemHover, 'important');
          root.style.setProperty('--pdf-doorhanger-bg-color', colors.doorhangerBg, 'important');
          root.style.setProperty('--pdf-dialog-bg-color', colors.dialogBg, 'important');
          
          // Also update body background directly
          document.body.style.setProperty('background-color', colors.bodyBg, 'important');
          
          // Apply colors to PDF.js elements using CSS variables
          // Override PDF.js CSS variables with our theme colors
          root.style.setProperty('--body-bg-color', colors.bodyBg, 'important');
          root.style.setProperty('--toolbar-bg-color', colors.toolbarBg, 'important');
          root.style.setProperty('--sidebar-toolbar-bg-color', colors.sidebarBg, 'important');
          root.style.setProperty('--main-color', colors.mainColor, 'important');
          root.style.setProperty('--button-hover-color', colors.buttonHover, 'important');
          root.style.setProperty('--field-bg-color', colors.fieldBg, 'important');
          root.style.setProperty('--field-border-color', colors.fieldBorder, 'important');
          root.style.setProperty('--dropdown-btn-bg-color', colors.fieldBg, 'important');
          root.style.setProperty('--treeitem-bg-color', colors.treeitemBg, 'important');
          root.style.setProperty('--treeitem-hover-bg-color', colors.treeitemHover, 'important');
          root.style.setProperty('--doorhanger-bg-color', colors.doorhangerBg, 'important');
          root.style.setProperty('--dialog-bg-color', colors.dialogBg, 'important');
          
          // Also directly set on elements for immediate effect
          const toolbar = document.querySelector('.toolbar');
          if (toolbar) {
            toolbar.style.setProperty('background-color', colors.toolbarBg, 'important');
          }
          
          const sidebarContainer = document.getElementById('sidebarContainer');
          if (sidebarContainer) {
            sidebarContainer.style.setProperty('background-color', colors.sidebarBg, 'important');
          }
          
          // Update toolbar fields
          const fields = document.querySelectorAll('.toolbarField, input[type="text"], input[type="number"], select, textarea, .dropdownToolbarButton > select');
          fields.forEach(field => {
            field.style.setProperty('background-color', colors.fieldBg, 'important');
            field.style.setProperty('border-color', colors.fieldBorder, 'important');
            field.style.setProperty('color', colors.mainColor, 'important');
          });
          
          // Update dropdown toolbar buttons
          const dropdownButtons = document.querySelectorAll('.dropdownToolbarButton');
          dropdownButtons.forEach(btn => {
            btn.style.setProperty('background-color', colors.fieldBg, 'important');
          });
          
          // Update tree items
          const treeItems = document.querySelectorAll('.treeItem, .treeItemContent');
          treeItems.forEach(item => {
            item.style.setProperty('background-color', colors.treeitemBg, 'important');
            item.style.setProperty('color', colors.mainColor, 'important');
          });
          
          // Update thumbnail toolbar (sidebar toolbar with thumbnail/outline buttons)
          const toolbarSidebar = document.querySelector('#toolbarSidebar');
          if (toolbarSidebar) {
            toolbarSidebar.style.setProperty('background-color', colors.sidebarBg, 'important');
          }
          
          // Update thumbnail view container background (the area around thumbnails)
          const thumbnailView = document.querySelector('#thumbnailView');
          if (thumbnailView) {
            thumbnailView.style.setProperty('background-color', colors.bodyBg, 'important');
          }
          
          // Update sidebar content area
          const sidebarContent = document.querySelector('#sidebarContent');
          if (sidebarContent) {
            sidebarContent.style.setProperty('background-color', colors.bodyBg, 'important');
          }
          
          // Update all toolbar containers (including secondary toolbar)
          const allToolbars = document.querySelectorAll('#toolbarContainer, #secondaryToolbar');
          allToolbars.forEach(toolbar => {
            toolbar.style.setProperty('background-color', colors.toolbarBg, 'important');
          });
          
          // Update main container background (PDF background area)
          const mainContainer = document.querySelector('#mainContainer');
          if (mainContainer) {
            mainContainer.style.setProperty('background-color', colors.bodyBg, 'important');
          }
          
          // Update outer container background
          const outerContainer = document.querySelector('#outerContainer');
          if (outerContainer) {
            outerContainer.style.setProperty('background-color', colors.bodyBg, 'important');
          }
        }
        
        // Set initial theme based on parent message or system preference
        window.addEventListener('message', (e) => {
          if (e.data && e.data.type === 'theme-changed') {
            currentTheme = e.data.theme || 'dark';
            if (e.data.pdfPageTheme !== undefined) {
              pdfPageThemeEnabled = e.data.pdfPageTheme;
            }
            console.log('PDF viewer received theme:', currentTheme, 'pdfPageTheme:', pdfPageThemeEnabled);
            // Update matchMedia override to reflect new theme BEFORE applying theme
            // This prevents PDF.js from applying wrong theme based on old matchMedia value
            if (window.updateMatchMediaTheme) {
              window.updateMatchMediaTheme(currentTheme);
            }
            window.pdfViewerTheme = currentTheme;
            applyPdfTheme(currentTheme);
            applyPdfPageInversion(pdfPageThemeEnabled);
            updateIcon();
          } else if (e.data && e.data.type === 'pdf-page-theme-changed') {
            pdfPageThemeEnabled = e.data.enabled || false;
            currentTheme = e.data.theme || currentTheme;
            console.log('PDF viewer received pdf-page-theme-changed:', pdfPageThemeEnabled);
            applyPdfPageInversion(pdfPageThemeEnabled);
          } else if (e.data && e.data.type === 'theme-colors-changed') {
            console.log('PDF viewer received theme colors:', e.data.colors);
            applyThemeColors(e.data.colors);
            // Also re-apply theme to ensure consistency
            if (e.data.mode) {
              currentTheme = e.data.mode === 'light' ? 'light' : 'dark';
              applyPdfTheme(currentTheme);
            }
          }
        });
        
        // Initial theme setup - wait for parent to send theme
        // Request theme from parent (but not too many times to avoid conflicts)
        if (window.parent && window.parent !== window) {
          // Request immediately
          window.parent.postMessage({ type: 'get-theme' }, '*');
          // Also request after a delay in case parent isn't ready
          setTimeout(() => {
            window.parent.postMessage({ type: 'get-theme' }, '*');
          }, 500);
        }
        
        // Also listen for PDF.js initialization and re-apply theme
        if (window.PDFViewerApplication) {
          window.PDFViewerApplication.initializedPromise.then(() => {
            // PDF.js is ready, ensure theme is applied and filters are removed
            if (window.pdfViewerTheme) {
              applyPdfTheme(window.pdfViewerTheme);
            }
            if (window.pdfPageThemeEnabled !== undefined) {
              applyPdfPageInversion(window.pdfPageThemeEnabled);
            } else {
              applyPdfPageInversion(false); // Default to no inversion
            }
          });
        } else {
          // Wait for PDF.js to be available
          const checkPdfJs = setInterval(() => {
            if (window.PDFViewerApplication) {
              clearInterval(checkPdfJs);
              window.PDFViewerApplication.initializedPromise.then(() => {
                if (window.pdfViewerTheme) {
                  applyPdfTheme(window.pdfViewerTheme);
                }
                if (window.pdfPageThemeEnabled !== undefined) {
                  applyPdfPageInversion(window.pdfPageThemeEnabled);
                } else {
                  applyPdfPageInversion(false); // Default to no inversion
                }
              });
            }
          }, 100);
          // Stop checking after 10 seconds
          setTimeout(() => clearInterval(checkPdfJs), 10000);
        }
        
        function updateIcon() {
          if (themeIcon && themeBtn) {
            // Animate icon change - smooth rotation animation like overlay button
            themeIcon.style.transform = 'rotate(180deg) scale(0.8)';
            themeIcon.style.opacity = '0.5';
            setTimeout(() => {
              themeIcon.textContent = currentTheme === 'light' ? '☀️' : '🌙';
              // Set data attribute for CSS targeting
              themeBtn.setAttribute('data-theme', currentTheme);
              themeIcon.style.transform = 'rotate(360deg) scale(1)';
              setTimeout(() => {
                themeIcon.style.transform = 'rotate(0deg) scale(1)';
                themeIcon.style.opacity = '1';
              }, 50);
            }, 200);
          }
        }
        
        themeBtn.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          if (window.parent && window.parent !== window) {
            window.parent.postMessage({ type: 'toggle-theme' }, '*');
          }
        });
        
        updateIcon();
      }
      
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initThemeToggle);
      } else {
        setTimeout(initThemeToggle, 500);
      }
    })();
  </script>'''
                # Replace {initial_theme} placeholder with actual theme
                # Use .replace() instead of .format() to avoid issues with CSS curly braces
                theme_script = theme_script.replace('{initial_theme}', initial_theme)
                if theme_script not in content:
                    content = content.replace('</body>', theme_script + '\n</body>')
            
            # Write updated content
            viewer_html_path.write_text(content, encoding="utf-8")
            print("✓ Theme toggle button updated with animation")
            return
        
        # Add theme toggle button after download button - match PDF.js toolbar button style
        button_html = '''                <button id="themeTogglePdf" class="toolbarButton" title="Toggle Light/Dark Mode" tabindex="34">
                  <span id="themeIconPdf">🌙</span>
                </button>

                <div class="verticalToolbarSeparator"></div>
'''
        
        # Find the download button and add theme button after it
        old_pattern = '                <button id="download" class="toolbarButton hiddenMediumView" title="Save" tabindex="33" data-l10n-id="save">\n                  <span data-l10n-id="save_label">Save</span>\n                </button>\n\n                <div class="verticalToolbarSeparator hiddenMediumView"></div>'
        new_pattern = '                <button id="download" class="toolbarButton hiddenMediumView" title="Save" tabindex="33" data-l10n-id="save">\n                  <span data-l10n-id="save_label">Save</span>\n                </button>\n\n' + button_html
        
        if old_pattern in content:
            content = content.replace(old_pattern, new_pattern)
        else:
            # Try alternative pattern (without hiddenMediumView)
            alt_pattern = '                <button id="download" class="toolbarButton" title="Save" tabindex="33" data-l10n-id="save">\n                  <span data-l10n-id="save_label">Save</span>\n                </button>\n\n                <div class="verticalToolbarSeparator"></div>'
            if alt_pattern in content:
                content = content.replace(alt_pattern, alt_pattern.replace('</button>\n\n                <div', '</button>\n\n' + button_html.rstrip() + '                <div'))
            else:
                # Fallback: add before secondaryToolbarToggle
                if '<button id="secondaryToolbarToggle"' in content:
                    content = content.replace('<button id="secondaryToolbarToggle"', button_html + '                <button id="secondaryToolbarToggle"')
                else:
                    print("Could not find insertion point for theme toggle button")
                    return
        
        # Add CSS override to make icon visible (PDF.js hides span by default), white moon, black sun, smaller moon
        # Also add CSS to keep PDF canvas/images in original colors
        css_override = '''
  <style>
    #themeTogglePdf {
      display: flex !important;
      align-items: center !important;
      justify-content: center !important;
      position: relative !important;
    }
    #themeTogglePdf::before {
      display: none !important;
    }
    #themeTogglePdf > #themeIconPdf,
    #themeTogglePdf span#themeIconPdf,
    button#themeTogglePdf > span#themeIconPdf {
      display: inline-block !important;
      width: auto !important;
      height: auto !important;
      overflow: visible !important;
      font-size: 13px !important;
      line-height: 1 !important;
      transition: transform 0.4s cubic-bezier(0.68, -0.55, 0.27, 1.55), opacity 0.3s ease, filter 0.3s ease;
      margin: 0 !important;
      padding: 0 !important;
      text-align: center !important;
      transform-origin: center center;
      opacity: 0.85;
    }
    /* Moon (crescent) - make it light/white for dark mode */
    button#themeTogglePdf[data-theme="dark"] > #themeIconPdf,
    button#themeTogglePdf[data-theme="dark"] > span#themeIconPdf {
      filter: grayscale(1) brightness(2) contrast(1.2);
    }
    /* Sun - make it dark gray */
    button#themeTogglePdf[data-theme="light"] > #themeIconPdf,
    button#themeTogglePdf[data-theme="light"] > span#themeIconPdf {
      filter: grayscale(1) brightness(0.4) contrast(1.3);
    }
    #themeTogglePdf:hover > #themeIconPdf,
    #themeTogglePdf:hover span#themeIconPdf,
    button#themeTogglePdf:hover > span#themeIconPdf {
      opacity: 1;
    }
    /* Hover state for moon - keep it light */
    button#themeTogglePdf[data-theme="dark"]:hover > #themeIconPdf,
    button#themeTogglePdf[data-theme="dark"]:hover > span#themeIconPdf {
      filter: grayscale(1) brightness(2.2) contrast(1.2);
    }
    /* Hover state for sun - slightly brighter but still dark gray */
    button#themeTogglePdf[data-theme="light"]:hover > #themeIconPdf,
    button#themeTogglePdf[data-theme="light"]:hover > span#themeIconPdf {
      filter: grayscale(1) brightness(0.5) contrast(1.3);
    }
    /* Override PDF.js automatic theme detection - force it to respect app theme */
    /* PDF.js uses @media (prefers-color-scheme: dark) - we completely override it */
    /* When is-dark is set, force ALL dark theme variables */
    :root.is-dark {
      --main-color: rgb(249 249 250) !important;
      --body-bg-color: rgb(42 42 46) !important;
      --progressBar-color: rgb(0 96 223) !important;
      --progressBar-bg-color: rgb(40 40 43) !important;
      --progressBar-blend-color: rgb(20 68 133) !important;
      --scrollbar-color: rgb(121 121 123) !important;
      --scrollbar-bg-color: rgb(35 35 39) !important;
      --toolbar-icon-bg-color: rgb(255 255 255) !important;
      --toolbar-icon-hover-bg-color: rgb(255 255 255) !important;
      --sidebar-narrow-bg-color: rgb(42 42 46 / 0.9) !important;
      --sidebar-toolbar-bg-color: rgb(50 50 52) !important;
      --toolbar-bg-color: rgb(56 56 61) !important;
      --toolbar-border-color: rgb(12 12 13) !important;
      --button-hover-color: rgb(102 102 103) !important;
      --toggled-btn-color: rgb(255 255 255) !important;
      --toggled-btn-bg-color: rgb(0 0 0 / 0.3) !important;
      --toggled-hover-active-btn-color: rgb(0 0 0 / 0.4) !important;
      --dropdown-btn-bg-color: rgb(74 74 79) !important;
      --separator-color: rgb(0 0 0 / 0.3) !important;
      --field-color: rgb(250 250 250) !important;
      --field-bg-color: rgb(64 64 68) !important;
      --field-border-color: rgb(115 115 115) !important;
      --treeitem-color: rgb(255 255 255 / 0.8) !important;
      --treeitem-bg-color: rgb(255 255 255 / 0.15) !important;
      --treeitem-hover-color: rgb(255 255 255 / 0.9) !important;
      --treeitem-selected-color: rgb(255 255 255 / 0.9) !important;
      --treeitem-selected-bg-color: rgb(255 255 255 / 0.25) !important;
      --thumbnail-hover-color: rgb(255 255 255 / 0.1) !important;
      --thumbnail-selected-color: rgb(255 255 255 / 0.2) !important;
      --doorhanger-bg-color: rgb(74 74 79) !important;
      --doorhanger-border-color: rgb(39 39 43) !important;
      --doorhanger-hover-color: rgb(249 249 250) !important;
      --doorhanger-hover-bg-color: rgb(93 94 98) !important;
      --doorhanger-separator-color: rgb(92 92 97) !important;
      --dialog-button-bg-color: rgb(92 92 97) !important;
      --dialog-button-hover-bg-color: rgb(115 115 115) !important;
    }
    /* When is-light is set, force ALL light theme variables */
    :root.is-light {
      --main-color: rgb(12 12 13) !important;
      --body-bg-color: rgb(212 212 215) !important;
      --progressBar-color: rgb(10 132 255) !important;
      --progressBar-bg-color: rgb(221 221 222) !important;
      --progressBar-blend-color: rgb(116 177 239) !important;
      --scrollbar-color: auto !important;
      --scrollbar-bg-color: auto !important;
      --toolbar-icon-bg-color: rgb(0 0 0) !important;
      --toolbar-icon-hover-bg-color: rgb(0 0 0) !important;
      --sidebar-narrow-bg-color: rgb(212 212 215 / 0.9) !important;
      --sidebar-toolbar-bg-color: rgb(245 246 247) !important;
      --toolbar-bg-color: rgb(249 249 250) !important;
      --toolbar-border-color: rgb(184 184 184) !important;
      --button-hover-color: rgb(221 222 223) !important;
      --toggled-btn-color: rgb(0 0 0) !important;
      --toggled-btn-bg-color: rgb(0 0 0 / 0.3) !important;
      --toggled-hover-active-btn-color: rgb(0 0 0 / 0.4) !important;
      --dropdown-btn-bg-color: rgb(215 215 219) !important;
      --separator-color: rgb(0 0 0 / 0.3) !important;
      --field-color: rgb(12 12 13) !important;
      --field-bg-color: rgb(255 255 255) !important;
      --field-border-color: rgb(115 115 115) !important;
      --treeitem-color: rgb(0 0 0 / 0.8) !important;
      --treeitem-bg-color: rgb(0 0 0 / 0.15) !important;
      --treeitem-hover-color: rgb(0 0 0 / 0.9) !important;
      --treeitem-selected-color: rgb(0 0 0 / 0.9) !important;
      --treeitem-selected-bg-color: rgb(0 0 0 / 0.25) !important;
      --thumbnail-hover-color: rgb(0 0 0 / 0.1) !important;
      --thumbnail-selected-color: rgb(0 0 0 / 0.2) !important;
      --doorhanger-bg-color: rgb(255 255 255) !important;
      --doorhanger-border-color: rgb(184 184 184) !important;
      --doorhanger-hover-color: rgb(12 12 13) !important;
      --doorhanger-hover-bg-color: rgb(221 222 223) !important;
      --doorhanger-separator-color: rgb(184 184 184) !important;
      --dialog-button-bg-color: rgb(215 215 219) !important;
      --dialog-button-hover-bg-color: rgb(184 184 184) !important;
    }
    /* Disable PDF.js media query - force it to use our classes only */
    @media (prefers-color-scheme: dark) {
      :root.is-light {
        /* When is-light is set, ignore system dark preference */
        --main-color: rgb(12 12 13) !important;
        --body-bg-color: rgb(212 212 215) !important;
      }
    }
    @media (prefers-color-scheme: light) {
      :root.is-dark {
        /* When is-dark is set, ignore system light preference */
        --main-color: rgb(249 249 250) !important;
        --body-bg-color: rgb(42 42 46) !important;
      }
    }
    /* CRITICAL: Keep PDF document pages in ORIGINAL colors by default - NO inversion */
    /* PDF page theme inversion is controlled by user setting, applied via JavaScript */
    /* This CSS ensures no default inversion happens */
    body .pdfViewer .page canvas,
    body .pdfViewer .page img,
    body .pdfViewer .page svg,
    body .thumbnailView canvas,
    body .thumbnailView img,
    body .thumbnail canvas,
    body .thumbnail img,
    body .thumbnailImage canvas,
    body .thumbnailImage img,
    body #thumbnailView canvas,
    body #thumbnailView img,
    .pdfViewer .page canvas,
    .pdfViewer .page img,
    .pdfViewer .page svg,
    .pdfViewer .page,
    .thumbnailView canvas,
    .thumbnailView img {
      filter: none !important;
      -webkit-filter: none !important;
    }
    /* Ensure no parent elements are applying filters by default */
    body[data-dark-mode="true"] .pdfViewer .page,
    body[data-dark-mode="false"] .pdfViewer .page,
    body[data-dark-mode="true"] .pdfViewer .page canvas,
    body[data-dark-mode="false"] .pdfViewer .page canvas,
    .pdfViewer .page {
      filter: none !important;
      -webkit-filter: none !important;
    }
    
    /* ============================================
       SIDEBAR-STYLE MATCHING: Modern macOS Aesthetic
       ============================================ */
    
    /* Typography - Match sidebar font */
    body, .toolbar, .toolbarButton, .secondaryToolbarButton,
    .doorhanger, .doorHanger, .dialog, .dialogContainer, .findbar, #sidebarContainer,
    .treeItem, .treeItemContent, input, select, textarea, .toolbarField {
      font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Segoe UI', Roboto, sans-serif !important;
      -webkit-font-smoothing: antialiased !important;
      -moz-osx-font-smoothing: grayscale !important;
    }
    
    /* Toolbar - Modern shadows */
    .toolbar {
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1) !important;
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
    }
    
    /* Toolbar buttons - Rounded corners, smooth transitions */
    .toolbarButton, .secondaryToolbarButton {
      border-radius: 8px !important;
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
      margin: 2px 1px !important;
    }
    
    .toolbarButton:hover, .secondaryToolbarButton:hover {
      transform: translateY(-1px) !important;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15) !important;
    }
    
    .toolbarButton:active, .secondaryToolbarButton:active {
      transform: translateY(0) !important;
      transition: all 0.1s ease !important;
    }
    
    /* Split toolbar buttons */
    .splitToolbarButton > .toolbarButton {
      border-radius: 8px !important;
    }
    
    /* Dropdowns and doorhangers - Rounded corners, modern shadows */
    .doorhanger, .doorHanger {
      border-radius: 10px !important;
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2) !important;
      backdrop-filter: blur(20px) !important;
      -webkit-backdrop-filter: blur(20px) !important;
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
      border: 1px solid rgba(255, 255, 255, 0.1) !important;
    }
    
    .doorhanger::before {
      border-radius: 10px !important;
    }
    
    /* Doorhanger menu items */
    .doorhanger > .doorhangerItem, .doorHanger > .doorhangerItem {
      border-radius: 6px !important;
      margin: 2px 4px !important;
      transition: all 0.15s cubic-bezier(0.4, 0, 0.2, 1) !important;
    }
    
    .doorhanger > .doorhangerItem:hover, .doorHanger > .doorhangerItem:hover {
      transform: translateX(2px) !important;
    }
    
    /* Dialogs - Rounded corners, modern styling */
    .dialog, .dialogContainer {
      border-radius: 12px !important;
      box-shadow: 0 12px 48px rgba(0, 0, 0, 0.3) !important;
      backdrop-filter: blur(20px) !important;
      -webkit-backdrop-filter: blur(20px) !important;
      transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) !important;
    }
    
    /* Dialog buttons */
    .dialogButton {
      border-radius: 8px !important;
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
      font-weight: 500 !important;
    }
    
    .dialogButton:hover {
      transform: translateY(-1px) !important;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15) !important;
    }
    
    /* Input fields - Rounded corners */
    .toolbarField, input[type="text"], input[type="number"],
    select, textarea {
      border-radius: 8px !important;
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
      border: 1px solid var(--field-border-color) !important;
    }
    
    .toolbarField:focus, input:focus, select:focus, textarea:focus {
      outline: none !important;
      box-shadow: 0 0 0 3px rgba(139, 92, 246, 0.2) !important;
      border-color: rgba(139, 92, 246, 0.5) !important;
    }
    
    /* Findbar - Rounded corners */
    .findbar {
      border-radius: 0 0 10px 10px !important;
    }
    
    /* Sidebar - Match sidebar styling */
    #sidebarContainer {
      box-shadow: inset -1px 0 0 rgba(0, 0, 0, 0.1) !important;
    }
    
    /* Tree items - Rounded corners, smooth transitions */
    .treeItem, .treeItemContent {
      border-radius: 6px !important;
      transition: all 0.15s cubic-bezier(0.4, 0, 0.2, 1) !important;
      margin: 1px 4px !important;
    }
    
    .treeItem:hover {
      transform: translateX(2px) !important;
    }
    
    /* Thumbnails - Rounded corners */
    .thumbnail {
      border-radius: 8px !important;
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1) !important;
    }
    
    .thumbnail:hover {
      transform: translateY(-2px) scale(1.02) !important;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.2) !important;
    }
    
    .thumbnail.selected {
      box-shadow: 0 0 0 2px rgba(139, 92, 246, 0.5), 0 4px 16px rgba(0, 0, 0, 0.2) !important;
    }
    
    /* Progress bar - Rounded */
    #loadingBar, .progressBar {
      border-radius: 4px !important;
    }
    
    /* Scrollbars - Match sidebar custom scrollbars */
    ::-webkit-scrollbar {
      width: 10px !important;
      height: 10px !important;
    }
    
    ::-webkit-scrollbar-track {
      background: var(--scrollbar-bg-color) !important;
      border-radius: 5px !important;
    }
    
    ::-webkit-scrollbar-thumb {
      background: var(--scrollbar-color) !important;
      border-radius: 5px !important;
      border: 2px solid var(--scrollbar-bg-color) !important;
      transition: background 0.2s ease !important;
    }
    
    ::-webkit-scrollbar-thumb:hover {
      background: var(--button-hover-color) !important;
    }
    
    /* Light mode scrollbar adjustments */
    :root.is-light ::-webkit-scrollbar-thumb {
      background: rgba(0, 0, 0, 0.2) !important;
    }
    
    :root.is-light ::-webkit-scrollbar-thumb:hover {
      background: rgba(0, 0, 0, 0.3) !important;
    }
    
    /* Dark mode scrollbar adjustments */
    :root.is-dark ::-webkit-scrollbar-thumb {
      background: rgba(255, 255, 255, 0.2) !important;
    }
    
    :root.is-dark ::-webkit-scrollbar-thumb:hover {
      background: rgba(255, 255, 255, 0.3) !important;
    }
    
    /* Separators - Subtle styling */
    .verticalToolbarSeparator, .horizontalToolbarSeparator {
      opacity: 0.3 !important;
      transition: opacity 0.2s ease !important;
    }
    
    /* Secondary toolbar - Rounded corners */
    #secondaryToolbar {
      border-radius: 0 0 10px 10px !important;
    }
    
    /* Annotation editor toolbar */
    .editorParamsToolbar {
      border-radius: 8px !important;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.15) !important;
    }
    
    /* Color picker - Ensure consistency */
    .colorPicker {
      border-radius: 10px !important;
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2) !important;
    }
    
    /* Page rotation indicator */
    .page {
      transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
    }
    
    /* Smooth transitions for all interactive elements */
    button, .toolbarButton, .secondaryToolbarButton,
    .doorhangerItem, .treeItem, .thumbnail,
    input, select, textarea {
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
    }
    
    /* Focus states - macOS style */
    *:focus-visible {
      outline: 2px solid rgba(139, 92, 246, 0.5) !important;
      outline-offset: 2px !important;
      border-radius: 4px !important;
    }
    
    /* Disabled states */
    .toolbarButton[disabled], .secondaryToolbarButton[disabled],
    button[disabled] {
      opacity: 0.5 !important;
      cursor: not-allowed !important;
    }
    
    /* Toggled/active states */
    .toolbarButton.toggled, .secondaryToolbarButton.toggled {
      box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.1) !important;
    }
  </style>'''
        
        # Replace existing CSS if present, otherwise add it
        import re
        # Remove any existing themeIconPdf style block
        content = re.sub(r'<style>.*?themeIconPdf.*?</style>', '', content, flags=re.DOTALL)
        
        # Add CSS to head - always add it fresh
        if '</head>' in content:
            content = content.replace('</head>', css_override + '\n</head>')
        elif '<link rel="stylesheet" href="viewer.css">' in content:
            content = content.replace('<link rel="stylesheet" href="viewer.css">', 
                                     '<link rel="stylesheet" href="viewer.css">' + css_override)
        
        # Add JavaScript to handle theme toggle at the end of body, before closing script tags
        theme_script = '''
  <script>
    // Theme toggle functionality for PDF viewer
    (function() {
      function initThemeToggle() {
        const themeBtn = document.getElementById('themeTogglePdf');
        const themeIcon = document.getElementById('themeIconPdf');
        if (!themeBtn || !themeIcon) {
          // Retry if elements not ready yet
          setTimeout(initThemeToggle, 100);
          return;
        }
        
        let currentTheme = 'dark';
        
        // Set initial data attribute
        document.body.setAttribute('data-dark-mode', 'true');
        
        // Listen for theme changes from parent window
        window.addEventListener('message', (e) => {
          if (e.data && e.data.type === 'theme-changed') {
            currentTheme = e.data.theme || 'dark';
            // Set data attribute on body for CSS targeting
            document.body.setAttribute('data-dark-mode', currentTheme === 'dark' ? 'true' : 'false');
            updateIcon();
          }
        });
        
        // Request current theme from parent on load
        if (window.parent && window.parent !== window) {
          setTimeout(() => {
            window.parent.postMessage({ type: 'get-theme' }, '*');
          }, 500);
        }
        
        function updateIcon() {
          if (themeIcon && themeBtn) {
            // Animate icon change - smooth rotation animation like overlay button
            themeIcon.style.transform = 'rotate(180deg) scale(0.8)';
            themeIcon.style.opacity = '0.5';
            setTimeout(() => {
              themeIcon.textContent = currentTheme === 'light' ? '☀️' : '🌙';
              // Set data attribute for CSS targeting
              themeBtn.setAttribute('data-theme', currentTheme);
              themeIcon.style.transform = 'rotate(360deg) scale(1)';
              setTimeout(() => {
                themeIcon.style.transform = 'rotate(0deg) scale(1)';
                themeIcon.style.opacity = '1';
              }, 50);
            }, 200);
          }
        }
        
        themeBtn.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          // Notify parent window to toggle theme (parent will update theme and send back)
          if (window.parent && window.parent !== window) {
            window.parent.postMessage({ type: 'toggle-theme' }, '*');
          }
        });
        
        // Initial icon update
        updateIcon();
      }
      
      // Wait for DOM to be ready
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initThemeToggle);
      } else {
        // DOM already ready, but wait a bit for PDF.js to initialize
        setTimeout(initThemeToggle, 500);
      }
    })();
    
    // Elegant Text Highlighting System
    (function() {
      const HIGHLIGHT_COLORS = [
        { name: 'yellow', hex: '#FFEB3B', rgba: 'rgba(255, 235, 59, 0.5)' },
        { name: 'green', hex: '#4CAF50', rgba: 'rgba(76, 175, 80, 0.5)' },
        { name: 'blue', hex: '#2196F3', rgba: 'rgba(33, 150, 243, 0.5)' },
        { name: 'pink', hex: '#FF4081', rgba: 'rgba(255, 64, 129, 0.5)' },
        { name: 'orange', hex: '#FF9800', rgba: 'rgba(255, 152, 0, 0.5)' },
        { name: 'purple', hex: '#9C27B0', rgba: 'rgba(156, 39, 176, 0.5)' },
        { name: 'red', hex: '#F44336', rgba: 'rgba(244, 67, 54, 0.5)' },
        { name: 'cyan', hex: '#00BCD4', rgba: 'rgba(0, 188, 212, 0.5)' }
      ];
      
      let currentHighlightColor = HIGHLIGHT_COLORS[0]; // Default yellow
      let highlightMode = false;
      let highlights = []; // Store all highlights for saving to PDF
      
      function initHighlightButton() {
        const highlightBtn = document.getElementById('editorHighlight');
        if (!highlightBtn) {
          setTimeout(initHighlightButton, 100);
          return;
        }
        
        // Create color picker popup
        const colorPicker = document.createElement('div');
        colorPicker.id = 'highlightColorPicker';
        
        // Row 1
        const row1 = document.createElement('div');
        row1.className = 'colorPickerRow';
        for (let i = 0; i < 4; i++) {
          const swatch = createColorSwatch(HIGHLIGHT_COLORS[i]);
          row1.appendChild(swatch);
        }
        
        // Row 2
        const row2 = document.createElement('div');
        row2.className = 'colorPickerRow';
        for (let i = 4; i < 8; i++) {
          const swatch = createColorSwatch(HIGHLIGHT_COLORS[i]);
          row2.appendChild(swatch);
        }
        
        colorPicker.appendChild(row1);
        colorPicker.appendChild(row2);
        
        // Insert color picker after highlight button
        highlightBtn.parentElement.appendChild(colorPicker);
        
        // Mark first swatch as selected
        const firstSwatch = colorPicker.querySelector('.highlightColorSwatch');
        if (firstSwatch) {
          firstSwatch.classList.add('selected');
        }
        
        // Toggle color picker on button click
        highlightBtn.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopPropagation();
          const isOpen = colorPicker.classList.contains('show');
          colorPicker.classList.toggle('show');
          
          if (!isOpen) {
            // Enable highlight mode
            enableHighlightMode();
          }
        });
        
        // Close picker when clicking outside
        document.addEventListener('click', (e) => {
          if (!highlightBtn.contains(e.target) && !colorPicker.contains(e.target)) {
            colorPicker.classList.remove('show');
          }
        });
        
        console.log('✓ Highlight button initialized');
      }
      
      function createColorSwatch(color) {
        const swatch = document.createElement('div');
        swatch.className = 'highlightColorSwatch';
        swatch.style.backgroundColor = color.hex;
        swatch.title = color.name.charAt(0).toUpperCase() + color.name.slice(1);
        swatch.dataset.colorName = color.name;
        
        swatch.addEventListener('click', (e) => {
          e.stopPropagation();
          currentHighlightColor = color;
          
          // Update selected state
          const colorPicker = document.getElementById('highlightColorPicker');
          if (colorPicker) {
            colorPicker.querySelectorAll('.highlightColorSwatch').forEach(s => s.classList.remove('selected'));
          }
          swatch.classList.add('selected');
          
          // Enable highlight mode when color is selected
          enableHighlightMode();
          
          console.log(\`Highlight color selected: \${color.name}\`);
        });
        
        return swatch;
      }
      
      function enableHighlightMode() {
        highlightMode = true;
        const highlightBtn = document.getElementById('editorHighlight');
        if (highlightBtn) {
          highlightBtn.setAttribute('aria-checked', 'true');
          highlightBtn.classList.add('toggled');
          highlightBtn.removeAttribute('disabled');
        }
        
        // Listen for text selection
        document.addEventListener('mouseup', handleTextSelection);
        console.log('Highlight mode enabled');
      }
      
      function disableHighlightMode() {
        highlightMode = false;
        const highlightBtn = document.getElementById('editorHighlight');
        if (highlightBtn) {
          highlightBtn.setAttribute('aria-checked', 'false');
          highlightBtn.classList.remove('toggled');
        }
        
        document.removeEventListener('mouseup', handleTextSelection);
      }
      
      function handleTextSelection(e) {
        if (!highlightMode) return;
        
        setTimeout(() => {
          const selection = window.getSelection();
          if (!selection || selection.rangeCount === 0) return;
          
          const selectedText = selection.toString().trim();
          if (!selectedText) return;
          
          try {
            const range = selection.getRangeAt(0);
            
            // Check if selection is in text layer
            let container = range.commonAncestorContainer;
            while (container && container !== document) {
              if (container.classList && container.classList.contains('textLayer')) {
                applyHighlight(range, selection);
                break;
              }
              container = container.parentElement;
            }
          } catch (error) {
            console.error('Highlight error:', error);
          }
        }, 10);
      }
      
      function applyHighlight(range, selection) {
        try {
          const selectedText = range.toString().trim();
          if (!selectedText) return;
          
          // Get page number
          let pageNum = null;
          let node = range.commonAncestorContainer;
          while (node && node !== document) {
            if (node.classList && node.classList.contains('page')) {
              pageNum = parseInt(node.dataset.pageNumber);
              break;
            }
            node = node.parentElement;
          }
          
          // Create highlight span
          const highlightSpan = document.createElement('span');
          highlightSpan.className = \`pdf-highlight \${currentHighlightColor.name}\`;
          highlightSpan.setAttribute('data-highlight-color', currentHighlightColor.name);
          highlightSpan.setAttribute('data-page', pageNum || '');
          highlightSpan.style.backgroundColor = currentHighlightColor.rgba;
          
          // Try to surround the selected content
          try {
            range.surroundContents(highlightSpan);
            
            // Store highlight data for PDF saving
            storeHighlight({
              text: selectedText,
              color: currentHighlightColor.name,
              page: pageNum,
              position: getTextPosition(range)
            });
            
            // Clear selection with animation
            setTimeout(() => {
              selection.removeAllRanges();
            }, 300);
            
            console.log(\`✓ Highlighted text on page \${pageNum}: "\${selectedText.substring(0, 30)}..."\`);
          } catch (e) {
            // Fallback for complex selections
            console.warn('surroundContents failed, using fallback:', e);
            highlightComplexSelection(range, selection, pageNum);
          }
        } catch (error) {
          console.error('Apply highlight error:', error);
        }
      }
      
      function highlightComplexSelection(range, selection, pageNum) {
        const selectedText = range.toString().trim();
        const startContainer = range.startContainer;
        const endContainer = range.endContainer;
        const startOffset = range.startOffset;
        const endOffset = range.endOffset;
        
        // Single text node case
        if (startContainer === endContainer && startContainer.nodeType === 3) {
          const textNode = startContainer;
          const parent = textNode.parentNode;
          const text = textNode.textContent;
          
          const before = text.substring(0, startOffset);
          const selected = text.substring(startOffset, endOffset);
          const after = text.substring(endOffset);
          
          if (selected) {
            const highlightSpan = document.createElement('span');
            highlightSpan.className = \`pdf-highlight \${currentHighlightColor.name}\`;
            highlightSpan.setAttribute('data-highlight-color', currentHighlightColor.name);
            highlightSpan.style.backgroundColor = currentHighlightColor.rgba;
            highlightSpan.textContent = selected;
            
            // Replace text node
            if (before) parent.insertBefore(document.createTextNode(before), textNode);
            parent.insertBefore(highlightSpan, textNode);
            if (after) parent.insertBefore(document.createTextNode(after), textNode);
            parent.removeChild(textNode);
            
            storeHighlight({
              text: selectedText,
              color: currentHighlightColor.name,
              page: pageNum,
              position: getTextPosition(range)
            });
            
            selection.removeAllRanges();
            console.log(\`✓ Complex highlight applied on page \${pageNum}\`);
          }
        }
      }
      
      function getTextPosition(range) {
        try {
          const rect = range.getBoundingClientRect();
          return {
            x: rect.left,
            y: rect.top,
            width: rect.width,
            height: rect.height
          };
        } catch (e) {
          return null;
        }
      }
      
      function storeHighlight(highlightData) {
        highlights.push({
          ...highlightData,
          timestamp: new Date().toISOString()
        });
        
        // Send to parent for backend saving (debounced)
        clearTimeout(window.highlightSaveTimeout);
        window.highlightSaveTimeout = setTimeout(() => {
          if (window.parent && window.parent !== window) {
            window.parent.postMessage({
              type: 'save-highlights',
              highlights: highlights
            }, '*');
          }
        }, 1000);
      }
      
      // Initialize when PDF.js is ready
      if (window.PDFViewerApplication && window.PDFViewerApplication.initializedPromise) {
        window.PDFViewerApplication.initializedPromise.then(initHighlightButton);
      } else {
        setTimeout(initHighlightButton, 1000);
      }
    })();
  </script>'''
        
        # Insert script before closing body tag - but only if new script doesn't exist
        if '</body>' in content:
            # Check if new script (with applyPdfTheme) already exists
            if 'function applyPdfTheme' not in content:
                # Remove any old theme scripts first
                content = re.sub(r'<script>[\s\S]*?function initThemeToggle[\s\S]*?</script>', '', content, flags=re.DOTALL)
                content = content.replace('</body>', theme_script + '\n</body>')
        else:
            # Fallback: add before closing html tag
            if theme_script not in content:
                content = content.replace('</html>', theme_script + '\n</html>')
        
        # Akson toolbar refinement: doc title + zoom slider + tidy zoom layout
        if 'AKSON_TOOLBAR_TWEAK' not in content:
            toolbar_css = '''
    <!-- AKSON_TOOLBAR_TWEAK -->
    <style id="akson-toolbar-css">
      #toolbarViewerMiddle {
        display: flex;
        align-items: center;
        justify-content: center;
        min-width: 200px;
      }
      #aksonDocTitle {
        padding: 4px 10px;
        border-radius: 8px;
        background: rgba(255, 255, 255, 0.06);
        color: var(--toolbarButton-tint, #fff);
        max-width: 420px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        font-weight: 600;
        letter-spacing: 0.2px;
      }
      .aksonZoomCluster {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-left: 12px;
      }
      #aksonZoomSlider {
        width: 140px;
        accent-color: #8b5cf6;
        cursor: pointer;
      }
      #scaleSelectContainer select {
        border-radius: 8px;
        padding-left: 10px;
      }
    </style>
'''
            akson_script = '''
    <script id="AKSON_TOOLBAR_TWEAK">
      (function() {
        if (window.__aksonToolbarTweaked) return;
        window.__aksonToolbarTweaked = true;

        function getDocName() {
          try {
            const params = new URLSearchParams(window.location.search);
            const file = params.get('file');
            if (!file) return 'Document';
            const base = decodeURIComponent(file.split('/').pop() || '').replace(/\\.pdf$/i, '').trim();
            return base || 'Document';
          } catch (e) {
            return 'Document';
          }
        }

        function clamp(v, min, max) { return Math.min(max, Math.max(min, v)); }

        function buildToolbar() {
          const left = document.getElementById('toolbarViewerLeft');
          const mid = document.getElementById('toolbarViewerMiddle');
          const zoomOut = document.getElementById('zoomOut');
          const zoomIn = document.getElementById('zoomIn');
          const scaleSelectContainer = document.getElementById('scaleSelectContainer');
          if (!left || !mid || !zoomOut || !zoomIn || !scaleSelectContainer) {
            setTimeout(buildToolbar, 200);
            return;
          }

          const zoomButtonsWrapper = zoomOut.closest('.splitToolbarButton') || zoomOut.parentElement;
          const cluster = document.createElement('div');
          cluster.className = 'aksonZoomCluster';

          if (zoomButtonsWrapper) cluster.appendChild(zoomButtonsWrapper);

          const slider = document.createElement('input');
          slider.type = 'range';
          slider.id = 'aksonZoomSlider';
          slider.min = '50';
          slider.max = '400';
          slider.step = '10';
          slider.value = '100';
          slider.setAttribute('aria-label', 'Zoom slider');
          cluster.appendChild(slider);

          cluster.appendChild(scaleSelectContainer);

          // Place cluster after page number info
          const numPages = document.getElementById('numPages');
          if (numPages && numPages.parentElement) {
            numPages.parentElement.insertAdjacentElement('afterend', cluster);
          } else {
            left.appendChild(cluster);
          }

          // Put doc title in the middle
          mid.innerHTML = '';
          const title = document.createElement('div');
          title.id = 'aksonDocTitle';
          title.textContent = getDocName();
          title.title = title.textContent;
          mid.appendChild(title);

          function setScaleFromSlider(val) {
            const pct = clamp(parseInt(val || '100', 10), 50, 400);
            const app = window.PDFViewerApplication;
            if (app?.pdfViewer) {
              app.pdfViewer.currentScaleValue = pct / 100;
            }
          }

          slider.addEventListener('input', e => setScaleFromSlider(e.target.value));

          function syncSlider(scale) {
            if (!slider || typeof scale !== 'number' || isNaN(scale)) return;
            const pct = clamp(Math.round(scale * 100), 50, 400);
            slider.value = String(pct);
          }

          function watchApp() {
            const app = window.PDFViewerApplication;
            if (!app || !app.eventBus || !app.pdfViewer) {
              setTimeout(watchApp, 200);
              return;
            }
            app.eventBus.on('scalechanging', evt => syncSlider(evt?.scale));
            syncSlider(app.pdfViewer.currentScale || app.pdfViewer.currentScaleValue || 1);
          }

          watchApp();
        }

        document.addEventListener('DOMContentLoaded', buildToolbar);
        buildToolbar();
      })();
    </script>
'''
            if 'akson-toolbar-css' not in content:
                content = content.replace('</head>', toolbar_css + '\n</head>', 1)
            if 'AKSON_TOOLBAR_TWEAK' not in content:
                content = content.replace('</body>', akson_script + '\n</body>', 1)

        viewer_html_path.write_text(content, encoding="utf-8")
        print("✓ Theme toggle button added to PDF viewer toolbar")
    except Exception as e:
        print(f"⚠ Could not patch viewer.html: {e}")


def download_pdfjs_async() -> None:
    """Start PDF.js download in background thread."""
    def download():
        download_pdfjs_if_needed()
    threading.Thread(target=download, daemon=True).start()


def ensure_splash_created() -> None:
    """Create a polished splash page with branding."""
    splash_html = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Akson</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  
  body { 
    margin: 0; 
    background: #1a1a1c; 
    color: #e8e8ea; 
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
    display: flex; 
    align-items: center; 
    justify-content: center; 
    height: 100vh;
    overflow: hidden;
  }
  
  .splash-container {
    text-align: center;
    animation: fadeIn 0.4s ease-in;
  }
  
  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(10px); }
    to { opacity: 1; transform: translateY(0); }
  }
  
  .app-name {
    font-size: 48px;
    font-weight: 600;
    letter-spacing: -0.5px;
    margin-bottom: 24px;
    color: #e8e8ea;
  }
  
  .spinner {
    width: 40px;
    height: 40px;
    margin: 0 auto 16px;
    border: 3px solid rgba(139, 92, 246, 0.2);
    border-top-color: #8B5CF6;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  
  @keyframes spin {
    to { transform: rotate(360deg); }
  }
  
  .loading-text {
    font-size: 14px;
    color: #9a9a9c;
    font-weight: 400;
    letter-spacing: 0.3px;
  }
  
  /* 7. Enhanced Loading States */
  .rsBox.loading,
  .aiAnswerBox.loading,
  .rsEmpty.loading,
  #fcEmpty.loading {
    color: var(--accent-purple) !important;
    font-style: italic;
    position: relative;
    text-align: center;
    min-height: 80px;
    padding-top: 50px !important;
    padding-left: 0 !important;
    padding-right: 0 !important;
  }
  
  /* Centered spinner */
  .rsBox.loading::before,
  .aiAnswerBox.loading::before,
  .rsEmpty.loading::before,
  #fcEmpty.loading::before {
    content: '' !important;
    position: absolute;
    left: 50%;
    top: 20px;
    transform: translateX(-50%);
    width: 20px;
    height: 20px;
    border: 2px solid rgba(139, 92, 246, 0.3);
    border-top-color: var(--accent-purple);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    z-index: 10;
    margin: 0;
    opacity: 1;
    background: none;
    pointer-events: none;
  }
  
  /* Hide empty state icons when loading */
  .rsEmpty.loading[data-placeholder]::after,
  #fcEmpty.loading[data-placeholder]::after,
  #rsExplain.loading[data-placeholder]::after,
  #rsSummary.loading[data-placeholder]::after,
  #aiAnswer.loading[data-placeholder]::after {
    display: none !important;
  }
  
  /* Override placeholder when loading - ensure spinner shows */
  .rsBox.loading[data-placeholder]::before,
  .aiAnswerBox.loading[data-placeholder]::before,
  .rsEmpty.loading[data-placeholder]::before,
  #fcEmpty.loading[data-placeholder]::before {
    content: '' !important;
    border: 2px solid rgba(139, 92, 246, 0.3) !important;
    border-top-color: var(--accent-purple) !important;
    border-radius: 50% !important;
    animation: spin 0.8s linear infinite !important;
    width: 20px !important;
    height: 20px !important;
    position: absolute !important;
    left: 50% !important;
    top: 20px !important;
    transform: translateX(-50%) !important;
    margin: 0 !important;
    opacity: 1 !important;
    background: none !important;
    pointer-events: none !important;
  }
  
  /* Ensure loading text is visible and centered */
  .rsBox.loading > *,
  .aiAnswerBox.loading > *,
  .rsEmpty.loading > *,
  #fcEmpty.loading > * {
    text-align: center;
    margin: 0 auto;
  }
  
  /* Light mode adjustments for loading */
  body.light-mode .rsBox.loading,
  body.light-mode .aiAnswerBox.loading,
  body.light-mode .rsEmpty.loading,
  body.light-mode #fcEmpty.loading {
    color: var(--accent-purple) !important;
  }
  
  body.light-mode .rsBox.loading::before,
  body.light-mode .aiAnswerBox.loading::before,
  body.light-mode .rsEmpty.loading::before,
  body.light-mode #fcEmpty.loading::before {
    border-color: rgba(139, 92, 246, 0.3);
    border-top-color: var(--accent-purple);
  }
  
  /* 10. Enhanced Error & Success Messages */
  .rsBox.error,
  .aiAnswerBox.error {
    color: #ff6b6b;
    background: rgba(255, 107, 107, 0.1);
    border-left: 3px solid #ff6b6b;
    padding-left: 36px;
    position: relative;
  }
  
  .rsBox.error::before,
  .aiAnswerBox.error::before {
    content: '⚠️';
    position: absolute;
    left: 12px;
    top: 12px;
    font-size: 16px;
  }
  
  .rsBox.success,
  .aiAnswerBox.success {
    color: #51cf66;
    background: rgba(81, 207, 102, 0.1);
    border-left: 3px solid #51cf66;
    padding-left: 36px;
    position: relative;
  }
  
  .rsBox.success::before,
  .aiAnswerBox.success::before {
    content: '✓';
    position: absolute;
    left: 12px;
    top: 12px;
    font-size: 16px;
    color: #51cf66;
  }
  
  body.light-mode .rsBox.error,
  body.light-mode .aiAnswerBox.error {
    background: rgba(255, 107, 107, 0.08);
  }
  
  body.light-mode .rsBox.success,
  body.light-mode .aiAnswerBox.success {
    background: rgba(81, 207, 102, 0.08);
  }
  
  /* 2. Enhanced Toast Notification System */
  .toast-container {
    position: fixed;
    top: 20px;
    right: 20px;
    z-index: 100000;
    display: flex;
    flex-direction: column;
    gap: 12px;
    pointer-events: none;
  }
  
  .toast {
    background: var(--surface-elevated);
    border: 1px solid var(--border-subtle);
    border-radius: 8px;
    padding: 14px 18px;
    min-width: 280px;
    max-width: 400px;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
    display: flex;
    align-items: center;
    gap: 12px;
    pointer-events: auto;
    animation: toastSlideIn 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    position: relative;
    overflow: hidden;
  }
  
  .toast::after {
    content: '';
    position: absolute;
    bottom: 0;
    left: 0;
    height: 3px;
    background: currentColor;
    animation: toastProgress 3s linear forwards;
  }
  
  @keyframes toastSlideIn {
    from {
      transform: translateX(400px);
      opacity: 0;
    }
    to {
      transform: translateX(0);
      opacity: 1;
    }
  }
  
  @keyframes toastProgress {
    from { width: 100%; }
    to { width: 0%; }
  }
  
  .toast.success {
    border-left: 4px solid #51cf66;
    color: #51cf66;
  }
  
  .toast.error {
    border-left: 4px solid #ff6b6b;
    color: #ff6b6b;
  }
  
  .toast.info {
    border-left: 4px solid var(--accent-purple);
    color: var(--accent-purple);
  }
  
  .toast-icon {
    font-size: 20px;
    flex-shrink: 0;
  }
  
  .toast-content {
    flex: 1;
    color: var(--text-primary);
    font-size: 14px;
    line-height: 1.4;
  }
  
  .toast-close {
    background: none;
    border: none;
    color: var(--text-quaternary);
    cursor: pointer;
    font-size: 18px;
    padding: 0;
    width: 20px;
    height: 20px;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: color 0.2s ease;
  }
  
  .toast-close:hover {
    color: var(--text-primary);
  }
  
  body.light-mode .toast {
    background: #ffffff;
    border-color: rgba(0, 0, 0, 0.1);
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);
  }
  
  body.light-mode .toast-content {
    color: var(--text-primary-light);
  }
</style>
</head>
<body>
  <div class="splash-container">
    <div class="app-name">Akson</div>
    <div class="spinner"></div>
    <div class="loading-text">Loading PDF Viewer...</div>
  </div>
  <script>
    // Redirect immediately for instant loading
    const params = new URLSearchParams(location.search);
    const fileParam = params.get('file') || '';
    const mainUrl = '/app_wrapper.html' + (fileParam ? ('?file=' + encodeURIComponent(fileParam)) : '');
    window.location.href = mainUrl;
  </script>
</body>
</html>
"""
    splash_path = PDFJS_DIR / "splash.html"
    splash_path.write_text(splash_html, encoding="utf-8")


def ensure_wrapper_created() -> None:
    """Always (re)write wrapper so UI/JS updates are applied."""
    html = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>PDF Viewer + Notes</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<!-- WRAPPER_VERSION: 2025-11-02-PROFESSIONAL -->
<style>
  /* ============================================
     DESIGN SYSTEM - CSS CUSTOM PROPERTIES
     Apple-inspired design language
     ============================================ */
  :root {
    /* Layout */
    --sidebar-w: 380px;
    
    /* Colors - Dark Mode */
    --bg-primary: #000000;
    --bg-secondary: #1a1a1c;
    --bg-tertiary: #252528;
    --theme-dark: #1a1a1c;
    --theme-light: #ffffff;
    --surface-elevated: rgba(255, 255, 255, 0.03);
    --surface-hover: rgba(255, 255, 255, 0.05);
    --surface-active: rgba(255, 255, 255, 0.08);
    --border-subtle: rgba(255, 255, 255, 0.06);
    --border-default: rgba(255, 255, 255, 0.12);
    --text-primary: #ffffff;
    --text-secondary: #e8e8ea;
    --text-tertiary: #a0a0a2;
    --text-quaternary: #6e6e70;
    --accent-purple: #8B5CF6;
    --accent-purple-light: #A78BFA;
    --accent-purple-dark: #7C3AED;
    --accent-purple-glow: rgba(139, 92, 246, 0.15);
    /* Legacy support - keeping accent-blue for backward compatibility */
    --accent-blue: #8B5CF6;
    --accent-blue-glow: rgba(139, 92, 246, 0.15);
    
    /* Colors - Light Mode */
    --bg-primary-light: #ffffff;
    --bg-secondary-light: #f5f5f7;
    --bg-tertiary-light: #ffffff;
    --theme-light: #ffffff;
    --theme-dark: #1a1a1c;
    --surface-elevated-light: rgba(0, 0, 0, 0.02);
    --surface-hover-light: rgba(0, 0, 0, 0.04);
    --surface-active-light: rgba(0, 0, 0, 0.06);
    --border-subtle-light: rgba(0, 0, 0, 0.08);
    --border-default-light: rgba(0, 0, 0, 0.12);
    --text-primary-light: #1a1a1a;
    --text-secondary-light: #333333;
    --text-tertiary-light: #666666;
    --text-quaternary-light: #999999;
    --accent-purple-light-mode: #8B5CF6;
    --accent-purple-glow-light: rgba(139, 92, 246, 0.2);
    
    /* Typography */
    --font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Segoe UI', Roboto, sans-serif;
    --font-size-base: 13px;
    --font-size-small: 11px;
    --font-size-medium: 13.5px;
    --font-size-large: 15px;
    --font-size-title: 22px;
    --font-weight-regular: 400;
    --font-weight-medium: 500;
    --font-weight-semibold: 600;
    --font-weight-bold: 700;
    --line-height-base: 1.5;
    --line-height-relaxed: 1.7;
    
    /* Spacing */
    --spacing-xs: 4px;
    --spacing-sm: 8px;
    --spacing-md: 12px;
    --spacing-lg: 16px;
    --spacing-xl: 20px;
    --spacing-2xl: 24px;
    --spacing-3xl: 32px;
    --spacing-4xl: 40px;
    
    /* Border Radius */
    --radius-sm: 6px;
    --radius-md: 8px;
    --radius-lg: 10px;
    --radius-xl: 12px;
    --radius-2xl: 16px;
    --radius-3xl: 20px;
    
    /* Shadows - Layered elevation system */
    --shadow-xs: 0 1px 2px rgba(0, 0, 0, 0.04);
    --shadow-sm: 0 2px 8px rgba(0, 0, 0, 0.08);
    --shadow-md: 0 4px 16px rgba(0, 0, 0, 0.12);
    --shadow-lg: 0 8px 24px rgba(0, 0, 0, 0.16);
    --shadow-xl: 0 12px 48px rgba(0, 0, 0, 0.24);
    --shadow-2xl: 0 20px 60px rgba(0, 0, 0, 0.32);
    
    /* Transitions - Apple-inspired easing */
    --ease-out-quart: cubic-bezier(0.25, 1, 0.5, 1);
    --ease-out-expo: cubic-bezier(0.19, 1, 0.22, 1);
    --ease-in-out-quart: cubic-bezier(0.76, 0, 0.24, 1);
    --ease-spring: cubic-bezier(0.34, 1.56, 0.64, 1);
    --transition-fast: 0.15s var(--ease-out-quart);
    --transition-base: 0.25s var(--ease-out-expo);
    --transition-slow: 0.4s var(--ease-in-out-quart);
    --transition-spring: 0.5s var(--ease-spring);
    
    /* Z-index layers */
    --z-base: 1;
    --z-elevated: 10;
    --z-sticky: 100;
    --z-overlay: 1000;
    --z-modal: 10000;
    --z-maximum: 99999;
  }
  
  /* ============================================
     BASE STYLES
     ============================================ */
  * {
    box-sizing: border-box;
  }
  
  /* Smooth theme transitions for theme-related properties */
  body, #root, #work, #rightSidebar, 
  .rsBox, .fcItem, .bigBtn, #sidebarButtons, #rsScroll, #rsScrollContent,
  .rsSection, .rsSection h3, button, input, select, textarea {
    transition: background-color 0.8s ease, color 0.8s ease, border-color 0.8s ease, 
                box-shadow 0.8s ease, filter 0.8s ease;
  }
  
  /* Exclude PDF frame from transitions during zoom for better performance */
  #pdfWrap, #pdfFrame {
    transition: background-color 0.8s ease, border-color 0.8s ease;
  }
  
  html, body {
    height: 100%;
    margin: 0;
    padding: 0;
    background: var(--bg-primary);
    color: var(--text-secondary);
    font-family: var(--font-family);
    font-size: var(--font-size-base);
    line-height: var(--line-height-base);
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
    text-rendering: optimizeLegibility;
    transition: background-color 0.8s ease, color 0.8s ease, filter 0.8s ease;
  }
  
  
  /* ============================================
     LAYOUT
     ============================================ */
  #root {
    position: fixed;
    inset: 0;
    display: grid;
    grid-template-rows: 1fr;
    overflow: hidden;
    /* Ensure root container is always interactive */
    pointer-events: auto !important;
  }
  
  #work {
    display: grid;
    grid-template-columns: 1fr var(--sidebar-w);
    gap: 0;
    min-height: 0;
    position: relative;
    /* Smooth transition for grid AFTER sidebar animation completes */
    transition: grid-template-columns 0.3s cubic-bezier(0.25, 0.46, 0.45, 0.94);
    /* Ensure work area is always interactive */
    pointer-events: auto !important;
    /* Isolate layout changes to prevent reflow propagation */
    contain: layout style;
  }
  
  /* Disable transitions during active resizing for smooth performance */
  #work.resizing {
    transition: none !important;
    /* Remove inline grid styles during resize so CSS variable works */
  }
  
  #work.resizing #pdfWrap {
    transition: none !important;
  }
  
  #work.resizing #pdfFrame {
    transition: none !important;
  }
  
  /* During resize, ensure grid uses CSS variable, not inline style */
  #work.sidebar-resizing {
    grid-template-columns: 1fr var(--sidebar-w) !important;
  }
  
  /* During sidebar animation, completely freeze PDF - prevent all updates */
  #work.sidebar-animating #pdfWrap {
    contain: layout style paint;
    will-change: auto;
    /* Prevent any layout changes from propagating */
    isolation: isolate;
  }
  
  #work.sidebar-animating #pdfFrame {
    pointer-events: none;
    /* Completely freeze PDF - prevent any rendering updates */
    contain: strict;
    isolation: isolate;
    /* Force GPU layer to prevent repaints */
    transform: translateZ(0);
    /* Keep PDF visible - don't hide it */
  }
  
  /* Removed screenshot overlay - using resize blocking instead */
  
  /* During sidebar animation, grid transitions smoothly in sync with sidebar */
  #work.sidebar-animating {
    /* Grid transition is enabled - animates simultaneously with sidebar transform */
    /* Grid value is set via JavaScript inline style */
  }
  
  /* ============================================
     PDF VIEWER
     ============================================ */
  #pdfWrap {
    /* Contain layout changes to prevent reflow propagation */
    contain: layout;
    position: relative;
    min-width: 0;
    background: var(--bg-secondary);
    /* GPU acceleration for smooth zoom */
    will-change: contents;
    transform: translateZ(0);
    -webkit-backface-visibility: hidden;
    backface-visibility: hidden;
    /* Ensure PDF area is always interactive */
    pointer-events: auto !important;
  }

  #pdfWrap.empty #pdfFrame {
    display: none;
  }

  #pdfWrap .emptyState {
    position: absolute;
    inset: 0;
    display: none;
    align-items: center;
    justify-content: center;
    padding: 32px;
    background: linear-gradient(145deg, rgba(120, 131, 255, 0.06), rgba(118, 75, 162, 0.05));
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
  }

  #pdfWrap.empty .emptyState {
    display: flex;
  }

  .emptyCard {
    max-width: 560px;
    width: 100%;
    padding: 28px;
    border-radius: 20px;
    background: var(--surface-elevated);
    border: 1px solid var(--border-subtle);
    box-shadow: var(--shadow-lg);
    text-align: center;
    color: var(--text-primary);
  }

  .emptyIcon {
    width: 72px;
    height: 72px;
    margin: 0 auto 16px;
    border-radius: 18px;
    display: grid;
    place-items: center;
    background: radial-gradient(circle at 30% 30%, rgba(120,131,255,0.35), rgba(118,75,162,0.25));
    color: var(--text-primary);
    font-size: 32px;
    box-shadow: 0 12px 30px rgba(118,75,162,0.12);
  }

  .emptyCard h2 {
    margin: 0 0 8px;
    font-size: 22px;
    color: var(--text-primary);
  }

  .emptyCard p {
    margin: 0 0 20px;
    color: var(--text-secondary);
    line-height: 1.5;
  }

  .emptyActions {
    display: flex;
    gap: 12px;
    justify-content: center;
    flex-wrap: wrap;
  }

  .emptyPrimaryBtn, .emptyGhostBtn {
    min-width: 140px;
    padding: 12px 18px;
    border-radius: 12px;
    border: 1px solid transparent;
    font-size: var(--font-size-base);
    font-weight: var(--font-weight-medium);
    cursor: pointer;
    transition: all var(--transition-base);
    box-shadow: var(--shadow-sm);
  }

  .emptyPrimaryBtn {
    background: linear-gradient(120deg, #667eea 0%, #764ba2 100%);
    color: white;
    border-color: rgba(255,255,255,0.18);
  }

  .emptyPrimaryBtn:hover {
    box-shadow: var(--shadow-md), 0 0 0 1px rgba(118,75,162,0.35);
    transform: translateY(-1px);
  }

  .emptyGhostBtn {
    background: transparent;
    border-color: var(--border-subtle);
    color: var(--text-primary);
  }

  .emptyGhostBtn:hover {
    border-color: var(--border-default);
    background: var(--surface-hover);
    transform: translateY(-1px);
  }

  /* Folder tabs overlay */
  .folderTabs {
    position: absolute;
    bottom: 10px;
    left: 12px;
    z-index: 5;
    display: none;
    flex-direction: column;
    gap: 4px;
    pointer-events: none;
    max-width: min(900px, calc(100% - 32px));
  }

  .folderTabs.show {
    display: flex;
  }

  .folderTabsToggle {
    position: absolute;
    bottom: 10px;
    left: 12px;
    z-index: 4;
    display: none;
    padding: 6px 10px;
    border-radius: 9px;
    border: 1px solid var(--border-subtle);
    background: var(--surface-elevated);
    color: var(--text-primary);
    box-shadow: var(--shadow-sm);
    cursor: pointer;
    font-size: 12px;
  }

  .folderTabsToggle.show {
    display: inline-flex;
    align-items: center;
    gap: 8px;
  }

  .folderTabsHeader {
    display: flex;
    align-items: center;
    gap: 6px;
    pointer-events: auto;
  }

  #folderTabsTitle {
    padding: 6px 10px;
    border-radius: 9px;
    background: var(--surface-elevated);
    color: var(--text-primary);
    font-size: 12px;
    letter-spacing: 0.01em;
    box-shadow: var(--shadow-xs);
    border: 1px solid var(--border-subtle);
  }

  body.light-mode #folderTabsTitle,
  [data-theme="light"] #folderTabsTitle {
    background: rgba(255, 255, 255, 0.9);
    color: #111;
  }

  #folderTabsClose {
    width: 22px;
    height: 22px;
    border-radius: 8px;
    border: 1px solid var(--border-subtle);
    background: var(--surface-elevated);
    color: var(--text-primary);
    cursor: pointer;
    box-shadow: var(--shadow-sm);
    pointer-events: auto;
    font-size: 12px;
    line-height: 1;
  }

  #folderTabsClose:hover {
    background: var(--surface-hover);
  }

  .folderTabsList {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    pointer-events: auto;
    overflow-x: hidden;
    overflow-y: auto;
    padding: 6px;
    background: var(--surface-elevated);
    border: 1px solid var(--border-subtle);
    border-radius: 10px;
    box-shadow: var(--shadow-sm);
    max-height: 112px;
  }

  .folderTabsList::-webkit-scrollbar {
    width: 6px;
    height: 6px;
  }

  .folderTabsList::-webkit-scrollbar-thumb {
    background: var(--border-default);
    border-radius: 6px;
  }

  .folderTabsList::-webkit-scrollbar-track {
    background: transparent;
  }

  .folderTab {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 6px 8px;
    border-radius: 8px;
    border: 1px solid var(--border-subtle);
    background: var(--bg-secondary);
    color: var(--text-primary);
    cursor: pointer;
    box-shadow: var(--shadow-xs);
    transition: all var(--transition-base);
    white-space: nowrap;
    font-size: 12px;
  }

  .folderTab:hover {
    transform: translateY(-1px);
    box-shadow: var(--shadow-md);
    border-color: var(--border-default);
  }

  .folderTab.active {
    background: linear-gradient(120deg, #667eea 0%, #764ba2 100%);
    color: white;
    border-color: rgba(255, 255, 255, 0.25);
    box-shadow: var(--shadow-md);
  }

  .folderTab small {
    opacity: 0.75;
    font-size: 12px;
  }

  /* Folder quick-switch overlay */
  #folderSwitchOverlay {
    position: absolute;
    top: 18px;
    left: 18px;
    right: 18px;
    max-width: 520px;
    background: rgba(18, 18, 20, 0.92);
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 16px;
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.4);
    padding: 14px;
    display: none;
    gap: 12px;
    z-index: 20;
    pointer-events: auto;
    backdrop-filter: blur(14px) saturate(140%);
    -webkit-backdrop-filter: blur(14px) saturate(140%);
  }

  [data-theme="light"] #folderSwitchOverlay,
  body.light-mode #folderSwitchOverlay {
    background: rgba(255, 255, 255, 0.92);
    border-color: rgba(0, 0, 0, 0.12);
    box-shadow: 0 18px 48px rgba(0, 0, 0, 0.18);
  }

  #folderSwitchOverlay.show {
    display: flex;
    flex-direction: column;
  }

  .fsHeader {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .fsLabel {
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-secondary);
  }

  .fsTitle {
    font-size: 18px;
    font-weight: 700;
    color: var(--text-primary);
  }

  .fsMeta {
    font-size: 13px;
    color: var(--text-secondary);
  }

  .fsClose {
    margin-left: auto;
    width: 32px;
    height: 32px;
    border-radius: 10px;
    border: 1px solid var(--border-subtle);
    background: var(--surface-elevated);
    color: var(--text-primary);
    cursor: pointer;
    display: grid;
    place-items: center;
    transition: all var(--transition-base);
  }

  .fsClose:hover {
    border-color: var(--border-default);
    background: var(--surface-hover);
  }

  .fsList {
    margin-top: 10px;
    border: 1px solid var(--border-subtle);
    border-radius: 12px;
    overflow: hidden;
    background: var(--surface-elevated);
    max-height: 280px;
    overflow-y: auto;
  }

  .fsItem {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 12px;
    cursor: pointer;
    transition: background 0.12s ease, transform 0.08s ease;
  }

  .fsItem:hover {
    background: var(--surface-hover);
    transform: translateY(-1px);
  }

  .fsItemIcon {
    width: 28px;
    height: 28px;
    border-radius: 8px;
    display: grid;
    place-items: center;
    background: rgba(102, 126, 234, 0.16);
    color: var(--text-primary);
    font-size: 14px;
  }

  .fsItemText {
    flex: 1;
    min-width: 0;
  }

  .fsItemName {
    font-size: 14px;
    color: var(--text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .fsItemSub {
    font-size: 12px;
    color: var(--text-secondary);
  }

  .fsFooter {
    margin-top: 10px;
    display: flex;
    justify-content: flex-end;
  }

  .fsLibraryBtn {
    padding: 10px 14px;
    border-radius: 10px;
    border: 1px solid var(--border-subtle);
    background: var(--surface-elevated);
    color: var(--text-primary);
    cursor: pointer;
    transition: all var(--transition-base);
  }

  .fsLibraryBtn:hover {
    border-color: var(--border-default);
    background: var(--surface-hover);
  }
  
  #pdfFrame {
    position: relative;
    z-index: 1;
    width: 100%;
    height: 100%;
    border: 0;
    background: var(--bg-tertiary);
    /* GPU acceleration for smooth zoom and scroll */
    will-change: transform;
    transform: translateZ(0);
    -webkit-backface-visibility: hidden;
    backface-visibility: hidden;
    /* Optimize filter rendering during zoom */
    image-rendering: -webkit-optimize-contrast;
    image-rendering: crisp-edges;
    /* Ensure iframe is always interactive */
    pointer-events: auto !important;
  }
  
  /* PDF viewer frame - NO inversion by default, keep original colors */
  body.dark-mode #pdfFrame,
  body.light-mode #pdfFrame {
    filter: none !important;
    -webkit-filter: none !important;
    background: var(--bg-tertiary);
  }
  
  /* ============================================
     BUTTONS - Professional design system
     ============================================ */
  .bigBtn {
    font-size: var(--font-size-base);
    padding: var(--spacing-sm) var(--spacing-md);
    border-radius: var(--radius-lg);
    border: 1px solid var(--border-subtle);
    background: var(--surface-elevated);
    color: var(--text-secondary);
    cursor: pointer;
    font-family: var(--font-family);
    font-weight: var(--font-weight-medium);
    transition: all var(--transition-base);
    position: relative;
    transform: translateY(0) scale(1);
    box-shadow: var(--shadow-xs);
    opacity: 0;
    animation: buttonFadeIn var(--transition-spring) forwards;
    backdrop-filter: blur(10px);
    -webkit-backdrop-filter: blur(10px);
  }
  
  .bigBtn:hover {
    background: var(--surface-hover);
    border-color: var(--border-default);
    transform: translateY(-1px) scale(1.01);
    box-shadow: var(--shadow-md), 0 0 0 1px var(--accent-purple-glow);
    color: var(--text-primary);
  }
  
  .bigBtn:active {
    transform: translateY(0) scale(0.99);
    transition: transform var(--transition-fast);
    box-shadow: var(--shadow-xs);
  }
  
  /* 8. Enhanced Disabled Button States */
  .bigBtn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
    pointer-events: none;
    filter: grayscale(0.3);
    transform: none !important;
  }
  
  @keyframes buttonFadeIn {
    from {
      opacity: 0;
      transform: translateY(8px) scale(0.97);
    }
    to {
      opacity: 1;
      transform: translateY(0) scale(1);
    }
  }

  /* Quiz Me Button - Prominent styling */
  .quizMeBtn {
    display: flex;
    align-items: center;
    padding: 8px 14px;
    background: linear-gradient(135deg, var(--accent-purple) 0%, var(--accent-purple-dark) 100%);
    border: none;
    border-radius: 8px;
    color: white;
    font-size: 13px;
    font-weight: 600;
    font-family: var(--font-family);
    cursor: pointer;
    transition: all 0.2s ease;
    box-shadow: 0 2px 8px rgba(139, 92, 246, 0.3);
    white-space: nowrap;
  }
  
  .quizMeBtn:hover {
    background: linear-gradient(135deg, var(--accent-purple-dark) 0%, var(--accent-purple) 100%);
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(139, 92, 246, 0.4);
  }
  
  .quizMeBtn:active {
    transform: translateY(0);
    box-shadow: 0 2px 6px rgba(139, 92, 246, 0.3);
  }
  
  .quizMeBtn img {
    display: block;
    filter: brightness(0) invert(1);
  }
  
  /* Light mode styling for Quiz Me button */
  body.light-mode .quizMeBtn {
    background: linear-gradient(135deg, var(--accent-purple) 0%, var(--accent-purple-dark) 100%);
    box-shadow: 0 2px 8px rgba(139, 92, 246, 0.25);
  }
  
  body.light-mode .quizMeBtn:hover {
    background: linear-gradient(135deg, var(--accent-purple-dark) 0%, var(--accent-purple) 100%);
    box-shadow: 0 4px 12px rgba(139, 92, 246, 0.35);
  }

  /* ============================================
     SIDEBAR - Professional design
     ============================================ */
  #rightSidebar {
    position: absolute;
    top: 0;
    right: 0;
    height: 100%;
    width: var(--sidebar-w);
    background: var(--bg-secondary);
    overflow: hidden;
    /* Use transform3d for GPU acceleration - no reflow! */
    will-change: transform;
    -webkit-backface-visibility: hidden;
    backface-visibility: hidden;
    transform: translate3d(0, 0, 0);
    /* Smooth transform animation - transforms don't trigger reflow */
    transition: transform 0.3s cubic-bezier(0.25, 0.46, 0.45, 0.94),
                opacity 0.3s cubic-bezier(0.25, 0.46, 0.45, 0.94),
                background-color 0.8s ease,
                border-color 0.8s ease;
    opacity: 1;
    /* Isolate this element's layout to prevent reflow propagation */
    contain: layout style paint;
  }
  
  /* When sidebar is hidden, transform it off-screen (no reflow) */
  #work.sidebar-hidden #rightSidebar {
    transform: translate3d(100%, 0, 0);
    opacity: 0;
    pointer-events: none;
  }
  
  /* During animation, ensure sidebar is visible */
  #work.sidebar-animating #rightSidebar {
    pointer-events: auto;
  }
  
  /* Ensure toggle button area in sidebar is transparent */
  #rightSidebar > #rsScroll > #sidebarButtonsToggle {
    background: transparent !important;
  }
  
  /* Make sure toggle button area is completely transparent - no horizontal section */
  #sidebarButtonsContainer + #sidebarButtonsToggle {
    background: transparent !important;
  }
  
  /* Ensure the area around toggle button has no background */
  #rsScroll > #sidebarButtonsToggle {
    background: transparent !important;
  }
  
  /* Make sure container doesn't create a background section */
  /* #sidebarButtonsContainer background set dynamically via applyThemeColorsToAllUI */
  
  /* Sidebar resize handle */
  #sidebarResizeHandle {
    position: absolute;
    left: 0;
    top: 0;
    width: 4px;
    height: 100%;
    cursor: ew-resize;
    z-index: 100;
    background: transparent;
    transition: background 0.2s ease;
  }
  #sidebarResizeHandle:hover {
    background: rgba(139, 92, 246, 0.5);
  }
  #sidebarResizeHandle:active {
    background: rgba(139, 92, 246, 0.8);
  }
  
  /* Theme toggle button icon animation */
  #themeIcon {
    display: inline-block;
    transition: transform 0.4s cubic-bezier(0.68, -0.55, 0.27, 1.55),
                opacity 0.3s ease;
    font-size: 18px;
    line-height: 1;
  }
  
  /* Light mode styles - Elegant & Refined */
  body.light-mode {
    background: #f8f9fa;
    color: #2d2d30;
  }
  body.light-mode #rightSidebar {
    background: var(--theme-light);
    border-left: 1px solid rgba(0, 0, 0, 0.08);
    box-shadow: -1px 0 0 rgba(0, 0, 0, 0.04);
  }
  
  body.dark-mode #rightSidebar {
    background: var(--theme-dark);
  }
  
  /* Make toggle button area transparent in both themes */
  body.light-mode #rsScroll > #sidebarButtonsToggle,
  body.dark-mode #rsScroll > #sidebarButtonsToggle {
    background: transparent !important;
  }
  
  body.light-mode #sidebarButtonsContainer ~ #sidebarButtonsToggle,
  body.dark-mode #sidebarButtonsContainer ~ #sidebarButtonsToggle {
    background: transparent !important;
  }
  body.light-mode .rsBox {
    background: #ffffff;
    color: #2d2d30;
    border: 1px solid rgba(0, 0, 0, 0.06);
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04), 0 1px 2px rgba(0, 0, 0, 0.02);
  }
  body.light-mode .rsBox strong {
    color: #1d1d1f;
    font-weight: 600;
  }
  body.light-mode .rsEmpty {
    color: rgba(0, 0, 0, 0.5) !important;
  }
  body.light-mode .bigBtn {
    background: rgba(0, 0, 0, 0.04) !important;
    border: 1px solid rgba(0, 0, 0, 0.12) !important;
    color: rgba(0, 0, 0, 0.85) !important;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  }
  body.light-mode .bigBtn:hover {
    background: rgba(0, 0, 0, 0.08) !important;
    border-color: rgba(0, 0, 0, 0.18) !important;
    color: rgba(0, 0, 0, 0.95) !important;
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.08);
    transform: translateY(-0.5px);
  }
  body.light-mode #sidebarToggleBtn {
    background: rgba(248, 249, 250, 0.95);
    border-left-color: rgba(0, 0, 0, 0.08);
    color: #2d2d30;
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
  }
  body.light-mode .rsSection h3 {
    color: rgba(0, 0, 0, 0.85) !important;
    font-weight: 600;
    letter-spacing: -0.2px;
  }
  /* Light mode flashcards */
  body.light-mode .fcItem {
    background: #ffffff;
    border: 1px solid rgba(0, 0, 0, 0.1);
    color: #2d2d30;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05), 0 1px 2px rgba(0, 0, 0, 0.03);
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  }
  body.light-mode .fcItem:hover {
    border-color: rgba(0, 0, 0, 0.15);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08), 0 2px 4px rgba(0, 0, 0, 0.04);
    transform: translateY(-1px);
  }
  body.light-mode .fcItem b {
    color: #1d1d1f;
    font-weight: 600;
  }
  body.light-mode .fcItem span {
    color: #424245;
  }
  body.light-mode .fcItem .fcActions button {
    background: transparent !important;
    color: rgba(0, 0, 0, 0.6) !important;
    border: none !important;
    transition: all 0.2s ease;
  }
  body.light-mode .fcItem .fcActions button:hover {
    color: rgba(0, 0, 0, 0.9) !important;
    background: rgba(0, 0, 0, 0.06) !important;
    transform: scale(1.05);
  }
  
  body.light-mode .fcItem .fcActions .deleteBtn {
    color: rgba(0, 0, 0, 0.7) !important;
  }
  
  body.light-mode .fcItem .fcActions .deleteBtn:hover {
    color: rgba(220, 38, 38, 0.9) !important;
  }
  
  body.light-mode .fcItem .copyBtn {
    color: rgba(0, 0, 0, 0.7) !important;
    filter: brightness(0) invert(0.3) !important;
  }
  
  body.light-mode .fcItem .copyBtn:hover {
    color: rgba(0, 0, 0, 0.95) !important;
    filter: brightness(0) invert(0.5) !important;
  }
  
  body.light-mode .rsBox .copyBtn {
    color: rgba(0, 0, 0, 0.7) !important;
    filter: brightness(0) invert(0.3) !important;
  }
  
  body.light-mode .rsBox .copyBtn:hover {
    color: rgba(0, 0, 0, 0.95) !important;
    filter: brightness(0) invert(0.5) !important;
  }
  
  body.light-mode .rsBox .copyBtn.copied::before {
    color: #22c55e;
  }
  
  body.light-mode .fcItem .copyBtn.copied::before {
    color: #22c55e;
  }
  /* Icon buttons - monochrome styling (dark mode is default) */
  .iconBtn {
    color: rgba(255, 255, 255, 0.7);
    filter: grayscale(1);
    position: relative;
  }
  .iconBtn:hover {
    color: rgba(255, 255, 255, 0.9);
  }
  /* Icon buttons - monochrome styling for light mode */
  body.light-mode .iconBtn {
    color: rgba(0, 0, 0, 0.65) !important;
    filter: grayscale(1) brightness(0.4) !important;
    transition: all 0.2s ease;
  }
  body.light-mode .iconBtn:hover {
    color: rgba(0, 0, 0, 0.85) !important;
    filter: grayscale(1) brightness(0.3) !important;
    background: rgba(0, 0, 0, 0.05) !important;
  }
  /* macOS native-style tooltip - appended to body to always be on top */
  .macTooltip {
    position: fixed;
    padding: 6px 10px;
    background: rgba(0, 0, 0, 0.85);
    color: rgba(255, 255, 255, 0.95);
    border: none;
    border-radius: 6px;
    font-size: 11px;
    font-weight: 500;
    white-space: nowrap;
    pointer-events: none;
    z-index: 2147483647 !important;
    box-shadow: 0 2px 12px rgba(0, 0, 0, 0.4), 0 0 0 0.5px rgba(255, 255, 255, 0.1);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    opacity: 0;
    animation: tooltipFadeIn 0.2s ease-out forwards;
    letter-spacing: -0.2px;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", Helvetica, Arial, sans-serif;
  }
  @keyframes tooltipFadeIn {
    from {
      opacity: 0;
      transform: translateX(-50%) translateY(4px);
    }
    to {
      opacity: 1;
      transform: translateX(-50%) translateY(0);
    }
  }
  /* Theme toggle button removed - using PDF toolbar button instead */
  #rsScroll { 
    position: relative; 
    inset: 0; 
    left: 4px; /* Make room for resize handle */
    display: flex;
    flex-direction: column;
    overflow: hidden;
    -webkit-overflow-scrolling: touch; /* Smooth scrolling on Mac */
    background: transparent !important;
    height: 100%;
  }
  
  /* Make the area around toggle button completely transparent */
  #rsScroll > #sidebarButtonsToggle {
    background: transparent !important;
  }
  
  /* Ensure no background section between buttons and toggle */
  #sidebarButtonsContainer ~ #sidebarButtonsToggle {
    background: transparent !important;
  }
  
  /* Fixed sidebar buttons section at top - Elegant Design */
  #sidebarButtons {
    flex-shrink: 0;
    padding: 16px 18px;
    /* Background set dynamically via applyThemeColorsToAllUI */
    border-bottom: 1px solid rgba(255, 255, 255, 0.1);
    border-top: 1px solid rgba(255, 255, 255, 0.05);
    position: sticky;
    top: 0;
    z-index: 10;
    backdrop-filter: blur(20px) saturate(180%);
    -webkit-backdrop-filter: blur(20px) saturate(180%);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1), 
                inset 0 1px 0 rgba(255, 255, 255, 0.1);
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    align-items: center;
    justify-content: space-between;
  }
  
  [data-theme="light"] #sidebarButtons,
  body.light-mode #sidebarButtons {
    /* Background set dynamically via applyThemeColorsToAllUI - removed hardcoded white */
    border-bottom: 1px solid rgba(0, 0, 0, 0.08) !important;
    border-top: 1px solid rgba(0, 0, 0, 0.04) !important;
    backdrop-filter: blur(20px) saturate(180%);
    -webkit-backdrop-filter: blur(20px) saturate(180%);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06), 
                inset 0 1px 0 rgba(255, 255, 255, 0.8);
    transition: transform 0.35s cubic-bezier(0.4, 0, 0.2, 1),
                opacity 0.35s cubic-bezier(0.4, 0, 0.2, 1),
                max-height 0.35s cubic-bezier(0.4, 0, 0.2, 1),
                margin-bottom 0.35s cubic-bezier(0.4, 0, 0.2, 1),
                padding 0.35s cubic-bezier(0.4, 0, 0.2, 1);
    overflow: visible;
    max-height: 200px;
    margin-bottom: 0;
  }
  
  #sidebarButtonsContainer {
    overflow: visible;
    /* Background set dynamically via applyThemeColorsToAllUI */
    padding: 0 !important;
    margin: 0 !important;
  }
  
  /* Ensure no background section around toggle button */
  #sidebarButtonsContainer ~ * {
    background: transparent !important;
  }
  
  
  #sidebarButtons.hidden {
    transform: translateY(-100%);
    opacity: 0;
    max-height: 0;
    padding: 0 16px;
    border-bottom: none;
    margin-bottom: -8px;
  }
  
  /* Elegant collapse toggle button */
  #sidebarButtonsToggle {
    position: absolute;
    top: 85px;
    left: 50%;
    transform: translateX(-50%);
    width: 32px;
    height: 20px;
    padding: 0;
    margin: 0;
    background: transparent !important;
    border: none !important;
    border-radius: 10px;
    cursor: pointer;
    z-index: 12;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: transform 0.35s cubic-bezier(0.4, 0, 0.2, 1),
                opacity 0.35s cubic-bezier(0.4, 0, 0.2, 1),
                top 0.35s cubic-bezier(0.4, 0, 0.2, 1);
    box-shadow: none !important;
    backdrop-filter: none !important;
    -webkit-backdrop-filter: none !important;
  }
  
  #sidebarButtonsToggle::after {
    content: '';
    position: absolute;
    inset: 0;
    background: transparent;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 10px;
    pointer-events: none;
    z-index: 1;
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    box-shadow: none;
  }
  
  #sidebarButtonsToggle:hover::after {
    background: rgba(255, 255, 255, 0.05);
    border-color: rgba(255, 255, 255, 0.15);
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.15);
  }
  
  [data-theme="light"] #sidebarButtonsToggle::after {
    background: transparent;
    border-color: rgba(0, 0, 0, 0.08);
  }
  
  [data-theme="light"] #sidebarButtonsToggle:hover::after {
    background: rgba(0, 0, 0, 0.04);
    border-color: rgba(0, 0, 0, 0.12);
  }
  
  /* When buttons are hidden, move toggle button up */
  #sidebarButtons.hidden ~ #sidebarButtonsToggle,
  #sidebarButtonsContainer:has(#sidebarButtons.hidden) ~ #sidebarButtonsToggle,
  #sidebarButtonsToggle.buttons-hidden {
    top: 8px;
  }
  
  #sidebarButtonsToggle:hover {
    transform: translateX(-50%) scale(1.05);
  }
  
  /* Elegant chevron icon */
  #sidebarButtonsToggle::before {
    content: '';
    width: 6px;
    height: 6px;
    border-right: 1.5px solid rgba(255, 255, 255, 0.5);
    border-bottom: 1.5px solid rgba(255, 255, 255, 0.5);
    transform: rotate(-135deg);
    transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1), 
                border-color 0.2s ease;
    margin-top: -2px;
  }
  
  /* When buttons are hidden, chevron points down */
  #sidebarButtons.hidden ~ #sidebarButtonsToggle::before {
    transform: rotate(45deg);
    margin-top: 2px;
  }
  
  #sidebarButtonsContainer:has(#sidebarButtons.hidden) ~ #sidebarButtonsToggle::before {
    transform: rotate(45deg);
    margin-top: 2px;
  }
  
  #sidebarButtonsToggle:hover::before {
    border-color: rgba(255, 255, 255, 0.8);
  }
  
  /* Light mode toggle */
  [data-theme="light"] #sidebarButtonsToggle {
    background: transparent !important;
  }
  
  [data-theme="light"] #sidebarButtonsToggle::before {
    border-right-color: rgba(0, 0, 0, 0.4);
    border-bottom-color: rgba(0, 0, 0, 0.4);
  }
  
  [data-theme="light"] #sidebarButtonsToggle:hover::before {
    border-right-color: rgba(0, 0, 0, 0.7);
    border-bottom-color: rgba(0, 0, 0, 0.7);
  }
  
  [data-theme="light"] #sidebarButtons.hidden ~ #sidebarButtonsToggle::before {
    transform: rotate(45deg);
    margin-top: 2px;
  }
  
  /* Scrollable content section below buttons */
  #rsScrollContent {
    flex: 1;
    overflow-y: auto;
    overflow-x: hidden;
    padding: 16px 18px 20px;
    -webkit-overflow-scrolling: touch;
    background: transparent;
  }
  
  /* 5. Custom Scrollbar Styling */
  #rsScrollContent::-webkit-scrollbar {
    width: 8px;
  }
  
  #rsScrollContent::-webkit-scrollbar-track {
    background: transparent;
  }
  
  #rsScrollContent::-webkit-scrollbar-thumb {
    background: rgba(255, 255, 255, 0.1);
    border-radius: 4px;
    transition: background 0.2s ease;
  }
  
  #rsScrollContent::-webkit-scrollbar-thumb:hover {
    background: rgba(255, 255, 255, 0.2);
  }
  
  body.light-mode #rsScrollContent::-webkit-scrollbar-thumb {
    background: rgba(0, 0, 0, 0.15);
  }
  
  body.light-mode #rsScrollContent::-webkit-scrollbar-thumb:hover {
    background: rgba(0, 0, 0, 0.25);
  }
  
  /* Firefox scrollbar */
  #rsScrollContent {
    scrollbar-width: thin;
    scrollbar-color: rgba(255, 255, 255, 0.1) transparent;
  }
  
  body.light-mode #rsScrollContent {
    scrollbar-color: rgba(0, 0, 0, 0.15) transparent;
  }
  
  /* 6. Custom Text Selection */
  ::selection {
    background: var(--accent-purple-glow);
    color: var(--text-primary);
  }
  
  ::-moz-selection {
    background: var(--accent-purple-glow);
    color: var(--text-primary);
  }
  
  body.light-mode ::selection {
    background: rgba(139, 92, 246, 0.2);
    color: var(--text-primary-light);
  }
  
  body.light-mode ::-moz-selection {
    background: rgba(139, 92, 246, 0.2);
    color: var(--text-primary-light);
  }
  
  /* Fixed Ask AI section at bottom - Elegant Design */
  #aiSectionFixed {
    position: sticky;
    bottom: 0;
    flex-shrink: 0;
    padding: 0;
    background: linear-gradient(0deg, 
      rgba(255, 255, 255, 0.08) 0%, 
      rgba(255, 255, 255, 0.04) 50%,
      rgba(255, 255, 255, 0.02) 100%);
    border-top: 1px solid rgba(255, 255, 255, 0.1);
    border-bottom: 1px solid rgba(255, 255, 255, 0.05);
    box-shadow: 0 -2px 8px rgba(0, 0, 0, 0.1), 
                inset 0 -1px 0 rgba(255, 255, 255, 0.1);
    z-index: 10;
    backdrop-filter: blur(20px) saturate(180%);
    -webkit-backdrop-filter: blur(20px) saturate(180%);
  }
  
  #aiSectionContent {
    padding: 16px 18px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  
  #aiHeader {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 4px;
  }
  
  #aiHeader h3 {
    margin: 0;
    font-size: 14px;
    font-weight: 600;
    color: var(--text-secondary);
    letter-spacing: -0.01em;
  }
  
  body.light-mode #aiHeader h3 {
    color: var(--text-secondary-light);
  }
  
  #aiInputWrapper {
    position: relative;
    display: flex;
    align-items: center;
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid var(--border-subtle);
    border-radius: 10px;
    padding: 2px;
    transition: all 0.2s ease;
    gap: 2px;
  }
  
  #aiInputWrapper:focus-within {
    background: rgba(255, 255, 255, 0.05);
    border-color: var(--accent-purple);
    box-shadow: 0 0 0 2px var(--accent-purple-glow);
  }
  
  /* 3. Enhanced Input Focus States */
  #aiQuestion {
    flex: 1;
    padding: 10px 12px;
    background: transparent;
    border: none;
    color: var(--text-secondary);
    font-size: 13px;
    font-family: inherit;
    outline: none;
    line-height: 1.4;
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  }
  
  /* 9. Styled Placeholder Text */
  #aiQuestion::placeholder {
    color: var(--text-quaternary);
    opacity: 0.7;
    transition: opacity 0.2s ease;
  }
  
  #aiInputWrapper:focus-within #aiQuestion::placeholder {
    opacity: 0.5;
  }
  
  /* Enhanced focus glow for input wrapper */
  #aiInputWrapper:focus-within {
    box-shadow: 0 0 0 3px rgba(139, 92, 246, 0.15), 0 0 0 1px var(--accent-purple);
  }
  
  .aiSendBtn {
    flex-shrink: 0;
    width: 36px;
    height: 36px;
    padding: 0;
    background: transparent;
    border: none;
    border-radius: 8px;
    color: var(--text-secondary);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.2s ease;
    opacity: 0.6;
  }
  
  .aiSendBtn:hover {
    background: var(--accent-purple);
    border: none;
    color: white;
    transform: translateY(-1px);
    box-shadow: 0 2px 8px rgba(139, 92, 246, 0.25);
    opacity: 1;
  }
  
  .aiSendBtn:active {
    transform: translateY(0);
    box-shadow: var(--shadow-xs);
  }
  
  .aiSendBtn svg {
    width: 16px;
    height: 16px;
    stroke-width: 2.5;
  }
  
  .aiAnswerBox {
    min-height: 60px;
    max-height: 200px;
    overflow-y: auto;
    padding: 12px 14px;
    background: rgba(255, 255, 255, 0.02);
    border: 1px solid var(--border-subtle);
    border-radius: 8px;
    color: var(--text-secondary);
    font-size: 13px;
    line-height: 1.6;
    white-space: pre-wrap;
    word-wrap: break-word;
    transition: all 0.2s ease;
  }
  
  .aiAnswerBox:not(.rsEmpty) {
    background: rgba(255, 255, 255, 0.03);
    border-color: var(--border-subtle);
  }
  
  .aiAnswerBox::-webkit-scrollbar {
    width: 6px;
  }
  
  .aiAnswerBox::-webkit-scrollbar-track {
    background: transparent;
  }
  
  .aiAnswerBox::-webkit-scrollbar-thumb {
    background: rgba(255, 255, 255, 0.1);
    border-radius: 3px;
  }
  
  .aiAnswerBox::-webkit-scrollbar-thumb:hover {
    background: rgba(255, 255, 255, 0.15);
  }
  
  body.light-mode .aiAnswerBox::-webkit-scrollbar-thumb {
    background: rgba(0, 0, 0, 0.15);
  }
  
  body.light-mode .aiAnswerBox::-webkit-scrollbar-thumb:hover {
    background: rgba(0, 0, 0, 0.25);
  }
  
  #aiActions {
    display: flex;
    align-items: center;
    gap: 8px;
    opacity: 0;
    transition: opacity 0.2s ease;
  }
  
  #aiActions:not(:empty) {
    opacity: 1;
  }
  
  .aiActionBtn {
    padding: 6px 10px;
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid var(--border-subtle);
    border-radius: 6px;
    color: var(--text-tertiary);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.2s ease;
    font-size: 12px;
  }
  
  .aiActionBtn:hover {
    background: rgba(255, 255, 255, 0.08);
    color: var(--text-secondary);
    border-color: var(--border-subtle);
  }
  
  /* Light mode styles for AI section */
  body.light-mode #aiSectionFixed {
    background: linear-gradient(0deg, 
      rgba(255, 255, 255, 0.95) 0%, 
      rgba(255, 255, 255, 0.9) 50%,
      rgba(255, 255, 255, 0.85) 100%);
    border-top: 1px solid rgba(0, 0, 0, 0.08);
    border-bottom: 1px solid rgba(0, 0, 0, 0.04);
    box-shadow: 0 -2px 8px rgba(0, 0, 0, 0.06), 
                inset 0 -1px 0 rgba(255, 255, 255, 0.8);
  }
  
  body.light-mode #aiInputWrapper {
    background: rgba(0, 0, 0, 0.02);
    border-color: rgba(0, 0, 0, 0.1);
  }
  
  body.light-mode #aiInputWrapper:focus-within {
    background: rgba(0, 0, 0, 0.03);
    border-color: var(--accent-purple);
    box-shadow: 0 0 0 2px rgba(139, 92, 246, 0.15);
  }
  
  body.light-mode #aiQuestion {
    color: var(--text-primary-light);
  }
  
  body.light-mode #aiQuestion::placeholder {
    color: var(--text-quaternary-light);
  }
  
  body.light-mode .aiSendBtn {
    background: transparent;
    border: none;
    color: var(--text-secondary-light);
    opacity: 0.6;
  }
  
  body.light-mode .aiSendBtn:hover {
    background: var(--accent-purple);
    border: none;
    color: white;
    box-shadow: 0 2px 8px rgba(139, 92, 246, 0.3);
    opacity: 1;
  }
  
  body.light-mode .aiAnswerBox {
    background: rgba(0, 0, 0, 0.02);
    border-color: rgba(0, 0, 0, 0.1);
    color: var(--text-primary-light);
  }
  
  body.light-mode .aiAnswerBox:not(.rsEmpty) {
    background: rgba(0, 0, 0, 0.03);
  }
  
  body.light-mode .aiActionBtn {
    background: rgba(0, 0, 0, 0.03);
    border-color: rgba(0, 0, 0, 0.1);
    color: var(--text-tertiary-light);
  }
  
  body.light-mode .aiActionBtn:hover {
    background: rgba(0, 0, 0, 0.05);
    color: var(--text-secondary-light);
    border-color: rgba(0, 0, 0, 0.15);
  }
  
  /* Ensure toggle button sticky area is transparent */
  #sidebarButtonsToggle {
    background: transparent !important;
  }
  
  /* Make sure no parent creates background for sticky toggle */
  #rsScroll > #sidebarButtonsToggle {
    background: transparent !important;
  }
  
  #rsScrollContent::-webkit-scrollbar {
    width: 8px;
  }
  
  #rsScrollContent::-webkit-scrollbar-track {
    background: transparent;
  }
  
  #rsScrollContent::-webkit-scrollbar-thumb {
    background: rgba(255, 255, 255, 0.1);
    border-radius: 4px;
  }
  
  #rsScrollContent::-webkit-scrollbar-thumb:hover {
    background: rgba(139, 92, 246, 0.4);
  }
  
  [data-theme="light"] #rsScrollContent::-webkit-scrollbar-thumb {
    background: rgba(0, 0, 0, 0.15);
  }
  
  [data-theme="light"] #rsScrollContent::-webkit-scrollbar-thumb:hover {
    background: rgba(139, 92, 246, 0.4);
  }
  /* Duplicate removed - handled above with translate3d */
  
  /* Sidebar toggle button - synchronized with sidebar animation */
  #sidebarToggleBtn {
    position: fixed;
    right: 0;
    top: 50%;
    transform: translateY(-50%) translateX(0) translateZ(0);
    width: 28px;
    height: 80px;
    background: rgba(42, 42, 44, 0.9);
    backdrop-filter: blur(10px);
    border: none;
    border-left: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 12px 0 0 12px; /* Always same shape - rounded on left */
    color: #e8e8ea;
    font-size: 18px;
    cursor: pointer;
    z-index: 1000;
    display: flex;
    align-items: center;
    justify-content: center;
    will-change: transform;
    -webkit-backface-visibility: hidden;
    backface-visibility: hidden;
    /* Match sidebar animation timing exactly */
    transition: transform 0.3s cubic-bezier(0.25, 0.46, 0.45, 0.94),
                background 0.15s ease,
                box-shadow 0.15s ease,
                opacity 0.15s ease;
    box-shadow: -2px 0 12px rgba(0, 0, 0, 0.3);
    opacity: 0.8;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
  }
  #sidebarToggleBtn:hover {
    background: rgba(51, 51, 53, 0.95);
    opacity: 1;
    box-shadow: -3px 0 16px rgba(0, 0, 0, 0.4);
  }
  #sidebarToggleBtn:active {
    transform: translateY(-50%) translateX(0) scale(0.98) translateZ(0);
  }
  /* Move with sidebar - use transform like sidebar does, same duration and easing */
  #work:not(.sidebar-hidden) #sidebarToggleBtn {
    transform: translateY(-50%) translateX(calc(-1 * var(--sidebar-w))) translateZ(0);
  }
  #work:not(.sidebar-hidden) #sidebarToggleBtn:hover {
    transform: translateY(-50%) translateX(calc(-1 * var(--sidebar-w) - 2px)) translateZ(0);
  }
  #work:not(.sidebar-hidden) #sidebarToggleBtn:active {
    transform: translateY(-50%) translateX(calc(-1 * var(--sidebar-w))) scale(0.98) translateZ(0);
  }
  /* Disable transition during resize for instant following */
  #sidebarToggleBtn.resizing {
    transition: none !important;
  }
  /* Icon - change arrow direction only */
  #sidebarToggleBtn::before {
    content: '▶'; /* Right arrow when sidebar visible - points right to hide */
    display: block;
    will-change: transform;
    transition: transform 0.2s cubic-bezier(0.25, 0.46, 0.45, 0.94);
    transform: rotate(0deg) translateZ(0);
  }
  #work.sidebar-hidden #sidebarToggleBtn::before {
    content: '◀'; /* Left arrow when sidebar hidden - points left to show */
    transform: rotate(0deg) translateZ(0); /* No rotation, just change content */
  }
  
  /* Sidebar buttons - icon with text, always colored - more elegant */
  #sidebarButtons .bigBtn { 
    font-size: 15px; 
    padding: 11px 18px; 
    border-radius: 10px; 
    font-weight: 500;
    letter-spacing: 0.02em;
    display: flex;
    align-items: center;
    justify-content: flex-start;
    transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
    background: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.12);
    color: rgba(255, 255, 255, 0.95);
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.12);
    position: relative;
    overflow: hidden;
    height: 42px;
    line-height: 1;
    filter: grayscale(0%) brightness(1);
    cursor: pointer;
    font-variant-emoji: text;
    -webkit-font-smoothing: antialiased;
    gap: 10px;
    white-space: nowrap;
    min-width: 100px;
  }
  
  /* Text label inside button */
  #sidebarButtons .bigBtn .btn-label {
    font-size: 13px;
    font-weight: 500;
    opacity: 0.95;
    transition: opacity 0.25s cubic-bezier(0.4, 0, 0.2, 1);
    letter-spacing: 0.3px;
  }
  
  /* Icon in button */
  #sidebarButtons .bigBtn .btn-icon {
    font-size: 18px;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 18px;
    height: 18px;
    background-size: contain;
    background-repeat: no-repeat;
    background-position: center;
  }
  
  /* Akson button icon - keep original colors */
  #sidebarButtons #btnAkson .btn-icon {
    background-image: url('icons/akson.png');
  }
  
  /* Library button icon - monochrome */
  #sidebarButtons #btnLibrary .btn-icon {
    background-image: url('icons/library.svg');
    filter: brightness(0) invert(1) opacity(0.95);
  }
  
  /* Elegant subtle background glow on hover */
  #sidebarButtons .bigBtn::before {
    content: '';
    position: absolute;
    inset: 0;
    border-radius: 8px;
    background: linear-gradient(135deg, rgba(255, 255, 255, 0.12) 0%, rgba(255, 255, 255, 0.04) 100%);
    opacity: 0;
    transition: opacity 0.25s cubic-bezier(0.4, 0, 0.2, 1);
    pointer-events: none;
    z-index: 0;
  }
  
  /* Elegant hover effect - slight expansion */
  #sidebarButtons .bigBtn:hover { 
    background: rgba(255, 255, 255, 0.12);
    border-color: rgba(255, 255, 255, 0.2);
    color: rgba(255, 255, 255, 1);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
    transform: scale(1.04);
    padding-left: 20px;
    padding-right: 20px;
  }
  
  #sidebarButtons .bigBtn:hover .btn-label {
    opacity: 1;
  }
  
  #sidebarButtons .bigBtn:hover::before {
    opacity: 1;
  }
  
  /* Ensure content stays visible above background */
  #sidebarButtons .bigBtn > * {
    position: relative;
    z-index: 1;
  }
  
  #sidebarButtons .bigBtn:active { 
    transform: translateY(0) scale(0.98);
    transition: transform 0.15s ease;
  }
  
  /* Open dropdown */
  .openDropdown {
    position: relative;
  }

  .openDropdownMenu {
    position: absolute;
    top: 42px;
    right: 0;
    display: none;
    flex-direction: column;
    gap: 4px;
    padding: 8px;
    background: var(--surface-elevated);
    border: 1px solid var(--border-subtle);
    border-radius: 12px;
    box-shadow: var(--shadow-lg);
    z-index: 10;
  }

  .openDropdown.show .openDropdownMenu {
    display: flex;
  }

  .openDropdownMenu button {
    border: 1px solid var(--border-subtle);
    background: var(--bg-secondary);
    color: var(--text-primary);
    border-radius: 10px;
    padding: 10px 12px;
    cursor: pointer;
    transition: all var(--transition-base);
    text-align: left;
    min-width: 160px;
  }

  .openDropdownMenu button:hover {
    background: var(--surface-hover);
    border-color: var(--border-default);
  }

  /* Open File button - elegant icon-only styling (like Settings) */
  #sidebarButtons #btnOpenFile {
    width: 36px;
    height: 36px;
    padding: 0;
    margin: 0;
    background: transparent;
    border: none;
    border-radius: 0;
    font-size: 20px;
    display: flex;
    align-items: center;
    justify-content: center;
    color: rgba(255, 255, 255, 0.75);
    cursor: pointer;
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    font-variant-emoji: text;
    -webkit-font-smoothing: antialiased;
    position: relative;
    background-image: url('icons/open_folder.svg');
    background-size: 20px 20px;
    background-repeat: no-repeat;
    background-position: center;
    filter: brightness(0) invert(1) opacity(0.75);
  }
  
  #sidebarButtons #btnOpenFile::before {
    content: '';
    position: absolute;
    inset: 0;
    border-radius: 50%;
    background: rgba(255, 255, 255, 0.08);
    opacity: 0;
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    transform: scale(0.8);
  }
  
  #sidebarButtons #btnOpenFile:hover {
    color: rgba(255, 255, 255, 1);
    transform: scale(1.1);
    filter: brightness(0) invert(1) opacity(1);
  }
  
  #sidebarButtons #btnOpenFile:hover::before {
    opacity: 1;
    transform: scale(1);
  }
  
  #sidebarButtons #btnOpenFile:active {
    transform: scale(1.05);
  }
  
  /* Settings button - completely separate styling */
  #sidebarButtons #btnSettings {
    width: 36px;
    height: 36px;
    padding: 0;
    margin: 0;
    background: transparent;
    border: none;
    border-radius: 0;
    font-size: 20px;
    display: flex;
    align-items: center;
    justify-content: center;
    color: rgba(255, 255, 255, 0.75);
    cursor: pointer;
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    font-variant-emoji: text;
    -webkit-font-smoothing: antialiased;
    position: relative;
    background-image: url('icons/settings.svg');
    background-size: 20px 20px;
    background-repeat: no-repeat;
    background-position: center;
    filter: brightness(0) invert(1) opacity(0.75);
  }
  
  #sidebarButtons #btnSettings::before {
    content: '';
    position: absolute;
    inset: 0;
    border-radius: 50%;
    background: rgba(255, 255, 255, 0.08);
    opacity: 0;
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    transform: scale(0.8);
  }
  
  #sidebarButtons #btnSettings:hover {
    color: rgba(255, 255, 255, 1);
    transform: scale(1.1) rotate(90deg);
    filter: brightness(0) invert(1) opacity(1);
  }
  
  #sidebarButtons #btnSettings:hover::before {
    opacity: 1;
    transform: scale(1);
  }
  
  #sidebarButtons #btnSettings:active {
    transform: scale(1.05) rotate(90deg);
  }
  
  /* Light mode icon adjustments */
  [data-theme="light"] #sidebarButtons #btnLibrary .btn-icon {
    filter: brightness(0) invert(0) opacity(0.7);
  }
  
  [data-theme="light"] #sidebarButtons #btnLibrary:hover .btn-icon {
    filter: brightness(0) invert(0) opacity(0.9);
  }
  
  /* Light mode for sidebar buttons */
  [data-theme="light"] #sidebarButtons .bigBtn {
    background: rgba(0, 0, 0, 0.04);
    border: 1px solid rgba(0, 0, 0, 0.08);
    color: rgba(0, 0, 0, 0.8);
    filter: grayscale(0%) brightness(0.95);
  }
  
  [data-theme="light"] #sidebarButtons .bigBtn::before {
    background: linear-gradient(135deg, rgba(0, 0, 0, 0.08) 0%, rgba(0, 0, 0, 0.02) 100%);
  }
  
  [data-theme="light"] #sidebarButtons .bigBtn:hover {
    background: rgba(0, 0, 0, 0.08);
    border-color: rgba(0, 0, 0, 0.12);
    color: rgba(0, 0, 0, 0.95);
  }
  
  [data-theme="light"] #sidebarButtons #btnOpenFile {
    color: rgba(0, 0, 0, 0.7);
    filter: brightness(0) invert(0) opacity(0.7);
  }
  
  [data-theme="light"] #sidebarButtons #btnOpenFile::before {
    background: rgba(0, 0, 0, 0.06);
  }
  
  [data-theme="light"] #sidebarButtons #btnOpenFile:hover {
    color: rgba(0, 0, 0, 0.9);
    filter: brightness(0) invert(0) opacity(0.9);
  }
  
  [data-theme="light"] #sidebarButtons #btnSettings {
    color: rgba(0, 0, 0, 0.7);
    filter: brightness(0) invert(0) opacity(0.7);
  }
  
  [data-theme="light"] #sidebarButtons #btnSettings::before {
    background: rgba(0, 0, 0, 0.06);
  }
  
  [data-theme="light"] #sidebarButtons #btnSettings:hover {
    color: rgba(0, 0, 0, 0.9);
    filter: brightness(0) invert(0) opacity(0.9);
  }
  
  /* Light mode for sidebar buttons container */
  /* Background set dynamically via applyThemeColorsToAllUI - removed hardcoded white */
  [data-theme="light"] #sidebarButtons {
    border-bottom: 1px solid rgba(0, 0, 0, 0.06) !important;
    backdrop-filter: blur(20px) saturate(180%);
    -webkit-backdrop-filter: blur(20px) saturate(180%);
    box-shadow: 0 1px 0 rgba(0, 0, 0, 0.04);
  }
  
  /* Elegant Summary Controls Bar - Complete redesign */
  .summaryControlsBar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 12px;
    padding: 10px 14px;
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 10px;
    transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
    backdrop-filter: blur(10px);
    -webkit-backdrop-filter: blur(10px);
  }
  
  .summaryControlsBar:hover {
    background: rgba(255, 255, 255, 0.06);
    border-color: rgba(255, 255, 255, 0.12);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
    transform: translateY(-1px);
  }
  
  .summaryControlsLeft {
    display: flex;
    align-items: center;
    flex: 1;
  }
  
  .summaryControlsRight {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
  }
  
  /* Regenerate Controls */
  /* Compact Section Header - Clean, minimal layout */
  .sectionHeaderCompact {
    display: flex !important;
    justify-content: space-between !important;
    align-items: center !important;
    margin-bottom: 6px; /* Reduced from 10px for tighter spacing */
    gap: 10px;
    height: 28px !important; /* Fixed height for perfect alignment */
    min-height: 28px !important;
    max-height: 28px !important;
    margin-top: 0 !important;
    padding: 0 !important;
  }
  
  .sectionHeaderCompact h3 {
    margin: 0 !important;
    padding: 0 !important;
    font-size: 15px;
    font-weight: 600;
    color: var(--text-primary);
    flex: 1;
    min-width: 0;
    line-height: 1 !important;
    display: flex !important;
    align-items: center !important;
    height: 28px !important; /* Match button height for alignment */
    vertical-align: middle !important;
  }
  
  /* Ensure the left side container (with h3 and Quiz button) has proper alignment */
  .sectionHeaderCompact > div:first-child {
    display: flex !important;
    align-items: center !important;
    gap: 8px;
    height: 28px !important;
    min-height: 28px !important;
    max-height: 28px !important;
    margin: 0 !important;
    padding: 0 !important;
    line-height: 28px !important;
  }
  
  /* Summary dropdown container - ensure it aligns properly */
  .sectionHeaderCompact > .summaryDropdown {
    height: 28px !important;
    min-height: 28px !important;
    max-height: 28px !important;
    margin: 0 !important;
    padding: 0 !important;
    display: inline-flex !important;
    align-items: center !important;
    vertical-align: middle !important;
  }
  
  /* Summary Dropdown - Very subtle, looks like regular heading */
  .summaryDropdown {
    position: relative;
    display: inline-flex;
    align-items: center;
    gap: 4px;
    cursor: pointer;
    user-select: none;
    margin: 0;
    padding: 0;
    height: 28px; /* Match container height */
    min-height: 28px;
    max-height: 28px;
  }
  
  .summaryDropdown .summaryTitle {
    margin: 0;
    padding: 0;
    font-size: 15px;
    font-weight: 600;
    color: var(--text-primary);
    line-height: 1;
    cursor: pointer;
    display: flex;
    align-items: center;
    height: 28px;
  }
  
  .summaryDropdown .dropdownArrow {
    font-size: 10px;
    color: var(--text-secondary);
    opacity: 0.5;
    transition: opacity 0.2s ease, transform 0.2s ease;
    margin-left: 2px;
  }
  
  .summaryDropdown:hover .dropdownArrow {
    opacity: 0.8;
  }
  
  .summaryDropdown.active .dropdownArrow {
    transform: rotate(180deg);
    opacity: 1;
  }
  
  .summaryDropdownMenu {
    position: absolute;
    top: calc(100% + 4px);
    left: 0;
    background: var(--surface-elevated);
    border: 1px solid var(--border-subtle);
    border-radius: 8px;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.2);
    min-width: 140px;
    z-index: 1000;
    opacity: 0;
    visibility: hidden;
    transform: translateY(-4px);
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    overflow: hidden;
  }
  
  .summaryDropdown.active .summaryDropdownMenu {
    opacity: 1;
    visibility: visible;
    transform: translateY(0);
  }
  
  .summaryDropdownOption {
    padding: 8px 12px;
    font-size: 13px;
    color: var(--text-primary);
    cursor: pointer;
    transition: background 0.15s ease;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  
  .summaryDropdownOption:hover {
    background: var(--surface-hover);
  }
  
  .summaryDropdownOption.active {
    background: rgba(139, 92, 246, 0.15);
    color: var(--accent-purple);
    font-weight: 600;
  }
  
  .summaryDropdownOption .checkIcon {
    font-size: 12px;
    opacity: 0;
    transition: opacity 0.15s ease;
  }
  
  .summaryDropdownOption.active .checkIcon {
    opacity: 1;
  }
  
  .sectionHeaderControls {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 6px;
    flex-shrink: 0;
    height: 28px !important;
    min-height: 28px !important;
    max-height: 28px !important;
    margin-top: 0 !important;
    padding: 0 !important;
  }
  
  /* Ensure AI badge aligns properly - FORCE alignment */
  .sectionHeaderControls .autoGeneratedBadge,
  .sectionHeaderControls .autoGeneratedBadge.interactiveBadge {
    height: 28px !important;
    min-height: 28px !important;
    max-height: 28px !important;
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    margin: 0 !important;
    padding: 0 10px !important; /* No vertical padding */
    box-sizing: border-box !important;
    line-height: 1 !important;
    vertical-align: middle !important;
    position: relative !important;
    top: 0 !important;
    transform: none !important;
  }
  
  /* Override hover transform that might affect alignment */
  .sectionHeaderControls .autoGeneratedBadge:hover {
    transform: translateY(-1px) scale(1.02) !important; /* Less aggressive hover */
  }
  
  .sectionHeaderControls #summaryModeSwitch {
    height: 28px;
    margin: 0;
  }
  
  .sectionHeaderControls .sectionQuizBtnCompact {
    height: 28px;
    width: 28px;
    margin: 0;
  }
  
  /* Compact Section Actions - Single row, clean spacing */
  .sectionActionsCompact {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-top: 10px;
    flex-wrap: wrap;
  }
  
  /* Ensure regenerate button and input are aligned */
  .sectionActionsCompact .sectionExtraInput {
    height: 28px;
    box-sizing: border-box;
    margin-top: 6px; /* Bring down 2 more pixels */
  }
  
  .sectionActionsCompact .sectionRegenerateBtn {
    height: 28px;
  }
  
  .sectionActionsSpacer {
    flex: 1;
    min-width: 8px;
  }
  
  /* Section Action Buttons - Clean icon buttons */
  .sectionActionBtn {
    width: 28px;
    height: 28px;
    padding: 0;
    border: 1px solid var(--border-subtle);
    background: var(--surface-elevated);
    color: var(--text-secondary);
    border-radius: 6px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 16px;
    font-weight: 500;
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    font-family: var(--font-family);
    line-height: 1;
  }
  
  .sectionActionBtn span {
    display: block;
    line-height: 1;
  }
  
  .sectionActionBtn:hover {
    background: var(--surface-hover);
    border-color: var(--border-default);
    color: var(--text-primary);
    transform: translateY(-1px);
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.1);
  }
  
  .sectionActionBtn:active {
    transform: translateY(0);
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  }
  
  /* Quiz Button - Compact icon-only - Always accent color */
  .sectionQuizBtnCompact {
    width: 28px !important;
    height: 28px !important;
    min-height: 28px !important;
    max-height: 28px !important;
    padding: 0 !important;
    border: 1px solid var(--accent-purple);
    background: linear-gradient(135deg, var(--accent-purple) 0%, var(--accent-purple-dark) 100%);
    border-radius: 6px;
    cursor: pointer;
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    font-family: var(--font-family);
    margin: 0 !important;
    flex-shrink: 0 !important;
    vertical-align: middle !important;
    line-height: 1 !important;
    box-sizing: border-box !important;
    position: relative !important;
    top: 0 !important;
  }
  
  /* Ensure button and badge are aligned when in same container - use flexbox alignment */
  .sectionHeaderCompact {
    align-items: center !important;
  }
  
  .sectionHeaderCompact > div:first-child {
    align-items: center !important;
  }
  
  .sectionHeaderControls {
    align-items: center !important;
  }
  
  /* Force both button and badge to center align */
  .sectionHeaderCompact > div:first-child .sectionQuizBtnCompact,
  .sectionHeaderControls .autoGeneratedBadge {
    align-self: center !important;
    margin-top: 0 !important;
    margin-bottom: 0 !important;
  }
  
  .sectionQuizBtnCompact img {
    width: 16px;
    height: 16px;
    display: block;
    filter: brightness(0) invert(1); /* White icon */
    transition: filter 0.2s ease;
  }
  
  .sectionQuizBtnCompact:hover {
    transform: translateY(-1px);
    box-shadow: 0 2px 8px rgba(139, 92, 246, 0.4);
  }
  
  .sectionQuizBtnCompact:hover img {
    filter: brightness(0) invert(1) brightness(1.1);
  }
  
  .sectionQuizBtnCompact:active {
    transform: translateY(0);
    box-shadow: 0 1px 4px rgba(139, 92, 246, 0.3);
  }
  
  [data-theme="light"] .sectionQuizBtnCompact,
  body.light-mode .sectionQuizBtnCompact {
    border-color: var(--accent-purple);
    background: linear-gradient(135deg, var(--accent-purple) 0%, var(--accent-purple-dark) 100%);
  }
  
  [data-theme="light"] .sectionQuizBtnCompact img,
  body.light-mode .sectionQuizBtnCompact img {
    filter: brightness(0) invert(1); /* White icon in light mode too */
  }
  
  [data-theme="light"] .sectionQuizBtnCompact:hover,
  body.light-mode .sectionQuizBtnCompact:hover {
    box-shadow: 0 2px 8px rgba(139, 92, 246, 0.4);
  }
  
  /* Regenerate Button - Compact */
  .sectionRegenerateBtn {
    width: 28px;
    height: 28px;
    padding: 0;
    border: 1px solid var(--border-subtle);
    background: var(--surface-elevated);
    color: var(--text-secondary);
    border-radius: 6px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 14px;
    transition: background 0.2s ease, border-color 0.2s ease, transform 0.2s ease, box-shadow 0.2s ease;
    font-family: var(--font-family);
    flex-shrink: 0;
  }
  
  .sectionRegenerateBtn span {
    display: block;
    line-height: 1;
    transition: transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
  }
  
  .sectionRegenerateBtn:hover {
    background: var(--surface-hover);
    border-color: var(--accent-purple);
    color: var(--accent-purple);
    transform: translateY(-1px);
    box-shadow: 0 2px 8px rgba(139, 92, 246, 0.25);
  }
  
  .sectionRegenerateBtn:hover span {
    transform: rotate(180deg);
  }
  
  .sectionRegenerateBtn:active {
    transform: translateY(0) scale(0.95);
    box-shadow: 0 1px 2px rgba(139, 92, 246, 0.15);
  }
  
  .sectionRegenerateBtn:active span {
    transform: rotate(180deg) scale(0.95);
  }
  
  /* Extra Instruction Input - Compact */
  .sectionExtraInput {
    width: 140px;
    padding: 6px 10px;
    border: 1px solid var(--border-subtle);
    background: var(--surface-elevated);
    color: var(--text-secondary);
    border-radius: 6px;
    font-size: 11px;
    font-family: var(--font-family);
    transition: all 0.2s ease;
    outline: none;
  }
  
  .sectionExtraInput::placeholder {
    color: var(--text-tertiary);
  }
  
  .sectionExtraInput:focus {
    width: 160px;
    border-color: var(--accent-purple);
    background: var(--surface-active);
    color: var(--text-primary);
    box-shadow: 0 0 0 2px rgba(139, 92, 246, 0.1);
  }
  
  /* Flashcards Container */
  .flashcardsContainer {
    max-height: 300px;
    overflow-y: auto;
    border: 1px solid var(--border-subtle);
    border-radius: 8px;
    padding: 10px;
    background: var(--surface-elevated);
    transition: all 0.2s ease;
  }
  
  .flashcardsContainer:hover {
    border-color: var(--border-default);
  }
  
  /* Light mode adjustments */
  [data-theme="light"] .sectionActionBtn,
  body.light-mode .sectionActionBtn {
    border-color: rgba(0, 0, 0, 0.1);
    background: rgba(0, 0, 0, 0.02);
  }
  
  [data-theme="light"] .sectionActionBtn:hover,
  body.light-mode .sectionActionBtn:hover {
    background: rgba(0, 0, 0, 0.05);
    border-color: rgba(0, 0, 0, 0.15);
  }
  
  [data-theme="light"] .sectionRegenerateBtn,
  body.light-mode .sectionRegenerateBtn {
    border-color: rgba(0, 0, 0, 0.1);
    background: rgba(0, 0, 0, 0.02);
  }
  
  [data-theme="light"] .sectionRegenerateBtn:hover,
  body.light-mode .sectionRegenerateBtn:hover {
    border-color: var(--accent-purple);
    background: rgba(139, 92, 246, 0.1);
  }
  
  [data-theme="light"] .sectionExtraInput,
  body.light-mode .sectionExtraInput {
    border-color: rgba(0, 0, 0, 0.1);
    background: rgba(0, 0, 0, 0.02);
  }
  
  [data-theme="light"] .sectionExtraInput:focus,
  body.light-mode .sectionExtraInput:focus {
    background: rgba(0, 0, 0, 0.04);
    border-color: var(--accent-purple);
  }
  
  [data-theme="light"] .flashcardsContainer,
  body.light-mode .flashcardsContainer {
    border-color: rgba(0, 0, 0, 0.1);
    background: rgba(0, 0, 0, 0.02);
  }
  
  .summaryRegenerateControls {
    display: flex;
    align-items: center;
    gap: 6px;
    position: relative;
    justify-content: flex-end;
    flex-shrink: 0;
  }
  
  .summaryExtraInstruction {
    width: 0;
    opacity: 0;
    padding: 0;
    border: none;
    background: transparent;
    color: var(--text-secondary);
    font-size: 11px;
    border-radius: 6px;
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    pointer-events: none;
    overflow: hidden;
    order: 1;
    margin-right: 0;
    height: 28px;
    box-sizing: border-box;
    vertical-align: middle;
  }
  
  .summaryRegenerateControls:hover .summaryExtraInstruction,
  .summaryExtraInstruction:focus {
    width: 110px;
    opacity: 1;
    padding: 6px 8px;
    border: 1px solid rgba(255, 255, 255, 0.15);
    background: rgba(255, 255, 255, 0.05);
    pointer-events: auto;
    margin-right: 0;
    align-self: center;
    vertical-align: middle;
  }
  
  .regenerateBtn {
    order: 2;
    position: relative;
    z-index: 1;
    flex-shrink: 0;
    width: 28px;
    height: 28px;
    padding: 0;
    border: 1px solid rgba(255, 255, 255, 0.15);
    background: rgba(255, 255, 255, 0.08);
    color: rgba(255, 255, 255, 0.75);
    border-radius: 6px;
    cursor: pointer;
    font-size: 14px;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: background 0.2s ease, border-color 0.2s ease, color 0.2s ease;
    flex-shrink: 0;
  }
  
  .summaryExtraInstruction::placeholder {
    color: rgba(255, 255, 255, 0.4);
  }
  
  .regenerateBtn:hover {
    background: rgba(255, 255, 255, 0.12);
    border-color: rgba(100, 150, 255, 0.6);
    color: rgba(255, 255, 255, 0.9);
  }
  
  .regenerateBtn:active {
    transform: scale(0.95);
  }
  
  .regenerateBtn:active .regenerateIcon {
    transform: rotate(180deg);
  }
  
  .regenerateIcon {
    display: inline-block;
    transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  }
  
  [data-theme="light"] .summaryExtraInstruction,
  body.light-mode .summaryExtraInstruction {
    background: rgba(0, 0, 0, 0.03) !important;
    border: 1px solid rgba(0, 0, 0, 0.12) !important;
    color: rgba(0, 0, 0, 0.85) !important;
    transition: all 0.2s ease !important;
  }
  
  [data-theme="light"] .summaryRegenerateControls:hover .summaryExtraInstruction,
  [data-theme="light"] .summaryExtraInstruction:focus,
  body.light-mode .summaryRegenerateControls:hover .summaryExtraInstruction,
  body.light-mode .summaryExtraInstruction:focus {
    border: 1px solid rgba(139, 92, 246, 0.5) !important;
    background: rgba(0, 0, 0, 0.04) !important;
    color: rgba(0, 0, 0, 0.9) !important;
    box-shadow: 0 0 0 3px rgba(139, 92, 246, 0.1) !important;
  }
  
  [data-theme="light"] .summaryExtraInstruction::placeholder,
  body.light-mode .summaryExtraInstruction::placeholder {
    color: rgba(0, 0, 0, 0.45) !important;
  }
  
  [data-theme="light"] .regenerateBtn,
  body.light-mode .regenerateBtn {
    border: 1px solid rgba(0, 0, 0, 0.12) !important;
    background: rgba(0, 0, 0, 0.04) !important;
    color: rgba(0, 0, 0, 0.85) !important;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04) !important;
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
  }
  
  [data-theme="light"] .regenerateBtn:hover,
  body.light-mode .regenerateBtn:hover {
    background: rgba(139, 92, 246, 0.1) !important;
    border-color: rgba(139, 92, 246, 0.4) !important;
    color: var(--accent-purple) !important;
    box-shadow: 0 2px 4px rgba(139, 92, 246, 0.2) !important;
    transform: translateY(-0.5px) !important;
  }
    border-color: rgba(0, 0, 0, 0.15);
    background: rgba(0, 0, 0, 0.03);
  }
  
  [data-theme="light"] .summaryExtraInstruction::placeholder {
    color: rgba(0, 0, 0, 0.4);
  }
  
  [data-theme="light"] .regenerateBtn {
    border-color: rgba(0, 0, 0, 0.12) !important;
    background: rgba(0, 0, 0, 0.04) !important;
    color: rgba(0, 0, 0, 0.85) !important;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04) !important;
  }
  
  [data-theme="light"] .regenerateBtn:hover {
    background: rgba(90, 159, 212, 0.1) !important;
    border-color: rgba(90, 159, 212, 0.4) !important;
    color: rgba(90, 159, 212, 1) !important;
    box-shadow: 0 2px 4px rgba(90, 159, 212, 0.2) !important;
    transform: translateY(-0.5px) !important;
  }
  
  /* Notes Mode Toggle - Elegant checkbox design with improved styling */
  .notesModeToggle {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    cursor: pointer;
    user-select: none;
    padding: 6px 10px;
    border-radius: 8px;
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.1);
    transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
    position: relative;
    overflow: hidden;
  }
  
  .notesModeToggle::before {
    content: '';
    position: absolute;
    top: 0;
    left: -100%;
    width: 100%;
    height: 100%;
    background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.1), transparent);
    transition: left 0.5s ease;
  }
  
  .notesModeToggle:hover {
    background: rgba(255, 255, 255, 0.08);
    border-color: rgba(255, 255, 255, 0.15);
    transform: translateY(-1px);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
  }
  
  .notesModeToggle:hover::before {
    left: 100%;
  }
  
  .notesModeToggle input[type="checkbox"] {
    width: 18px;
    height: 18px;
    cursor: pointer;
    accent-color: var(--accent-purple);
    margin: 0;
    flex-shrink: 0;
    transition: transform 0.2s ease;
  }
  
  .notesModeToggle:hover input[type="checkbox"] {
    transform: scale(1.1);
  }
  
  .toggleLabel {
    font-size: 12px;
    font-weight: 600;
    color: rgba(255, 255, 255, 0.8);
    transition: color 0.2s ease;
    letter-spacing: 0.3px;
    position: relative;
    z-index: 1;
  }
  
  .notesModeToggle:hover .toggleLabel {
    color: rgba(255, 255, 255, 0.95);
  }
  
  [data-theme="light"] .toggleLabel,
  body.light-mode .toggleLabel {
    color: rgba(0, 0, 0, 0.75) !important;
  }
  
  [data-theme="light"] .notesModeToggle,
  body.light-mode .notesModeToggle {
    background: rgba(0, 0, 0, 0.04) !important;
    border-color: rgba(0, 0, 0, 0.1) !important;
  }
  
  [data-theme="light"] .notesModeToggle:hover,
  body.light-mode .notesModeToggle:hover {
    background: rgba(0, 0, 0, 0.06) !important;
    border-color: rgba(0, 0, 0, 0.15) !important;
  }
  
  [data-theme="light"] .notesModeToggle:hover .toggleLabel,
  body.light-mode .notesModeToggle:hover .toggleLabel {
    color: rgba(0, 0, 0, 0.9) !important;
  }
  
  /* Auto-generated Badge - Cool animated pill */
  .autoGeneratedBadge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 0 10px; /* No vertical padding - height controlled by container */
    background: linear-gradient(135deg, rgba(138, 43, 226, 0.2), rgba(75, 0, 130, 0.2));
    border: 1px solid rgba(138, 43, 226, 0.3);
    border-radius: 12px;
    font-size: 11px;
    font-weight: 600;
    color: rgba(255, 255, 255, 0.9);
    cursor: pointer;
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    position: relative;
    overflow: hidden;
    letter-spacing: 0.2px;
    box-shadow: 0 2px 8px rgba(138, 43, 226, 0.15);
    height: 28px; /* Fixed height to match button */
    min-height: 28px;
    max-height: 28px;
    box-sizing: border-box;
    margin: 0;
    vertical-align: middle;
  }
  
  .autoGeneratedBadge::before {
    content: '';
    position: absolute;
    top: 0;
    left: -100%;
    width: 100%;
    height: 100%;
    background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.2), transparent);
    transition: left 0.6s ease;
  }
  
  .autoGeneratedBadge:hover {
    background: linear-gradient(135deg, rgba(138, 43, 226, 0.3), rgba(75, 0, 130, 0.3));
    border-color: rgba(138, 43, 226, 0.5);
    transform: translateY(-2px) scale(1.05);
    box-shadow: 0 4px 16px rgba(138, 43, 226, 0.25);
  }
  
  .autoGeneratedBadge:hover::before {
    left: 100%;
  }
  
  .autoGeneratedBadge .badgeIcon {
    font-size: 14px;
    animation: sparkle 2s ease-in-out infinite;
    display: inline-block;
  }
  
  .autoGeneratedBadge:hover .badgeIcon {
    animation: sparkle 0.8s ease-in-out infinite;
    transform: rotate(15deg);
  }
  
  .autoGeneratedBadge .badgeText {
    position: relative;
    z-index: 1;
  }
  
  @keyframes sparkle {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.7; transform: scale(1.1); }
  }
  
  [data-theme="light"] .autoGeneratedBadge,
  body.light-mode .autoGeneratedBadge {
    background: linear-gradient(135deg, rgba(138, 43, 226, 0.15), rgba(75, 0, 130, 0.15)) !important;
    border-color: rgba(138, 43, 226, 0.25) !important;
    color: rgba(0, 0, 0, 0.8) !important;
    box-shadow: 0 2px 8px rgba(138, 43, 226, 0.1) !important;
  }
  
  [data-theme="light"] .autoGeneratedBadge:hover,
  body.light-mode .autoGeneratedBadge:hover {
    background: linear-gradient(135deg, rgba(138, 43, 226, 0.25), rgba(75, 0, 130, 0.25)) !important;
    border-color: rgba(138, 43, 226, 0.4) !important;
    box-shadow: 0 4px 16px rgba(138, 43, 226, 0.2) !important;
  }
  
  /* Interactive badge - clickable */
  .autoGeneratedBadge.interactiveBadge {
    cursor: pointer;
    user-select: none;
  }
  
  /* Click animation for badge toggle */
  .autoGeneratedBadge.interactiveBadge:active {
    transform: scale(0.95);
    transition: transform 0.1s cubic-bezier(0.4, 0, 0.2, 1);
  }
  
  /* Transition animation for mode change */
  .autoGeneratedBadge {
    transition: all 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
  }
  
  /* Notes mode state - different styling with smooth transition */
  .autoGeneratedBadge.notesMode {
    background: linear-gradient(135deg, rgba(120, 120, 120, 0.2), rgba(80, 80, 80, 0.2)) !important;
    border-color: rgba(120, 120, 120, 0.3) !important;
    box-shadow: 0 2px 8px rgba(120, 120, 120, 0.15) !important;
    transform: scale(1);
  }
  
  .autoGeneratedBadge.notesMode:hover {
    background: linear-gradient(135deg, rgba(120, 120, 120, 0.3), rgba(80, 80, 80, 0.3)) !important;
    border-color: rgba(120, 120, 120, 0.5) !important;
    box-shadow: 0 4px 16px rgba(120, 120, 120, 0.25) !important;
  }
  
  .autoGeneratedBadge.notesMode .badgeIcon {
    /* Remove fadeOut animation - keep icon visible */
    animation: none !important;
    opacity: 1 !important;
    display: inline-block !important;
  }
  
  /* AI enabled state - enhanced purple with smooth transition */
  .autoGeneratedBadge.aiEnabled {
    background: linear-gradient(135deg, rgba(138, 43, 226, 0.25), rgba(75, 0, 130, 0.25)) !important;
    border-color: rgba(138, 43, 226, 0.4) !important;
    animation: pulseGlow 0.6s ease;
  }
  
  @keyframes fadeOut {
    0% { opacity: 1; transform: scale(1) rotate(0deg); }
    100% { opacity: 1; transform: scale(1) rotate(0deg); } /* Keep visible - don't fade out */
  }
  
  @keyframes pulseGlow {
    0% { 
      box-shadow: 0 2px 8px rgba(138, 43, 226, 0.15);
      transform: scale(1);
    }
    50% { 
      box-shadow: 0 4px 20px rgba(138, 43, 226, 0.4);
      transform: scale(1.05);
    }
    100% { 
      box-shadow: 0 2px 8px rgba(138, 43, 226, 0.15);
      transform: scale(1);
    }
  }
  
  [data-theme="light"] .autoGeneratedBadge.notesMode,
  body.light-mode .autoGeneratedBadge.notesMode {
    background: linear-gradient(135deg, rgba(0, 0, 0, 0.1), rgba(0, 0, 0, 0.08)) !important;
    border-color: rgba(0, 0, 0, 0.2) !important;
    color: rgba(0, 0, 0, 0.7) !important;
  }
  
  [data-theme="light"] .autoGeneratedBadge.notesMode:hover,
  body.light-mode .autoGeneratedBadge.notesMode:hover {
    background: linear-gradient(135deg, rgba(0, 0, 0, 0.15), rgba(0, 0, 0, 0.12)) !important;
    border-color: rgba(0, 0, 0, 0.3) !important;
  }
  
  /* Mode Switch - Beautiful compact design */
  #summaryModeSwitch {
    display: inline-flex;
    background: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 6px;
    padding: 2px;
    gap: 0;
    overflow: hidden;
    box-shadow: 0 1px 4px rgba(0, 0, 0, 0.1), inset 0 1px 0 rgba(255, 255, 255, 0.08);
    transition: background 0.25s ease, border-color 0.25s ease, box-shadow 0.25s ease;
    position: relative;
    height: 28px;
    width: 160px;
    min-width: 160px;
    flex-shrink: 0;
  }
  
  #summaryModeSwitch:hover {
    background: rgba(255, 255, 255, 0.12);
    border-color: rgba(255, 255, 255, 0.2);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15), inset 0 1px 0 rgba(255, 255, 255, 0.12);
  }
  
  #summaryModeSwitch .modeOption {
    position: relative;
    z-index: 2;
    padding: 0 10px;
    font-size: 10px;
    font-weight: 600;
    background: transparent;
    border: none;
    color: rgba(255, 255, 255, 0.65);
    cursor: pointer;
    transition: color 0.25s ease;
    border-radius: 4px;
    white-space: nowrap;
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    margin: 0;
    flex: 1;
    width: 50%;
  }
  
  #summaryModeSwitch .modeOption .modeShort {
    display: none;
  }
  
  #summaryModeSwitch .modeOption .modeFull {
    display: inline-block;
    opacity: 1;
    position: relative;
    font-weight: 600;
    line-height: 1;
    white-space: nowrap;
  }
  
  #summaryModeSwitch .modeOption:hover {
    color: rgba(255, 255, 255, 0.95);
  }
  
  #summaryModeSwitch .modeOption.active {
    color: rgba(255, 255, 255, 1);
    font-weight: 700;
  }
  
  #summaryModeSwitch .modeSlider {
    position: absolute;
    top: 2px;
    left: 2px;
    width: calc(50% - 2px);
    height: calc(100% - 4px);
    background: linear-gradient(135deg, rgba(102, 126, 234, 0.5) 0%, rgba(118, 74, 162, 0.5) 100%);
    border: 1px solid rgba(102, 126, 234, 0.6);
    border-radius: 4px;
    transition: transform 0.35s cubic-bezier(0.4, 0, 0.2, 1);
    z-index: 1;
    box-shadow: 0 1px 4px rgba(102, 126, 234, 0.3), inset 0 1px 0 rgba(255, 255, 255, 0.15);
  }
  
  #summaryModeSwitch[data-mode="explain"] .modeSlider {
    transform: translateX(100%);
  }
  
  /* Light mode for controls bar */
  [data-theme="light"] .summaryControlsBar {
    background: rgba(0, 0, 0, 0.03);
    border: 1px solid rgba(0, 0, 0, 0.1);
  }
  
  [data-theme="light"] .summaryControlsBar:hover {
    background: rgba(0, 0, 0, 0.05);
    border-color: rgba(0, 0, 0, 0.15);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  }
  
  [data-theme="light"] .toggleLabel {
    color: rgba(0, 0, 0, 0.7);
  }
  
  [data-theme="light"] .notesModeToggle:hover .toggleLabel {
    color: rgba(0, 0, 0, 0.85);
  }
  
  [data-theme="light"] #summaryModeSwitch {
    background: rgba(0, 0, 0, 0.06);
    border: 1px solid rgba(0, 0, 0, 0.12);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08), inset 0 1px 0 rgba(255, 255, 255, 0.5);
  }
  
  [data-theme="light"] #summaryModeSwitch:hover {
    background: rgba(0, 0, 0, 0.08);
    border-color: rgba(0, 0, 0, 0.18);
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12), inset 0 1px 0 rgba(255, 255, 255, 0.6);
  }
  
  [data-theme="light"] #summaryModeSwitch .modeOption {
    color: rgba(0, 0, 0, 0.6);
  }
  
  [data-theme="light"] #summaryModeSwitch .modeOption:hover {
    color: rgba(0, 0, 0, 0.85);
  }
  
  [data-theme="light"] #summaryModeSwitch .modeOption.active {
    color: rgba(0, 0, 0, 0.95);
  }
  
  [data-theme="light"] #summaryModeSwitch .modeSlider {
    background: linear-gradient(135deg, rgba(102, 126, 234, 0.25) 0%, rgba(118, 74, 162, 0.25) 100%);
    border: 1px solid rgba(102, 126, 234, 0.5);
    box-shadow: 0 2px 8px rgba(102, 126, 234, 0.25), inset 0 1px 0 rgba(255, 255, 255, 0.3);
  }
  
  /* Checkbox styling */
  input[type="checkbox"] {
    cursor: pointer;
    accent-color: var(--accent-purple);
  }
  
  [data-theme="light"] input[type="checkbox"] {
    accent-color: var(--accent-purple);
  }
  
  /* Seamless sections with balanced typography */
  /* ============================================
     CONTENT SECTIONS & BOXES
     ============================================ */
  .rsSection {
    margin-bottom: var(--spacing-xl);
    opacity: 0;
    transform: translateY(12px);
    animation: sectionFadeIn var(--transition-spring) forwards;
  }
  
  .rsSection:nth-child(1) { 
    animation-delay: 0.05s;
    margin-top: 8px; /* Add top margin for summary section */
  }
  .rsSection:nth-child(2) { animation-delay: 0.1s; }
  .rsSection:nth-child(3) { animation-delay: 0.15s; }
  .rsSection:nth-child(4) { animation-delay: 0.2s; }
  .rsSection:nth-child(5) { animation-delay: 0.25s; }
  .rsSection:nth-child(6) { animation-delay: 0.3s; }
  
  .rsSection h3 {
    margin: 0 0 var(--spacing-md) 0;
    font-size: var(--font-size-large);
    font-weight: var(--font-weight-semibold);
    color: var(--text-tertiary);
    font-family: var(--font-family);
    transition: color var(--transition-base);
    letter-spacing: -0.01em;
  }
  
  .rsSection:hover h3 {
    color: var(--text-secondary);
  }
  
  @keyframes sectionFadeIn {
    from {
      opacity: 0;
      transform: translateY(16px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }
  
  .rsBox { 
    white-space: pre-wrap; 
    font-size: var(--font-size-medium);
    line-height: var(--line-height-relaxed);
    color: var(--text-secondary);
    min-height: 120px; 
    padding: var(--spacing-lg);
    border-radius: var(--radius-md);
    background: var(--surface-elevated);
    border: 1px solid var(--border-subtle);
    font-family: var(--font-family);
    transition: all var(--transition-base);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    box-shadow: var(--shadow-sm);
    opacity: 0;
    transform: translateY(8px);
    animation: boxFadeIn var(--transition-spring) 0.15s forwards;
    position: relative;
    user-select: text;
    -webkit-user-select: text;
    -moz-user-select: text;
    -ms-user-select: text;
    /* Allow resizing and scrolling */
    overflow-y: hidden;
    box-sizing: border-box;
  }
  
  /* Term explainer should not have minimum height */
  #rsExplain {
    min-height: 50px;
  }
  
  /* Summary box - allow resizing smaller */
  #rsSummary {
    min-height: 120px;
  }
  
  .rsBox:focus {
    outline: none;
    border-color: var(--accent-purple);
    background: var(--surface-active);
    box-shadow: var(--shadow-md), 0 0 0 2px var(--accent-purple-glow);
    transform: translateY(0) scale(1.005);
  }
  
  .rsBox[contenteditable="true"] {
    cursor: text;
  }
  
  .rsBox:hover {
    background: var(--surface-hover);
    border-color: var(--border-default);
    box-shadow: var(--shadow-md);
  }
  
  /* Copy button for summary boxes - simple icon, no box */
  .rsBox .copyBtn {
    position: absolute;
    top: 10px;
    right: 10px;
    background: transparent;
    border: none;
    padding: 0;
    width: 16px;
    height: 16px;
    cursor: pointer;
    transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1), 
                background-image 0.3s ease,
                transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
    opacity: 0;
    transform: translateX(8px);
    z-index: 10;
    color: rgba(255, 255, 255, 0.4);
    display: flex;
    align-items: center;
    justify-content: center;
    background-image: url('icons/copy_icon.png');
    background-size: contain;
    background-repeat: no-repeat;
    background-position: center;
    filter: brightness(0) invert(0.5);
  }
  
  /* Copy button checkmark state */
  .rsBox .copyBtn.copied {
    background-image: none;
    filter: brightness(0) invert(1);
  }
  
  .rsBox .copyBtn.copied::before {
    content: '✓';
    position: absolute;
    font-size: 14px;
    font-weight: bold;
    color: #4ade80;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 100%;
    height: 100%;
    animation: checkmarkAppear 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
  }
  
  @keyframes checkmarkAppear {
    0% {
      transform: scale(0) rotate(-180deg);
      opacity: 0;
    }
    50% {
      transform: scale(1.2) rotate(0deg);
    }
    100% {
      transform: scale(1) rotate(0deg);
      opacity: 1;
    }
  }
  
  .rsBox:hover .copyBtn {
    opacity: 1;
    transform: translateX(0);
  }
  
  .rsBox .copyBtn:hover {
    color: rgba(255, 255, 255, 0.7);
    transform: translateX(0) scale(1.1);
    filter: brightness(0) invert(0.8);
  }
  
  .rsBox .copyBtn:active {
    transform: translateX(0) scale(0.95);
  }
  
  .rsBox strong {
    font-weight: var(--font-weight-semibold);
    color: var(--text-primary);
  }
  
  .rsBox p {
    margin: 0 0 var(--spacing-xl) 0;
  }
  
  .rsBox p:last-child {
    margin-bottom: 0;
  }
  
  /* Markdown formatting styles */
  .rsBox code {
    background: rgba(255, 255, 255, 0.1);
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 4px;
    padding: 2px 6px;
    font-family: 'SF Mono', 'Monaco', 'Inconsolata', 'Roboto Mono', monospace;
    font-size: 0.9em;
    color: var(--accent-purple);
    font-weight: 500;
  }
  
  .rsBox .markdown-h2 {
    font-size: 1.3em;
    font-weight: 600;
    color: var(--text-primary);
    margin: 1.2em 0 0.8em 0;
    padding-bottom: 0.4em;
    border-bottom: 2px solid var(--border-subtle);
  }
  
  .rsBox .markdown-h2:first-child {
    margin-top: 0;
  }
  
  .rsBox .markdown-h3 {
    font-size: 1.15em;
    font-weight: 600;
    color: var(--text-primary);
    margin: 1em 0 0.6em 0;
  }
  
  .rsBox .markdown-hr {
    border: none;
    border-top: 1px solid var(--border-subtle);
    margin: 1.5em 0;
    opacity: 0.5;
  }
  
  .rsBox .markdown-quote {
    border-left: 3px solid var(--accent-purple);
    padding-left: 1em;
    margin: 1em 0;
    padding-top: 0.5em;
    padding-bottom: 0.5em;
    background: rgba(139, 92, 246, 0.08);
    border-radius: 4px;
    font-style: italic;
    color: var(--text-secondary);
  }
  
  .rsBox .markdown-ul,
  .rsBox .markdown-ol {
    margin: 0.8em 0;
    padding-left: 1.5em;
  }
  
  .rsBox .markdown-ul {
    list-style-type: disc;
  }
  
  .rsBox .markdown-ol {
    list-style-type: decimal;
  }
  
  .rsBox .markdown-li-bullet,
  .rsBox .markdown-li-numbered {
    margin: 0.4em 0;
    line-height: 1.6;
  }
  
  .rsBox em {
    font-style: italic;
    color: var(--text-secondary);
  }
  
  /* Markdown links */
  .rsBox a,
  .rsBox .markdown-link {
    color: var(--accent-purple);
    text-decoration: none;
    border-bottom: 1px solid rgba(139, 92, 246, 0.3);
    transition: all 0.2s ease;
  }
  
  .rsBox a:hover,
  .rsBox .markdown-link:hover {
    color: var(--accent-purple-light);
    border-bottom-color: var(--accent-purple);
  }
  
  .rsBox a:visited {
    color: var(--accent-purple-dark);
    opacity: 0.8;
  }
  
  /* Light mode markdown styles */
  body.light-mode .rsBox code {
    background: rgba(0, 0, 0, 0.06);
    border-color: rgba(0, 0, 0, 0.12);
    color: var(--accent-purple);
  }
  
  body.light-mode .rsBox .markdown-h2 {
    color: #1d1d1f;
    border-bottom-color: rgba(0, 0, 0, 0.1);
  }
  
  body.light-mode .rsBox .markdown-h3 {
    color: #1d1d1f;
  }
  
  body.light-mode .rsBox .markdown-hr {
    border-top-color: rgba(0, 0, 0, 0.1);
  }
  
  body.light-mode .rsBox .markdown-quote {
    border-left-color: var(--accent-purple);
    background: rgba(139, 92, 246, 0.1);
    color: #2d2d30;
  }
  
  body.light-mode .rsBox em {
    color: #2d2d30;
  }
  
  /* Resize handle for rsBox - must stay fixed at bottom-right of visible area */
  .rsBox {
    position: relative;
    /* Create a stacking context for the handle */
  }
  
  .rsBox .resizeHandle {
    position: absolute;
    bottom: 0;
    right: 0;
    width: 16px;
    height: 16px;
    cursor: nwse-resize;
    z-index: 100;
    background: transparent;
    opacity: 0;
    transition: opacity 0.2s ease;
    pointer-events: auto;
    /* Ensure handle stays at bottom-right of box container, not scrollable content */
    position: fixed;
  }
  
  /* When box is scrollable, calculate position relative to box's visible area */
  .rsBox[style*="overflow-y: auto"] .resizeHandle,
  .rsBox[style*="overflow-y: scroll"] .resizeHandle {
    position: absolute;
  }
  
  /* Scrollbar styling for rsBox in dark mode */
  .rsBox::-webkit-scrollbar {
    width: 8px;
  }
  
  .rsBox::-webkit-scrollbar-track {
    background: transparent;
  }
  
  .rsBox::-webkit-scrollbar-thumb {
    background: rgba(255, 255, 255, 0.15);
    border-radius: 4px;
  }
  
  .rsBox::-webkit-scrollbar-thumb:hover {
    background: rgba(139, 92, 246, 0.4);
  }
  
  [data-theme="light"] .rsBox::-webkit-scrollbar-thumb {
    background: rgba(0, 0, 0, 0.15);
  }
  
  [data-theme="light"] .rsBox::-webkit-scrollbar-thumb:hover {
    background: rgba(0, 0, 0, 0.25);
  }
  
  /* Firefox scrollbar */
  .rsBox {
    scrollbar-width: thin;
    scrollbar-color: rgba(255, 255, 255, 0.15) transparent;
  }
  
  [data-theme="light"] .rsBox {
    scrollbar-color: rgba(0, 0, 0, 0.15) transparent;
  }
  
  .rsBox:hover .resizeHandle {
    opacity: 0.6;
  }
  
  .rsBox .resizeHandle:hover,
  .rsBox .resizeHandle.resizing {
    opacity: 1;
  }
  
  .rsBox .resizeHandle::after {
    content: '';
    position: absolute;
    bottom: 2px;
    right: 2px;
    width: 0;
    height: 0;
    border-style: solid;
    border-width: 0 0 12px 12px;
    border-color: transparent transparent var(--text-tertiary) transparent;
    pointer-events: none;
  }
  
  body.light-mode .rsBox .resizeHandle::after {
    border-color: transparent transparent var(--text-quaternary) transparent;
  }
  
  /* 1. Enhanced Empty States with Icons */
  .rsEmpty {
    color: var(--text-quaternary);
    font-size: var(--font-size-small);
    font-style: italic;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    min-height: 60px;
    text-align: center;
    position: relative;
  }
  
  /* 1. Enhanced Empty States with Icons - positioned above placeholder text */
  .rsEmpty[data-placeholder] {
    position: relative;
    padding-top: 50px; /* Increased top margin */
  }
  
  /* Summary Empty State - Similar to flashcards */
  .summaryEmptyState {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 50px 20px;
    text-align: center;
    min-height: 120px;
  }
  
  .summaryEmptyIcon {
    font-size: 48px;
    opacity: 0.5;
    margin-bottom: 12px;
    line-height: 1;
    filter: grayscale(0.3);
    transition: opacity 0.3s ease;
  }
  
  .summaryEmptyText {
    font-size: 13px;
    color: var(--text-tertiary);
    font-style: italic;
    opacity: 0.8;
    font-weight: 400;
  }
  
  [data-theme="light"] .summaryEmptyText,
  body.light-mode .summaryEmptyText {
    color: rgba(0, 0, 0, 0.5);
  }
  
  [data-theme="light"] .summaryEmptyIcon,
  body.light-mode .summaryEmptyIcon {
    opacity: 0.4;
  }
  
  /* Loading state animation */
  .rsBox.loading .summaryEmptyState .summaryEmptyIcon,
  #rsSummary.loading .summaryEmptyState .summaryEmptyIcon {
    animation: pulse 1.5s ease-in-out infinite;
  }
  
  /* Summary empty state - Better styled like flashcards */
  #rsSummary.rsEmpty[data-placeholder="Click Summary dropdown to generate content."] {
    padding: 30px 20px 20px 20px; /* Reduced padding to match flashcards */
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    text-align: center;
    min-height: 120px;
  }
  
  #rsSummary.rsEmpty[data-placeholder="Click Summary dropdown to generate content."]::before {
    content: '📄';
    font-size: 48px;
    opacity: 0.5;
    margin-bottom: 8px;
    line-height: 1;
    filter: grayscale(0.3);
    display: block;
    margin-top: 0;
  }
  
  #rsSummary.rsEmpty[data-placeholder="Click Summary dropdown to generate content."]::after {
    content: 'No summary available for this page';
    font-size: 13px;
    color: var(--text-tertiary);
    font-style: normal;
    opacity: 0.8;
    font-weight: 400;
    display: block;
    margin-top: 0;
  }
  
  [data-theme="light"] #rsSummary.rsEmpty[data-placeholder="Click Summary dropdown to generate content."]::after,
  body.light-mode #rsSummary.rsEmpty[data-placeholder="Click Summary dropdown to generate content."]::after {
    color: rgba(0, 0, 0, 0.5);
  }
  
  [data-theme="light"] #rsSummary.rsEmpty[data-placeholder="Click Summary dropdown to generate content."]::before,
  body.light-mode #rsSummary.rsEmpty[data-placeholder="Click Summary dropdown to generate content."]::before {
    opacity: 0.4;
  }
  
  /* Icon for flashcards empty state */
  #fcEmpty.rsEmpty[data-placeholder*="No flashcards"]::after {
    content: '🃏';
    font-size: 28px;
    opacity: 0.4;
    display: block;
    line-height: 1;
    position: absolute;
    top: 8px;
    left: 50%;
    transform: translateX(-50%);
    pointer-events: none;
  }
  
  /* Ensure fcEmpty is completely hidden when display is none */
  #fcEmpty[style*="display: none"],
  #fcEmpty[style*="display:none"] {
    display: none !important;
    visibility: hidden !important;
    opacity: 0 !important;
    height: 0 !important;
    overflow: hidden !important;
    pointer-events: none !important;
  }
  
  #fcEmpty[style*="display: none"]::after,
  #fcEmpty[style*="display:none"]::after,
  #fcEmpty.fcHidden::after {
    display: none !important;
    content: none !important;
    opacity: 0 !important;
    visibility: hidden !important;
  }
  
  /* Aggressive hiding for fcEmpty when cards are present */
  #fcEmpty.fcHidden {
    display: none !important;
    visibility: hidden !important;
    opacity: 0 !important;
    height: 0 !important;
    overflow: hidden !important;
    pointer-events: none !important;
    margin: 0 !important;
    padding: 0 !important;
  }
  
  #fcEmpty.fcHidden::after,
  #fcEmpty.fcHidden::before {
    display: none !important;
    content: none !important;
    opacity: 0 !important;
    visibility: hidden !important;
  }
  
  /* Icon for Ask AI empty state */
  #aiAnswer.rsEmpty[data-placeholder="Ask a question to get started."]::after {
    content: '💬';
    font-size: 28px;
    opacity: 0.4;
    display: block;
    line-height: 1;
    position: absolute;
    top: 8px;
    left: 50%;
    transform: translateX(-50%);
    pointer-events: none;
  }
  
  /* Icon for Term Explainer empty state */
  #rsExplain.rsEmpty[data-placeholder="Select a word or term in the PDF"]::after {
    content: '🔍';
    font-size: 28px;
    opacity: 0.4;
    display: block;
    line-height: 1;
    position: absolute;
    top: 8px;
    left: 50%;
    transform: translateX(-50%);
    pointer-events: none;
  }
  
  /* Icon for Summary empty state */
  
  /* Placeholder system for contenteditable and empty boxes */
  .rsBox[data-placeholder].rsEmpty::before,
  .rsEmpty[data-placeholder]::before,
  .aiAnswerBox[data-placeholder].rsEmpty::before {
    content: attr(data-placeholder);
    color: var(--text-quaternary);
    font-style: italic;
    pointer-events: none;
    user-select: none;
    -webkit-user-select: none;
    -moz-user-select: none;
    -ms-user-select: none;
  }
  
  /* Hide placeholder when element has content */
  .rsBox[data-placeholder]:not(.rsEmpty)::before {
    display: none;
  }
  
  /* Ensure placeholder doesn't interfere with contenteditable when focused and has content */
  .rsBox[contenteditable="true"][data-placeholder]:focus:not(.rsEmpty)::before {
    display: none;
  }
  
  /* Add margin to aiAnswer placeholder */
  .aiAnswerBox[data-placeholder].rsEmpty::before {
    margin-top: 0;
    margin-left: 0;
    display: block;
  }
  
  /* Elegant Table Container */
  .elegant-table-wrapper {
    margin: 16px 0;
    border-radius: 8px;
    overflow: hidden;
    background: rgba(255, 255, 255, 0.02);
    border: 1px solid rgba(255, 255, 255, 0.08);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
  }
  
  .elegant-table-scroll {
    overflow-x: auto;
    overflow-y: visible;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: thin;
    scrollbar-color: rgba(255, 255, 255, 0.2) transparent;
  }
  
  .elegant-table-scroll::-webkit-scrollbar {
    height: 8px;
  }
  
  .elegant-table-scroll::-webkit-scrollbar-track {
    background: transparent;
  }
  
  .elegant-table-scroll::-webkit-scrollbar-thumb {
    background: rgba(255, 255, 255, 0.2);
    border-radius: 4px;
  }
  
  .elegant-table-scroll::-webkit-scrollbar-thumb:hover {
    background: rgba(255, 255, 255, 0.3);
  }
  
  .elegant-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
    line-height: 1.5;
    min-width: 100%;
    table-layout: auto;
  }
  
  .elegant-table th,
  .elegant-table td {
    min-width: 80px;
    max-width: none;
  }
  
  .elegant-table thead {
    background: linear-gradient(135deg, rgba(139, 92, 246, 0.15) 0%, rgba(167, 139, 250, 0.15) 100%);
    border-bottom: 2px solid rgba(139, 92, 246, 0.3);
  }
  
  .elegant-table th {
    padding: 12px 16px;
    text-align: left;
    font-weight: 600;
    color: var(--text-primary);
    white-space: nowrap;
    border-right: 1px solid rgba(255, 255, 255, 0.08);
    font-size: 12px;
    letter-spacing: 0.3px;
    text-transform: uppercase;
  }
  
  .elegant-table th:last-child {
    border-right: none;
  }
  
  .elegant-table tbody tr {
    border-bottom: 1px solid rgba(255, 255, 255, 0.06);
    transition: background 0.2s ease;
  }
  
  .elegant-table tbody tr:hover {
    background: rgba(255, 255, 255, 0.04);
  }
  
  .elegant-table tbody tr:last-child {
    border-bottom: none;
  }
  
  .elegant-table td {
    padding: 10px 16px;
    color: var(--text-secondary);
    border-right: 1px solid rgba(255, 255, 255, 0.05);
    vertical-align: top;
  }
  
  .elegant-table td:last-child {
    border-right: none;
  }
  
  .elegant-table td strong {
    color: var(--text-primary);
    font-weight: 600;
  }
  
  /* Light mode table styles */
  [data-theme="light"] .elegant-table-wrapper {
    background: #ffffff;
    border-color: rgba(0, 0, 0, 0.1);
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05), 0 1px 2px rgba(0, 0, 0, 0.03);
  }
  
  [data-theme="light"] .elegant-table thead {
    background: rgba(0, 0, 0, 0.03) !important;
  }
  
  [data-theme="light"] .elegant-table th {
    color: rgba(0, 0, 0, 0.85) !important;
    border-color: rgba(0, 0, 0, 0.1) !important;
  }
  
  [data-theme="light"] .elegant-table tbody tr {
    border-color: rgba(0, 0, 0, 0.06) !important;
  }
  
  [data-theme="light"] .elegant-table tbody tr:hover {
    background: rgba(0, 0, 0, 0.03) !important;
  }
  
  [data-theme="light"] .elegant-table-scroll::-webkit-scrollbar-thumb {
    background: rgba(0, 0, 0, 0.2);
  }
  
  [data-theme="light"] .elegant-table-scroll::-webkit-scrollbar-thumb:hover {
    background: rgba(0, 0, 0, 0.3);
  }
  
  [data-theme="light"] .elegant-table thead {
    background: linear-gradient(135deg, rgba(139, 92, 246, 0.1) 0%, rgba(167, 139, 250, 0.1) 100%);
    border-bottom-color: rgba(139, 92, 246, 0.25);
  }
  
  [data-theme="light"] .elegant-table th {
    color: rgba(0, 0, 0, 0.9);
    border-right-color: rgba(0, 0, 0, 0.1);
  }
  
  [data-theme="light"] .elegant-table tbody tr {
    border-bottom-color: rgba(0, 0, 0, 0.08);
  }
  
  [data-theme="light"] .elegant-table tbody tr:hover {
    background: rgba(0, 0, 0, 0.04);
  }
  
  [data-theme="light"] .elegant-table td {
    color: rgba(0, 0, 0, 0.75);
    border-right-color: rgba(0, 0, 0, 0.06);
  }
  
  [data-theme="light"] .elegant-table td strong {
    color: rgba(0, 0, 0, 0.9);
  }
  
  @keyframes boxFadeIn {
    from {
      opacity: 0;
      transform: translateY(12px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }
  
  /* Light mode adjustments for various text elements */
  body.light-mode #rsScroll {
    color: #1a1a1a;
  }
  body.light-mode #pageDebugInfo {
    color: rgba(0, 0, 0, 0.7) !important;
    background: rgba(0, 0, 0, 0.08) !important;
    border: 1px solid rgba(0, 0, 0, 0.15) !important;
  }
  body.light-mode input[type="text"] {
    color: rgba(0, 0, 0, 0.9) !important;
    border-bottom-color: rgba(0, 0, 0, 0.25) !important;
  }
  body.light-mode input[type="text"]::placeholder {
    color: rgba(0, 0, 0, 0.5) !important;
  }
  body.light-mode input[type="text"]:focus {
    border-bottom-color: var(--accent-purple) !important;
  }
  
  /* Button styling for save/clear */
  .rsSection button { margin-top: 6px; margin-right: 6px; font-size: 11px; padding: 6px 9px; }

  /* Flashcards simple list - subtle separation */
  #fcList { display: grid; gap: 12px; }
  
  /* Empty State - Show only when fcList is empty */
  .fcEmptyState {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 30px 20px 20px 20px; /* Reduced top padding to move icon down */
    text-align: center;
    min-height: 180px;
  }
  
  .fcEmptyIcon {
    font-size: 48px;
    opacity: 0.5;
    margin-top: 0;
    margin-bottom: 8px; /* Space between icon and text */
    line-height: 1;
    filter: grayscale(0.3);
    transition: opacity 0.3s ease;
  }
  
  .fcEmptyText {
    font-size: 13px;
    color: var(--text-tertiary);
    font-style: normal; /* Changed from italic */
    opacity: 0.8;
    font-weight: 400;
  }
  
  [data-theme="light"] .fcEmptyText,
  body.light-mode .fcEmptyText {
    color: rgba(0, 0, 0, 0.5);
  }
  
  [data-theme="light"] .fcEmptyIcon,
  body.light-mode .fcEmptyIcon {
    opacity: 0.4;
  }
  
  /* Loading state */
  .fcEmptyState .fcEmptyIcon:has-text("⏳") {
    animation: pulse 1.5s ease-in-out infinite;
  }
  
  @keyframes pulse {
    0%, 100% { opacity: 0.5; transform: scale(1); }
    50% { opacity: 0.8; transform: scale(1.05); }
  }
  
  /* Empty state is automatically hidden when cards are added (they replace the innerHTML) */
  
  .fcItem { 
    background: rgba(255,255,255,0.02); 
    border: 1px solid rgba(255,255,255,0.08); 
    border-radius: 8px; 
    padding: 14px; 
    position: relative; 
    margin-bottom: 4px;
    transform: translateY(10px) scale(0.98);
    opacity: 0;
    animation: cardSlideIn 0.5s cubic-bezier(0.34, 1.56, 0.64, 1) forwards;
    transition: all 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
    backdrop-filter: blur(10px);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
    cursor: pointer;
  }
  
  /* Only show text cursor when hovering over editable content, not buttons */
  .fcItem.editing {
    cursor: default;
  }
  
  .fcItem.editing .fcQuestion,
  .fcItem.editing .fcAnswer {
    cursor: text;
  }
  
  /* Ensure buttons always show pointer cursor, even in edit mode */
  .fcItem .copyBtn,
  .fcItem .deleteBtn,
  .fcItem .fcActions {
    cursor: pointer !important;
  }
  
  /* When hovering over buttons in edit mode, show pointer */
  .fcItem.editing .copyBtn,
  .fcItem.editing .deleteBtn {
    cursor: pointer !important;
  }
  .fcItem:nth-child(1) { animation-delay: 0.1s; }
  .fcItem:nth-child(2) { animation-delay: 0.15s; }
  .fcItem:nth-child(3) { animation-delay: 0.2s; }
  .fcItem:nth-child(4) { animation-delay: 0.25s; }
  .fcItem:nth-child(5) { animation-delay: 0.3s; }
  .fcItem:nth-child(n+6) { animation-delay: 0.35s; }
  .fcItem:hover { 
    transform: translateY(-4px) scale(1.02);
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.2);
    border-color: rgba(255, 255, 255, 0.15);
    background: rgba(255,255,255,0.04);
  }
  /* Flashcard Question & Answer Wrappers - Modern Design */
  .fcQuestionWrapper,
  .fcAnswerWrapper {
    margin-bottom: 16px;
    position: relative;
  }
  
  .fcQuestionWrapper:last-child,
  .fcAnswerWrapper:last-child {
    margin-bottom: 0;
  }
  
  .fcLabel {
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--accent-purple);
    margin-bottom: 6px;
    opacity: 0.8;
    transition: opacity 0.2s ease;
  }
  
  .fcItem:hover .fcLabel {
    opacity: 1;
  }
  
  .fcQuestion,
  .fcAnswer {
    display: block; 
    font-size: 13.5px;
    color: var(--text-primary);
    line-height: 1.6;
    transition: all 0.3s ease;
    word-wrap: break-word;
    white-space: pre-wrap;
    user-select: text;
    -webkit-user-select: text;
    -moz-user-select: text;
    -ms-user-select: text;
    padding: 10px 12px;
    background: rgba(255, 255, 255, 0.03);
    border-left: 3px solid var(--accent-purple);
    border-radius: 6px;
    margin-top: 4px;
  }
  
  .fcAnswer {
    background: rgba(255, 255, 255, 0.02);
    border-left-color: rgba(139, 92, 246, 0.5);
  }
  
  .fcItem:hover .fcQuestion,
  .fcItem:hover .fcAnswer {
    background: rgba(255, 255, 255, 0.05);
    border-left-color: var(--accent-purple);
  }
  
  [data-theme="light"] .fcQuestion,
  [data-theme="light"] .fcAnswer,
  body.light-mode .fcQuestion,
  body.light-mode .fcAnswer {
    background: rgba(0, 0, 0, 0.02);
    color: rgba(0, 0, 0, 0.85);
  }
  
  [data-theme="light"] .fcItem:hover .fcQuestion,
  [data-theme="light"] .fcItem:hover .fcAnswer,
  body.light-mode .fcItem:hover .fcQuestion,
  body.light-mode .fcItem:hover .fcAnswer {
    background: rgba(0, 0, 0, 0.04);
  }
  
  [data-theme="light"] .fcLabel,
  body.light-mode .fcLabel {
    color: var(--accent-purple);
  }
  
  /* Editing state styles */
  .fcItem.editing {
    background: rgba(255,255,255,0.06);
    border-color: rgba(255, 255, 255, 0.2);
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25);
  }
  
  .fcItem.editing .fcQuestion,
  .fcItem.editing .fcAnswer {
    background: rgba(255, 255, 255, 0.08) !important;
    border: 1px solid rgba(255, 255, 255, 0.2) !important;
    border-left: 3px solid var(--accent-purple) !important;
    border-radius: 6px;
    padding: 10px 12px;
    margin: 4px 0;
    outline: none;
    color: #ffffff;
    min-height: 24px;
  }
  
  .fcItem.editing .fcQuestion:focus,
  .fcItem.editing .fcAnswer:focus {
    border-color: var(--accent-purple) !important;
    border-left-width: 4px !important;
    background: rgba(255, 255, 255, 0.12) !important;
    box-shadow: 0 0 0 2px rgba(139, 92, 246, 0.2);
  }
  
  [data-theme="light"] .fcItem.editing .fcQuestion,
  [data-theme="light"] .fcItem.editing .fcAnswer,
  body.light-mode .fcItem.editing .fcQuestion,
  body.light-mode .fcItem.editing .fcAnswer {
    background: rgba(0, 0, 0, 0.04) !important;
    border: 1px solid rgba(0, 0, 0, 0.15) !important;
    color: rgba(0, 0, 0, 0.9) !important;
  }
  
  [data-theme="light"] .fcItem.editing .fcQuestion:focus,
  [data-theme="light"] .fcItem.editing .fcAnswer:focus,
  body.light-mode .fcItem.editing .fcQuestion:focus,
  body.light-mode .fcItem.editing .fcAnswer:focus {
    border-color: rgba(139, 92, 246, 0.6) !important;
    background: rgba(0, 0, 0, 0.06) !important;
    box-shadow: 0 0 0 2px rgba(139, 92, 246, 0.15) !important;
  }
  
  .fcItem .fcActions { 
    position: absolute; 
    top: 8px; 
    right: 8px; 
    display: flex; 
    gap: 4px; 
    opacity: 0; 
    transform: translateX(10px);
    transition: all 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
  }
  .fcItem:hover .fcActions { 
    opacity: 1;
    transform: translateX(0);
  }
  
  /* Copy button for flashcards - simple icon, no box */
  .fcItem .copyBtn {
    position: absolute;
    top: 8px;
    right: 30px;
    background: transparent;
    border: none;
    padding: 0;
    width: 14px;
    height: 14px;
    cursor: pointer;
    transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1),
                background-image 0.3s ease,
                transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
    opacity: 0;
    transform: translateX(8px);
    z-index: 5;
    color: rgba(255, 255, 255, 0.4);
    display: flex;
    align-items: center;
    justify-content: center;
    background-image: url('icons/copy_icon.png');
    background-size: contain;
    background-repeat: no-repeat;
    background-position: center;
    filter: brightness(0) invert(0.5);
  }
  
  /* Copy button checkmark state for flashcards */
  .fcItem .copyBtn.copied {
    background-image: none;
    filter: brightness(0) invert(1);
  }
  
  .fcItem .copyBtn.copied::before {
    content: '✓';
    position: absolute;
    font-size: 12px;
    font-weight: bold;
    color: #4ade80;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 100%;
    height: 100%;
    animation: checkmarkAppear 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
  }
  
  .fcItem:hover .copyBtn {
    opacity: 1;
    transform: translateX(0);
  }
  
  .fcItem .copyBtn:hover {
    color: rgba(255, 255, 255, 0.7);
    transform: translateX(0) scale(1.1);
    filter: brightness(0) invert(0.8);
  }
  
  .fcItem .copyBtn:active {
    transform: translateX(0) scale(0.95);
  }
  
  .fcItem.editing .copyBtn {
    display: none;
  }
  .fcItem .fcActions button { 
    background: transparent;
    border: none; 
    padding: 0;
    font-size: 10px; 
    cursor: pointer;
    transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
    color: rgba(255, 255, 255, 0.4);
    display: flex;
    align-items: center;
    justify-content: center;
    width: auto;
    height: auto;
  }
  
  .fcItem .fcActions .deleteBtn {
    width: 14px;
    height: 14px;
    position: relative;
  }
  
  /* Delete icon - simple X/trash icon */
  .fcItem .fcActions .deleteBtn::before,
  .fcItem .fcActions .deleteBtn::after {
    content: '';
    position: absolute;
    width: 10px;
    height: 1.5px;
    background: currentColor;
    border-radius: 1px;
    top: 50%;
    left: 50%;
  }
  
  .fcItem .fcActions .deleteBtn::before {
    transform: translate(-50%, -50%) rotate(45deg);
  }
  
  .fcItem .fcActions .deleteBtn::after {
    transform: translate(-50%, -50%) rotate(-45deg);
  }
  
  .fcItem .fcActions button:hover { 
    color: rgba(255, 255, 255, 0.7);
    transform: scale(1.1);
  }
  
  .fcItem .fcActions .deleteBtn:hover { 
    color: rgba(255, 100, 100, 0.7);
  }
  
  @keyframes cardSlideIn {
    from {
      opacity: 0;
      transform: translateY(20px) scale(0.95);
    }
    to {
      opacity: 1;
      transform: translateY(0) scale(1);
    }
  }



  /* Compact mode tweaks */
  .compact #pdfFrame { filter: saturate(0.95); }
  .compact #sidebarButtons .bigBtn { padding: 6px 10px; font-size: 11px; }
  
  /* Settings Modal - Professional seamless design */
  /* macOS System Settings Style Modal */
  #settingsModal { 
    position: fixed; 
    inset: 0; 
    background: rgba(0, 0, 0, 0);
    z-index: 10001; 
    display: none; 
    align-items: center;
    justify-content: center;
    padding: 40px 20px;
    backdrop-filter: blur(0px);
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
    opacity: 0;
    transition: opacity 0.25s cubic-bezier(0.33, 1, 0.68, 1),
                backdrop-filter 0.25s cubic-bezier(0.33, 1, 0.68, 1),
                background 0.25s cubic-bezier(0.33, 1, 0.68, 1);
    pointer-events: none;
  }
  #settingsModal.show { 
    display: flex;
    opacity: 1;
    pointer-events: auto;
    background: rgba(0, 0, 0, 0.4);
    backdrop-filter: blur(20px) saturate(180%);
  }
  #settingsContent {
    background: rgba(28, 28, 30, 0.95);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 14px;
    width: 840px;
    height: 580px;
    max-width: 90vw;
    max-height: 90vh;
    display: flex;
    overflow: hidden;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4), 0 0 0 0.5px rgba(255, 255, 255, 0.1);
    transform-origin: center center;
    transform: scale(0.96);
    opacity: 0;
    transition: transform 0.3s cubic-bezier(0.33, 1, 0.68, 1),
                opacity 0.3s cubic-bezier(0.33, 1, 0.68, 1);
    backdrop-filter: blur(40px) saturate(180%);
    position: relative;
  }
  
  [data-theme="light"] #settingsContent {
    background: rgba(240, 240, 242, 0.98);
    border: 1px solid rgba(0, 0, 0, 0.15);
  }
  
  #settingsModal.show #settingsContent {
    transform: scale(1);
    opacity: 1;
  }
  /* Sidebar Navigation */
  #settingsSidebar {
    width: 220px;
    background: rgba(20, 20, 22, 0.6);
    border-right: 1px solid rgba(255, 255, 255, 0.08);
    padding: 12px 0;
    overflow-y: auto;
    flex-shrink: 0;
  }
  
  [data-theme="light"] #settingsSidebar {
    background: rgba(232, 232, 234, 0.95);
    border-right: 1px solid rgba(0, 0, 0, 0.1);
  }
  
  .settingsNavItem {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 16px;
    margin: 0 8px;
    border-radius: 8px;
    cursor: pointer;
    transition: background 0.15s ease;
    color: rgba(255, 255, 255, 0.6);
    font-size: 13px;
    font-weight: 500;
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif;
    user-select: none;
  }
  
  [data-theme="light"] .settingsNavItem {
    color: rgba(0, 0, 0, 0.6);
  }
  
  .settingsNavItem:hover {
    background: rgba(255, 255, 255, 0.05);
    color: rgba(255, 255, 255, 0.8);
  }
  
  [data-theme="light"] .settingsNavItem:hover {
    background: rgba(0, 0, 0, 0.12);
    color: rgba(0, 0, 0, 0.85);
  }
  
  .settingsNavItem.active {
    background: rgba(255, 255, 255, 0.08);
    color: rgba(255, 255, 255, 0.95);
  }
  
  [data-theme="light"] .settingsNavItem.active {
    background: rgba(0, 0, 0, 0.2);
    color: rgba(0, 0, 0, 0.95);
  }
  
  /* Active state should remain darker even on hover */
  [data-theme="light"] .settingsNavItem.active:hover {
    background: rgba(0, 0, 0, 0.25);
    color: rgba(0, 0, 0, 0.95);
  }
  
  .settingsNavIcon {
    font-size: 16px;
    width: 20px;
    text-align: center;
  }
  
  /* Main Content Area */
  #settingsMain {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  #settingsHeader {
    padding: 24px 32px 20px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  }
  
  [data-theme="light"] #settingsHeader {
    border-bottom: 1px solid rgba(0, 0, 0, 0.1);
  }
  
  #settingsTitle {
    margin: 0;
    font-size: 28px;
    font-weight: 600;
    color: rgba(255, 255, 255, 0.95);
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif;
    letter-spacing: -0.5px;
  }
  
  [data-theme="light"] #settingsTitle {
    color: rgba(0, 0, 0, 0.95);
  }
  #settingsBody {
    flex: 1;
    overflow-y: auto;
    padding: 24px 32px;
  }
  .settingsPage {
    animation: fadeIn 0.2s ease;
  }
  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(4px); }
    to { opacity: 1; transform: translateY(0); }
  }
  
  /* Close Button */
  .settingsCloseBtn {
    position: absolute;
    top: 12px;
    right: 12px;
    width: 28px;
    height: 28px;
    background: transparent;
    border: none;
    color: rgba(255, 255, 255, 0.5);
    font-size: 18px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 6px;
    transition: all 0.15s ease;
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif;
    z-index: 10;
  }
  
  [data-theme="light"] .settingsCloseBtn {
    color: rgba(0, 0, 0, 0.5);
  }
  
  .settingsCloseBtn:hover {
    background: rgba(255, 255, 255, 0.1);
    color: rgba(255, 255, 255, 0.8);
  }
  
  [data-theme="light"] .settingsCloseBtn:hover {
    background: rgba(0, 0, 0, 0.1);
    color: rgba(0, 0, 0, 0.8);
  }
  /* Settings Groups and Rows */
  .settingsGroup {
    margin-bottom: 32px;
  }
  .settingsGroup:last-child {
    margin-bottom: 0;
  }
  .settingsRow {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    padding: 12px 0;
    gap: 24px;
  }
  .settingsLabel {
    flex: 1;
    min-width: 0;
  }
  .settingsLabel label {
    display: block;
    font-size: 13px;
    font-weight: 600;
    color: rgba(255, 255, 255, 0.9);
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif;
    margin-bottom: 4px;
  }
  
  [data-theme="light"] .settingsLabel label {
    color: rgba(0, 0, 0, 0.9);
  }
  
  .settingsDescription {
    font-size: 11px;
    color: rgba(255, 255, 255, 0.5);
    line-height: 1.4;
    margin-top: 2px;
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif;
  }
  
  [data-theme="light"] .settingsDescription,
  body.light-mode .settingsDescription {
    color: rgba(0, 0, 0, 0.7) !important;
  }
  .settingsControl {
    flex-shrink: 0;
  }
  
  /* Settings row hover - darker in light mode */
  .settingsRow:hover {
    background: var(--surface-hover);
    border-radius: 6px;
    margin: 0 -8px;
    padding: 12px 8px;
  }
  
  [data-theme="light"] .settingsRow:hover {
    background: rgba(0, 0, 0, 0.04) !important;
  }
  /* macOS Style Select Dropdown */
  .macSelect {
    appearance: none !important;
    -webkit-appearance: none !important;
    -moz-appearance: none !important;
    background: rgba(255, 255, 255, 0.08) !important;
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 6px;
    padding: 6px 32px 6px 10px;
    font-size: 13px;
    color: rgba(255, 255, 255, 0.9);
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif;
    cursor: pointer;
    transition: all 0.15s ease;
    min-width: 180px;
    background-image: url("data:image/svg+xml,%3Csvg width='12' height='8' viewBox='0 0 12 8' fill='none' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M1 1L6 6L11 1' stroke='%23ffffff' stroke-opacity='0.6' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") !important;
    background-repeat: no-repeat !important;
    background-position: right 10px center !important;
    background-size: 12px !important;
    background-color: rgba(255, 255, 255, 0.08) !important;
  }
  .macSelect:hover {
    background-color: rgba(255, 255, 255, 0.12);
    border-color: rgba(255, 255, 255, 0.16);
  }
  
  [data-theme="light"] .macSelect {
    background: rgba(255, 255, 255, 1) !important;
    background-color: rgba(255, 255, 255, 1) !important;
    border: 1px solid rgba(0, 0, 0, 0.15);
    color: rgba(0, 0, 0, 0.9);
    background-image: url("data:image/svg+xml,%3Csvg width='12' height='8' viewBox='0 0 12 8' fill='none' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M1 1L6 6L11 1' stroke='%23000000' stroke-opacity='0.6' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") !important;
    background-repeat: no-repeat !important;
    background-position: right 10px center !important;
    background-size: 12px !important;
  }
  
  [data-theme="light"] .macSelect:hover {
    background-color: rgba(0, 0, 0, 0.06) !important;
    border-color: rgba(0, 0, 0, 0.2);
    background-image: url("data:image/svg+xml,%3Csvg width='12' height='8' viewBox='0 0 12 8' fill='none' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M1 1L6 6L11 1' stroke='%23000000' stroke-opacity='0.6' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") !important;
    background-repeat: no-repeat !important;
    background-position: right 10px center !important;
    background-size: 12px !important;
  }
  
  .macSelect:focus {
    outline: none;
    background-color: rgba(255, 255, 255, 0.12);
    border-color: var(--accent-purple);
    box-shadow: 0 0 0 3px var(--accent-purple-glow);
  }
  
  [data-theme="light"] .macSelect:focus {
    background-color: rgba(0, 0, 0, 0.06) !important;
    border-color: var(--accent-purple);
    box-shadow: 0 0 0 3px var(--accent-purple-glow);
    background-image: url("data:image/svg+xml,%3Csvg width='12' height='8' viewBox='0 0 12 8' fill='none' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M1 1L6 6L11 1' stroke='%23000000' stroke-opacity='0.6' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") !important;
    background-repeat: no-repeat !important;
    background-position: right 10px center !important;
    background-size: 12px !important;
  }
  
  .macSelect option {
    background: rgba(28, 28, 30, 1);
    color: rgba(255, 255, 255, 0.9);
    padding: 8px;
  }
  
  [data-theme="light"] .macSelect option {
    background: rgba(255, 255, 255, 1) !important;
    color: rgba(0, 0, 0, 0.9) !important;
    padding: 8px;
  }
  
  /* Theme Presets */
  .themePresets {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
    margin-top: 8px;
  }
  
  .themePresetCard {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 12px;
    border-radius: 10px;
    background: rgba(255, 255, 255, 0.05);
    border: 2px solid rgba(255, 255, 255, 0.1);
    cursor: pointer;
    transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
    position: relative;
    overflow: hidden;
  }
  
  [data-theme="light"] .themePresetCard {
    background: rgba(0, 0, 0, 0.03);
    border: 2px solid rgba(0, 0, 0, 0.1);
  }
  
  .themePresetCard:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.2);
    border-color: var(--accent-purple);
    background: rgba(255, 255, 255, 0.08);
  }
  
  [data-theme="light"] .themePresetCard:hover {
    background: rgba(0, 0, 0, 0.05);
    border-color: var(--accent-purple);
  }
  
  .themePresetCard.active {
    border-color: var(--accent-purple);
    box-shadow: 0 0 0 3px var(--accent-purple-glow), 0 4px 16px rgba(0, 0, 0, 0.3);
    background: rgba(139, 92, 246, 0.1);
  }
  
  [data-theme="light"] .themePresetCard.active {
    background: rgba(139, 92, 246, 0.15);
  }
  
  .presetPreview {
    display: flex;
    gap: 4px;
    height: 48px;
    border-radius: 8px;
    overflow: hidden;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
  }
  
  .presetLight,
  .presetDark,
  .presetAccent {
    flex: 1;
    transition: transform 0.2s ease;
  }
  
  .themePresetCard:hover .presetLight,
  .themePresetCard:hover .presetDark,
  .themePresetCard:hover .presetAccent {
    transform: scale(1.05);
  }
  
  .presetLabel {
    font-size: 12px;
    font-weight: 600;
    color: rgba(255, 255, 255, 0.9);
    text-align: center;
    margin-top: 4px;
  }
  
  [data-theme="light"] .presetLabel {
    color: rgba(0, 0, 0, 0.8);
  }
  
  .themePresetCard.active .presetLabel {
    color: var(--accent-purple);
    font-weight: 700;
  }
  
  /* Text Color Selector - Segmented Control Style */
  #textColorSelector {
    display: flex;
    gap: 4px;
    background: var(--bg-tertiary);
    padding: 4px;
    border-radius: 8px;
    border: 1px solid var(--border-subtle);
  }
  
  .textColorOption {
    flex: 1;
    padding: 10px 16px;
    border-radius: 6px;
    border: none;
    background: transparent;
    color: var(--text-secondary);
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    position: relative;
  }
  
  .textColorOption:hover {
    background: var(--surface-hover);
    color: var(--text-primary);
  }
  
  .textColorOption[data-selected="true"],
  .textColorOption.active {
    background: var(--bg-secondary);
    color: var(--text-primary);
    font-weight: 600;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  }
  
  [data-theme="light"] .textColorOption[data-selected="true"],
  [data-theme="light"] .textColorOption.active {
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
  }
  
  /* Advanced Colors Panel Animation - Compact subsetting style */
  #advancedColorsPanel {
    overflow: hidden;
    transition: max-height 0.3s cubic-bezier(0.4, 0, 0.2, 1), 
                padding 0.3s cubic-bezier(0.4, 0, 0.2, 1),
                margin-top 0.3s cubic-bezier(0.4, 0, 0.2, 1),
                opacity 0.3s ease,
                border-color 0.3s ease;
    border: 1px solid var(--border-default);
    border-radius: 6px;
    background: var(--bg-tertiary);
    /* Normal width like other settings controls - match settingsControl width */
    box-sizing: border-box;
    width: auto;
    max-width: 400px;
    min-width: 180px;
    align-self: flex-end;
    margin-left: auto;
  }
  
  /* Container for button and panel to prevent width expansion */
  #advancedColorsToggle {
    transition: all 0.2s ease;
    /* Keep button width fixed */
    box-sizing: border-box;
    cursor: pointer;
  }
  
  #advancedColorsToggle:hover {
    background: var(--surface-hover) !important;
  }
  
  #advancedColorsToggleIcon {
    transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  }
  
  /* Advanced colors container - prevent width expansion */
  #advancedColorsToggle + #advancedColorsPanel,
  #advancedColorsPanel {
    box-sizing: border-box;
  }
  
  /* Ensure all buttons have pointer cursor */
  button, .macBtn {
    cursor: pointer;
  }
  
  /* Advanced colors buttons styling */
  #saveCustomPresetBtn {
    background: var(--accent-purple) !important;
    border-color: var(--accent-purple) !important;
    color: white !important;
    font-weight: 600;
  }
  
  #saveCustomPresetBtn:hover {
    opacity: 0.9;
    transform: translateY(-1px);
  }
  
  #applyCustomColorsBtn {
    background: var(--bg-secondary) !important;
    border-color: var(--border-default) !important;
    color: var(--text-primary) !important;
  }
  
  #applyCustomColorsBtn:hover {
    background: var(--surface-hover) !important;
    border-color: var(--border-default) !important;
  }
  
  /* Theme Color Picker */
  .themeColorPicker {
    display: flex;
    flex-direction: column;
    gap: 20px;
  }
  
  .themeColorModeSection {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  
  .themeColorModeLabel {
    font-size: 13px;
    font-weight: 600;
    color: rgba(255, 255, 255, 0.9);
    margin-bottom: 4px;
  }
  
  [data-theme="light"] .themeColorModeLabel {
    color: rgba(0, 0, 0, 0.8);
  }
  
  .themeColorPresets {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  
  .themeColorSwatch {
    width: 40px;
    height: 40px;
    border-radius: 8px;
    cursor: pointer;
    border: 2px solid transparent;
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    position: relative;
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.15);
  }
  
  .themeColorSwatch:hover {
    transform: translateY(-2px) scale(1.08);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
  }
  
  .themeColorSwatch.active {
    border-color: rgba(255, 255, 255, 0.9);
    box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.3), 0 4px 16px rgba(0, 0, 0, 0.4);
    transform: scale(1.12);
  }
  
  [data-theme="light"] .themeColorSwatch.active {
    border-color: rgba(0, 0, 0, 0.5);
    box-shadow: 0 0 0 3px rgba(0, 0, 0, 0.15), 0 4px 16px rgba(0, 0, 0, 0.25);
  }
  
  .themeColorSwatch::after {
    content: '✓';
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    color: white;
    font-size: 18px;
    font-weight: bold;
    opacity: 0;
    transition: opacity 0.2s ease;
    text-shadow: 0 1px 3px rgba(0, 0, 0, 0.6);
  }
  
  .themeColorSwatch.active::after {
    opacity: 1;
  }
  
  /* Light mode swatches need dark checkmark */
  .themeColorModeSection[data-mode="light"] .themeColorSwatch::after {
    color: #1a1a1a;
    text-shadow: 0 1px 2px rgba(255, 255, 255, 0.8);
  }
  
  .themeColorCustom {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-top: 4px;
  }
  
  .themeColorCustom input[type="color"] {
    width: 48px;
    height: 48px;
    border-radius: 8px;
    border: 2px solid rgba(255, 255, 255, 0.2);
    cursor: pointer;
    transition: all 0.2s ease;
    background: none;
    padding: 0;
    -webkit-appearance: none;
    appearance: none;
  }
  
  .themeColorCustom input[type="color"]::-webkit-color-swatch-wrapper {
    padding: 0;
    border-radius: 6px;
    overflow: hidden;
  }
  
  .themeColorCustom input[type="color"]::-webkit-color-swatch {
    border: none;
    border-radius: 6px;
  }
  
  .themeColorCustom input[type="color"]:hover {
    border-color: rgba(255, 255, 255, 0.4);
    transform: scale(1.05);
  }
  
  [data-theme="light"] .themeColorCustom input[type="color"] {
    border-color: rgba(0, 0, 0, 0.2);
  }
  
  [data-theme="light"] .themeColorCustom input[type="color"]:hover {
    border-color: rgba(0, 0, 0, 0.4);
  }
  
  .themeColorCustom label {
    font-size: 12px;
    color: rgba(255, 255, 255, 0.7);
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif;
    cursor: pointer;
  }
  
  [data-theme="light"] .themeColorCustom label {
    color: rgba(0, 0, 0, 0.7);
  }
  
  /* Accent Color Picker */
  .accentColorPicker {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  
  .accentColorPresets {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  
  .accentColorSwatch {
    width: 36px;
    height: 36px;
    border-radius: 8px;
    cursor: pointer;
    border: 2px solid transparent;
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    position: relative;
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
  }
  
  .accentColorSwatch:hover {
    transform: translateY(-2px) scale(1.05);
    box-shadow: 0 4px 8px rgba(0, 0, 0, 0.2);
  }
  
  .accentColorSwatch.active {
    border-color: rgba(255, 255, 255, 0.8);
    box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.2), 0 4px 12px rgba(0, 0, 0, 0.3);
    transform: scale(1.1);
  }
  
  [data-theme="light"] .accentColorSwatch.active {
    border-color: rgba(0, 0, 0, 0.4);
    box-shadow: 0 0 0 3px rgba(0, 0, 0, 0.1), 0 4px 12px rgba(0, 0, 0, 0.2);
  }
  
  .accentColorSwatch::after {
    content: '✓';
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    color: white;
    font-size: 16px;
    font-weight: bold;
    opacity: 0;
    transition: opacity 0.2s ease;
    text-shadow: 0 1px 2px rgba(0, 0, 0, 0.5);
  }
  
  .accentColorSwatch.active::after {
    opacity: 1;
  }
  
  .accentColorCustom {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  
  .accentColorCustom input[type="color"] {
    width: 48px;
    height: 48px;
    border-radius: 8px;
    border: 2px solid rgba(255, 255, 255, 0.2);
    cursor: pointer;
    transition: all 0.2s ease;
    background: none;
    padding: 0;
    -webkit-appearance: none;
    appearance: none;
  }
  
  .accentColorCustom input[type="color"]::-webkit-color-swatch-wrapper {
    padding: 0;
    border-radius: 6px;
    overflow: hidden;
  }
  
  .accentColorCustom input[type="color"]::-webkit-color-swatch {
    border: none;
    border-radius: 6px;
  }
  
  .accentColorCustom input[type="color"]:hover {
    border-color: rgba(255, 255, 255, 0.4);
    transform: scale(1.05);
  }
  
  [data-theme="light"] .accentColorCustom input[type="color"] {
    border-color: rgba(0, 0, 0, 0.2);
  }
  
  [data-theme="light"] .accentColorCustom input[type="color"]:hover {
    border-color: rgba(0, 0, 0, 0.4);
  }
  
  .accentColorCustom label {
    font-size: 12px;
    color: rgba(255, 255, 255, 0.7);
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif;
    cursor: pointer;
  }
  
  [data-theme="light"] .accentColorCustom label {
    color: rgba(0, 0, 0, 0.7);
  }
  
  /* Fullscreen Summary View Styles */
  .fullSummaryBackBtn {
    padding: 8px 16px;
    background: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.1);
    color: rgba(255,255,255,0.9);
    border-radius: 6px;
    cursor: pointer;
    font-size: 13px;
    font-family: var(--font-family);
    transition: all 0.2s ease;
  }
  
  [data-theme="light"] .fullSummaryBackBtn {
    background: rgba(0, 0, 0, 0.04);
    border: 1px solid rgba(0, 0, 0, 0.15);
    color: rgba(0, 0, 0, 0.85);
  }
  
  .fullSummaryBackBtn:hover {
    background: rgba(255,255,255,0.08);
  }
  
  [data-theme="light"] .fullSummaryBackBtn:hover {
    background: rgba(0, 0, 0, 0.08);
  }
  
  .fullSummaryEditBtn {
    padding: 8px 16px;
    background: rgba(139, 92, 246, 0.2);
    border: 1px solid rgba(139, 92, 246, 0.4);
    color: rgba(139, 92, 246, 0.9);
    border-radius: 6px;
    cursor: pointer;
    font-size: 13px;
    font-family: var(--font-family);
    transition: all 0.2s ease;
  }
  
  .fullSummaryEditBtn.done {
    background: rgba(76, 175, 80, 0.2);
    border-color: rgba(76, 175, 80, 0.4);
    color: rgba(76, 175, 80, 0.9);
  }
  
  .fullSummaryEditBtn:hover {
    background: rgba(139, 92, 246, 0.3);
  }
  
  .fullSummaryEditBtn.done:hover {
    background: rgba(76, 175, 80, 0.3);
  }
  
  .fullSummaryTitle {
    font-size: 28px;
    margin-bottom: 32px;
    color: #f0f0f2;
    font-family: var(--font-family);
    font-weight: 600;
  }
  
  [data-theme="light"] .fullSummaryTitle {
    color: rgba(0, 0, 0, 0.95);
  }
  
  .fullSummaryPage {
    border-radius: 12px;
    padding: 24px;
    margin-bottom: 20px;
    position: relative;
    transition: all 0.2s ease;
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.1);
  }
  
  .fullSummaryPage.editMode {
    background: rgba(255,255,255,0.06);
    border: 2px solid rgba(139, 92, 246, 0.4);
  }
  
  [data-theme="light"] .fullSummaryPage {
    background: rgba(0, 0, 0, 0.02);
    border: 1px solid rgba(0, 0, 0, 0.1);
  }
  
  [data-theme="light"] .fullSummaryPage.editMode {
    background: rgba(0, 0, 0, 0.04);
    border: 2px solid rgba(90, 159, 212, 0.5);
  }
  
  .fullSummaryPage:hover:not(.editMode) {
    background: rgba(255,255,255,0.05);
  }
  
  [data-theme="light"] .fullSummaryPage:hover:not(.editMode) {
    background: rgba(0, 0, 0, 0.03);
  }
  
  .fullSummaryPageTitle {
    color: #5a9fd4;
    font-size: 16px;
    margin: 0;
    font-weight: 600;
    font-family: var(--font-family);
  }
  
  .fullSummaryDeleteBtn {
    width: 32px;
    height: 32px;
    padding: 0;
    background: rgba(255, 59, 48, 0.15);
    border: 1px solid rgba(255, 59, 48, 0.3);
    color: rgba(255, 59, 48, 0.9);
    border-radius: 6px;
    cursor: pointer;
    font-size: 14px;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.2s ease;
  }
  
  .fullSummaryDeleteBtn:hover {
    background: rgba(255, 59, 48, 0.25);
  }
  
  .editableSummary {
    font-size: 14px;
    line-height: 1.7;
    white-space: pre-wrap;
    font-family: var(--font-family);
    outline: none;
    border-radius: 8px;
    padding: 16px;
    min-height: 60px;
    cursor: text;
  }
  
  .editableSummary.editMode {
    color: #e8e8ea;
    background: rgba(255,255,255,0.02);
    border: 1px solid rgba(255,255,255,0.08);
  }
  
  [data-theme="light"] .editableSummary.editMode {
    color: rgba(0, 0, 0, 0.85);
    background: rgba(0, 0, 0, 0.02);
    border: 1px solid rgba(0, 0, 0, 0.1);
  }
  
  .editableSummary:not(.editMode) {
    color: rgba(255, 255, 255, 0.85);
  }
  
  [data-theme="light"] .editableSummary:not(.editMode) {
    color: rgba(0, 0, 0, 0.8);
  }
  
  /* Rich Text Editor Toolbar */
  .richTextToolbar {
    display: flex;
    gap: 4px;
    padding: 8px;
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 8px;
    margin-bottom: 12px;
    flex-wrap: wrap;
    align-items: center;
  }
  
  [data-theme="light"] .richTextToolbar {
    background: rgba(0, 0, 0, 0.05);
    border: 1px solid rgba(0, 0, 0, 0.1);
  }
  
  .toolbarBtn {
    padding: 6px 12px;
    background: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 6px;
    color: rgba(255, 255, 255, 0.9);
    cursor: pointer;
    font-size: 13px;
    font-weight: 500;
    transition: all 0.2s ease;
    display: flex;
    align-items: center;
    gap: 4px;
    font-family: var(--font-family);
  }
  
  .toolbarBtn:hover {
    background: rgba(255, 255, 255, 0.15);
    border-color: rgba(255, 255, 255, 0.25);
  }
  
  .toolbarBtn.active {
    background: rgba(90, 159, 212, 0.3);
    border-color: rgba(90, 159, 212, 0.5);
    color: #5a9fd4;
  }
  
  [data-theme="light"] .toolbarBtn {
    background: rgba(0, 0, 0, 0.08);
    border: 1px solid rgba(0, 0, 0, 0.15);
    color: rgba(0, 0, 0, 0.8);
  }
  
  [data-theme="light"] .toolbarBtn:hover {
    background: rgba(0, 0, 0, 0.12);
    border-color: rgba(0, 0, 0, 0.25);
  }
  
  [data-theme="light"] .toolbarBtn.active {
    background: rgba(90, 159, 212, 0.2);
    border-color: rgba(90, 159, 212, 0.4);
    color: #5a9fd4;
  }
  
  .toolbarBtn.separator {
    width: 1px;
    height: 20px;
    background: rgba(255, 255, 255, 0.2);
    padding: 0;
    border: none;
    cursor: default;
  }
  
  [data-theme="light"] .toolbarBtn.separator {
    background: rgba(0, 0, 0, 0.2);
  }
  
  .toolbarBtn input[type="color"] {
    width: 30px;
    height: 24px;
    border: none;
    border-radius: 4px;
    cursor: pointer;
    padding: 0;
    background: transparent;
  }
  
  .toolbarBtn select {
    padding: 4px 8px;
    background: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 4px;
    color: rgba(255, 255, 255, 0.9);
    cursor: pointer;
    font-size: 13px;
    font-family: var(--font-family);
  }
  
  [data-theme="light"] .toolbarBtn select {
    background: rgba(0, 0, 0, 0.08);
    border: 1px solid rgba(0, 0, 0, 0.15);
    color: rgba(0, 0, 0, 0.8);
  }
  
  /* Color Palette */
  .colorPalette {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 4px;
    padding: 8px;
    background: rgba(30, 30, 35, 0.98);
    border: 1px solid rgba(255, 255, 255, 0.2);
    border-radius: 8px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
    position: absolute;
    top: 100%;
    left: 0;
    z-index: 10001;
    margin-top: 4px;
  }
  
  [data-theme="light"] .colorPalette {
    background: rgba(255, 255, 255, 0.98);
    border: 1px solid rgba(0, 0, 0, 0.2);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  }
  
  .colorPalette div {
    width: 24px;
    height: 24px;
    border: 1px solid rgba(255, 255, 255, 0.3);
    border-radius: 4px;
    cursor: pointer;
    transition: transform 0.2s ease, box-shadow 0.2s ease;
  }
  
  .colorPalette div:hover {
    transform: scale(1.15);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
    z-index: 1;
  }
  
  [data-theme="light"] .colorPalette div {
    border-color: rgba(0, 0, 0, 0.2);
  }
  
  /* Floating Term Explainer Widget */
  .termExplainerWidget {
    position: absolute;
    background: rgba(30, 30, 35, 0.98);
    border: 1px solid rgba(255, 255, 255, 0.2);
    border-radius: 12px;
    padding: 12px 16px;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
    z-index: 10000;
    min-width: 200px;
    max-width: 400px;
    display: none;
    pointer-events: auto;
    backdrop-filter: blur(10px);
  }
  
  [data-theme="light"] .termExplainerWidget {
    background: rgba(255, 255, 255, 0.98);
    border: 1px solid rgba(0, 0, 0, 0.2);
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.15);
  }
  
  .termExplainerWidget.visible {
    display: block;
  }
  
  .termExplainerWidget .widgetHeader {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
  }
  
  .termExplainerWidget .widgetTitle {
    font-size: 14px;
    font-weight: 600;
    color: #5a9fd4;
    margin: 0;
  }
  
  .termExplainerWidget .widgetClose {
    background: none;
    border: none;
    color: rgba(255, 255, 255, 0.6);
    cursor: pointer;
    font-size: 18px;
    padding: 0;
    width: 20px;
    height: 20px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 4px;
    transition: all 0.2s ease;
  }
  
  .termExplainerWidget .widgetClose:hover {
    background: rgba(255, 255, 255, 0.1);
    color: rgba(255, 255, 255, 0.9);
  }
  
  [data-theme="light"] .termExplainerWidget .widgetClose {
    color: rgba(0, 0, 0, 0.6);
  }
  
  [data-theme="light"] .termExplainerWidget .widgetClose:hover {
    background: rgba(0, 0, 0, 0.1);
    color: rgba(0, 0, 0, 0.9);
  }
  
  .termExplainerWidget .widgetContent {
    font-size: 13px;
    line-height: 1.6;
    color: rgba(255, 255, 255, 0.9);
    margin: 0;
  }
  
  [data-theme="light"] .termExplainerWidget .widgetContent {
    color: rgba(0, 0, 0, 0.8);
  }
  
  .termExplainerWidget .widgetLoading {
    display: flex;
    align-items: center;
    gap: 8px;
    color: rgba(255, 255, 255, 0.6);
    font-size: 13px;
  }
  
  [data-theme="light"] .termExplainerWidget .widgetLoading {
    color: rgba(0, 0, 0, 0.6);
  }
  
  .termExplainerWidget .widgetLoading::before {
    content: '';
    width: 16px;
    height: 16px;
    border: 2px solid rgba(255, 255, 255, 0.3);
    border-top-color: #5a9fd4;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  
  @keyframes spin {
    to { transform: rotate(360deg); }
  }
  
  /* macOS Style Toggle Switch */
  .macToggle {
    position: relative;
    display: inline-block;
    width: 50px;
    height: 30px;
    cursor: pointer;
  }
  .macToggle input {
    opacity: 0;
    width: 0;
    height: 0;
  }
  .toggleSlider {
    position: absolute;
    cursor: pointer;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background-color: rgba(255, 255, 255, 0.15);
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    border-radius: 15px;
    border: 1px solid rgba(255, 255, 255, 0.1);
  }
  
  [data-theme="light"] .toggleSlider {
    background-color: rgba(0, 0, 0, 0.12);
    border: 1px solid rgba(0, 0, 0, 0.15);
  }
  .toggleSlider:before {
    position: absolute;
    content: "";
    height: 22px;
    width: 22px;
    left: 3px;
    bottom: 3px;
    background-color: white;
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    border-radius: 50%;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
  }
  .macToggle input:checked + .toggleSlider {
    background-color: rgba(0, 122, 255, 0.9);
    border-color: rgba(0, 122, 255, 0.9);
  }
  .macToggle input:checked + .toggleSlider:before {
    transform: translateX(20px);
  }
  .macToggle:hover .toggleSlider {
    background-color: rgba(255, 255, 255, 0.2);
    border-color: rgba(255, 255, 255, 0.15);
  }
  .macToggle input:checked:hover + .toggleSlider {
    background-color: rgba(0, 122, 255, 1);
  }
  
  /* Hotkey Picker */
  .hotkeyPicker {
    min-width: 200px;
    min-height: 32px;
    background: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 6px;
    padding: 6px 10px;
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
    cursor: pointer;
    transition: all 0.15s ease;
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif;
  }
  .hotkeyPicker:focus {
    outline: none;
    background-color: rgba(255, 255, 255, 0.12);
    border-color: rgba(0, 122, 255, 0.6);
    box-shadow: 0 0 0 3px rgba(0, 122, 255, 0.1);
  }
  .hotkeyPicker:hover {
    background-color: rgba(255, 255, 255, 0.12);
    border-color: rgba(255, 255, 255, 0.16);
  }
  
  [data-theme="light"] .hotkeyPicker {
    background: rgba(0, 0, 0, 0.04);
    border: 1px solid rgba(0, 0, 0, 0.15);
  }
  
  [data-theme="light"] .hotkeyPicker:hover {
    background-color: rgba(0, 0, 0, 0.06);
    border-color: rgba(0, 0, 0, 0.2);
  }
  
  [data-theme="light"] .hotkeyPicker:focus {
    background-color: rgba(0, 0, 0, 0.06);
    border-color: rgba(0, 122, 255, 0.6);
    box-shadow: 0 0 0 3px rgba(0, 122, 255, 0.15);
  }
  
  .hotkeyPicker.recording {
    border-color: rgba(0, 122, 255, 0.8);
    background-color: rgba(0, 122, 255, 0.1);
    box-shadow: 0 0 0 3px rgba(0, 122, 255, 0.15);
  }
  
  [data-theme="light"] .hotkeyPicker.recording {
    border-color: rgba(0, 122, 255, 0.8);
    background-color: rgba(0, 122, 255, 0.12);
    box-shadow: 0 0 0 3px rgba(0, 122, 255, 0.2);
  }
  
  .hotkeyPlaceholder {
    color: rgba(255, 255, 255, 0.4);
    font-size: 13px;
    font-style: italic;
  }
  
  [data-theme="light"] .hotkeyPlaceholder {
    color: rgba(0, 0, 0, 0.4);
  }
  
  .hotkeyBadge {
    display: inline-flex;
    align-items: center;
    padding: 4px 8px;
    background: rgba(255, 255, 255, 0.15);
    border: 1px solid rgba(255, 255, 255, 0.2);
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
    color: rgba(255, 255, 255, 0.9);
    font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.2);
  }
  
  [data-theme="light"] .hotkeyBadge {
    background: rgba(0, 0, 0, 0.08);
    border: 1px solid rgba(0, 0, 0, 0.15);
    color: rgba(0, 0, 0, 0.85);
  }
  
  .hotkeyBadge:hover {
    background: rgba(255, 255, 255, 0.2);
  }
  
  [data-theme="light"] .hotkeyBadge:hover {
    background: rgba(0, 0, 0, 0.12);
  }

  /* Duplicate Detection Modal - Elegant UI */
  #duplicateModal {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0);
    backdrop-filter: blur(0px);
    z-index: 10002;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: auto;
    padding: 40px 20px;
    opacity: 0;
    transition: opacity 0.3s cubic-bezier(0.33, 1, 0.68, 1),
                backdrop-filter 0.3s cubic-bezier(0.33, 1, 0.68, 1),
                background 0.3s cubic-bezier(0.33, 1, 0.68, 1);
    pointer-events: none;
  }
  
  #duplicateModal.show {
    opacity: 1;
    background: rgba(0, 0, 0, 0.5);
    backdrop-filter: blur(20px) saturate(180%);
    pointer-events: auto;
  }
  
  #duplicateModalContent {
    background: var(--bg-primary) !important;
    border: 1px solid var(--border-default) !important;
    border-radius: 20px;
    width: 90%;
    max-width: 500px;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
    transform: scale(0.96) translateY(10px);
    transition: transform 0.3s cubic-bezier(0.33, 1, 0.68, 1),
                opacity 0.3s cubic-bezier(0.33, 1, 0.68, 1);
    opacity: 0;
  }
  
  #duplicateModal.show #duplicateModalContent {
    transform: scale(1) translateY(0);
    opacity: 1;
  }
  
  #duplicateModalHeader {
    padding: 28px 32px;
    border-bottom: 1px solid var(--border-subtle) !important;
    background: var(--bg-secondary) !important;
  }
  
  #duplicateModalHeader h2 {
    margin: 0;
    font-size: 22px;
    font-weight: 600;
    color: var(--text-primary) !important;
    font-family: var(--font-family);
  }
  
  #duplicateModalBody {
    padding: 24px 32px;
    color: var(--text-primary) !important;
    font-size: 14px;
    line-height: 1.6;
  }
  
  #duplicateModalBody p {
    color: var(--text-primary) !important;
  }
  
  #duplicateModalBody label {
    color: var(--text-secondary) !important;
  }
  
  #duplicateModalBody label span {
    color: var(--text-secondary) !important;
  }
  
  #duplicateRememberChoice {
    accent-color: var(--accent-purple) !important;
  }
  
  /* Duplicate stats styling - improved */
  #duplicateStats {
    display: inline-block !important;
    padding: 12px 16px !important;
    background: var(--bg-secondary) !important;
    border-radius: 8px !important;
    border: 1px solid var(--border-subtle) !important;
    font-size: 13px !important;
    color: var(--text-secondary) !important;
    font-weight: 500 !important;
    margin-top: 16px !important;
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif !important;
    letter-spacing: 0.2px;
    line-height: 1.5;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  }
  
  [data-theme="light"] #duplicateStats {
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
  }
  
  
  #duplicateModalActions {
    padding: 20px 32px 32px;
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
  }
  
  .duplicateModalBtn {
    padding: 12px 24px;
    border: none;
    border-radius: 10px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s ease;
    font-family: var(--font-family);
    flex: 1;
    min-width: 120px;
  }
  
  .duplicateModalBtn.primary {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
  }
  
  .duplicateModalBtn.primary:hover {
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
  }
  
  .duplicateModalBtn.secondary {
    background: rgba(255, 255, 255, 0.08);
    color: rgba(255, 255, 255, 0.9);
    border: 1px solid rgba(255, 255, 255, 0.12);
  }
  
  [data-theme="light"] .duplicateModalBtn.secondary {
    background: rgba(0, 0, 0, 0.05);
    color: rgba(0, 0, 0, 0.9);
    border: 1px solid rgba(0, 0, 0, 0.12);
  }
  
  .duplicateModalBtn.secondary:hover {
    background: rgba(255, 255, 255, 0.12);
  }
  
  [data-theme="light"] .duplicateModalBtn.secondary:hover {
    background: rgba(0, 0, 0, 0.08);
  }

  /* Library Modal - Elegant Overhaul */
  #libraryModal { 
    position: fixed; 
    inset: 0; 
    background: rgba(0, 0, 0, 0);
    backdrop-filter: blur(0px);
    z-index: 10001; 
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: auto;
    padding: 40px 20px;
    opacity: 0;
    transition: opacity 0.3s cubic-bezier(0.33, 1, 0.68, 1),
                backdrop-filter 0.3s cubic-bezier(0.33, 1, 0.68, 1),
                background 0.3s cubic-bezier(0.33, 1, 0.68, 1);
    pointer-events: none;
  }
  
  #libraryModal.show {
    opacity: 1;
    background: rgba(0, 0, 0, 0.5);
    backdrop-filter: blur(20px) saturate(180%);
    pointer-events: auto;
  }
  
  #libraryModalContent {
    background: rgba(28, 28, 30, 1);
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 20px;
    width: 90%;
    max-width: 1200px;
    max-height: 90vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
    transform: scale(0.96) translateY(10px);
    transition: transform 0.3s cubic-bezier(0.33, 1, 0.68, 1),
                opacity 0.3s cubic-bezier(0.33, 1, 0.68, 1);
    opacity: 0;
  }
  
  [data-theme="light"] #libraryModalContent {
    background: rgba(240, 240, 242, 1);
    border: 1px solid rgba(0, 0, 0, 0.18);
  }
  
  #libraryModal.show #libraryModalContent {
    transform: scale(1) translateY(0);
    opacity: 1;
  }
  
  /* Quizzer Mode Styles */
  #libraryModal.quizzer-mode #libraryModalContent {
    position: relative;
  }
  
  #libraryContent {
    transition: margin-right 0.4s cubic-bezier(0.4, 0, 0.2, 1);
  }
  
  #libraryModal.quizzer-mode #libraryContent {
    margin-right: 320px;
    transition: margin-right 0.4s cubic-bezier(0.4, 0, 0.2, 1);
  }
  
  #libraryModalContent {
    position: relative;
    display: flex;
    flex-direction: column;
    height: 100%;
  }
  
  #libraryQuizzerSettings {
    position: absolute;
    right: 0;
    top: 0;
    bottom: 0;
    width: 320px;
    background: rgba(36, 36, 38, 1);
    border-left: 1px solid rgba(255, 255, 255, 0.12);
    padding: 24px;
    padding-top: 140px;
    overflow-y: auto;
    transform: translateX(100%);
    transition: transform 0.5s cubic-bezier(0.34, 1.56, 0.64, 1);
    z-index: 5;
    display: none;
    box-shadow: -4px 0 20px rgba(0, 0, 0, 0.3);
  }
  
  [data-theme="light"] #libraryQuizzerSettings {
    background: rgba(250, 250, 250, 1);
    border-left: 1px solid rgba(0, 0, 0, 0.12);
    box-shadow: -4px 0 20px rgba(0, 0, 0, 0.1);
  }
  
  #libraryModal.quizzer-mode #libraryQuizzerSettings {
    transform: translateX(0);
    display: block;
  }
  
  #libraryModal.quizzer-mode #libraryHeader {
    z-index: 15;
    background: rgba(255, 255, 255, 0.02);
    backdrop-filter: blur(10px);
  }
  
  [data-theme="light"] #libraryModal.quizzer-mode #libraryHeader {
    background: rgba(255, 255, 255, 0.98);
  }
  
  #libraryModal.quizzer-mode #libraryHeader h1 {
    transition: all 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
  }
  
  #libraryModal.quizzer-mode .libraryClickHint {
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.3s ease;
  }
  
  /* Hide edit and favorite icons in quizzer mode */
  #libraryModal.quizzer-mode .libraryEditIcon,
  #libraryModal.quizzer-mode .libraryFavorite {
    display: none !important;
  }
  
  /* Hide folder actions (delete button, search/add button) in quizzer mode */
  #libraryModal.quizzer-mode .folderSearchAddButton,
  #libraryModal.quizzer-mode .libraryFolderHeader > div:last-child {
    display: none !important;
  }
  
  /* Hide folder edit icon in quizzer mode */
  #libraryModal.quizzer-mode .libraryFolder .libraryEditIcon {
    display: none !important;
  }
  
  /* Disable pointer events on edit/favorite buttons in quizzer mode */
  #libraryModal.quizzer-mode .libraryEditIcon,
  #libraryModal.quizzer-mode .libraryFavorite {
    pointer-events: none !important;
  }
  
  /* Main search bar for quizzer mode */
  #libraryQuizzerSearch {
    display: none;
    margin-top: 16px;
    position: relative;
  }
  
  #libraryModal.quizzer-mode #libraryQuizzerSearch {
    display: block;
  }
  
  #libraryQuizzerSearchInput {
    width: 100%;
    padding: 10px 16px;
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 8px;
    color: rgba(255, 255, 255, 0.9);
    font-size: 14px;
    font-family: var(--font-family);
    transition: all 0.2s ease;
  }
  
  #libraryQuizzerSearchInput:focus {
    outline: none;
    border-color: rgba(90, 159, 212, 0.6);
    background: rgba(255, 255, 255, 0.08);
  }
  
  [data-theme="light"] #libraryQuizzerSearchInput,
  body.light-mode #libraryQuizzerSearchInput {
    background: rgba(0, 0, 0, 0.03);
    border-color: rgba(0, 0, 0, 0.1);
    color: rgba(0, 0, 0, 0.9);
  }
  
  [data-theme="light"] #libraryQuizzerSearchInput:focus,
  body.light-mode #libraryQuizzerSearchInput:focus {
    border-color: rgba(90, 159, 212, 0.6);
    background: rgba(0, 0, 0, 0.05);
  }
  
  #libraryQuizzerSearchResults {
    position: absolute;
    top: 100%;
    left: 0;
    right: 0;
    background: rgba(36, 36, 38, 1);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 8px;
    margin-top: 8px;
    max-height: 300px;
    overflow-y: auto;
    z-index: 100;
    display: none;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
  }
  
  [data-theme="light"] #libraryQuizzerSearchResults,
  body.light-mode #libraryQuizzerSearchResults {
    background: rgba(255, 255, 255, 1);
    border-color: rgba(0, 0, 0, 0.1);
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.1);
  }
  
  .libraryQuizzerSearchResultItem {
    padding: 12px 16px;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 12px;
    transition: background 0.2s ease;
    border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  }
  
  .libraryQuizzerSearchResultItem:last-child {
    border-bottom: none;
  }
  
  .libraryQuizzerSearchResultItem:hover {
    background: rgba(255, 255, 255, 0.05);
  }
  
  [data-theme="light"] .libraryQuizzerSearchResultItem:hover,
  body.light-mode .libraryQuizzerSearchResultItem:hover {
    background: rgba(0, 0, 0, 0.03);
  }
  
  .libraryQuizzerSearchResultCheckbox {
    width: 18px;
    height: 18px;
    cursor: pointer;
    accent-color: var(--accent-purple);
    flex-shrink: 0;
  }
  
  .libraryQuizzerSearchResultName {
    flex: 1;
    color: rgba(255, 255, 255, 0.9);
    font-size: 14px;
  }
  
  [data-theme="light"] .libraryQuizzerSearchResultName,
  body.light-mode .libraryQuizzerSearchResultName {
    color: rgba(0, 0, 0, 0.9);
  }
  
  .libraryQuizzerSearchResultName .highlight {
    background: rgba(90, 159, 212, 0.3);
    padding: 2px 4px;
    border-radius: 3px;
    font-weight: 600;
  }
  
  /* Hide folder search bars in quizzer mode */
  #libraryModal.quizzer-mode .folderSearchWrapper {
    display: none !important;
  }
  
  #libraryModal.quizzer-mode {
    animation: quizzerModeEnter 0.6s cubic-bezier(0.34, 1.56, 0.64, 1);
  }
  
  #libraryModal.quizzer-mode #libraryModalContent {
    animation: quizzerContentPulse 0.6s cubic-bezier(0.34, 1.56, 0.64, 1);
  }
  
  @keyframes quizzerModeEnter {
    0% {
      filter: brightness(1) saturate(1);
    }
    50% {
      filter: brightness(0.98) saturate(1.05);
    }
    100% {
      filter: brightness(1) saturate(1);
    }
  }
  
  @keyframes quizzerContentPulse {
    0% {
      transform: scale(1);
    }
    50% {
      transform: scale(0.998);
    }
    100% {
      transform: scale(1);
    }
  }
  
  #libraryModal.quizzer-mode #libraryContent {
    position: relative;
  }
  
  #libraryModal.quizzer-mode #libraryContent::before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: linear-gradient(90deg, transparent 0%, rgba(90, 159, 212, 0.03) 50%, transparent 100%);
    pointer-events: none;
    z-index: 1;
    animation: shimmer 2s ease-in-out infinite;
  }
  
  @keyframes shimmer {
    0%, 100% {
      opacity: 0;
    }
    50% {
      opacity: 1;
    }
  }
  
  .libraryFile.quizzer-selectable {
    position: relative;
    cursor: pointer;
    transition: all 0.2s ease;
  }
  
  .libraryFile.quizzer-selectable:hover {
    transform: translateY(-1px);
  }
  
  .libraryFile.quizzer-selected {
    border-color: rgba(90, 159, 212, 0.6) !important;
    background: rgba(90, 159, 212, 0.1) !important;
  }
  
  .libraryFile.quizzer-selectable {
    padding-left: 48px !important;
  }
  
  .libraryFileQuizzerCheckbox {
    position: absolute;
    left: 16px;
    top: 50%;
    transform: translateY(-50%);
    width: 20px;
    height: 20px;
    cursor: pointer;
    accent-color: var(--accent-purple);
    z-index: 5;
    flex-shrink: 0;
  }
  
  .libraryFolder.quizzer-selectable {
    position: relative;
    padding-left: 48px !important;
    cursor: pointer;
  }
  
  .libraryFolderQuizzerCheckbox {
    position: absolute;
    left: 16px;
    top: 20px;
    width: 20px;
    height: 20px;
    cursor: pointer;
    accent-color: var(--accent-purple);
    z-index: 5;
    flex-shrink: 0;
  }
  
  .libraryFolderItems .libraryFileQuizzerCheckbox {
    left: 8px;
    top: 50%;
    transform: translateY(-50%);
  }
  
  .libraryFolderItems > div {
    position: relative;
    padding-left: 36px !important;
  }
  
  .libraryFolder.quizzer-selected {
    border-color: rgba(90, 159, 212, 0.6) !important;
    background: rgba(90, 159, 212, 0.1) !important;
  }
  
  #libraryHeader {
    display: flex;
    flex-direction: column;
    padding: 28px 40px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    background: rgba(255, 255, 255, 0.02);
    position: relative;
  }
  
  #libraryHeaderTop {
    display: flex;
    justify-content: space-between;
    align-items: center;
    width: 100%;
    margin-bottom: 12px;
    position: relative;
  }
  
  #libraryHeader h1 {
    margin: 0;
    font-size: 28px;
    font-weight: 600;
    color: rgba(255, 255, 255, 0.95);
    font-family: var(--font-family);
    letter-spacing: -0.5px;
    display: flex;
    align-items: center;
    gap: 12px;
  }
  
  [data-theme="light"] #libraryHeader h1 {
    color: rgba(0, 0, 0, 0.95);
  }
  
  .libraryClickHint {
    text-align: center;
    padding: 0;
    color: rgba(90, 159, 212, 0.9);
    font-size: 12px;
    font-family: var(--font-family);
    opacity: 0.85;
    transition: opacity 0.2s ease;
    font-weight: 500;
    letter-spacing: 0.3px;
    position: absolute;
    left: 50%;
    transform: translateX(-50%);
    white-space: nowrap;
  }
  
  .libraryClickHint:hover {
    opacity: 1;
  }
  
  [data-theme="light"] .libraryClickHint {
    color: rgba(90, 159, 212, 0.8);
  }
  
  .libraryClickHint span {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-weight: 700;
    letter-spacing: 0.5px;
    margin: 0 2px;
  }
  
  #libraryHeaderActions {
    display: flex;
    gap: 10px;
    align-items: center;
    position: relative;
  }
  
  #libraryClose {
    background: transparent;
    border: 1px solid rgba(255, 255, 255, 0.1);
    color: rgba(255, 255, 255, 0.7);
    font-size: 22px;
    width: 36px;
    height: 36px;
    border-radius: 8px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.2s ease;
    font-weight: 300;
    line-height: 1;
    padding: 0;
    margin: 0;
    position: relative;
    top: 0;
    right: 0;
  }
  
  [data-theme="light"] #libraryClose {
    border: 1px solid rgba(0, 0, 0, 0.15);
    color: rgba(0, 0, 0, 0.6);
  }
  
  #libraryClose:hover {
    background: rgba(255, 255, 255, 0.1);
    color: rgba(255, 255, 255, 0.95);
    border-color: rgba(255, 255, 255, 0.2);
    transform: scale(1.05);
  }
  
  [data-theme="light"] #libraryClose:hover {
    background: rgba(0, 0, 0, 0.08);
    color: rgba(0, 0, 0, 0.9);
    border-color: rgba(0, 0, 0, 0.2);
  }

  /* Flashcards Center Modal */
  #fcModal {
    position: fixed;
    inset: 0;
    background: #1a1a1c;
    z-index: 10000;
    display: none;
    overflow: auto;
    padding: 40px 60px;
  }
  #fcModal.show { display: block; }
  #fcHeader { display:flex; justify-content: space-between; align-items:center; margin-bottom: 24px; padding-bottom: 16px; border-bottom:1px solid #333; }
  #fcHeader h1 { margin:0; font-size: 28px; color:#f0f0f2; }
  #fcClose { background:#2a2a2c; border:1px solid #404040; color:#e8e8ea; font-size:24px; width:44px; height:44px; border-radius:8px; cursor:pointer; display:flex; align-items:center; justify-content:center; }
  #fcClose:hover { background:#333335; }
  #fcContent { max-width: 1000px; margin: 0 auto; }
  .deckList { display:grid; grid-template-columns: repeat(auto-fill, minmax(320px,1fr)); gap:16px; }
  .deckItem { background: rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.1); border-radius:12px; padding:16px; }
  .deckItem h3 { margin:0 0 8px 0; font-size:16px; color:#f0f0f2; }
  .deckItem .meta { font-size:12px; color:#a0a0a2; margin-bottom:10px; }
  .deckItem .actions { display:flex; gap:8px; }
  .deckItem .actions button { font-size:12px; padding:8px 12px; border-radius:6px; border:1px solid #404040; background:#2a2a2c; color:#e8e8ea; cursor:pointer; }
  .deckItem .actions button:hover { background:#333335; }
  
  #libraryContent {
    flex: 1;
    overflow-y: auto;
    padding: 32px 40px;
    display: flex;
    flex-direction: column;
    gap: 16px;
    background: rgba(28, 28, 30, 1);
  }
  
  [data-theme="light"] #libraryContent {
    background: rgba(240, 240, 242, 1);
  }
  
  #libraryContent::-webkit-scrollbar {
    width: 8px;
  }
  
  #libraryContent::-webkit-scrollbar-track {
    background: transparent;
  }
  
  #libraryContent::-webkit-scrollbar-thumb {
    background: rgba(255, 255, 255, 0.1);
    border-radius: 4px;
  }
  
  [data-theme="light"] #libraryContent::-webkit-scrollbar-thumb {
    background: rgba(0, 0, 0, 0.15);
  }
  
  #libraryContent::-webkit-scrollbar-thumb:hover {
    background: rgba(255, 255, 255, 0.15);
  }
  
  [data-theme="light"] #libraryContent::-webkit-scrollbar-thumb:hover {
    background: rgba(0, 0, 0, 0.25);
  }
  
  .libraryFile.expanded .libraryFileContent::-webkit-scrollbar-thumb {
    background: rgba(255, 255, 255, 0.2);
    border-radius: 4px;
    transition: background 0.2s ease;
  }
  
  [data-theme="light"] .libraryFile.expanded .libraryFileContent::-webkit-scrollbar-thumb {
    background: rgba(0, 0, 0, 0.2);
  }
  
  .libraryFile.expanded .libraryFileContent::-webkit-scrollbar-thumb:hover {
    background: rgba(255, 255, 255, 0.3);
  }
  
  [data-theme="light"] .libraryFile.expanded .libraryFileContent::-webkit-scrollbar-thumb:hover {
    background: rgba(0, 0, 0, 0.3);
  }
  
  .libraryFile.expanded .libraryFileContent {
    scrollbar-color: rgba(255, 255, 255, 0.2) transparent;
  }
  
  [data-theme="light"] .libraryFile.expanded .libraryFileContent {
    scrollbar-color: rgba(0, 0, 0, 0.2) transparent;
  }
  
  .libraryFile {
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 14px;
    padding: 24px 28px;
    margin-bottom: 16px;
    transition: background 0.1s ease, border-color 0.1s ease;
    cursor: pointer;
    position: relative;
    overflow: visible;
  }
  
  [data-theme="light"] .libraryFile {
    background: #ffffff;
    border: 1px solid rgba(0, 0, 0, 0.1);
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05), 0 1px 2px rgba(0, 0, 0, 0.03);
  }
  
  .libraryFile.expanded {
    overflow: visible;
  }
  
  .libraryFile::before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 2px;
    background: linear-gradient(90deg, var(--accent-blue), rgba(90, 159, 212, 0.3));
    transform: scaleX(0);
    transition: transform 0.1s ease;
  }
  
  .libraryFile:hover {
    background: rgba(255, 255, 255, 0.05);
    border-color: rgba(255, 255, 255, 0.12);
  }
  
  [data-theme="light"] .libraryFile:hover {
    background: #ffffff;
    border-color: rgba(0, 0, 0, 0.15);
  }
  
  .libraryFile:hover::before {
    transform: scaleX(1);
  }
  
  .libraryFile.expanded {
    background: rgba(255, 255, 255, 0.04);
    border-color: rgba(90, 159, 212, 0.3);
  }
  
  [data-theme="light"] .libraryFile.expanded {
    background: #ffffff;
    border-color: rgba(90, 159, 212, 0.5);
    box-shadow: 0 4px 12px rgba(90, 159, 212, 0.15), 0 2px 4px rgba(0, 0, 0, 0.05);
  }
  
  .libraryFile.expanded::before {
    transform: scaleX(1);
    background: linear-gradient(90deg, var(--accent-blue), rgba(90, 159, 212, 0.5));
  }
  
  .libraryFileHeader {
    display: flex;
    justify-content: space-between;
    align-items: center;
    cursor: pointer;
    user-select: none;
  }
  
  .libraryTitleContainer {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    position: relative;
    flex: 1;
    min-width: 0;
  }
  
  /* Show buttons on hover anywhere on the file */
  .libraryFile:hover .libraryEditIcon,
  .libraryFile:hover .libraryFavorite {
    opacity: 0.6 !important;
  }
  
  .libraryFile:hover .libraryEditIcon:hover,
  .libraryFile:hover .libraryFavorite:hover {
    opacity: 1 !important;
  }
  
  /* Favorite star icon */
  .libraryFavorite {
    background: transparent;
    border: none;
    font-size: 16px;
    cursor: pointer;
    padding: 4px 6px;
    border-radius: 4px;
    transition: opacity 0.2s ease, background 0.2s ease, transform 0.2s ease;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 24px;
    height: 24px;
    opacity: 0;
    margin-left: 4px;
    flex-shrink: 0;
  }
  
  
  .libraryFavorite:hover {
    opacity: 1 !important;
    background: rgba(255, 255, 255, 0.1);
    transform: scale(1.1);
  }
  
  [data-theme="light"] .libraryFavorite:hover {
    background: rgba(0, 0, 0, 0.05);
  }
  
  .libraryFavorite.favorited {
    opacity: 1 !important;
    color: #ff69b4;
    filter: drop-shadow(0 0 4px rgba(255, 105, 180, 0.4));
  }
  
  .libraryFavorite:not(.favorited) {
    color: rgba(255, 255, 255, 0.5);
  }
  
  [data-theme="light"] .libraryFavorite:not(.favorited) {
    color: rgba(0, 0, 0, 0.4);
  }
  
  [data-theme="light"] .libraryFavorite.favorited {
    color: #ff69b4;
  }
  
  /* Folders section */
  .libraryFoldersSection {
    margin-bottom: 32px;
  }
  
  .libraryFoldersHeader {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 16px;
  }
  
  .libraryFoldersTitle {
    font-size: 14px;
    font-weight: 600;
    color: rgba(255, 255, 255, 0.7);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  
  [data-theme="light"] .libraryFoldersTitle,
  body.light-mode .libraryFoldersTitle {
    color: rgba(0, 0, 0, 0.85) !important;
  }
  
  .libraryCreateFolderBtn {
    padding: 6px 12px;
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.1);
    color: rgba(255, 255, 255, 0.85);
    border-radius: 6px;
    cursor: pointer;
    font-size: 12px;
    font-weight: 500;
    transition: all 0.2s ease;
  }
  
  .libraryCreateFolderBtn:hover {
    background: rgba(255, 255, 255, 0.1);
    border-color: rgba(255, 255, 255, 0.15);
  }
  
  [data-theme="light"] .libraryCreateFolderBtn,
  body.light-mode .libraryCreateFolderBtn {
    background: rgba(0, 0, 0, 0.04) !important;
    border: 1px solid rgba(0, 0, 0, 0.12) !important;
    color: rgba(0, 0, 0, 0.85) !important;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04) !important;
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
  }

  [data-theme="light"] .libraryCreateFolderBtn:hover,
  body.light-mode .libraryCreateFolderBtn:hover {
    background: rgba(0, 0, 0, 0.08) !important;
    border-color: rgba(0, 0, 0, 0.18) !important;
    color: rgba(0, 0, 0, 0.95) !important;
    box-shadow: 0 2px 4px rgba(0, 0, 0, 0.08) !important;
    transform: translateY(-0.5px) !important;
  }
  
  .libraryFolder {
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 12px;
    padding: 16px 20px;
    margin-bottom: 12px;
    transition: all 0.2s ease;
    position: relative;
  }
  
  [data-theme="light"] .libraryFolder,
  body.light-mode .libraryFolder {
    background: #ffffff !important;
    border: 1px solid rgba(0, 0, 0, 0.1) !important;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05), 0 1px 2px rgba(0, 0, 0, 0.03) !important;
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
  }
  
  .libraryFolder:hover {
    background: rgba(255, 255, 255, 0.05);
    border-color: rgba(255, 255, 255, 0.12);
  }
  
  [data-theme="light"] .libraryFolder:hover,
  body.light-mode .libraryFolder:hover {
    background: #ffffff !important;
    border-color: rgba(0, 0, 0, 0.15) !important;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08), 0 2px 4px rgba(0, 0, 0, 0.04) !important;
    transform: translateY(-1px) !important;
  }
  
  .libraryFolderHeader {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
  }
  
  .libraryFolderName {
    font-size: 16px;
    font-weight: 600;
    color: rgba(255, 255, 255, 0.9);
    margin: 0;
  }
  
  [data-theme="light"] .libraryFolderName,
  body.light-mode .libraryFolderName {
    color: rgba(0, 0, 0, 0.95) !important;
  }
  
  .libraryFolderItems {
    display: flex;
    flex-direction: column;
    gap: 8px;
    min-height: 40px;
  }
  
  .libraryFolderEmpty {
    color: rgba(255, 255, 255, 0.4);
    font-size: 12px;
    font-style: italic;
    padding: 8px;
    text-align: center;
  }
  
  [data-theme="light"] .libraryFolderEmpty,
  body.light-mode .libraryFolderEmpty {
    color: rgba(0, 0, 0, 0.65) !important;
  }
  
  .libraryFile.dragging {
    opacity: 0.9;
    transform: scale(0.95);
    z-index: 1000;
    background: rgba(30, 30, 30, 0.98) !important;
    border-color: rgba(90, 159, 212, 0.4) !important;
  }
  
  [data-theme="light"] .libraryFile.dragging {
    background: rgba(240, 240, 242, 0.98) !important;
  }
  
  .libraryFile.drag-over {
    border-color: var(--accent-blue);
    background: rgba(90, 159, 212, 0.1);
  }
  
  .libraryFolder.drag-over {
    border-color: var(--accent-blue);
    background: rgba(90, 159, 212, 0.15);
    border-width: 2px;
  }
  
  /* Folder Search Bar */
  .folderSearchWrapper {
    position: relative;
    display: flex;
    align-items: center;
    opacity: 0;
    transform: scale(0.95);
    pointer-events: none;
    transition: opacity 0.2s ease, transform 0.2s ease;
    margin-right: 8px;
  }
  
  .libraryFolder:hover .folderSearchWrapper {
    opacity: 1;
    transform: scale(1);
    pointer-events: auto;
  }
  
  .folderSearchInput {
    width: 200px;
    padding: 6px 10px;
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 6px;
    color: rgba(255, 255, 255, 0.9);
    font-size: 12px;
    font-family: var(--font-family);
    outline: none;
    transition: all 0.2s ease;
    box-sizing: border-box;
  }
  
  .folderSearchInput:focus {
    width: 250px;
    background: rgba(255, 255, 255, 0.08);
    border-color: var(--accent-blue);
    box-shadow: 0 0 0 3px rgba(90, 159, 212, 0.1);
  }
  
  .folderSearchInput::placeholder {
    color: rgba(255, 255, 255, 0.4);
  }
  
  [data-theme="light"] .folderSearchInput,
  body.light-mode .folderSearchInput {
    background: rgba(0, 0, 0, 0.06) !important;
    border: 1px solid rgba(0, 0, 0, 0.2) !important;
    color: rgba(0, 0, 0, 0.95) !important;
  }
  
  [data-theme="light"] .folderSearchInput:focus,
  body.light-mode .folderSearchInput:focus {
    background: rgba(0, 0, 0, 0.08) !important;
    border-color: rgba(90, 159, 212, 0.8) !important;
    box-shadow: 0 0 0 3px rgba(90, 159, 212, 0.15) !important;
  }
  
  [data-theme="light"] .folderSearchInput::placeholder,
  body.light-mode .folderSearchInput::placeholder {
    color: rgba(0, 0, 0, 0.6) !important;
  }
  
  .folderSearchResults {
    position: absolute;
    top: 100%;
    left: 0;
    right: 0;
    margin-top: 4px;
    max-height: 300px;
    overflow-y: auto;
    border-radius: 8px;
    background: rgba(28, 28, 30, 0.98);
    border: 1px solid rgba(255, 255, 255, 0.1);
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
    z-index: 1000;
    backdrop-filter: blur(20px);
  }
  
  [data-theme="light"] .folderSearchResults,
  body.light-mode .folderSearchResults {
    background: rgba(255, 255, 255, 0.98) !important;
    border: 1px solid rgba(0, 0, 0, 0.2) !important;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.15) !important;
  }
  
  .folderSearchResultItem {
    display: flex;
    align-items: center;
    padding: 10px 12px;
    cursor: pointer;
    transition: background 0.15s ease;
    border-bottom: 1px solid rgba(255, 255, 255, 0.05);
    color: rgba(255, 255, 255, 0.9);
  }
  
  [data-theme="light"] .folderSearchResultItem,
  body.light-mode .folderSearchResultItem {
    color: rgba(0, 0, 0, 0.9) !important;
    border-bottom-color: rgba(0, 0, 0, 0.1) !important;
  }
  
  .folderSearchResultItem:last-child {
    border-bottom: none;
  }
  
  .folderSearchResultItem:hover {
    background: rgba(255, 255, 255, 0.05);
  }
  
  [data-theme="light"] .folderSearchResultItem:hover,
  body.light-mode .folderSearchResultItem:hover {
    background: rgba(0, 0, 0, 0.08) !important;
  }
  
  .folderSearchResultCheckbox {
    width: 18px;
    height: 18px;
    margin-right: 10px;
    cursor: pointer;
    accent-color: var(--accent-blue);
  }
  
  .folderSearchResultName {
    flex: 1;
    font-size: 13px;
    color: rgba(255, 255, 255, 0.85);
  }
  
  [data-theme="light"] .folderSearchResultName,
  body.light-mode .folderSearchResultName {
    color: rgba(0, 0, 0, 0.9) !important;
  }
  
  .folderSearchResultName .highlight {
    background: rgba(90, 159, 212, 0.3);
    padding: 0 2px;
    border-radius: 2px;
    font-weight: 600;
  }
  
  .folderSearchAddButton {
    position: absolute;
    top: 100%;
    right: 0;
    margin-top: 4px;
    padding: 6px 12px;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    border: none;
    border-radius: 6px;
    color: white;
    font-size: 11px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s ease;
    font-family: var(--font-family);
    opacity: 0;
    transform: translateY(-5px);
    pointer-events: none;
    white-space: nowrap;
    z-index: 1001;
    box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
  }
  
  .folderSearchAddButton.show {
    opacity: 1;
    transform: translateY(0);
    pointer-events: auto;
  }
  
  .folderSearchAddButton:hover {
    transform: translateY(-1px);
    box-shadow: 0 6px 16px rgba(102, 126, 234, 0.5);
  }
  
  .folderSearchEmpty {
    padding: 20px;
    text-align: center;
    color: rgba(255, 255, 255, 0.5);
    font-size: 12px;
  }
  
  [data-theme="light"] .folderSearchEmpty {
    color: rgba(0, 0, 0, 0.5);
  }
  
  .libraryFileName {
    font-size: 18px;
    font-weight: 600;
    color: rgba(255, 255, 255, 0.95);
    margin: 0;
    font-family: var(--font-family);
    letter-spacing: -0.3px;
    display: inline-block;
  }
  
  [data-theme="light"] .libraryFileName {
    color: rgba(0, 0, 0, 0.95);
  }
  
  .libraryEditIcon {
    background: transparent;
    border: none;
    font-size: 14px;
    opacity: 0;
    cursor: pointer;
    padding: 4px 6px;
    border-radius: 4px;
    transition: opacity 0.2s ease, background 0.2s ease;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 20px;
    height: 20px;
    margin-left: 4px;
    vertical-align: middle;
  }
  
  
  .libraryEditIcon:hover {
    opacity: 1 !important;
    background: rgba(255, 255, 255, 0.1);
  }
  
  [data-theme="light"] .libraryEditIcon:hover {
    background: rgba(0, 0, 0, 0.05);
  }
  
  .libraryFileNameEdit {
    background: rgba(255, 255, 255, 0.1);
    border: 2px solid rgba(90, 159, 212, 0.6);
    border-radius: 6px;
    padding: 4px 12px;
    font-size: 18px;
    font-weight: 600;
    font-family: var(--font-family);
    color: rgba(255, 255, 255, 0.95);
    width: 100%;
    max-width: 400px;
    outline: none;
    flex: 1;
  }
  
  [data-theme="light"] .libraryFileNameEdit {
    background: rgba(0, 0, 0, 0.05);
    color: rgba(0, 0, 0, 0.95);
    border-color: rgba(90, 159, 212, 0.8);
  }
  
  .libraryFileStats {
    display: flex;
    gap: 20px;
    font-size: 12px;
    color: rgba(255, 255, 255, 0.5);
    font-weight: 500;
    align-items: center;
  }
  
  [data-theme="light"] .libraryFileStats,
  body.light-mode .libraryFileStats {
    color: rgba(0, 0, 0, 0.75) !important;
  }
  
  .libraryFileStats span {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  
  /* Indent stats to match title when in quizzer mode (checkbox adds padding) */
  #libraryModal.quizzer-mode .libraryFileStats {
    padding-left: 48px;
  }
  
  .libraryFileDate {
    font-size: 12px;
    color: rgba(255, 255, 255, 0.5);
    font-weight: 400;
    white-space: nowrap;
  }
  
  [data-theme="light"] .libraryFileDate,
  body.light-mode .libraryFileDate {
    color: rgba(0, 0, 0, 0.5) !important;
  }
  
  .libraryFileContent {
    max-height: 0;
    overflow: hidden;
    transition: max-height 0.4s cubic-bezier(0.34, 1.56, 0.64, 1),
                margin-top 0.4s cubic-bezier(0.34, 1.56, 0.64, 1),
                opacity 0.3s ease;
    margin-top: 0;
    opacity: 0;
  }
  
  .libraryFile.expanded .libraryFileContent {
    max-height: 600px;
    margin-top: 32px;
    margin-bottom: 0;
    opacity: 1;
    overflow-y: scroll;
    overflow-x: hidden;
    padding-right: 4px;
    padding-bottom: 60px;
    padding-left: 0;
    padding-top: 0;
    scrollbar-width: thin;
    scrollbar-color: rgba(255, 255, 255, 0.2) transparent;
    box-sizing: border-box;
    min-height: 0;
  }
  
  .libraryFile.expanded .libraryFileContent::-webkit-scrollbar {
    width: 8px;
  }
  
  .libraryFile.expanded .libraryFileContent::-webkit-scrollbar-track {
    background: transparent;
    border-radius: 4px;
  }
  
  .libraryFile.expanded .libraryFileContent::-webkit-scrollbar-thumb {
    background: rgba(255, 255, 255, 0.2);
    border-radius: 4px;
    transition: background 0.2s ease;
  }
  
  .libraryFile.expanded .libraryFileContent::-webkit-scrollbar-thumb:hover {
    background: rgba(255, 255, 255, 0.3);
  }
  
  .librarySection {
    margin-bottom: 32px;
    position: relative;
  }
  
  .librarySection:first-child {
    margin-top: 24px;
  }
  
  .librarySection:last-child {
    margin-bottom: 0;
    padding-bottom: 16px;
  }
  
  .librarySection::before {
    display: none;
  }
  
  .librarySection h3 {
    font-size: 11px;
    font-weight: 600;
    color: rgba(255, 255, 255, 0.5);
    margin: 0 0 20px 0;
    margin-top: 16px;
    text-transform: uppercase;
    letter-spacing: 1px;
    font-family: var(--font-family);
    padding-bottom: 0;
    border-bottom: none;
  }
  
  [data-theme="light"] .librarySection h3,
  body.light-mode .librarySection h3 {
    color: rgba(0, 0, 0, 0.8) !important;
    border-bottom: none;
  }
  
  .librarySummaryItem {
    background: rgba(255, 255, 255, 0.02);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 10px;
    padding: 16px 20px;
    margin-bottom: 12px;
    transition: all 0.2s ease;
  }
  
  [data-theme="light"] .librarySummaryItem,
  body.light-mode .librarySummaryItem {
    background: rgba(0, 0, 0, 0.03) !important;
    border: 1px solid rgba(0, 0, 0, 0.15) !important;
  }
  
  .librarySummaryItem:hover {
    background: rgba(255, 255, 255, 0.04);
    border-color: rgba(255, 255, 255, 0.1);
  }
  
  [data-theme="light"] .librarySummaryItem:hover,
  body.light-mode .librarySummaryItem:hover {
    background: rgba(0, 0, 0, 0.06) !important;
    border-color: rgba(0, 0, 0, 0.2) !important;
  }
  
  .librarySummaryItem strong {
    display: block;
    font-size: 12px;
    color: var(--accent-blue);
    margin-bottom: 8px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.3px;
  }
  
  .librarySummaryItem p {
    margin: 0;
    font-size: 13px;
    line-height: 1.7;
    color: rgba(255, 255, 255, 0.85);
    white-space: pre-wrap;
  }
  
  [data-theme="light"] .librarySummaryItem p,
  body.light-mode .librarySummaryItem p {
    color: rgba(0, 0, 0, 0.85) !important;
  }
  
  [data-theme="light"] .librarySummaryItem strong,
  body.light-mode .librarySummaryItem strong {
    color: rgba(0, 100, 200, 0.9) !important;
  }
  
  .libraryFlashcard {
    background: rgba(255, 255, 255, 0.02);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 10px;
    padding: 16px 20px;
    margin-bottom: 12px;
    transition: all 0.2s ease;
    border-left: 3px solid rgba(90, 159, 212, 0.4);
  }
  
  [data-theme="light"] .libraryFlashcard,
  body.light-mode .libraryFlashcard {
    background: rgba(0, 0, 0, 0.03) !important;
    border: 1px solid rgba(0, 0, 0, 0.15) !important;
    border-left: 3px solid rgba(90, 159, 212, 0.6) !important;
  }
  
  .libraryFlashcard:hover {
    background: rgba(255, 255, 255, 0.04);
    border-color: rgba(255, 255, 255, 0.1);
    border-left-color: var(--accent-blue);
    transform: translateX(2px);
  }
  
  [data-theme="light"] .libraryFlashcard:hover,
  body.light-mode .libraryFlashcard:hover {
    background: rgba(0, 0, 0, 0.06) !important;
    border-color: rgba(0, 0, 0, 0.2) !important;
    border-left-color: rgba(90, 159, 212, 0.8) !important;
  }
  
  .libraryFlashcard .q {
    display: block;
    font-size: 14px;
    font-weight: 600;
    color: rgba(255, 255, 255, 0.95);
    margin-bottom: 8px;
    font-family: var(--font-family);
  }
  
  [data-theme="light"] .libraryFlashcard .q,
  body.light-mode .libraryFlashcard .q {
    color: rgba(0, 0, 0, 0.95) !important;
  }
  
  .libraryFlashcard .a {
    display: block;
    font-size: 13px;
    color: rgba(255, 255, 255, 0.75);
    line-height: 1.6;
  }
  
  [data-theme="light"] .libraryFlashcard .a,
  body.light-mode .libraryFlashcard .a {
    color: rgba(0, 0, 0, 0.8) !important;
  }
  
  .libraryEmpty {
    text-align: center;
    padding: 80px 40px;
    color: rgba(255, 255, 255, 0.5);
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    min-height: 400px;
  }
  
  [data-theme="light"] .libraryEmpty,
  body.light-mode .libraryEmpty {
    color: rgba(0, 0, 0, 0.65) !important;
  }
  
  .libraryEmpty h2 {
    font-size: 24px;
    margin: 0 0 12px 0;
    color: rgba(255, 255, 255, 0.7);
    font-weight: 600;
    font-family: var(--font-family);
  }
  
  [data-theme="light"] .libraryEmpty h2,
  body.light-mode .libraryEmpty h2 {
    color: rgba(0, 0, 0, 0.85) !important;
  }
  
  .libraryEmpty p {
    font-size: 14px;
    margin: 0;
    color: rgba(255, 255, 255, 0.5);
    max-width: 400px;
    line-height: 1.6;
  }
  
  [data-theme="light"] .libraryEmpty p,
  body.light-mode .libraryEmpty p {
    color: rgba(0, 0, 0, 0.7) !important;
  }
  
  .libraryFileActions {
    display: flex;
    gap: 10px;
    margin-top: 24px;
    padding-top: 24px;
    border-top: 1px solid rgba(255, 255, 255, 0.08);
    margin-bottom: 8px;
  }
  
  [data-theme="light"] .libraryFileActions {
    border-top: 1px solid rgba(0, 0, 0, 0.1);
  }

  .libraryFileActions button {
    font-size: 12px;
    padding: 10px 18px;
    border-radius: 8px;
    border: 1px solid rgba(255, 255, 255, 0.1);
    background: rgba(255, 255, 255, 0.05);
    color: rgba(255, 255, 255, 0.85);
    cursor: pointer;
    transition: all 0.2s ease;
    font-weight: 500;
    font-family: var(--font-family);
  }
  
  [data-theme="light"] .libraryFileActions button,
  body.light-mode .libraryFileActions button {
    border: 1px solid rgba(0, 0, 0, 0.2) !important;
    background: rgba(0, 0, 0, 0.06) !important;
    color: rgba(0, 0, 0, 0.9) !important;
  }

  .libraryFileActions button:hover {
    background: rgba(255, 255, 255, 0.1);
    border-color: rgba(255, 255, 255, 0.15);
    transform: translateY(-1px);
    color: rgba(255, 255, 255, 0.95);
  }
  
  [data-theme="light"] .libraryFileActions button:hover,
  body.light-mode .libraryFileActions button:hover {
    background: rgba(0, 0, 0, 0.12) !important;
    border-color: rgba(0, 0, 0, 0.25) !important;
    color: rgba(0, 0, 0, 1) !important;
  }
  
  .libraryFileActions button.primary {
    background: var(--accent-blue);
    border-color: var(--accent-blue);
    color: #ffffff;
  }
  
  .libraryFileActions button.primary:hover {
    background: rgba(90, 159, 212, 0.9);
    border-color: rgba(90, 159, 212, 0.9);
    box-shadow: 0 4px 12px rgba(90, 159, 212, 0.3);
  }
  
  .libraryFileActions button.createMindmap {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important;
    border: none !important;
    color: white !important;
  }
  
  [data-theme="light"] .libraryFileActions button.createMindmap {
    color: white !important;
  }
  
  .libraryFileActions button.createMindmap:hover {
    background: linear-gradient(135deg, #764ba2 0%, #667eea 100%) !important;
    color: white !important;
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
  }
  
  [data-theme="light"] .libraryFileActions button.createMindmap:hover {
    color: white !important;
  }
  
  .libraryFileActions button.danger:hover {
    background: rgba(255, 59, 48, 0.15);
    border-color: rgba(255, 59, 48, 0.3);
    color: rgba(255, 59, 48, 1);
  }
  
  [data-theme="light"] .libraryFileActions button.danger,
  body.light-mode .libraryFileActions button.danger {
    border-color: rgba(220, 38, 38, 0.4) !important;
    color: rgba(220, 38, 38, 0.9) !important;
  }
  
  [data-theme="light"] .libraryFileActions button.danger:hover,
  body.light-mode .libraryFileActions button.danger:hover {
    background: rgba(220, 38, 38, 0.15) !important;
    border-color: rgba(220, 38, 38, 0.5) !important;
    color: rgba(220, 38, 38, 1) !important;
  }
  
  /* Comprehensive Light Mode Contrast Fixes - Ensure all text, buttons, borders have proper contrast */
  body.light-mode * {
    /* Override any light text colors that don't contrast well */
  }
  
  body.light-mode button:not(.libraryFileActions button.primary):not(.libraryFileActions button.createMindmap),
  [data-theme="light"] button:not(.libraryFileActions button.primary):not(.libraryFileActions button.createMindmap) {
    /* Ensure all buttons have dark text and visible borders */
    color: rgba(0, 0, 0, 0.9) !important;
    border-color: rgba(0, 0, 0, 0.2) !important;
  }
  
  body.light-mode input,
  [data-theme="light"] input {
    color: rgba(0, 0, 0, 0.9) !important;
    border-color: rgba(0, 0, 0, 0.2) !important;
  }
  
  body.light-mode textarea,
  [data-theme="light"] textarea {
    color: rgba(0, 0, 0, 0.9) !important;
    border-color: rgba(0, 0, 0, 0.2) !important;
  }
  
  body.light-mode select:not(.macSelect),
  [data-theme="light"] select:not(.macSelect) {
    color: rgba(0, 0, 0, 0.9) !important;
    border-color: rgba(0, 0, 0, 0.2) !important;
    background: rgba(255, 255, 255, 1) !important;
  }
  
  body.light-mode .rsBox,
  [data-theme="light"] .rsBox {
    border: 1px solid rgba(0, 0, 0, 0.1) !important;
  }
  
  body.light-mode .rsSection h3,
  [data-theme="light"] .rsSection h3 {
    color: rgba(0, 0, 0, 0.9) !important;
  }

  /* Mindmap Modal Styles */
  #mindmapModal {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0);
    backdrop-filter: blur(0px);
    z-index: 10002;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: auto;
    padding: 40px 20px;
    opacity: 0;
    transition: opacity 0.3s cubic-bezier(0.33, 1, 0.68, 1),
                backdrop-filter 0.3s cubic-bezier(0.33, 1, 0.68, 1),
                background 0.3s cubic-bezier(0.33, 1, 0.68, 1);
    pointer-events: none;
  }

  #mindmapModal.show {
    opacity: 1;
    background: rgba(0, 0, 0, 0.5);
    backdrop-filter: blur(20px) saturate(180%);
    pointer-events: auto;
  }

  #mindmapModalContent {
    background: rgba(28, 28, 30, 1);
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 20px;
    width: 95%;
    max-width: 1400px;
    max-height: 95vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
    transform: scale(0.96) translateY(10px);
    transition: transform 0.3s cubic-bezier(0.33, 1, 0.68, 1),
                opacity 0.3s cubic-bezier(0.33, 1, 0.68, 1);
    opacity: 0;
  }

  #mindmapModal.show #mindmapModalContent {
    transform: scale(1) translateY(0);
    opacity: 1;
  }

  #mindmapHeader {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 28px 40px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    background: rgba(255, 255, 255, 0.02);
    position: relative;
  }
  
  [data-theme="light"] #mindmapHeader {
    border-bottom: 1px solid rgba(0, 0, 0, 0.1);
    background: rgba(0, 0, 0, 0.02);
  }

  #mindmapHeader h1 {
    margin: 0;
    font-size: 28px;
    font-weight: 600;
    color: rgba(255, 255, 255, 0.95);
    font-family: var(--font-family);
    letter-spacing: -0.5px;
    display: flex;
    align-items: center;
    gap: 12px;
  }
  
  [data-theme="light"] #mindmapHeader h1 {
    color: rgba(0, 0, 0, 0.95);
  }

  #mindmapHeaderActions {
    display: flex;
    gap: 10px;
    align-items: center;
    position: relative;
  }

  .mindmapActionBtn {
    padding: 10px 20px;
    border: 1px solid rgba(255, 255, 255, 0.1);
    background: rgba(255, 255, 255, 0.05);
    color: rgba(255, 255, 255, 0.85);
    border-radius: 8px;
    cursor: pointer;
    font-size: 13px;
    font-weight: 500;
    transition: all 0.2s ease;
    font-family: var(--font-family);
  }
  
  [data-theme="light"] .mindmapActionBtn {
    border: 1px solid rgba(0, 0, 0, 0.15);
    background: rgba(0, 0, 0, 0.04);
    color: rgba(0, 0, 0, 0.8);
  }

  .mindmapActionBtn:hover {
    background: rgba(255, 255, 255, 0.1);
    border-color: rgba(255, 255, 255, 0.2);
    color: rgba(255, 255, 255, 1);
    transform: translateY(-1px);
  }
  
  [data-theme="light"] .mindmapActionBtn:hover {
    background: rgba(0, 0, 0, 0.08);
    border-color: rgba(0, 0, 0, 0.2);
    color: rgba(0, 0, 0, 0.95);
  }

  #mindmapGenerateBtn {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    border: none;
    color: white !important;
  }
  
  [data-theme="light"] #mindmapGenerateBtn {
    color: white !important;
  }

  #mindmapGenerateBtn:hover {
    background: linear-gradient(135deg, #764ba2 0%, #667eea 100%);
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
    color: white !important;
  }
  
  [data-theme="light"] #mindmapGenerateBtn:hover {
    color: white !important;
  }

  #mindmapClose {
    background: transparent;
    border: 1px solid rgba(255, 255, 255, 0.1);
    color: rgba(255, 255, 255, 0.7);
    width: 32px;
    height: 32px;
    border-radius: 8px;
    cursor: pointer;
    font-size: 24px;
    line-height: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.2s ease;
    padding: 0;
    position: relative;
    top: 0;
    right: 0;
  }
  
  [data-theme="light"] #mindmapClose {
    border: 1px solid rgba(0, 0, 0, 0.15);
    color: rgba(0, 0, 0, 0.6);
  }

  #mindmapClose:hover {
    background: rgba(255, 255, 255, 0.1);
    border-color: rgba(255, 255, 255, 0.2);
    color: rgba(255, 255, 255, 1);
  }
  
  [data-theme="light"] #mindmapClose:hover {
    background: rgba(0, 0, 0, 0.08);
    border-color: rgba(0, 0, 0, 0.2);
    color: rgba(0, 0, 0, 0.9);
  }

  #mindmapToolbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 16px 40px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    background: rgba(255, 255, 255, 0.01);
  }
  
  [data-theme="light"] #mindmapToolbar {
    border-bottom: 1px solid rgba(0, 0, 0, 0.1);
    background: rgba(0, 0, 0, 0.01);
  }

  #mindmapStatus {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  #mindmapStatusText {
    font-size: 13px;
    color: rgba(255, 255, 255, 0.6);
    font-family: var(--font-family);
  }
  
  [data-theme="light"] #mindmapStatusText {
    color: rgba(0, 0, 0, 0.6);
  }
  
  #mindmapTypeSelector {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  
  #mindmapTypeSelector label {
    color: rgba(255,255,255,0.6) !important;
    font-size: 12px;
    margin: 0;
    padding: 0;
    line-height: 1;
    display: flex;
    align-items: center;
    font-family: var(--font-family);
  }
  
  [data-theme="light"] #mindmapTypeSelector label {
    color: rgba(0, 0, 0, 0.6) !important;
  }

  #mindmapControls {
    display: flex;
    gap: 12px;
    align-items: center;
  }
  
  #mindmapZoomSliderContainer {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 4px 12px;
    background: rgba(255, 255, 255, 0.05);
    border-radius: 8px;
    border: 1px solid rgba(255, 255, 255, 0.1);
  }
  
  .mindmapZoomLabel {
    font-size: 14px;
    color: rgba(255, 255, 255, 0.6);
    font-weight: 500;
    user-select: none;
    min-width: 14px;
    text-align: center;
  }
  
  .mindmapZoomSlider {
    width: 120px;
    height: 4px;
    border-radius: 2px;
    background: rgba(255, 255, 255, 0.15);
    outline: none;
    -webkit-appearance: none;
    appearance: none;
    cursor: pointer;
  }
  
  .mindmapZoomSlider::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: rgba(255, 255, 255, 0.9);
    cursor: pointer;
    border: 1px solid rgba(255, 255, 255, 0.3);
    transition: background 0.2s ease, transform 0.2s ease;
  }
  
  .mindmapZoomSlider::-webkit-slider-thumb:hover {
    background: rgba(255, 255, 255, 1);
    transform: scale(1.1);
  }
  
  .mindmapZoomSlider::-moz-range-thumb {
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: rgba(255, 255, 255, 0.9);
    cursor: pointer;
    border: 1px solid rgba(255, 255, 255, 0.3);
    transition: background 0.2s ease, transform 0.2s ease;
  }
  
  .mindmapZoomSlider::-moz-range-thumb:hover {
    background: rgba(255, 255, 255, 1);
    transform: scale(1.1);
  }
  
  [data-theme="light"] #mindmapZoomSliderContainer {
    background: rgba(0, 0, 0, 0.04);
    border: 1px solid rgba(0, 0, 0, 0.15);
  }
  
  [data-theme="light"] .mindmapZoomLabel {
    color: rgba(0, 0, 0, 0.6);
  }
  
  [data-theme="light"] .mindmapZoomSlider {
    background: rgba(0, 0, 0, 0.15);
  }
  
  [data-theme="light"] .mindmapZoomSlider::-webkit-slider-thumb {
    background: rgba(0, 0, 0, 0.7);
    border: 1px solid rgba(0, 0, 0, 0.3);
  }
  
  [data-theme="light"] .mindmapZoomSlider::-webkit-slider-thumb:hover {
    background: rgba(0, 0, 0, 0.85);
  }
  
  [data-theme="light"] .mindmapZoomSlider::-moz-range-thumb {
    background: rgba(0, 0, 0, 0.7);
    border: 1px solid rgba(0, 0, 0, 0.3);
  }
  
  [data-theme="light"] .mindmapZoomSlider::-moz-range-thumb:hover {
    background: rgba(0, 0, 0, 0.85);
  }

  .mindmapControlBtn {
    width: 36px;
    height: 36px;
    border: 1px solid rgba(255, 255, 255, 0.1);
    background: rgba(255, 255, 255, 0.05);
    color: rgba(255, 255, 255, 0.7);
    border-radius: 8px;
    cursor: pointer;
    font-size: 18px;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.2s ease;
    font-family: var(--font-family);
  }
  
  [data-theme="light"] .mindmapControlBtn {
    border: 1px solid rgba(0, 0, 0, 0.15);
    background: rgba(0, 0, 0, 0.04);
    color: rgba(0, 0, 0, 0.65);
  }

  .mindmapControlBtn:hover {
    background: rgba(255, 255, 255, 0.1);
    border-color: rgba(255, 255, 255, 0.2);
    color: rgba(255, 255, 255, 1);
  }
  
  [data-theme="light"] .mindmapControlBtn:hover {
    background: rgba(0, 0, 0, 0.08);
    border-color: rgba(0, 0, 0, 0.2);
    color: rgba(0, 0, 0, 0.85);
  }

  #mindmapContainer {
    flex: 1;
    overflow: hidden;
    position: relative;
    background: rgba(20, 20, 22, 0.5);
  }
  
  #mindmapContainer::before {
    content: '';
    position: absolute;
    inset: 0;
    background-image: radial-gradient(circle, rgba(255, 255, 255, 0.15) 1px, transparent 1px);
    background-size: 40px 40px;
    background-position: 0 0;
    pointer-events: none;
    z-index: 0;
  }
  
  [data-theme="light"] #mindmapContainer::before {
    background-image: radial-gradient(circle, rgba(0, 0, 0, 0.15) 1px, transparent 1px);
    background-size: 40px 40px;
  }
  
  [data-theme="light"] #mindmapContainer {
    background: rgba(245, 245, 245, 0.5);
  }
  
  [data-theme="light"] #mindmapModalContent {
    background: rgba(250, 250, 250, 1);
    border: 1px solid rgba(0, 0, 0, 0.15);
  }
  
  [data-theme="light"] #mindmapModal.show {
    background: rgba(0, 0, 0, 0.3);
  }
  
  #mindmapCanvasWrapper {
    width: 100%;
    height: 100%;
    position: absolute;
    inset: 0;
    overflow: hidden;
    z-index: 1;
  }

  #mindmapCanvas {
    width: 100%;
    height: 100%;
    display: block;
    background: transparent;
    cursor: grab;
    position: absolute;
    inset: 0;
  }

  #mindmapCanvas:active {
    cursor: grabbing;
  }

  #mindmapModal.fullscreen {
    padding: 0;
    align-items: stretch;
  }

  #mindmapModal.fullscreen #mindmapModalContent {
    width: 100vw;
    height: 100vh;
    max-width: 100vw;
    max-height: 100vh;
    border-radius: 0;
  }

  #mindmapModal.fullscreen #mindmapContainer {
    flex: 1;
    min-height: 0;
  }
  
  /* True fullscreen mode - hide all UI except mindmap */
  #mindmapModal.trueFullscreen {
    background: rgba(0, 0, 0, 0.95);
  }
  
  #mindmapModal.trueFullscreen #mindmapModalContent {
    border: none;
    box-shadow: none;
  }
  
  #mindmapModal.trueFullscreen #mindmapHeader {
    display: none;
  }
  
  #mindmapModal.trueFullscreen #mindmapToolbar {
    display: none;
  }
  
  /* Elegant exit button in true fullscreen */
  #mindmapModal.trueFullscreen::after {
    content: 'ESC';
    position: fixed;
    top: 20px;
    right: 20px;
    padding: 8px 16px;
    background: rgba(255, 255, 255, 0.1);
    border: 1px solid rgba(255, 255, 255, 0.2);
    border-radius: 8px;
    color: rgba(255, 255, 255, 0.8);
    font-size: 12px;
    font-family: var(--font-family);
    z-index: 10004;
    pointer-events: none;
    backdrop-filter: blur(10px);
    transition: opacity 0.3s ease;
  }
  
  [data-theme="light"] #mindmapModal.trueFullscreen::after {
    background: rgba(0, 0, 0, 0.1);
    border: 1px solid rgba(0, 0, 0, 0.2);
    color: rgba(0, 0, 0, 0.8);
  }
  
  #mindmapModal.trueFullscreen #mindmapContainer {
    position: fixed;
    inset: 0;
    width: 100vw;
    height: 100vh;
  }

  .mindmapLoading {
    position: absolute;
    inset: 0;
    display: none;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    background: rgba(28, 28, 30, 0.95);
    z-index: 10;
    gap: 20px;
  }
  
  [data-theme="light"] .mindmapLoading {
    background: rgba(250, 250, 250, 0.95);
  }

  .mindmapLoading.show {
    display: flex;
  }

  .mindmapSpinner {
    width: 48px;
    height: 48px;
    border: 4px solid rgba(255, 255, 255, 0.1);
    border-top-color: #667eea;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  
  [data-theme="light"] .mindmapSpinner {
    border: 4px solid rgba(0, 0, 0, 0.1);
    border-top-color: #667eea;
  }

  @keyframes spin {
    to { transform: rotate(360deg); }
  }

  .mindmapLoading p {
    color: rgba(255, 255, 255, 0.7);
    font-size: 16px;
    font-family: var(--font-family);
    margin: 0;
  }
  
  [data-theme="light"] .mindmapLoading p {
    color: rgba(0, 0, 0, 0.7);
  }

  .mindmapEmpty {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    text-align: center;
    padding: 40px;
    z-index: 5;
  }

  .mindmapEmpty.hidden {
    display: none;
  }

  .mindmapEmptyIcon {
    font-size: 64px;
    margin-bottom: 24px;
    opacity: 0.6;
  }

  .mindmapEmpty h2 {
    font-size: 24px;
    font-weight: 600;
    color: rgba(255, 255, 255, 0.9);
    margin: 0 0 12px 0;
    font-family: var(--font-family);
  }
  
  [data-theme="light"] .mindmapEmpty h2 {
    color: rgba(0, 0, 0, 0.9);
  }

  .mindmapEmpty p {
    font-size: 15px;
    color: rgba(255, 255, 255, 0.6);
    margin: 0;
    font-family: var(--font-family);
    max-width: 400px;
    line-height: 1.6;
  }
  
  [data-theme="light"] .mindmapEmpty p {
    color: rgba(0, 0, 0, 0.6);
  }

  /* Drag and Drop Zone Overlay - Elegant Design */
  #dropZoneOverlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0);
    backdrop-filter: blur(0px);
    z-index: 100000;
    display: none;
    align-items: center;
    justify-content: center;
    pointer-events: none;
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    opacity: 0;
  }

  #dropZoneOverlay.active {
    display: flex;
    pointer-events: auto;
    opacity: 1;
    background: rgba(0, 0, 0, 0.6);
    backdrop-filter: blur(20px) saturate(180%);
  }

  #dropZoneContent {
    background: rgba(28, 28, 30, 0.98);
    border: 2px dashed rgba(90, 159, 212, 0.5);
    border-radius: 24px;
    padding: 80px 120px;
    text-align: center;
    transform: scale(0.9);
    transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5),
                0 0 0 1px rgba(90, 159, 212, 0.2),
                inset 0 0 0 1px rgba(255, 255, 255, 0.1);
    max-width: 600px;
    position: relative;
    overflow: hidden;
  }

  #dropZoneOverlay.active #dropZoneContent {
    transform: scale(1);
    border-color: rgba(90, 159, 212, 0.8);
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5),
                0 0 0 2px rgba(90, 159, 212, 0.4),
                inset 0 0 0 1px rgba(255, 255, 255, 0.15),
                0 0 40px rgba(90, 159, 212, 0.3);
  }

  #dropZoneContent::before {
    content: '';
    position: absolute;
    inset: 0;
    background: linear-gradient(135deg, rgba(90, 159, 212, 0.1) 0%, rgba(102, 126, 234, 0.1) 100%);
    opacity: 0;
    transition: opacity 0.3s ease;
    pointer-events: none;
  }

  #dropZoneOverlay.active #dropZoneContent::before {
    opacity: 1;
  }

  #dropZoneIcon {
    font-size: 80px;
    margin-bottom: 24px;
    display: block;
    opacity: 0.8;
    transform: translateY(-10px);
    transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
    filter: drop-shadow(0 4px 12px rgba(90, 159, 212, 0.3));
  }

  #dropZoneOverlay.active #dropZoneIcon {
    opacity: 1;
    transform: translateY(0) scale(1.1);
    filter: drop-shadow(0 8px 24px rgba(90, 159, 212, 0.5));
  }

  #dropZoneText {
    font-size: 24px;
    font-weight: 600;
    color: rgba(255, 255, 255, 0.95);
    margin-bottom: 12px;
    font-family: var(--font-family);
    letter-spacing: -0.5px;
  }

  #dropZoneSubtext {
    font-size: 15px;
    color: rgba(255, 255, 255, 0.6);
    font-family: var(--font-family);
    line-height: 1.6;
  }

  /* Light mode styles */
  [data-theme="light"] #dropZoneOverlay.active {
    background: rgba(0, 0, 0, 0.4);
  }

  [data-theme="light"] #dropZoneContent {
    background: rgba(250, 250, 250, 0.98);
    border-color: rgba(90, 159, 212, 0.4);
  }

  [data-theme="light"] #dropZoneOverlay.active #dropZoneContent {
    border-color: rgba(90, 159, 212, 0.7);
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3),
                0 0 0 2px rgba(90, 159, 212, 0.5),
                inset 0 0 0 1px rgba(0, 0, 0, 0.1),
                0 0 40px rgba(90, 159, 212, 0.2);
  }

  [data-theme="light"] #dropZoneText {
    color: rgba(0, 0, 0, 0.95);
  }

  [data-theme="light"] #dropZoneSubtext {
    color: rgba(0, 0, 0, 0.6);
  }

  /* Pulse animation for active drop zone */
  @keyframes dropZonePulse {
    0%, 100% {
      transform: scale(1);
      opacity: 1;
    }
    50% {
      transform: scale(1.02);
      opacity: 0.95;
    }
  }

  #dropZoneOverlay.active #dropZoneContent {
    animation: dropZonePulse 2s ease-in-out infinite;
  }
</style>
<script src="https://cdn.jsdelivr.net/npm/fuse.js@7.0.0/dist/fuse.js"></script>
</head>
<body>

  <!-- Settings Modal -->
  <div id="settingsModal">
    <div id="settingsContent">
      <div id="settingsSidebar">
        <div class="settingsNavItem active" data-section="general">
          <span class="settingsNavIcon"></span>
          <span>General</span>
        </div>
        <div class="settingsNavItem" data-section="appearance">
          <span class="settingsNavIcon"></span>
          <span>Appearance</span>
        </div>
        <div class="settingsNavItem" data-section="pdf">
          <span class="settingsNavIcon"></span>
          <span>PDF Viewer</span>
        </div>
      </div>
      
      <div id="settingsMain">
        <div id="settingsHeader">
          <h1 id="settingsTitle">General</h1>
        </div>
        
        <div id="settingsBody">
          <!-- General Section -->
          <div class="settingsPage" id="pageGeneral">
            <div class="settingsGroup">
              <div class="settingsRow">
                <div class="settingsLabel">
                  <label>Sidebar Toggle Shortcut</label>
                  <div class="settingsDescription">Press keys to set a custom keyboard shortcut</div>
                </div>
                <div class="settingsControl">
                  <div id="hotkeyPicker" class="hotkeyPicker">
                    <span class="hotkeyPlaceholder">Click to set shortcut</span>
                  </div>
                </div>
              </div>
            </div>
            
            <div class="settingsGroup">
              <div class="settingsRow">
                <div class="settingsLabel">
                  <label>Animation Speed</label>
                </div>
                <div class="settingsControl">
                  <select id="animationSpeedInput" class="macSelect">
                    <option value="fast">Fast</option>
                    <option value="normal" selected>Normal</option>
                    <option value="slow">Slow</option>
                  </select>
                </div>
              </div>
              
              <div class="settingsRow">
                <div class="settingsLabel">
                  <label>Default Sidebar State</label>
                </div>
                <div class="settingsControl">
                  <select id="defaultSidebarStateInput" class="macSelect">
                    <option value="visible" selected>Visible</option>
                    <option value="hidden">Hidden</option>
                  </select>
                </div>
              </div>
            </div>
            
            <div class="settingsGroup">
              <div class="settingsRow">
                <div class="settingsLabel">
                  <label>Automatically Save Library Data</label>
                  <div class="settingsDescription">Save summaries and flashcards automatically when changed</div>
                </div>
                <div class="settingsControl">
                  <label class="macToggle">
                    <input type="checkbox" id="autoSaveEnabledInput" checked />
                    <span class="toggleSlider"></span>
                  </label>
                </div>
              </div>
              
              <div class="settingsRow">
                <div class="settingsLabel">
                  <label>Duplicate Lecture Behavior</label>
                  <div class="settingsDescription">Choose what happens when opening a lecture that already has saved data</div>
                </div>
                <div class="settingsControl">
                  <select id="duplicatePromptBehaviorInput" class="macSelect">
                    <option value="ask">Always Ask</option>
                    <option value="load">Auto-Load Existing</option>
                    <option value="create">Auto-Create New</option>
                  </select>
                </div>
              </div>
            </div>
          </div>
          
          <!-- Appearance Section -->
          <div class="settingsPage" id="pageAppearance" style="display: none;">
            <!-- Preset Combinations - At the very top -->
            <div class="settingsGroup">
              <div class="settingsRow">
                <div class="settingsLabel">
                  <label>Preset Themes</label>
                  <div class="settingsDescription">Quickly apply beautiful combinations of theme and accent colors</div>
                </div>
                <div class="settingsControl">
                  <div id="themePresets" class="themePresets">
                    <div class="themePresetCard" data-preset="default">
                      <div class="presetPreview">
                        <div class="presetLight" style="background: #ffffff;"></div>
                        <div class="presetDark" style="background: #1a1a1c;"></div>
                        <div class="presetAccent" style="background: #8B5CF6;"></div>
                      </div>
                      <div class="presetLabel">Default</div>
                    </div>
                    <div class="themePresetCard" data-preset="ocean">
                      <div class="presetPreview">
                        <div class="presetLight" style="background: #e3f2fd;"></div>
                        <div class="presetDark" style="background: #1e1e2e;"></div>
                        <div class="presetAccent" style="background: #5a9fd4;"></div>
                      </div>
                      <div class="presetLabel">Ocean</div>
                    </div>
                    <div class="themePresetCard" data-preset="forest">
                      <div class="presetPreview">
                        <div class="presetLight" style="background: #f1f8f4;"></div>
                        <div class="presetDark" style="background: #1e2a1e;"></div>
                        <div class="presetAccent" style="background: #10b981;"></div>
                      </div>
                      <div class="presetLabel">Forest</div>
                    </div>
                    <div class="themePresetCard" data-preset="sunset">
                      <div class="presetPreview">
                        <div class="presetLight" style="background: #fff8e1;"></div>
                        <div class="presetDark" style="background: #2a1e2e;"></div>
                        <div class="presetAccent" style="background: #f59e0b;"></div>
                      </div>
                      <div class="presetLabel">Sunset</div>
                    </div>
                    <div class="themePresetCard" data-preset="lavender">
                      <div class="presetPreview">
                        <div class="presetLight" style="background: #f3e5f5;"></div>
                        <div class="presetDark" style="background: #2a1e2e;"></div>
                        <div class="presetAccent" style="background: #8B5CF6;"></div>
                      </div>
                      <div class="presetLabel">Lavender</div>
                    </div>
                    <div class="themePresetCard" data-preset="minimal">
                      <div class="presetPreview">
                        <div class="presetLight" style="background: #fafafa;"></div>
                        <div class="presetDark" style="background: #000000;"></div>
                        <div class="presetAccent" style="background: #5a9fd4;"></div>
                      </div>
                      <div class="presetLabel">Minimal</div>
                    </div>
                  </div>
                  <!-- Advanced Custom Colors Button -->
                  <div style="position: relative; margin-top: 16px; max-width: 400px; width: auto; margin-left: auto;">
                    <button id="advancedColorsToggle" class="macBtn" style="width: 100%; justify-content: space-between; display: flex; align-items: center;">
                      <span>Advanced Custom Colors</span>
                      <span id="advancedColorsToggleIcon" style="font-size: 12px; transition: transform 0.3s ease;">▼</span>
                    </button>
                    <!-- Advanced Custom Colors Panel - Inline Expandable (Compact) -->
                    <div id="advancedColorsPanel" style="display: none; max-height: 0; overflow: hidden; padding: 0; margin-top: 8px; opacity: 0; border: 1px solid var(--border-default); border-radius: 6px; background: var(--bg-tertiary); width: auto; max-width: 400px; min-width: 180px; align-self: flex-end; margin-left: auto;">
                      <div style="padding: 12px; max-width: 100%; box-sizing: border-box;">
                        <div style="padding: 8px 10px; background: rgba(255, 193, 7, 0.1); border-left: 3px solid rgba(255, 193, 7, 0.5); border-radius: 4px; margin-bottom: 12px;">
                          <div style="font-weight: 600; color: var(--text-primary); margin-bottom: 2px; font-size: 12px;">⚠️ Experimental Feature</div>
                          <div style="font-size: 11px; color: var(--text-secondary); line-height: 1.3;">These options are VERY experimental and may cause UI inconsistencies or visual issues. Use at your own risk.</div>
                        </div>
                        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px; max-width: 360px;">
                          <div>
                            <label style="display: block; font-weight: 600; color: var(--text-primary); margin-bottom: 6px; font-size: 12px;">Theme Color</label>
                            <input type="color" id="customThemeColorInput" value="#1a1a1c" style="width: 100%; height: 45px; border-radius: 6px; border: 1px solid var(--border-default); cursor: pointer;" />
                            <div style="font-size: 11px; color: var(--text-secondary); margin-top: 4px;">Base background</div>
                          </div>
                          <div>
                            <label style="display: block; font-weight: 600; color: var(--text-primary); margin-bottom: 6px; font-size: 12px;">Accent Color</label>
                            <input type="color" id="customAccentColorInput" value="#8B5CF6" style="width: 100%; height: 45px; border-radius: 6px; border: 1px solid var(--border-default); cursor: pointer;" />
                            <div style="font-size: 11px; color: var(--text-secondary); margin-top: 4px;">Interactive elements</div>
                          </div>
                        </div>
                        <div style="margin-bottom: 12px; max-width: 360px;">
                          <label style="display: block; font-weight: 600; color: var(--text-primary); margin-bottom: 8px; font-size: 12px;">Text Color Style</label>
                          <select id="textColorStyleSelect" class="macSelect" style="width: 100%; font-size: 12px; padding: 8px 12px;">
                            <option value="light">Light Mode / Black Text</option>
                            <option value="dark">Dark Mode / White Text</option>
                          </select>
                        </div>
                        <div style="display: flex; gap: 6px; flex-direction: row-reverse; max-width: 360px;">
                          <button id="saveCustomPresetBtn" class="macBtn primary" style="flex: 1; padding: 8px 12px; font-size: 12px; cursor: pointer;">Save as Preset</button>
                          <button id="applyCustomColorsBtn" class="macBtn" style="flex: 1; padding: 8px 12px; font-size: 12px; cursor: pointer;">Apply Now</button>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
            
            <div class="settingsGroup">
              <div class="settingsRow">
                <div class="settingsLabel">
                  <label>Font Size</label>
                </div>
                <div class="settingsControl">
                  <select id="fontSizeInput" class="macSelect">
                    <option value="small">Small</option>
                    <option value="medium" selected>Medium</option>
                    <option value="large">Large</option>
                  </select>
                </div>
              </div>
            </div>
            
            <div class="settingsGroup">
              <div class="settingsRow">
                <div class="settingsLabel">
                  <label>Theme Mode</label>
                  <div class="settingsDescription">Choose whether theme follows system settings or use custom theme</div>
                </div>
                <div class="settingsControl">
                  <select id="themeModeInput" class="macSelect">
                    <option value="system" selected>System</option>
                    <option value="custom">Custom</option>
                  </select>
                </div>
              </div>
            </div>
            
            <div class="settingsGroup">
              <div class="settingsRow">
                <div class="settingsLabel">
                  <label>Invert PDF Page Colors</label>
                  <div class="settingsDescription">Apply dark/light theme to PDF pages themselves (not just the viewer UI)</div>
                </div>
                <div class="settingsControl">
                  <label class="macToggle">
                    <input type="checkbox" id="pdfPageThemeInput" />
                    <span class="toggleSlider"></span>
                  </label>
                </div>
              </div>
            </div>
          </div>
          
          <!-- PDF Section -->
          <div class="settingsPage" id="pagePDF" style="display: none;">
            <div class="settingsGroup">
              <div class="settingsRow">
                <div class="settingsLabel">
                  <label>Default Zoom Level</label>
                </div>
                <div class="settingsControl">
                  <select id="pdfZoomInput" class="macSelect">
                    <option value="auto" selected>Auto-fit</option>
                    <option value="75">75%</option>
                    <option value="100">100%</option>
                    <option value="125">125%</option>
                    <option value="150">150%</option>
                    <option value="200">200%</option>
                  </select>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
      
      <button id="settingsClose" class="settingsCloseBtn">✕</button>
    </div>
  </div>

  <!-- Library Modal -->
  <!-- Duplicate Detection Modal -->
  <div id="duplicateModal">
    <div id="duplicateModalContent">
      <div id="duplicateModalHeader">
        <h2>📚 Existing Lecture Found</h2>
      </div>
      <div id="duplicateModalBody">
        <p style="color: var(--text-primary);">You already have a lecture with this name stored with summaries and flashcards.</p>
        <div id="duplicateStats" style="margin-top: 16px; padding: 12px 16px; background: var(--bg-secondary); border-radius: 8px; border: 1px solid var(--border-subtle); font-size: 13px; color: var(--text-secondary); font-weight: 500; display: inline-block; font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', sans-serif;"></div>
        <label style="display: flex; align-items: center; gap: 8px; margin-top: 16px; cursor: pointer; user-select: none;">
          <input type="checkbox" id="duplicateRememberChoice" style="width: 16px; height: 16px; cursor: pointer; accent-color: var(--accent-purple);" />
          <span style="font-size: 13px; color: var(--text-secondary);">Remember my choice</span>
        </label>
      </div>
      <div id="duplicateModalActions">
        <button class="duplicateModalBtn primary" id="duplicateLoadExisting">Load Existing</button>
        <button class="duplicateModalBtn secondary" id="duplicateCreateNew">Create New</button>
      </div>
    </div>
  </div>

  <div id="libraryModal">
    <div id="libraryModalContent">
    <div id="libraryHeader">
        <div id="libraryHeaderTop">
        <h1>Your Library</h1>
        <div id="libraryHeaderActions">
          <button id="openDocsFolderHeader" style="padding:8px 16px; border:1px solid rgba(90,159,212,0.5); background:rgba(90,159,212,0.12); color:#dcefff; border-radius:8px; cursor:pointer; font-size:12px; font-weight:600; transition:all 0.2s ease; font-family:var(--font-family); margin-right:8px;">Open PDFs Folder</button>
          <button id="openQuizzer" style="padding:8px 16px; border:1px solid rgba(255,255,255,0.15); background:rgba(255,255,255,0.12); color:rgba(255,255,255,0.92); border-radius:8px; cursor:pointer; font-size:12px; font-weight:600; transition:all 0.2s ease; font-family:var(--font-family);">Quiz</button>
        <button id="libraryClose">×</button>
        </div>
      </div>
      <div id="libraryQuizzerSearch" style="display: none;">
        <input type="text" id="libraryQuizzerSearchInput" placeholder="Search lectures to select...">
        <div id="libraryQuizzerSearchResults"></div>
      </div>
    </div>
    <div id="libraryContent"></div>
    <div id="libraryQuizzerSettings" style="display: none;"></div>
    </div>
  </div>

  <!-- Flashcards Center Modal -->
  <div id="fcModal">
    <div id="fcHeader">
      <h1>Flashcards Center</h1>
      <div>
        <button id="fcImportBtn" style="margin-right:8px; padding:8px 12px; border:1px solid #404040; background:#2a2a2c; color:#e8e8ea; border-radius:6px; cursor:pointer;">Import</button>
        <button id="fcClose">×</button>
      </div>
    </div>
    <div id="fcContent">
      <div id="deckList" class="deckList"></div>
      <div id="fcEmpty" class="libraryEmpty" style="display:none;">
        <h2>No decks yet</h2>
        <p>Import cards or create decks from your library flashcards.</p>
      </div>
    </div>
  </div>

  <!-- Mindmap Modal -->
  <div id="mindmapModal">
    <div id="mindmapModalContent">
      <div id="mindmapHeader">
        <h1>Study Mindmap</h1>
        <div id="mindmapHeaderActions">
          <button id="mindmapGenerateBtn" class="mindmapActionBtn" style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border: none; color: white;">Generate</button>
          <button id="mindmapExportBtn" class="mindmapActionBtn" style="display: none;">Export</button>
          <button id="mindmapClose">×</button>
        </div>
      </div>
      <div id="mindmapToolbar">
        <div id="mindmapStatus">
          <span id="mindmapStatusText">Ready to generate mindmap</span>
        </div>
        <div id="mindmapTypeSelector" style="margin-right: 16px;">
          <label>Layout:</label>
          <select id="mindmapTypeSelect" class="macSelect" style="font-size: 12px;">
            <option value="radial">Radial</option>
            <option value="brace">Brace</option>
            <option value="flow">Flow</option>
            <option value="tree">Tree</option>
          </select>
        </div>
        <div id="mindmapControls">
          <button id="mindmapFullscreen" class="mindmapControlBtn" title="Fullscreen">⛶</button>
          <div id="mindmapZoomSliderContainer">
            <span class="mindmapZoomLabel">−</span>
            <input type="range" id="mindmapZoomSlider" class="mindmapZoomSlider" min="0.5" max="3" step="0.05" value="1" title="Zoom">
            <span class="mindmapZoomLabel">+</span>
          </div>
          <button id="mindmapReset" class="mindmapControlBtn" title="Reset View & Zoom">↺</button>
        </div>
      </div>
      <div id="mindmapContainer">
        <div id="mindmapCanvasWrapper" style="position: relative; width: 100%; height: 100%;">
          <svg id="mindmapCanvas" width="100%" height="100%"></svg>
          <div id="mindmapLoading" class="mindmapLoading">
            <div class="mindmapSpinner"></div>
            <p>Generating your study mindmap...</p>
          </div>
          <div id="mindmapEmpty" class="mindmapEmpty">
            <div class="mindmapEmptyIcon"></div>
            <h2>No mindmap generated yet</h2>
            <p>Select a layout type above and click "Generate" to create an AI-powered study mindmap.</p>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- Drag and Drop Zone Overlay -->
  <div id="dropZoneOverlay">
    <div id="dropZoneContent">
      <span id="dropZoneIcon">📄</span>
      <div id="dropZoneText">Drop PDF here</div>
      <div id="dropZoneSubtext">Release to open the PDF file</div>
    </div>
  </div>

  <!-- Work area: PDF left + right sidebar -->
  <div id="root">
    <div id="work">
      <div id="pdfWrap">
        <iframe id="pdfFrame" src="" allow="clipboard-read; clipboard-write"></iframe>
        <button id="folderTabsToggle" class="folderTabsToggle"></button>
        <div id="folderTabs" class="folderTabs">
          <div class="folderTabsHeader">
            <span id="folderTabsTitle"></span>
            <button id="folderTabsClose" title="Hide folder tabs">×</button>
          </div>
          <div class="folderTabsList" id="folderTabsList"></div>
        </div>
        <div class="emptyState" id="emptyState">
          <div class="emptyCard">
            <div class="emptyIcon">📂</div>
            <h2>Open a folder to get started</h2>
            <p>Choose a folder with PDFs to explore your library and begin studying.</p>
            <div class="emptyActions">
              <button id="emptyOpenFolder" class="emptyPrimaryBtn">Open folder</button>
              <button id="emptyOpenFile" class="emptyGhostBtn">Open a single PDF</button>
            </div>
          </div>
        </div>
        <div id="folderSwitchOverlay">
          <div class="fsHeader">
            <div>
              <div class="fsLabel">Imported folder</div>
              <div class="fsTitle" id="fsFolderName">Folder</div>
              <div class="fsMeta" id="fsMeta"></div>
            </div>
            <button id="fsClose" class="fsClose" title="Close">×</button>
          </div>
          <div class="fsList" id="fsList"></div>
          <div class="fsFooter">
            <button id="fsViewLibrary" class="fsLibraryBtn">View in Library</button>
          </div>
        </div>
      </div>
      <button id="sidebarToggleBtn" title="Toggle Sidebar"></button>
      <aside id="rightSidebar">
        <div id="sidebarResizeHandle"></div>
        <div id="rsScroll">
          <!-- Fixed sidebar buttons section at top -->
          <div id="sidebarButtonsContainer">
          <div id="sidebarButtons">
            <div style="display: flex; gap: 12px; flex-wrap: wrap; align-items: center; flex: 1;">
              <button class="bigBtn" id="btnAkson"><span class="btn-icon"></span><span class="btn-label">Akson</span></button>
              <button class="bigBtn" id="btnSlides" style="display: none;"><span class="btn-icon">📊</span><span class="btn-label">Slides</span></button>
              <button class="bigBtn" id="btnLibrary"><span class="btn-icon"></span><span class="btn-label">Library</span></button>
            </div>
            <div style="display: flex; gap: 8px; align-items: center;">
              <div class="openDropdown" id="openDropdown">
                <button id="btnOpenFile" title="Open PDF or Folder"></button>
                <div class="openDropdownMenu" id="openDropdownMenu">
                  <button id="dropdownOpenPdf">Open PDF</button>
                  <button id="dropdownOpenFolder">Open Folder</button>
                </div>
              </div>
              <button id="btnSettings" title="Settings"></button>
            </div>
          </div>
          </div>
          <button id="sidebarButtonsToggle" title="Hide buttons"></button>
          
          <!-- Scrollable content section -->
          <div id="rsScrollContent">
          <section class="rsSection">
            <div class="sectionHeaderCompact">
              <div class="summaryDropdown" id="summaryDropdown">
                <h3 class="summaryTitle" id="summaryHeader">Summary</h3>
                <span class="dropdownArrow">▼</span>
                <div class="summaryDropdownMenu">
                  <div class="summaryDropdownOption" data-mode="summarize">
                    <span class="checkIcon">✓</span>
                    <span>Summary</span>
                </div>
                  <div class="summaryDropdownOption" data-mode="explain">
                    <span class="checkIcon">✓</span>
                    <span>Explanation</span>
              </div>
            </div>
            </div>
              <div class="sectionHeaderControls">
                <span id="summaryAIBadge" class="autoGeneratedBadge interactiveBadge" title="Click to toggle AI generation">
                  <span class="badgeIcon">✨</span>
                  <span class="badgeText">AI</span>
                </span>
            </div>
            </div>
            <input type="checkbox" id="disableSummaryAI" style="display: none;">
            <div id="rsSummary" class="rsBox rsEmpty" contenteditable="true" data-placeholder="Click Summary dropdown to generate content."></div>
            <div class="sectionActionsCompact">
              <button id="clearSummary" class="sectionActionBtn" title="Clear Summary">
                <span>×</span>
                  </button>
              <div class="sectionActionsSpacer"></div>
              <input type="text" id="summaryExtraInstruction" class="sectionExtraInput" placeholder="Extra instruction..." title="Add extra instructions for the AI">
              <button id="btnRegenerateSummary" class="sectionRegenerateBtn" title="Regenerate">
                <span>↻</span>
                  </button>
            </div>
          </section>

          <section class="rsSection">
            <h3>Term Explainer</h3>
            <div id="rsExplain" class="rsBox rsEmpty" contenteditable="true" data-placeholder="Select a word or term in the PDF"></div>
            <div style="margin-top: 8px; display: flex; gap: 6px;">
              <button id="saveExplain" class="bigBtn iconBtn" style="padding: 6px 8px; font-size: 18px; width: 28px; height: 28px; display: flex; align-items: center; justify-content: center; line-height: 1;" title="Add to Page Summary">+</button>
              <button id="clearExplain" class="bigBtn iconBtn" style="padding: 6px 8px; font-size: 16px; width: 28px; height: 28px; display: flex; align-items: center; justify-content: center; line-height: 1;" title="Clear">×</button>
            </div>
          </section>

          <section class="rsSection">
            <div class="sectionHeaderCompact">
              <div style="display: flex; align-items: center; gap: 8px;">
                <h3>Flashcards</h3>
                <button id="btnViewCards" class="sectionQuizBtnCompact" title="Start Quiz">
                  <img src="icons/flashcard.png" alt="Quiz" style="width: 16px; height: 16px;">
                </button>
            </div>
              <div class="sectionHeaderControls">
                <span id="flashcardsAIBadge" class="autoGeneratedBadge interactiveBadge" title="Click to toggle AI generation">
                  <span class="badgeIcon">✨</span>
                  <span class="badgeText">AI</span>
                </span>
            </div>
            </div>
            <input type="checkbox" id="disableFlashcardsAI" style="display: none;">
            <div id="fcScrollContainer" class="flashcardsContainer">
              <div id="fcList"></div>
            </div>
            <div class="sectionActionsCompact">
              <button id="btnAddCard" class="sectionActionBtn" title="Add Card">
                <span>+</span>
              </button>
              <button id="btnExportCards" class="sectionActionBtn" title="Export All Cards">
                <span>↓</span>
              </button>
              <button id="btnClearCards" class="sectionActionBtn" title="Clear Cards">
                <span>×</span>
              </button>
              <div class="sectionActionsSpacer"></div>
              <input type="text" id="flashcardsExtraInstruction" class="sectionExtraInput" placeholder="Extra instruction..." title="Add extra instructions for the AI">
              <button id="btnRegenerateFlashcards" class="sectionRegenerateBtn" title="Regenerate">
                <span>↻</span>
              </button>
            </div>
          </section>
          </div>

          <!-- Fixed Ask AI section at bottom -->
          <div id="aiSectionFixed">
            <div id="aiSectionContent">
              <div id="aiHeader">
            <h3>Ask AI</h3>
            </div>
              <div id="aiInputWrapper">
                <input type="text" id="aiQuestion" placeholder="Ask anything..." autocomplete="off">
                <button id="btnAskAI" class="aiSendBtn" title="Send question">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <line x1="22" y1="2" x2="11" y2="13"></line>
                    <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
                  </svg>
                </button>
            </div>
              <div id="aiAnswer" class="aiAnswerBox rsEmpty" data-placeholder="Ask a question to get started."></div>
              <div id="aiActions" style="display: none;">
                <button id="clearAiAnswer" class="aiActionBtn" title="Clear">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <line x1="18" y1="6" x2="6" y2="18"></line>
                    <line x1="6" y1="6" x2="18" y2="18"></line>
                  </svg>
                </button>
              </div>
            </div>
          </div>
        </div>

        
      </aside>
    </div>

  </div>

<script>
(function(){
  // ---- File param & iframe setup ----
  const params = new URLSearchParams(location.search);
  const fileParam = params.get('file') || '';
  const folderImportParam = params.get('folderImport') || '';
  const initial = '/web/viewer.html' + (fileParam ? ('?file=' + encodeURIComponent(fileParam)) : '');
  const frame = document.getElementById('pdfFrame');
  const pdfWrap = document.getElementById('pdfWrap');
  const emptyState = document.getElementById('emptyState');
  const folderTabs = document.getElementById('folderTabs');
  const folderTabsList = document.getElementById('folderTabsList');
  const folderTabsTitle = document.getElementById('folderTabsTitle');
  const folderTabsClose = document.getElementById('folderTabsClose');
  const folderTabsToggle = document.getElementById('folderTabsToggle');
  const hasFile = !!fileParam;
  let folderContext = null;

  if (folderImportParam) {
    try {
      const decoded = atob(folderImportParam.replace(/-/g, '+').replace(/_/g, '/'));
      folderContext = JSON.parse(decoded);
    } catch(e) {
      console.warn('Could not parse folder import payload', e);
    }
  }

  function renderFolderTabs(activeFileBase) {
    if (!folderContext || !Array.isArray(folderContext.files) || folderContext.files.length === 0) {
      folderTabs?.classList.remove('show');
      folderTabsToggle?.classList.remove('show');
      return;
    }
    folderTabs?.classList.add('show');
    folderTabsToggle?.classList.remove('show');
    folderTabsTitle.textContent = folderContext.folder || 'Folder';
    folderTabsList.innerHTML = '';
    folderContext.files.forEach((name, idx) => {
      const tab = document.createElement('button');
      tab.className = 'folderTab' + (name === activeFileBase ? ' active' : '');
      const displayName = name.replace(/\.pdf$/i, '');
      const titleSpan = document.createElement('span');
      titleSpan.textContent = `${idx + 1}. ${displayName}`;
      tab.appendChild(titleSpan);
      tab.onclick = () => switchFolderFile(name);
      folderTabsList.appendChild(tab);
    });
  }

  function switchFolderFile(fileName) {
    if (!fileName) return;
    const search = [`file=${encodeURIComponent('/docs/' + fileName)}`];
    if (folderImportParam) {
      search.push(`folderImport=${encodeURIComponent(folderImportParam)}`);
    }
    const nextSrc = `/web/viewer.html?${search.join('&')}`;
    if (frame) {
      frame.src = nextSrc;
      pdfWrap?.classList.remove('empty');
      renderFolderTabs(fileName);
    }
  }

  if (folderTabsClose) {
    folderTabsClose.onclick = () => {
      folderTabs?.classList.remove('show');
      if (folderContext) {
        folderTabsToggle?.classList.add('show');
        folderTabsToggle.textContent = folderContext.folder || 'Folder';
      }
    };
  }

  if (folderTabsToggle) {
    folderTabsToggle.onclick = () => {
      folderTabs?.classList.add('show');
      folderTabsToggle?.classList.remove('show');
    };
  }
  const folderSwitch = document.getElementById('folderSwitchOverlay');
  const fsFolderName = document.getElementById('fsFolderName');
  const fsMeta = document.getElementById('fsMeta');
  const fsList = document.getElementById('fsList');
  const fsClose = document.getElementById('fsClose');
  const fsViewLibrary = document.getElementById('fsViewLibrary');
  
  // Send theme to PDF viewer when frame loads
  frame.addEventListener('load', () => {
    setTimeout(() => {
      const pdfFrame = document.getElementById('pdfFrame');
      if (pdfFrame && pdfFrame.contentWindow) {
        pdfFrame.contentWindow.postMessage({ 
          type: 'theme-changed', 
          theme: theme,
          pdfPageTheme: pdfPageTheme
        }, '*');
      }
    }, 100);
  });
  
  if (hasFile) {
    pdfWrap?.classList.remove('empty');
    if (frame) frame.src = initial;
    const activeBase = fileParam ? decodeURIComponent(fileParam).split('/').pop() : '';
    renderFolderTabs(activeBase);
  } else {
    pdfWrap?.classList.add('empty');
    folderTabs?.classList.remove('show');
    folderTabsToggle?.classList.remove('show');
  }

  // Restore pending folder import overlay after reload
  try {
    const stored = sessionStorage.getItem('aksonLastFolderImport');
    if (stored) {
      sessionStorage.removeItem('aksonLastFolderImport');
      const payload = JSON.parse(stored);
      if (payload && payload.ok) {
        showFolderSwitchOverlay(payload, { skipStore: true });
      }
    }
  } catch(e) {
    console.warn('Could not restore folder overlay', e);
  }

  // Folder quick-switch overlay helpers
  function hideFolderSwitch() {
    if (folderSwitch) folderSwitch.classList.remove('show');
  }

  async function handleSwitchPath(path, safeName) {
    try {
      hideFolderSwitch();
      if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.load_pdf_from_path === 'function') {
        await window.pywebview.api.load_pdf_from_path(path);
      } else if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.open_library_file === 'function' && safeName) {
        await window.pywebview.api.open_library_file(safeName);
      } else {
        alert('Cannot switch PDF: bridge not available.');
      }
    } catch(e) {
      console.error('Error loading PDF from folder overlay:', e);
      alert('Error opening PDF: ' + (e.message || e));
    }
  }

  function showFolderSwitchOverlay(result, options = {}) {
    const { skipStore = false } = options;
    if (!result || !result.ok) return;
    if (!folderSwitch || !fsFolderName || !fsMeta || !fsList) return;

    // Persist so it can be restored after the viewer reloads
    if (!skipStore) {
      try {
        sessionStorage.setItem('aksonLastFolderImport', JSON.stringify(result));
      } catch(e) {
        console.warn('Could not persist folder import payload', e);
      }
    }

    const paths = Array.isArray(result.files) ? result.files : [];
    const safeNames = Array.isArray(result.names) ? result.names : [];
    const folderName = result.folder || 'Imported Folder';

    if (!paths.length) return;

    fsFolderName.textContent = folderName;
    fsMeta.textContent = `${paths.length} PDF${paths.length > 1 ? 's' : ''} imported`;

    fsList.innerHTML = '';
    paths.forEach((path, idx) => {
      const item = document.createElement('div');
      item.className = 'fsItem';
      const icon = document.createElement('div');
      icon.className = 'fsItemIcon';
      icon.textContent = '📄';
      const text = document.createElement('div');
      text.className = 'fsItemText';
      const nameEl = document.createElement('div');
      nameEl.className = 'fsItemName';
      const subEl = document.createElement('div');
      subEl.className = 'fsItemSub';

      const fileName = path.split('/').pop() || path;
      nameEl.textContent = fileName.replace(/\.pdf$/i, '');
      subEl.textContent = folderName;

      text.appendChild(nameEl);
      text.appendChild(subEl);

      item.appendChild(icon);
      item.appendChild(text);

      item.onclick = () => handleSwitchPath(path, safeNames[idx]);
      fsList.appendChild(item);
    });

    if (fsClose) {
      fsClose.onclick = hideFolderSwitch;
    }
    if (fsViewLibrary) {
      fsViewLibrary.onclick = () => {
        hideFolderSwitch();
        if (typeof openLibrary === 'function') {
          openLibrary();
        }
      };
    }

    folderSwitch.classList.add('show');
  }

  // Extract clean filename for library key - remove numbered suffix (e.g., "filename-8" -> "filename")
  let rawFileName = fileParam ? decodeURIComponent(fileParam).split('/').pop().replace('.pdf', '') : 'untitled';
  // Remove numbered suffix pattern like "-1", "-2", "-8" etc. at the end
  // This handles cases like "filename-8" -> "filename" or "filename-8-1" -> "filename"
  // Keep removing trailing "-N" patterns until no more match
  let currentFileName = rawFileName;
  while (currentFileName.match(/-\d+$/)) {
    currentFileName = currentFileName.replace(/-\d+$/, '');
  }
  console.log('Current file (original, for library):', currentFileName);
  console.log('Current file (raw, with number):', rawFileName);
  
  // Check if opening from library (via URL parameter)
  const urlParams = new URLSearchParams(window.location.search);
  const fromLibrary = urlParams.get('fromLibrary') === 'true';

  // ---- Library System: Persistent storage per PDF file ----
  const pageSummaryCache = new Map(); // pageNo -> {summary, timestamp}
  let currentPage = null;
  let documentPages = {}; // Store all page text for Ask AI context
  
  // Track which pages have library data (summaries/flashcards) for current filename
  const pagesWithLibraryData = new Set(); // Set of page numbers that have cached data
  
  // Track if we've shown duplicate modal for this session
  let duplicateModalShown = false;
  
  // Setting for duplicate prompt behavior
  // 'ask' = always show prompt, 'load' = auto-load existing, 'create' = auto-create new
  let duplicatePromptBehavior = 'ask';
  
  // Track AI generation disable flags
  let disableSummaryAI = false;
  let disableFlashcardsAI = false;
  
  // Load library data for current filename when PDF opens
  async function loadLibraryDataForFilename(forceSkipPrompt = false) {
    try {
      if (!currentFileName || !window.pywebview || !window.pywebview.api) {
        return;
      }
      
      console.log(`📚 Loading library data for filename: ${currentFileName}`);
        const result = await window.pywebview.api.load_library_data(currentFileName);
      
      if (result && result.ok && result.data) {
        const hasExistingData = (result.data.summaries && Object.keys(result.data.summaries).length > 0) ||
                               (result.data.flashcards && Object.keys(result.data.flashcards).length > 0);
        
        // Handle duplicate detection based on user preference
        // Skip if opening from library (user already knows it exists) - check URL parameter
        if (hasExistingData && !forceSkipPrompt && !fromLibrary && !duplicateModalShown) {
          if (duplicatePromptBehavior === 'load') {
            // Auto-load existing - no prompt
            duplicateModalShown = true;
            // Continue to load data below
          } else if (duplicatePromptBehavior === 'create') {
            // Auto-create new - rename file
            const originalFileName = currentFileName;
            let suffix = 1;
            let newFileName = `${originalFileName}-${suffix}`;
            
            // Check if library file with this name already exists, increment suffix if needed
            while (true) {
              const checkResult = await window.pywebview.api.load_library_data(newFileName);
              if (!checkResult || !checkResult.ok || !checkResult.data || 
                  (Object.keys(checkResult.data.summaries || {}).length === 0 && 
                   Object.keys(checkResult.data.flashcards || {}).length === 0)) {
                break; // Found a free name
              }
              suffix++;
              newFileName = `${originalFileName}-${suffix}`;
            }
            
            currentFileName = newFileName;
            console.log(`📝 Auto-created new lecture: ${currentFileName} (original: ${originalFileName})`);
            pagesWithLibraryData.clear();
            pageSummaryCache.clear();
            cardsByPage = {};
            duplicateModalShown = true;
            // Continue without loading existing data
            return;
          } else {
            // Show prompt (duplicatePromptBehavior === 'ask')
            showDuplicateModal(result.data);
            duplicateModalShown = true;
            return; // Don't load data yet, wait for user choice
          }
        }
        
        // Load the library data
        pagesWithLibraryData.clear(); // Reset for new file
        
        // Load summaries from library and track which pages have them
          if (result.data.summaries) {
            Object.keys(result.data.summaries).forEach(pageNo => {
            const pageNum = parseInt(pageNo);
            const summaryData = result.data.summaries[pageNo];
            const summaryText = typeof summaryData === 'string' ? summaryData : (summaryData.summary || summaryData.content || summaryData);
            
            if (summaryText) {
              pageSummaryCache.set(pageNum, { summary: summaryText, timestamp: Date.now() });
              pagesWithLibraryData.add(pageNum);
              console.log(`✓ Loaded summary for page ${pageNum} from library`);
            }
          });
        }
        
        // Load flashcards from library and track which pages have them
          if (result.data.flashcards) {
          Object.keys(result.data.flashcards).forEach(pageNo => {
            const pageNum = parseInt(pageNo);
            const cards = result.data.flashcards[pageNo];
            if (cards && Array.isArray(cards) && cards.length > 0) {
              cardsByPage[pageNum] = cards;
              pagesWithLibraryData.add(pageNum);
              console.log(`✓ Loaded ${cards.length} flashcards for page ${pageNum} from library`);
            }
          });
        }
        
        console.log(`📚 Loaded library data for ${currentFileName}: ${pagesWithLibraryData.size} pages with cached data`);
        
        // If we're on a page that has library data, show it
        if (currentPage && pagesWithLibraryData.has(currentPage)) {
            loadPageSummary(currentPage);
            refreshCards(currentPage);
          }
      } else {
        console.log(`📚 No library data found for ${currentFileName}`);
        pagesWithLibraryData.clear();
      }
    } catch(e) {
      console.error('Error loading library data:', e);
      pagesWithLibraryData.clear();
    }
  }
  
  // Show duplicate detection modal
  function showDuplicateModal(libraryData) {
    const modal = document.getElementById('duplicateModal');
    const stats = document.getElementById('duplicateStats');
    
    const summaryCount = libraryData.summaries ? Object.keys(libraryData.summaries).length : 0;
    const flashcardPages = libraryData.flashcards ? Object.keys(libraryData.flashcards).length : 0;
    const totalFlashcards = libraryData.flashcards ? 
      Object.values(libraryData.flashcards).reduce((sum, cards) => sum + (Array.isArray(cards) ? cards.length : 0), 0) : 0;
    
    stats.textContent = `${summaryCount} page summaries • ${totalFlashcards} flashcards across ${flashcardPages} pages`;
    
    modal.classList.add('show');
  }
  
  // Hide duplicate modal
  function hideDuplicateModal() {
    const modal = document.getElementById('duplicateModal');
    modal.classList.remove('show');
  }
  
  // Save library data for current filename (tracks which pages have summaries/flashcards)
  async function saveLibraryData() {
    try {
      if (!currentFileName || !window.pywebview || !window.pywebview.api) {
        return;
      }
      
      // Convert Map to object - only save pages that have summaries
        const summaries = {};
      pageSummaryCache.forEach((value, pageNum) => {
        summaries[pageNum] = value;
        // Mark this page as having library data
        pagesWithLibraryData.add(pageNum);
      });
      
      // Only save flashcards for pages that have them
      const flashcards = {};
      Object.keys(cardsByPage).forEach(pageNum => {
        const pageNumInt = parseInt(pageNum);
        const cards = cardsByPage[pageNum];
        if (cards && Array.isArray(cards) && cards.length > 0) {
          flashcards[pageNum] = cards;
          // Mark this page as having library data
          pagesWithLibraryData.add(pageNumInt);
        }
        });
        
        const libraryData = {
          summaries: summaries,
        flashcards: flashcards,
          lastModified: new Date().toISOString()
        };
        
        await window.pywebview.api.save_library_data(currentFileName, libraryData);
      console.log(`💾 Saved library data for ${currentFileName} (${Object.keys(summaries).length} summaries, ${Object.keys(flashcards).length} flashcard pages)`);
    } catch(e) {
      console.error('Error saving library data:', e);
    }
  }
  
  // Auto-save library data periodically
  setInterval(() => {
    saveLibraryData();
  }, 30000); // Save every 30 seconds
  
  // Save on page unload
  window.addEventListener('beforeunload', () => {
    saveLibraryData();
  });
  
  // ---- Right Sidebar refs ----
  const rsSummary = document.getElementById('rsSummary');
  const rsExplain = document.getElementById('rsExplain');
  const fcList = document.getElementById('fcList');
  const summaryHeader = document.getElementById('summaryHeader');
  const currentPageNumber = document.getElementById('currentPageNumber');
  const aiQuestion = document.getElementById('aiQuestion');
  const aiAnswer = document.getElementById('aiAnswer');
  
  // Initialize placeholder visibility for elements with data-placeholder
  setTimeout(() => {
    [rsSummary, rsExplain, aiAnswer].forEach(el => {
      if (el && el.hasAttribute('data-placeholder')) {
        updatePlaceholderVisibility(el);
      }
    });
  }, 100);

  // ---- Resize handles for rsBox elements ----
  function setupResizeHandles() {
    // Specifically ensure rsSummary and rsExplain have handles
    const specificBoxes = ['rsSummary', 'rsExplain', 'aiAnswer'];
    specificBoxes.forEach(id => {
      const box = document.getElementById(id);
      if (box && !box.querySelector('.resizeHandle')) {
        const handle = document.createElement('div');
        handle.className = 'resizeHandle';
        handle.title = 'Drag to resize';
        box.appendChild(handle);
        attachResizeListeners(box, handle);
      }
    });
    
    // Also handle all other rsBox elements
    const rsBoxes = document.querySelectorAll('.rsBox');
    rsBoxes.forEach(box => {
      // Skip if resize handle already exists
      if (box.querySelector('.resizeHandle')) return;
      // Skip if we already handled it above
      if (specificBoxes.includes(box.id)) return;
      
      // Create resize handle
      const handle = document.createElement('div');
      handle.className = 'resizeHandle';
      handle.title = 'Drag to resize';
      box.appendChild(handle);
      attachResizeListeners(box, handle);
    });
  }
  
  function attachResizeListeners(box, handle) {
      // Store initial height and get actual min-height from CSS
      let startY = 0;
      let startHeight = 0;
      let isResizing = false;
      
      // Function to keep handle fixed at bottom-right of visible area
      function updateHandlePosition() {
        // Always position relative to box's visible viewport
        const rect = box.getBoundingClientRect();
        const scrollTop = box.scrollTop;
        const scrollHeight = box.scrollHeight;
        const clientHeight = box.clientHeight;
        
        // Check if content is scrollable
        if (scrollHeight > clientHeight) {
          // Position handle at bottom of visible area (not scrollable content)
          handle.style.position = 'absolute';
          handle.style.bottom = '0px';
          handle.style.right = '0px';
          handle.style.top = 'auto';
          handle.style.left = 'auto';
          // Ensure it's above scrollbar
          handle.style.marginRight = '0px';
        } else {
          // Normal positioning when not scrolling
          handle.style.position = 'absolute';
          handle.style.bottom = '0px';
          handle.style.right = '0px';
        }
      }
      
      // Update position on scroll and resize
      box.addEventListener('scroll', updateHandlePosition);
      const resizeObserver = new ResizeObserver(updateHandlePosition);
      resizeObserver.observe(box);
      
      // Get the actual min-height from computed styles or CSS
      function getMinHeight() {
        const computed = window.getComputedStyle(box);
        const minHeight = computed.minHeight;
        if (minHeight && minHeight !== '0px' && minHeight !== 'auto') {
          return parseInt(minHeight);
        }
        // Fallback: check for specific IDs
        if (box.id === 'rsExplain') return 50;
        if (box.id === 'rsSummary') return 120;
        return 120; // Default
      }
      
      handle.addEventListener('mousedown', (e) => {
        e.preventDefault();
        e.stopPropagation();
        isResizing = true;
        startY = e.clientY;
        startHeight = box.offsetHeight;
        handle.classList.add('resizing');
        box.style.userSelect = 'none';
        document.body.style.cursor = 'nwse-resize';
        document.body.style.userSelect = 'none';
      });
      
      document.addEventListener('mousemove', (e) => {
        if (!isResizing) return;
        e.preventDefault();
        const deltaY = e.clientY - startY;
        const minHeight = getMinHeight();
        // Allow resizing smaller than default min-height (minimum 50px for usability)
        const newHeight = Math.max(50, startHeight + deltaY);
        box.style.height = newHeight + 'px';
        // Enable scrolling when content exceeds height
        if (box.scrollHeight > newHeight) {
          box.style.overflowY = 'auto';
        } else {
          box.style.overflowY = 'hidden';
        }
        updateHandlePosition();
      });
      
      document.addEventListener('mouseup', () => {
        if (isResizing) {
          isResizing = false;
          handle.classList.remove('resizing');
          box.style.userSelect = '';
          document.body.style.cursor = '';
          document.body.style.userSelect = '';
          // Ensure overflow is set based on content
          if (box.scrollHeight > box.offsetHeight) {
            box.style.overflowY = 'auto';
          }
          updateHandlePosition();
        }
      });
      
      // Initial position update
      updateHandlePosition();
  }
  
  // Setup resize handles when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupResizeHandles);
  } else {
    setupResizeHandles();
  }
  
  // Also setup resize handles for dynamically created rsBoxes
  const observer = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => {
      mutation.addedNodes.forEach((node) => {
        if (node.nodeType === 1) {
          if (node.classList && node.classList.contains('rsBox')) {
            setTimeout(setupResizeHandles, 0);
          } else if (node.querySelectorAll) {
            const rsBoxes = node.querySelectorAll('.rsBox');
            if (rsBoxes.length > 0) {
              setTimeout(setupResizeHandles, 0);
            }
          }
        }
      });
    });
  });
  
  observer.observe(document.body, { childList: true, subtree: true });

  // ---- Flashcards helpers (per-page cache + CRUD) ----
  let cardsByPage = {}; // Will be loaded from library

  function getPageCards(page){
    const list = cardsByPage[String(page)];
    return Array.isArray(list) ? list.slice() : [];
  }
  function setPageCards(page, list){
    cardsByPage[String(page)] = Array.isArray(list) ? list : [];
    // Mark this page as having library data if it has cards
    if (Array.isArray(list) && list.length > 0) {
      pagesWithLibraryData.add(parseInt(page));
    }
    saveLibraryData(); // Auto-save to library
  }
  function escapeHtml(s){ return (s||'').replace(/</g,'&lt;'); }

  function renderCardItem(idx, c){
    const el = document.createElement('div');
    el.className = 'fcItem';
    el.dataset.index = idx;
    
    // Create question and answer spans with contenteditable
    const qSpan = document.createElement('span');
    qSpan.className = 'fcQuestion';
    qSpan.textContent = c.q || '';
    qSpan.contentEditable = false;
    
    const aSpan = document.createElement('span');
    aSpan.className = 'fcAnswer';
    aSpan.textContent = c.a || '';
    aSpan.contentEditable = false;
    
    el.innerHTML = `
      <button class="copyBtn" title="Copy flashcard"></button>
      <div class="fcActions">
        <button class="deleteBtn" title="Delete flashcard"></button>
      </div>
      <div class="fcQuestionWrapper">
        <div class="fcLabel">Question</div>
      </div>
      <div class="fcAnswerWrapper">
        <div class="fcLabel">Answer</div>
      </div>
    `;
    
    // Insert the spans into their wrappers
    const qWrapper = el.querySelector('.fcQuestionWrapper');
    qWrapper.appendChild(qSpan);
    const aWrapper = el.querySelector('.fcAnswerWrapper');
    aWrapper.appendChild(aSpan);
    
    let originalQ = c.q || '';
    let originalA = c.a || '';
    let isEditing = false;
    let clickOutsideHandler = null;
    
    const startEdit = ()=>{
      if (isEditing) return;
      isEditing = true;
      originalQ = qSpan.textContent;
      originalA = aSpan.textContent;
      
      qSpan.contentEditable = true;
      aSpan.contentEditable = true;
      el.classList.add('editing');
      el.style.cursor = 'default'; // Set cursor to default for the card item
      el.querySelector('.fcActions').style.display = 'none';
      
      // Focus on question
      setTimeout(() => {
        qSpan.focus();
        // Select all text for easy replacement
        const range = document.createRange();
        range.selectNodeContents(qSpan);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
      }, 10);
      
      // Add click-outside listener to auto-save
      clickOutsideHandler = (e) => {
        // Don't auto-save if clicking on the editable fields themselves
        if (e.target === qSpan || e.target === aSpan || qSpan.contains(e.target) || aSpan.contains(e.target)) {
          return;
        }
        // Auto-save if clicking outside the card
        if (!el.contains(e.target) && isEditing) {
          saveEdit();
        }
      };
      // Use setTimeout to avoid immediate trigger from the click that started editing
      setTimeout(() => {
        document.addEventListener('click', clickOutsideHandler, true); // Use capture phase for better event handling
      }, 100);
    };
    
    const saveEdit = ()=>{
      if (!isEditing) return;
      const newQ = qSpan.textContent.trim();
      const newA = aSpan.textContent.trim();
      
      qSpan.contentEditable = false;
      aSpan.contentEditable = false;
      el.classList.remove('editing');
      el.style.cursor = 'pointer'; // Restore cursor to pointer for the card item
      el.querySelector('.fcActions').style.display = 'flex';
      
      const list = getPageCards(currentPage);
      list[idx] = { q: newQ, a: newA };
      setPageCards(currentPage, list);
      isEditing = false;
      
      // Remove click-outside listener
      if (clickOutsideHandler) {
        document.removeEventListener('click', clickOutsideHandler);
        clickOutsideHandler = null;
      }
    };
    
    const cancelEdit = ()=>{
      if (!isEditing) return;
      qSpan.textContent = originalQ;
      aSpan.textContent = originalA;
      qSpan.contentEditable = false;
      aSpan.contentEditable = false;
      el.classList.remove('editing');
      el.style.cursor = 'pointer'; // Restore cursor to pointer for the card item
      el.querySelector('.fcActions').style.display = 'flex';
      isEditing = false;
      
      // Remove click-outside listener
      if (clickOutsideHandler) {
        document.removeEventListener('click', clickOutsideHandler);
        clickOutsideHandler = null;
      }
    };
    
    const onDelete = ()=>{
      if (isEditing) return;
      if (!confirm('Delete this flashcard?')) return;
      const list = getPageCards(currentPage);
      list.splice(idx, 1);
      setPageCards(currentPage, list);
      refreshCards(currentPage);
    };
    
    // Handle Enter key in edit mode (new line) and Escape to cancel
    qSpan.addEventListener('keydown', (e) => {
      if (!isEditing) return;
      if (e.key === 'Escape') {
        e.preventDefault();
        cancelEdit();
      }
    });
    
    aSpan.addEventListener('keydown', (e) => {
      if (!isEditing) return;
      if (e.key === 'Escape') {
        e.preventDefault();
        cancelEdit();
      }
      // Allow Enter for new lines, Ctrl+Enter or Cmd+Enter to save
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault();
        saveEdit();
      }
    });
    
    const onCopy = ()=>{
      if (isEditing) return;
      const cardText = `Q: ${qSpan.textContent}\nA: ${aSpan.textContent}`;
      navigator.clipboard.writeText(cardText).then(() => {
        // Visual feedback - animate to checkmark
        const copyBtn = el.querySelector('.copyBtn');
        copyBtn.classList.add('copied');
        setTimeout(() => {
          copyBtn.classList.remove('copied');
        }, 1500);
      }).catch(err => {
        console.error('Failed to copy:', err);
      });
    };
    
    // Make flashcard clickable to enter edit mode (but not when clicking buttons)
    el.addEventListener('click', (e) => {
      // Don't enter edit mode if clicking on buttons or if already editing
      if (isEditing) return;
      if (e.target.closest('.copyBtn') || e.target.closest('.deleteBtn') || 
          e.target.closest('.fcActions')) {
        return;
      }
      startEdit();
    });
    
    el.querySelector('.deleteBtn').onclick = (e) => {
      e.stopPropagation();
      onDelete();
    };
    el.querySelector('.copyBtn').onclick = (e) => {
      e.stopPropagation();
      onCopy();
    };
    
    return el;
  }

  function refreshCards(page){
    fcList.innerHTML = '';
    const list = getPageCards(page);
    if (!list.length) { 
      // Show empty state INSIDE fcList
      const emptyState = document.createElement('div');
      emptyState.className = 'fcEmptyState';
      emptyState.innerHTML = `
        <div class="fcEmptyIcon">🃏</div>
        <div class="fcEmptyText">No flashcards for this page</div>
      `;
      fcList.appendChild(emptyState);
      return; 
    }
    // Render all cards
    list.forEach((c, i)=> fcList.appendChild(renderCardItem(i, c)));
  }

  function addCard(q, a){
    if (!currentPage) return;
    const list = getPageCards(currentPage);
    list.push({ q: q||'', a: a||'' });
    setPageCards(currentPage, list);
    refreshCards(currentPage);
  }

  // Manual add/export/clear
  // macOS-style tooltips for icon buttons - always on top
  function setupMacTooltip(button) {
    let tooltip = null;
    let timeout = null;
    const title = button.getAttribute('title');
    
    if (!title) return;
    
    // Store title and remove to prevent native tooltip
    button.setAttribute('data-title', title);
    button.removeAttribute('title');
    
    button.addEventListener('mouseenter', (e) => {
      timeout = setTimeout(() => {
        tooltip = document.createElement('div');
        tooltip.className = 'macTooltip';
        tooltip.textContent = title;
        document.body.appendChild(tooltip);
        
        // Get button position
        const rect = button.getBoundingClientRect();
        const tooltipRect = tooltip.getBoundingClientRect();
        
        // Center tooltip above button
        const left = rect.left + (rect.width / 2);
        const top = rect.top - tooltipRect.height - 8;
        
        tooltip.style.left = `${left}px`;
        tooltip.style.top = `${Math.max(8, top)}px`;
        tooltip.style.transform = 'translateX(-50%)';
      }, 400);
    });
    
    button.addEventListener('mouseleave', () => {
      if (timeout) {
        clearTimeout(timeout);
        timeout = null;
      }
      if (tooltip) {
        tooltip.remove();
        tooltip = null;
      }
    });
  }
  
  // SVG Icons
  const iconCards = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M2 7h20M7 3v18M17 3v18"/></svg>';
  const iconList = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>';
  const iconFlip = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>';
  const iconClose = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
  const iconArrowLeft = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>';
  const iconArrowRight = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6"/></svg>';
  const iconEdit = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>';
  const iconCheck = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>';
  const iconSettings = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 1v6m0 6v6M5.64 5.64l4.24 4.24m4.24 4.24l4.24 4.24M1 12h6m6 0h6M5.64 18.36l4.24-4.24m4.24-4.24l4.24-4.24"/></svg>';
  
  // Unified interactive flashcard viewer function
  function openInteractiveFlashcardViewer(cards, title, backCallback, options = {}) {
    // options: { cardOrder: 'random'|'sequential', lectureGroups: [{name, cards, startIdx}], maxCards: number|null }
    const cardOrder = options.cardOrder || 'random';
    const lectureGroups = options.lectureGroups || null;
    const maxCards = options.maxCards || null;
    const isLightMode = document.body.dataset.theme === 'light' || document.body.classList.contains('light-mode');
    const bgColor = isLightMode ? '#ffffff' : '#1a1a1c';
    const borderColor = isLightMode ? '#e0e0e0' : '#404040';
    const textColor = isLightMode ? '#1a1a1c' : '#f0f0f2';
    const textSecondary = isLightMode ? '#666666' : '#a0a0a2';
    const cardBg = isLightMode ? 'rgba(0,0,0,0.02)' : 'rgba(255,255,255,0.05)';
    const cardBorder = isLightMode ? 'rgba(0,0,0,0.1)' : 'rgba(255,255,255,0.1)';
    const overlayBg = isLightMode ? 'rgba(0,0,0,0.6)' : 'rgba(0,0,0,0.85)';
    
    // Create modal overlay with fade-in animation
    const modal = document.createElement('div');
    modal.id = 'flashcardViewerModal';
    modal.style.cssText = `position:fixed; top:0; left:0; right:0; bottom:0; background:${overlayBg}; z-index:100000; display:flex; align-items:center; justify-content:center; padding:20px; opacity:0; transition:opacity 0.2s ease;`;
    
    const content = document.createElement('div');
    content.style.cssText = `background:${bgColor}; border:1px solid ${borderColor}; border-radius:16px; padding:0; max-width:900px; width:100%; max-height:90vh; overflow:hidden; position:relative; box-shadow:0 20px 60px rgba(0,0,0,0.3); transform:scale(0.95); transition:transform 0.2s ease; display:flex; flex-direction:column;`;
    
    // Smooth scrollbar styles
    const scrollbarStyles = document.createElement('style');
    scrollbarStyles.textContent = `
      #flashcardViewerModal .smooth-scroll::-webkit-scrollbar {
        width: 8px;
        height: 8px;
      }
      #flashcardViewerModal .smooth-scroll::-webkit-scrollbar-track {
        background: ${isLightMode ? '#f5f5f5' : '#2a2a2c'};
        border-radius: 4px;
      }
      #flashcardViewerModal .smooth-scroll::-webkit-scrollbar-thumb {
        background: ${isLightMode ? '#b0b0b0' : '#5a5a5c'};
        border-radius: 4px;
        transition: background 0.2s;
      }
      #flashcardViewerModal .smooth-scroll::-webkit-scrollbar-thumb:hover {
        background: ${isLightMode ? '#909090' : '#5a9fd4'};
      }
    `;
    document.head.appendChild(scrollbarStyles);
    
    if (!cards || cards.length === 0) {
      const emptyContainer = document.createElement('div');
      emptyContainer.style.cssText = 'padding:60px 40px; text-align:center;';
      
      const emptyMsg = document.createElement('div');
      emptyMsg.style.cssText = `padding:20px; color:${textSecondary}; font-size:16px;`;
      emptyMsg.textContent = 'No flashcards available.';
      emptyContainer.appendChild(emptyMsg);
      
      const closeBtn = document.createElement('button');
      closeBtn.innerHTML = iconClose;
      closeBtn.style.cssText = `position:absolute; top:20px; right:20px; width:36px; height:36px; background:${isLightMode ? 'rgba(0,0,0,0.05)' : 'rgba(255,255,255,0.1)'}; border:none; border-radius:8px; color:${textColor}; cursor:pointer; display:flex; align-items:center; justify-content:center; transition:all 0.2s;`;
      closeBtn.onmouseover = () => closeBtn.style.background = isLightMode ? 'rgba(0,0,0,0.1)' : 'rgba(255,255,255,0.15)';
      closeBtn.onmouseout = () => closeBtn.style.background = isLightMode ? 'rgba(0,0,0,0.05)' : 'rgba(255,255,255,0.1)';
      closeBtn.onclick = () => modal.remove();
      content.appendChild(closeBtn);
      content.appendChild(emptyContainer);
      modal.appendChild(content);
      document.body.appendChild(modal);
      requestAnimationFrame(() => {
        modal.style.opacity = '1';
        content.style.transform = 'scale(1)';
      });
      return;
    }
    
    let currentIndex = 0;
    let showingBack = false;
    let isEditing = false;
    let currentCardOrder = cardOrder; // Can be changed during quiz
    const originalCardsOrder = [...cards]; // Store original order for sequential mode
    
    // Function to save card changes
    const saveCardChanges = async (idx, newQ, newA) => {
      cards[idx] = { q: newQ, a: newA, front: newQ, back: newA };
      // Try to save to library if we have a filename
      if (currentFileName && window.pywebview && window.pywebview.api) {
        try {
          const result = await window.pywebview.api.load_library_data(currentFileName);
          if (result && result.ok && result.data) {
            if (!result.data.flashcards) result.data.flashcards = {};
            // Find which page this card belongs to (we'll need to track this)
            // For now, save to page 1 as fallback
            const page = 1; // TODO: track page numbers for cards
            if (!result.data.flashcards[page]) result.data.flashcards[page] = [];
            const cardIdx = result.data.flashcards[page].findIndex(c => 
              (c.q === cards[idx].q || c.front === cards[idx].front) && 
              (c.a === cards[idx].a || c.back === cards[idx].back)
            );
            if (cardIdx >= 0) {
              result.data.flashcards[page][cardIdx] = { q: newQ, a: newA };
            }
            await window.pywebview.api.save_library_data(currentFileName, result.data);
          }
        } catch (e) {
          console.error('Error saving card changes:', e);
        }
      }
    };
    
    // Header
    const header = document.createElement('div');
    header.style.cssText = `display:flex; justify-content:space-between; align-items:center; padding:24px 30px; border-bottom:1px solid ${borderColor}; background:${isLightMode ? '#fafafa' : '#242426'};`;
    
    const headerLeft = document.createElement('div');
    headerLeft.style.cssText = 'display:flex; align-items:center; gap:16px;';
    
    const titleEl = document.createElement('h2');
    titleEl.textContent = title || 'Flashcards';
    titleEl.style.cssText = `font-size:22px; color:${textColor}; margin:0; font-weight:600;`;
    headerLeft.appendChild(titleEl);
    
    // Settings button
    const settingsBtn = document.createElement('button');
    settingsBtn.innerHTML = iconSettings;
    settingsBtn.title = 'Settings';
    settingsBtn.style.cssText = `width:32px; height:32px; background:${isLightMode ? 'rgba(0,0,0,0.05)' : 'rgba(255,255,255,0.1)'}; border:1px solid ${borderColor}; border-radius:8px; color:${textColor}; cursor:pointer; display:flex; align-items:center; justify-content:center; transition:all 0.2s;`;
    settingsBtn.onmouseover = () => settingsBtn.style.background = isLightMode ? 'rgba(0,0,0,0.1)' : 'rgba(255,255,255,0.15)';
    settingsBtn.onmouseout = () => settingsBtn.style.background = isLightMode ? 'rgba(0,0,0,0.05)' : 'rgba(255,255,255,0.1)';
    
    header.appendChild(headerLeft);
    
    const closeBtn = document.createElement('button');
    closeBtn.innerHTML = iconClose;
    closeBtn.style.cssText = `position:absolute; top:20px; right:20px; width:36px; height:36px; background:${isLightMode ? 'rgba(0,0,0,0.05)' : 'rgba(255,255,255,0.1)'}; border:none; border-radius:8px; color:${textColor}; cursor:pointer; display:flex; align-items:center; justify-content:center; transition:all 0.2s; z-index:10;`;
    closeBtn.onmouseover = () => closeBtn.style.background = isLightMode ? 'rgba(0,0,0,0.1)' : 'rgba(255,255,255,0.15)';
    closeBtn.onmouseout = () => closeBtn.style.background = isLightMode ? 'rgba(0,0,0,0.05)' : 'rgba(255,255,255,0.1)';
    closeBtn.onclick = () => {
      modal.style.opacity = '0';
      content.style.transform = 'scale(0.95)';
      setTimeout(() => {
        modal.remove();
        scrollbarStyles.remove();
        if (backCallback) backCallback();
      }, 200);
    };
    
    // Scrollable content area
    const scrollContent = document.createElement('div');
    scrollContent.className = 'smooth-scroll';
    scrollContent.style.cssText = 'flex:1; overflow-y:auto; padding:30px;';
    
    // Card container with all controls inside
    const cardDiv = document.createElement('div');
    cardDiv.style.cssText = `background:${cardBg}; border:2px solid ${cardBorder}; border-radius:16px; padding:50px 40px; min-height:350px; display:flex; flex-direction:column; align-items:center; justify-content:center; margin-bottom:30px; cursor:pointer; transition:all 0.3s cubic-bezier(0.4, 0, 0.2, 1); position:relative; overflow:visible;`;
    
    // Container for the card content and list (so list extends downward)
    const cardWrapper = document.createElement('div');
    cardWrapper.style.cssText = 'width:100%; display:flex; flex-direction:column; position:relative;';
    
    // "View All Cards" button - top-left of card (icon only, no box)
    const listToggle = document.createElement('button');
    listToggle.innerHTML = iconList;
    listToggle.title = 'View All Cards';
    listToggle.style.cssText = `position:absolute; top:16px; left:16px; width:32px; height:32px; background:transparent; border:none; color:${isLightMode ? '#666666' : '#a0a0a2'}; cursor:pointer; display:flex; align-items:center; justify-content:center; transition:all 0.2s; z-index:5; padding:4px; border-radius:6px;`;
    listToggle.onmouseover = () => {
      listToggle.style.background = isLightMode ? 'rgba(90,159,212,0.15)' : 'rgba(90,159,212,0.2)';
      listToggle.style.color = isLightMode ? '#4a7fa4' : '#5a9fd4';
    };
    listToggle.onmouseout = () => {
      listToggle.style.background = 'transparent';
      listToggle.style.color = isLightMode ? '#666666' : '#a0a0a2';
    };
    
    // Edit button - top-right of card (icon only, no box)
    const editBtn = document.createElement('button');
    editBtn.innerHTML = iconEdit;
    editBtn.title = 'Edit Card';
    editBtn.style.cssText = `position:absolute; top:16px; right:16px; width:32px; height:32px; background:transparent; border:none; color:${textSecondary}; cursor:pointer; display:flex; align-items:center; justify-content:center; transition:all 0.2s; z-index:5; opacity:0; pointer-events:none; padding:4px; border-radius:6px;`;
    editBtn.onmouseover = () => {
      editBtn.style.background = isLightMode ? 'rgba(0,0,0,0.1)' : 'rgba(255,255,255,0.1)';
      editBtn.style.color = textColor;
    };
    editBtn.onmouseout = () => {
      editBtn.style.background = 'transparent';
      editBtn.style.color = textSecondary;
    };
    
    // Previous button - left side inside card
    const prevBtn = document.createElement('button');
    prevBtn.innerHTML = iconArrowLeft;
    prevBtn.style.cssText = `position:absolute; left:16px; top:50%; transform:translateY(-50%); width:44px; height:44px; background:${isLightMode ? 'rgba(0,0,0,0.05)' : 'rgba(255,255,255,0.1)'}; border:1px solid ${borderColor}; border-radius:50%; color:${textColor}; cursor:pointer; display:flex; align-items:center; justify-content:center; transition:all 0.2s; z-index:5; opacity:0; pointer-events:none;`;
    prevBtn.onmouseover = () => prevBtn.style.background = isLightMode ? 'rgba(0,0,0,0.1)' : 'rgba(255,255,255,0.15)';
    prevBtn.onmouseout = () => prevBtn.style.background = isLightMode ? 'rgba(0,0,0,0.05)' : 'rgba(255,255,255,0.1)';
    prevBtn.onclick = (e) => {
      e.stopPropagation();
      if (currentIndex > 0 && !isEditing) {
        currentIndex--;
        updateCard();
      }
    };
    
    // Next button - right side inside card
    const nextBtn = document.createElement('button');
    nextBtn.innerHTML = iconArrowRight;
    nextBtn.style.cssText = `position:absolute; right:16px; top:50%; transform:translateY(-50%); width:44px; height:44px; background:${isLightMode ? 'rgba(0,0,0,0.05)' : 'rgba(255,255,255,0.1)'}; border:1px solid ${borderColor}; border-radius:50%; color:${textColor}; cursor:pointer; display:flex; align-items:center; justify-content:center; transition:all 0.2s; z-index:5; opacity:0; pointer-events:none;`;
    nextBtn.onmouseover = () => nextBtn.style.background = isLightMode ? 'rgba(0,0,0,0.1)' : 'rgba(255,255,255,0.15)';
    nextBtn.onmouseout = () => nextBtn.style.background = isLightMode ? 'rgba(0,0,0,0.05)' : 'rgba(255,255,255,0.1)';
    nextBtn.onclick = (e) => {
      e.stopPropagation();
      if (currentIndex < cards.length - 1 && !isEditing) {
        currentIndex++;
        updateCard();
      }
    };
    
    // Update nav button visibility
    const updateNavButtons = () => {
      if (isEditing) {
        prevBtn.style.opacity = '0';
        prevBtn.style.pointerEvents = 'none';
        nextBtn.style.opacity = '0';
        nextBtn.style.pointerEvents = 'none';
      } else {
        // Previous button - always visible if not at start
        if (currentIndex > 0) {
          prevBtn.style.opacity = '1';
          prevBtn.style.pointerEvents = 'auto';
        } else {
          prevBtn.style.opacity = '0.3';
          prevBtn.style.pointerEvents = 'none';
        }
        // Next button - always visible if not at end
        if (currentIndex < cards.length - 1) {
          nextBtn.style.opacity = '1';
          nextBtn.style.pointerEvents = 'auto';
        } else {
          nextBtn.style.opacity = '0.3';
          nextBtn.style.pointerEvents = 'none';
        }
      }
    };
    
    // Show edit button on card hover
    cardDiv.onmouseenter = () => {
      if (!isEditing) {
        editBtn.style.opacity = '1';
        editBtn.style.pointerEvents = 'auto';
      }
      updateNavButtons();
    };
    cardDiv.onmouseleave = () => {
      if (!isEditing) {
        editBtn.style.opacity = '0';
        editBtn.style.pointerEvents = 'none';
      }
      updateNavButtons();
    };
    
    // Card counter - bottom center of card
    const cardCounter = document.createElement('div');
    cardCounter.style.cssText = `position:absolute; bottom:16px; left:50%; transform:translateX(-50%); font-size:13px; color:${textSecondary}; font-weight:500; padding:6px 12px; background:${isLightMode ? 'rgba(0,0,0,0.05)' : 'rgba(255,255,255,0.1)'}; border-radius:12px; z-index:5;`;
    function updateCounter() {
      cardCounter.textContent = `${currentIndex + 1} / ${cards.length}`;
    }
    updateCounter();
    
    // Card content
    const cardContent = document.createElement('div');
    cardContent.style.cssText = 'width:100%; display:flex; flex-direction:column; align-items:center; justify-content:center; position:relative; z-index:1;';
    
    const frontText = document.createElement('div');
    frontText.style.cssText = `font-size:22px; color:${textColor}; text-align:center; line-height:1.7; width:100%; font-weight:500;`;
    
    const backText = document.createElement('div');
    backText.style.cssText = `margin-top:40px; padding-top:40px; border-top:2px solid ${borderColor}; font-size:20px; color:${textSecondary}; text-align:center; line-height:1.7; width:100%; display:none; opacity:0; transition:opacity 0.3s ease;`;
    
    // Edit mode elements
    const editContainer = document.createElement('div');
    editContainer.style.cssText = 'display:none; width:100%; flex-direction:column; gap:16px;';
    const editQ = document.createElement('textarea');
    editQ.style.cssText = `width:100%; min-height:120px; padding:16px; background:${bgColor}; border:2px solid ${isLightMode ? '#5a9fd4' : '#5a9fd4'}; border-radius:8px; color:${textColor}; font-size:18px; font-family:inherit; resize:vertical;`;
    const editA = document.createElement('textarea');
    editA.style.cssText = `width:100%; min-height:120px; padding:16px; background:${bgColor}; border:2px solid ${isLightMode ? '#5a9fd4' : '#5a9fd4'}; border-radius:8px; color:${textColor}; font-size:18px; font-family:inherit; resize:vertical;`;
    const editActions = document.createElement('div');
    editActions.style.cssText = 'display:flex; gap:12px; justify-content:flex-end;';
    const saveEditBtn = document.createElement('button');
    saveEditBtn.textContent = 'Save';
    saveEditBtn.style.cssText = `padding:10px 24px; background:#5a9fd4; border:none; color:#fff; border-radius:8px; cursor:pointer; font-size:14px; font-weight:600; transition:all 0.2s;`;
    saveEditBtn.onmouseover = () => saveEditBtn.style.background = '#4a8fc4';
    saveEditBtn.onmouseout = () => saveEditBtn.style.background = '#5a9fd4';
    const cancelEditBtn = document.createElement('button');
    cancelEditBtn.textContent = 'Cancel';
    cancelEditBtn.style.cssText = `padding:10px 24px; background:${isLightMode ? '#f5f5f5' : '#2a2a2c'}; border:1px solid ${borderColor}; color:${textColor}; border-radius:8px; cursor:pointer; font-size:14px; font-weight:500; transition:all 0.2s;`;
    cancelEditBtn.onmouseover = () => cancelEditBtn.style.background = isLightMode ? '#eeeeee' : '#333335';
    cancelEditBtn.onmouseout = () => cancelEditBtn.style.background = isLightMode ? '#f5f5f5' : '#2a2a2c';
    editActions.appendChild(saveEditBtn);
    editActions.appendChild(cancelEditBtn);
    editContainer.appendChild(editQ);
    editContainer.appendChild(editA);
    editContainer.appendChild(editActions);
    
    let originalQ = '', originalA = '';
    let clickOutsideHandler = null;
    
    const startEdit = () => {
      if (isEditing) return;
      isEditing = true;
      const card = cards[currentIndex];
      originalQ = card.front || card.q || '';
      originalA = card.back || card.a || '';
      editQ.value = originalQ;
      editA.value = originalA;
      
      cardWrapper.style.display = 'none';
      editContainer.style.display = 'flex';
      editBtn.style.opacity = '1';
      editBtn.style.pointerEvents = 'auto';
      updateNavButtons();
      
      setTimeout(() => editQ.focus(), 10);
      
      clickOutsideHandler = (e) => {
        if (!cardDiv.contains(e.target) && !editContainer.contains(e.target)) {
          saveEdit();
        }
      };
      setTimeout(() => document.addEventListener('click', clickOutsideHandler), 100);
    };
    
    const saveEdit = async () => {
      if (!isEditing) return;
      const newQ = editQ.value.trim();
      const newA = editA.value.trim();
      
      if (newQ !== originalQ || newA !== originalA) {
        await saveCardChanges(currentIndex, newQ, newA);
        cards[currentIndex] = { q: newQ, a: newA, front: newQ, back: newA };
        updateCard(false);
      }
      
      isEditing = false;
      cardWrapper.style.display = 'flex';
      editContainer.style.display = 'none';
      editBtn.style.opacity = '0';
      editBtn.style.pointerEvents = 'none';
      updateNavButtons();
      
      if (clickOutsideHandler) {
        document.removeEventListener('click', clickOutsideHandler);
        clickOutsideHandler = null;
      }
    };
    
    const cancelEdit = () => {
      if (!isEditing) return;
      isEditing = false;
      cardWrapper.style.display = 'flex';
      editContainer.style.display = 'none';
      editBtn.style.opacity = '0';
      editBtn.style.pointerEvents = 'none';
      updateNavButtons();
      
      if (clickOutsideHandler) {
        document.removeEventListener('click', clickOutsideHandler);
        clickOutsideHandler = null;
      }
    };
    
    editBtn.onclick = (e) => {
      e.stopPropagation();
      startEdit();
    };
    saveEditBtn.onclick = (e) => {
      e.stopPropagation();
      saveEdit();
    };
    cancelEditBtn.onclick = (e) => {
      e.stopPropagation();
      cancelEdit();
    };
    
    editQ.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        cancelEdit();
      }
    });
    editA.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        cancelEdit();
      }
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault();
        saveEdit();
      }
    });
    
    function updateCard(animate = true) {
      const card = cards[currentIndex];
      const front = card.front || card.q || '';
      const back = card.back || card.a || '';
      frontText.innerHTML = escapeHtml(front).replace(/\n/g, '<br>');
      backText.innerHTML = escapeHtml(back).replace(/\n/g, '<br>');
      showingBack = false;
      backText.style.display = 'none';
      backText.style.opacity = '0';
      if (animate) {
        cardDiv.style.opacity = '0';
        cardDiv.style.transform = 'translateX(-10px)';
        setTimeout(() => {
          cardDiv.style.opacity = '1';
          cardDiv.style.transform = 'translateX(0)';
        }, 150);
      }
      updateCounter();
      updateNavButtons();
      // Update list highlights if list is showing
      if (showingList) {
        renderList();
      }
    }
    
    cardContent.appendChild(frontText);
    cardContent.appendChild(backText);
    cardDiv.appendChild(listToggle);
    cardDiv.appendChild(editBtn);
    cardDiv.appendChild(prevBtn);
    cardDiv.appendChild(nextBtn);
    cardDiv.appendChild(cardCounter);
    cardDiv.appendChild(cardWrapper);
    cardDiv.appendChild(editContainer);
    
    cardDiv.onclick = (e) => {
      // Don't flip if clicking on buttons or editing
      if (e.target.closest('button') || isEditing) return;
      if (showingBack) {
        showingBack = false;
        backText.style.opacity = '0';
        setTimeout(() => backText.style.display = 'none', 300);
      } else {
        showingBack = true;
        backText.style.display = 'block';
        setTimeout(() => backText.style.opacity = '1', 10);
      }
    };
    
    // View All Cards list container - extends card downward
    let showingList = false;
    const listContainer = document.createElement('div');
    listContainer.style.cssText = `display:none; width:100%; margin-top:20px; padding:20px; border:2px solid ${borderColor}; border-radius:12px; background:${isLightMode ? 'rgba(0,0,0,0.01)' : 'rgba(255,255,255,0.02)'}; max-height:400px; overflow-y:auto; transition:all 0.3s ease;`;
    
    const renderList = () => {
      listContainer.innerHTML = '';
      const list = document.createElement('div');
      list.className = 'smooth-scroll';
      list.style.cssText = `display:flex; flex-direction:column; gap:12px;`;
      
      // If we have lecture groups, show grouped by lecture
      if (lectureGroups && lectureGroups.length > 1) {
        lectureGroups.forEach((group, groupIdx) => {
          const groupHeader = document.createElement('div');
          groupHeader.style.cssText = `font-size:14px; font-weight:600; color:${textColor}; margin-bottom:8px; padding-bottom:8px; border-bottom:1px solid ${borderColor};`;
          groupHeader.textContent = `${group.name} (${group.cards.length} cards)`;
          list.appendChild(groupHeader);
          
          group.cards.forEach((c, cardIdx) => {
            const idx = group.startIdx + cardIdx;
            const d = document.createElement('div');
            d.style.cssText = `background:${cardBg}; border:2px solid ${idx === currentIndex ? '#5a9fd4' : cardBorder}; border-radius:10px; padding:16px 20px; cursor:pointer; transition:all 0.2s; margin-left:16px;`;
            if (idx === currentIndex) {
              d.style.background = isLightMode ? 'rgba(90,159,212,0.15)' : 'rgba(90,159,212,0.2)';
            }
            d.onmouseover = () => {
              if (idx !== currentIndex) {
                d.style.background = isLightMode ? 'rgba(0,0,0,0.05)' : 'rgba(255,255,255,0.08)';
                d.style.borderColor = isLightMode ? 'rgba(90,159,212,0.5)' : 'rgba(90,159,212,0.6)';
              }
            };
            d.onmouseout = () => {
              if (idx !== currentIndex) {
                d.style.background = cardBg;
                d.style.borderColor = cardBorder;
              }
            };
            const front = c.front || c.q || '';
            const back = c.back || c.a || '';
            
            // Create editable content
            const qDiv = document.createElement('div');
            qDiv.style.cssText = `font-weight:600;color:${textColor};margin-bottom:8px;font-size:15px;`;
            qDiv.textContent = `Q: ${front}`;
            qDiv.contentEditable = false;
            
            const aDiv = document.createElement('div');
            aDiv.style.cssText = `color:${textSecondary};font-size:14px;`;
            aDiv.textContent = `A: ${back}`;
            aDiv.contentEditable = false;
            
            // Edit button for this card
            const editCardBtn = document.createElement('button');
            editCardBtn.innerHTML = iconEdit;
            editCardBtn.title = 'Edit Card';
            editCardBtn.style.cssText = `position:absolute; top:12px; right:12px; width:28px; height:28px; background:transparent; border:none; color:${textSecondary}; cursor:pointer; display:flex; align-items:center; justify-content:center; transition:all 0.2s; opacity:0; padding:4px; border-radius:6px;`;
            editCardBtn.onmouseover = () => {
              editCardBtn.style.background = isLightMode ? 'rgba(0,0,0,0.1)' : 'rgba(255,255,255,0.1)';
              editCardBtn.style.color = textColor;
            };
            editCardBtn.onmouseout = () => {
              editCardBtn.style.background = 'transparent';
              editCardBtn.style.color = textSecondary;
            };
            
            let isEditingCard = false;
            editCardBtn.onclick = async (e) => {
              e.stopPropagation();
              if (isEditingCard) {
                // Save
                const newQ = qDiv.textContent.replace(/^Q:\s*/, '').trim();
                const newA = aDiv.textContent.replace(/^A:\s*/, '').trim();
                await saveCardChanges(idx, newQ, newA);
                cards[idx] = { q: newQ, a: newA, front: newQ, back: newA };
                if (idx === currentIndex) updateCard(false);
                qDiv.contentEditable = false;
                aDiv.contentEditable = false;
                qDiv.style.border = 'none';
                aDiv.style.border = 'none';
                qDiv.style.padding = '0';
                aDiv.style.padding = '0';
                isEditingCard = false;
                editCardBtn.innerHTML = iconEdit;
              } else {
                // Start editing
                isEditingCard = true;
                qDiv.contentEditable = true;
                aDiv.contentEditable = true;
                qDiv.style.border = `1px solid ${isLightMode ? '#5a9fd4' : '#5a9fd4'}`;
                aDiv.style.border = `1px solid ${isLightMode ? '#5a9fd4' : '#5a9fd4'}`;
                qDiv.style.padding = '4px';
                aDiv.style.padding = '4px';
                qDiv.style.borderRadius = '4px';
                aDiv.style.borderRadius = '4px';
                editCardBtn.innerHTML = iconCheck;
                setTimeout(() => qDiv.focus(), 10);
              }
            };
            
            d.onmouseenter = () => {
              editCardBtn.style.opacity = '1';
            };
            d.onmouseleave = () => {
              if (!isEditingCard) editCardBtn.style.opacity = '0';
            };
            
            d.style.position = 'relative';
            d.appendChild(qDiv);
            d.appendChild(aDiv);
            d.appendChild(editCardBtn);
            
            d.onclick = (e) => {
              if (e.target === editCardBtn || e.target.closest('button')) return;
              e.stopPropagation();
              currentIndex = idx;
              updateCard();
              renderList(); // Re-render to update highlights
            };
            list.appendChild(d);
          });
        });
      } else {
        // No grouping, show all cards flat
        cards.forEach((c, idx) => {
          const d = document.createElement('div');
          d.style.cssText = `background:${cardBg}; border:2px solid ${idx === currentIndex ? '#5a9fd4' : cardBorder}; border-radius:10px; padding:16px 20px; cursor:pointer; transition:all 0.2s;`;
          if (idx === currentIndex) {
            d.style.background = isLightMode ? 'rgba(90,159,212,0.15)' : 'rgba(90,159,212,0.2)';
          }
          d.onmouseover = () => {
            if (idx !== currentIndex) {
              d.style.background = isLightMode ? 'rgba(0,0,0,0.05)' : 'rgba(255,255,255,0.08)';
              d.style.borderColor = isLightMode ? 'rgba(90,159,212,0.5)' : 'rgba(90,159,212,0.6)';
            }
          };
          d.onmouseout = () => {
            if (idx !== currentIndex) {
              d.style.background = cardBg;
              d.style.borderColor = cardBorder;
            }
          };
          const front = c.front || c.q || '';
          const back = c.back || c.a || '';
          
          // Create editable content
          const qDiv = document.createElement('div');
          qDiv.style.cssText = `font-weight:600;color:${textColor};margin-bottom:8px;font-size:15px;`;
          qDiv.textContent = `Q: ${front}`;
          qDiv.contentEditable = false;
          
          const aDiv = document.createElement('div');
          aDiv.style.cssText = `color:${textSecondary};font-size:14px;`;
          aDiv.textContent = `A: ${back}`;
          aDiv.contentEditable = false;
          
          // Edit button for this card
          const editCardBtn = document.createElement('button');
          editCardBtn.innerHTML = iconEdit;
          editCardBtn.title = 'Edit Card';
          editCardBtn.style.cssText = `position:absolute; top:12px; right:12px; width:28px; height:28px; background:transparent; border:none; color:${textSecondary}; cursor:pointer; display:flex; align-items:center; justify-content:center; transition:all 0.2s; opacity:0; padding:4px; border-radius:6px;`;
          editCardBtn.onmouseover = () => {
            editCardBtn.style.background = isLightMode ? 'rgba(0,0,0,0.1)' : 'rgba(255,255,255,0.1)';
            editCardBtn.style.color = textColor;
          };
          editCardBtn.onmouseout = () => {
            editCardBtn.style.background = 'transparent';
            editCardBtn.style.color = textSecondary;
          };
          
          let isEditingCard = false;
          editCardBtn.onclick = async (e) => {
            e.stopPropagation();
            if (isEditingCard) {
              // Save
              const newQ = qDiv.textContent.replace(/^Q:\s*/, '').trim();
              const newA = aDiv.textContent.replace(/^A:\s*/, '').trim();
              await saveCardChanges(idx, newQ, newA);
              cards[idx] = { q: newQ, a: newA, front: newQ, back: newA };
              if (idx === currentIndex) updateCard(false);
              qDiv.contentEditable = false;
              aDiv.contentEditable = false;
              qDiv.style.border = 'none';
              aDiv.style.border = 'none';
              qDiv.style.padding = '0';
              aDiv.style.padding = '0';
              isEditingCard = false;
              editCardBtn.innerHTML = iconEdit;
            } else {
              // Start editing
              isEditingCard = true;
              qDiv.contentEditable = true;
              aDiv.contentEditable = true;
              qDiv.style.border = `1px solid ${isLightMode ? '#5a9fd4' : '#5a9fd4'}`;
              aDiv.style.border = `1px solid ${isLightMode ? '#5a9fd4' : '#5a9fd4'}`;
              qDiv.style.padding = '4px';
              aDiv.style.padding = '4px';
              qDiv.style.borderRadius = '4px';
              aDiv.style.borderRadius = '4px';
              editCardBtn.innerHTML = iconCheck;
              setTimeout(() => qDiv.focus(), 10);
            }
          };
          
          d.onmouseenter = () => {
            editCardBtn.style.opacity = '1';
          };
          d.onmouseleave = () => {
            if (!isEditingCard) editCardBtn.style.opacity = '0';
          };
          
          d.style.position = 'relative';
          d.appendChild(qDiv);
          d.appendChild(aDiv);
          d.appendChild(editCardBtn);
          
          d.onclick = (e) => {
            if (e.target === editCardBtn || e.target.closest('button')) return;
            e.stopPropagation();
            currentIndex = idx;
            updateCard();
            renderList(); // Re-render to update highlights
          };
          list.appendChild(d);
        });
      }
      listContainer.appendChild(list);
    };
    
    listToggle.onclick = (e) => {
      e.stopPropagation();
      if (showingList) {
        showingList = false;
        listContainer.style.display = 'none';
      } else {
        showingList = true;
        listContainer.style.display = 'block';
        renderList();
      }
    };
    
    cardWrapper.appendChild(cardContent);
    cardWrapper.appendChild(listContainer);
    
    scrollContent.appendChild(cardDiv);
    
    // Settings panel
    let showingSettings = false;
    const settingsPanel = document.createElement('div');
    settingsPanel.style.cssText = `display:none; position:absolute; top:70px; right:20px; background:${bgColor}; border:1px solid ${borderColor}; border-radius:12px; padding:20px; min-width:280px; z-index:30; box-shadow:0 8px 24px rgba(0,0,0,0.3);`;
    
    const settingsTitle = document.createElement('div');
    settingsTitle.textContent = 'Quiz Settings';
    settingsTitle.style.cssText = `font-size:16px; font-weight:600; color:${textColor}; margin-bottom:16px;`;
    
    // Card order toggle
    const orderLabel = document.createElement('div');
    orderLabel.textContent = 'Card Order';
    orderLabel.style.cssText = `font-size:14px; color:${textColor}; margin-bottom:8px;`;
    
    const orderToggle = document.createElement('div');
    orderToggle.style.cssText = 'display:flex; gap:12px; align-items:center; margin-bottom:16px;';
    
    const randomBtn = document.createElement('button');
    randomBtn.textContent = 'Random';
    randomBtn.style.cssText = `flex:1; padding:8px 16px; background:${currentCardOrder === 'random' ? '#5a9fd4' : (isLightMode ? '#f5f5f5' : '#2a2a2c')}; border:1px solid ${currentCardOrder === 'random' ? '#5a9fd4' : borderColor}; color:${currentCardOrder === 'random' ? '#fff' : textColor}; border-radius:8px; cursor:pointer; font-size:13px; font-weight:500; transition:all 0.2s;`;
    randomBtn.onclick = () => {
      currentCardOrder = 'random';
      // Reshuffle cards
      for (let i = cards.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [cards[i], cards[j]] = [cards[j], cards[i]];
      }
      currentIndex = 0;
      updateCard();
      randomBtn.style.background = '#5a9fd4';
      randomBtn.style.borderColor = '#5a9fd4';
      randomBtn.style.color = '#fff';
      sequentialBtn.style.background = isLightMode ? '#f5f5f5' : '#2a2a2c';
      sequentialBtn.style.borderColor = borderColor;
      sequentialBtn.style.color = textColor;
    };
    
    const sequentialBtn = document.createElement('button');
    sequentialBtn.textContent = 'Sequential';
    sequentialBtn.style.cssText = `flex:1; padding:8px 16px; background:${currentCardOrder === 'sequential' ? '#5a9fd4' : (isLightMode ? '#f5f5f5' : '#2a2a2c')}; border:1px solid ${currentCardOrder === 'sequential' ? '#5a9fd4' : borderColor}; color:${currentCardOrder === 'sequential' ? '#fff' : textColor}; border-radius:8px; cursor:pointer; font-size:13px; font-weight:500; transition:all 0.2s;`;
    sequentialBtn.onclick = () => {
      currentCardOrder = 'sequential';
      // Restore original order
      const currentCard = cards[currentIndex];
      cards.splice(0, cards.length, ...originalCardsOrder);
      // Find the current card in the restored order
      const newIndex = cards.findIndex(c => 
        (c.front || c.q) === (currentCard.front || currentCard.q) &&
        (c.back || c.a) === (currentCard.back || currentCard.a)
      );
      currentIndex = newIndex >= 0 ? newIndex : 0;
      updateCard();
      sequentialBtn.style.background = '#5a9fd4';
      sequentialBtn.style.borderColor = '#5a9fd4';
      sequentialBtn.style.color = '#fff';
      randomBtn.style.background = isLightMode ? '#f5f5f5' : '#2a2a2c';
      randomBtn.style.borderColor = borderColor;
      randomBtn.style.color = textColor;
    };
    
    orderToggle.appendChild(randomBtn);
    orderToggle.appendChild(sequentialBtn);
    
    settingsPanel.appendChild(settingsTitle);
    settingsPanel.appendChild(orderLabel);
    settingsPanel.appendChild(orderToggle);
    
    settingsBtn.onclick = (e) => {
      e.stopPropagation();
      showingSettings = !showingSettings;
      settingsPanel.style.display = showingSettings ? 'block' : 'none';
    };
    
    // Close settings when clicking outside
    document.addEventListener('click', (e) => {
      if (showingSettings && !settingsPanel.contains(e.target) && !settingsBtn.contains(e.target)) {
        showingSettings = false;
        settingsPanel.style.display = 'none';
      }
    });
    
    headerLeft.appendChild(settingsBtn);
    content.appendChild(settingsPanel);
    
    content.appendChild(closeBtn);
    content.appendChild(header);
    content.appendChild(scrollContent);
    modal.appendChild(content);
    document.body.appendChild(modal);
    
    updateCard(false);
    
    // Animate in
    requestAnimationFrame(() => {
      modal.style.opacity = '1';
      content.style.transform = 'scale(1)';
    });
    
    // Close on Escape key
    const handleEscape = (e) => {
      if (e.key === 'Escape') {
        if (isEditing) {
          cancelEdit();
        } else {
          modal.style.opacity = '0';
          content.style.transform = 'scale(0.95)';
          setTimeout(() => {
            modal.remove();
            scrollbarStyles.remove();
            document.removeEventListener('keydown', handleEscape);
            if (backCallback) backCallback();
          }, 200);
        }
      }
    };
    document.addEventListener('keydown', handleEscape);
    
    // Keyboard navigation
    const handleKeyNav = (e) => {
      if (isEditing) return;
      if (e.key === 'ArrowLeft' && currentIndex > 0) {
        e.preventDefault();
        e.stopPropagation();
        currentIndex--;
        updateCard();
      } else if (e.key === 'ArrowRight' && currentIndex < cards.length - 1) {
        e.preventDefault();
        e.stopPropagation();
        currentIndex++;
        updateCard();
      } else if (e.key === ' ' || e.key === 'Enter') {
        e.preventDefault();
        e.stopPropagation();
        if (!showingBack) {
          showingBack = true;
          backText.style.display = 'block';
          setTimeout(() => backText.style.opacity = '1', 10);
        } else {
          if (currentIndex < cards.length - 1) {
            currentIndex++;
            showingBack = false;
            backText.style.opacity = '0';
            setTimeout(() => {
              backText.style.display = 'none';
              updateCard();
            }, 150);
          }
        }
      }
    };
    document.addEventListener('keydown', handleKeyNav);
    modal.addEventListener('remove', () => {
      document.removeEventListener('keydown', handleKeyNav);
    });
  }
  
  document.getElementById('btnAddCard').onclick = ()=> addCard('', '');
  document.getElementById('btnViewCards').onclick = async () => {
    // Collect all flashcards from entire PDF, not just current page
    const allCards = [];
    const pages = Object.keys(cardsByPage).map(Number).sort((a, b) => a - b);
    for (const page of pages) {
      const pageCards = getPageCards(page);
      if (Array.isArray(pageCards) && pageCards.length > 0) {
        allCards.push(...pageCards);
      }
    }
    
    // If no cards in memory, try loading from library
    if (allCards.length === 0 && currentFileName) {
      try {
        const result = await window.pywebview.api.load_library_data(currentFileName);
        if (result && result.ok && result.data && result.data.flashcards) {
          const flashcardPages = Object.keys(result.data.flashcards).map(Number).sort((a, b) => a - b);
          for (const page of flashcardPages) {
            const cards = result.data.flashcards[page];
            if (Array.isArray(cards) && cards.length > 0) {
              allCards.push(...cards);
            }
          }
        }
      } catch (e) {
        console.error('Error loading flashcards from library:', e);
      }
    }
    
    if (allCards.length === 0) {
      alert('No flashcards available for this lecture');
      return;
    }
    
    const title = currentFileName ? `${currentFileName} - Flashcards` : 'Flashcards';
    openInteractiveFlashcardViewer(allCards, title, null);
  };
  document.getElementById('btnExportCards').onclick = async ()=>{
    // Get ALL flashcards from ALL pages
    const allFlashcards = {};
    let totalCards = 0;
    
    Object.keys(cardsByPage).forEach(pageNum => {
      const pageCards = getPageCards(pageNum);
      if (pageCards.length > 0) {
        allFlashcards[String(pageNum)] = pageCards;
        totalCards += pageCards.length;
      }
    });

    if (totalCards === 0) {
      showToast('No flashcards to export.', 'warning');
      return;
    }

    const fileName = currentFileName ? currentFileName.replace(/\.[^/.]+$/, '') : 'flashcards';
    const safeFileName = fileName.replace(/[^a-z0-9]/gi, '_').toLowerCase() || 'flashcards';

    // Call Python API to save the file
    try {
      if (!window.pywebview || !window.pywebview.api) {
        showToast('Export API not available.', 'error');
        return;
      }
      const result = await window.pywebview.api.download_flashcards_anki(safeFileName, {'flashcards': allFlashcards});
      if (result && result.ok) {
        showToast(`Exported ${totalCards} flashcards from ${Object.keys(allFlashcards).length} pages to: ${result.path}`, 'success');
      } else {
        showToast(`Export failed: ${result?.error || 'Unknown error'}`, 'error');
      }
    } catch (error) {
      console.error('Error exporting flashcards:', error);
      showToast('Failed to export flashcards.', 'error');
    }
  };
  const btnClear = document.getElementById('btnClearCards');
  if (btnClear) btnClear.onclick = ()=>{ if (!currentPage) return; setPageCards(currentPage, []); refreshCards(currentPage); };
  
  // Setup tooltips for icon buttons
  setTimeout(() => {
    const iconButtons = document.querySelectorAll('.iconBtn[title]');
    iconButtons.forEach(btn => setupMacTooltip(btn));
    
    // Setup tooltip for quiz button
    const quizBtn = document.getElementById('btnViewCards');
    if (quizBtn && quizBtn.getAttribute('title')) {
      setupMacTooltip(quizBtn);
    }
  }, 100);

  // Auto-generate flashcards for a page if none cached
  let lastFlashcardsPage = null;
  async function doGenerateFlashcards(page, text){
    try {
      console.log(`doGenerateFlashcards called for page ${page}, text length: ${text?.length || 0}`);
      if (!page) {
        console.log('No page number, aborting');
        return;
      }
      
      // If this page has library data, DON'T generate - use library data instead
      if (pagesWithLibraryData.has(page)) {
        console.log(`⏭️ Page ${page} has library flashcards - skipping AI generation`);
        if (currentPage === page) {
          refreshCards(page);
        }
        return;
      }
      
      // If cards already exist in memory, just show them instantly
      const existing = getPageCards(page);
      if (existing.length) { 
        // Only update if still on this page (prevent race conditions)
        if (currentPage === page) {
          refreshCards(page); 
          console.log(`Using cached flashcards for page ${page}`);
        }
        return; 
      }
      
      // Prevent duplicate generation
      if (lastFlashcardsPage === page) {
        console.log(`Already generated for page ${page}, skipping`);
        return;
      }
      
      const trimmed = text && text.length>6000 ? text.slice(0,6000) : (text||'');
      if (!trimmed) {
        console.log('No text content, aborting flashcard generation');
        return;
      }
      if (!window.pywebview || !window.pywebview.api) {
        console.log('Pywebview API not available');
        return;
      }
      
      // Show loading state inside fcList
      fcList.innerHTML = '<div class="fcEmptyState"><div class="fcEmptyIcon">⏳</div><div class="fcEmptyText">Generating flashcards…</div></div>';
      console.log(`Calling API to generate flashcards for page ${page}...`);
      
      const res = await window.pywebview.api.generate_flashcards(trimmed, page);
      console.log('API response:', res);
      
      // Only update if still on this page (prevent race conditions)
      if (currentPage !== page) {
        console.log(`Page changed from ${page} to ${currentPage}, discarding results`);
        return;
      }
      
      if (res && res.ok && Array.isArray(res.cards)){
        if (res.cards.length === 0) {
          console.log('API returned 0 flashcards (likely NO_CARDS or no content)');
          fcList.innerHTML = '<div class="fcEmptyState"><div class="fcEmptyIcon">🃏</div><div class="fcEmptyText">No flashcards generated for this page.</div></div>';
          lastFlashcardsPage = page; // Mark as processed
        } else {
          const normalized = res.cards.map(it=>({ q: String(it.q||'').trim(), a: String(it.a||'').trim() }))
                                      .filter(it=>it.q || it.a);
          console.log(`Normalized ${normalized.length} flashcards`);
          setPageCards(page, normalized);
          refreshCards(page);
          lastFlashcardsPage = page;
          console.log(`Generated ${normalized.length} flashcards for page ${page}`);
        }
      } else {
        console.log('API error or invalid response:', res);
        fcList.innerHTML = '<div class="fcEmptyState"><div class="fcEmptyIcon">⚠️</div><div class="fcEmptyText">Error generating flashcards.</div></div>';
      }
    } catch(err) {
      console.error('Flashcard generation error:', err);
      fcList.innerHTML = '<div class="fcEmptyState"><div class="fcEmptyIcon">⚠️</div><div class="fcEmptyText">Error generating flashcards.</div></div>';
    }
  }

  // ---- Helpers: set box text (right panel) ----
  function setBox(el, txt){
    if (!txt || !txt.trim()) { 
      // Preserve resize handle when clearing
      const resizeHandle = el.querySelector('.resizeHandle');
      el.textContent = ''; 
      // Re-add resize handle if it was there
      if (resizeHandle) {
        el.appendChild(resizeHandle);
      }
      el.classList.add('rsEmpty');
      // Show placeholder if element has one
      if (el.hasAttribute('data-placeholder')) {
        el.setAttribute('data-placeholder', el.getAttribute('data-placeholder'));
      }
      return; 
    }
    
    // Function to parse markdown table
    function parseMarkdownTable(markdown) {
      const lines = markdown.split('\n');
      if (lines.length < 2) return null;
      
      // Helper to check if a line is a table separator
      function isTableSeparator(line) {
        const trimmed = line.trim();
        // Must start and end with |, and contain only dashes, colons, spaces, and pipes
        return /^\|[\s\-\|:]+\|$/.test(trimmed) && trimmed.length > 3;
      }
      
      // Helper to check if a line is a table row (has pipes and content)
      function isTableRow(line) {
        const trimmed = line.trim();
        return trimmed.includes('|') && trimmed.length > 2 && !isTableSeparator(trimmed);
      }
      
      // Find potential table start (line with |)
      let tableStart = -1;
      for (let i = 0; i < lines.length; i++) {
        if (isTableRow(lines[i])) {
          tableStart = i;
          break;
        }
      }
      
      if (tableStart === -1) return null;
      
      // Check if second line is a separator (standard markdown table format)
      // Only if there's a separator do we treat the first line as a header
      let hasSeparator = false;
      let headerRowIndex = -1;
      let firstDataRowIndex = tableStart;
      
      if (tableStart + 1 < lines.length) {
        const nextLine = lines[tableStart + 1].trim();
        if (isTableSeparator(nextLine)) {
          hasSeparator = true;
          headerRowIndex = tableStart;
          firstDataRowIndex = tableStart + 2;
        }
      }
      
      // If no separator, treat all lines as data rows (no header)
      // Find table end - continue until we hit a truly empty line or non-table content
      let tableEnd = firstDataRowIndex;
      let consecutiveEmptyLines = 0;
      let lastTableRowIndex = tableStart;
      
      for (let i = firstDataRowIndex; i < lines.length; i++) {
        const trimmed = lines[i].trim();
        
        // If it's a table row, continue
        if (isTableRow(trimmed)) {
          tableEnd = i + 1;
          lastTableRowIndex = i;
          consecutiveEmptyLines = 0;
          continue;
        }
        
        // If it's a separator, skip it but don't end the table
        if (isTableSeparator(trimmed)) {
          continue;
        }
        
        // If it's empty, allow one empty line but not multiple
        if (trimmed === '') {
          consecutiveEmptyLines++;
          if (consecutiveEmptyLines > 1) {
            // Two consecutive empty lines = end of table
            break;
          }
          // Allow one empty line, might be formatting
          continue;
        }
        
        // If it's not a table row and not empty, end the table
        // But check if next line is a table row (might be a paragraph break)
        if (i + 1 < lines.length && isTableRow(lines[i + 1])) {
          // Next line is a table row, so this might be a paragraph break
          // Continue but mark this as potential end
          tableEnd = i;
            break;
        } else {
          // Not a table row and next line isn't either, end table
          break;
        }
      }
      
      // Need at least 2 rows (either header + 1 data row, or 2 data rows)
      if (tableEnd <= firstDataRowIndex && !hasSeparator) {
        // If no separator, we need at least 2 rows total
        if (tableEnd <= tableStart + 1) return null;
      } else if (hasSeparator && tableEnd <= firstDataRowIndex) {
        // If separator exists, need at least header + 1 data row
        return null;
      }
      
      // Extract all table lines (including header if separator exists, skipping separators)
      const tableLines = [];
      for (let i = tableStart; i < tableEnd; i++) {
        const trimmed = lines[i].trim();
        if (isTableRow(trimmed)) {
          tableLines.push(trimmed);
        }
        // Skip separators
      }
      
            if (tableLines.length < 2) return null; // Need at least 2 rows
      
      // Parse header only if separator exists, otherwise treat first line as data
      let headers = [];
      let dataStartIndex = 0;
      
      if (hasSeparator && headerRowIndex >= 0) {
        // Parse header (first line)
        const headerLine = tableLines[0];
        const headerParts = headerLine.split('|').map(p => p.trim());
        // Remove empty first/last if present
        if (headerParts[0] === '') headerParts.shift();
        if (headerParts[headerParts.length - 1] === '') headerParts.pop();
        
        headers = headerParts.filter(h => h !== '');
        if (headers.length === 0) return null;
        dataStartIndex = 1;
      } else {
        // No separator - treat first line as data and generate empty headers
        // We'll use the first row to determine column count
        const firstRow = tableLines[0];
        const firstRowParts = firstRow.split('|').map(p => p.trim());
        if (firstRowParts[0] === '') firstRowParts.shift();
        if (firstRowParts[firstRowParts.length - 1] === '') firstRowParts.pop();
        const firstRowCells = firstRowParts.filter(c => c !== '');
        // Create empty headers for each column
        headers = new Array(firstRowCells.length).fill('');
        dataStartIndex = 0;
      }
      
      // Parse rows and find maximum column count
      const rows = [];
      let maxColumns = headers.length || 0;
      
      for (let i = dataStartIndex; i < tableLines.length; i++) {
        const rowLine = tableLines[i];
        const rowParts = rowLine.split('|').map(p => p.trim());
        // Remove empty first/last if present
        if (rowParts[0] === '') rowParts.shift();
        if (rowParts[rowParts.length - 1] === '') rowParts.pop();
        
        const cells = rowParts.filter(c => c !== '');
        if (cells.length > 0) {
          rows.push(cells);
          // Update max columns if this row has more
          if (cells.length > maxColumns) {
            maxColumns = cells.length;
          }
        }
      }
      
      if (rows.length === 0) return null;
      
      // Pad headers if rows have more columns
      while (headers.length < maxColumns) {
        headers.push('');
      }
      
      return { headers, rows, maxColumns, start: tableStart, end: tableEnd };
    }
    
    // Function to convert markdown table to elegant HTML table
    function markdownTableToHTML(table) {
      // Function to process inline markdown (reuse from above scope)
      function processInlineMarkdown(text) {
        if (!text) return '';
        let processed = text;
        // Process inline code (backticks)
        processed = processed.replace(/`([^`]+)`/g, '<code>$1</code>');
        
        // Process bold and italic by temporarily replacing bold with placeholder
        const boldPlaceholder = '___BOLD_TABLE___';
        const boldMatches = [];
        
        // Extract all bold (**text**) and replace with placeholder
        processed = processed.replace(/\*\*([^*]+?)\*\*/g, (match, content) => {
          const placeholder = boldPlaceholder + boldMatches.length + '___';
          boldMatches.push('<strong>' + content + '</strong>');
          return placeholder;
        });
        
        // Now process italics (*text*) - safe because bold is already replaced
        // Simple pattern: match * followed by one or more non-asterisk characters, then *
        processed = processed.replace(/\*([^*\n\r]+?)\*/g, function(match, content) {
          // Only process if content has at least one non-whitespace character
          if (content.trim().length > 0) {
            return '<em>' + content + '</em>';
          }
          return match; // Return original if it's just whitespace
        });
        
        // Restore bold placeholders
        boldMatches.forEach((boldHtml, index) => {
          processed = processed.replace(boldPlaceholder + index + '___', boldHtml);
        });
        return processed;
      }
      
      let html = '<div class="elegant-table-wrapper"><div class="elegant-table-scroll"><table class="elegant-table">';
      
      // Determine column count (use maxColumns if available, otherwise headers length)
      const columnCount = table.maxColumns || table.headers.length;
      
      // Only show header row if at least one header has content
      const hasHeaderContent = table.headers.some(h => h && h.trim() !== '');
      
      if (hasHeaderContent) {
        html += '<thead><tr>';
        // Headers - render all columns with full markdown processing
        for (let i = 0; i < columnCount; i++) {
          const header = table.headers[i] || '';
          const headerText = processInlineMarkdown(header);
          html += `<th>${headerText}</th>`;
        }
        html += '</tr></thead>';
      }
      
      html += '<tbody>';
      
      // Rows - render all columns, padding as needed, with full markdown processing
      table.rows.forEach(row => {
        html += '<tr>';
        for (let i = 0; i < columnCount; i++) {
          const cellText = row[i] || '';
          const processedCell = processInlineMarkdown(cellText);
          html += `<td>${processedCell}</td>`;
        }
        html += '</tr>';
      });
      
      html += '</tbody></table></div></div>';
      return html;
    }
    
    // Function to process inline markdown (bold, italic, code, color spans)
    function processInlineMarkdown(text) {
      if (!text) return '';
      
      // Process color spans first (before other markdown)
      // These are already HTML, so we preserve them
      let processed = text;
      
      // Process inline code (backticks) - must be before bold/italic to avoid conflicts
      processed = processed.replace(/`([^`]+)`/g, '<code>$1</code>');
      
      // Process bold and italic by temporarily replacing bold with placeholder
      // This ensures we don't conflict between **bold** and *italic*
      const boldPlaceholder = '___BOLD___';
      const boldMatches = [];
      
      // Extract all bold (**text**) and replace with placeholder
      processed = processed.replace(/\*\*([^*]+?)\*\*/g, (match, content) => {
        const placeholder = boldPlaceholder + boldMatches.length + '___';
        boldMatches.push('<strong>' + content + '</strong>');
        return placeholder;
      });
      
      // Now process italics (*text*) - safe because bold is already replaced
      // Simple pattern: match * followed by one or more non-asterisk characters, then *
      // This will match *text*, *also known as*, etc.
      processed = processed.replace(/\*([^*\n\r]+?)\*/g, function(match, content) {
        // Only process if content has at least one non-whitespace character
        if (content.trim().length > 0) {
          return '<em>' + content + '</em>';
        }
        return match; // Return original if it's just whitespace
      });
      
      // Restore bold placeholders
      boldMatches.forEach((boldHtml, index) => {
        processed = processed.replace(boldPlaceholder + index + '___', boldHtml);
      });
      
      return processed;
    }
    
    // Function to process a line of markdown
    function processMarkdownLine(line) {
      const trimmed = line.trim();
      
      // Horizontal rule
      if (trimmed === '---' || trimmed.match(/^---+$/)) {
        return '<hr class="markdown-hr">';
      }
      
      // Block quote
      if (trimmed.startsWith('> ')) {
        const quoteText = trimmed.substring(2);
        return `<blockquote class="markdown-quote">${processInlineMarkdown(quoteText)}</blockquote>`;
      }
      
      // Headings
      if (trimmed.startsWith('## ')) {
        const headingText = trimmed.substring(3);
        return `<h2 class="markdown-h2">${processInlineMarkdown(headingText)}</h2>`;
      }
      if (trimmed.startsWith('### ')) {
        const headingText = trimmed.substring(4);
        return `<h3 class="markdown-h3">${processInlineMarkdown(headingText)}</h3>`;
      }
      
      // Numbered list item
      const numberedMatch = trimmed.match(/^(\d+)\.\s+(.+)$/);
      if (numberedMatch) {
        const itemText = numberedMatch[2];
        return `<li class="markdown-li-numbered">${processInlineMarkdown(itemText)}</li>`;
      }
      
      // Bullet point
      if (trimmed.startsWith('- ')) {
        const bulletText = trimmed.substring(2);
        return `<li class="markdown-li-bullet">${processInlineMarkdown(bulletText)}</li>`;
      }
      
      // Regular paragraph
      if (trimmed) {
        return `<p>${processInlineMarkdown(trimmed)}</p>`;
      }
      
      return '';
    }
    
    // Function to process markdown content (handles lists, paragraphs, etc.)
    function processMarkdownContent(text) {
      if (!text || !text.trim()) return '';
      
      const lines = text.split('\n');
      let result = '';
      let inNumberedList = false;
      let inBulletList = false;
      
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        const trimmed = line.trim();
        
        // Check if this is a numbered list item
        const isNumbered = /^\d+\.\s+/.test(trimmed);
        // Check if this is a bullet item
        const isBullet = trimmed.startsWith('- ');
        // Check if this is a block quote
        const isQuote = trimmed.startsWith('> ');
        
        // Handle numbered lists
        if (isNumbered) {
          if (!inNumberedList) {
            if (inBulletList) {
              result += '</ul>';
              inBulletList = false;
            }
            result += '<ol class="markdown-ol">';
            inNumberedList = true;
          }
          result += processMarkdownLine(line);
        }
        // Handle bullet lists
        else if (isBullet) {
          if (!inBulletList) {
            if (inNumberedList) {
              result += '</ol>';
              inNumberedList = false;
            }
            result += '<ul class="markdown-ul">';
            inBulletList = true;
          }
          result += processMarkdownLine(line);
        }
        // Handle block quotes (can be standalone)
        else if (isQuote) {
          if (inNumberedList) {
            result += '</ol>';
            inNumberedList = false;
          }
          if (inBulletList) {
            result += '</ul>';
            inBulletList = false;
          }
          result += processMarkdownLine(line);
        }
        // Handle empty lines (close lists, add spacing)
        else if (!trimmed) {
          if (inNumberedList) {
            result += '</ol>';
            inNumberedList = false;
          }
          if (inBulletList) {
            result += '</ul>';
            inBulletList = false;
          }
        }
        // Regular content
        else {
          if (inNumberedList) {
            result += '</ol>';
            inNumberedList = false;
          }
          if (inBulletList) {
            result += '</ul>';
            inBulletList = false;
          }
          result += processMarkdownLine(line);
        }
      }
      
      // Close any open lists
      if (inNumberedList) {
        result += '</ol>';
      }
      if (inBulletList) {
        result += '</ul>';
      }
      
      return result;
    }
    
    // Process text: detect and convert tables, then process other markdown
    let processedText = txt;
    let html = '';
    let lastIndex = 0;
    
    // Split by lines to process
    const allLines = txt.split('\n');
    let i = 0;
    
    while (i < allLines.length) {
      // Try to find a table starting at this line
      const remainingText = allLines.slice(i).join('\n');
      const table = parseMarkdownTable(remainingText);
      
      if (table) {
        // Add text before table
        if (i > lastIndex) {
          const beforeText = allLines.slice(lastIndex, i).join('\n');
          if (beforeText.trim()) {
            html += processMarkdownContent(beforeText);
          }
        }
        
        // Add table
        html += markdownTableToHTML(table);
        
        // Skip past the table - ensure we don't re-process the same table
        // table.end is relative to the start of remainingText, so add i to get absolute position
        i = i + table.end;
        lastIndex = i;
      } else {
        i++;
      }
    }
    
    // Add remaining text after last table
    if (lastIndex < allLines.length) {
      const remainingText = allLines.slice(lastIndex).join('\n');
      if (remainingText.trim()) {
        html += processMarkdownContent(remainingText);
      }
    }
    
    // If no tables were found, process entire text
    if (!html || html === '') {
      html = processMarkdownContent(txt);
    }
    
    // Store copy button and resize handle if they exist
    const existingCopyBtn = el.querySelector('.copyBtn');
    const resizeHandle = el.querySelector('.resizeHandle');
    
    el.innerHTML = html;
    el.classList.remove('rsEmpty', 'loading', 'error', 'success');
    
    // Check for loading/error/success states
    const txtLower = txt.toLowerCase().trim();
    // Check for various loading text patterns (including ellipsis characters)
    if (txtLower === 'generating…' || txtLower === 'generating...' || 
        txtLower === 'explaining…' || txtLower === 'explaining...' ||
        txtLower.includes('generating') || txtLower.includes('thinking') || 
        txtLower.includes('looking up') || txtLower.includes('explaining') ||
        txtLower.includes('generating flashcards')) {
      el.classList.add('loading');
    } else if (txtLower.startsWith('error:') || txtLower.includes('failed') || 
               txtLower.startsWith('error ')) {
      el.classList.add('error');
    } else if (txtLower.includes('success') || txtLower.includes('completed')) {
      el.classList.add('success');
    }
    
    // Update placeholder visibility
    updatePlaceholderVisibility(el);
    
    // Show actions when aiAnswer has content
    if (el.id === 'aiAnswer') {
      const aiActions = document.getElementById('aiActions');
      if (aiActions && html.trim()) {
        aiActions.style.display = 'flex';
      } else if (aiActions) {
        aiActions.style.display = 'none';
      }
    }
    
    // Re-add resize handle if it was there, or ensure it exists for rsBox elements
    if (resizeHandle) {
      el.appendChild(resizeHandle);
    } else if (el.classList.contains('rsBox') && !el.querySelector('.resizeHandle')) {
      // Ensure resize handle exists for rsBox elements
      setTimeout(() => setupResizeHandles(), 0);
    }
    
    // Add or restore copy button
    let copyBtn = el.querySelector('.copyBtn');
    if (!copyBtn) {
      copyBtn = document.createElement('button');
      copyBtn.className = 'copyBtn';
      copyBtn.title = 'Copy text';
      // Copy button is created with CSS ::before and ::after, no text needed
      el.appendChild(copyBtn);
    }
    
    // Set up copy functionality
    copyBtn.onclick = () => {
      const text = el.textContent || el.innerText || '';
      if (!text.trim()) return;
      navigator.clipboard.writeText(text).then(() => {
        // Visual feedback - animate to checkmark
        copyBtn.classList.add('copied');
        setTimeout(() => {
          copyBtn.classList.remove('copied');
        }, 1500);
      }).catch(err => {
        console.error('Failed to copy:', err);
      });
    };
  }
  
  // Handle placeholder visibility for contenteditable elements
  function updatePlaceholderVisibility(el) {
    if (!el || !el.hasAttribute('data-placeholder')) return;
    const text = el.textContent || el.innerText || '';
    const hasContent = text.trim().length > 0;
    // Check if text matches placeholder exactly
    const placeholder = el.getAttribute('data-placeholder');
    const isPlaceholder = placeholder && text.trim() === placeholder.trim();
    
    if (hasContent && !isPlaceholder) {
      el.classList.remove('rsEmpty');
    } else {
      el.classList.add('rsEmpty');
    }
  }
  
  // ---- Get text content from contenteditable div ----
  function getBoxText(el) {
    // Get text content, excluding placeholder text
    const text = el.textContent || el.innerText || '';
    // If text matches placeholder, return empty string
    const placeholder = el.getAttribute('data-placeholder');
    if (placeholder && text.trim() === placeholder.trim()) {
      return '';
    }
    return text;
  }
  
  // 2. Enhanced Toast Notification System
  function showToast(message, type = 'info') {
    // Ensure toast container exists
    let container = document.querySelector('.toast-container');
    if (!container) {
      container = document.createElement('div');
      container.className = 'toast-container';
      document.body.appendChild(container);
    }
    
    // Create toast element
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    
    const icons = {
      success: '✓',
      error: '⚠',
      info: 'ℹ'
    };
    
    toast.innerHTML = `
      <span class="toast-icon">${icons[type] || icons.info}</span>
      <span class="toast-content">${message}</span>
      <button class="toast-close" onclick="this.parentElement.remove()">×</button>
    `;
    
    container.appendChild(toast);
    
    // Auto-remove after 3 seconds
    setTimeout(() => {
      toast.style.animation = 'toastSlideIn 0.3s cubic-bezier(0.4, 0, 0.2, 1) reverse';
      setTimeout(() => {
        if (toast.parentNode) {
          toast.remove();
        }
      }, 300);
    }, 3000);
  }
  
  // Make toast function globally available
  window.showToast = showToast;
  
  // ---- Page summary management ----
  // SINGLE SOURCE OF TRUTH: All page updates go through this function
  let pendingPageUpdate = null;
  let isUpdatingPage = false;
  let lastPageUpdateTime = 0;
  let lastUpdatedPage = null;
  
  // Centralized page update function - this is the ONLY place that updates UI for page changes
  function updatePageUI(page) {
    // Validate page number
    if (!page || page < 1) return;
    
    // Prevent rapid successive calls (debounce)
    const now = Date.now();
    if (now - lastPageUpdateTime < 150) {
      // Too soon, schedule for later
      clearTimeout(pendingPageUpdate);
      pendingPageUpdate = setTimeout(() => updatePageUI(page), 200);
      return;
    }
    
    // Prevent concurrent updates
    if (isUpdatingPage) {
      clearTimeout(pendingPageUpdate);
      pendingPageUpdate = setTimeout(() => updatePageUI(page), 100);
      return;
    }
    
    // Skip if already on this page
    if (page === lastUpdatedPage && page === currentPage) {
      return;
    }
    
    isUpdatingPage = true;
    lastPageUpdateTime = now;
    lastUpdatedPage = page;
    currentPage = page;
    
    console.log(`[updatePageUI] ⚡ Updating UI for page ${page}`);
    
    // Update the big visible page number indicator
    if (currentPageNumber) {
      currentPageNumber.textContent = page;
      currentPageNumber.style.animation = 'pulse 0.3s';
    }
    
    // Load summary
    const cached = pageSummaryCache.get(page);
    if (cached) {
      setBox(rsSummary, cached.summary);
      if (summaryHeader) summaryHeader.textContent = 'Summary';
    } else {
      setBox(rsSummary, '');
      if (summaryHeader) summaryHeader.textContent = 'Summary';
    }
    
    // Refresh flashcards
    refreshCards(page);
    
    // Release lock after a short delay
    setTimeout(() => {
      isUpdatingPage = false;
    }, 50);
  }
  
  // Keep loadPageSummary for backward compatibility but route through updatePageUI
  function loadPageSummary(page) {
    updatePageUI(page);
  }
  
  function savePageSummary(page, summary) {
    pageSummaryCache.set(page, {
      summary: summary,
      timestamp: new Date().toISOString()
    });
    // Mark this page as having library data
    pagesWithLibraryData.add(page);
    if (summaryHeader) summaryHeader.textContent = 'Summary';
    
    // Update page number indicator
    if (currentPageNumber) {
      currentPageNumber.textContent = page;
    }
    
    console.log(`✓ Saved summary for page ${page}, summary: "${summary.substring(0, 100)}..."`);
    saveLibraryData(); // Auto-save to library
  }
  
  function clearCurrentSummary() {
    setBox(rsSummary, '');
    if (summaryHeader) summaryHeader.textContent = 'Summary';
  }
  
  // ---- Save/Clear functionality ----
  function saveCurrentSummary() {
    if (currentPage && getBoxText(rsSummary).trim()) {
      savePageSummary(currentPage, getBoxText(rsSummary).trim());
      console.log(`Manually saved summary for page ${currentPage}`);
      // Show visual feedback
      showToast(`Summary saved for page ${currentPage}`, 'success');
    } else {
      showToast('No summary content to save', 'warning');
    }
  }
  
  function clearSummary() {
    if (currentPage) {
      setBox(rsSummary, '');
      savePageSummary(currentPage, '');
      console.log(`Cleared summary for page ${currentPage}`);
    }
  }
  
  // Copy summary to clipboard
  function copySummaryToClipboard() {
    const summaryText = getBoxText(rsSummary);
    if (!summaryText || !summaryText.trim()) {
      showToast('No summary content to copy', 'warning');
      return;
    }
    
    // Copy to clipboard
    navigator.clipboard.writeText(summaryText.trim()).then(() => {
      showToast('Summary copied to clipboard', 'success');
    }).catch(err => {
      console.error('Failed to copy:', err);
      showToast('Failed to copy summary', 'error');
    });
  }
  
  function saveCurrentExplain() {
    if (currentPage && getBoxText(rsExplain).trim()) {
      // Add term explanation to current page summary
      const currentSummary = getBoxText(rsSummary).trim();
      const termExplanation = getBoxText(rsExplain).trim();
      
      if (currentSummary) {
        const combinedSummary = currentSummary + '\n\n**Term:** ' + termExplanation;
        savePageSummary(currentPage, combinedSummary);
        setBox(rsSummary, combinedSummary);
      } else {
        savePageSummary(currentPage, '**Term:** ' + termExplanation);
        setBox(rsSummary, '**Term:** ' + termExplanation);
      }
      
      console.log(`Added term explanation to page ${currentPage} summary`);
    }
  }
  
  function clearExplain() {
    setBox(rsExplain, '');
    console.log('Cleared term explanation');
  }

  // ---- AI calls ----
  async function summarizeSelection(text){
    try {
      if (!text || !text.trim()) return;
      if (!window.pywebview || !window.pywebview.api) { return; }
      const res = await window.pywebview.api.summarize_selection(text.slice(0, 4000));
      if (res && res.ok) {
        // Summary is now handled in the right sidebar only
        console.log('Selection summarized:', res.summary);
      }
    } catch(e) {
      console.error('Error summarizing selection:', e);
    }
  }

  async function doDefine(word){
    try {
      console.log(`[doDefine] Called with word: "${word}"`);
      if (!word || !word.trim()) {
        console.log(`[doDefine] Empty word, aborting`);
        return;
      }
      
      setBox(rsExplain, 'Generating…');
      
      if (!window.pywebview || !window.pywebview.api) { 
        console.log(`[doDefine] ❌ Bridge unavailable`);
        setBox(rsExplain, 'Bridge unavailable.'); 
        return; 
      }
      
      console.log(`[doDefine] 📤 Calling API to define: "${word}"`);
      const res = await window.pywebview.api.define_term(word.slice(0, 120));
      console.log(`[doDefine] 📥 Got response:`, res);
      
      if (res && res.ok) {
        console.log(`[doDefine] ✅ Definition: "${res.definition}"`);
        setBox(rsExplain, res.definition.trim());
      } else {
        console.log(`[doDefine] ❌ Error:`, res?.error);
        setBox(rsExplain, (res && res.error) ? ('Error: ' + res.error) : 'Failed.');
        rsExplain.classList.remove('rsEmpty');
      }
    } catch(e){
      console.error(`[doDefine] 💥 Exception:`, e);
      setBox(rsExplain, 'Error: ' + e.message);
    }
  }

  let lastSummarizedPage = null;
  let pendingSummaryPage = null; // Track if a summary is currently being generated
  let currentPageText = ''; // Store current page text for manual triggers
  
  // Streaming buffer for Ask AI only
  let streamingAIBuffer = '';
  
  // Streaming handlers for Ask AI
  window.updateStreamingAI = function(chunk) {
    if (aiAnswer) {
      streamingAIBuffer += chunk;
      // Convert markdown to HTML as we stream
      let html = streamingAIBuffer
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        .replace(/\n/g, '<br>');
      aiAnswer.innerHTML = html;
      aiAnswer.classList.remove('rsEmpty');
      // Auto-scroll to bottom
      aiAnswer.scrollTop = aiAnswer.scrollHeight;
    }
  };
  
  window.finishStreamingAI = function() {
    if (aiAnswer) {
      // Mark as complete
      aiAnswer.classList.remove('rsEmpty');
      streamingAIBuffer = ''; // Reset buffer
    }
  };
  
  async function doPageSummary(page, text, mode='summarize', extraInstruction=''){
    try {
      console.log(`[doPageSummary] 🎯 Called for page ${page}, mode: ${mode}, text length: ${text?.length || 0}, extra instruction: "${extraInstruction}"`);
      
      if (!page) {
        console.log(`[doPageSummary] ❌ No page number, aborting`);
        return;
      }
      
      // If this page has library data, DON'T generate - use library data instead
      if (pagesWithLibraryData.has(page) && mode === 'auto') {
        console.log(`[doPageSummary] ⏭️ Page ${page} has library data - skipping AI generation`);
        // Library data already loaded in cache, just display it
        const cached = pageSummaryCache.get(page);
        if (cached && currentPage === page) {
            setBox(rsSummary, cached.summary);
          }
          return;
        }
        
      // For manual triggers (Explain mode or explicit summarize), always generate new
      if (mode === 'auto') {
        // Prevent duplicate generations: check if already processed OR currently generating
        if (lastSummarizedPage === page || pendingSummaryPage === page) {
          console.log(`[doPageSummary] ⏭️ Summary already generated or in progress for page ${page}`);
          return;
        }
      }
      
      // Mark this page as being processed
      pendingSummaryPage = page;
      // Show nice loading state for summary (similar to flashcards)
      const loadingText = mode === 'explain' ? 'Explaining…' : 'Generating summary…';
      rsSummary.innerHTML = `<div class="summaryEmptyState"><div class="summaryEmptyIcon">⏳</div><div class="summaryEmptyText">${loadingText}</div></div>`;
      rsSummary.classList.add('loading');
      rsSummary.classList.remove('rsEmpty');
      
      if (!window.pywebview || !window.pywebview.api) { 
        console.log(`[doPageSummary] ❌ Bridge unavailable`);
        pendingSummaryPage = null;
        setBox(rsSummary, 'Bridge unavailable.'); 
        return; 
      }
      
      const trimmed = text && text.length > 8000 ? text.slice(0, 8000) : (text || '');
      console.log(`[doPageSummary] 📤 Calling ${mode} API for page ${page} with ${trimmed.length} chars...`);
      
      const res = mode === 'explain' 
        ? await window.pywebview.api.explain_page(trimmed, page, extraInstruction || '')
        : await window.pywebview.api.summarize_page(trimmed, page, extraInstruction || '');
      
      console.log(`[doPageSummary] 📥 Got ${mode} API response for page ${page}:`, res);
      
      // Clear pending flag
      pendingSummaryPage = null;
      
      // Only update if still on this page (prevent race conditions)
      if (currentPage !== page) {
        console.log(`[doPageSummary] ⚠️ Page changed from ${page} to ${currentPage}, discarding result`);
        return;
      }
      
      if (res && res.ok) {
        const sum = res.summary.trim();
        console.log(`[doPageSummary] ✅ Summary generated for page ${page}, length: ${sum.length}`);
        console.log(`[doPageSummary] ✅ Summary content: "${sum}"`);
        
        // Display the summary in the UI
        setBox(rsSummary, sum);
        console.log(`[doPageSummary] ✅ Summary displayed in rsSummary element`);
        console.log(`[doPageSummary] ✅ rsSummary.innerHTML: "${rsSummary.innerHTML.substring(0, 100)}..."`);
        
        savePageSummary(page, sum);
        lastSummarizedPage = page;
      } else {
        console.log(`[doPageSummary] ❌ API error for page ${page}:`, res?.error);
        setBox(rsSummary, (res && res.error) ? ('Error: ' + res.error) : 'Failed.');
        rsSummary.classList.remove('rsEmpty');
      }
    } catch(e){
      console.error(`[doPageSummary] 💥 Exception for page ${page}:`, e);
      pendingSummaryPage = null; // Clear on error
      setBox(rsSummary, 'Error: ' + e.message);
    }
  }

  // ---- Receive messages from iframe ----
  window.addEventListener('message', (ev) => {
    const d = ev.data || {};
    
    if (d.type === 'define-term' && d.text) { 
      console.log(`[Wrapper] 🔤 Received define-term request for: "${d.text}"`);
      
      // Show term explainer widget for PDF selections
      if (d.highlight && termExplainerWidget) {
        showTermExplainerForPDFSelection(d.text, d.page);
      }
      
      doDefine(d.text); 
    }
    if (d.type === 'summarize-snippet' && d.text) { 
      console.log(`[Wrapper] 📄 Received summarize-snippet request (${d.text.length} chars)`);
      summarizeSelection(d.text); 
    }
    if (d.type === 'save-highlights' && d.highlights) {
      // Save highlights to PDF
      console.log(`[Wrapper] 🎨 Received ${d.highlights.length} highlights to save`);
      if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.save_highlights_to_pdf(d.highlights).then(result => {
          if (result && result.ok) {
            console.log(`✨ Saved ${result.count} highlights to PDF`);
          } else {
            console.error('Error saving highlights:', result?.error);
          }
        }).catch(err => {
          console.error('Error saving highlights:', err);
        });
      }
    }
    if (d.type === 'page-changed' && d.page) { 
      // Single source of truth - route through centralized update function
      updatePageUI(d.page);
    }
    if (d.type === 'page-text' && d.text) { 
      console.log(`[Wrapper] ✉️ Received page-text for page ${d.page}, text length: ${d.text.length}`);
      
      // Store current page text for manual Summarize/Explain buttons
      if (d.page === currentPage) {
      currentPageText = d.text;
      console.log(`[Wrapper] 💾 Stored current page text (${d.text.length} chars)`);
      }
      
      // Check if this page has library data - if yes, use it; if no, generate
      if (pagesWithLibraryData.has(d.page)) {
        // Page has library data - use it, don't generate
        console.log(`[Wrapper] ✓ Page ${d.page} has library data - loading from library`);
        updatePageUI(d.page);
      } else {
        // Page doesn't have library data - generate new (only if AI not disabled)
        console.log(`[Wrapper] 🚀 Page ${d.page} has no library data - checking AI flags`);
      if (d.page && !currentPage) currentPage = d.page;
        
        // Only generate summary if AI not disabled
        if (!disableSummaryAI) {
          doPageSummary(d.page, d.text, 'auto');
        } else {
          console.log(`[Wrapper] ⏭️ Summary AI disabled - skipping auto generation`);
        }
        
        // Only generate flashcards if AI not disabled
        if (!disableFlashcardsAI) {
          doGenerateFlashcards(d.page, d.text);
        } else {
          console.log(`[Wrapper] ⏭️ Flashcards AI disabled - skipping auto generation`);
        }
      }
    }
    if (d.type === 'page-text' && d.page && d.text) {
      // Store page text for Ask AI context
      documentPages[d.page] = d.text;
      console.log(`[Wrapper] 💾 Stored text for page ${d.page} in context`);
    }
    if (d.type === 'all-text' && d.pages) {
      // Update all pages from iframe cache
      documentPages = {...documentPages, ...d.pages};
      console.log(`Loaded ${Object.keys(d.pages).length} pages for context`);
    }
  });


  // ---- Helper to post messages to iframe ----
  function postToIframe(msg) {
    try {
      if (frame && frame.contentWindow) {
        frame.contentWindow.postMessage(msg, '*');
      }
    } catch(e) {
      console.error('Error posting to iframe:', e);
    }
  }

  // ---- Library Modal Functions ----
  // Format date opened with dynamic formatting
  function formatDateOpened(dateString) {
    if (!dateString) return 'Never opened';
    
    const now = new Date();
    const date = new Date(dateString);
    
    // Check if date is valid
    if (isNaN(date.getTime())) return 'Unknown';
    
    const diffMs = now - date;
    const diffSeconds = Math.floor(diffMs / 1000);
    const diffMinutes = Math.floor(diffSeconds / 60);
    const diffHours = Math.floor(diffMinutes / 60);
    const diffDays = Math.floor(diffHours / 24);
    
    // "now" for very recently opened (within 1 minute)
    if (diffSeconds < 60) {
      return 'now';
    }
    
    // "x hours ago" for lectures opened within a few hours (up to 12 hours)
    if (diffHours < 12 && diffHours > 0) {
      return `${diffHours} hour${diffHours === 1 ? '' : 's'} ago`;
    }
    
    // Check if it's today or yesterday
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);
    const dateOnly = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    
    // Format time as HH:MM
    const hours = date.getHours().toString().padStart(2, '0');
    const minutes = date.getMinutes().toString().padStart(2, '0');
    const timeStr = `${hours}:${minutes}`;
    
    if (dateOnly.getTime() === today.getTime()) {
      return `today ${timeStr}`;
    } else if (dateOnly.getTime() === yesterday.getTime()) {
      return `yesterday ${timeStr}`;
    }
    
    // For older entries, use standard date/time format
    const day = date.getDate().toString().padStart(2, '0');
    const month = (date.getMonth() + 1).toString().padStart(2, '0');
    const year = date.getFullYear();
    return `${day}/${month}/${year} ${timeStr}`;
  }
  
  async function openLibrary() {
    try {
      if (!window.pywebview || !window.pywebview.api) {
        alert('Library: bridge not available.');
        return;
      }
      
      const libraryModal = document.getElementById('libraryModal');
      const libraryContent = document.getElementById('libraryContent');
      
      // Show modal
      libraryModal.classList.add('show');
      libraryContent.innerHTML = '<div style="text-align: center; padding: 40px; color: #a0a0a2;">Loading library...</div>';

      // Wire header buttons
      const quizBtn = document.getElementById('openQuizzer');
      if (quizBtn) {
        quizBtn.textContent = 'Quiz';
        quizBtn.style.background = 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)';
        quizBtn.style.borderColor = '#667eea';
        quizBtn.style.color = '#fff';
      }
      const docsBtn = document.getElementById('openDocsFolderHeader');
      if (docsBtn) {
        docsBtn.onclick = async (e) => {
          e.stopPropagation();
          if (window.pywebview?.api?.open_docs_folder) {
            try {
              const res = await window.pywebview.api.open_docs_folder();
              if (!res?.ok) {
                showToast(res?.error || 'Could not open folder', 'error');
              }
            } catch(err) {
              showToast(err?.message || 'Could not open folder', 'error');
            }
          } else {
            showToast('Open folder not available', 'error');
          }
        };
      }
      
      // Add command+click hint to header
      const libraryHeaderTop = document.getElementById('libraryHeaderTop');
      if (libraryHeaderTop) {
        // Remove existing hint if any
        const existingHint = libraryHeaderTop.querySelector('.libraryClickHint');
        if (existingHint) {
          existingHint.remove();
        }
        const hint = document.createElement('div');
        hint.className = 'libraryClickHint';
        hint.innerHTML = '<span>⌘</span> + Click to view summaries & flashcards etc';
        libraryHeaderTop.appendChild(hint);
      }
      
      // Fetch library data
      const result = await window.pywebview.api.list_library_files();
      
      if (!result || !result.ok) {
        libraryContent.innerHTML = '<div class="libraryEmpty"><h2>Error</h2><p>Could not load library</p></div>';
        return;
      }
      
      const files = result.files || [];
      const folders = result.folders || {};
      const favorites = result.favorites || [];
      
      if (files.length === 0) {
        libraryContent.innerHTML = '<div class="libraryEmpty"><h2>Your library is empty</h2><p>Open some PDFs and create summaries to build your library!</p></div>';
        return;
      }
      
      // Default sort: by last_opened (latest first)
      let sortOrder = 'latest'; // 'latest' or 'oldest'
      
      // Sort files based on last_opened
      const sortedFiles = [...files].sort((a, b) => {
        const aFav = favorites.includes(a.name);
        const bFav = favorites.includes(b.name);
        
        // Favorites always first
        if (aFav && !bFav) return -1;
        if (!aFav && bFav) return 1;
        
        // Then sort by last_opened
        const aDate = a.last_opened ? new Date(a.last_opened).getTime() : 0;
        const bDate = b.last_opened ? new Date(b.last_opened).getTime() : 0;
        
        if (sortOrder === 'latest') {
          return bDate - aDate; // Latest first
        } else {
          return aDate - bDate; // Oldest first
        }
      });
      
      // Render library files
      libraryContent.innerHTML = '';
      
      // Render folders section
      if (Object.keys(folders).length > 0 || true) { // Always show folders section
        const foldersSection = document.createElement('div');
        foldersSection.className = 'libraryFoldersSection';
        
        const foldersHeader = document.createElement('div');
        foldersHeader.className = 'libraryFoldersHeader';
        
        const foldersTitle = document.createElement('h3');
        foldersTitle.className = 'libraryFoldersTitle';
        foldersTitle.textContent = 'Folders';
        
        const createFolderBtn = document.createElement('button');
        createFolderBtn.className = 'libraryCreateFolderBtn';
        createFolderBtn.textContent = '+ New Folder';
        createFolderBtn.onclick = async () => {
          const folderName = prompt('Enter folder name:');
          if (folderName && folderName.trim()) {
            try {
              const result = await window.pywebview.api.create_folder(folderName.trim());
              if (result && result.ok) {
                openLibrary(); // Refresh
              } else {
                alert('Error creating folder: ' + (result?.error || 'Unknown error'));
              }
            } catch(e) {
              alert('Error: ' + e.message);
            }
          }
        };
        
        foldersHeader.appendChild(foldersTitle);
        foldersHeader.appendChild(createFolderBtn);
        foldersSection.appendChild(foldersHeader);
        
        const foldersContainer = document.createElement('div');
        foldersContainer.className = 'libraryFoldersContainer';
        
        // Render existing folders
        for (const [folderName, folderFiles] of Object.entries(folders)) {
          const folderDiv = document.createElement('div');
          folderDiv.className = 'libraryFolder';
          folderDiv.dataset.folderName = folderName;
          
          const folderHeader = document.createElement('div');
          folderHeader.className = 'libraryFolderHeader';
          
          // Title container with edit icon
          const folderTitleContainer = document.createElement('div');
          folderTitleContainer.className = 'libraryTitleContainer';
          folderTitleContainer.style.cssText = 'display: inline-flex; align-items: center; gap: 6px; position: relative; flex: 1; min-width: 0;';
          
          const folderNameEl = document.createElement('h4');
          folderNameEl.className = 'libraryFolderName';
          folderNameEl.textContent = folderName;
          folderNameEl.setAttribute('data-foldername', folderName);
          
          // Edit icon (pencil) - appears on hover
          const folderEditIcon = document.createElement('button');
          folderEditIcon.className = 'libraryEditIcon';
          folderEditIcon.innerHTML = '✏️';
          folderEditIcon.style.cssText = 'background: transparent; border: none; font-size: 14px; opacity: 0; cursor: pointer; padding: 4px 6px; border-radius: 4px; transition: opacity 0.2s ease, background 0.2s ease; display: flex; align-items: center; justify-content: center; width: 24px; height: 24px;';
          folderEditIcon.title = 'Rename folder';
          
          // Show edit icon on folder hover
          folderDiv.addEventListener('mouseenter', function() {
            folderEditIcon.style.opacity = '0.6';
          });
          
          folderDiv.addEventListener('mouseleave', function() {
            if (!folderEditIcon.classList.contains('editing')) {
              folderEditIcon.style.opacity = '0';
            }
          });
          
          folderEditIcon.addEventListener('mouseenter', function() {
            this.style.opacity = '1';
            this.style.background = 'rgba(255, 255, 255, 0.1)';
          });
          
          folderEditIcon.addEventListener('mouseleave', function() {
            if (!this.classList.contains('editing')) {
              this.style.opacity = '0.6';
              this.style.background = 'transparent';
            }
          });
          
          // Click edit icon to rename
          let isRenamingFolder = false;
          folderEditIcon.addEventListener('click', async function(e) {
            e.stopPropagation();
            if (isRenamingFolder) return;
            
            isRenamingFolder = true;
            this.classList.add('editing');
            const originalName = folderNameEl.textContent;
            const foldername = folderNameEl.getAttribute('data-foldername');
            
            // Create editable input
            const input = document.createElement('input');
            input.type = 'text';
            input.value = originalName;
            input.className = 'libraryFileNameEdit';
            input.style.cssText = 'background: rgba(255, 255, 255, 0.1); border: 2px solid rgba(90, 159, 212, 0.6); border-radius: 6px; padding: 4px 12px; font-size: 16px; font-weight: 600; font-family: var(--font-family); color: rgba(255, 255, 255, 0.95); width: 100%; max-width: 400px; outline: none; flex: 1;';
            
            // Apply light theme if needed
            if (document.body.dataset.theme === 'light') {
              input.style.background = 'rgba(0, 0, 0, 0.05)';
              input.style.color = 'rgba(0, 0, 0, 0.95)';
              input.style.borderColor = 'rgba(90, 159, 212, 0.8)';
            }
            
            // Hide title and icon, show input
            folderNameEl.style.display = 'none';
            folderEditIcon.style.display = 'none';
            folderTitleContainer.insertBefore(input, folderNameEl);
            input.focus();
            input.select();
            
            const finishRename = async () => {
              const newName = input.value.trim();
              
              if (newName && newName !== originalName && newName.length > 0) {
                // Save new name
                try {
                  const result = await window.pywebview.api.rename_folder(foldername, newName);
                  if (result && result.ok) {
                    folderNameEl.textContent = newName;
                    folderNameEl.setAttribute('data-foldername', result.newFolderName || newName);
                    folderDiv.dataset.folderName = result.newFolderName || newName;
                  } else {
                    alert('Error renaming folder: ' + (result?.error || 'Unknown error'));
                    folderNameEl.textContent = originalName;
                  }
                } catch (e) {
                  alert('Error renaming folder: ' + e.message);
                  folderNameEl.textContent = originalName;
                }
              } else {
                folderNameEl.textContent = originalName;
              }
              
              input.remove();
              folderNameEl.style.display = '';
              folderEditIcon.style.display = '';
              folderEditIcon.classList.remove('editing');
              isRenamingFolder = false;
            };
            
            input.addEventListener('blur', finishRename);
            input.addEventListener('keydown', function(e) {
              if (e.key === 'Enter') {
                e.preventDefault();
                finishRename();
              } else if (e.key === 'Escape') {
                e.preventDefault();
                input.remove();
                folderNameEl.style.display = '';
                folderEditIcon.style.display = '';
                folderNameEl.textContent = originalName;
                folderEditIcon.classList.remove('editing');
                isRenamingFolder = false;
              }
            });
          });
          
          folderTitleContainer.appendChild(folderNameEl);
          folderTitleContainer.appendChild(folderEditIcon);
          
          const folderActions = document.createElement('div');
          folderActions.style.cssText = 'display: flex; gap: 8px;';
          
          const deleteFolderBtn = document.createElement('button');
          deleteFolderBtn.textContent = 'Delete';
          deleteFolderBtn.style.cssText = 'padding: 4px 8px; background: rgba(255, 59, 48, 0.15); border: 1px solid rgba(255, 59, 48, 0.3); color: rgba(255, 59, 48, 0.9); border-radius: 4px; cursor: pointer; font-size: 11px;';
          deleteFolderBtn.onclick = async (e) => {
            e.stopPropagation();
            if (confirm(`Delete folder "${folderName}"?`)) {
              try {
                const result = await window.pywebview.api.delete_folder(folderName);
                if (result && result.ok) {
                  openLibrary(); // Refresh
                } else {
                  alert('Error deleting folder: ' + (result?.error || 'Unknown error'));
                }
              } catch(e) {
                alert('Error: ' + e.message);
              }
            }
          };
          
          // Create search wrapper
          const searchWrapper = document.createElement('div');
          searchWrapper.className = 'folderSearchWrapper';
          
          const searchInput = document.createElement('input');
          searchInput.type = 'text';
          searchInput.className = 'folderSearchInput';
          searchInput.placeholder = 'Search...';
          
          const searchResults = document.createElement('div');
          searchResults.className = 'folderSearchResults';
          searchResults.style.display = 'none';
          
          const addButton = document.createElement('button');
          addButton.className = 'folderSearchAddButton';
          addButton.textContent = 'Add to folder';
          
          folderActions.appendChild(searchWrapper);
          folderActions.appendChild(deleteFolderBtn);
          folderHeader.appendChild(folderTitleContainer);
          folderHeader.appendChild(folderActions);
          
          searchWrapper.appendChild(searchInput);
          searchWrapper.appendChild(searchResults);
          searchWrapper.appendChild(addButton);
          
          const selectedLectures = new Set();
          
          // Initialize Fuse.js with all lectures
          let fuse = null;
          const initializeSearch = () => {
            const allLectures = files.map(f => ({ name: f.name }));
            fuse = new Fuse(allLectures, {
              keys: ['name'],
              threshold: 0.3,
              includeScore: true,
              includeMatches: true
            });
          };
          initializeSearch();
          
          // Search functionality
          searchInput.addEventListener('input', (e) => {
            const query = e.target.value.trim();
            
            if (query === '') {
              searchResults.style.display = 'none';
              addButton.classList.remove('show');
              selectedLectures.clear();
              return;
            }
            
            const results = fuse.search(query);
            searchResults.innerHTML = '';
            
            if (results.length === 0) {
              searchResults.style.display = 'block';
              const empty = document.createElement('div');
              empty.className = 'folderSearchEmpty';
              empty.textContent = 'No lectures found';
              searchResults.appendChild(empty);
              addButton.classList.remove('show');
              return;
            }
            
            searchResults.style.display = 'block';
            
            results.forEach(result => {
              const item = result.item;
              const matches = result.matches[0];
              
              // Skip if already in folder
              if (folderFiles.includes(item.name)) {
                return;
              }
              
              const resultItem = document.createElement('div');
              resultItem.className = 'folderSearchResultItem';
              
              const checkbox = document.createElement('input');
              checkbox.type = 'checkbox';
              checkbox.className = 'folderSearchResultCheckbox';
              checkbox.dataset.fileName = item.name;
              
              const nameSpan = document.createElement('span');
              nameSpan.className = 'folderSearchResultName';
              
              // Highlight matched text - process from end to start to avoid index shifting
              let highlightedName = item.name;
              if (matches && matches.indices && matches.indices.length > 0) {
                // Sort indices by start position, then process from end to start
                const sortedIndices = [...matches.indices].sort((a, b) => a[0] - b[0]);
                // Process in reverse order to avoid index shifting
                for (let i = sortedIndices.length - 1; i >= 0; i--) {
                  const [start, end] = sortedIndices[i];
                  const before = highlightedName.substring(0, start);
                  const match = highlightedName.substring(start, end + 1);
                  const after = highlightedName.substring(end + 1);
                  highlightedName = before + '<span class="highlight">' + match + '</span>' + after;
                }
              }
              nameSpan.innerHTML = highlightedName;
              
              checkbox.addEventListener('change', (e) => {
                if (e.target.checked) {
                  selectedLectures.add(item.name);
                } else {
                  selectedLectures.delete(item.name);
                }
                if (selectedLectures.size > 0) {
                  addButton.classList.add('show');
                  addButton.textContent = `Add ${selectedLectures.size}`;
                } else {
                  addButton.classList.remove('show');
                }
              });
              
              resultItem.appendChild(checkbox);
              resultItem.appendChild(nameSpan);
              searchResults.appendChild(resultItem);
            });
          });
          
          // Add to folder button
          addButton.addEventListener('click', async () => {
            if (selectedLectures.size === 0) return;
            
            const lecturesToAdd = Array.from(selectedLectures);
            let successCount = 0;
            
            for (const fileName of lecturesToAdd) {
              try {
                const result = await window.pywebview.api.move_to_folder(fileName, folderName);
                if (result && result.ok) {
                  successCount++;
                }
              } catch(e) {
                console.error('Error moving file:', e);
              }
            }
            
            if (successCount > 0) {
              searchInput.value = '';
              searchResults.style.display = 'none';
              searchResults.innerHTML = '';
              selectedLectures.clear();
              addButton.classList.remove('show');
              openLibrary(); // Refresh
            }
          });
          
          folderDiv.appendChild(folderHeader);
          
          const folderItems = document.createElement('div');
          folderItems.className = 'libraryFolderItems';
          
          if (folderFiles && folderFiles.length > 0) {
            for (const fileName of folderFiles) {
              const fileInFolder = files.find(f => f.name === fileName);
              if (fileInFolder) {
                const item = document.createElement('div');
                const isLightMode = document.body.dataset.theme === 'light' || document.body.classList.contains('light-mode');
                item.style.cssText = `padding: 8px 12px; background: ${isLightMode ? 'rgba(0,0,0,0.03)' : 'rgba(255,255,255,0.02)'}; border-radius: 6px; font-size: 13px; color: ${isLightMode ? 'rgba(0,0,0,0.75)' : 'rgba(255,255,255,0.8)'}; cursor: pointer; transition: all 0.2s ease;`;
                item.textContent = fileInFolder.name;
                item.onmouseenter = () => {
                  item.style.background = isLightMode ? 'rgba(0,0,0,0.06)' : 'rgba(255,255,255,0.05)';
                  item.style.color = isLightMode ? 'rgba(0,0,0,0.9)' : 'rgba(255,255,255,0.95)';
                };
                item.onmouseleave = () => {
                  item.style.background = isLightMode ? 'rgba(0,0,0,0.03)' : 'rgba(255,255,255,0.02)';
                  item.style.color = isLightMode ? 'rgba(0,0,0,0.75)' : 'rgba(255,255,255,0.8)';
                };
                item.onclick = async (e) => {
                  e.stopPropagation();
                  
                  // Check if in quizzer mode - if so, toggle checkbox instead
                  const libraryModal = document.getElementById('libraryModal');
                  if (libraryModal && libraryModal.classList.contains('quizzer-mode')) {
                    const checkbox = item.querySelector('.libraryFileQuizzerCheckbox');
                    if (checkbox) {
                      checkbox.checked = !checkbox.checked;
                      checkbox.dispatchEvent(new Event('change'));
                    }
                    return;
                  }
                  
                  // Check for command/meta key (Mac) or ctrl key (Windows/Linux)
                  const isCommandClick = e.metaKey || e.ctrlKey;
                  
                  if (isCommandClick) {
                    // Command+click: expand/collapse to view details
                    // Create a temporary modal to show details
                    const tempModal = document.createElement('div');
                    tempModal.style.cssText = 'position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0, 0, 0, 0.7); z-index: 10002; display: flex; align-items: center; justify-content: center;';
                    
                    const tempContent = document.createElement('div');
                    tempContent.style.cssText = 'background: rgba(30, 30, 30, 0.98); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 16px; width: 90%; max-width: 800px; max-height: 85vh; overflow-y: auto; padding: 24px; box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);';
                    
                    const header = document.createElement('div');
                    header.style.cssText = 'display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; padding-bottom: 0;';
                    
                    const title = document.createElement('h2');
                    title.textContent = fileInFolder.name;
                    title.style.cssText = 'margin: 0; font-size: 20px; font-weight: 600; color: rgba(255, 255, 255, 0.95);';
                    
                    const closeBtn = document.createElement('button');
                    closeBtn.textContent = '×';
                    closeBtn.style.cssText = 'background: transparent; border: 1px solid rgba(255,255,255,0.1); color: rgba(255,255,255,0.7); padding: 8px 16px; border-radius: 8px; cursor: pointer; font-size: 18px; line-height: 1; width: 36px; height: 36px; display: flex; align-items: center; justify-content: center;';
                    closeBtn.onclick = () => tempModal.remove();
                    
                    const content = document.createElement('div');
                    content.className = 'libraryFileContent';
                    content.style.cssText = 'max-height: none; margin-top: 0; opacity: 1;';
                    
                    // Load detailed data
                    const detailResult = await window.pywebview.api.load_library_data(fileName);
                    
                    if (detailResult && detailResult.ok) {
                      renderLibraryFileDetails(content, detailResult.data, fileName);
                      
                      header.appendChild(title);
                      header.appendChild(closeBtn);
                      tempContent.appendChild(header);
                      tempContent.appendChild(content);
                      tempModal.appendChild(tempContent);
                      document.body.appendChild(tempModal);
                      
                      // Remove on escape key or click outside
                      const removeModal = (e) => {
                        if (e.key === 'Escape' || (e.target === tempModal && e.target !== tempContent)) {
                          tempModal.remove();
                          document.removeEventListener('keydown', removeModal);
                          tempModal.removeEventListener('click', removeModal);
                        }
                      };
                      document.addEventListener('keydown', removeModal);
                      setTimeout(() => tempModal.addEventListener('click', removeModal), 100);
                    } else {
                      alert('Error loading file details: ' + (detailResult?.error || 'Unknown error'));
                      tempModal.remove();
                    }
                  } else {
                    // Regular click: open file
                    try {
                      const result = await window.pywebview.api.open_library_file(fileName);
                      if (result && result.ok) {
                        // File will be loaded (fromLibrary parameter will be added by Python)
                        document.getElementById('libraryModal').classList.remove('show');
                      } else {
                        alert('Error opening file: ' + (result?.error || 'Unknown error'));
                      }
                    } catch(e) {
                      alert('Error: ' + e.message);
                    }
                  }
                };
                folderItems.appendChild(item);
              }
            }
          } else {
            const empty = document.createElement('div');
            empty.className = 'libraryFolderEmpty';
            empty.textContent = 'Drag lectures here';
            folderItems.appendChild(empty);
          }
          
          // Make folder a drop target
          folderDiv.addEventListener('dragover', (e) => {
            e.preventDefault();
            folderDiv.classList.add('drag-over');
          });
          
          folderDiv.addEventListener('dragleave', () => {
            folderDiv.classList.remove('drag-over');
          });
          
          folderDiv.addEventListener('drop', async (e) => {
            e.preventDefault();
            folderDiv.classList.remove('drag-over');
            const fileName = e.dataTransfer.getData('text/plain');
            if (fileName) {
              try {
                const result = await window.pywebview.api.move_to_folder(fileName, folderName);
                if (result && result.ok) {
                  openLibrary(); // Refresh
                } else {
                  alert('Error moving file: ' + (result?.error || 'Unknown error'));
                }
              } catch(e) {
                alert('Error: ' + e.message);
              }
            }
          });
          
          folderDiv.appendChild(folderHeader);
          folderDiv.appendChild(folderItems);
          foldersContainer.appendChild(folderDiv);
        }
        
        foldersSection.appendChild(foldersContainer);
        libraryContent.appendChild(foldersSection);
      }
      
      // Render lectures section
      const lecturesSection = document.createElement('div');
      lecturesSection.className = 'libraryFoldersSection';
      
      const lecturesHeader = document.createElement('div');
      lecturesHeader.className = 'libraryFoldersHeader';
      
      const lecturesTitle = document.createElement('h3');
      lecturesTitle.className = 'libraryFoldersTitle';
      lecturesTitle.textContent = 'Lectures';
      
      // Add sorting dropdown
      const sortContainer = document.createElement('div');
      sortContainer.style.cssText = 'display: flex; align-items: center; gap: 8px; margin-left: auto;';
      
      const sortLabel = document.createElement('span');
      sortLabel.textContent = 'Sort by:';
      sortLabel.style.cssText = 'font-size: 13px; color: rgba(255, 255, 255, 0.6);';
      
      const sortDropdown = document.createElement('div');
      sortDropdown.style.cssText = 'position: relative; display: inline-block;';
      
      const sortButton = document.createElement('button');
      sortButton.style.cssText = 'display: flex; align-items: center; gap: 6px; padding: 6px 12px; background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 6px; color: rgba(255, 255, 255, 0.9); font-size: 13px; cursor: pointer; font-family: var(--font-family); transition: background 0.1s ease, border-color 0.1s ease;';
      
      const sortText = document.createElement('span');
      sortText.textContent = 'Latest opened';
      sortText.id = 'librarySortText';
      
      const sortIcon = document.createElement('span');
      sortIcon.innerHTML = '↓';
      sortIcon.id = 'librarySortIcon';
      sortIcon.style.cssText = 'font-size: 12px; transition: transform 0.15s ease; display: inline-block;';
      
      sortButton.appendChild(sortText);
      sortButton.appendChild(sortIcon);
      
      // Apply light theme styles if needed
      const isLightMode = document.body.dataset.theme === 'light' || document.body.classList.contains('light-mode');
      if (isLightMode) {
        sortLabel.style.color = 'rgba(0, 0, 0, 0.6)';
        sortButton.style.background = 'rgba(0, 0, 0, 0.03)';
        sortButton.style.borderColor = 'rgba(0, 0, 0, 0.1)';
        sortButton.style.color = 'rgba(0, 0, 0, 0.9)';
      }
      
      sortButton.onmouseenter = () => {
        sortButton.style.background = isLightMode ? 'rgba(0, 0, 0, 0.06)' : 'rgba(255, 255, 255, 0.08)';
        sortButton.style.borderColor = isLightMode ? 'rgba(0, 0, 0, 0.15)' : 'rgba(255, 255, 255, 0.15)';
      };
      sortButton.onmouseleave = () => {
        sortButton.style.background = isLightMode ? 'rgba(0, 0, 0, 0.03)' : 'rgba(255, 255, 255, 0.05)';
        sortButton.style.borderColor = isLightMode ? 'rgba(0, 0, 0, 0.1)' : 'rgba(255, 255, 255, 0.1)';
      };
      
      sortButton.onclick = (() => {
        let currentSortOrder = sortOrder;
        return () => {
          // Toggle sort order
          currentSortOrder = currentSortOrder === 'latest' ? 'oldest' : 'latest';
          
          // Update button text and icon
          sortText.textContent = currentSortOrder === 'latest' ? 'Latest opened' : 'Oldest opened';
          sortIcon.style.transform = currentSortOrder === 'latest' ? 'rotate(0deg)' : 'rotate(180deg)';
          
          // Re-sort files
          const reSortedFiles = [...files].sort((a, b) => {
            const aFav = favorites.includes(a.name);
            const bFav = favorites.includes(b.name);
            if (aFav && !bFav) return -1;
            if (!aFav && bFav) return 1;
            const aDate = a.last_opened ? new Date(a.last_opened).getTime() : 0;
            const bDate = b.last_opened ? new Date(b.last_opened).getTime() : 0;
            if (currentSortOrder === 'latest') {
              return bDate - aDate;
            } else {
              return aDate - bDate;
            }
          });
          
          // Re-render lectures
          const lecturesContainer = document.querySelector('.libraryLecturesContainer');
          if (lecturesContainer) {
            // Save scroll position
            const libraryContent = document.getElementById('libraryContent');
            const scrollPosition = libraryContent ? libraryContent.scrollTop : 0;
            
            // Get all file divs before removing
            const fileDivs = Array.from(lecturesContainer.querySelectorAll('.libraryFile'));
            
            // Create map of file names to divs
            const fileDivMap = new Map();
            fileDivs.forEach(div => {
              const fileName = div.querySelector('.libraryFileName')?.textContent;
              if (fileName) fileDivMap.set(fileName, div);
            });
            
            // Remove all file divs
            fileDivs.forEach(div => div.remove());
            
            // Re-add in new order
            reSortedFiles.forEach(file => {
              const fileDiv = fileDivMap.get(file.name);
              if (fileDiv) {
                lecturesContainer.appendChild(fileDiv);
              }
            });
            
            // Restore scroll position
            if (libraryContent) {
              libraryContent.scrollTop = scrollPosition;
            }
          }
        };
      })();
      
      sortDropdown.appendChild(sortButton);
      sortContainer.appendChild(sortLabel);
      sortContainer.appendChild(sortDropdown);
      
      lecturesHeader.appendChild(lecturesTitle);
      lecturesHeader.appendChild(sortContainer);
      lecturesSection.appendChild(lecturesHeader);
      
      const lecturesContainer = document.createElement('div');
      lecturesContainer.className = 'libraryLecturesContainer';
      
      for (const file of sortedFiles) {
        const fileDiv = document.createElement('div');
        fileDiv.className = 'libraryFile';
        
        const header = document.createElement('div');
        header.className = 'libraryFileHeader';
        
        // Title container with edit icon
        const titleContainer = document.createElement('div');
        titleContainer.className = 'libraryTitleContainer';
        
        const fileNameElement = document.createElement('h2');
        fileNameElement.className = 'libraryFileName';
        fileNameElement.textContent = file.name;
        fileNameElement.setAttribute('data-filename', file.name);
        
        // Edit icon (pencil) - appears on hover
        const editIcon = document.createElement('button');
        editIcon.className = 'libraryEditIcon';
        editIcon.innerHTML = '✏️';
        editIcon.style.cssText = 'background: transparent; border: none; font-size: 14px; opacity: 0; cursor: pointer; padding: 4px 6px; border-radius: 4px; transition: opacity 0.2s ease, background 0.2s ease; display: flex; align-items: center; justify-content: center; width: 24px; height: 24px;';
        editIcon.title = 'Rename lecture';
        
        // Favorite heart icon - appears on hover next to edit button
        const favoriteBtn = document.createElement('button');
        favoriteBtn.className = 'libraryFavorite';
        favoriteBtn.innerHTML = '♡';
        favoriteBtn.title = 'Add to favorites';
        if (favorites.includes(file.name)) {
          favoriteBtn.classList.add('favorited');
          favoriteBtn.innerHTML = '♥';
          favoriteBtn.title = 'Remove from favorites';
        }
        favoriteBtn.onclick = async (e) => {
          e.stopPropagation();
          const isFavorited = favoriteBtn.classList.contains('favorited');
          try {
            const result = await window.pywebview.api.toggle_favorite(file.name, !isFavorited);
            if (result && result.ok) {
              // Update button state
              if (isFavorited) {
                favoriteBtn.classList.remove('favorited');
                favoriteBtn.innerHTML = '♡';
                favoriteBtn.title = 'Add to favorites';
              } else {
                favoriteBtn.classList.add('favorited');
                favoriteBtn.innerHTML = '♥';
                favoriteBtn.title = 'Remove from favorites';
              }
              
              // Save scroll position
              const libraryContent = document.getElementById('libraryContent');
              const scrollPosition = libraryContent ? libraryContent.scrollTop : 0;
              
              // Re-sort files by moving DOM elements without re-rendering
              // Find the lectures container (parent of fileDiv)
              const lecturesContainer = fileDiv.parentElement;
              if (lecturesContainer && lecturesContainer.querySelectorAll) {
                const allFiles = Array.from(lecturesContainer.querySelectorAll('.libraryFile'));
                const listResult = await window.pywebview.api.list_library_files();
                const favoritesList = listResult && listResult.ok ? (listResult.favorites || []) : [];
                
                // Sort files: favorites first, then by name
                allFiles.sort((a, b) => {
                  const aName = a.querySelector('.libraryFileName')?.textContent || '';
                  const bName = b.querySelector('.libraryFileName')?.textContent || '';
                  const aFav = favoritesList.includes(aName);
                  const bFav = favoritesList.includes(bName);
                  if (aFav && !bFav) return -1;
                  if (!aFav && bFav) return 1;
                  return aName.localeCompare(bName);
                });
                
                // Re-append in sorted order (this preserves scroll position better)
                allFiles.forEach(f => lecturesContainer.appendChild(f));
                
                // Restore scroll position immediately without animation
                if (libraryContent) {
                  libraryContent.scrollTop = scrollPosition;
                }
              }
            } else {
              alert('Error: ' + (result?.error || 'Unknown error'));
            }
          } catch(e) {
            alert('Error: ' + e.message);
          }
        };
        
        // Buttons show on hover anywhere on the file (handled by CSS)
        // Individual hover effects for buttons
        editIcon.addEventListener('mouseenter', function() {
          this.style.opacity = '1';
          this.style.background = 'rgba(255, 255, 255, 0.1)';
        });
        
        editIcon.addEventListener('mouseleave', function() {
          if (!this.classList.contains('editing')) {
            this.style.opacity = '0.6';
            this.style.background = 'transparent';
          }
        });
        
        favoriteBtn.addEventListener('mouseenter', function() {
          this.style.opacity = '1';
          this.style.background = 'rgba(255, 255, 255, 0.1)';
        });
        
        favoriteBtn.addEventListener('mouseleave', function() {
          if (!this.classList.contains('favorited')) {
            this.style.opacity = '0.6';
            this.style.background = 'transparent';
          }
        });
        
        // Click edit icon to rename
        let isRenaming = false;
        editIcon.addEventListener('click', async function(e) {
          e.stopPropagation();
          if (isRenaming) return;
          
          isRenaming = true;
          this.classList.add('editing');
          const originalName = fileNameElement.textContent;
          const filename = fileNameElement.getAttribute('data-filename');
          
          // Create editable input
          const input = document.createElement('input');
          input.type = 'text';
          input.value = originalName;
          input.className = 'libraryFileNameEdit';
          input.style.cssText = 'background: rgba(255, 255, 255, 0.1); border: 2px solid rgba(90, 159, 212, 0.6); border-radius: 6px; padding: 4px 12px; font-size: 18px; font-weight: 600; font-family: var(--font-family); color: rgba(255, 255, 255, 0.95); width: 100%; max-width: 400px; outline: none; flex: 1;';
          
          // Apply light theme if needed
          if (document.body.dataset.theme === 'light') {
            input.style.background = 'rgba(0, 0, 0, 0.05)';
            input.style.color = 'rgba(0, 0, 0, 0.95)';
            input.style.borderColor = 'rgba(90, 159, 212, 0.8)';
          }
          
          // Hide title and icon, show input
          fileNameElement.style.display = 'none';
          editIcon.style.display = 'none';
          titleContainer.insertBefore(input, fileNameElement);
          input.focus();
          input.select();
          
          const finishRename = async () => {
            const newName = input.value.trim();
            
            if (newName && newName !== originalName && newName.length > 0) {
              // Save new name
              try {
                const result = await window.pywebview.api.rename_library_file(filename, newName);
                if (result && result.ok) {
                  const nextName = result.newFilename || newName;
                  fileNameElement.textContent = nextName;
                  fileNameElement.setAttribute('data-filename', nextName);
                  if (file && file.name) file.name = nextName;
                  const payload = { oldName: filename, newName: nextName };
                  window.dispatchEvent(new CustomEvent('akson-library-renamed', { detail: payload }));
                  try { window.parent?.postMessage({ type: 'akson-library-renamed', ...payload }, '*'); } catch(e){}
                  if (typeof showToast === 'function') showToast(`Renamed to ${nextName}`, 'success');
                } else {
                  alert('Error renaming: ' + (result?.error || 'Unknown error'));
                  fileNameElement.textContent = originalName;
                }
              } catch (e) {
                alert('Error renaming: ' + e.message);
                fileNameElement.textContent = originalName;
              }
            } else {
              fileNameElement.textContent = originalName;
            }
            
            input.remove();
            fileNameElement.style.display = '';
            editIcon.style.display = '';
            editIcon.classList.remove('editing');
            isRenaming = false;
          };
          
          input.addEventListener('blur', finishRename);
          input.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
              e.preventDefault();
              finishRename();
            } else if (e.key === 'Escape') {
              e.preventDefault();
              input.remove();
              fileNameElement.style.display = '';
              editIcon.style.display = '';
              fileNameElement.textContent = originalName;
              editIcon.classList.remove('editing');
              isRenaming = false;
            }
          });
        });
        
        titleContainer.appendChild(fileNameElement);
        // Switch positions: favorite first, then edit
        titleContainer.appendChild(favoriteBtn);
        titleContainer.appendChild(editIcon);
        header.appendChild(titleContainer);
        
        // Stats and date container
        const statsAndDateDiv = document.createElement('div');
        statsAndDateDiv.style.cssText = 'display: flex; flex-direction: column; gap: 6px; align-items: flex-end;';
        
        const statsDiv = document.createElement('div');
        statsDiv.className = 'libraryFileStats';
        statsDiv.innerHTML = `
            <span>${file.summaries} summaries</span>
            <span>${file.flashcards} flashcards</span>
        `;
        
        const dateDiv = document.createElement('div');
        dateDiv.className = 'libraryFileDate';
        dateDiv.textContent = formatDateOpened(file.last_opened);
        dateDiv.style.cssText = 'font-size: 12px; color: rgba(255, 255, 255, 0.5); font-weight: 400;';
        if (isLightMode) {
          dateDiv.style.color = 'rgba(0, 0, 0, 0.5)';
        }
        
        statsAndDateDiv.appendChild(statsDiv);
        statsAndDateDiv.appendChild(dateDiv);
        header.appendChild(statsAndDateDiv);
        
        const content = document.createElement('div');
        content.className = 'libraryFileContent';
        content.innerHTML = '<div style="text-align: center; padding: 20px; color: #a0a0a2;">Loading...</div>';
        
        fileDiv.appendChild(header);
        fileDiv.appendChild(content);
        
        // Make file draggable
        fileDiv.draggable = true;
        fileDiv.addEventListener('dragstart', (e) => {
          e.dataTransfer.setData('text/plain', file.name);
          fileDiv.classList.add('dragging');
          
          // Create custom drag image to prevent transparency
          const dragImage = document.createElement('div');
          dragImage.style.cssText = `
            position: absolute;
            top: -1000px;
            left: -1000px;
            width: ${fileDiv.offsetWidth}px;
            height: ${fileDiv.offsetHeight}px;
            background: rgba(30, 30, 30, 1);
            border: 1px solid rgba(90, 159, 212, 0.4);
            border-radius: 14px;
            padding: 24px 28px;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
            opacity: 0.95;
            pointer-events: none;
          `;
          dragImage.innerHTML = fileDiv.innerHTML;
          document.body.appendChild(dragImage);
          
          // Set custom drag image
          e.dataTransfer.setDragImage(dragImage, e.offsetX, e.offsetY);
          
          // Remove after a short delay
          setTimeout(() => {
            document.body.removeChild(dragImage);
          }, 0);
        });
        
        fileDiv.addEventListener('dragend', () => {
          fileDiv.classList.remove('dragging');
        });
        
        // Click handler: regular click opens file, command+click expands (unless in quizzer mode)
        fileDiv.onclick = async (e) => {
          // Don't expand if clicking on buttons or inside content area
          if (e.target.closest('.libraryFileActions') || e.target.closest('.libraryFileContent')) {
            return;
          }
          
          // Don't open if clicking edit or favorite buttons
          if (e.target.closest('.libraryEditIcon') || e.target.closest('.libraryFavorite')) {
            return;
          }
          
          // Check if in quizzer mode - if so, toggle checkbox instead
          const libraryModal = document.getElementById('libraryModal');
          if (libraryModal && libraryModal.classList.contains('quizzer-mode')) {
            e.preventDefault();
            e.stopPropagation();
            const checkbox = fileDiv.querySelector('.libraryFileQuizzerCheckbox');
            if (checkbox) {
              checkbox.checked = !checkbox.checked;
              checkbox.dispatchEvent(new Event('change'));
            }
            return;
          }
          
          // Check for command/meta key (Mac) or ctrl key (Windows/Linux)
          const isCommandClick = e.metaKey || e.ctrlKey;
          
          if (isCommandClick) {
            // Command+click: expand/collapse
            e.preventDefault();
            e.stopPropagation();
          
          const wasExpanded = fileDiv.classList.contains('expanded');
          
          // Collapse all
          document.querySelectorAll('.libraryFile').forEach(f => f.classList.remove('expanded'));
          
          if (!wasExpanded) {
            fileDiv.classList.add('expanded');
            
            // Load detailed data
            const detailResult = await window.pywebview.api.load_library_data(file.name);
            
            if (detailResult && detailResult.ok) {
              renderLibraryFileDetails(content, detailResult.data, file.name);
            } else {
              content.innerHTML = '<div style="padding: 20px; color: #d44;">Error loading file details</div>';
              }
            }
          } else {
            // Regular click: open file
            e.preventDefault();
            e.stopPropagation();
            
            try {
              const result = await window.pywebview.api.open_library_file(file.name);
              if (result && result.ok) {
                // File will be loaded (fromLibrary parameter will be added by Python)
                document.getElementById('libraryModal').classList.remove('show');
              } else {
                alert('Error opening file: ' + (result?.error || 'Unknown error'));
              }
            } catch(e) {
              alert('Error: ' + e.message);
            }
          }
        };
        
        lecturesContainer.appendChild(fileDiv);
      }
      
      lecturesSection.appendChild(lecturesContainer);
      libraryContent.appendChild(lecturesSection);
      
    } catch(e) {
      console.error('Error opening library:', e);
      alert('Error opening library: ' + e.message);
    }
  }
  
  function renderLibraryFileDetails(container, data, fileName) {
    container.innerHTML = '';

    // Actions
    const actionsDiv = document.createElement('div');
    actionsDiv.className = 'libraryFileActions';
    actionsDiv.innerHTML = `
      <button class="viewFullFlashcards">Flashcards</button>
      <button class="createMindmap" style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);">Mindmap</button>
      <button class="downloadSummary">Download</button>
      <button class="downloadFlashcards danger">Delete</button>
      <button class="viewFullSummary" style="margin-left: auto; background: rgba(255,255,255,0.08); border-color: rgba(255,255,255,0.15);">⛶ Fullscreen</button>
    `;
    actionsDiv.querySelector('.viewFullSummary').onclick = () => openFullSummaryView(fileName, data);
    actionsDiv.querySelector('.viewFullFlashcards').onclick = () => {
      // Collect all flashcards from all pages
      const allCards = [];
      if (data.flashcards) {
        const pages = Object.keys(data.flashcards).map(Number).sort((a,b)=>a-b);
        for (const page of pages) {
          const cards = data.flashcards[page];
          if (Array.isArray(cards) && cards.length > 0) {
            allCards.push(...cards);
          }
        }
      }
      if (allCards.length === 0) {
        alert('No flashcards available for this file');
        return;
      }
      openInteractiveFlashcardViewer(allCards, `${fileName} - Flashcards`, null);
    };
    actionsDiv.querySelector('.createMindmap').onclick = () => openMindmapModal(fileName, data);
    actionsDiv.querySelector('.downloadSummary').onclick = () => downloadSummary(fileName, data);
    actionsDiv.querySelector('.downloadFlashcards').onclick = async () => {
      if (confirm(`Are you sure you want to permanently delete "${fileName}" and all its summaries and flashcards?`)) {
        try {
          const result = await window.pywebview.api.delete_library_file(fileName);
          if (result && result.ok) {
            alert('Lecture deleted successfully');
            openLibrary(); // Refresh library view
          } else {
            alert('Error deleting lecture: ' + (result?.error || 'Unknown error'));
          }
        } catch (e) {
          alert('Error deleting lecture: ' + e.message);
        }
      }
    };
    container.appendChild(actionsDiv);

    // Render summaries
    if (data.summaries && Object.keys(data.summaries).length > 0) {
      const summarySection = document.createElement('div');
      summarySection.className = 'librarySection';
      summarySection.innerHTML = '<h3>Page Summaries</h3>';
      
      const pages = Object.keys(data.summaries).map(Number).sort((a, b) => a - b);
      
      for (const page of pages) {
        const summary = data.summaries[page];
        const item = document.createElement('div');
        item.className = 'librarySummaryItem';
        const summaryText = typeof summary === 'string' ? summary : (summary.summary || '');
        item.innerHTML = `
          <strong>Page ${page}</strong>
          <p>${escapeHtml(summaryText).replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')}</p>
        `;
        summarySection.appendChild(item);
      }
      
      container.appendChild(summarySection);
    }
    
    // Render flashcards
    if (data.flashcards && Object.keys(data.flashcards).length > 0) {
      const flashcardSection = document.createElement('div');
      flashcardSection.className = 'librarySection';
      flashcardSection.innerHTML = '<h3>Flashcards</h3>';
      
      const pages = Object.keys(data.flashcards).map(Number).sort((a, b) => a - b);
      
      for (const page of pages) {
        const cards = data.flashcards[page];
        if (!Array.isArray(cards) || cards.length === 0) continue;
        
        const pageHeader = document.createElement('div');
        pageHeader.style.cssText = 'font-size: 13px; color: #a0a0a2; margin: 12px 0 8px 0; font-weight: 600;';
        pageHeader.textContent = `Page ${page}`;
        flashcardSection.appendChild(pageHeader);
        
        for (const card of cards) {
          const item = document.createElement('div');
          item.className = 'libraryFlashcard';
          item.innerHTML = `
            <span class="q">Q: ${escapeHtml(card.q || '')}</span>
            <span class="a">A: ${escapeHtml(card.a || '')}</span>
          `;
          flashcardSection.appendChild(item);
        }
      }
      
      container.appendChild(flashcardSection);
    }
    
    if (container.children.length === 0) {
      container.innerHTML = '<div style="padding: 20px; text-align: center; color: #a0a0a2;">No data available</div>';
    }
  }
  
  let isEditMode = false;
  let fullScreenData = null;
  let fullScreenFileName = null;
  
  // Full view functions
  function openFullSummaryView(fileName, data) {
    isEditMode = false;
    fullScreenData = data;
    fullScreenFileName = fileName;
    
    const content = document.getElementById('libraryContent');
    content.innerHTML = '';
    const wrap = document.createElement('div');
    wrap.style.cssText = 'max-width: 900px; margin: 0 auto;';
    
    // Header with back and edit button
    const header = document.createElement('div');
    header.style.cssText = 'display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px;';
    
    const backBtn = document.createElement('button');
    backBtn.textContent = '← Back to Library';
    backBtn.className = 'fullSummaryBackBtn';
    backBtn.onclick = openLibrary;
    
    const editBtn = document.createElement('button');
    editBtn.id = 'editSummaryModeBtn';
    editBtn.textContent = '✏️ Edit';
    editBtn.className = 'fullSummaryEditBtn';
    editBtn.onclick = toggleEditMode;
    
    header.appendChild(backBtn);
    header.appendChild(editBtn);
    
    const title = document.createElement('h2');
    title.className = 'fullSummaryTitle';
    title.textContent = `${fileName} - Complete Summary`;
    
    const summaryContent = document.createElement('div');
    summaryContent.id = 'fullSummaryContent';
    
    wrap.appendChild(header);
    wrap.appendChild(title);
    wrap.appendChild(summaryContent);
    content.appendChild(wrap);

    renderFullSummaryContent(summaryContent, data);
  }
  
  function toggleEditMode() {
    isEditMode = !isEditMode;
    const editBtn = document.getElementById('editSummaryModeBtn');
    const summaryContent = document.getElementById('fullSummaryContent');
    
    if (isEditMode) {
      editBtn.textContent = '✓ Done';
      editBtn.classList.add('done');
    } else {
      editBtn.textContent = '✏️ Edit';
      editBtn.classList.remove('done');
    }
    
    renderFullSummaryContent(summaryContent, fullScreenData);
  }
  
  // ---- Rich Text Editor Functions ----
  
  function createRichTextToolbar() {
    const toolbar = document.createElement('div');
    toolbar.className = 'richTextToolbar';
    
    // Bold
    const boldBtn = createToolbarButton('B', 'Bold', () => document.execCommand('bold', false, null));
    toolbar.appendChild(boldBtn);
    
    // Italic
    const italicBtn = createToolbarButton('I', 'Italic', () => document.execCommand('italic', false, null));
    toolbar.appendChild(italicBtn);
    
    // Underline
    const underlineBtn = createToolbarButton('U', 'Underline', () => document.execCommand('underline', false, null));
    toolbar.appendChild(underlineBtn);
    
    // Strikethrough
    const strikeBtn = createToolbarButton('S', 'Strikethrough', () => document.execCommand('strikeThrough', false, null));
    toolbar.appendChild(strikeBtn);
    
    // Separator
    toolbar.appendChild(createSeparator());
    
    // Font Size - More options
    const fontSizeSelect = document.createElement('select');
    fontSizeSelect.className = 'toolbarBtn';
    fontSizeSelect.innerHTML = '<option value="">Size</option><option value="1">8px</option><option value="2">10px</option><option value="3">12px</option><option value="4">14px</option><option value="5">16px</option><option value="6">18px</option><option value="7">24px</option>';
    fontSizeSelect.onchange = () => {
      if (fontSizeSelect.value) {
        document.execCommand('fontSize', false, fontSizeSelect.value);
      }
    };
    toolbar.appendChild(fontSizeSelect);
    
    // Font Family
    const fontFamilySelect = document.createElement('select');
    fontFamilySelect.className = 'toolbarBtn';
    fontFamilySelect.innerHTML = '<option value="">Font</option><option value="Arial">Arial</option><option value="Helvetica">Helvetica</option><option value="Times New Roman">Times</option><option value="Courier New">Courier</option><option value="Georgia">Georgia</option><option value="Verdana">Verdana</option><option value="Monaco">Monaco</option>';
    fontFamilySelect.onchange = () => {
      if (fontFamilySelect.value) {
        document.execCommand('fontName', false, fontFamilySelect.value);
      }
    };
    toolbar.appendChild(fontFamilySelect);
    
    // Separator
    toolbar.appendChild(createSeparator());
    
    // Text Color with preset palette
    const colorContainer = document.createElement('div');
    colorContainer.className = 'toolbarBtn';
    colorContainer.style.cssText = 'padding: 4px 8px; display: flex; align-items: center; gap: 4px; position: relative;';
    
    // Preset colors
    const presetColors = ['#ffffff', '#000000', '#ff0000', '#00ff00', '#0000ff', '#ffff00', '#ff00ff', '#00ffff', '#ff8800', '#8800ff'];
    const colorPalette = document.createElement('div');
    colorPalette.className = 'colorPalette';
    colorPalette.style.display = 'none';
    
    presetColors.forEach(color => {
      const colorSwatch = document.createElement('div');
      colorSwatch.style.cssText = `width: 24px; height: 24px; background: ${color}; border: 1px solid rgba(255, 255, 255, 0.3); border-radius: 4px; cursor: pointer; transition: transform 0.2s;`;
      colorSwatch.onclick = (e) => {
        e.stopPropagation();
        document.execCommand('foreColor', false, color);
        colorPalette.style.display = 'none';
        updateToolbarState(toolbar);
      };
      colorSwatch.onmouseenter = () => colorSwatch.style.transform = 'scale(1.1)';
      colorSwatch.onmouseleave = () => colorSwatch.style.transform = 'scale(1)';
      colorPalette.appendChild(colorSwatch);
    });
    
    const colorToggle = document.createElement('button');
    colorToggle.textContent = 'A';
    colorToggle.style.cssText = 'padding: 4px 8px; background: rgba(255, 255, 255, 0.1); border: 1px solid rgba(255, 255, 255, 0.2); border-radius: 4px; color: rgba(255, 255, 255, 0.9); cursor: pointer; font-weight: bold;';
    colorToggle.onclick = (e) => {
      e.stopPropagation();
      const isOpen = colorPalette.style.display !== 'none';
      // Close all palettes first
      document.querySelectorAll('.colorPalette').forEach(p => p.style.display = 'none');
      // Toggle this one
      colorPalette.style.display = isOpen ? 'none' : 'grid';
    };
    
    const colorInput = document.createElement('input');
    colorInput.type = 'color';
    colorInput.value = '#ffffff';
    colorInput.style.cssText = 'width: 30px; height: 24px; border: none; border-radius: 4px; cursor: pointer; padding: 0;';
    colorInput.onchange = () => {
      document.execCommand('foreColor', false, colorInput.value);
      colorPalette.style.display = 'none';
    };
    
    colorContainer.appendChild(colorToggle);
    colorContainer.appendChild(colorInput);
    colorContainer.appendChild(colorPalette);
    toolbar.appendChild(colorContainer);
    
    // Background Color with preset palette
    const bgColorContainer = document.createElement('div');
    bgColorContainer.className = 'toolbarBtn';
    bgColorContainer.style.cssText = 'padding: 4px 8px; display: flex; align-items: center; gap: 4px; position: relative;';
    
    const bgColorPalette = document.createElement('div');
    bgColorPalette.className = 'colorPalette';
    bgColorPalette.style.display = 'none';
    
    presetColors.forEach(color => {
      const colorSwatch = document.createElement('div');
      colorSwatch.style.cssText = `width: 24px; height: 24px; background: ${color}; border: 1px solid rgba(255, 255, 255, 0.3); border-radius: 4px; cursor: pointer; transition: transform 0.2s;`;
      colorSwatch.onclick = (e) => {
        e.stopPropagation();
        document.execCommand('backColor', false, color);
        bgColorPalette.style.display = 'none';
        updateToolbarState(toolbar);
      };
      colorSwatch.onmouseenter = () => colorSwatch.style.transform = 'scale(1.1)';
      colorSwatch.onmouseleave = () => colorSwatch.style.transform = 'scale(1)';
      bgColorPalette.appendChild(colorSwatch);
    });
    
    const bgColorToggle = document.createElement('button');
    bgColorToggle.textContent = 'Bg';
    bgColorToggle.style.cssText = 'padding: 4px 8px; background: rgba(255, 255, 255, 0.1); border: 1px solid rgba(255, 255, 255, 0.2); border-radius: 4px; color: rgba(255, 255, 255, 0.9); cursor: pointer; font-weight: bold;';
    bgColorToggle.onclick = (e) => {
      e.stopPropagation();
      const isOpen = bgColorPalette.style.display !== 'none';
      // Close all palettes first
      document.querySelectorAll('.colorPalette').forEach(p => p.style.display = 'none');
      // Toggle this one
      bgColorPalette.style.display = isOpen ? 'none' : 'grid';
    };
    
    const bgColorInput = document.createElement('input');
    bgColorInput.type = 'color';
    bgColorInput.value = '#000000';
    bgColorInput.style.cssText = 'width: 30px; height: 24px; border: none; border-radius: 4px; cursor: pointer; padding: 0;';
    bgColorInput.onchange = () => {
      document.execCommand('backColor', false, bgColorInput.value);
      bgColorPalette.style.display = 'none';
    };
    
    bgColorContainer.appendChild(bgColorToggle);
    bgColorContainer.appendChild(bgColorInput);
    bgColorContainer.appendChild(bgColorPalette);
    toolbar.appendChild(bgColorContainer);
    
    // Separator
    toolbar.appendChild(createSeparator());
    
    // Align Left
    const alignLeftBtn = createToolbarButton('⬅', 'Align Left', () => document.execCommand('justifyLeft', false, null));
    toolbar.appendChild(alignLeftBtn);
    
    // Align Center
    const alignCenterBtn = createToolbarButton('⬌', 'Align Center', () => document.execCommand('justifyCenter', false, null));
    toolbar.appendChild(alignCenterBtn);
    
    // Align Right
    const alignRightBtn = createToolbarButton('➡', 'Align Right', () => document.execCommand('justifyRight', false, null));
    toolbar.appendChild(alignRightBtn);
    
    // Justify
    const justifyBtn = createToolbarButton('⬌⬌', 'Justify', () => document.execCommand('justifyFull', false, null));
    toolbar.appendChild(justifyBtn);
    
    // Separator
    toolbar.appendChild(createSeparator());
    
    // Indent
    const indentBtn = createToolbarButton('→', 'Indent', () => document.execCommand('indent', false, null));
    toolbar.appendChild(indentBtn);
    
    // Outdent
    const outdentBtn = createToolbarButton('←', 'Outdent', () => document.execCommand('outdent', false, null));
    toolbar.appendChild(outdentBtn);
    
    // Separator
    toolbar.appendChild(createSeparator());
    
    // Unordered List
    const ulBtn = createToolbarButton('• List', 'Bullet List', () => document.execCommand('insertUnorderedList', false, null));
    toolbar.appendChild(ulBtn);
    
    // Ordered List
    const olBtn = createToolbarButton('1. List', 'Numbered List', () => document.execCommand('insertOrderedList', false, null));
    toolbar.appendChild(olBtn);
    
    // Separator
    toolbar.appendChild(createSeparator());
    
    // Subscript
    const subBtn = createToolbarButton('X₂', 'Subscript', () => document.execCommand('subscript', false, null));
    toolbar.appendChild(subBtn);
    
    // Superscript
    const supBtn = createToolbarButton('X²', 'Superscript', () => document.execCommand('superscript', false, null));
    toolbar.appendChild(supBtn);
    
    // Separator
    toolbar.appendChild(createSeparator());
    
    // Remove Formatting
    const removeFormatBtn = createToolbarButton('Clear', 'Remove Formatting', () => document.execCommand('removeFormat', false, null));
    toolbar.appendChild(removeFormatBtn);
    
    // Separator
    toolbar.appendChild(createSeparator());
    
    // Explain Selected Term Button (appears when text is selected)
    const explainSelectedBtn = document.createElement('button');
    explainSelectedBtn.className = 'toolbarBtn';
    explainSelectedBtn.id = 'explainSelectedTermBtn';
    explainSelectedBtn.textContent = '🤖 Explain Selected';
    explainSelectedBtn.title = 'Explain selected term';
    explainSelectedBtn.style.display = 'none';
    explainSelectedBtn.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      const selection = window.getSelection();
      if (selection && selection.toString().trim()) {
        const selectedText = selection.toString().trim();
        if (window.explainSelectedTerm) {
          window.explainSelectedTerm(selectedText);
        } else {
          console.error('explainSelectedTerm not available');
        }
      }
    };
    toolbar.appendChild(explainSelectedBtn);
    
    // Store reference for updating visibility
    toolbar.explainSelectedBtn = explainSelectedBtn;
    
    // Close color palettes when clicking outside
    const closePalettesHandler = (e) => {
      const clickedInColorPalette = colorContainer.contains(e.target) || colorPalette.contains(e.target);
      const clickedInBgPalette = bgColorContainer.contains(e.target) || bgColorPalette.contains(e.target);
      
      if (!clickedInColorPalette && colorPalette.style.display !== 'none') {
        colorPalette.style.display = 'none';
      }
      if (!clickedInBgPalette && bgColorPalette.style.display !== 'none') {
        bgColorPalette.style.display = 'none';
      }
    };
    
    // Use capture phase to catch clicks before they bubble
    document.addEventListener('click', closePalettesHandler, true);
    
    return toolbar;
  }
  
  function createToolbarButton(text, title, onClick) {
    const btn = document.createElement('button');
    btn.className = 'toolbarBtn';
    btn.textContent = text;
    btn.title = title;
    btn.onclick = (e) => {
      e.preventDefault();
      onClick();
      updateToolbarState(btn.closest('.richTextToolbar'));
    };
    return btn;
  }
  
  function createSeparator() {
    const sep = document.createElement('div');
    sep.className = 'toolbarBtn separator';
    return sep;
  }
  
  function attachToolbarToEditor(toolbar, editor) {
    // Update toolbar state when selection changes
    const updateSelection = () => {
      updateToolbarState(toolbar);
      // Show/hide explain selected button
      setTimeout(() => {
        const selection = window.getSelection();
        const explainBtn = toolbar.explainSelectedBtn;
        if (explainBtn && selection && selection.rangeCount > 0) {
          const range = selection.getRangeAt(0);
          const selectedText = selection.toString().trim();
          let isInEditor = false;
          let node = range.commonAncestorContainer;
          while (node && node !== document) {
            if (node === editor || (node.nodeType === 1 && editor.contains(node)) || (node.nodeType === 3 && editor.contains(node.parentNode))) {
              isInEditor = true;
              break;
            }
            node = node.parentNode;
          }
          if (selectedText && isInEditor) {
            explainBtn.style.display = 'inline-block';
          } else {
            explainBtn.style.display = 'none';
          }
        } else if (explainBtn) {
          explainBtn.style.display = 'none';
        }
      }, 10);
    };
    
    editor.addEventListener('mouseup', updateSelection);
    editor.addEventListener('keyup', updateSelection);
    
    // Listen to global selectionchange for toolbar button
    if (!window.selectionChangeHandlerAdded) {
      document.addEventListener('selectionchange', () => {
        setTimeout(() => {
          const editors = document.querySelectorAll('.editableSummary.editMode');
          editors.forEach(ed => {
            const toolbar = ed.previousElementSibling;
            if (toolbar && toolbar.classList.contains('richTextToolbar')) {
              const selection = window.getSelection();
              const explainBtn = toolbar.explainSelectedBtn;
              if (explainBtn && selection && selection.rangeCount > 0) {
                const range = selection.getRangeAt(0);
                const selectedText = selection.toString().trim();
                let isInEditor = false;
                let node = range.commonAncestorContainer;
                while (node && node !== document) {
                  if (node === ed || (node.nodeType === 1 && ed.contains(node)) || (node.nodeType === 3 && ed.contains(node.parentNode))) {
                    isInEditor = true;
                    break;
                  }
                  node = node.parentNode;
                }
                if (selectedText && isInEditor) {
                  explainBtn.style.display = 'inline-block';
                } else {
                  explainBtn.style.display = 'none';
                }
              } else if (explainBtn) {
                explainBtn.style.display = 'none';
              }
            }
          });
        }, 10);
      });
      window.selectionChangeHandlerAdded = true;
    }
    
    // Prevent default behavior for formatting shortcuts
    editor.addEventListener('keydown', (e) => {
      if (e.ctrlKey || e.metaKey) {
        if (['b', 'i', 'u'].includes(e.key.toLowerCase())) {
          e.preventDefault();
          if (e.key.toLowerCase() === 'b') {
            document.execCommand('bold', false, null);
          } else if (e.key.toLowerCase() === 'i') {
            document.execCommand('italic', false, null);
          } else if (e.key.toLowerCase() === 'u') {
            document.execCommand('underline', false, null);
          }
          updateToolbarState(toolbar);
        }
      }
    });
  }
  
  function updateToolbarState(toolbar) {
    const buttons = toolbar.querySelectorAll('.toolbarBtn:not(.separator)');
    buttons.forEach(btn => {
      if (btn.tagName === 'BUTTON') {
        const command = btn.textContent.trim();
        let isActive = false;
        
        if (command === 'B') {
          isActive = document.queryCommandState('bold');
        } else if (command === 'I') {
          isActive = document.queryCommandState('italic');
        } else if (command === 'U') {
          isActive = document.queryCommandState('underline');
        } else if (command === 'S') {
          isActive = document.queryCommandState('strikeThrough');
        } else if (command === '⬅') {
          isActive = document.queryCommandState('justifyLeft');
        } else if (command === '⬌' && !btn.textContent.includes('X')) {
          isActive = document.queryCommandState('justifyCenter');
        } else if (command === '⬌⬌') {
          isActive = document.queryCommandState('justifyFull');
        } else if (command === '➡') {
          isActive = document.queryCommandState('justifyRight');
        } else if (command === 'X₂') {
          isActive = document.queryCommandState('subscript');
        } else if (command === 'X²') {
          isActive = document.queryCommandState('superscript');
        }
        
        if (isActive) {
          btn.classList.add('active');
        } else {
          btn.classList.remove('active');
        }
      }
    });
  }
  
  // ---- Floating Term Explainer Widget ----
  
  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }
  
  let termExplainerWidget = null;
  let selectedText = '';
  
  function setupTermExplainerWidget(editor) {
    // Create widget if it doesn't exist
    if (!termExplainerWidget) {
      termExplainerWidget = document.createElement('div');
      termExplainerWidget.className = 'termExplainerWidget';
      
      const header = document.createElement('div');
      header.className = 'widgetHeader';
      
      const title = document.createElement('h4');
      title.className = 'widgetTitle';
      title.textContent = 'Term Explainer';
      
      const closeBtn = document.createElement('button');
      closeBtn.className = 'widgetClose';
      closeBtn.innerHTML = '×';
      closeBtn.onclick = (e) => {
        e.stopPropagation();
        termExplainerWidget.classList.remove('visible');
      };
      
      header.appendChild(title);
      header.appendChild(closeBtn);
      
      const content = document.createElement('div');
      content.className = 'widgetContent';
      content.id = 'termExplainerContent';
      
      termExplainerWidget.appendChild(header);
      termExplainerWidget.appendChild(content);
      document.body.appendChild(termExplainerWidget);
    }
    
    // Handle text selection - use multiple events for better detection
    const handleSelection = () => {
      setTimeout(() => handleTextSelection(editor), 50);
    };
    
    editor.addEventListener('mouseup', handleSelection);
    editor.addEventListener('keyup', handleSelection);
    
    // Listen to global selectionchange for floating widget
    if (!window.floatingWidgetSelectionHandlerAdded) {
      document.addEventListener('selectionchange', () => {
        const editors = document.querySelectorAll('.editableSummary.editMode');
        editors.forEach(ed => {
          handleTextSelection(ed);
        });
      });
      window.floatingWidgetSelectionHandlerAdded = true;
    }
    
    // Hide widget when clicking outside (only add once)
    if (!window.termExplainerClickHandlerAdded) {
      document.addEventListener('click', (e) => {
        if (termExplainerWidget && !termExplainerWidget.contains(e.target)) {
          // Check if click is in any editor
          const editors = document.querySelectorAll('.editableSummary.editMode');
          let clickedInEditor = false;
          for (let ed of editors) {
            if (ed.contains(e.target)) {
              clickedInEditor = true;
              break;
            }
          }
          if (!clickedInEditor) {
            termExplainerWidget.classList.remove('visible');
          }
        }
      }, true);
      window.termExplainerClickHandlerAdded = true;
    }
  }
  
  function handleTextSelection(editor) {
    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0) {
      if (termExplainerWidget) {
        termExplainerWidget.classList.remove('visible');
      }
      return;
    }
    
    const range = selection.getRangeAt(0);
    const selectedText = selection.toString().trim();
    
    // Check if selection is within the editor (handle both element and text nodes)
    let isInEditor = false;
    let node = range.commonAncestorContainer;
    while (node && node !== document) {
      if (node === editor || (node.nodeType === 1 && editor.contains(node)) || (node.nodeType === 3 && editor.contains(node.parentNode))) {
        isInEditor = true;
        break;
      }
      node = node.parentNode;
    }
    
    // Only show widget if text is selected and it's within the editor
    if (selectedText && selectedText.length > 0 && isInEditor && termExplainerWidget) {
      // Show button to explain first
      const content = document.getElementById('termExplainerContent');
      if (!content) {
        console.error('termExplainerContent not found');
        return;
      }
      
      const selectedTextEscaped = escapeHtml(selectedText);
      content.innerHTML = `
        <div style="text-align: center; padding: 12px;">
          <div style="margin-bottom: 8px; font-weight: 500;">Selected: "${selectedTextEscaped}"</div>
          <button id="explainTermBtn" class="toolbarBtn" style="width: 100%; padding: 8px; margin-top: 8px;">
            Explain Term
          </button>
        </div>
      `;
      
      // Attach click handler
      const explainBtn = document.getElementById('explainTermBtn');
      if (explainBtn) {
        explainBtn.onclick = (e) => {
          e.stopPropagation();
          if (window.explainSelectedTerm) {
            window.explainSelectedTerm(selectedText);
          } else {
            console.error('explainSelectedTerm function not found');
          }
        };
      }
      
      // Make widget visible temporarily to measure it
      termExplainerWidget.style.display = 'block';
      termExplainerWidget.style.visibility = 'hidden';
      termExplainerWidget.style.top = '-9999px';
      termExplainerWidget.style.left = '-9999px';
      
      // Force a reflow to ensure dimensions are calculated
      void termExplainerWidget.offsetWidth;
      
      // Get dimensions
      const widgetWidth = termExplainerWidget.offsetWidth || 250;
      const widgetHeight = termExplainerWidget.offsetHeight || 100;
      
      // Get position for widget
      const rect = range.getBoundingClientRect();
      const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
      const scrollLeft = window.pageXOffset || document.documentElement.scrollLeft;
      
      // Position widget above selection, or below if not enough space
      let top = rect.top + scrollTop - widgetHeight - 10;
      let left = rect.left + scrollLeft + (rect.width / 2) - (widgetWidth / 2);
      
      // Adjust if widget would go off screen
      if (top < scrollTop + 10) {
        top = rect.bottom + scrollTop + 10;
      }
      if (left < scrollLeft + 10) {
        left = scrollLeft + 10;
      }
      if (left + widgetWidth > scrollLeft + window.innerWidth - 10) {
        left = scrollLeft + window.innerWidth - widgetWidth - 10;
      }
      
      // Set final position and make visible
      termExplainerWidget.style.top = top + 'px';
      termExplainerWidget.style.left = left + 'px';
      termExplainerWidget.style.visibility = 'visible';
      termExplainerWidget.style.display = 'block';
      termExplainerWidget.classList.add('visible');
    } else {
      if (termExplainerWidget) {
        termExplainerWidget.classList.remove('visible');
        termExplainerWidget.style.display = 'none';
      }
    }
  }
  
  // Show term explainer widget for PDF text selection
  function showTermExplainerForPDFSelection(text, page) {
    // Initialize widget if it doesn't exist
    if (!termExplainerWidget) {
      // Create widget directly without needing an editor
      termExplainerWidget = document.createElement('div');
      termExplainerWidget.className = 'termExplainerWidget';
      
      const header = document.createElement('div');
      header.className = 'widgetHeader';
      
      const title = document.createElement('h4');
      title.className = 'widgetTitle';
      title.textContent = 'Term Explainer';
      
      const closeBtn = document.createElement('button');
      closeBtn.className = 'widgetClose';
      closeBtn.innerHTML = '×';
      closeBtn.onclick = (e) => {
        e.stopPropagation();
        termExplainerWidget.classList.remove('visible');
      };
      
      header.appendChild(title);
      header.appendChild(closeBtn);
      
      const content = document.createElement('div');
      content.className = 'widgetContent';
      content.id = 'termExplainerContent';
      
      termExplainerWidget.appendChild(header);
      termExplainerWidget.appendChild(content);
      document.body.appendChild(termExplainerWidget);
      
      // Add click handler to hide widget when clicking outside
      if (!window.termExplainerClickHandlerAdded) {
        document.addEventListener('click', (e) => {
          if (termExplainerWidget && !termExplainerWidget.contains(e.target)) {
            // Don't hide if clicking in PDF frame
            const pdfFrame = document.getElementById('pdfFrame');
            if (pdfFrame && pdfFrame.contains(e.target)) {
              return;
            }
            termExplainerWidget.classList.remove('visible');
          }
        }, true);
        window.termExplainerClickHandlerAdded = true;
      }
    }
    
    if (!termExplainerWidget) return;
    
    const content = document.getElementById('termExplainerContent');
    if (!content) return;
    
    const selectedTextEscaped = escapeHtml(text);
    content.innerHTML = `
      <div style="text-align: center; padding: 12px;">
        <div style="margin-bottom: 8px; font-weight: 500;">Selected: "${selectedTextEscaped}"</div>
        <button id="explainTermBtn" class="toolbarBtn" style="width: 100%; padding: 8px; margin-top: 8px;">
          Explain Term
        </button>
      </div>
    `;
    
    // Attach click handler
    const explainBtn = document.getElementById('explainTermBtn');
    if (explainBtn) {
      explainBtn.onclick = (e) => {
        e.stopPropagation();
        // Highlight the text in PDF before explaining
        highlightPDFText();
        if (window.explainSelectedTerm) {
          window.explainSelectedTerm(text);
        } else {
          console.error('explainSelectedTerm function not found');
        }
      };
    }
    
    // Position widget near PDF (we'll use a fixed position for now)
    const pdfFrame = document.getElementById('pdfFrame');
    if (pdfFrame) {
      const rect = pdfFrame.getBoundingClientRect();
      const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
      const scrollLeft = window.pageXOffset || document.documentElement.scrollLeft;
      
      // Position widget to the right of PDF or above it
      termExplainerWidget.style.display = 'block';
      termExplainerWidget.style.visibility = 'hidden';
      termExplainerWidget.style.top = '-9999px';
      termExplainerWidget.style.left = '-9999px';
      
      void termExplainerWidget.offsetWidth;
      
      const widgetWidth = termExplainerWidget.offsetWidth || 250;
      const widgetHeight = termExplainerWidget.offsetHeight || 100;
      
      let top = rect.top + scrollTop + 20;
      let left = rect.right + scrollLeft + 20;
      
      // Adjust if widget would go off screen
      if (left + widgetWidth > scrollLeft + window.innerWidth - 10) {
        left = rect.left + scrollLeft - widgetWidth - 20;
      }
      if (left < scrollLeft + 10) {
        left = scrollLeft + 10;
      }
      if (top + widgetHeight > scrollTop + window.innerHeight - 10) {
        top = scrollTop + window.innerHeight - widgetHeight - 10;
      }
      
      termExplainerWidget.style.top = top + 'px';
      termExplainerWidget.style.left = left + 'px';
      termExplainerWidget.style.visibility = 'visible';
      termExplainerWidget.style.display = 'block';
      termExplainerWidget.classList.add('visible');
    }
  }
  
  // Function to trigger highlighting in PDF iframe
  function highlightPDFText() {
    try {
      const pdfFrame = document.getElementById('pdfFrame');
      if (pdfFrame && pdfFrame.contentWindow) {
        // Call the highlight function in the PDF iframe
        if (pdfFrame.contentWindow.highlightSelectedText) {
          pdfFrame.contentWindow.highlightSelectedText();
        } else {
          console.warn('[Wrapper] highlightSelectedText function not found in PDF iframe');
        }
      }
    } catch(e) {
      console.error('[Wrapper] Error highlighting PDF text:', e);
    }
  }
  
  // Global function to explain selected term (called from button)
  window.explainSelectedTerm = async function(term) {
    const content = document.getElementById('termExplainerContent');
    content.innerHTML = '<div class="widgetLoading">Explaining term...</div>';
    await explainTerm(term, content);
  };
  
  async function explainTerm(term, contentElement) {
    try {
      // Use the existing define_term API
      if (window.pywebview && window.pywebview.api && window.pywebview.api.define_term) {
        const result = await window.pywebview.api.define_term(term);
        if (result && result.ok && result.definition) {
          // Format the definition nicely
          const formatted = result.definition.replace(/\n/g, '<br>').replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
          contentElement.innerHTML = formatted;
        } else {
          contentElement.innerHTML = result && result.error ? `Error: ${escapeHtml(result.error)}` : 'No explanation available.';
        }
      } else {
        // Fallback: simple message
        contentElement.innerHTML = `Term: <strong>${escapeHtml(term)}</strong><br><br>AI explanation feature requires backend integration.`;
      }
    } catch (error) {
      console.error('Error explaining term:', error);
      contentElement.innerHTML = 'Error: Could not explain term. Please try again.';
    }
  }
  
  function renderFullSummaryContent(container, data) {
    container.innerHTML = '';

    if (data.summaries && Object.keys(data.summaries).length > 0) {
      const pages = Object.keys(data.summaries).map(Number).sort((a,b)=>a-b);
      for (const page of pages) {
        const summary = data.summaries[page];
        const summaryText = typeof summary === 'string' ? summary : (summary.summary || '');
        const pageDiv = document.createElement('div');
        pageDiv.className = 'fullSummaryPage';
        pageDiv.dataset.pageNum = page;
        
        // Styling based on edit mode
        if (isEditMode) {
          pageDiv.classList.add('editMode');
        }
        
        const pageHeader = document.createElement('div');
        pageHeader.style.cssText = 'display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;';
        
        const pageTitle = document.createElement('h3');
        pageTitle.className = 'fullSummaryPageTitle';
        pageTitle.textContent = `Page ${page}`;
        
        pageHeader.appendChild(pageTitle);
        
        if (isEditMode) {
          const actions = document.createElement('div');
          actions.style.cssText = 'display: flex; gap: 8px;';
          
          const deleteBtn = document.createElement('button');
          deleteBtn.textContent = '🗑️';
          deleteBtn.className = 'fullSummaryDeleteBtn';
          deleteBtn.onclick = () => deletePageSummary(page);
          
          actions.appendChild(deleteBtn);
          pageHeader.appendChild(actions);
        }
        
        // Create toolbar for edit mode
        let toolbar = null;
        if (isEditMode) {
          toolbar = createRichTextToolbar();
        }
        
        const summaryDiv = document.createElement('div');
        summaryDiv.className = 'editableSummary';
        summaryDiv.dataset.pageNum = page;
        
        if (isEditMode) {
          summaryDiv.contentEditable = 'true';
          summaryDiv.classList.add('editMode');
          
          // Parse HTML if it exists, otherwise use plain text
          const isHtml = summaryText.includes('<') && summaryText.includes('>');
          if (isHtml) {
            summaryDiv.innerHTML = summaryText;
          } else {
          summaryDiv.textContent = summaryText;
          }
          
          // Attach toolbar to this editor
          if (toolbar) {
            attachToolbarToEditor(toolbar, summaryDiv);
          }
          
          // Setup floating term explainer widget
          setupTermExplainerWidget(summaryDiv);
          
          // Save on blur - capture original HTML
          const originalHtml = summaryDiv.innerHTML;
          summaryDiv.addEventListener('blur', async function() {
            const newHtml = this.innerHTML.trim();
            if (newHtml !== originalHtml && fullScreenData && fullScreenData.summaries) {
              fullScreenData.summaries[page] = newHtml;
              
              // Update in-memory cache
              pageSummaryCache.set(page, {
                summary: newHtml,
                timestamp: Date.now()
              });
              
              // Update sidebar if this is the current page (strip HTML for sidebar)
              if (currentPage === page) {
                const plainText = summaryDiv.textContent.trim();
                setBox(rsSummary, plainText);
                console.log('✓ Updated sidebar summary for page', page);
              }
              
              // Save to backend
              if (window.pywebview && window.pywebview.api) {
                try {
                  const libraryData = {
                    summaries: fullScreenData.summaries,
                    flashcards: fullScreenData.flashcards || {},
                    lastModified: new Date().toISOString()
                  };
                  await window.pywebview.api.save_library_data(fullScreenFileName, libraryData);
                  console.log('✓ Saved summary update to library for page', page);
                } catch(e) {
                  console.error('Error saving summary update:', e);
                }
              }
            }
          });
          
          // Allow Enter for new line (Escape to finish editing)
          summaryDiv.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
              this.blur();
            }
          });
    } else {
          summaryDiv.contentEditable = 'false';
          summaryDiv.classList.remove('editMode');
          // Check if content is HTML or plain text
          const isHtml = summaryText.includes('<') && summaryText.includes('>');
          if (isHtml) {
            summaryDiv.innerHTML = summaryText;
          } else {
          summaryDiv.innerHTML = escapeHtml(summaryText).replace(/\*\*(.*?)\*\*/g,'<strong>$1</strong>');
          }
        }
        
        pageDiv.appendChild(pageHeader);
        if (toolbar) {
          pageDiv.appendChild(toolbar);
        }
        pageDiv.appendChild(summaryDiv);
        container.appendChild(pageDiv);
      }
    } else {
      container.innerHTML = '<div style="text-align: center; padding: 40px; color: #a0a0a2;">No summaries available</div>';
    }
  }
  
  // Edit function no longer needed - using inline editing instead
  
  function deletePageSummary(page) {
    if (confirm('Delete summary for Page ' + page + '?')) {
      if (fullScreenData && fullScreenData.summaries) {
        delete fullScreenData.summaries[page];
        renderFullSummaryContent(document.getElementById('fullSummaryContent'), fullScreenData);
        // TODO: Save to backend
      }
    }
  }

  // ---- Flashcards Center ----
  async function openFlashcardsCenter() {
    const modal = document.getElementById('fcModal');
    const listEl = document.getElementById('deckList');
    const emptyEl = document.getElementById('fcEmpty');
    modal.classList.add('show');
    listEl.innerHTML = '<div style="text-align:center;padding:40px;color:#a0a0a2;">Loading decks...</div>';
    emptyEl.style.display = 'none';

    try {
      const result = await window.pywebview.api.load_flashcards_center();
      if (!result || !result.ok) throw new Error(result?.error||'Failed to load decks');
      const decks = result.decks || {};
      renderDecks(decks);
    } catch(e){
      listEl.innerHTML = `<div style="text-align:center;padding:40px;color:#d44;">${e.message}</div>`;
    }
  }

  function closeFlashcardsCenter(){
    document.getElementById('fcModal').classList.remove('show');
  }

  function renderDecks(decks){
    const listEl = document.getElementById('deckList');
    const emptyEl = document.getElementById('fcEmpty');
    const names = Object.keys(decks).sort((a,b)=>a.localeCompare(b));
    if (names.length === 0){
      listEl.innerHTML = '';
      emptyEl.style.display = 'block';
      return;
    }
    emptyEl.style.display = 'none';
    listEl.innerHTML = '';
    for (const name of names){
      const deckInfo = decks[name];
      const totalCards = deckInfo.total_cards || 0;
      const dueCards = deckInfo.due_cards || 0;
      const item = document.createElement('div');
      item.className = 'deckItem';
      item.innerHTML = `
        <h3>${escapeHtml(name)}</h3>
        <div class="meta">
          <span>${totalCards} total cards</span> | 
          <span style="color: ${dueCards > 0 ? '#ff9800' : '#0f0'};">${dueCards} due</span>
        </div>
        ${deckInfo.description ? `<p style="font-size:12px;color:#a0a0a2;margin:8px 0;">${escapeHtml(deckInfo.description)}</p>` : ''}
        <div class="actions">
          <button class="studyDeck" style="background:#5a9fd4;">Study</button>
          <button class="openDeck">Browse</button>
          <button class="renameDeck">Rename</button>
          <button class="deleteDeck">Delete</button>
        </div>
      `;
      item.querySelector('.studyDeck').onclick = ()=>startStudySession(name);
      item.querySelector('.openDeck').onclick = async ()=> {
        const r = await window.pywebview.api.get_deck_details(name);
        if (r && r.ok) openDeck(name, r.cards || []);
      };
      item.querySelector('.renameDeck').onclick = ()=>renameDeck(name);
      item.querySelector('.deleteDeck').onclick = ()=>deleteDeck(name);
      listEl.appendChild(item);
    }
  }

  async function openDeck(name, cards){
    // Interactive flashcard viewer
    const content = document.getElementById('fcContent');
    content.innerHTML = '';
    
    if (!cards || cards.length === 0) {
    const back = document.createElement('button');
    back.textContent = '← Back to Decks';
    back.style.cssText = 'margin-bottom: 16px; padding:8px 12px; border:1px solid #404040; background:#2a2a2c; color:#e8e8ea; border-radius:6px; cursor:pointer;';
    back.onclick = async ()=>{ const r=await window.pywebview.api.load_flashcards_center(); renderDecks(r.decks||{}); };
      const emptyMsg = document.createElement('div');
      emptyMsg.style.cssText = 'text-align:center; padding:40px; color:#a0a0a2; font-size:16px;';
      emptyMsg.textContent = 'This deck is empty.';
      content.appendChild(back);
      content.appendChild(emptyMsg);
      return;
    }
    
    let currentIndex = 0;
    let showingBack = false;
    
    const back = document.createElement('button');
    back.textContent = '← Back to Decks';
    back.style.cssText = 'margin-bottom: 16px; padding:8px 12px; border:1px solid #404040; background:#2a2a2c; color:#e8e8ea; border-radius:6px; cursor:pointer;';
    back.onclick = async ()=>{ const r=await window.pywebview.api.load_flashcards_center(); renderDecks(r.decks||{}); };
    
    const header = document.createElement('div');
    header.style.cssText = 'display:flex; justify-content:space-between; align-items:center; margin-bottom:20px;';
    
    const title = document.createElement('h2');
    title.textContent = name;
    title.style.cssText = 'font-size:24px;color:#f0f0f2;margin:0;';
    
    const cardCounter = document.createElement('div');
    cardCounter.style.cssText = 'font-size:14px;color:#a0a0a2;';
    function updateCounter() {
      cardCounter.textContent = `${currentIndex + 1} / ${cards.length}`;
    }
    updateCounter();
    
    header.appendChild(title);
    header.appendChild(cardCounter);
    
    const cardDiv = document.createElement('div');
    cardDiv.id = 'deckCardViewer';
    cardDiv.style.cssText = 'background:rgba(255,255,255,0.05); border:2px solid rgba(255,255,255,0.1); border-radius:12px; padding:40px; min-height:300px; display:flex; flex-direction:column; align-items:center; justify-content:center; margin-bottom:30px; cursor:pointer; transition:all 0.3s;';
    
    const frontText = document.createElement('div');
    frontText.style.cssText = 'font-size:20px; color:#f0f0f2; text-align:center; line-height:1.6; width:100%;';
    
    const backText = document.createElement('div');
    backText.style.cssText = 'margin-top:30px; padding-top:30px; border-top:1px solid rgba(255,255,255,0.2); font-size:18px; color:#d8d8da; text-align:center; line-height:1.6; width:100%; display:none;';
    
    function updateCard() {
      const card = cards[currentIndex];
      const front = card.front || card.q || '';
      const back = card.back || card.a || '';
      frontText.innerHTML = escapeHtml(front).replace(/\n/g, '<br>');
      backText.innerHTML = escapeHtml(back).replace(/\n/g, '<br>');
      showingBack = false;
      backText.style.display = 'none';
      updateCounter();
    }
    
    cardDiv.appendChild(frontText);
    cardDiv.appendChild(backText);
    
    cardDiv.onclick = () => {
      if (!showingBack) {
        showingBack = true;
        backText.style.display = 'block';
      }
    };
    
    const navButtons = document.createElement('div');
    navButtons.style.cssText = 'display:flex; gap:12px; justify-content:center; align-items:center; margin-bottom:20px;';
    
    const prevBtn = document.createElement('button');
    prevBtn.textContent = '← Previous';
    prevBtn.style.cssText = 'padding:12px 20px; background:#2a2a2c; border:1px solid #404040; color:#e8e8ea; border-radius:8px; cursor:pointer; font-size:14px;';
    prevBtn.onclick = (e) => {
      e.stopPropagation();
      if (currentIndex > 0) {
        currentIndex--;
        updateCard();
      }
    };
    
    const flipBtn = document.createElement('button');
    flipBtn.innerHTML = `${iconFlip} Flip Card`;
    flipBtn.style.cssText = 'padding:12px 20px; background:#5a9fd4; border:none; color:#fff; border-radius:8px; cursor:pointer; font-size:14px; font-weight:600; display:flex; align-items:center; gap:8px;';
    flipBtn.onclick = (e) => {
      e.stopPropagation();
      if (showingBack) {
        showingBack = false;
        backText.style.display = 'none';
      } else {
        showingBack = true;
        backText.style.display = 'block';
      }
    };
    
    const nextBtn = document.createElement('button');
    nextBtn.textContent = 'Next →';
    nextBtn.style.cssText = 'padding:12px 20px; background:#2a2a2c; border:1px solid #404040; color:#e8e8ea; border-radius:8px; cursor:pointer; font-size:14px;';
    nextBtn.onclick = (e) => {
      e.stopPropagation();
      if (currentIndex < cards.length - 1) {
        currentIndex++;
        updateCard();
      }
    };
    
    navButtons.appendChild(prevBtn);
    navButtons.appendChild(flipBtn);
    navButtons.appendChild(nextBtn);
    
    const listToggle = document.createElement('button');
    listToggle.innerHTML = `${iconList} View All Cards`;
    listToggle.style.cssText = 'padding:10px 16px; background:rgba(90,159,212,0.2); border:1px solid rgba(90,159,212,0.4); color:rgba(90,159,212,0.9); border-radius:6px; cursor:pointer; font-size:13px; margin-bottom:20px; display:flex; align-items:center; gap:8px;';
    
    let showingList = false;
    const listContainer = document.createElement('div');
    listContainer.style.cssText = 'display:none; margin-top:20px;';
    
    listToggle.onclick = () => {
      if (showingList) {
        showingList = false;
        listContainer.style.display = 'none';
        listToggle.innerHTML = `${iconList} View All Cards`;
      } else {
        showingList = true;
        listContainer.style.display = 'block';
        listToggle.innerHTML = `${iconCards} Card View`;
        
        listContainer.innerHTML = '';
    const list = document.createElement('div');
        list.style.cssText = 'display:grid; gap:12px; max-height:500px; overflow-y:auto;';
        cards.forEach((c, idx) => {
      const d = document.createElement('div');
          d.style.cssText = 'background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.1); border-radius:8px; padding:12px 16px; cursor:pointer;';
          if (idx === currentIndex) {
            d.style.border = '2px solid #5a9fd4';
            d.style.background = 'rgba(90,159,212,0.1)';
          }
      const front = c.front || c.q || '';
      const back = c.back || c.a || '';
      d.innerHTML = `<div style="font-weight:600;color:#f0f0f2;margin-bottom:6px;">Q: ${escapeHtml(front)}</div><div style="color:#d8d8da;">A: ${escapeHtml(back)}</div>`;
          d.onclick = () => {
            currentIndex = idx;
            updateCard();
            showingList = false;
            listContainer.style.display = 'none';
            listToggle.innerHTML = `${iconList} View All Cards`;
          };
      list.appendChild(d);
        });
        listContainer.appendChild(list);
    }
    };
    
    content.appendChild(back);
    content.appendChild(header);
    content.appendChild(cardDiv);
    content.appendChild(navButtons);
    content.appendChild(listToggle);
    content.appendChild(listContainer);
    
    updateCard();
  }

  async function renameDeck(name){
    const newName = prompt('Rename deck', name);
    if (!newName || newName.trim()===name) return;
    await window.pywebview.api.rename_flashcards_deck(name, newName.trim());
    const r = await window.pywebview.api.load_flashcards_center();
    renderDecks(r.decks||{});
  }

  async function deleteDeck(name){
    if (!confirm(`Delete deck "${name}"?`)) return;
    await window.pywebview.api.delete_flashcards_deck(name);
    const r = await window.pywebview.api.load_flashcards_center();
    renderDecks(r.decks||{});
  }

  async function importDeckFromFile(){
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.tsv,.csv,.txt';
    input.onchange = async () => {
      const file = input.files && input.files[0];
      if (!file) return;
      const text = await file.text();
      const delim = file.name.endsWith('.csv') ? ',' : '\t';
      const lines = text.split(/\r?\n/).filter(Boolean);
      const cards = [];
      for (const line of lines){
        const parts = line.split(delim);
        if (parts.length < 2) continue;
        cards.push({ q: parts[0].trim(), a: parts.slice(1).join(delim).trim() });
      }
      const deckName = prompt('Deck name for import:', file.name.replace(/\.(csv|tsv|txt)$/i,'').trim()) || 'Imported';
      const res = await window.pywebview.api.import_flashcards_deck(deckName, cards);
      if (!res || !res.ok) { alert('Import failed'); return; }
      const r = await window.pywebview.api.load_flashcards_center();
      renderDecks(r.decks||{});
    };
    input.click();
  }

  let currentStudySession = null;
  let currentDeckName = null;
  
  async function startStudySession(deckName){
    try {
      const result = await window.pywebview.api.start_study_session(deckName, 50, 20);
      if (!result || !result.ok) {
        alert('Failed to start session: ' + (result?.error || 'Unknown error'));
        return;
      }
      currentStudySession = result;
      currentDeckName = deckName;
      showStudyUI(result);
    } catch(e) {
      alert('Error: ' + e.message);
    }
  }
  
  function showStudyUI(session){
    const content = document.getElementById('fcContent');
    content.innerHTML = '';
    
    const backBtn = document.createElement('button');
    backBtn.textContent = '← End Session';
    backBtn.style.cssText = 'margin-bottom: 16px; padding:8px 12px; border:1px solid #404040 ; background:#2a2a2c; color:#e8e8ea; border-radius:6px; cursor:pointer;';
    backBtn.onclick = async ()=>{ 
      currentStudySession = null;
      const r=await window.pywebview.api.load_flashcards_center(); 
      renderDecks(r.decks||{}); 
    };
    
    const progress = session.progress || {current: 0, total: 0};
    const progressBar = document.createElement('div');
    progressBar.style.cssText = 'width:100%; height:8px; background:#2a2a2c; border-radius:4px; margin-bottom:20px; overflow:hidden;';
    const progressFill = document.createElement('div');
    const pct = progress.total > 0 ? (progress.current/progress.total)*100  : 0;
    progressFill.style.cssText = `width:${pct}%; height:100%; background:#5a9fd4; transition:width 0.3s;`;
    progressBar.appendChild(progressFill);
    
    const progressText = document.createElement('div');
    progressText.style.cssText = 'margin-bottom:20px; font-size:14px; color:#a0a0a2;';
    progressText.textContent = `${progress.current} / ${progress.total}`;
    
    const cardDiv = document.createElement('div');
    cardDiv.id = 'studyCard';
    cardDiv.style.cssText = 'background:rgba(255,255,255,0.05); border:2px solid rgba(255,255,255,0.1); border-radius:12px; padding:40px; min-height:300px; display:flex; align-items:center; justify-content:center; margin-bottom:30px; cursor:pointer;';
    
    let showingBack = false;
    const card = session.card;
    const frontText = document.createElement('div');
    frontText.style.cssText = 'font-size:20px; color:#f0f0f2; text-align:center; line-height:1.6;';
    frontText.innerHTML = escapeHtml(card.front || '').replace(/\n/g, '<br>');
    cardDiv.appendChild(frontText);
    
    cardDiv.onclick = () => {
      if (!showingBack) {
        showingBack = true;
        const backText = document.createElement('div');
        backText.style.cssText = 'margin-top:30px; padding-top:30px; border-top:1px solid rgba(255,255,255,0.2); font-size:18px; color:#d8d8da; text-align:center; line-height:1.6;';
        backText.innerHTML = escapeHtml(card.back || '').replace(/\n/g, '<br>');
        cardDiv.appendChild(backText);
      }
    };
    
    const ratingButtons = document.createElement('div');
    ratingButtons.style.cssText = 'display:grid; grid-template-columns:repeat(4,1fr); gap:12px;';
    ratingButtons.innerHTML = `
      <button class="ratingBtn" data-rating="1" style="padding:16px; background:#d44; border:none; border-radius:8px; color:#fff; font-size:14px; cursor:pointer; font-weight:600;">Again (1)</button>
      <button class="ratingBtn" data-rating="2" style="padding:16px; background:#ff9800; border:none; border-radius:8px; color:#fff; font-size:14px; cursor:pointer; font-weight:600;">Hard (2)</button>
      <button class="ratingBtn" data-rating="3" style="padding:16px; background:#5a9fd4; border:none; border-radius:8px; color:#fff; font-size:14px; cursor:pointer; font-weight:600;">Good (3)</button>
      <button class="ratingBtn" data-rating="4" style="padding:16px; background:#0f0; border:none; border-radius:8px; color:#fff; font-size:14px; cursor:pointer; font-weight:600;">Easy (4)</button>
    `;
    
    ratingButtons.querySelectorAll('.ratingBtn').forEach(btn => {
      btn.onclick = async () => {
        if (!showingBack) return;
        const rating = parseInt(btn.dataset.rating);
        const result = await window.pywebview.api.answer_study_card(currentDeckName, card.id, rating);
        if (result && result.ok) {
          if (result.complete) {
            alert(`Session complete!\n\nStats:\n- Again: ${result.stats.again}\n- Hard: ${result.stats.hard}\n- Good: ${result.stats.good}\n- Easy: ${result.stats.easy}`);
            currentStudySession = null;
            const r = await window.pywebview.api.load_flashcards_center();
            renderDecks(r.decks||{});
          } else {
            currentStudySession = result;
            showStudyUI(result);
          }
        }
      };
    });
    
    content.appendChild(backBtn);
    content.appendChild(progressBar);
    content.appendChild(progressText);
    content.appendChild(cardDiv);
    content.appendChild(ratingButtons);
  }

  // Wire up modal buttons after DOM available
  document.getElementById('fcClose').onclick = closeFlashcardsCenter;
  document.getElementById('fcImportBtn').onclick = importDeckFromFile;
  // Library Flashcards Quiz Feature
  async function openLibraryFlashcardsQuiz() {
    try {
      if (!window.pywebview || !window.pywebview.api) {
        alert('Library: bridge not available.');
        return;
      }
      
      const result = await window.pywebview.api.list_library_files();
      if (!result || !result.ok) {
        alert('Could not load library');
        return;
      }
      
      const files = result.files || [];
      if (files.length === 0) {
        alert('No lectures in library');
        return;
      }
      
      // Filter files that have flashcards
      const filesWithFlashcards = [];
      for (const file of files) {
        const fileData = await window.pywebview.api.get_library_file(file.name);
        if (fileData && fileData.ok && fileData.data && fileData.data.flashcards) {
          const flashcardPages = Object.keys(fileData.data.flashcards);
          let totalCards = 0;
          for (const page of flashcardPages) {
            const cards = fileData.data.flashcards[page];
            if (Array.isArray(cards)) totalCards += cards.length;
          }
          if (totalCards > 0) {
            filesWithFlashcards.push({ ...file, totalCards });
          }
        }
      }
      
      if (filesWithFlashcards.length === 0) {
        alert('No flashcards found in any lectures');
        return;
      }
      
      // Create modal for quiz selection
      const modal = document.createElement('div');
      modal.id = 'libraryQuizModal';
      const isLightMode = document.body.dataset.theme === 'light' || document.body.classList.contains('light-mode');
      const bgColor = isLightMode ? '#ffffff' : '#1a1a1c';
      const borderColor = isLightMode ? '#e0e0e0' : '#404040';
      const textColor = isLightMode ? '#1a1a1c' : '#f0f0f2';
      const textSecondary = isLightMode ? '#666666' : '#a0a0a2';
      const cardBg = isLightMode ? 'rgba(0,0,0,0.02)' : 'rgba(255,255,255,0.05)';
      const cardBorder = isLightMode ? 'rgba(0,0,0,0.1)' : 'rgba(255,255,255,0.1)';
      const overlayBg = isLightMode ? 'rgba(0,0,0,0.6)' : 'rgba(0,0,0,0.85)';
      
      modal.style.cssText = `position:fixed; top:0; left:0; right:0; bottom:0; background:${overlayBg}; z-index:100001; display:flex; align-items:center; justify-content:center; padding:20px; opacity:0; transition:opacity 0.2s ease;`;
      
      const content = document.createElement('div');
      content.style.cssText = `background:${bgColor}; border:1px solid ${borderColor}; border-radius:16px; padding:0; max-width:1200px; width:100%; max-height:85vh; overflow:hidden; position:relative; box-shadow:0 20px 60px rgba(0,0,0,0.3); transform:scale(0.95); transition:transform 0.2s ease; display:flex; flex-direction:column;`;
      
      const header = document.createElement('div');
      header.style.cssText = `display:flex; justify-content:space-between; align-items:center; padding:24px 30px; border-bottom:1px solid ${borderColor}; background:${isLightMode ? '#fafafa' : '#242426'};`;
      
      const title = document.createElement('h2');
      title.textContent = 'Select Lectures to Quiz';
      title.style.cssText = `font-size:22px; color:${textColor}; margin:0; font-weight:600;`;
      
      const closeBtn = document.createElement('button');
      closeBtn.innerHTML = iconClose;
      closeBtn.style.cssText = `width:36px; height:36px; background:${isLightMode ? 'rgba(0,0,0,0.05)' : 'rgba(255,255,255,0.1)'}; border:none; border-radius:8px; color:${textColor}; cursor:pointer; display:flex; align-items:center; justify-content:center; transition:all 0.2s;`;
      closeBtn.onmouseover = () => closeBtn.style.background = isLightMode ? 'rgba(0,0,0,0.1)' : 'rgba(255,255,255,0.15)';
      closeBtn.onmouseout = () => closeBtn.style.background = isLightMode ? 'rgba(0,0,0,0.05)' : 'rgba(255,255,255,0.1)';
      closeBtn.onclick = () => {
        modal.style.opacity = '0';
        content.style.transform = 'scale(0.95)';
        setTimeout(() => modal.remove(), 200);
      };
      
      header.appendChild(title);
      header.appendChild(closeBtn);
      
      // Two-column layout container
      const mainContent = document.createElement('div');
      mainContent.style.cssText = `flex:1; display:flex; overflow:hidden;`;
      
      // Left column - Lecture selections
      const leftColumn = document.createElement('div');
      leftColumn.style.cssText = `flex:1; display:flex; flex-direction:column; border-right:1px solid ${borderColor};`;
      
      const leftScrollContent = document.createElement('div');
      leftScrollContent.className = 'smooth-scroll';
      leftScrollContent.style.cssText = `flex:1; overflow-y:auto; padding:30px;`;
      
      const selectedLectures = new Set();
      
      const lectureList = document.createElement('div');
      lectureList.style.cssText = 'display:grid; gap:12px;';
      
      filesWithFlashcards.forEach(file => {
        const lectureCard = document.createElement('div');
        lectureCard.style.cssText = `background:${cardBg}; border:2px solid ${cardBorder}; border-radius:12px; padding:20px; cursor:pointer; transition:all 0.2s; display:flex; align-items:center; gap:16px;`;
        
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.style.cssText = 'width:20px; height:20px; cursor:pointer; accent-color:#5a9fd4;';
        checkbox.onchange = () => {
          if (checkbox.checked) {
            selectedLectures.add(file.name);
            lectureCard.style.borderColor = '#5a9fd4';
            lectureCard.style.background = isLightMode ? 'rgba(90,159,212,0.1)' : 'rgba(90,159,212,0.15)';
          } else {
            selectedLectures.delete(file.name);
            lectureCard.style.borderColor = cardBorder;
            lectureCard.style.background = cardBg;
          }
        };
        
        const info = document.createElement('div');
        info.style.cssText = 'flex:1;';
        
        const name = document.createElement('div');
        name.textContent = file.name;
        name.style.cssText = `font-size:16px; font-weight:600; color:${textColor}; margin-bottom:4px;`;
        
        const count = document.createElement('div');
        count.textContent = `${file.totalCards} flashcard${file.totalCards !== 1 ? 's' : ''}`;
        count.style.cssText = `font-size:13px; color:${textSecondary};`;
        
        info.appendChild(name);
        info.appendChild(count);
        
        lectureCard.appendChild(checkbox);
        lectureCard.appendChild(info);
        lectureCard.onclick = (e) => {
          if (e.target !== checkbox) {
            checkbox.checked = !checkbox.checked;
            checkbox.dispatchEvent(new Event('change'));
          }
        };
        
        lectureList.appendChild(lectureCard);
      });
      
      leftScrollContent.appendChild(lectureList);
      leftColumn.appendChild(leftScrollContent);
      
      // Right column - Settings
      const rightColumn = document.createElement('div');
      rightColumn.style.cssText = `width:320px; display:flex; flex-direction:column; background:${isLightMode ? '#fafafa' : '#242426'};`;
      
      const rightScrollContent = document.createElement('div');
      rightScrollContent.className = 'smooth-scroll';
      rightScrollContent.style.cssText = `flex:1; overflow-y:auto; padding:24px;`;
      
      // Settings section
      const settingsSection = document.createElement('div');
      settingsSection.style.cssText = `width:100%;`;
      
      const settingsTitle = document.createElement('div');
      settingsTitle.textContent = 'Quiz Settings';
      settingsTitle.style.cssText = `font-size:18px; font-weight:600; color:${textColor}; margin-bottom:20px;`;
      
      // Card count selection
      const countLabel = document.createElement('div');
      countLabel.textContent = 'Number of Cards';
      countLabel.style.cssText = `font-size:14px; font-weight:500; color:${textColor}; margin-bottom:12px;`;
      
      const countOptions = document.createElement('div');
      countOptions.style.cssText = 'display:flex; flex-direction:column; gap:10px; margin-bottom:24px;';
      
      const allCardsOption = document.createElement('label');
      allCardsOption.style.cssText = `display:flex; align-items:center; gap:10px; cursor:pointer; padding:10px; border-radius:8px; transition:all 0.2s;`;
      const allCardsRadio = document.createElement('input');
      allCardsRadio.type = 'radio';
      allCardsRadio.name = 'cardCount';
      allCardsRadio.value = 'all';
      allCardsRadio.checked = true;
      allCardsRadio.style.cssText = 'accent-color:#5a9fd4; cursor:pointer;';
      const allCardsText = document.createElement('span');
      allCardsText.textContent = 'All cards';
      allCardsText.style.cssText = `color:${textColor}; font-size:14px;`;
      allCardsOption.appendChild(allCardsRadio);
      allCardsOption.appendChild(allCardsText);
      allCardsOption.onmouseover = () => allCardsOption.style.background = isLightMode ? 'rgba(0,0,0,0.03)' : 'rgba(255,255,255,0.05)';
      allCardsOption.onmouseout = () => allCardsOption.style.background = 'transparent';
      
      const customCountOption = document.createElement('label');
      customCountOption.style.cssText = `display:flex; align-items:center; gap:10px; cursor:pointer; padding:10px; border-radius:8px; transition:all 0.2s;`;
      const customCountRadio = document.createElement('input');
      customCountRadio.type = 'radio';
      customCountRadio.name = 'cardCount';
      customCountRadio.value = 'custom';
      customCountRadio.style.cssText = 'accent-color:#5a9fd4; cursor:pointer;';
      const customCountText = document.createElement('span');
      customCountText.textContent = 'Custom amount:';
      customCountText.style.cssText = `color:${textColor}; font-size:14px;`;
      const customCountInput = document.createElement('input');
      customCountInput.type = 'number';
      customCountInput.min = '1';
      customCountInput.value = '20';
      customCountInput.style.cssText = `width:80px; padding:6px 10px; background:${bgColor}; border:1px solid ${borderColor}; border-radius:6px; color:${textColor}; font-size:14px;`;
      customCountInput.disabled = true;
      
      // When clicking on input field, switch to custom mode
      customCountInput.onfocus = () => {
        if (!customCountRadio.checked) {
          customCountRadio.checked = true;
          customCountInput.disabled = false;
          allCardsRadio.checked = false;
        }
      };
      
      customCountRadio.onchange = () => {
        customCountInput.disabled = !customCountRadio.checked;
        if (customCountRadio.checked) {
          setTimeout(() => customCountInput.focus(), 10);
        }
      };
      allCardsRadio.onchange = () => {
        customCountInput.disabled = !customCountRadio.checked;
      };
      customCountOption.appendChild(customCountRadio);
      customCountOption.appendChild(customCountText);
      customCountOption.appendChild(customCountInput);
      customCountOption.onmouseover = () => customCountOption.style.background = isLightMode ? 'rgba(0,0,0,0.03)' : 'rgba(255,255,255,0.05)';
      customCountOption.onmouseout = () => customCountOption.style.background = 'transparent';
      
      countOptions.appendChild(allCardsOption);
      countOptions.appendChild(customCountOption);
      
      settingsSection.appendChild(settingsTitle);
      settingsSection.appendChild(countLabel);
      settingsSection.appendChild(countOptions);
      
      rightScrollContent.appendChild(settingsSection);
      rightColumn.appendChild(rightScrollContent);
      
      mainContent.appendChild(leftColumn);
      mainContent.appendChild(rightColumn);
      
      const actionBar = document.createElement('div');
      actionBar.style.cssText = `padding:20px 30px; border-top:1px solid ${borderColor}; background:${isLightMode ? '#fafafa' : '#242426'}; display:flex; justify-content:space-between; align-items:center; gap:12px;`;
      
      const selectedCount = document.createElement('div');
      selectedCount.style.cssText = `font-size:14px; color:${textSecondary};`;
      function updateSelectedCount() {
        const count = selectedLectures.size;
        selectedCount.textContent = count > 0 ? `${count} lecture${count !== 1 ? 's' : ''} selected` : 'No lectures selected';
      }
      updateSelectedCount();
      
      const startQuizBtn = document.createElement('button');
      startQuizBtn.textContent = 'Start Quiz';
      startQuizBtn.style.cssText = 'padding:12px 32px; background:#5a9fd4; border:none; color:#fff; border-radius:10px; cursor:pointer; font-size:15px; font-weight:600; transition:all 0.2s;';
      startQuizBtn.onmouseover = () => startQuizBtn.style.background = '#4a8fc4';
      startQuizBtn.onmouseout = () => startQuizBtn.style.background = '#5a9fd4';
      startQuizBtn.onclick = async () => {
        if (selectedLectures.size === 0) {
          alert('Please select at least one lecture');
          return;
        }
        
        // Collect all flashcards from selected lectures with lecture grouping
        const allCards = [];
        const lectureGroups = [];
        let currentIdx = 0;
        
        for (const fileName of selectedLectures) {
          const fileData = await window.pywebview.api.get_library_file(fileName);
          if (fileData && fileData.ok && fileData.data && fileData.data.flashcards) {
            const lectureCards = [];
            const pages = Object.keys(fileData.data.flashcards).map(Number).sort((a,b)=>a-b);
            for (const page of pages) {
              const cards = fileData.data.flashcards[page];
              if (Array.isArray(cards) && cards.length > 0) {
                lectureCards.push(...cards);
              }
            }
            if (lectureCards.length > 0) {
              lectureGroups.push({
                name: fileName,
                cards: [...lectureCards],
                startIdx: currentIdx
              });
              allCards.push(...lectureCards);
              currentIdx += lectureCards.length;
            }
          }
        }
        
        if (allCards.length === 0) {
          alert('No flashcards found in selected lectures');
          return;
        }
        
        // Apply card count limit if custom
        let finalCards = [...allCards];
        let finalGroups = lectureGroups.length > 1 ? [...lectureGroups] : null;
        
        if (customCountRadio.checked) {
          const maxCount = parseInt(customCountInput.value) || 20;
          if (maxCount < allCards.length) {
            // Shuffle and take first N
            const shuffled = [...allCards];
            for (let i = shuffled.length - 1; i > 0; i--) {
              const j = Math.floor(Math.random() * (i + 1));
              [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
            }
            finalCards = shuffled.slice(0, maxCount);
            // Recalculate groups (simplified - just mark as mixed)
            finalGroups = null; // Don't show groups if we're using a subset
          }
        }
        
        // Shuffle cards (default is random)
        const shuffledCards = [...finalCards];
        for (let i = shuffledCards.length - 1; i > 0; i--) {
          const j = Math.floor(Math.random() * (i + 1));
          [shuffledCards[i], shuffledCards[j]] = [shuffledCards[j], shuffledCards[i]];
        }
        
        modal.remove();
        const lectureNames = Array.from(selectedLectures).join(', ');
        openInteractiveFlashcardViewer(shuffledCards, `Quiz: ${lectureNames}`, null, {
          cardOrder: 'random',
          lectureGroups: finalGroups,
          maxCards: customCountRadio.checked ? parseInt(customCountInput.value) : null
        });
      };
      
      // Update selected count when checkboxes change
      const observer = new MutationObserver(() => updateSelectedCount());
      lectureList.querySelectorAll('input[type="checkbox"]').forEach(cb => {
        cb.addEventListener('change', updateSelectedCount);
      });
      
      actionBar.appendChild(selectedCount);
      actionBar.appendChild(startQuizBtn);
      
      content.appendChild(header);
      content.appendChild(mainContent);
      content.appendChild(actionBar);
      modal.appendChild(content);
      document.body.appendChild(modal);
      
      requestAnimationFrame(() => {
        modal.style.opacity = '1';
        content.style.transform = 'scale(1)';
      });
      
      // Close on Escape
      const handleEscape = (e) => {
        if (e.key === 'Escape') {
          modal.style.opacity = '0';
          content.style.transform = 'scale(0.95)';
          setTimeout(() => modal.remove(), 200);
          document.removeEventListener('keydown', handleEscape);
        }
      };
      document.addEventListener('keydown', handleEscape);
      
    } catch (e) {
      alert('Error: ' + e.message);
      console.error(e);
    }
  }
  
  // Transform library modal into quizzer mode
  async function openQuizzer() {
    try {
      const libraryModal = document.getElementById('libraryModal');
      const libraryContent = document.getElementById('libraryContent');
      const libraryHeader = document.getElementById('libraryHeader');
      const libraryHeaderTitle = libraryHeader.querySelector('h1');
      const libraryQuizzerSettings = document.getElementById('libraryQuizzerSettings');
      
      // Check if already in quizzer mode - if so, exit quizzer mode
      if (libraryModal.classList.contains('quizzer-mode')) {
        exitQuizzerMode();
        return;
      }
      
      // If library modal is not open, open it first
      if (!libraryModal.classList.contains('show')) {
        await openLibrary();
        // Wait a bit for library to render
        await new Promise(resolve => setTimeout(resolve, 300));
      }
      
      if (!window.pywebview || !window.pywebview.api) {
        alert('Study Mode: bridge not available.');
        return;
      }
      
      const isLightMode = document.body.dataset.theme === 'light' || document.body.classList.contains('light-mode');
      const bgColor = isLightMode ? '#ffffff' : '#1a1a1c';
      const borderColor = isLightMode ? '#e0e0e0' : '#404040';
      const textColor = isLightMode ? '#1a1a1c' : '#f0f0f2';
      
      // Add quizzer mode class to library modal
      libraryModal.classList.add('quizzer-mode');
      
      // Hide hint text
      const hint = libraryHeader.querySelector('.libraryClickHint');
      if (hint) {
        hint.style.opacity = '0';
        hint.style.pointerEvents = 'none';
      }
      
      // Transform header title with animation
      libraryHeaderTitle.style.transition = 'all 0.4s cubic-bezier(0.34, 1.56, 0.64, 1)';
      libraryHeaderTitle.textContent = 'Select Lectures to Study';
      
      // Update button text and style
      const quizzerBtn = document.getElementById('openQuizzer');
      if (quizzerBtn) {
        quizzerBtn.textContent = 'Exit Study Mode';
        quizzerBtn.style.background = 'rgba(90, 159, 212, 0.2)';
        quizzerBtn.style.borderColor = 'rgba(90, 159, 212, 0.4)';
        quizzerBtn.style.color = 'rgba(90, 159, 212, 1)';
      }
      
      // Build settings panel
      libraryQuizzerSettings.innerHTML = '';
      libraryQuizzerSettings.style.display = 'block';
      
      const settingsTitle = document.createElement('div');
      settingsTitle.textContent = 'Study Settings';
      settingsTitle.style.cssText = `font-size:20px; font-weight:600; color:${textColor}; margin-bottom:24px;`;
      
      const countLabel = document.createElement('div');
      countLabel.textContent = 'Number of Cards';
      countLabel.style.cssText = `font-size:14px; font-weight:500; color:${textColor}; margin-bottom:12px;`;
      
      const countOptions = document.createElement('div');
      countOptions.style.cssText = 'display:flex; flex-direction:column; gap:10px; margin-bottom:24px;';
      
      const allCardsOption = document.createElement('label');
      allCardsOption.style.cssText = `display:flex; align-items:center; gap:10px; cursor:pointer; padding:10px; border-radius:8px; transition:all 0.2s;`;
      const allCardsRadio = document.createElement('input');
      allCardsRadio.type = 'radio';
      allCardsRadio.name = 'cardCount';
      allCardsRadio.value = 'all';
      allCardsRadio.checked = true;
      allCardsRadio.style.cssText = 'accent-color:#5a9fd4; cursor:pointer;';
      const allCardsText = document.createElement('span');
      allCardsText.textContent = 'All cards';
      allCardsText.style.cssText = `color:${textColor}; font-size:14px;`;
      allCardsOption.appendChild(allCardsRadio);
      allCardsOption.appendChild(allCardsText);
      
      const customCountOption = document.createElement('label');
      customCountOption.style.cssText = `display:flex; align-items:center; gap:10px; cursor:pointer; padding:10px; border-radius:8px; transition:all 0.2s;`;
      const customCountRadio = document.createElement('input');
      customCountRadio.type = 'radio';
      customCountRadio.name = 'cardCount';
      customCountRadio.value = 'custom';
      customCountRadio.style.cssText = 'accent-color:#5a9fd4; cursor:pointer;';
      const customCountText = document.createElement('span');
      customCountText.textContent = 'Custom amount:';
      customCountText.style.cssText = `color:${textColor}; font-size:14px;`;
      const customCountInput = document.createElement('input');
      customCountInput.type = 'number';
      customCountInput.min = '1';
      customCountInput.value = '20';
      customCountInput.style.cssText = `width:80px; padding:6px 10px; background:${bgColor}; border:1px solid ${borderColor}; border-radius:6px; color:${textColor}; font-size:14px;`;
      customCountInput.disabled = true;
      customCountInput.onfocus = () => {
        if (!customCountRadio.checked) {
          customCountRadio.checked = true;
          customCountInput.disabled = false;
          allCardsRadio.checked = false;
        }
      };
      customCountRadio.onchange = () => {
        customCountInput.disabled = !customCountRadio.checked;
        if (customCountRadio.checked) {
          setTimeout(() => customCountInput.focus(), 10);
        }
      };
      allCardsRadio.onchange = () => {
        customCountInput.disabled = !customCountRadio.checked;
      };
      customCountOption.appendChild(customCountRadio);
      customCountOption.appendChild(customCountText);
      customCountOption.appendChild(customCountInput);
      
      countOptions.appendChild(allCardsOption);
      countOptions.appendChild(customCountOption);
      
      const selectedItems = new Set();
      
      // Function to sync checkbox state between search UI and library UI
      const syncCheckboxState = (fileName, checked) => {
        // Update all checkboxes with matching fileName
        document.querySelectorAll('.libraryFileQuizzerCheckbox, .libraryQuizzerSearchResultCheckbox').forEach(cb => {
          if (cb.dataset.fileName === fileName) {
            cb.checked = checked;
          }
        });
      };
      
      // Get all lectures for search
      const result = await window.pywebview.api.list_library_files();
      const allFiles = result.files || [];
      const folders = result.folders || {};
      
      // Initialize Fuse.js for main search
      const allLectures = allFiles.map(f => ({ name: f.name }));
      let mainFuse = new Fuse(allLectures, {
        keys: ['name'],
        threshold: 0.3,
        includeScore: true,
        includeMatches: true
      });
      
      // Setup main search bar
      const libraryQuizzerSearch = document.getElementById('libraryQuizzerSearch');
      const libraryQuizzerSearchInput = document.getElementById('libraryQuizzerSearchInput');
      const libraryQuizzerSearchResults = document.getElementById('libraryQuizzerSearchResults');
      
      // Show main search bar
      if (libraryQuizzerSearch) {
        libraryQuizzerSearch.style.display = 'block';
      }
      
      libraryQuizzerSearchInput.addEventListener('input', (e) => {
        const query = e.target.value.trim();
        
        if (query === '') {
          libraryQuizzerSearchResults.style.display = 'none';
          return;
        }
        
        const results = mainFuse.search(query);
        libraryQuizzerSearchResults.innerHTML = '';
        
        if (results.length === 0) {
          libraryQuizzerSearchResults.style.display = 'block';
          const empty = document.createElement('div');
          empty.className = 'libraryQuizzerSearchResultItem';
          empty.textContent = 'No lectures found';
          empty.style.justifyContent = 'center';
          empty.style.color = 'rgba(255, 255, 255, 0.5)';
          libraryQuizzerSearchResults.appendChild(empty);
          return;
        }
        
        libraryQuizzerSearchResults.style.display = 'block';
        
        results.forEach(result => {
          const item = result.item;
          const matches = result.matches[0];
          
          const resultItem = document.createElement('div');
          resultItem.className = 'libraryQuizzerSearchResultItem';
          
          const checkbox = document.createElement('input');
          checkbox.type = 'checkbox';
          checkbox.className = 'libraryQuizzerSearchResultCheckbox';
          checkbox.dataset.fileName = item.name;
          checkbox.checked = selectedItems.has(item.name);
          
          const nameSpan = document.createElement('span');
          nameSpan.className = 'libraryQuizzerSearchResultName';
          
          // Highlight matched text - process from end to start to avoid index shifting
          let highlightedName = item.name;
          if (matches && matches.indices && matches.indices.length > 0) {
            // Sort indices by start position, then process from end to start
            const sortedIndices = [...matches.indices].sort((a, b) => a[0] - b[0]);
            // Process in reverse order to avoid index shifting
            for (let i = sortedIndices.length - 1; i >= 0; i--) {
              const [start, end] = sortedIndices[i];
              const before = highlightedName.substring(0, start);
              const match = highlightedName.substring(start, end + 1);
              const after = highlightedName.substring(end + 1);
              highlightedName = before + '<span class="highlight">' + match + '</span>' + after;
            }
          }
          nameSpan.innerHTML = highlightedName;
          
          checkbox.addEventListener('change', (e) => {
            const checked = e.target.checked;
            if (checked) {
              selectedItems.add(item.name);
            } else {
              selectedItems.delete(item.name);
            }
            syncCheckboxState(item.name, checked);
          });
          
          resultItem.appendChild(checkbox);
          resultItem.appendChild(nameSpan);
          libraryQuizzerSearchResults.appendChild(resultItem);
        });
      });
      
      // Close search results when clicking outside
      document.addEventListener('click', (e) => {
        if (!libraryQuizzerSearch.contains(e.target)) {
          libraryQuizzerSearchResults.style.display = 'none';
        }
      });
      
      const startQuizBtn = document.createElement('button');
      startQuizBtn.textContent = 'Start Study Session';
      startQuizBtn.style.cssText = 'width:100%; padding:14px; background:#5a9fd4; border:none; color:#fff; border-radius:10px; cursor:pointer; font-size:16px; font-weight:600; transition:all 0.2s; margin-top:24px;';
      startQuizBtn.onmouseover = () => startQuizBtn.style.background = '#4a8fc4';
      startQuizBtn.onmouseout = () => startQuizBtn.style.background = '#5a9fd4';
      startQuizBtn.onclick = async () => {
        if (selectedItems.size === 0) {
          alert('Please select at least one lecture or folder');
          return;
        }
        
        // Get folders data
        const result = await window.pywebview.api.list_library_files();
        const folders = result.folders || {};
        
        // Collect flashcards from selected items
        const allCards = [];
        const lectureGroups = [];
        let currentIdx = 0;
        
        for (const itemName of selectedItems) {
          if (folders[itemName]) {
            for (const fileName of folders[itemName]) {
              const fileData = await window.pywebview.api.get_library_file(fileName);
              if (fileData && fileData.ok && fileData.data && fileData.data.flashcards) {
                const lectureCards = [];
                const pages = Object.keys(fileData.data.flashcards).map(Number).sort((a,b)=>a-b);
                for (const page of pages) {
                  const cards = fileData.data.flashcards[page];
                  if (Array.isArray(cards) && cards.length > 0) {
                    lectureCards.push(...cards);
                  }
                }
                if (lectureCards.length > 0) {
                  lectureGroups.push({
                    name: fileName,
                    cards: [...lectureCards],
                    startIdx: currentIdx
                  });
                  allCards.push(...lectureCards);
                  currentIdx += lectureCards.length;
                }
              }
            }
          } else {
            const fileData = await window.pywebview.api.get_library_file(itemName);
            if (fileData && fileData.ok && fileData.data && fileData.data.flashcards) {
              const lectureCards = [];
              const pages = Object.keys(fileData.data.flashcards).map(Number).sort((a,b)=>a-b);
              for (const page of pages) {
                const cards = fileData.data.flashcards[page];
                if (Array.isArray(cards) && cards.length > 0) {
                  lectureCards.push(...cards);
                }
              }
              if (lectureCards.length > 0) {
                lectureGroups.push({
                  name: itemName,
                  cards: [...lectureCards],
                  startIdx: currentIdx
                });
                allCards.push(...lectureCards);
                currentIdx += lectureCards.length;
              }
            }
          }
        }
        
        if (allCards.length === 0) {
          alert('No flashcards found in selected items');
          return;
        }
        
        let finalCards = [...allCards];
        let finalGroups = lectureGroups.length > 1 ? [...lectureGroups] : null;
        
        if (customCountRadio.checked) {
          const maxCount = parseInt(customCountInput.value) || 20;
          if (maxCount < allCards.length) {
            const shuffled = [...allCards];
            for (let i = shuffled.length - 1; i > 0; i--) {
              const j = Math.floor(Math.random() * (i + 1));
              [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
            }
            finalCards = shuffled.slice(0, maxCount);
            finalGroups = null;
          }
        }
        
        const shuffledCards = [...finalCards];
        for (let i = shuffledCards.length - 1; i > 0; i--) {
          const j = Math.floor(Math.random() * (i + 1));
          [shuffledCards[i], shuffledCards[j]] = [shuffledCards[j], shuffledCards[i]];
        }
        
        // Exit quizzer mode and start quiz
        exitQuizzerMode();
        
        const lectureNames = Array.from(selectedItems).join(', ');
        openInteractiveFlashcardViewer(shuffledCards, `Study: ${lectureNames}`, null, {
          cardOrder: 'random',
          lectureGroups: finalGroups,
          maxCards: customCountRadio.checked ? parseInt(customCountInput.value) : null
        });
      };
      
      libraryQuizzerSettings.appendChild(settingsTitle);
      libraryQuizzerSettings.appendChild(countLabel);
      libraryQuizzerSettings.appendChild(countOptions);
      libraryQuizzerSettings.appendChild(startQuizBtn);
      
      // Add checkboxes to existing lectures and folders
      const addCheckboxesToItems = () => {
        // Add checkboxes to folders
        libraryContent.querySelectorAll('.libraryFolder').forEach(folderDiv => {
          if (folderDiv.querySelector('.libraryFolderQuizzerCheckbox')) return; // Already has checkbox
          
          folderDiv.classList.add('quizzer-selectable');
          const folderName = folderDiv.dataset.folderName;
          const folderHeader = folderDiv.querySelector('.libraryFolderHeader');
          
          if (folderHeader) {
            // Check if checkbox already exists
            let checkbox = folderDiv.querySelector('.libraryFolderQuizzerCheckbox');
            if (!checkbox) {
              checkbox = document.createElement('input');
              checkbox.type = 'checkbox';
              checkbox.className = 'libraryFolderQuizzerCheckbox';
              checkbox.style.cssText = 'width:20px; height:20px; cursor:pointer; accent-color:#5a9fd4; flex-shrink:0;';
              folderDiv.appendChild(checkbox);
            }
            
            checkbox.onchange = () => {
              if (checkbox.checked) {
              selectedItems.add(folderName);
                folderDiv.classList.add('quizzer-selected');
                // Check all items in folder
                folderDiv.querySelectorAll('.libraryFileQuizzerCheckbox').forEach(cb => {
                  cb.checked = true;
                  const fileName = cb.dataset.fileName;
                  if (fileName) {
                    selectedItems.add(fileName);
                    syncCheckboxState(fileName, true);
                    const item = cb.closest('.libraryFolderItems > div');
                    if (item) {
                      item.style.borderColor = '#5a9fd4';
                      item.style.background = isLightMode ? 'rgba(90,159,212,0.1)' : 'rgba(90,159,212,0.15)';
                    }
                  }
                });
            } else {
              selectedItems.delete(folderName);
                folderDiv.classList.remove('quizzer-selected');
                // Uncheck all items in folder
                folderDiv.querySelectorAll('.libraryFileQuizzerCheckbox').forEach(cb => {
                  cb.checked = false;
                  const fileName = cb.dataset.fileName;
                  if (fileName) {
                    selectedItems.delete(fileName);
                    syncCheckboxState(fileName, false);
                    const item = cb.closest('.libraryFolderItems > div');
                    if (item) {
                      item.style.borderColor = '';
                      item.style.background = isLightMode ? 'rgba(0,0,0,0.03)' : 'rgba(255,255,255,0.02)';
                    }
                  }
                });
              }
            };
            
            // Add click handler to folder div to toggle selection when clicking anywhere on folder
            // (but not on items inside the folder)
            folderDiv.onclick = (e) => {
              // Don't handle clicks on items inside the folder
              if (e.target.closest('.libraryFolderItems')) {
                return;
              }
              // Don't handle clicks on the checkbox itself (it has its own handler)
              if (e.target === checkbox || e.target.closest('input[type="checkbox"]')) {
                return;
              }
              // Don't handle clicks on buttons or interactive elements
              if (e.target.closest('button') || e.target.closest('.libraryEditIcon') || e.target.closest('.libraryFavorite')) {
                return;
              }
              
              // Toggle checkbox
              checkbox.checked = !checkbox.checked;
              checkbox.dispatchEvent(new Event('change'));
            };
          }
          
          // Add checkboxes to items inside folders
          folderDiv.querySelectorAll('.libraryFolderItems > div').forEach(item => {
            if (item.querySelector('.libraryFileQuizzerCheckbox')) return;
            if (item.classList.contains('libraryFolderEmpty')) return; // Skip empty message
            
            // Get fileName from the text content or data attribute
            let fileName = item.textContent.trim();
            // Try to get from link if it exists
            const link = item.querySelector('a');
            if (link && link.textContent) {
              fileName = link.textContent.trim();
            }
            if (!fileName) return;
            
            // Store original onclick to preserve it
            const originalOnclick = item.onclick;
            
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.className = 'libraryFileQuizzerCheckbox';
            checkbox.dataset.fileName = fileName;
            checkbox.style.cssText = 'width:18px; height:18px; cursor:pointer; accent-color:#5a9fd4; flex-shrink:0;';
            
            checkbox.onchange = (e) => {
                  e.stopPropagation();
              if (checkbox.checked) {
                    selectedItems.add(fileName);
                syncCheckboxState(fileName, true);
                    item.style.borderColor = '#5a9fd4';
                    item.style.background = isLightMode ? 'rgba(90,159,212,0.1)' : 'rgba(90,159,212,0.15)';
                  } else {
                    selectedItems.delete(fileName);
                syncCheckboxState(fileName, false);
                    item.style.borderColor = '';
                item.style.background = isLightMode ? 'rgba(0,0,0,0.03)' : 'rgba(255,255,255,0.02)';
                // Uncheck folder checkbox if any item is unchecked
                const folderCheckbox = folderDiv.querySelector('.libraryFolderQuizzerCheckbox');
                if (folderCheckbox) {
                  folderCheckbox.checked = false;
                  folderDiv.classList.remove('quizzer-selected');
                  selectedItems.delete(folderDiv.dataset.folderName);
                }
              }
            };
            
            // Insert checkbox at the beginning
            item.style.position = 'relative';
            item.style.display = 'flex';
            item.style.alignItems = 'center';
            item.style.gap = '8px';
            // Ensure padding-left is set for proper spacing
            if (!item.style.paddingLeft || item.style.paddingLeft === '') {
              item.style.paddingLeft = '36px';
            }
            item.insertBefore(checkbox, item.firstChild);
            
            // Preserve original click handler but prevent it when clicking checkbox
            if (originalOnclick) {
                item.onclick = (e) => {
                if (e.target === checkbox || e.target.closest('input')) {
                  return; // Let checkbox handle it
                }
                originalOnclick(e);
              };
            } else {
              // If no original handler, toggle checkbox on click
              item.onclick = (e) => {
                if (e.target !== checkbox && !e.target.closest('input')) {
                  checkbox.checked = !checkbox.checked;
                  checkbox.dispatchEvent(new Event('change'));
                }
              };
            }
          });
        });
        
        // Add checkboxes to lecture files
        libraryContent.querySelectorAll('.libraryFile').forEach(fileDiv => {
          // Skip if already has checkbox or is inside a folder
          if (fileDiv.querySelector('.libraryFileQuizzerCheckbox')) return;
          if (fileDiv.closest('.libraryFolderItems')) return; // Skip files inside folders (handled above)
          
          fileDiv.classList.add('quizzer-selectable');
          const fileNameEl = fileDiv.querySelector('.libraryFileName');
          const fileName = fileNameEl ? fileNameEl.textContent.trim() : null;
          
          if (fileName) {
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.className = 'libraryFileQuizzerCheckbox';
            checkbox.dataset.fileName = fileName;
            checkbox.style.cssText = 'width:20px; height:20px; cursor:pointer; accent-color:#5a9fd4; flex-shrink:0;';
            
            checkbox.onchange = () => {
              if (checkbox.checked) {
                selectedItems.add(fileName);
                syncCheckboxState(fileName, true);
                fileDiv.classList.add('quizzer-selected');
              } else {
                selectedItems.delete(fileName);
                syncCheckboxState(fileName, false);
                fileDiv.classList.remove('quizzer-selected');
              }
            };
            
            fileDiv.style.position = 'relative';
            fileDiv.style.display = 'flex';
            fileDiv.style.alignItems = 'center';
            fileDiv.style.gap = '12px';
            
            // Insert checkbox at the beginning
            const fileHeader = fileDiv.querySelector('.libraryFileHeader');
            if (fileHeader) {
              fileDiv.insertBefore(checkbox, fileHeader);
          } else {
              fileDiv.insertBefore(checkbox, fileDiv.firstChild);
            }
          }
        });
      };
      
      // Wait for content to be rendered, then add checkboxes
      setTimeout(() => {
        addCheckboxesToItems();
        
        // Animate settings panel sliding in with elegant bounce
        requestAnimationFrame(() => {
          libraryQuizzerSettings.style.transform = 'translateX(0)';
        });
      }, 150);
      
    } catch(e) {
      alert('Error opening study mode: ' + e.message);
      console.error(e);
    }
  }
  
  // Function to exit quizzer mode and return to normal library view
  function exitQuizzerMode() {
    const libraryModal = document.getElementById('libraryModal');
    const libraryContent = document.getElementById('libraryContent');
    const libraryHeader = document.getElementById('libraryHeader');
    const libraryHeaderTitle = libraryHeader.querySelector('h1');
    const libraryQuizzerSettings = document.getElementById('libraryQuizzerSettings');
    
    // Remove quizzer mode class
    libraryModal.classList.remove('quizzer-mode');
    
    // Restore header title
    libraryHeaderTitle.textContent = 'Your Library';
    
    // Restore button text and style
    const quizzerBtn = document.getElementById('openQuizzer');
    if (quizzerBtn) {
      quizzerBtn.textContent = 'Study Mode';
      quizzerBtn.style.background = '';
      quizzerBtn.style.borderColor = '';
      quizzerBtn.style.color = '';
    }
    
    // Hide search bar
    const libraryQuizzerSearch = document.getElementById('libraryQuizzerSearch');
    const libraryQuizzerSearchInput = document.getElementById('libraryQuizzerSearchInput');
    const libraryQuizzerSearchResults = document.getElementById('libraryQuizzerSearchResults');
    if (libraryQuizzerSearch) {
      libraryQuizzerSearch.style.display = 'none';
    }
    if (libraryQuizzerSearchInput) {
      libraryQuizzerSearchInput.value = '';
    }
    if (libraryQuizzerSearchResults) {
      libraryQuizzerSearchResults.style.display = 'none';
      libraryQuizzerSearchResults.innerHTML = '';
    }
    
    // Restore hint text
    const hint = libraryHeader.querySelector('.libraryClickHint');
    if (hint) {
      hint.style.opacity = '';
      hint.style.pointerEvents = '';
    }
    
    // Hide settings panel with animation
    libraryQuizzerSettings.style.transform = 'translateX(100%)';
    setTimeout(() => {
      libraryQuizzerSettings.style.display = 'none';
    }, 400);
    
    // Remove checkboxes and restore normal click behavior
    libraryContent.querySelectorAll('.libraryFileQuizzerCheckbox, .libraryFolderQuizzerCheckbox').forEach(cb => cb.remove());
    libraryContent.querySelectorAll('.libraryFile, .libraryFolder').forEach(el => {
      el.classList.remove('quizzer-selectable', 'quizzer-selected');
      el.style.borderColor = '';
      el.style.background = '';
      el.style.paddingLeft = '';
      el.style.display = '';
      el.style.alignItems = '';
      el.style.gap = '';
      el.style.position = '';
    });
    // Also restore items inside folders
    libraryContent.querySelectorAll('.libraryFolderItems > div').forEach(item => {
      item.style.borderColor = '';
      item.style.background = '';
      item.style.display = '';
      item.style.alignItems = '';
      item.style.gap = '';
      item.style.position = '';
    });
    
    // Clear search results in folder search bars
    libraryContent.querySelectorAll('.folderSearchResults').forEach(results => {
      results.style.display = 'none';
      results.innerHTML = '';
    });
    libraryContent.querySelectorAll('.folderSearchInput').forEach(input => {
      input.value = '';
    });
    
    // Reload library to restore normal state
    setTimeout(() => {
      openLibrary();
    }, 200);
  }
  
  document.getElementById('openQuizzer').onclick = openQuizzer;

  function openFullFlashcardsView(fileName, data) {
    fullScreenFileName = fileName;
    fullScreenData = data;
    
    const content = document.getElementById('libraryContent');
    content.innerHTML = '';
    const wrap = document.createElement('div');
    wrap.style.cssText = 'max-width: 900px; margin: 0 auto;';
    
    // Header with back and edit button
    const header = document.createElement('div');
    header.style.cssText = 'display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px;';
    
    const backBtn = document.createElement('button');
    backBtn.textContent = '← Back to Library';
    backBtn.style.cssText = 'padding: 8px 16px; background: #2a2a2c; border: 1px solid #404040; color: #e8e8ea; border-radius: 6px; cursor: pointer;';
    backBtn.onclick = openLibrary;
    
    const editBtn = document.createElement('button');
    editBtn.id = 'editFlashcardsModeBtn';
    editBtn.textContent = '✏️ Edit';
    editBtn.style.cssText = 'padding: 8px 16px; background: rgba(90, 159, 212, 0.2); border: 1px solid rgba(90, 159, 212, 0.4); color: rgba(90, 159, 212, 0.9); border-radius: 6px; cursor: pointer; font-size: 13px;';
    editBtn.onclick = toggleFlashcardsEditMode;
    
    header.appendChild(backBtn);
    header.appendChild(editBtn);
    
    const title = document.createElement('h2');
    title.style.cssText = 'font-size: 28px; margin-bottom: 24px; color: #f0f0f2;';
    title.textContent = `${fileName} - All Flashcards`;
    const fcContent = document.createElement('div');
    fcContent.id = 'fullFlashcardsContent';
    wrap.appendChild(header);
    wrap.appendChild(title);
    wrap.appendChild(fcContent);
    content.appendChild(wrap);

    renderFullFlashcardsContent(fcContent, data);
  }
  
  let isFlashcardsEditMode = false;
  
  function toggleFlashcardsEditMode() {
    isFlashcardsEditMode = !isFlashcardsEditMode;
    const editBtn = document.getElementById('editFlashcardsModeBtn');
    const fcContent = document.getElementById('fullFlashcardsContent');
    
    if (isFlashcardsEditMode) {
      editBtn.textContent = '✓ Done';
      editBtn.style.background = 'rgba(76, 175, 80, 0.2)';
      editBtn.style.borderColor = 'rgba(76, 175, 80, 0.4)';
      editBtn.style.color = 'rgba(76, 175, 80, 0.9)';
    } else {
      editBtn.textContent = '✏️ Edit';
      editBtn.style.background = 'rgba(90, 159, 212, 0.2)';
      editBtn.style.borderColor = 'rgba(90, 159, 212, 0.4)';
      editBtn.style.color = 'rgba(90, 159, 212, 0.9)';
    }
    
    renderFullFlashcardsContent(fcContent, fullScreenData);
  }
  
  function renderFullFlashcardsContent(container, data) {
    container.innerHTML = '';

    if (data.flashcards && Object.keys(data.flashcards).length > 0) {
      const pages = Object.keys(data.flashcards).map(Number).sort((a,b)=>a-b);
      let total = 0;
      for (const page of pages) {
        const cards = data.flashcards[page];
        if (!Array.isArray(cards) || cards.length===0) continue;
        
        const pageHeader = document.createElement('h3');
        pageHeader.style.cssText = 'font-size: 16px; color: #d0d0d2; margin: 24px 0 12px 0;';
        pageHeader.textContent = `Page ${page}`;
        container.appendChild(pageHeader);
        
        cards.forEach((card, cardIdx) => {
          total++;
          const cardDiv = document.createElement('div');
          cardDiv.style.cssText = 'background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.1); border-radius: 8px; padding: 16px; margin-bottom: 12px; position: relative;';
          
          if (isFlashcardsEditMode) {
            cardDiv.style.border = '2px solid rgba(90, 159, 212, 0.4)';
            
            const qInput = document.createElement('textarea');
            qInput.value = card.q || '';
            qInput.placeholder = 'Question';
            qInput.style.cssText = 'width: 100%; background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); border-radius: 4px; padding: 8px; color: #f0f0f2; font-size: 14px; font-weight: 600; margin-bottom: 8px; font-family: inherit; resize: vertical; min-height: 40px;';
            qInput.style.outline = 'none';
            
            const aInput = document.createElement('textarea');
            aInput.value = card.a || '';
            aInput.placeholder = 'Answer';
            aInput.style.cssText = 'width: 100%; background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); border-radius: 4px; padding: 8px; color: #d8d8da; font-size: 14px; margin-bottom: 8px; font-family: inherit; resize: vertical; min-height: 60px;';
            aInput.style.outline = 'none';
            
            const deleteBtn = document.createElement('button');
            deleteBtn.textContent = '🗑️';
            deleteBtn.style.cssText = 'position: absolute; top: 8px; right: 8px; width: 28px; height: 28px; padding: 0; background: rgba(255, 59, 48, 0.15); border: 1px solid rgba(255, 59, 48, 0.3); color: rgba(255, 59, 48, 0.9); border-radius: 6px; cursor: pointer; font-size: 14px;';
            deleteBtn.onclick = async () => {
              if (confirm('Delete this flashcard?')) {
                cards.splice(cardIdx, 1);
                if (cards.length === 0) {
                  delete data.flashcards[page];
                  pagesWithLibraryData.delete(page);
                  delete cardsByPage[page];
                } else {
                  // Update in-memory cache
                  cardsByPage[page] = cards;
                }
                
                // Update sidebar if this is the current page
                if (currentPage === page) {
                  refreshCards(page);
                  console.log('✓ Updated sidebar flashcards for page', page);
                }
                
                // Save to backend
                if (window.pywebview && window.pywebview.api) {
                  try {
                    const libraryData = {
                      summaries: fullScreenData.summaries || {},
                      flashcards: fullScreenData.flashcards,
                      lastModified: new Date().toISOString()
                    };
                    await window.pywebview.api.save_library_data(fullScreenFileName, libraryData);
                    console.log('✓ Saved flashcard deletion to library for page', page);
                  } catch(e) {
                    console.error('Error saving flashcard deletion:', e);
                  }
                }
                
                renderFullFlashcardsContent(container, data);
              }
            };
            
            const saveFlashcard = async () => {
              const newQ = qInput.value.trim();
              const newA = aInput.value.trim();
              
              if (newQ !== (card.q || '') || newA !== (card.a || '')) {
                card.q = newQ;
                card.a = newA;
                
                // Update in-memory cache
                if (!cardsByPage[page]) {
                  cardsByPage[page] = [];
                }
                cardsByPage[page] = cards;
                
                // Update sidebar if this is the current page
                if (currentPage === page) {
                  refreshCards(page);
                  console.log('✓ Updated sidebar flashcards for page', page);
                }
                
                // Save to backend
                if (window.pywebview && window.pywebview.api) {
                  try {
                    const libraryData = {
                      summaries: fullScreenData.summaries || {},
                      flashcards: fullScreenData.flashcards,
                      lastModified: new Date().toISOString()
                    };
                    await window.pywebview.api.save_library_data(fullScreenFileName, libraryData);
                    console.log('✓ Saved flashcard update to library for page', page);
                  } catch(e) {
                    console.error('Error saving flashcard update:', e);
                  }
                }
              }
            };
            
            qInput.addEventListener('blur', saveFlashcard);
            aInput.addEventListener('blur', saveFlashcard);
            
            cardDiv.appendChild(qInput);
            cardDiv.appendChild(aInput);
            cardDiv.appendChild(deleteBtn);
          } else {
          cardDiv.innerHTML = `<div style=\"font-size:14px;font-weight:600;color:#f0f0f2;margin-bottom:8px;\">Q: ${escapeHtml(card.q||'')}</div><div style=\"font-size:14px;color:#d8d8da;line-height:1.6;\">A: ${escapeHtml(card.a||'')}</div>`;
        }
          
          container.appendChild(cardDiv);
        });
      }
      if (total===0) container.innerHTML = '<div style="text-align: center; padding: 40px; color: #a0a0a2;">No flashcards available</div>';
    } else {
      container.innerHTML = '<div style="text-align: center; padding: 40px; color: #a0a0a2;">No flashcards available</div>';
    }
  }
  
  async function saveFlashcardsUpdate(page, cardIdx) {
    // Update in-memory cache
    if (fullScreenData && fullScreenData.flashcards && fullScreenData.flashcards[page]) {
      cardsByPage[page] = fullScreenData.flashcards[page];
      
      // Update sidebar if this is the current page
      if (currentPage === page) {
        refreshCards(page);
        console.log('✓ Updated sidebar flashcards for page', page);
      }
      
      // Save to backend
      if (window.pywebview && window.pywebview.api) {
        try {
          const libraryData = {
            summaries: fullScreenData.summaries || {},
            flashcards: fullScreenData.flashcards,
            lastModified: new Date().toISOString()
          };
          await window.pywebview.api.save_library_data(fullScreenFileName, libraryData);
          console.log('✓ Saved flashcard deletion to library for page', page);
        } catch(e) {
          console.error('Error saving flashcard deletion:', e);
        }
      }
    }
  }

  async function downloadSummary(fileName, data) {
    try {
      if (!window.pywebview || !window.pywebview.api) { alert('Download: bridge not available.'); return; }
      const result = await window.pywebview.api.download_summary(fileName, data);
      if (result && result.ok) alert(`Summary downloaded to:\n${result.path}`); else alert('Error: ' + (result?.error||'Unknown'));
    } catch(e){ alert('Error: ' + e.message); }
  }

  async function downloadFlashcards(fileName, data) {
    try {
      if (!window.pywebview || !window.pywebview.api) { alert('Download: bridge not available.'); return; }
      const result = await window.pywebview.api.download_flashcards_anki(fileName, data);
      if (result && result.ok) alert(`Flashcards downloaded to:\n${result.path}\n\nImport into Anki with tab-separated fields.`); else alert('Error: ' + (result?.error||'Unknown'));
    } catch(e){ alert('Error: ' + e.message); }
  }

  function closeLibrary() {
    const libraryModal = document.getElementById('libraryModal');
    // If in quizzer mode, exit quizzer mode instead of closing
    if (libraryModal.classList.contains('quizzer-mode')) {
      exitQuizzerMode();
    } else {
      libraryModal.classList.remove('show');
    }
  }
  
  document.getElementById('libraryClose').onclick = closeLibrary;
  
  // ---- Mindmap Modal Functions ----
  let currentMindmapData = null;
  let currentMindmapFileName = null;
  let mindmapZoom = 1;
  let mindmapPanX = 0;
  let mindmapPanY = 0;
  
  function openMindmapModal(fileName, data) {
    currentMindmapData = data;
    currentMindmapFileName = fileName;
    mindmapZoom = 1;
    mindmapPanX = 0;
    mindmapPanY = 0;
    isFullscreen = true; // Start in fullscreen mode
    
    // Initialize zoom slider
    const zoomSlider = document.getElementById('mindmapZoomSlider');
    if (zoomSlider) {
      zoomSlider.value = 1;
    }
    
    const mindmapModal = document.getElementById('mindmapModal');
    const mindmapHeader = mindmapModal.querySelector('#mindmapHeader h1');
    const mindmapStatus = document.getElementById('mindmapStatusText');
    const mindmapEmpty = document.getElementById('mindmapEmpty');
    const mindmapLoading = document.getElementById('mindmapLoading');
    const mindmapCanvas = document.getElementById('mindmapCanvas');
    const mindmapContainer = document.getElementById('mindmapContainer');
    
    // Update header with file name
    mindmapHeader.innerHTML = 'Study Mindmap - ' + escapeHtml(fileName);
    
      // Show modal in fullscreen mode by default
      mindmapModal.classList.add('show');
      mindmapModal.classList.add('fullscreen');
      document.getElementById('mindmapFullscreen').textContent = '⛶';
      document.getElementById('mindmapFullscreen').title = 'True Fullscreen';
    
    // Try to generate preview if data exists
    if (data && data.summaries && Object.keys(data.summaries).length > 0) {
      // Auto-generate preview
      generateMindmap();
    } else {
      // Reset status
      mindmapStatus.textContent = 'Ready to generate mindmap';
      
      // Show empty state
      mindmapEmpty.classList.remove('hidden');
      mindmapLoading.classList.remove('show');
      
      // Clear canvas
      if (mindmapCanvas) {
        mindmapCanvas.innerHTML = '';
      }
    }
  }
  
  function closeMindmapModal() {
    document.getElementById('mindmapModal').classList.remove('show');
    currentMindmapData = null;
    currentMindmapFileName = null;
  }
  
  let mindmapData = null;
  let collapsedNodes = new Set();
  let mindmapType = 'radial';
  let nodePositions = {}; // Store custom node positions
  let isFullscreen = false;
  
  function toggleFullscreen() {
    const modal = document.getElementById('mindmapModal');
    const isTrueFullscreen = modal.classList.contains('trueFullscreen');
    
    if (isTrueFullscreen) {
      // Exit true fullscreen - go back to modal fullscreen
      modal.classList.remove('trueFullscreen');
      document.getElementById('mindmapFullscreen').textContent = '⛶';
      document.getElementById('mindmapFullscreen').title = 'True Fullscreen';
    } else {
      // Enter true fullscreen - hide all UI
      modal.classList.add('trueFullscreen');
      document.getElementById('mindmapFullscreen').textContent = '⇱';
      document.getElementById('mindmapFullscreen').title = 'Exit True Fullscreen';
    }
    
    // Re-render to adjust sizing
    if (mindmapData) {
      setTimeout(() => {
        const svg = document.getElementById('mindmapCanvas');
        renderMindmap(svg, mindmapData, mindmapType);
      }, 100);
    }
  }
  
  // Escape key to exit true fullscreen
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
      const modal = document.getElementById('mindmapModal');
      if (modal && modal.classList.contains('trueFullscreen')) {
        toggleFullscreen();
      }
    }
  });
  
  async function generateMindmap() {
    if (!currentMindmapData || !currentMindmapFileName) {
      alert('No document selected for mindmap generation');
      return;
    }
    
    const mindmapLoading = document.getElementById('mindmapLoading');
    const mindmapEmpty = document.getElementById('mindmapEmpty');
    const mindmapStatus = document.getElementById('mindmapStatusText');
    const mindmapCanvas = document.getElementById('mindmapCanvas');
    
    if (!mindmapCanvas) {
      alert('Mindmap canvas not found. Please try again.');
      return;
    }
    
    // Show loading state
    mindmapEmpty.classList.add('hidden');
    mindmapLoading.classList.add('show');
    mindmapStatus.textContent = 'Generating mindmap structure...';
    
    try {
      if (!window.pywebview || !window.pywebview.api) {
        throw new Error('API not available');
      }
      
      const result = await window.pywebview.api.generate_mindmap(currentMindmapFileName);
      
      if (!result || !result.ok) {
        throw new Error(result?.error || 'Failed to generate mindmap');
      }
      
      mindmapData = result.mindmap;
      // Start with all branches collapsed (only center visible)
      collapsedNodes.clear();
      if (mindmapData && mindmapData.branches) {
        mindmapData.branches.forEach(branch => {
          if (branch && branch.id) {
            collapsedNodes.add(branch.id);
          }
        });
      }
      nodePositions = {}; // Reset positions
      
      // Update zoom slider min/max based on data
      const zoomSlider = document.getElementById('mindmapZoomSlider');
      if (zoomSlider && mindmapData && mindmapData.branches) {
        const maxNodes = Math.max(mindmapData.branches.length, 1);
        const minZoom = Math.max(0.5, 1.0 / (1 + maxNodes * 0.1));
        zoomSlider.min = minZoom.toString();
        zoomSlider.max = '3';
        zoomSlider.value = '1'; // Reset to default
      }
      
      // Get selected type
      const typeSelect = document.getElementById('mindmapTypeSelectEmpty') || document.getElementById('mindmapTypeSelect');
      mindmapType = typeSelect ? typeSelect.value : 'radial';
      
      mindmapStatus.textContent = 'Rendering mindmap...';
      
      // Show type selector in toolbar
      document.getElementById('mindmapTypeSelector').style.display = 'flex';
      document.getElementById('mindmapExportBtn').style.display = 'block';
      
      // Render the mindmap
      console.log('[DEBUG generateMindmap] Calling renderMindmap...');
      renderMindmap(mindmapCanvas, mindmapData, mindmapType);
      console.log('[DEBUG generateMindmap] Render complete');
      
      // Update transform after render to ensure zoom/pan is applied
      updateMindmapTransform();
      
      mindmapLoading.classList.remove('show');
      mindmapStatus.textContent = 'Mindmap ready - Drag nodes to customize';
      mindmapEmpty.classList.add('hidden');
      
    } catch (error) {
      console.error('[DEBUG generateMindmap] Error caught:', error);
      console.error('[DEBUG generateMindmap] Error message:', error.message);
      console.error('[DEBUG generateMindmap] Error stack:', error.stack);
      mindmapLoading.classList.remove('show');
      mindmapStatus.textContent = 'Error: ' + error.message;
      alert('Failed to generate mindmap: ' + error.message + '\n\nCheck browser console (F12) for detailed logs.');
    }
  }
  
  function renderMindmap(svg, data, type = 'radial') {
    try {
      console.log('[DEBUG renderMindmap] Starting render, type:', type);
      if (!svg) {
        console.error('[DEBUG renderMindmap] SVG element is null');
        return;
      }
      
      if (!data) {
        console.error('[DEBUG renderMindmap] Data is null');
        return;
      }
      
      console.log('[DEBUG renderMindmap] Data structure:', {
        hasCentralNode: !!data.central_node,
        branchesCount: data.branches ? data.branches.length : 0,
        type: type
      });
      
      // Clear previous content
      svg.innerHTML = '';
    
    // Get actual container dimensions
    const container = svg.parentElement;
    if (!container) {
      console.error('SVG container not found');
      return;
    }
    const width = container.clientWidth || window.innerWidth * 0.9;
    const height = container.clientHeight || window.innerHeight * 0.7;
    const centerX = width / 2;
    const centerY = height / 2;
    
    // Set viewBox for proper scaling - use larger dimensions for better rendering
    const viewBoxWidth = Math.max(width, 1600);
    const viewBoxHeight = Math.max(height, 1000);
    svg.setAttribute('viewBox', '0 0 ' + viewBoxWidth + ' ' + viewBoxHeight);
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.setAttribute('width', '100%');
    svg.setAttribute('height', '100%');
    
    // Add defs with filters and gradients (must be first)
    const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
    
    // Shadow filter
    const filter = document.createElementNS('http://www.w3.org/2000/svg', 'filter');
    filter.setAttribute('id', 'shadow');
    filter.setAttribute('x', '-50%');
    filter.setAttribute('y', '-50%');
    filter.setAttribute('width', '200%');
    filter.setAttribute('height', '200%');
    const feDropShadow = document.createElementNS('http://www.w3.org/2000/svg', 'feDropShadow');
    feDropShadow.setAttribute('dx', '0');
    feDropShadow.setAttribute('dy', '4');
    feDropShadow.setAttribute('stdDeviation', '8');
    feDropShadow.setAttribute('flood-opacity', '0.3');
    filter.appendChild(feDropShadow);
    defs.appendChild(filter);
    
    // Central node gradient
    const centralGrad = document.createElementNS('http://www.w3.org/2000/svg', 'linearGradient');
    centralGrad.setAttribute('id', 'centralGrad');
    centralGrad.setAttribute('x1', '0%');
    centralGrad.setAttribute('y1', '0%');
    centralGrad.setAttribute('x2', '100%');
    centralGrad.setAttribute('y2', '100%');
    const stop1 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
    stop1.setAttribute('offset', '0%');
    stop1.setAttribute('stop-color', '#667eea');
    const stop2 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
    stop2.setAttribute('offset', '100%');
    stop2.setAttribute('stop-color', '#764ba2');
    centralGrad.appendChild(stop1);
    centralGrad.appendChild(stop2);
    defs.appendChild(centralGrad);
    
    svg.appendChild(defs);
    
    const root = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    root.setAttribute('id', 'mindmapRoot');
    root.setAttribute('transform', 'translate(' + mindmapPanX + ', ' + mindmapPanY + ') scale(' + mindmapZoom + ')');
    svg.appendChild(root);
    
    // Draw central node
    const centralNode = data.central_node;
    const centralRadius = 80;
    const centralBg = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    centralBg.setAttribute('cx', centerX);
    centralBg.setAttribute('cy', centerY);
    centralBg.setAttribute('r', centralRadius);
    centralBg.setAttribute('fill', 'url(#centralGrad)');
    centralBg.setAttribute('stroke', '#764ba2');
    centralBg.setAttribute('stroke-width', '3');
    centralBg.setAttribute('filter', 'url(#shadow)');
    centralBg.setAttribute('class', 'mindmap-node mindmap-central');
    root.appendChild(centralBg);
    
    // Central node text
    const centralText = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    centralText.setAttribute('x', centerX);
    centralText.setAttribute('y', centerY);
    centralText.setAttribute('text-anchor', 'middle');
    centralText.setAttribute('dominant-baseline', 'middle');
    centralText.setAttribute('fill', '#ffffff');
    centralText.setAttribute('font-size', '18');
    centralText.setAttribute('font-weight', '600');
    centralText.setAttribute('font-family', 'var(--font-family)');
    centralText.textContent = escapeHtml(centralNode.label).substring(0, 30);
    root.appendChild(centralText);
    
      // Calculate node positions based on layout type
      console.log('[DEBUG renderMindmap] Calculating positions, type:', type);
      const branches = data.branches || [];
      console.log('[DEBUG renderMindmap] Branches array:', branches);
      let nodePositionsCalc = {};
      
      if (type === 'radial') {
      // Radial layout - circular arrangement around center
      const angleStep = (2 * Math.PI) / Math.max(branches.length, 1);
      branches.forEach((branch, idx) => {
        const angle = idx * angleStep - Math.PI / 2;
        const branchDistance = 220;
        const key = branch.id;
        if (!nodePositions[key]) {
          nodePositions[key] = {
            x: centerX + Math.cos(angle) * branchDistance,
            y: centerY + Math.sin(angle) * branchDistance
          };
        }
        nodePositionsCalc[key] = nodePositions[key];
      });
    } else if (type === 'tree') {
      // Tree layout - hierarchical top-to-bottom with branches
      const level1Count = Math.ceil(branches.length / 2);
      const spacingX = 280;
      const level1Y = 120;
      const level2Y = 280;
      
      // First pass: set all first-level positions
      branches.forEach((branch, idx) => {
        const key = branch.id;
        if (idx < level1Count) {
          if (!nodePositions[key]) {
            nodePositions[key] = {
              x: centerX + (idx - (level1Count - 1) / 2) * spacingX,
              y: level1Y
            };
          }
          nodePositionsCalc[key] = nodePositions[key];
        }
      });
      
      // Second pass: set second-level positions (after first level is calculated)
      branches.forEach((branch, idx) => {
        if (!branch || !branch.id) return; // Safety check
        const key = branch.id;
        if (idx >= level1Count) {
          if (!nodePositions[key]) {
            // Second level - positioned below corresponding first level
            const parentIdx = idx - level1Count;
            let parentPos = null;
            if (parentIdx >= 0 && parentIdx < branches.length && branches[parentIdx] && branches[parentIdx].id) {
              const parentId = branches[parentIdx].id;
              parentPos = nodePositionsCalc[parentId] || null;
            }
            nodePositions[key] = {
              x: parentPos ? parentPos.x : centerX + ((idx - level1Count) - (level1Count - 1) / 2) * spacingX,
              y: level2Y
            };
          }
          nodePositionsCalc[key] = nodePositions[key];
        }
      });
    } else if (type === 'brace') {
      // Brace layout - vertical left-to-right expansion with horizontal alignment
      const spacingY = 140;
      const startX = 280;
      const centerOffset = (branches.length - 1) * spacingY / 2;
      
      branches.forEach((branch, idx) => {
        const key = branch.id;
        if (!nodePositions[key]) {
          nodePositions[key] = {
            x: startX,
            y: centerY - centerOffset + idx * spacingY
          };
        }
        nodePositionsCalc[key] = nodePositions[key];
      });
    } else if (type === 'flow') {
      // Flow layout - sequential diagonal cascade
      const spacingX = 320;
      const spacingY = 100;
      const startX = 180;
      const startY = centerY - (branches.length - 1) * spacingY / 2;
      
      branches.forEach((branch, idx) => {
        const key = branch.id;
        if (!nodePositions[key]) {
          // Create diagonal flow pattern
          nodePositions[key] = {
            x: startX + idx * spacingX,
            y: startY + idx * spacingY + Math.sin(idx * 0.5) * 30 // Slight wave
          };
        }
        nodePositionsCalc[key] = nodePositions[key];
      });
    }
    
      // Draw branches
      console.log('[DEBUG renderMindmap] Drawing branches, count:', branches.length);
      branches.forEach((branch, idx) => {
        try {
          if (!branch || !branch.id) {
            console.warn('[DEBUG renderMindmap] Skipping invalid branch at index', idx);
            return;
          }
          const branchPos = nodePositionsCalc[branch.id] || { x: centerX, y: centerY };
          const branchX = branchPos.x;
          const branchY = branchPos.y;
      
          // Use simplified accent color (ensure it's always defined)
          const accentColor = (branch && branch.accent) ? branch.accent : '#667eea';
          
          // Draw curved connector line from center to branch (using quadratic curve)
          const midX = (centerX + branchX) / 2;
          const midY = (centerY + branchY) / 2;
          // Add curve offset perpendicular to line direction
          const dx = branchX - centerX;
          const dy = branchY - centerY;
          const len = Math.sqrt(dx * dx + dy * dy);
          const perpX = -dy / len * 30; // Curve amount
          const perpY = dx / len * 30;
          const curveX = midX + perpX;
          const curveY = midY + perpY;
          
          const line = document.createElementNS('http://www.w3.org/2000/svg', 'path');
          const pathData = `M ${centerX} ${centerY} Q ${curveX} ${curveY} ${branchX} ${branchY}`;
          line.setAttribute('d', pathData);
          line.setAttribute('fill', 'none');
          line.setAttribute('stroke', accentColor);
          line.setAttribute('stroke-width', '2.5');
          line.setAttribute('opacity', '0.5');
          line.setAttribute('class', 'mindmap-connector');
          line.style.transition = 'opacity 0.2s ease';
          // Insert line BEFORE nodes so it's behind them
          root.insertBefore(line, root.firstChild);
          
          // Enhanced branch node with image support
          const branchGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
          branchGroup.setAttribute('class', 'mindmap-branch-group');
          branchGroup.setAttribute('data-node-id', branch.id);
          
          const branchRadius = 60;
          
          // Add image pattern if available
          if (branch.page_image) {
            const imagePattern = document.createElementNS('http://www.w3.org/2000/svg', 'pattern');
            imagePattern.setAttribute('id', `branch-pattern-${branch.id}`);
            imagePattern.setAttribute('x', '0');
            imagePattern.setAttribute('y', '0');
            imagePattern.setAttribute('width', '1');
            imagePattern.setAttribute('height', '1');
            imagePattern.setAttribute('patternUnits', 'objectBoundingBox');
            
            const patternImage = document.createElementNS('http://www.w3.org/2000/svg', 'image');
            patternImage.setAttribute('href', branch.page_image);
            patternImage.setAttribute('x', '0');
            patternImage.setAttribute('y', '0');
            patternImage.setAttribute('width', branchRadius * 2);
            patternImage.setAttribute('height', branchRadius * 2);
            patternImage.setAttribute('preserveAspectRatio', 'xMidYMid slice');
            imagePattern.appendChild(patternImage);
            defs.appendChild(imagePattern);
          }
          
          const branchCircle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
          branchCircle.setAttribute('cx', branchX);
          branchCircle.setAttribute('cy', branchY);
          branchCircle.setAttribute('r', branchRadius);
          // Simplified colors - same for all branches, accent for stroke
          branchCircle.setAttribute('fill', branch.page_image ? `url(#branch-pattern-${branch.id})` : '#f5f5f7');
          branchCircle.setAttribute('stroke', accentColor);
          branchCircle.setAttribute('stroke-width', '3');
          branchCircle.setAttribute('filter', 'url(#shadow)');
          branchCircle.setAttribute('class', 'mindmap-node mindmap-branch');
          branchCircle.setAttribute('data-node-id', branch.id);
          branchCircle.setAttribute('data-collapsed', collapsedNodes.has(branch.id) ? 'true' : 'false');
          if (branch.page_image) {
            branchCircle.setAttribute('opacity', '0.9');
          }
          branchCircle.style.cursor = 'pointer';
          branchCircle.style.transition = 'r 0.2s ease, stroke-width 0.2s ease, opacity 0.2s ease';
          
          // Add overlay for text readability if image present
          if (branch.page_image) {
            const overlay = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
            overlay.setAttribute('cx', branchX);
            overlay.setAttribute('cy', branchY);
            overlay.setAttribute('r', branchRadius);
            overlay.setAttribute('fill', 'rgba(0, 0, 0, 0.3)');
            branchGroup.appendChild(overlay);
          }
          
          // Simple hover effect - just subtle color change (no movement/scale)
          branchCircle.setAttribute('data-original-stroke', accentColor); // Store original for hover
          branchCircle.addEventListener('mouseenter', function() {
            const originalStroke = this.getAttribute('data-original-stroke') || accentColor;
            // Convert hex to rgba and make slightly lighter
            if (originalStroke.startsWith('#')) {
              const r = parseInt(originalStroke.slice(1, 3), 16);
              const g = parseInt(originalStroke.slice(3, 5), 16);
              const b = parseInt(originalStroke.slice(5, 7), 16);
              this.setAttribute('stroke', `rgba(${r}, ${g}, ${b}, 0.8)`); // Slightly lighter
            } else {
              this.setAttribute('stroke', originalStroke);
            }
            if (branch.page_image) {
              this.setAttribute('opacity', '1');
            }
          });
          branchCircle.addEventListener('mouseleave', function() {
            const originalStroke = this.getAttribute('data-original-stroke') || accentColor;
            this.setAttribute('stroke', originalStroke); // Back to original
            if (branch.page_image) {
              this.setAttribute('opacity', '0.9');
            }
          });
          
          // Click anywhere on node (including text) to toggle collapse
          const nodeClickHandler = function(e) {
            e.stopPropagation();
            toggleBranchCollapse(branch.id);
          };
          branchCircle.addEventListener('click', nodeClickHandler);
          
          // Make node draggable
          makeNodeDraggable(branchCircle, branch.id, branchX, branchY);
          
          branchGroup.appendChild(branchCircle);
          
          // Enhanced branch text with dynamic sizing and better contrast
          const labelLength = branch.label.length;
          // Dynamic font size based on label length and node size
          const fontSize = Math.max(11, Math.min(16, Math.round(branchRadius * 0.25 - labelLength * 0.3)));
          // Calculate text contrast - use accent color darkened for readability on light background
          const textColor = '#1a1a1a'; // Dark text on light background for better contrast
          
          const branchText = document.createElementNS('http://www.w3.org/2000/svg', 'text');
          branchText.setAttribute('x', branchX);
          branchText.setAttribute('y', branchY);
          branchText.setAttribute('text-anchor', 'middle');
          branchText.setAttribute('dominant-baseline', 'middle');
          branchText.setAttribute('fill', textColor);
          branchText.setAttribute('font-size', fontSize);
          branchText.setAttribute('font-weight', '600');
          branchText.setAttribute('font-family', 'var(--font-family)');
          branchText.setAttribute('class', 'mindmap-branch-text');
          branchText.setAttribute('data-node-id', branch.id);
          branchText.style.cursor = 'pointer'; // Allow clicking on text
          branchText.style.pointerEvents = 'all'; // Enable pointer events
          // Truncate based on available space (rough estimate: ~8px per char)
          const maxChars = Math.floor((branchRadius * 2) / 8);
          branchText.textContent = escapeHtml(branch.label).substring(0, maxChars);
          branchText.addEventListener('click', nodeClickHandler); // Also handle text clicks
          
          // Make text editable on double-click
          let isEditingBranch = false;
          branchCircle.addEventListener('dblclick', function(e) {
            e.stopPropagation();
            if (isEditingBranch) return;
            
            isEditingBranch = true;
            const originalText = branch.label;
            const foreignObject = document.createElementNS('http://www.w3.org/2000/svg', 'foreignObject');
            foreignObject.setAttribute('x', branchX - 60);
            foreignObject.setAttribute('y', branchY - 12);
            foreignObject.setAttribute('width', '120');
            foreignObject.setAttribute('height', '24');
            
            const input = document.createElement('input');
            input.type = 'text';
            input.value = originalText;
            input.style.cssText = 'width: 100%; height: 100%; border: 2px solid rgba(90, 159, 212, 0.8); border-radius: 6px; padding: 4px 8px; font-size: 14px; font-weight: 700; font-family: var(--font-family); outline: none; background: white; color: #1a1a1a; text-align: center;';
            input.focus();
            input.select();
            
            const finishEdit = () => {
              const newText = input.value.trim();
              if (newText && newText !== originalText) {
                branchText.textContent = escapeHtml(newText).substring(0, 20);
                if (mindmapData && branch) {
                  branch.label = newText;
                }
              }
              foreignObject.remove();
              branchText.style.display = '';
              isEditingBranch = false;
            };
            
            input.addEventListener('blur', finishEdit);
            input.addEventListener('keydown', function(e) {
              if (e.key === 'Enter') {
                e.preventDefault();
                finishEdit();
              } else if (e.key === 'Escape') {
                e.preventDefault();
                foreignObject.remove();
                branchText.style.display = '';
                isEditingBranch = false;
              }
            });
            
            branchText.style.display = 'none';
            foreignObject.appendChild(input);
            root.appendChild(foreignObject);
          });
          
          branchGroup.appendChild(branchText);
          root.appendChild(branchGroup);
          
          // Draw subnodes if not collapsed
          console.log('[DEBUG renderMindmap] Branch', branch.id, 'collapsed:', collapsedNodes.has(branch.id));
          if (!collapsedNodes.has(branch.id) && branch && branch.subnodes && Array.isArray(branch.subnodes) && branch.subnodes.length > 0) {
            console.log('[DEBUG renderMindmap] Drawing subnodes for branch', branch.id, 'count:', branch.subnodes.length);
            branch.subnodes.forEach((subnode, subIdx) => {
              try {
                if (!subnode || !subnode.id) {
                  console.warn('[DEBUG renderMindmap] Skipping invalid subnode at index', subIdx);
                  return;
                }
                // Calculate subnode position relative to branch
                const subnodeKey = subnode.id;
                let subX, subY;
                
                if (type === 'radial') {
                  // Radial: subnodes in arc around branch
                  const angleStep = (2 * Math.PI) / Math.max(branches.length, 1);
                  const branchAngle = idx * angleStep - Math.PI / 2;
                  const subAngle = branchAngle + (subIdx - (branch.subnodes.length - 1) / 2) * 0.4;
                  const subDistance = 160;
                  if (!nodePositions[subnodeKey]) {
                    nodePositions[subnodeKey] = {
                      x: branchX + Math.cos(subAngle) * subDistance,
                      y: branchY + Math.sin(subAngle) * subDistance
                    };
                  }
                  const subPos = nodePositions[subnodeKey];
                  subX = subPos.x;
                  subY = subPos.y;
                } else if (type === 'tree') {
                  // Tree: subnodes stacked vertically below branch
                  const subOffsetY = 140;
                  const subSpacing = 90;
                  if (!nodePositions[subnodeKey]) {
                    nodePositions[subnodeKey] = {
                      x: branchX,
                      y: branchY + subOffsetY + subIdx * subSpacing
                    };
                  }
                  const subPos = nodePositions[subnodeKey];
                  subX = subPos.x;
                  subY = subPos.y;
                } else if (type === 'brace') {
                  // Brace: subnodes extend horizontally to the right
                  const subOffsetX = 180;
                  const subOffsetY = (subIdx - (branch.subnodes.length - 1) / 2) * 85;
                  if (!nodePositions[subnodeKey]) {
                    nodePositions[subnodeKey] = {
                      x: branchX + subOffsetX,
                      y: branchY + subOffsetY
                    };
                  }
                  const subPos = nodePositions[subnodeKey];
                  subX = subPos.x;
                  subY = subPos.y;
                } else { // flow
                  // Flow: subnodes cascade diagonally
                  const subOffsetX = 100;
                  const subOffsetY = (subIdx - (branch.subnodes.length - 1) / 2) * 95 + subIdx * 20;
                  if (!nodePositions[subnodeKey]) {
                    nodePositions[subnodeKey] = {
                      x: branchX + subOffsetX + subIdx * 15,
                      y: branchY + subOffsetY
                    };
                  }
                  const subPos = nodePositions[subnodeKey];
                  subX = subPos.x;
                  subY = subPos.y;
                }
                
                // Curved subnode connector using same accent color (get from branch)
                const subAccentColor = (subnode && subnode.accent) ? subnode.accent : ((branch && branch.accent) ? branch.accent : '#667eea');
                const subMidX = (branchX + subX) / 2;
                const subMidY = (branchY + subY) / 2;
                const subDx = subX - branchX;
                const subDy = subY - branchY;
                const subLen = Math.sqrt(subDx * subDx + subDy * subDy);
                const subPerpX = subLen > 0 ? -subDy / subLen * 20 : 0;
                const subPerpY = subLen > 0 ? subDx / subLen * 20 : 0;
                const subCurveX = subMidX + subPerpX;
                const subCurveY = subMidY + subPerpY;
                
                const subLine = document.createElementNS('http://www.w3.org/2000/svg', 'path');
                const subPathData = `M ${branchX} ${branchY} Q ${subCurveX} ${subCurveY} ${subX} ${subY}`;
                subLine.setAttribute('d', subPathData);
                subLine.setAttribute('fill', 'none');
                subLine.setAttribute('stroke', subAccentColor);
                subLine.setAttribute('stroke-width', '2');
                subLine.setAttribute('opacity', '0.35');
                subLine.setAttribute('class', 'mindmap-connector');
                subLine.setAttribute('data-branch-id', branch.id);
                subLine.style.transition = 'opacity 0.3s ease';
                // Insert line BEFORE nodes so it's behind
                root.insertBefore(subLine, root.firstChild);
                
                // Enhanced subnode with image support
                const hasImage = subnode.image || subnode.page_image;
                const subWidth = hasImage ? 200 : 160;
                const subHeight = hasImage ? 120 : 70;
                
                // Create group for subnode
                const subnodeGroup = document.createElementNS('http://www.w3.org/2000/svg', 'g');
                subnodeGroup.setAttribute('class', 'mindmap-subnode-group');
                subnodeGroup.setAttribute('data-node-id', subnodeKey);
                
                // Background rectangle with better styling
                const subRect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
                subRect.setAttribute('x', subX - subWidth / 2);
                subRect.setAttribute('y', subY - subHeight / 2);
                subRect.setAttribute('width', subWidth);
                subRect.setAttribute('height', subHeight);
                subRect.setAttribute('rx', '12');
                subRect.setAttribute('ry', '12');
                // Simplified colors - same for all subnodes, accent for stroke
                const subNodeAccent = (subnode && subnode.accent) ? subnode.accent : ((branch && branch.accent) ? branch.accent : '#667eea');
                subRect.setAttribute('fill', '#ffffff');
                subRect.setAttribute('stroke', subNodeAccent);
                subRect.setAttribute('stroke-width', '2');
                subRect.setAttribute('filter', 'url(#shadow)');
                subRect.setAttribute('class', 'mindmap-node mindmap-subnode');
                subRect.setAttribute('data-full-text', escapeHtml(subnode.full_text || subnode.label));
                subRect.setAttribute('data-subnode-type', subnode.type || 'subnode');
                subRect.setAttribute('data-branch-id', branch.id);
                subRect.style.cursor = 'pointer';
                subRect.style.transition = 'opacity 0.3s ease, transform 0.3s ease, stroke-width 0.2s ease';
                
                // Add image if available
                let imageElement = null;
                if (hasImage) {
                  imageElement = document.createElementNS('http://www.w3.org/2000/svg', 'image');
                  imageElement.setAttribute('href', subnode.image || subnode.page_image);
                  imageElement.setAttribute('x', subX - subWidth / 2 + 4);
                  imageElement.setAttribute('y', subY - subHeight / 2 + 4);
                  imageElement.setAttribute('width', subWidth - 8);
                  imageElement.setAttribute('height', (subWidth - 8) * 0.6); // 60% aspect ratio
                  imageElement.setAttribute('preserveAspectRatio', 'xMidYMid slice');
                  imageElement.setAttribute('rx', '8');
                  imageElement.style.opacity = '0.9';
                }
                
                // Text area (below image if present)
                const textY = hasImage ? subY + (subHeight / 2) - 25 : subY;
                const subText = document.createElementNS('http://www.w3.org/2000/svg', 'text');
                subText.setAttribute('x', subX);
                subText.setAttribute('y', textY);
                subText.setAttribute('text-anchor', 'middle');
                subText.setAttribute('dominant-baseline', 'middle');
                // Dynamic text sizing and contrast
                const subLabelLength = subnode.label.length;
                const subFontSize = Math.max(9, Math.min(12, Math.round(subWidth * 0.07 - subLabelLength * 0.15)));
                subText.setAttribute('fill', '#1a1a1a'); // Dark text for good contrast on white
                subText.setAttribute('font-size', hasImage ? Math.max(8, subFontSize - 1) : subFontSize);
                subText.setAttribute('font-weight', '500');
                subText.setAttribute('font-family', 'var(--font-family)');
                subText.setAttribute('class', 'mindmap-subnode-text');
                subText.setAttribute('data-node-id', subnodeKey);
                subText.setAttribute('data-branch-id', branch.id);
                subText.style.cursor = 'pointer'; // Allow clicking
                subText.style.pointerEvents = 'all'; // Enable pointer events
                
                // Truncate text for display based on available width
                const subMaxChars = Math.floor(subWidth / 7);
                subText.textContent = escapeHtml(subnode.label).substring(0, subMaxChars);
                
                // Simple hover effect - just subtle color change
                subRect.addEventListener('mouseenter', function(e) {
                  const rect = svg.getBoundingClientRect();
                  const svgPoint = svg.createSVGPoint();
                  svgPoint.x = e.clientX - rect.left;
                  svgPoint.y = e.clientY - rect.top;
                  const CTM = svg.getScreenCTM();
                  if (CTM) {
                    const invertedCTM = CTM.inverse();
                    const point = svgPoint.matrixTransform(invertedCTM);
                    
                    showTooltip(e.clientX, e.clientY - 40, this.getAttribute('data-full-text'));
                  }
                  // Subtle color change on hover (no movement)
                  const originalStroke = this.getAttribute('data-original-stroke') || subNodeAccent;
                  // Convert hex to rgba and make slightly lighter
                  if (originalStroke.startsWith('#')) {
                    const r = parseInt(originalStroke.slice(1, 3), 16);
                    const g = parseInt(originalStroke.slice(3, 5), 16);
                    const b = parseInt(originalStroke.slice(5, 7), 16);
                    this.setAttribute('stroke', `rgba(${r}, ${g}, ${b}, 0.85)`); // Slightly lighter
                  } else {
                    this.setAttribute('stroke', originalStroke);
                  }
                });
                
                subRect.addEventListener('mouseleave', function() {
                  hideTooltip();
                  const originalStroke = this.getAttribute('data-original-stroke') || subNodeAccent;
                  this.setAttribute('stroke', originalStroke); // Back to original
                });
                
                // Click handler for subnode (including text)
                const subnodeClickHandler = function(e) {
                  e.stopPropagation();
                  // Could toggle collapse or show details
                };
                subRect.addEventListener('click', subnodeClickHandler);
                subText.addEventListener('click', subnodeClickHandler);
                
                // Double-click to edit text
                let isEditing = false;
                subRect.addEventListener('dblclick', function(e) {
                  e.stopPropagation();
                  if (isEditing) return;
                  
                  isEditing = true;
                  const originalText = subnode.label;
                  const foreignObject = document.createElementNS('http://www.w3.org/2000/svg', 'foreignObject');
                  foreignObject.setAttribute('x', subX - subWidth / 2 + 8);
                  foreignObject.setAttribute('y', textY - 12);
                  foreignObject.setAttribute('width', subWidth - 16);
                  foreignObject.setAttribute('height', '24');
                  
                  const input = document.createElement('input');
                  input.type = 'text';
                  input.value = originalText;
                  input.style.cssText = 'width: 100%; height: 100%; border: 2px solid rgba(90, 159, 212, 0.8); border-radius: 6px; padding: 4px 8px; font-size: 11px; font-weight: 500; font-family: var(--font-family); outline: none; background: white; color: #1a1a1a;';
                  input.focus();
                  input.select();
                  
                  const finishEdit = () => {
                    const newText = input.value.trim();
                    if (newText && newText !== originalText) {
                      subText.textContent = escapeHtml(newText).substring(0, subMaxChars);
                      // TODO: Save to backend
                      if (mindmapData && subnode) {
                        subnode.label = newText;
                        subnode.full_text = newText;
                      }
                    }
                    foreignObject.remove();
                    subText.style.display = '';
                    isEditing = false;
                  };
                  
                  input.addEventListener('blur', finishEdit);
                  input.addEventListener('keydown', function(e) {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      finishEdit();
                    } else if (e.key === 'Escape') {
                      e.preventDefault();
                      foreignObject.remove();
                      subText.style.display = '';
                      isEditing = false;
                    }
                  });
                  
                  subText.style.display = 'none';
                  foreignObject.appendChild(input);
                  root.appendChild(foreignObject);
                });
                
                // Make draggable
                makeNodeDraggable(subRect, subnodeKey, subX, subY);
                
                // Assemble subnode group
                if (imageElement) {
                  subnodeGroup.appendChild(imageElement);
                }
                subnodeGroup.appendChild(subRect);
                subnodeGroup.appendChild(subText);
                root.appendChild(subnodeGroup);
              } catch (subnodeError) {
                console.error('[DEBUG renderMindmap] Error processing subnode', subIdx, ':', subnodeError);
                console.error('[DEBUG renderMindmap] Subnode error stack:', subnodeError.stack);
              }
            });
          }
        } catch (branchError) {
          console.error('[DEBUG renderMindmap] Error processing branch', idx, ':', branchError);
          console.error('[DEBUG renderMindmap] Branch error stack:', branchError.stack);
        }
      });
    
      // Make canvas draggable
      console.log('[DEBUG renderMindmap] Setting up panning...');
      setupPanning(svg, root);
      console.log('[DEBUG renderMindmap] Render complete successfully');
    } catch (error) {
      console.error('[DEBUG renderMindmap] Error during render:', error);
      console.error('[DEBUG renderMindmap] Error message:', error.message);
      console.error('[DEBUG renderMindmap] Error stack:', error.stack);
      throw error; // Re-throw to be caught by generateMindmap
    }
  }
  
  function toggleBranchCollapse(branchId) {
    const wasCollapsed = collapsedNodes.has(branchId);
    if (wasCollapsed) {
      collapsedNodes.delete(branchId);
    } else {
      collapsedNodes.add(branchId);
    }
    
    if (mindmapData) {
      const svg = document.getElementById('mindmapCanvas');
      // Add smooth animation by transitioning opacity
      const branchGroup = svg.querySelector(`[data-node-id="${branchId}"]`);
      if (branchGroup && !wasCollapsed) {
        // Collapsing - fade out subnodes first
        const subnodes = branchGroup.parentElement.querySelectorAll(`[data-branch-id="${branchId}"]`);
        subnodes.forEach(subnode => {
          subnode.style.transition = 'opacity 0.3s ease-out, transform 0.3s ease-out';
          subnode.style.opacity = '0';
          subnode.style.transform = 'scale(0.8)';
        });
        setTimeout(() => {
          renderMindmap(svg, mindmapData, mindmapType);
        }, 300);
      } else {
        renderMindmap(svg, mindmapData, mindmapType);
        // Expanding - fade in subnodes after render
        setTimeout(() => {
          const newSubnodes = svg.querySelectorAll(`[data-branch-id="${branchId}"]`);
          newSubnodes.forEach((subnode, idx) => {
            subnode.style.transition = 'opacity 0.3s ease-out, transform 0.3s ease-out';
            subnode.style.opacity = '0';
            subnode.style.transform = 'scale(0.8)';
            setTimeout(() => {
              subnode.style.opacity = '1';
              subnode.style.transform = 'scale(1)';
            }, idx * 50);
          });
        }, 50);
      }
    }
  }
  
  let isDragging = false;
  let draggedNode = null;
  let draggedNodeId = null;
  let draggedNodeText = null;
  let draggedNodeLine = null;
  
  function makeNodeDraggable(nodeElement, nodeId, initialX, initialY) {
    if (!nodePositions[nodeId]) {
      nodePositions[nodeId] = { x: initialX, y: initialY };
    }
    
    // Find associated text element (could be before or after)
    const svg = nodeElement.ownerSVGElement;
    if (!svg) {
      console.error('SVG element not found for node:', nodeId);
      return;
    }
    
    let textElement = null;
    const allElements = Array.from(svg.querySelectorAll('text'));
    allElements.forEach(text => {
      const textX = parseFloat(text.getAttribute('x'));
      const textY = parseFloat(text.getAttribute('y'));
      const nodeX = parseFloat(nodeElement.getAttribute('cx') || nodeElement.getAttribute('x'));
      const nodeY = parseFloat(nodeElement.getAttribute('cy') || nodeElement.getAttribute('y'));
      if (Math.abs(textX - nodeX) < 10 && Math.abs(textY - nodeY) < 10) {
        textElement = text;
      }
    });
    
    let startX = 0, startY = 0;
    let connectorLines = [];
    
    nodeElement.addEventListener('mousedown', function(e) {
      e.stopPropagation();
      isDragging = true;
      draggedNode = nodeElement;
      draggedNodeId = nodeId;
      draggedNodeText = textElement;
      
      // Store initial mouse position in SVG coordinates
      const svgPoint = svg.createSVGPoint();
      svgPoint.x = e.clientX;
      svgPoint.y = e.clientY;
      const CTM = svg.getScreenCTM();
      if (CTM) {
        const invertedCTM = CTM.inverse();
        const pt = svgPoint.matrixTransform(invertedCTM);
        startX = (pt.x - mindmapPanX) / mindmapZoom;
        startY = (pt.y - mindmapPanY) / mindmapZoom;
      }
      
      // Find all connector lines connected to this node
      connectorLines = [];
      const lines = svg.querySelectorAll('line.mindmap-connector');
      const nodeX = parseFloat(nodeElement.getAttribute('cx') || nodeElement.getAttribute('x') || 0);
      const nodeY = parseFloat(nodeElement.getAttribute('cy') || nodeElement.getAttribute('y') || 0);
      lines.forEach(line => {
        const x2 = parseFloat(line.getAttribute('x2') || 0);
        const y2 = parseFloat(line.getAttribute('y2') || 0);
        if (Math.abs(x2 - nodeX) < 10 && Math.abs(y2 - nodeY) < 10) {
          connectorLines.push({ line, end: 'x2', endY: 'y2' });
        }
        const x1 = parseFloat(line.getAttribute('x1') || 0);
        const y1 = parseFloat(line.getAttribute('y1') || 0);
        if (Math.abs(x1 - nodeX) < 10 && Math.abs(y1 - nodeY) < 10) {
          connectorLines.push({ line, end: 'x1', endY: 'y1' });
        }
      });
      
      nodeElement.style.cursor = 'grabbing';
    });
    
    svg.addEventListener('mousemove', function(e) {
      if (isDragging && draggedNode === nodeElement) {
        const svgPoint = svg.createSVGPoint();
        svgPoint.x = e.clientX;
        svgPoint.y = e.clientY;
        const CTM = svg.getScreenCTM();
        if (CTM) {
          const invertedCTM = CTM.inverse();
          const pt = svgPoint.matrixTransform(invertedCTM);
          
          const currentX = (pt.x - mindmapPanX) / mindmapZoom;
          const currentY = (pt.y - mindmapPanY) / mindmapZoom;
          
          const deltaX = currentX - startX;
          const deltaY = currentY - startY;
          
          const currentPos = nodePositions[draggedNodeId];
          const newX = currentPos.x + deltaX;
          const newY = currentPos.y + deltaY;
          
          nodePositions[draggedNodeId] = { x: newX, y: newY };
          
          // Update node position
          if (nodeElement.tagName === 'circle') {
            nodeElement.setAttribute('cx', newX);
            nodeElement.setAttribute('cy', newY);
          } else if (nodeElement.tagName === 'rect') {
            const width = parseFloat(nodeElement.getAttribute('width'));
            const height = parseFloat(nodeElement.getAttribute('height'));
            nodeElement.setAttribute('x', newX - width / 2);
            nodeElement.setAttribute('y', newY - height / 2);
          }
          
          // Update text position
          if (textElement) {
            textElement.setAttribute('x', newX);
            textElement.setAttribute('y', newY);
          }
          
          // Update connector lines
          connectorLines.forEach(conn => {
            conn.line.setAttribute(conn.end, newX);
            conn.line.setAttribute(conn.endY, newY);
          });
          
          startX = currentX;
          startY = currentY;
        }
      }
    });
    
    svg.addEventListener('mouseup', function() {
      if (isDragging && draggedNode === nodeElement) {
        isDragging = false;
        draggedNode = null;
        draggedNodeId = null;
        draggedNodeText = null;
        connectorLines = [];
        nodeElement.style.cursor = 'move';
      }
    });
    
    nodeElement.style.cursor = 'move';
  }
  
  let tooltip = null;
  
  function showTooltip(x, y, text) {
    if (!tooltip) {
      tooltip = document.createElement('div');
      tooltip.id = 'mindmapTooltip';
      tooltip.style.cssText = 'position: absolute; background: rgba(28, 28, 30, 0.95); border: 1px solid rgba(255,255,255,0.2); border-radius: 8px; padding: 12px; color: rgba(255,255,255,0.9); font-size: 13px; font-family: var(--font-family); max-width: 300px; pointer-events: none; z-index: 10003; box-shadow: 0 4px 12px rgba(0,0,0,0.3); line-height: 1.5; white-space: pre-wrap;';
      document.body.appendChild(tooltip);
    }
    tooltip.textContent = text;
    tooltip.style.left = x + 'px';
    tooltip.style.top = y + 'px';
    tooltip.style.display = 'block';
  }
  
  function hideTooltip() {
    if (tooltip) {
      tooltip.style.display = 'none';
    }
  }
  
  let isPanning = false;
  let startPanX = 0;
  let startPanY = 0;
  let startMouseX = 0;
  let startMouseY = 0;
  
  function setupPanning(svg, root) {
    if (!svg) {
      console.error('SVG element is null in setupPanning');
      return;
    }
    
    // Mouse panning
    svg.addEventListener('mousedown', function(e) {
      if (e.target.tagName === 'svg' || e.target.classList.contains('mindmap-node') || e.target.classList.contains('mindmap-connector')) {
        isPanning = true;
        startMouseX = e.clientX;
        startMouseY = e.clientY;
        startPanX = mindmapPanX;
        startPanY = mindmapPanY;
        svg.style.cursor = 'grabbing';
      }
    });
    
    svg.addEventListener('mousemove', function(e) {
      if (isPanning) {
        const dx = e.clientX - startMouseX;
        const dy = e.clientY - startMouseY;
        mindmapPanX = startPanX + dx / mindmapZoom;
        mindmapPanY = startPanY + dy / mindmapZoom;
        updateMindmapTransform();
      }
    });
    
    svg.addEventListener('mouseup', function() {
      isPanning = false;
      svg.style.cursor = 'grab';
    });
    
    svg.addEventListener('mouseleave', function() {
      isPanning = false;
      svg.style.cursor = 'grab';
    });
    
    // Scroll to zoom (smooth) - much slower and properly centered
    let zoomAnimationFrame = null;
    svg.addEventListener('wheel', function(e) {
      e.preventDefault();
      const delta = e.deltaY > 0 ? 0.995 : 1.005; // Very small increments for smooth, slow zoom
      const oldZoom = mindmapZoom;
      
      // Calculate min zoom based on mindmap bounds (prevent zooming out too far)
      const branches = mindmapData?.branches || [];
      const maxNodes = Math.max(branches.length, 1);
      const minZoom = Math.max(0.5, 1.0 / (1 + maxNodes * 0.1)); // Limit zoom out based on node count
      const maxZoom = 3;
      const newZoom = Math.max(minZoom, Math.min(maxZoom, mindmapZoom * delta));
      
      // Cancel previous animation frame
      if (zoomAnimationFrame) {
        cancelAnimationFrame(zoomAnimationFrame);
      }
      
      // Smooth zoom with requestAnimationFrame
      zoomAnimationFrame = requestAnimationFrame(() => {
        const scale = newZoom / oldZoom;
        
        // Get mouse position relative to SVG viewport
        const rect = svg.getBoundingClientRect();
        const mouseX = e.clientX - rect.left;
        const mouseY = e.clientY - rect.top;
        
        // Convert mouse position to SVG coordinates BEFORE transform
        const svgPoint = svg.createSVGPoint();
        svgPoint.x = mouseX;
        svgPoint.y = mouseY;
        
        // Get current transform matrix
        const root = document.getElementById('mindmapRoot');
        if (root && root.getCTM) {
          const CTM = root.getCTM();
          if (CTM) {
            // Convert screen point to SVG coordinate space
            const invertedCTM = CTM.inverse();
            const pointInSvg = svgPoint.matrixTransform(invertedCTM);
            
            // Calculate new pan to keep the point under the mouse fixed
            mindmapPanX = mouseX - (pointInSvg.x * newZoom);
            mindmapPanY = mouseY - (pointInSvg.y * newZoom);
          }
        }
        
        mindmapZoom = newZoom;
        updateMindmapTransform();
      });
    });
  }
  
  function exportMindmap() {
    if (!currentMindmapData || !currentMindmapFileName) {
      alert('No mindmap to export');
      return;
    }
    
    // TODO: Implement export functionality (PNG, SVG, PDF)
    alert('Export functionality will be implemented next');
  }
  
  // Zoom functions removed - now using slider instead
  
  function mindmapReset() {
    mindmapZoom = 1;
    mindmapPanX = 0;
    mindmapPanY = 0;
    updateMindmapTransform();
    // Re-render to ensure reset is visible
    if (mindmapData) {
      const svg = document.getElementById('mindmapCanvas');
      if (svg) {
        renderMindmap(svg, mindmapData, mindmapType);
      }
    }
  }
  
  function updateMindmapTransform() {
    const mindmapRoot = document.getElementById('mindmapRoot');
    if (mindmapRoot) {
      mindmapRoot.setAttribute('transform', 'translate(' + mindmapPanX + ', ' + mindmapPanY + ') scale(' + mindmapZoom + ')');
    }
    // Update slider position
    const zoomSlider = document.getElementById('mindmapZoomSlider');
    if (zoomSlider) {
      // Calculate min zoom based on mindmap bounds
      const branches = mindmapData?.branches || [];
      const maxNodes = Math.max(branches.length, 1);
      const minZoom = Math.max(0.5, 1.0 / (1 + maxNodes * 0.1));
      const maxZoom = 3;
      const normalizedZoom = Math.max(minZoom, Math.min(maxZoom, mindmapZoom));
      zoomSlider.value = normalizedZoom;
    }
  }
  
  // Handle SVG gradient (simple workaround)
  function fixSvgGradients() {
    const svg = document.getElementById('mindmapCanvas');
    if (!svg) return;
    
    const circles = svg.querySelectorAll('circle[fill^="linear-gradient"]');
    circles.forEach(circle => {
      const fillValue = circle.getAttribute('fill');
      if (fillValue && fillValue.includes('667eea')) {
        circle.setAttribute('fill', '#667eea');
      }
    });
  }
  
  
  // Mindmap modal event handlers
  document.getElementById('mindmapClose').onclick = closeMindmapModal;
  document.getElementById('mindmapGenerateBtn')?.addEventListener('click', generateMindmap);
  document.getElementById('mindmapGenerateBtnEmpty')?.addEventListener('click', generateMindmap);
  document.getElementById('mindmapExportBtn').onclick = exportMindmap;
  document.getElementById('mindmapFullscreen').onclick = toggleFullscreen;
  document.getElementById('mindmapReset').onclick = mindmapReset;
  
  // Zoom slider handler
  const zoomSlider = document.getElementById('mindmapZoomSlider');
  if (zoomSlider) {
    zoomSlider.addEventListener('input', function() {
      mindmapZoom = parseFloat(this.value);
      updateMindmapTransform();
    });
    
    // Update slider value when zoom changes (e.g., from scroll)
    const updateZoomSlider = function() {
      if (zoomSlider) {
        // Calculate min zoom based on mindmap bounds
        const branches = mindmapData?.branches || [];
        const maxNodes = Math.max(branches.length, 1);
        const minZoom = Math.max(0.5, 1.0 / (1 + maxNodes * 0.1));
        const maxZoom = 3;
        const normalizedZoom = Math.max(minZoom, Math.min(maxZoom, mindmapZoom));
        zoomSlider.value = normalizedZoom;
      }
    };
    
    // Update slider on zoom changes
    const originalUpdateTransform = updateMindmapTransform;
    updateMindmapTransform = function() {
      originalUpdateTransform();
      updateZoomSlider();
    };
    
    // Initial slider update
    updateZoomSlider();
  }
  
  // Type selector change handler
  const typeSelect = document.getElementById('mindmapTypeSelect');
  const typeSelectEmpty = document.getElementById('mindmapTypeSelectEmpty');
  if (typeSelect) {
    typeSelect.addEventListener('change', function() {
      mindmapType = this.value;
      if (mindmapData) {
        nodePositions = {}; // Reset positions when changing layout
        const svg = document.getElementById('mindmapCanvas');
        renderMindmap(svg, mindmapData, mindmapType);
      }
    });
  }
  if (typeSelectEmpty) {
    typeSelectEmpty.addEventListener('change', function() {
      mindmapType = this.value;
    });
  }
  
  // Close modal on background click
  document.getElementById('mindmapModal').onclick = (e) => {
    if (e.target.id === 'mindmapModal') {
      closeMindmapModal();
    }
  };
  
  // ---- Top buttons ----
  const openDropdown = document.getElementById('openDropdown');
  const openDropdownMenu = document.getElementById('openDropdownMenu');
  const openMainBtn = document.getElementById('btnOpenFile');
  const dropdownOpenPdf = document.getElementById('dropdownOpenPdf');
  const dropdownOpenFolder = document.getElementById('dropdownOpenFolder');

  function closeOpenDropdown() {
    openDropdown?.classList.remove('show');
  }

  document.addEventListener('click', (e) => {
    if (!openDropdown) return;
    if (!openDropdown.contains(e.target)) {
      closeOpenDropdown();
    }
  });

  async function handleOpenFile() {
    closeOpenDropdown();
    if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.open_file === 'function') {
      try { 
        await window.pywebview.api.open_file();
      } catch(e){ 
        console.error('Error opening file:', e);
      }
    } else {
      alert('File picker: bridge not available.');
    }
  }

  async function handleOpenFolder() {
    closeOpenDropdown();
    if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.open_folder === 'function') {
      try {
        await window.pywebview.api.open_folder();
      } catch(e){
        console.error('Error opening folder:', e);
        alert('Error opening folder: ' + (e.message || e));
      }
    } else {
      alert('Folder picker: bridge not available.');
    }
  }

  if (openMainBtn && openDropdown) {
    openMainBtn.onclick = (e) => {
      e.stopPropagation();
      openDropdown.classList.toggle('show');
    };
  }
  if (dropdownOpenPdf) {
    dropdownOpenPdf.onclick = (e) => { e.stopPropagation(); handleOpenFile(); };
  }
  if (dropdownOpenFolder) {
    dropdownOpenFolder.onclick = (e) => { e.stopPropagation(); handleOpenFolder(); };
  }

  // Empty-state buttons
  const emptyOpenFileBtn = document.getElementById('emptyOpenFile');
  if (emptyOpenFileBtn) {
    emptyOpenFileBtn.onclick = handleOpenFile;
  }

  const emptyOpenFolderBtn = document.getElementById('emptyOpenFolder');
  if (emptyOpenFolderBtn) {
    emptyOpenFolderBtn.onclick = handleOpenFolder;
  }
  // Setup Akson button
  function setupAksonButton() {
  const btnAkson = document.getElementById('btnAkson');
  if (btnAkson) {
      btnAkson.onclick = async () => {
      console.log('[Akson] Button clicked');
      try { 
          if (window.pywebview && window.pywebview.api) {
            if (typeof window.pywebview.api.open_akson_widget === 'function') {
        console.log('[Akson] Launching Akson widget...');
          const result = await window.pywebview.api.open_akson_widget(); 
          console.log('[Akson] Result:', result);
          if (result && !result.ok) {
            alert('Error launching Akson: ' + (result.error || 'Unknown error'));
          }
            } else {
              console.error('[Akson] open_akson_widget function not found');
              alert('Akson widget function not available.');
      }
    } else {
        console.error('[Akson] Bridge not available:', {
          pywebview: !!window.pywebview,
              api: !!(window.pywebview && window.pywebview.api)
        });
      alert('Akson widget: bridge not available.');
          }
        } catch(e) {
          console.error('Error opening Akson widget:', e);
          alert('Error launching Akson: ' + (e.message || e));
    }
  };
      console.log('[Akson] Button handler set up');
  } else {
    console.error('[Akson] Button not found');
    }
  }
  
  // Setup button when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupAksonButton);
  } else {
    setupAksonButton();
  }
  document.getElementById('btnSlides').onclick  = ()=> postToIframe({ type: 'enter-presentation' });
  document.getElementById('btnLibrary').onclick = openLibrary;
  
  // ---- Theme Toggle ----
  function toggleTheme() {
    theme = theme === 'dark' ? 'light' : 'dark';
    themeMode = 'custom'; // Switch to custom mode when user clicks theme button
    themeManuallySet = true; // Mark as manually set
    const icon = document.getElementById('themeIcon');
    
    if (icon) {
      // Animate icon change
      icon.style.transform = 'rotate(180deg) scale(0.8)';
      icon.style.opacity = '0.5';
    }
    
    setTimeout(() => {
      applyTheme(theme);
      if (icon) {
        icon.style.transform = 'rotate(0deg) scale(1)';
        icon.style.opacity = '1';
      }
    }, 150);
    
    // Update settings UI if modal is open
    const themeModeInput = document.getElementById('themeModeInput');
    if (themeModeInput) {
      themeModeInput.value = 'custom';
    }
    
    // Save theme preference
    saveAllSettings();
  }
  
  // Theme toggle button removed - using PDF toolbar button instead
  
  // ---- Sidebar Resize ----
  let isResizing = false;
  let resizeStartX = 0;
  let resizeStartWidth = 380;
  let resizeAnimationFrame = null;
  
  const resizeHandle = document.getElementById('sidebarResizeHandle');
  const sidebar = document.getElementById('rightSidebar');
  const workElement = document.getElementById('work');
  
  if (resizeHandle) {
    resizeHandle.addEventListener('mousedown', async (e) => {
      isResizing = true;
      resizeStartX = e.clientX;
      const currentWidth = getComputedStyle(document.documentElement).getPropertyValue('--sidebar-w').trim();
      resizeStartWidth = parseInt(currentWidth) || 380;
      document.body.style.cursor = 'ew-resize';
      document.body.style.userSelect = 'none';
      
      // Mark as resizing
      if (workElement) {
        workElement.classList.add('sidebar-resizing');
        workElement.classList.add('resizing');
      }
      
      // Block PDF resize events - critical for performance!
      blockPdfResize(true);
      
      // Disable button transition during resize for instant following
      const btn = document.getElementById('sidebarToggleBtn');
      if (btn) {
        btn.classList.add('resizing');
      }
      
      e.preventDefault();
    });
  }
  
  document.addEventListener('mousemove', (e) => {
    if (!isResizing) return;
    
    // Cancel previous animation frame if it exists
    if (resizeAnimationFrame) {
      cancelAnimationFrame(resizeAnimationFrame);
    }
    
    // Schedule update for next frame to throttle updates
    resizeAnimationFrame = requestAnimationFrame(() => {
    const deltaX = resizeStartX - e.clientX; // Inverted because sidebar is on right
    let newWidth = resizeStartWidth + deltaX;
    
    // Clamp width between 280 and 600px
    newWidth = Math.max(280, Math.min(600, newWidth));
    
    sidebarWidth = newWidth;
    applySidebarWidth(newWidth);
      
      // Update grid to use new width (remove inline style if present)
      if (workElement) {
        workElement.style.gridTemplateColumns = `1fr ${newWidth}px`;
      }
    });
    
    e.preventDefault();
  });
  
  document.addEventListener('mouseup', () => {
    if (isResizing) {
      isResizing = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      
      // Unblock PDF resize events
      blockPdfResize(false);
      
      // Re-enable CSS transitions after resize is complete
      if (workElement) {
        workElement.classList.remove('sidebar-resizing');
        workElement.classList.remove('resizing');
        
        // Update grid to use CSS variable (remove inline style)
        // If sidebar is hidden, set to 1fr 0, otherwise remove inline style so CSS variable works
        if (workElement.classList.contains('sidebar-hidden')) {
          workElement.style.gridTemplateColumns = '1fr 0';
        } else {
          // Remove inline style so CSS variable takes effect
          workElement.style.gridTemplateColumns = '';
        }
      }
      
      // Re-enable button transition after resize
      const btn = document.getElementById('sidebarToggleBtn');
      if (btn) {
        btn.classList.remove('resizing');
      }
      
      // Cancel any pending animation frame
      if (resizeAnimationFrame) {
        cancelAnimationFrame(resizeAnimationFrame);
        resizeAnimationFrame = null;
      }
      
      // Trigger PDF resize once after resize completes
      const pdfFrame = document.getElementById('pdfFrame');
      setTimeout(() => {
        if (pdfFrame && pdfFrame.contentWindow && pdfFrame.contentWindow.PDFViewerApplication) {
          try {
            requestAnimationFrame(() => {
              if (pdfFrame.contentWindow.PDFViewerApplication && pdfFrame.contentWindow.PDFViewerApplication.eventBus) {
                pdfFrame.contentWindow.PDFViewerApplication.eventBus.dispatch('resize', { source: window });
              }
            });
          } catch(e) {
            console.log('PDF resize error after sidebar resize:', e);
          }
        }
      }, 100);
      
      saveAllSettings();
    }
  });
  
  // ---- Sidebar Toggle & Settings ----
  let sidebarHidden = false;
  let sidebarButtonsHidden = false;
  let sidebarHotkey = 'Ctrl+B'; // default hotkey
  let sidebarWidth = 380;
  let animationSpeed = 'normal';
  let defaultSidebarState = 'visible';
  let autoSaveEnabled = true;
  let fontSize = 'medium';
  let pdfZoom = 'auto';
  let theme = 'dark'; // 'dark' or 'light'
  let themeMode = 'system'; // 'system' or 'custom'
  let pdfPageTheme = false; // Whether to invert PDF page colors
  let accentColor = '#8B5CF6'; // Default purple accent color
  let themeLight = '#ffffff'; // Default light theme color
  let themeDark = '#1a1a1c'; // Default dark theme color
  
  // Apply CSS variable for sidebar width
  function applySidebarWidth(width) {
    document.documentElement.style.setProperty('--sidebar-w', width + 'px');
  }
  
  // Apply animation speed
  function applyAnimationSpeed(speed) {
    const durations = { fast: '0.2s', normal: '0.3s', slow: '0.4s' };
    const easing = 'cubic-bezier(0.25, 0.46, 0.45, 0.94)';
    const duration = durations[speed] || '0.3s';
    const sidebar = document.getElementById('rightSidebar');
    const btn = document.getElementById('sidebarToggleBtn');
    if (sidebar) {
      sidebar.style.transition = `transform ${duration} ${easing}, opacity ${duration} ${easing}`;
    }
    if (btn) {
      // Use EXACT same duration and easing as sidebar for perfect sync
      btn.style.transition = `transform ${duration} ${easing}, background 0.15s ease, box-shadow 0.15s ease, opacity 0.15s ease`;
    }
  }
  
  // Apply font size
  function applyFontSize(size) {
    console.log('Applying font size:', size);
    const multipliers = { small: 0.9, medium: 1, large: 1.15 };
    const multiplier = multipliers[size] || 1;
    const sidebar = document.getElementById('rsScroll');
    if (sidebar) {
      sidebar.style.fontSize = (13.5 * multiplier) + 'px';
      console.log('Font size applied:', (13.5 * multiplier) + 'px');
    } else {
      console.warn('rsScroll element not found, retrying...');
      // Retry after a short delay if element not found
      setTimeout(() => {
        const retrySidebar = document.getElementById('rsScroll');
        if (retrySidebar) {
          retrySidebar.style.fontSize = (13.5 * multiplier) + 'px';
          console.log('Font size applied on retry:', (13.5 * multiplier) + 'px');
        }
      }, 100);
    }
  }
  
  // Apply theme (both app and PDF) - unified approach like PDF viewer
  function applyTheme(currentTheme) {
    if (!currentTheme) currentTheme = 'dark';
    
    // Apply theme colors first to ensure all CSS variables are set
    applyThemeColor(currentTheme === 'light' ? 'light' : 'dark', currentTheme === 'light' ? themeLight : themeDark);
    
    // Apply theme colors to all UI elements
    if (typeof applyThemeColorsToAllUI === 'function') {
      applyThemeColorsToAllUI();
    }
    
    const body = document.body;
    const html = document.documentElement;
    const icon = document.getElementById('themeIcon');
    
    // Use same approach as PDF viewer - is-dark/is-light classes on root
    if (currentTheme === 'light') {
      html.classList.remove('is-dark');
      html.classList.add('is-light');
      body.classList.remove('dark-mode');
      body.classList.add('light-mode');
      if (icon) icon.textContent = '☀️';
    } else {
      html.classList.remove('is-light');
      html.classList.add('is-dark');
      body.classList.remove('light-mode');
      body.classList.add('dark-mode');
      if (icon) icon.textContent = '🌙';
    }
    
    // Set data-theme attribute for CSS targeting (backward compatibility)
    html.setAttribute('data-theme', currentTheme);
    body.setAttribute('data-theme', currentTheme);
    
    // Notify PDF viewer iframe about theme change
    const pdfFrame = document.getElementById('pdfFrame');
    if (pdfFrame && pdfFrame.contentWindow) {
      pdfFrame.contentWindow.postMessage({ 
        type: 'theme-changed', 
        theme: currentTheme,
        pdfPageTheme: pdfPageTheme
      }, '*');
    }
  }
  
  // Apply PDF page theme (invert colors or keep original)
  function applyPdfPageTheme(enabled) {
    console.log('Applying PDF page theme inversion:', enabled);
    const pdfFrame = document.getElementById('pdfFrame');
    if (pdfFrame && pdfFrame.contentWindow) {
      pdfFrame.contentWindow.postMessage({ 
        type: 'pdf-page-theme-changed', 
        enabled: enabled,
        theme: theme
      }, '*');
      console.log('PDF page theme message sent to iframe');
    } else {
      console.warn('PDF frame not ready, retrying...');
      // Retry after a short delay if frame not ready
      setTimeout(() => {
        const retryFrame = document.getElementById('pdfFrame');
        if (retryFrame && retryFrame.contentWindow) {
          retryFrame.contentWindow.postMessage({ 
            type: 'pdf-page-theme-changed', 
            enabled: enabled,
            theme: theme
          }, '*');
          console.log('PDF page theme message sent on retry');
        }
      }, 500);
    }
  }
  
  // Load settings on startup
  async function loadSettings() {
    try {
      if (window.pywebview && window.pywebview.api) {
        const result = await window.pywebview.api.load_settings();
        if (result && result.ok && result.settings) {
          if (result.settings.sidebarHotkey) sidebarHotkey = result.settings.sidebarHotkey;
          if (result.settings.sidebarWidth) sidebarWidth = result.settings.sidebarWidth;
          if (result.settings.animationSpeed) animationSpeed = result.settings.animationSpeed;
          if (result.settings.defaultSidebarState) defaultSidebarState = result.settings.defaultSidebarState;
          if (result.settings.autoSaveEnabled !== undefined) autoSaveEnabled = result.settings.autoSaveEnabled;
          if (result.settings.duplicatePromptBehavior) duplicatePromptBehavior = result.settings.duplicatePromptBehavior;
          // Backward compatibility: convert old showDuplicatePrompt setting
          if (result.settings.showDuplicatePrompt !== undefined) {
            duplicatePromptBehavior = result.settings.showDuplicatePrompt ? 'ask' : 'load';
          }
          if (result.settings.fontSize) fontSize = result.settings.fontSize;
          if (result.settings.pdfZoom) pdfZoom = result.settings.pdfZoom;
          if (result.settings.theme) theme = result.settings.theme;
          if (result.settings.themeMode) themeMode = result.settings.themeMode;
          if (result.settings.pdfPageTheme !== undefined) pdfPageTheme = result.settings.pdfPageTheme;
          if (result.settings.accentColor) {
            accentColor = result.settings.accentColor;
            applyAccentColor(accentColor);
          }
          if (result.settings.themeLight) {
            themeLight = result.settings.themeLight;
            applyThemeColor('light', themeLight);
          }
          if (result.settings.themeDark) {
            themeDark = result.settings.themeDark;
            applyThemeColor('dark', themeDark);
          }
          if (result.settings.sidebarHidden !== undefined) {
            sidebarHidden = result.settings.sidebarHidden;
          } else {
            sidebarHidden = defaultSidebarState === 'hidden';
          }
          
          // Apply settings
          applySidebarWidth(sidebarWidth);
          applyAnimationSpeed(animationSpeed);
          applyFontSize(fontSize);
          // Theme is handled by loadThemeOnStartup() - don't override it here
          // Just update the theme variables if they exist
          if (result.settings.theme) {
            theme = result.settings.theme;
          }
          if (result.settings.themeMode) {
            themeMode = result.settings.themeMode;
          }
          if (result.settings.pdfPageTheme !== undefined) {
            pdfPageTheme = result.settings.pdfPageTheme;
          } else {
            pdfPageTheme = false; // Default to off
          }
          
          // Apply PDF page theme if loaded
          if (pdfPageTheme !== undefined) {
            applyPdfPageTheme(pdfPageTheme);
          }
          // Apply accent color if loaded
          if (result.settings.accentColor) {
            accentColor = result.settings.accentColor;
            applyAccentColor(accentColor);
          } else {
            // Apply default accent color on first load
            applyAccentColor(accentColor);
          }
          // Apply theme colors
          if (result.settings.themeLight) {
            themeLight = result.settings.themeLight;
            applyThemeColor('light', themeLight);
          } else {
            applyThemeColor('light', themeLight);
          }
          if (result.settings.themeDark) {
            themeDark = result.settings.themeDark;
            applyThemeColor('dark', themeDark);
          } else {
            applyThemeColor('dark', themeDark);
          }
          // Apply current theme color based on active theme
          applyTheme(theme);
          toggleSidebarVisual(sidebarHidden);
        }
      }
    } catch(e) {
      console.error('Error loading settings:', e);
      // Apply default colors even if settings fail to load
      applyAccentColor(accentColor);
      applyThemeColor('light', themeLight);
      applyThemeColor('dark', themeDark);
      applyTheme(theme);
    }
  }
  
  // Load settings after theme is initialized
  // Use setTimeout to ensure loadThemeOnStartup runs first
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      setTimeout(() => loadSettings(), 200);
    });
  } else {
    setTimeout(() => loadSettings(), 200);
  }
  
  function toggleSidebar() {
    sidebarHidden = !sidebarHidden;
    toggleSidebarVisual(sidebarHidden);
    // Save state
    saveAllSettings();
  }
  
  function saveAllSettings() {
    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.save_settings({
        sidebarHidden,
        sidebarHotkey,
        sidebarWidth,
        animationSpeed,
        defaultSidebarState,
        autoSaveEnabled,
        duplicatePromptBehavior,
        fontSize,
        pdfZoom,
        theme,
        themeMode,
        pdfPageTheme,
        accentColor,
        themeLight,
        themeDark
      }).catch(e => console.error('Error saving settings:', e));
    }
  }
  
  // Load theme on startup - run after DOM is ready
  // Detect system theme preference
  function getSystemTheme() {
    if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
      return 'dark';
    }
    return 'light';
  }
  
  // Track if user has manually set theme (via button click)
  let themeManuallySet = false;
  
  // Listen for system theme changes - same approach as PDF viewer
  function setupSystemThemeListener() {
    if (window.matchMedia) {
      const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
      
      // Function to handle theme change
      const handleSystemThemeChange = (e) => {
        // Only auto-update if theme mode is 'system'
        if (themeMode === 'system') {
          const systemTheme = e.matches ? 'dark' : 'light';
          console.log('System theme changed to:', systemTheme);
          theme = systemTheme;
          themeManuallySet = false;
          applyTheme(theme);
          // Don't save on auto-update to allow manual override
        }
      };
      
      // Also check initial state
      if (themeMode === 'system') {
        const initialSystemTheme = getSystemTheme();
        if (theme !== initialSystemTheme) {
          theme = initialSystemTheme;
          themeManuallySet = false;
          applyTheme(theme);
        }
      }
      
      // Listen for changes
      if (mediaQuery.addEventListener) {
        mediaQuery.addEventListener('change', handleSystemThemeChange);
      } else {
        // Fallback for older browsers
        mediaQuery.addListener(handleSystemThemeChange);
      }
    }
  }
  
  async function loadThemeOnStartup() {
    try {
      const systemTheme = getSystemTheme();
      console.log('Initial system theme:', systemTheme);
      
      if (window.pywebview && window.pywebview.api) {
        const result = await window.pywebview.api.load_settings();
        if (result && result.ok && result.settings) {
          // Load theme mode setting
          if (result.settings.themeMode) {
            themeMode = result.settings.themeMode;
          }
          
          // Load PDF page theme setting
          if (result.settings.pdfPageTheme !== undefined) {
            pdfPageTheme = result.settings.pdfPageTheme;
          } else {
            pdfPageTheme = false; // Default to off
          }
          
          if (themeMode === 'custom' && result.settings.theme) {
            // Custom mode - use saved theme
          theme = result.settings.theme;
            themeManuallySet = true;
            console.log('Using saved theme:', theme, '(custom mode)');
          applyTheme(theme);
        } else {
            // System mode - use system theme
            theme = systemTheme;
            themeManuallySet = false;
            console.log('Using system theme:', theme, '(system mode)');
            applyTheme(theme);
        }
          
          // Apply PDF page theme after theme is set
          applyPdfPageTheme(pdfPageTheme);
      } else {
          // No saved settings - use system theme
          themeMode = 'system';
          theme = systemTheme;
          themeManuallySet = false;
          pdfPageTheme = false; // Default to off
          console.log('No saved settings, using system theme:', theme);
          applyTheme(theme);
          applyPdfPageTheme(pdfPageTheme);
        }
      } else {
        // No API - use system theme
        themeMode = 'system';
        theme = systemTheme;
        themeManuallySet = false;
        pdfPageTheme = false; // Default to off
        console.log('No API, using system theme:', theme);
        applyTheme(theme);
        applyPdfPageTheme(pdfPageTheme);
      }
      
      // Set up listener for system theme changes
      setupSystemThemeListener();
    } catch(e) {
      console.error('Error loading theme:', e);
      // Default to system theme on error
      themeMode = 'system';
      theme = getSystemTheme();
      themeManuallySet = false;
      pdfPageTheme = false; // Default to off
      applyTheme(theme);
      applyPdfPageTheme(pdfPageTheme);
      setupSystemThemeListener();
    }
  }
  
  // Wait for DOM to be ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', loadThemeOnStartup);
  } else {
    loadThemeOnStartup();
  }
  
  // ============================================
  // Block PDF.js Resize Events During Sidebar Operations
  // This prevents PDF from reflowing during sidebar resize/toggle
  // ============================================
  
  let pdfResizeBlocked = false;
  let originalResizeListeners = null;
  
  function blockPdfResize(block) {
    pdfResizeBlocked = block;
    const pdfFrame = document.getElementById('pdfFrame');
    
    if (!pdfFrame || !pdfFrame.contentWindow) {
      return;
    }
    
    try {
      const app = pdfFrame.contentWindow.PDFViewerApplication;
      if (!app || !app.eventBus) {
        return;
      }
      
      if (block) {
        // Store original resize listeners
        if (!originalResizeListeners && app.eventBus._listeners) {
          originalResizeListeners = app.eventBus._listeners.resize ? [...app.eventBus._listeners.resize] : [];
        }
        
        // Clear resize listeners - PDF won't respond to resize events
        if (app.eventBus._listeners) {
          app.eventBus._listeners.resize = [];
        }
        
        // Also disable the resize handler directly if possible
        if (app.pdfViewer && app.pdfViewer.update) {
          app.pdfViewer._originalUpdate = app.pdfViewer.update;
          app.pdfViewer.update = function() {
            // No-op during block
            if (!pdfResizeBlocked) {
              app.pdfViewer._originalUpdate.call(this);
            }
          };
        }
      } else {
        // Restore resize listeners
        if (originalResizeListeners && app.eventBus._listeners) {
          app.eventBus._listeners.resize = originalResizeListeners;
          originalResizeListeners = null;
        }
        
        // Restore update function
        if (app.pdfViewer && app.pdfViewer._originalUpdate) {
          app.pdfViewer.update = app.pdfViewer._originalUpdate;
          delete app.pdfViewer._originalUpdate;
        }
      }
    } catch(e) {
      console.log('Error blocking PDF resize:', e);
    }
  }
  
  // Also intercept window resize events to PDF.js
  const originalWindowResize = window.addEventListener;
  window.addEventListener('resize', function(e) {
    if (pdfResizeBlocked) {
      // Don't let resize events reach PDF.js
      e.stopImmediatePropagation();
      return false;
    }
  }, true); // Use capture phase
  
  
  function toggleSidebarVisual(hidden) {
    const work = document.getElementById('work');
    const toggleBtn = document.getElementById('sidebarToggleBtn');
    const pdfFrame = document.getElementById('pdfFrame');
    const sidebar = document.getElementById('rightSidebar');
    
    // Mark as animating to prevent PDF resize during animation
    work.classList.add('sidebar-animating');
    
    // Block PDF resize events - critical for performance!
    blockPdfResize(true);
    
    // Also freeze PDF rendering completely during animation
    if (pdfFrame && pdfFrame.contentWindow && pdfFrame.contentWindow.PDFViewerApplication) {
      try {
        const app = pdfFrame.contentWindow.PDFViewerApplication;
        const viewer = app.pdfViewer;
        
        // Store original functions
        if (viewer && !viewer._originalUpdate) {
          viewer._originalUpdate = viewer.update;
          viewer._originalRequestRendering = viewer.requestRendering;
        }
        
        // Disable rendering during animation
        if (viewer) {
          viewer.update = function() {}; // No-op
          if (viewer.requestRendering) {
            viewer.requestRendering = function() {}; // No-op
          }
        }
      } catch(e) {
        // PDF might not be ready
      }
    }
    
    // Get actual animation duration from CSS (respects user settings)
    const sidebarStyle = window.getComputedStyle(sidebar);
    const transitionDuration = parseFloat(sidebarStyle.transitionDuration) * 1000 || 300;
    
    // Start grid transition IMMEDIATELY, synchronized with sidebar transform
    // Use requestAnimationFrame to ensure they start together
    requestAnimationFrame(() => {
    if (hidden) {
      work.classList.add('sidebar-hidden');
      toggleBtn.title = 'Show Sidebar (Hotkey: ' + sidebarHotkey + ')';
        // Start grid transition immediately - same timing as sidebar transform
        work.style.gridTemplateColumns = '1fr 0';
    } else {
      work.classList.remove('sidebar-hidden');
      toggleBtn.title = 'Hide Sidebar (Hotkey: ' + sidebarHotkey + ')';
        // Start grid transition immediately - same timing as sidebar transform
        const sidebarWidth = getComputedStyle(document.documentElement).getPropertyValue('--sidebar-w').trim();
        work.style.gridTemplateColumns = `1fr ${sidebarWidth}`;
      }
    });
    
    // Wait for both animations to complete (sidebar transform + grid transition)
    setTimeout(() => {
      // Remove animating class
      work.classList.remove('sidebar-animating');
      
      // Restore PDF rendering functions
      if (pdfFrame && pdfFrame.contentWindow && pdfFrame.contentWindow.PDFViewerApplication) {
        try {
          const app = pdfFrame.contentWindow.PDFViewerApplication;
          const viewer = app.pdfViewer;
          
          if (viewer && viewer._originalUpdate) {
            viewer.update = viewer._originalUpdate;
            delete viewer._originalUpdate;
          }
          if (viewer && viewer._originalRequestRendering) {
            viewer.requestRendering = viewer._originalRequestRendering;
            delete viewer._originalRequestRendering;
          }
        } catch(e) {
          // PDF might not be ready
        }
      }
      
      // Unblock PDF resize events after a brief delay
      setTimeout(() => {
        blockPdfResize(false);
        
        // Trigger PDF resize after animations complete
        setTimeout(() => {
          if (pdfFrame && pdfFrame.contentWindow && pdfFrame.contentWindow.PDFViewerApplication) {
            try {
              // Use requestAnimationFrame to batch the resize
              requestAnimationFrame(() => {
                if (pdfFrame.contentWindow.PDFViewerApplication && pdfFrame.contentWindow.PDFViewerApplication.eventBus) {
                  pdfFrame.contentWindow.PDFViewerApplication.eventBus.dispatch('resize', { source: window });
                }
              });
            } catch(e) {
              // PDF viewer might not be ready
              console.log('PDF resize error:', e);
            }
          }
        }, 50);
      }, 50);
    }, transitionDuration + 20);
  }
  
  // Sidebar toggle button
  document.getElementById('sidebarToggleBtn').onclick = toggleSidebar;
  
  // Debounce window resize events to prevent PDF lag during sidebar animation
  let resizeDebounceTimeout = null;
  const originalDispatchEvent = window.dispatchEvent;
  window.dispatchEvent = function(event) {
    // Intercept resize events during sidebar animation
    if (event.type === 'resize') {
      const work = document.getElementById('work');
      if (work && work.classList.contains('sidebar-animating')) {
        // Skip resize events during sidebar animation
        return true;
      }
    }
    return originalDispatchEvent.call(this, event);
  };
  
  // Also debounce actual window resize events
  let windowResizeTimeout = null;
  window.addEventListener('resize', function() {
    const work = document.getElementById('work');
    if (work && work.classList.contains('sidebar-animating')) {
      // Clear any pending resize handlers
      if (windowResizeTimeout) {
        clearTimeout(windowResizeTimeout);
      }
      // Don't trigger resize during animation
      return;
    }
    
    // Debounce resize events
    if (windowResizeTimeout) {
      clearTimeout(windowResizeTimeout);
    }
    windowResizeTimeout = setTimeout(() => {
      // Resize will be handled by PDF.js after animation completes
    }, 100);
  }, { passive: true });
  
  // Sidebar buttons toggle
  function toggleSidebarButtons() {
    sidebarButtonsHidden = !sidebarButtonsHidden;
    const buttonsSection = document.getElementById('sidebarButtons');
    const toggleBtn = document.getElementById('sidebarButtonsToggle');
    console.log('Toggle buttons clicked, hidden:', sidebarButtonsHidden);
    if (buttonsSection) {
      if (sidebarButtonsHidden) {
        buttonsSection.classList.add('hidden');
        if (toggleBtn) {
          toggleBtn.title = 'Show buttons';
          toggleBtn.classList.add('buttons-hidden');
        }
      } else {
        buttonsSection.classList.remove('hidden');
        if (toggleBtn) {
          toggleBtn.title = 'Hide buttons';
          toggleBtn.classList.remove('buttons-hidden');
        }
      }
    }
  }
  
  // Ensure element exists before attaching handler
  const sidebarButtonsToggleEl = document.getElementById('sidebarButtonsToggle');
  if (sidebarButtonsToggleEl) {
    sidebarButtonsToggleEl.onclick = toggleSidebarButtons;
  } else {
    console.warn('sidebarButtonsToggle element not found');
  }
  
  // Hotkey support
  function parseHotkey(hotkeyStr) {
    const parts = hotkeyStr.toLowerCase().split(/[+\s]+/).filter(p => p);
    const modifiers = {
      ctrl: false,
      cmd: false,
      meta: false,
      alt: false,
      shift: false,
      key: null
    };
    
    parts.forEach(part => {
      if (part === 'ctrl' || part === 'control') modifiers.ctrl = true;
      else if (part === 'cmd' || part === 'meta') { modifiers.cmd = true; modifiers.meta = true; }
      else if (part === 'alt') modifiers.alt = true;
      else if (part === 'shift') modifiers.shift = true;
      else modifiers.key = part;
    });
    
    return modifiers;
  }
  
  function matchesHotkey(e, hotkeyStr) {
    const mods = parseHotkey(hotkeyStr);
    const isMac = navigator.platform.toUpperCase().indexOf('MAC') >= 0;
    
    // Normalize key - handle special keys
    let normalizedKey = e.key.toLowerCase();
    if (normalizedKey === 'control' || normalizedKey === 'meta' || normalizedKey === 'alt' || normalizedKey === 'shift') {
      return false; // Ignore modifier-only presses
    }
    
    // Handle Cmd vs Ctrl
    let wantsCtrl = mods.ctrl;
    let wantsCmd = mods.cmd || mods.meta;
    
    if (isMac) {
      // On Mac: Cmd = metaKey, Ctrl = ctrlKey
      if (wantsCmd && !e.metaKey) return false;
      if (wantsCtrl && !e.ctrlKey) return false;
      if (!wantsCmd && e.metaKey) return false; // Don't want Cmd but have it
      if (!wantsCtrl && !wantsCmd && (e.metaKey || e.ctrlKey)) return false; // Don't want either but have one
    } else {
      // On Windows/Linux: Ctrl = ctrlKey, no meta
      if (wantsCmd) return false; // Can't have Cmd on Windows/Linux
      if (wantsCtrl && !e.ctrlKey) return false;
      if (!wantsCtrl && e.ctrlKey) return false;
    }
    
    // Check Alt
    if (mods.alt !== e.altKey) return false;
    
    // Check Shift
    if (mods.shift !== e.shiftKey) return false;
    
    // Check the actual key
    if (mods.key && normalizedKey !== mods.key.toLowerCase()) return false;
    
    return true;
  }
  
  // Global hotkey listener - listen at window level to catch iframe events
          // Listen for theme toggle requests from PDF viewer iframe
          window.addEventListener('message', (e) => {
            if (e.data && e.data.type === 'toggle-theme') {
              toggleTheme();
            } else if (e.data && e.data.type === 'get-theme') {
              // PDF viewer is requesting current theme - send it with all info
              const pdfFrame = document.getElementById('pdfFrame');
              if (pdfFrame && pdfFrame.contentWindow) {
                pdfFrame.contentWindow.postMessage({ 
                  type: 'theme-changed', 
                  theme: theme,
                  pdfPageTheme: pdfPageTheme
                }, '*');
              }
            }
          });
          
          function handleHotkey(e) {
    // Don't trigger if settings modal is open
    const modalOpen = document.getElementById('settingsModal').classList.contains('show');
    if (modalOpen) {
      return;
    }
    
    // Don't trigger if typing in input/textarea
    const tagName = e.target.tagName;
    const isInput = tagName === 'INPUT' || tagName === 'TEXTAREA' || e.target.contentEditable === 'true';
    
    if (isInput) {
      return;
    }
    
    // Check if this matches our hotkey
    if (matchesHotkey(e, sidebarHotkey)) {
      e.preventDefault();
      e.stopPropagation();
      toggleSidebar();
      return false;
    }
  }
  
  // Listen at both document and window level to catch events from iframe
  document.addEventListener('keydown', handleHotkey, true);
  window.addEventListener('keydown', handleHotkey, true);
  
  // Settings modal
  function updateHotkeyDisplay(hotkeyStr) {
    const picker = document.getElementById('hotkeyPicker');
    picker.innerHTML = '';
    
    if (!hotkeyStr || !hotkeyStr.trim()) {
      picker.innerHTML = '<span class="hotkeyPlaceholder">Click to set shortcut</span>';
      return;
    }
    
    const parts = hotkeyStr.split(/[+\s]+/).filter(p => p.trim());
    parts.forEach(part => {
      const badge = document.createElement('span');
      badge.className = 'hotkeyBadge';
      badge.textContent = part.trim().toUpperCase();
      picker.appendChild(badge);
    });
  }
  
  function openSettings() {
    const modal = document.getElementById('settingsModal');
    const content = document.getElementById('settingsContent');
    
    // Populate all settings inputs
    updateHotkeyDisplay(sidebarHotkey);
    document.getElementById('animationSpeedInput').value = animationSpeed;
    document.getElementById('defaultSidebarStateInput').value = defaultSidebarState;
    document.getElementById('autoSaveEnabledInput').checked = autoSaveEnabled;
    document.getElementById('duplicatePromptBehaviorInput').value = duplicatePromptBehavior;
    document.getElementById('fontSizeInput').value = fontSize;
    document.getElementById('pdfZoomInput').value = pdfZoom;
    document.getElementById('themeModeInput').value = themeMode;
    document.getElementById('pdfPageThemeInput').checked = pdfPageTheme;
    
    // Initialize color pickers
    setTimeout(() => {
      initThemePresets();
      initAdvancedColorsPanel();
    }, 100);
    
    // Show modal
    modal.classList.add('show');
  }
  
  function closeSettings() {
    const modal = document.getElementById('settingsModal');
    modal.classList.remove('show');
  }
  
  function saveSettings() {
    // Get all settings (automatically saved on change in macOS style)
    const newSpeed = document.getElementById('animationSpeedInput').value;
    const newDefaultState = document.getElementById('defaultSidebarStateInput').value;
    const newAutoSave = document.getElementById('autoSaveEnabledInput').checked;
    const newDuplicatePromptBehavior = document.getElementById('duplicatePromptBehaviorInput').value;
    const newFontSize = document.getElementById('fontSizeInput').value;
    const newZoom = document.getElementById('pdfZoomInput').value;
    const newThemeMode = document.getElementById('themeModeInput').value;
    const newPdfPageTheme = document.getElementById('pdfPageThemeInput').checked;
    
    animationSpeed = newSpeed;
    defaultSidebarState = newDefaultState;
    autoSaveEnabled = newAutoSave;
    duplicatePromptBehavior = newDuplicatePromptBehavior;
    fontSize = newFontSize;
    pdfZoom = newZoom;
    themeMode = newThemeMode;
    pdfPageTheme = newPdfPageTheme;
    
    // Accent color is handled separately via event listeners
    
    // Handle theme mode change
    if (themeMode === 'system') {
      themeManuallySet = false;
      const systemTheme = getSystemTheme();
      if (theme !== systemTheme) {
        theme = systemTheme;
        applyTheme(theme);
      }
      setupSystemThemeListener(); // Re-setup listener
    } else {
      themeManuallySet = true;
    }
    
    // Apply changes immediately BEFORE saving
    applyAnimationSpeed(animationSpeed);
    applyFontSize(fontSize);
    applyPdfPageTheme(pdfPageTheme);
    
    // Save to backend
    if (window.pywebview && window.pywebview.api) {
      window.pywebview.api.save_settings({
        sidebarHidden,
        sidebarHotkey,
        sidebarWidth,
        animationSpeed,
        defaultSidebarState,
        autoSaveEnabled,
        duplicatePromptBehavior,
        fontSize,
        pdfZoom,
        theme,
        themeMode,
        pdfPageTheme,
        accentColor
      }).catch(e => {
        console.error('Error saving settings:', e);
      });
    }
  }
  
  // Apply accent color dynamically
  function applyAccentColor(color) {
    accentColor = color;
    
    // Convert hex to RGB for rgba calculations
    const hex = color.replace('#', '');
    const r = parseInt(hex.substring(0, 2), 16);
    const g = parseInt(hex.substring(2, 4), 16);
    const b = parseInt(hex.substring(4, 6), 16);
    
    // Calculate lighter and darker variants
    const lightR = Math.min(255, Math.round(r + (255 - r) * 0.2));
    const lightG = Math.min(255, Math.round(g + (255 - g) * 0.2));
    const lightB = Math.min(255, Math.round(b + (255 - b) * 0.2));
    const lightColor = `#${lightR.toString(16).padStart(2, '0')}${lightG.toString(16).padStart(2, '0')}${lightB.toString(16).padStart(2, '0')}`;
    
    const darkR = Math.max(0, Math.round(r * 0.9));
    const darkG = Math.max(0, Math.round(g * 0.9));
    const darkB = Math.max(0, Math.round(b * 0.9));
    const darkColor = `#${darkR.toString(16).padStart(2, '0')}${darkG.toString(16).padStart(2, '0')}${darkB.toString(16).padStart(2, '0')}`;
    
    // Update CSS variables
    document.documentElement.style.setProperty('--accent-purple', color);
    document.documentElement.style.setProperty('--accent-purple-light', lightColor);
    document.documentElement.style.setProperty('--accent-purple-dark', darkColor);
    document.documentElement.style.setProperty('--accent-purple-glow', `rgba(${r}, ${g}, ${b}, 0.15)`);
    
    // Legacy support
    document.documentElement.style.setProperty('--accent-blue', color);
    document.documentElement.style.setProperty('--accent-blue-glow', `rgba(${r}, ${g}, ${b}, 0.15)`);
    
    // Apply accent color to all UI elements
    if (typeof applyThemeColorsToAllUI === 'function') {
      applyThemeColorsToAllUI();
    }
    
    // Save to settings
    saveAllSettings();
  }
  
  // Color calculation helpers - Enhanced with HSL support
  function hexToRgb(hex) {
    const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
    return result ? {
      r: parseInt(result[1], 16),
      g: parseInt(result[2], 16),
      b: parseInt(result[3], 16)
    } : null;
  }
  
  function rgbToHex(r, g, b) {
    return "#" + [r, g, b].map(x => {
      const hex = Math.round(x).toString(16);
      return hex.length === 1 ? "0" + hex : hex;
    }).join("");
  }
  
  function rgbToHsl(r, g, b) {
    r /= 255;
    g /= 255;
    b /= 255;
    const max = Math.max(r, g, b);
    const min = Math.min(r, g, b);
    let h, s, l = (max + min) / 2;
    
    if (max === min) {
      h = s = 0; // achromatic
    } else {
      const d = max - min;
      s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
      switch (max) {
        case r: h = ((g - b) / d + (g < b ? 6 : 0)) / 6; break;
        case g: h = ((b - r) / d + 2) / 6; break;
        case b: h = ((r - g) / d + 4) / 6; break;
      }
    }
    return { h: h * 360, s: s * 100, l: l * 100 };
  }
  
  function hslToRgb(h, s, l) {
    h /= 360;
    s /= 100;
    l /= 100;
    let r, g, b;
    
    if (s === 0) {
      r = g = b = l; // achromatic
    } else {
      const hue2rgb = (p, q, t) => {
        if (t < 0) t += 1;
        if (t > 1) t -= 1;
        if (t < 1/6) return p + (q - p) * 6 * t;
        if (t < 1/2) return q;
        if (t < 2/3) return p + (q - p) * (2/3 - t) * 6;
        return p;
      };
      const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
      const p = 2 * l - q;
      r = hue2rgb(p, q, h + 1/3);
      g = hue2rgb(p, q, h);
      b = hue2rgb(p, q, h - 1/3);
    }
    return {
      r: Math.round(r * 255),
      g: Math.round(g * 255),
      b: Math.round(b * 255)
    };
  }
  
  function adjustHsl(color, hDelta = 0, sDelta = 0, lDelta = 0) {
    const rgb = hexToRgb(color);
    if (!rgb) return color;
    const hsl = rgbToHsl(rgb.r, rgb.g, rgb.b);
    hsl.h = Math.max(0, Math.min(360, hsl.h + hDelta));
    hsl.s = Math.max(0, Math.min(100, hsl.s + sDelta));
    hsl.l = Math.max(0, Math.min(100, hsl.l + lDelta));
    const newRgb = hslToRgb(hsl.h, hsl.s, hsl.l);
    return rgbToHex(newRgb.r, newRgb.g, newRgb.b);
  }
  
  function getLuminance(r, g, b) {
    // Relative luminance calculation (WCAG)
    const [rs, gs, bs] = [r, g, b].map(c => {
      c = c / 255;
      return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * rs + 0.7152 * gs + 0.0722 * bs;
  }
  
  function isLightColor(color) {
    const rgb = hexToRgb(color);
    if (!rgb) return false;
    return getLuminance(rgb.r, rgb.g, rgb.b) > 0.5;
  }
  
  function lightenColor(color, amount) {
    return adjustHsl(color, 0, 0, amount * 100);
  }
  
  function darkenColor(color, amount) {
    return adjustHsl(color, 0, 0, -amount * 100);
  }
  
  function saturateColor(color, amount) {
    return adjustHsl(color, 0, amount * 100, 0);
  }
  
  function desaturateColor(color, amount) {
    return adjustHsl(color, 0, -amount * 100, 0);
  }
  
  function blendColor(color1, color2, ratio) {
    const rgb1 = hexToRgb(color1);
    const rgb2 = hexToRgb(color2);
    if (!rgb1 || !rgb2) return color1;
    const r = Math.round(rgb1.r * (1 - ratio) + rgb2.r * ratio);
    const g = Math.round(rgb1.g * (1 - ratio) + rgb2.g * ratio);
    const b = Math.round(rgb1.b * (1 - ratio) + rgb2.b * ratio);
    return rgbToHex(r, g, b);
  }
  
  function rgba(color, alpha) {
    const rgb = hexToRgb(color);
    if (!rgb) return `rgba(0, 0, 0, ${alpha})`;
    return `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, ${alpha})`;
  }
  
  function calculateThemeColors(baseColor, isLightMode) {
    const rgb = hexToRgb(baseColor);
    if (!rgb) {
      // Return default colors if invalid
      return {
        bgPrimary: baseColor || '#1a1a1c',
        bgSecondary: baseColor || '#1a1a1c',
        bgTertiary: baseColor || '#1a1a1c',
        textPrimary: '#ffffff',
        textSecondary: '#e8e8ea',
        textTertiary: '#a0a0a2',
        textQuaternary: '#6e6e70',
        surfaceElevated: 'rgba(255, 255, 255, 0.03)',
        surfaceHover: 'rgba(255, 255, 255, 0.05)',
        surfaceActive: 'rgba(255, 255, 255, 0.08)',
        borderSubtle: 'rgba(255, 255, 255, 0.06)',
        borderDefault: 'rgba(255, 255, 255, 0.12)',
        pdfBodyBg: baseColor || '#1a1a1c',
        pdfToolbarBg: baseColor || '#1a1a1c',
        pdfSidebarBg: baseColor || '#1a1a1c',
        pdfMainColor: '#ffffff',
        pdfButtonHover: '#666666',
        pdfFieldBg: '#404044',
        pdfFieldBorder: '#737373',
        pdfTreeitemBg: 'rgba(255, 255, 255, 0.15)',
        pdfTreeitemHover: 'rgba(255, 255, 255, 0.2)',
        pdfDoorhangerBg: '#4a4a4f',
        pdfDialogBg: '#4a4a4f',
        rgb: rgb || { r: 26, g: 26, b: 28 }
      };
    }
    
    const isLight = isLightMode !== undefined ? isLightMode : isLightColor(baseColor);
    const hsl = rgbToHsl(rgb.r, rgb.g, rgb.b);
    
    // Determine contrast colors based on base color luminance
    const baseLuminance = getLuminance(rgb.r, rgb.g, rgb.b);
    const contrastColor = baseLuminance > 0.5 ? '#000000' : '#ffffff';
    const oppositeColor = baseLuminance > 0.5 ? '#ffffff' : '#000000';
    
    // Generate sophisticated color palette using HSL manipulation
    // Background hierarchy - different shades for different layers
    const bgPrimary = baseColor;
    // Secondary background: slightly darker/lighter with reduced saturation for subtlety
    const bgSecondary = isLight 
      ? adjustHsl(baseColor, 0, -0.15, -0.08)  // Darker, less saturated
      : adjustHsl(baseColor, 0, -0.1, 0.12);   // Lighter, less saturated
    // Tertiary background: more contrast
    const bgTertiary = isLight
      ? adjustHsl(baseColor, 0, -0.2, -0.15)  // Even darker
      : adjustHsl(baseColor, 0, -0.15, 0.2);   // Even lighter
    
    // Input fields - darker/more saturated for better contrast
    // For light mode: make darker than background
    // For dark mode: make lighter than background
    const inputBgFinal = isLight
      ? adjustHsl(baseColor, 0, -0.15, -0.15)  // Darker, less saturated for light mode
      : adjustHsl(baseColor, 0, -0.05, 0.18);  // Lighter for dark mode
    
    // Text colors - ensure proper contrast
    const textPrimary = isLight 
      ? adjustHsl(contrastColor, hsl.h, Math.min(20, hsl.s * 0.3), 10)  // Slight hue influence
      : '#ffffff';
    const textSecondary = isLight
      ? adjustHsl(contrastColor, hsl.h, Math.min(15, hsl.s * 0.2), 25)
      : blendColor('#ffffff', baseColor, 0.15);
    const textTertiary = isLight
      ? adjustHsl(contrastColor, hsl.h, Math.min(10, hsl.s * 0.15), 40)
      : blendColor('#ffffff', baseColor, 0.35);
    const textQuaternary = isLight
      ? adjustHsl(contrastColor, hsl.h, Math.min(5, hsl.s * 0.1), 60)
      : blendColor('#ffffff', baseColor, 0.5);
    
    // Surface colors - use base color with opacity for better harmony
    const surfaceBase = isLight 
      ? lightenColor(baseColor, 0.05)  // Slightly lighter
      : darkenColor(baseColor, 0.05);  // Slightly darker
    const surfaceElevated = rgba(surfaceBase, 0.4);
    const surfaceHover = rgba(surfaceBase, 0.6);
    const surfaceActive = rgba(surfaceBase, 0.8);
    
    // Border colors - derived from base with proper contrast
    const borderBase = isLight
      ? adjustHsl(baseColor, 0, -0.3, -0.2)  // Darker, less saturated
      : adjustHsl(baseColor, 0, -0.2, 0.25); // Lighter, less saturated
    const borderSubtle = rgba(borderBase, 0.3);
    const borderDefault = rgba(borderBase, 0.5);
    
    // PDF viewer colors - sophisticated shades
    const pdfBodyBg = baseColor;
    // Toolbar: slightly different shade
    const pdfToolbarBg = isLight
      ? adjustHsl(baseColor, 0, -0.1, 0.03)  // Lighter, less saturated
      : adjustHsl(baseColor, 0, -0.08, -0.06); // Darker, less saturated
    // Sidebar: different shade again
    const pdfSidebarBg = isLight
      ? adjustHsl(baseColor, 0, -0.12, 0.05)
      : adjustHsl(baseColor, 0, -0.1, -0.04);
    
    const pdfMainColor = textPrimary;
    // Button hover: use base color with adjustment
    const pdfButtonHover = isLight
      ? adjustHsl(baseColor, 0, -0.15, -0.1)
      : adjustHsl(baseColor, 0, -0.1, 0.12);
    
    // Input fields in PDF viewer - darker/more distinct
    const pdfFieldBg = isLight
      ? blendColor(adjustHsl(baseColor, 0, -0.2, -0.15), '#ffffff', 0.4)
      : adjustHsl(baseColor, 0, -0.1, 0.18);
    
    // Field borders - use border color system
    const pdfFieldBorder = isLight
      ? adjustHsl(baseColor, 0, -0.4, -0.3)
      : adjustHsl(baseColor, 0, -0.25, 0.35);
    
    // Tree items - subtle background
    const pdfTreeitemBg = isLight
      ? rgba(adjustHsl(baseColor, 0, -0.2, -0.25), 0.15)
      : rgba(adjustHsl(baseColor, 0, -0.15, 0.3), 0.15);
    const pdfTreeitemHover = isLight
      ? rgba(adjustHsl(baseColor, 0, -0.2, -0.25), 0.25)
      : rgba(adjustHsl(baseColor, 0, -0.15, 0.3), 0.25);
    
    // Doorhangers and dialogs - elevated surfaces
    const pdfDoorhangerBg = isLight
      ? blendColor(adjustHsl(baseColor, 0, -0.15, -0.1), '#ffffff', 0.5)
      : adjustHsl(baseColor, 0, -0.12, -0.1);
    const pdfDialogBg = pdfDoorhangerBg;
    
    return {
      // Base colors
      bgPrimary,
      bgSecondary,
      bgTertiary,
      // Input field background (darker for contrast)
      inputBg: inputBgFinal,
      // Text colors
      textPrimary,
      textSecondary,
      textTertiary,
      textQuaternary,
      // Surface colors
      surfaceElevated,
      surfaceHover,
      surfaceActive,
      // Border colors
      borderSubtle,
      borderDefault,
      // PDF viewer colors
      pdfBodyBg,
      pdfToolbarBg,
      pdfSidebarBg,
      pdfMainColor,
      pdfButtonHover,
      pdfFieldBg,
      pdfFieldBorder,
      pdfTreeitemBg,
      pdfTreeitemHover,
      pdfDoorhangerBg,
      pdfDialogBg,
      // RGB values for rgba calculations
      rgb: rgb
    };
  }
  
  // Apply theme color dynamically - COMPREHENSIVE VERSION
  function applyThemeColor(mode, color) {
    if (mode === 'light') {
      themeLight = color;
    } else {
      themeDark = color;
    }
    
    // Calculate all derived colors
    const lightColors = calculateThemeColors(themeLight, true);
    const darkColors = calculateThemeColors(themeDark, false);
    
    const root = document.documentElement;
    
    // Base theme colors
    root.style.setProperty('--theme-light', themeLight);
    root.style.setProperty('--theme-dark', themeDark);
    
    // Always set CSS variables for both modes (for future theme switching)
    root.style.setProperty('--bg-primary-light', lightColors.bgPrimary);
    root.style.setProperty('--bg-secondary-light', lightColors.bgSecondary);
    root.style.setProperty('--bg-tertiary-light', lightColors.bgTertiary);
    root.style.setProperty('--text-primary-light', lightColors.textPrimary);
    root.style.setProperty('--text-secondary-light', lightColors.textSecondary);
    root.style.setProperty('--text-tertiary-light', lightColors.textTertiary);
    root.style.setProperty('--text-quaternary-light', lightColors.textQuaternary);
    root.style.setProperty('--surface-elevated-light', lightColors.surfaceElevated);
    root.style.setProperty('--surface-hover-light', lightColors.surfaceHover);
    root.style.setProperty('--surface-active-light', lightColors.surfaceActive);
    root.style.setProperty('--border-subtle-light', lightColors.borderSubtle);
    root.style.setProperty('--border-default-light', lightColors.borderDefault);
    root.style.setProperty('--input-bg-light', lightColors.inputBg);
    
    root.style.setProperty('--bg-primary-dark', darkColors.bgPrimary);
    root.style.setProperty('--bg-secondary-dark', darkColors.bgSecondary);
    root.style.setProperty('--bg-tertiary-dark', darkColors.bgTertiary);
    root.style.setProperty('--text-primary-dark', darkColors.textPrimary);
    root.style.setProperty('--text-secondary-dark', darkColors.textSecondary);
    root.style.setProperty('--text-tertiary-dark', darkColors.textTertiary);
    root.style.setProperty('--text-quaternary-dark', darkColors.textQuaternary);
    root.style.setProperty('--surface-elevated-dark', darkColors.surfaceElevated);
    root.style.setProperty('--surface-hover-dark', darkColors.surfaceHover);
    root.style.setProperty('--surface-active-dark', darkColors.surfaceActive);
    root.style.setProperty('--border-subtle-dark', darkColors.borderSubtle);
    root.style.setProperty('--border-default-dark', darkColors.borderDefault);
    root.style.setProperty('--input-bg-dark', darkColors.inputBg);
    
    // Apply colors for the current theme mode (use theme variable if available, otherwise use mode)
    const currentTheme = typeof theme !== 'undefined' ? theme : (mode === 'light' ? 'light' : 'dark');
    const currentColors = currentTheme === 'light' ? lightColors : darkColors;
    const sidebar = document.getElementById('rightSidebar');
    if (sidebar && currentColors) {
      sidebar.style.background = currentColors.bgSecondary || currentColors.bgPrimary;
    }
    // Update root CSS variables for current theme
    if (currentColors) {
      root.style.setProperty('--bg-primary', currentColors.bgPrimary);
      root.style.setProperty('--bg-secondary', currentColors.bgSecondary);
      root.style.setProperty('--bg-tertiary', currentColors.bgTertiary);
      root.style.setProperty('--text-primary', currentColors.textPrimary);
      root.style.setProperty('--text-secondary', currentColors.textSecondary);
      root.style.setProperty('--text-tertiary', currentColors.textTertiary);
      root.style.setProperty('--text-quaternary', currentColors.textQuaternary);
      root.style.setProperty('--surface-elevated', currentColors.surfaceElevated);
      root.style.setProperty('--surface-hover', currentColors.surfaceHover);
      root.style.setProperty('--surface-active', currentColors.surfaceActive);
      root.style.setProperty('--border-subtle', currentColors.borderSubtle);
      root.style.setProperty('--border-default', currentColors.borderDefault);
      root.style.setProperty('--input-bg', currentColors.inputBg);
    }
    
    // Send theme colors to PDF viewer iframe (always send for current theme mode)
    const pdfFrame = document.getElementById('pdfFrame');
    if (pdfFrame && pdfFrame.contentWindow) {
      const pdfColors = currentTheme === 'light' ? lightColors : darkColors;
      if (pdfColors) {
        pdfFrame.contentWindow.postMessage({
          type: 'theme-colors-changed',
          mode: currentTheme, // Use currentTheme, not mode parameter
          colors: {
            bodyBg: pdfColors.pdfBodyBg,
            toolbarBg: pdfColors.pdfToolbarBg,
            sidebarBg: pdfColors.pdfSidebarBg,
            mainColor: pdfColors.pdfMainColor,
            buttonHover: pdfColors.pdfButtonHover,
            fieldBg: pdfColors.pdfFieldBg,
            fieldBorder: pdfColors.pdfFieldBorder,
            treeitemBg: pdfColors.pdfTreeitemBg,
            treeitemHover: pdfColors.pdfTreeitemHover,
            doorhangerBg: pdfColors.pdfDoorhangerBg,
            dialogBg: pdfColors.pdfDialogBg
          }
        }, '*');
      }
    }
    
    // Apply theme colors to all UI elements via dynamic CSS
    applyThemeColorsToAllUI();
    
    saveAllSettings();
  }
  
  // Apply theme colors to all UI elements throughout the app
  function applyThemeColorsToAllUI() {
    const root = document.documentElement;
    const currentTheme = typeof theme !== 'undefined' ? theme : 'dark';
    
    // Remove existing dynamic theme style if present
    let themeStyle = document.getElementById('dynamicThemeColors');
    if (!themeStyle) {
      themeStyle = document.createElement('style');
      themeStyle.id = 'dynamicThemeColors';
      document.head.appendChild(themeStyle);
    }
    
    // Get accent color RGB for rgba calculations
    const accentRgb = hexToRgb(accentColor);
    const accentR = accentRgb ? accentRgb.r : 139;
    const accentG = accentRgb ? accentRgb.g : 92;
    const accentB = accentRgb ? accentRgb.b : 246;
    
    // Generate comprehensive CSS using theme colors
    const css = `
      /* Library Modal */
      #libraryModalContent {
        background: var(--bg-primary) !important;
        border-color: var(--border-default) !important;
      }
      
      /* Settings Modal */
      #settingsContent {
        background: var(--bg-primary) !important;
        border-color: var(--border-default) !important;
      }
      
      /* Library Files and Folders */
      .libraryFile {
        background: var(--bg-secondary) !important;
        border-color: var(--border-subtle) !important;
        color: var(--text-primary) !important;
      }
      
      .libraryFile:hover {
        background: var(--surface-hover) !important;
        border-color: var(--border-default) !important;
      }
      
      .libraryFolder {
        background: var(--bg-secondary) !important;
        border-color: var(--border-subtle) !important;
        color: var(--text-primary) !important;
      }
      
      .libraryFolder:hover {
        background: var(--surface-hover) !important;
      }
      
      /* Buttons throughout the app */
      button:not(.bigBtn):not(.aiSendBtn):not(.sectionQuizBtnCompact):not(.sectionRegenerateBtn):not(#btnOpenFile):not(#btnSettings):not(.copyBtn):not(.fcActions button):not(.deleteBtn),
      .macBtn,
      .libraryButton,
      .folderSearchAddButton {
        background: var(--bg-secondary) !important;
        border-color: var(--border-default) !important;
        color: var(--text-primary) !important;
      }
      
      button:not(.bigBtn):not(.aiSendBtn):not(.sectionQuizBtnCompact):not(.sectionRegenerateBtn):not(#btnOpenFile):not(#btnSettings):not(.copyBtn):not(.fcActions button):not(.deleteBtn):hover,
      .macBtn:hover,
      .libraryButton:hover,
      .folderSearchAddButton:hover {
        background: var(--surface-hover) !important;
        border-color: rgba(${accentR}, ${accentG}, ${accentB}, 0.5) !important;
        color: var(--text-primary) !important;
      }
      
      /* Primary buttons - use accent color */
      button.primary,
      .macBtn.primary,
      .libraryButton.primary {
        background: rgba(${accentR}, ${accentG}, ${accentB}, 1) !important;
        border-color: rgba(${accentR}, ${accentG}, ${accentB}, 1) !important;
        color: white !important;
      }
      
      button.primary:hover,
      .macBtn.primary:hover,
      .libraryButton.primary:hover {
        background: rgba(${accentR}, ${accentG}, ${accentB}, 0.85) !important;
        border-color: rgba(${accentR}, ${accentG}, ${accentB}, 0.85) !important;
      }
      
      /* Input fields */
      input[type="text"],
      input[type="number"],
      input[type="search"],
      textarea,
      select,
      .macSelect,
      .librarySearchInput,
      .folderSearchInput {
        background: var(--input-bg) !important;
        border-color: var(--border-default) !important;
        color: var(--text-primary) !important;
      }
      
      input[type="text"]:focus,
      input[type="number"]:focus,
      input[type="search"]:focus,
      textarea:focus,
      select:focus,
      .macSelect:focus,
      .librarySearchInput:focus,
      .folderSearchInput:focus {
        border-color: rgba(${accentR}, ${accentG}, ${accentB}, 0.5) !important;
        box-shadow: 0 0 0 3px rgba(${accentR}, ${accentG}, ${accentB}, 0.2) !important;
      }
      
      /* Cards and elevated surfaces */
      .rsBox,
      .fcItem,
      .libraryFileStats,
      .quizCard {
        background: var(--bg-secondary) !important;
        border-color: var(--border-subtle) !important;
        color: var(--text-primary) !important;
      }
      
      .rsBox:hover,
      .fcItem:hover {
        background: var(--surface-hover) !important;
        border-color: var(--border-default) !important;
      }
      
      /* Quizzer elements */
      .libraryFile.quizzer-selected {
        border-color: rgba(${accentR}, ${accentG}, ${accentB}, 0.5) !important;
        background: rgba(${accentR}, ${accentG}, ${accentB}, 0.1) !important;
      }
      
      /* Headers and text */
      h1, h2, h3, h4, h5, h6,
      .libraryHeader h1,
      .settingsHeader h1 {
        color: var(--text-primary) !important;
      }
      
      /* Secondary text */
      .libraryFileStats,
      .settingsDescription,
      .libraryClickHint {
        color: var(--text-secondary) !important;
      }
      
      /* Tertiary text */
      .libraryFileDate,
      .libraryFileSize {
        color: var(--text-tertiary) !important;
      }
      
      /* Top Bar (Library/Settings buttons) */
      #topBar {
        background: var(--bg-secondary) !important;
        border-bottom-color: var(--border-subtle) !important;
      }
      
      /* Library Header */
      #libraryHeader {
        background: var(--bg-primary) !important;
        border-bottom-color: var(--border-subtle) !important;
        color: var(--text-primary) !important;
      }
      
      /* Library Content Area */
      #libraryContent {
        background: var(--bg-primary) !important;
      }
      
      /* Settings Sidebar */
      #settingsSidebar {
        background: var(--bg-tertiary) !important;
        border-right-color: var(--border-subtle) !important;
      }
      
      /* Removed settingsNavItem rules - using specific rgba rules instead for better light mode support */
      
      /* Settings Groups */
      .settingsGroup {
        border-bottom-color: var(--border-subtle) !important;
      }
      
      .settingsLabel label {
        color: var(--text-primary) !important;
      }
      
      .settingsDescription {
        color: var(--text-secondary) !important;
      }
      
      /* Quizzer Panel */
      #libraryQuizzerSettings {
        background: var(--bg-tertiary) !important;
        border-left-color: var(--border-subtle) !important;
      }
      
      /* Quiz Cards */
      .quizCard {
        background: var(--bg-secondary) !important;
        border-color: var(--border-default) !important;
        color: var(--text-primary) !important;
      }
      
      .quizCard:hover {
        background: var(--surface-hover) !important;
      }
      
      /* Folder Search */
      .folderSearchWrapper {
        background: var(--bg-secondary) !important;
        border-color: var(--border-subtle) !important;
      }
      
      /* Library File Icons */
      .libraryFileIcon {
        color: var(--text-secondary) !important;
      }
      
      /* Favorite Icon */
      .libraryFavorite {
        color: rgba(${accentR}, ${accentG}, ${accentB}, 1) !important;
      }
      
      /* Edit Icon */
      .libraryEditIcon {
        color: var(--text-tertiary) !important;
      }
      
      .libraryEditIcon:hover {
        color: rgba(${accentR}, ${accentG}, ${accentB}, 1) !important;
      }
      
      /* Search Inputs */
      #librarySearchInput,
      .folderSearchInput {
        background: var(--bg-secondary) !important;
        border-color: var(--border-default) !important;
        color: var(--text-primary) !important;
      }
      
      /* All text elements should use theme colors */
      body,
      #libraryModal,
      #settingsModal {
        color: var(--text-primary) !important;
      }
      
      /* Links */
      a {
        color: rgba(${accentR}, ${accentG}, ${accentB}, 1) !important;
      }
      
      a:hover {
        color: rgba(${accentR}, ${accentG}, ${accentB}, 0.8) !important;
      }
      
      /* Top Bar Buttons (Sidebar Buttons) */
      #sidebarButtonsContainer,
      body.light-mode #sidebarButtonsContainer,
      body.dark-mode #sidebarButtonsContainer,
      [data-theme="light"] #sidebarButtonsContainer,
      [data-theme="dark"] #sidebarButtonsContainer {
        background: var(--bg-secondary) !important;
      }
      
      #sidebarButtons,
      body.light-mode #sidebarButtons,
      body.dark-mode #sidebarButtons,
      [data-theme="light"] #sidebarButtons,
      [data-theme="dark"] #sidebarButtons {
        background: var(--bg-secondary) !important;
        border-bottom-color: var(--border-subtle) !important;
      }
      
      #sidebarButtons .bigBtn {
        background: var(--bg-secondary) !important;
        border-color: var(--border-default) !important;
        color: var(--text-primary) !important;
      }
      
      #sidebarButtons .bigBtn:hover {
        background: var(--surface-hover) !important;
        border-color: rgba(${accentR}, ${accentG}, ${accentB}, 0.5) !important;
        color: var(--text-primary) !important;
      }
      
      /* Text Highlighting */
      ::selection {
        background: rgba(${accentR}, ${accentG}, ${accentB}, 0.3) !important;
        color: var(--text-primary) !important;
      }
      
      ::-moz-selection {
        background: rgba(${accentR}, ${accentG}, ${accentB}, 0.3) !important;
        color: var(--text-primary) !important;
      }
      
      /* Quiz Cards - Enhanced Styling */
      .quizCard:hover {
        border-color: rgba(${accentR}, ${accentG}, ${accentB}, 0.5) !important;
        box-shadow: 0 4px 16px rgba(${accentR}, ${accentG}, ${accentB}, 0.2) !important;
      }
      
      .quizCard.selected {
        border-color: rgba(${accentR}, ${accentG}, ${accentB}, 0.5) !important;
        background: rgba(${accentR}, ${accentG}, ${accentB}, 0.1) !important;
      }
      
      /* Flashcard items */
      .fcItem.editing {
        background: var(--surface-elevated) !important;
        border-color: var(--border-default) !important;
      }
      
      .fcItem.editing .fcQuestion:focus,
      .fcItem.editing .fcAnswer:focus {
        border-color: rgba(${accentR}, ${accentG}, ${accentB}, 0.6) !important;
        box-shadow: 0 0 0 2px rgba(${accentR}, ${accentG}, ${accentB}, 0.15) !important;
      }
      
      /* Summary and Flashcard Sections */
      .rsSection {
        background: var(--bg-secondary) !important;
      }
      
      .sectionHeaderCompact {
        color: var(--text-primary) !important;
      }
      
      .sectionHeaderCompact h3 {
        color: var(--text-primary) !important;
      }
      
      /* Regenerate Button */
      .sectionRegenerateBtn {
        background: var(--bg-secondary) !important;
        border-color: var(--border-default) !important;
        color: var(--text-primary) !important;
      }
      
      .sectionRegenerateBtn:hover {
        background: var(--surface-hover) !important;
        border-color: rgba(${accentR}, ${accentG}, ${accentB}, 0.5) !important;
      }
      
      /* AI Badge */
      .autoGeneratedBadge {
        background: rgba(${accentR}, ${accentG}, ${accentB}, 0.2) !important;
        border-color: rgba(${accentR}, ${accentG}, ${accentB}, 0.5) !important;
        color: var(--text-primary) !important;
      }
      
      /* Summary Dropdown */
      .summaryDropdown {
        color: var(--text-primary) !important;
      }
      
      .summaryDropdownMenu {
        background: var(--bg-primary) !important;
        border-color: var(--border-default) !important;
      }
      
      .summaryDropdownOption {
        color: var(--text-primary) !important;
      }
      
      .summaryDropdownOption:hover {
        background: var(--surface-hover) !important;
      }
      
      .summaryDropdownOption.active {
        background: var(--surface-active) !important;
        color: rgba(${accentR}, ${accentG}, ${accentB}, 1) !important;
      }
    `;
    
    themeStyle.textContent = css;
  }
  
  // Initialize theme presets
  // Load custom presets from settings
  let customPresets = {};
  async function loadCustomPresets() {
    try {
      if (window.pywebview && window.pywebview.api) {
        const result = await window.pywebview.api.load_settings();
        if (result && result.ok && result.settings && result.settings.customPresets) {
          customPresets = result.settings.customPresets || {};
          return true;
        }
      }
    } catch(e) {
      console.error('Error loading custom presets:', e);
    }
    return false;
  }
  
  // Save custom presets to settings
  async function saveCustomPresets() {
    try {
      if (window.pywebview && window.pywebview.api) {
        const result = await window.pywebview.api.load_settings();
        if (result && result.ok && result.settings) {
          result.settings.customPresets = customPresets;
          await window.pywebview.api.save_settings(result.settings);
          return true;
        }
      }
    } catch(e) {
      console.error('Error saving custom presets:', e);
    }
    return false;
  }
  
  function initThemePresets() {
    const presets = {
      default: { light: '#ffffff', dark: '#1a1a1c', accent: '#8B5CF6' },
      ocean: { light: '#e3f2fd', dark: '#1e1e2e', accent: '#5a9fd4' },
      forest: { light: '#f1f8f4', dark: '#1e2a1e', accent: '#10b981' },
      sunset: { light: '#fff8e1', dark: '#2a1e2e', accent: '#f59e0b' },
      lavender: { light: '#f3e5f5', dark: '#2a1e2e', accent: '#8B5CF6' },
      minimal: { light: '#fafafa', dark: '#000000', accent: '#5a9fd4' }
    };
    
    // Merge custom presets
    Object.assign(presets, customPresets);
    
    // Check current preset
    function checkCurrentPreset() {
      const allPresetCards = document.querySelectorAll('.themePresetCard');
      allPresetCards.forEach(card => {
        const presetName = card.dataset.preset;
        const preset = presets[presetName] || customPresets[presetName];
        if (preset && 
            themeLight.toLowerCase() === preset.light.toLowerCase() &&
            themeDark.toLowerCase() === preset.dark.toLowerCase() &&
            accentColor.toLowerCase() === preset.accent.toLowerCase()) {
          card.classList.add('active');
        } else {
          card.classList.remove('active');
        }
      });
    }
    
    // Apply preset function
    function initPresetClickHandlers() {
      const allPresetCards = document.querySelectorAll('.themePresetCard');
      allPresetCards.forEach(card => {
        // Remove existing listeners by cloning
        const newCard = card.cloneNode(true);
        card.parentNode.replaceChild(newCard, card);
        
        newCard.addEventListener('click', () => {
          const presetName = newCard.dataset.preset;
          const preset = presets[presetName] || customPresets[presetName];
          if (preset) {
            // Update theme colors
            applyThemeColor('light', preset.light);
            applyThemeColor('dark', preset.dark);
            // Update accent color
            applyAccentColor(preset.accent);
            
            // Handle classic presets - set theme based on textMode
            if (presetName === 'classic-light' || preset.textMode === 'dark') {
              theme = 'light';
              applyTheme('light');
            } else if (presetName === 'classic-dark' || preset.textMode === 'light') {
              theme = 'dark';
              applyTheme('dark');
            } else {
              // For regular presets, re-apply current theme to ensure PDF viewer gets updated colors
              const currentThemeMode = typeof theme !== 'undefined' ? theme : 'dark';
              applyTheme(currentThemeMode);
            }
            
            // Update active preset
            checkCurrentPreset();
          }
        });
      });
    }
    
    // Render custom presets in the UI
    function renderCustomPresets() {
      const presetsContainer = document.getElementById('themePresets');
      if (!presetsContainer) return;
      
      // Remove existing custom preset cards (they have data-preset starting with "custom-")
      const existingCustom = presetsContainer.querySelectorAll('.themePresetCard[data-preset^="custom-"]');
      existingCustom.forEach(card => card.remove());
      
      // Add custom presets
      Object.keys(customPresets).forEach(presetName => {
        if (presetName.startsWith('custom-')) {
          const preset = customPresets[presetName];
          const card = document.createElement('div');
          card.className = 'themePresetCard';
          card.dataset.preset = presetName;
          card.innerHTML = `
            <div class="presetPreview">
              <div class="presetLight" style="background: ${preset.light};"></div>
              <div class="presetDark" style="background: ${preset.dark};"></div>
              <div class="presetAccent" style="background: ${preset.accent};"></div>
            </div>
            <div class="presetLabel">${preset.name || presetName.replace('custom-', '')}</div>
          `;
          presetsContainer.appendChild(card);
        }
      });
      
      // Re-initialize click handlers
      initPresetClickHandlers();
      checkCurrentPreset();
    }
    
    // Initial render
    renderCustomPresets();
    
    initPresetClickHandlers();
    
    // Check on load
    checkCurrentPreset();
    
    // Expose renderCustomPresets for external use
    window.renderCustomPresets = renderCustomPresets;
    
    // Re-check when colors change - use a MutationObserver or periodic check
    // Since we can't easily hook into the functions, we'll check periodically
    let lastThemeLight = themeLight;
    let lastThemeDark = themeDark;
    let lastAccentColor = accentColor;
    
    setInterval(() => {
      if (themeLight !== lastThemeLight || themeDark !== lastThemeDark || accentColor !== lastAccentColor) {
        lastThemeLight = themeLight;
        lastThemeDark = themeDark;
        lastAccentColor = accentColor;
        checkCurrentPreset();
      }
    }, 500);
  }
  
  // Initialize advanced custom colors panel
  function initAdvancedColorsPanel() {
    const toggleBtn = document.getElementById('advancedColorsToggle');
    const panel = document.getElementById('advancedColorsPanel');
    const toggleIcon = document.getElementById('advancedColorsToggleIcon');
    const customThemeInput = document.getElementById('customThemeColorInput');
    const customAccentInput = document.getElementById('customAccentColorInput');
    const textColorSelect = document.getElementById('textColorStyleSelect');
    const saveBtn = document.getElementById('saveCustomPresetBtn');
    const applyBtn = document.getElementById('applyCustomColorsBtn');
    
    if (!toggleBtn || !panel) return;
    
    let isOpen = false;
    let selectedTextMode = theme === 'dark' ? 'dark' : 'light';
    
    // Initialize text color select with current theme
    if (textColorSelect) {
      textColorSelect.value = selectedTextMode;
    }
    
    // Toggle panel expansion with smooth inline animation
    toggleBtn.addEventListener('click', (e) => {
      e.stopPropagation(); // Prevent any parent click handlers
      isOpen = !isOpen;
      
      if (isOpen) {
        panel.style.display = 'block';
        toggleIcon.style.transform = 'rotate(180deg)';
        // Start collapsed
        panel.style.maxHeight = '0';
        panel.style.padding = '0';
        panel.style.marginTop = '8px';
        panel.style.opacity = '0';
        panel.style.border = '1px solid transparent';
        
        // Animate to expanded
        setTimeout(() => {
          const targetHeight = panel.scrollHeight;
          panel.style.maxHeight = targetHeight + 20 + 'px';
          panel.style.padding = '12px';
          panel.style.opacity = '1';
          panel.style.border = '1px solid var(--border-default)';
        }, 10);
      } else {
        // Start expanded
        panel.style.maxHeight = panel.scrollHeight + 'px';
        panel.style.opacity = '1';
        
        // Animate to collapsed
        setTimeout(() => {
          panel.style.maxHeight = '0';
          panel.style.padding = '0';
          panel.style.opacity = '0';
          panel.style.border = '1px solid transparent';
          setTimeout(() => {
            panel.style.display = 'none';
          }, 300);
        }, 10);
        toggleIcon.style.transform = 'rotate(0deg)';
      }
    });
    
    // Initialize custom inputs with current values
    if (customThemeInput) {
      customThemeInput.value = theme === 'light' ? themeLight : themeDark;
    }
    if (customAccentInput) {
      customAccentInput.value = accentColor;
    }
    
    // Initialize text color select dropdown
    if (textColorSelect) {
      textColorSelect.value = selectedTextMode;
      textColorSelect.addEventListener('change', (e) => {
        selectedTextMode = e.target.value;
      });
    }
    
    // Apply custom colors immediately
    if (applyBtn) {
      applyBtn.addEventListener('click', () => {
        const themeColor = customThemeInput ? customThemeInput.value : (theme === 'light' ? themeLight : themeDark);
        const accentColorValue = customAccentInput ? customAccentInput.value : accentColor;
        const useWhiteText = textColorSelect ? textColorSelect.value === 'dark' : selectedTextMode === 'dark';
        
        // Apply colors
        applyThemeColor('light', themeColor);
        applyThemeColor('dark', themeColor);
        applyAccentColor(accentColorValue);
        
        // Set theme based on text color preference
        if (useWhiteText) {
          theme = 'dark';
          applyTheme('dark');
        } else {
          theme = 'light';
          applyTheme('light');
        }
      });
    }
    
    // Save as custom preset
    if (saveBtn) {
      saveBtn.addEventListener('click', async () => {
        const themeColor = customThemeInput ? customThemeInput.value : (theme === 'light' ? themeLight : themeDark);
        const accentColorValue = customAccentInput ? customAccentInput.value : accentColor;
        const useWhiteText = textColorSelect ? textColorSelect.value === 'dark' : selectedTextMode === 'dark';
        
        // Prompt for preset name
        const presetName = prompt('Enter a name for this preset:');
        if (!presetName || presetName.trim() === '') {
          alert('Preset name cannot be empty');
          return;
        }
        
        const presetId = 'custom-' + presetName.toLowerCase().replace(/[^a-z0-9]/g, '-');
        customPresets[presetId] = {
          name: presetName.trim(),
          light: themeColor,
          dark: themeColor,
          accent: accentColorValue,
          textMode: useWhiteText ? 'light' : 'dark'
        };
        
        // Save to settings
        await saveCustomPresets();
        
        // Re-render presets
        if (window.renderCustomPresets) {
          window.renderCustomPresets();
        }
        
        // Apply the preset
        applyThemeColor('light', themeColor);
        applyThemeColor('dark', themeColor);
        applyAccentColor(accentColorValue);
        if (useWhiteText) {
          theme = 'dark';
          applyTheme('dark');
        } else {
          theme = 'light';
          applyTheme('light');
        }
        
        alert('Custom preset saved!');
      });
    }
  }
  
  // Initialize theme color picker
  function initThemeColorPicker() {
    const lightSwatches = document.querySelectorAll('.themeColorSwatch[data-mode="light"]');
    const darkSwatches = document.querySelectorAll('.themeColorSwatch[data-mode="dark"]');
    const lightCustomInput = document.getElementById('themeColorLightCustomInput');
    const darkCustomInput = document.getElementById('themeColorDarkCustomInput');
    
    // Set active swatches based on current colors
    lightSwatches.forEach(swatch => {
      if (swatch.dataset.color.toLowerCase() === themeLight.toLowerCase()) {
        swatch.classList.add('active');
      }
      swatch.addEventListener('click', () => {
        lightSwatches.forEach(s => s.classList.remove('active'));
        swatch.classList.add('active');
        applyThemeColor('light', swatch.dataset.color);
        if (lightCustomInput) lightCustomInput.value = swatch.dataset.color;
      });
    });
    
    darkSwatches.forEach(swatch => {
      if (swatch.dataset.color.toLowerCase() === themeDark.toLowerCase()) {
        swatch.classList.add('active');
      }
      swatch.addEventListener('click', () => {
        darkSwatches.forEach(s => s.classList.remove('active'));
        swatch.classList.add('active');
        applyThemeColor('dark', swatch.dataset.color);
        if (darkCustomInput) darkCustomInput.value = swatch.dataset.color;
      });
    });
    
    // Custom color inputs
    if (lightCustomInput) {
      lightCustomInput.value = themeLight;
      lightCustomInput.addEventListener('input', (e) => {
        const newColor = e.target.value;
        lightSwatches.forEach(s => s.classList.remove('active'));
        applyThemeColor('light', newColor);
      });
    }
    
    if (darkCustomInput) {
      darkCustomInput.value = themeDark;
      darkCustomInput.addEventListener('input', (e) => {
        const newColor = e.target.value;
        darkSwatches.forEach(s => s.classList.remove('active'));
        applyThemeColor('dark', newColor);
      });
    }
  }
  
  // Initialize accent color picker
  function initAccentColorPicker() {
    const swatches = document.querySelectorAll('.accentColorSwatch');
    const customInput = document.getElementById('accentColorCustomInput');
    
    // Set active swatch based on current color
    swatches.forEach(swatch => {
      if (swatch.dataset.color.toLowerCase() === accentColor.toLowerCase()) {
        swatch.classList.add('active');
      }
      swatch.addEventListener('click', () => {
        swatches.forEach(s => s.classList.remove('active'));
        swatch.classList.add('active');
        applyAccentColor(swatch.dataset.color);
        if (customInput) customInput.value = swatch.dataset.color;
      });
    });
    
    // Custom color input
    if (customInput) {
      customInput.value = accentColor;
      customInput.addEventListener('input', (e) => {
        const newColor = e.target.value;
        // Remove active from all swatches when using custom
        swatches.forEach(s => s.classList.remove('active'));
        applyAccentColor(newColor);
      });
    }
  }
  
  // Sidebar navigation
  function switchSettingsSection(section) {
    // Update nav items
    document.querySelectorAll('.settingsNavItem').forEach(item => {
      item.classList.remove('active');
    });
    document.querySelector(`.settingsNavItem[data-section="${section}"]`).classList.add('active');
    
    // Update pages
    document.querySelectorAll('.settingsPage').forEach(page => {
      page.style.display = 'none';
    });
    
    // Update title
    const titles = {
      general: 'General',
      appearance: 'Appearance',
      pdf: 'PDF Viewer'
    };
    document.getElementById('settingsTitle').textContent = titles[section] || 'Settings';
    
    // Show selected page
    const pageMap = {
      general: 'pageGeneral',
      appearance: 'pageAppearance',
      pdf: 'pagePDF'
    };
    document.getElementById(pageMap[section]).style.display = 'block';
  }
  
  document.getElementById('btnSettings').onclick = openSettings;
  document.querySelector('.settingsCloseBtn').onclick = closeSettings;
  
  // Sidebar navigation
  document.querySelectorAll('.settingsNavItem').forEach(item => {
    item.onclick = () => switchSettingsSection(item.dataset.section);
  });
  
  // Auto-save on change (macOS style)
  document.getElementById('animationSpeedInput').addEventListener('change', saveSettings);
  document.getElementById('defaultSidebarStateInput').addEventListener('change', saveSettings);
  document.getElementById('autoSaveEnabledInput').addEventListener('change', saveSettings);
  document.getElementById('duplicatePromptBehaviorInput').addEventListener('change', saveSettings);
  // Font size - apply immediately
  const fontSizeInput = document.getElementById('fontSizeInput');
  if (fontSizeInput) {
    fontSizeInput.addEventListener('change', (e) => {
      fontSize = e.target.value;
      console.log('Font size changed to:', fontSize);
      applyFontSize(fontSize);
      saveSettings();
    });
  }
  
  // PDF zoom - apply immediately
  const pdfZoomInput = document.getElementById('pdfZoomInput');
  if (pdfZoomInput) {
    pdfZoomInput.addEventListener('change', (e) => {
      pdfZoom = e.target.value;
      saveSettings();
    });
  }
  
  // Theme mode - apply immediately
  const themeModeInput = document.getElementById('themeModeInput');
  if (themeModeInput) {
    themeModeInput.addEventListener('change', (e) => {
      themeMode = e.target.value;
      if (themeMode === 'system') {
        themeManuallySet = false;
        const systemTheme = getSystemTheme();
        if (theme !== systemTheme) {
          theme = systemTheme;
          applyTheme(theme);
        }
        setupSystemThemeListener();
      } else {
        themeManuallySet = true;
      }
      saveSettings();
    });
  }
  
  // PDF page theme - apply immediately
  const pdfPageThemeInput = document.getElementById('pdfPageThemeInput');
  if (pdfPageThemeInput) {
    pdfPageThemeInput.addEventListener('change', (e) => {
      pdfPageTheme = e.target.checked;
      console.log('PDF page theme changed to:', pdfPageTheme);
      applyPdfPageTheme(pdfPageTheme);
      saveSettings();
    });
  }
  
  // Duplicate modal button handlers
  document.getElementById('duplicateLoadExisting').onclick = async () => {
    const rememberChoice = document.getElementById('duplicateRememberChoice').checked;
    
    if (rememberChoice) {
      // Save preference to auto-load existing
      duplicatePromptBehavior = 'load';
      await saveSettings();
      console.log('💾 Saved preference: Auto-load existing lectures');
    }
    
    hideDuplicateModal();
    // Load existing library data
    await loadLibraryDataForFilename(false); // Don't show prompt again
  };
  
  document.getElementById('duplicateCreateNew').onclick = async () => {
    const rememberChoice = document.getElementById('duplicateRememberChoice').checked;
    
    if (rememberChoice) {
      // Save preference to auto-create new
      duplicatePromptBehavior = 'create';
      await saveSettings();
      console.log('💾 Saved preference: Auto-create new lectures');
    }
    
    hideDuplicateModal();
    // Modify currentFileName to add a numbered suffix
    const originalFileName = currentFileName;
    let suffix = 1;
    let newFileName = `${originalFileName}-${suffix}`;
    
    // Check if library file with this name already exists, increment suffix if needed
    while (true) {
      const checkResult = await window.pywebview.api.load_library_data(newFileName);
      if (!checkResult || !checkResult.ok || !checkResult.data || 
          (Object.keys(checkResult.data.summaries || {}).length === 0 && 
           Object.keys(checkResult.data.flashcards || {}).length === 0)) {
        break; // Found a free name
      }
      suffix++;
      newFileName = `${originalFileName}-${suffix}`;
    }
    
    // Update currentFileName to use the new name
    currentFileName = newFileName;
    console.log(`📝 Created new lecture: ${currentFileName} (original: ${originalFileName})`);
    
    // Clear library data tracking since this is a new lecture
    pagesWithLibraryData.clear();
    pageSummaryCache.clear();
    cardsByPage = {};
    
    // Don't load existing data - this is a fresh start
  };
  
  // Hotkey picker
  let hotkeyRecording = false;
  let hotkeyKeys = [];
  
  function startHotkeyRecording() {
    const picker = document.getElementById('hotkeyPicker');
    hotkeyRecording = true;
    hotkeyKeys = [];
    picker.classList.add('recording');
    picker.innerHTML = '<span class="hotkeyPlaceholder" style="color: rgba(0, 122, 255, 0.8);">Press keys...</span>';
  }
  
  function stopHotkeyRecording() {
    const picker = document.getElementById('hotkeyPicker');
    hotkeyRecording = false;
    picker.classList.remove('recording');
    
    if (hotkeyKeys.length > 1) {
      const hotkeyStr = hotkeyKeys.join('+');
      sidebarHotkey = hotkeyStr;
      updateHotkeyDisplay(hotkeyStr);
      saveSettings();
    } else {
      updateHotkeyDisplay(sidebarHotkey);
    }
  }
  
  document.getElementById('hotkeyPicker').addEventListener('click', () => {
    if (!hotkeyRecording) {
      startHotkeyRecording();
    }
  });
  
  document.addEventListener('keydown', (e) => {
    const modalOpen = document.getElementById('settingsModal').classList.contains('show');
    
    // Handle ESC to close modal or cancel hotkey recording
    if (e.key === 'Escape') {
      if (hotkeyRecording && modalOpen) {
        stopHotkeyRecording();
        e.preventDefault();
        return;
      }
      if (modalOpen) {
        closeSettings();
        e.preventDefault();
        return;
      }
    }
    
    // Handle hotkey recording
    if (!hotkeyRecording || !modalOpen) return;
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    
    e.preventDefault();
    e.stopPropagation();
    
    const parts = [];
    if (e.ctrlKey) parts.push('Ctrl');
    if (e.metaKey) parts.push('Cmd');
    if (e.altKey) parts.push('Alt');
    if (e.shiftKey) parts.push('Shift');
    
    if (e.key && !['Control', 'Meta', 'Alt', 'Shift', 'Tab', 'Escape'].includes(e.key)) {
      const keyName = e.key.length === 1 ? e.key.toUpperCase() : e.key;
      parts.push(keyName);
      hotkeyKeys = parts;
      stopHotkeyRecording();
    }
  });
  
  // ---- AI Disable Checkbox Handlers ----
  // Function to update badge appearance based on AI state
  function updateAIBadge(badgeId, checkboxId, isEnabled) {
    const badge = document.getElementById(badgeId);
    const badgeIcon = badge?.querySelector('.badgeIcon');
    const badgeText = badge?.querySelector('.badgeText');
    
    if (!badge) return;
    
    if (isEnabled) {
      badge.classList.remove('notesMode');
      badge.classList.add('aiEnabled');
      if (badgeIcon) {
        badgeIcon.textContent = '✨';
        badgeIcon.style.display = 'inline-block'; // Ensure icon is visible
        badgeIcon.style.opacity = '1'; // Ensure opacity is set
      }
      if (badgeText) badgeText.textContent = 'AI';
      badge.title = 'Click to disable AI (Notes mode)';
    } else {
      badge.classList.remove('aiEnabled');
      badge.classList.add('notesMode');
      if (badgeIcon) {
        badgeIcon.textContent = '📝';
        badgeIcon.style.display = 'inline-block'; // Ensure icon is visible
        badgeIcon.style.opacity = '1'; // Ensure opacity is set
      }
      if (badgeText) badgeText.textContent = 'Notes';
      badge.title = 'Click to enable AI generation';
    }
  }
  
  // Initialize badge click handlers
  setTimeout(() => {
    const flashcardsBadge = document.getElementById('flashcardsAIBadge');
    const summaryBadge = document.getElementById('summaryAIBadge');
    const flashcardsCheckbox = document.getElementById('disableFlashcardsAI');
    const summaryCheckbox = document.getElementById('disableSummaryAI');
    
    // Flashcards badge click handler
    if (flashcardsBadge && flashcardsCheckbox) {
      flashcardsBadge.addEventListener('click', (e) => {
        e.stopPropagation(); // Prevent event bubbling
        const newState = !flashcardsCheckbox.checked;
        flashcardsCheckbox.checked = newState;
        // Update badge immediately to prevent flicker
        updateAIBadge('flashcardsAIBadge', 'disableFlashcardsAI', !newState);
        flashcardsCheckbox.dispatchEvent(new Event('change'));
      });
      // Initialize badge state
      updateAIBadge('flashcardsAIBadge', 'disableFlashcardsAI', !flashcardsCheckbox.checked);
    }
    
    // Summary badge click handler
    if (summaryBadge && summaryCheckbox) {
      summaryBadge.addEventListener('click', (e) => {
        e.stopPropagation(); // Prevent event bubbling
        const newState = !summaryCheckbox.checked;
        summaryCheckbox.checked = newState;
        // Update badge immediately to prevent flicker
        updateAIBadge('summaryAIBadge', 'disableSummaryAI', !newState);
        summaryCheckbox.dispatchEvent(new Event('change'));
      });
      // Initialize badge state
      updateAIBadge('summaryAIBadge', 'disableSummaryAI', !summaryCheckbox.checked);
    }
  }, 100);

  document.getElementById('disableSummaryAI').addEventListener('change', function(e) {
    disableSummaryAI = e.target.checked;
    console.log(`[AI Control] Summary AI ${disableSummaryAI ? 'disabled' : 'enabled'}`);
    
    // Update badge appearance
    updateAIBadge('summaryAIBadge', 'disableSummaryAI', !disableSummaryAI);
    
    // Disable/enable regenerate button
    const btnRegenerate = document.getElementById('btnRegenerateSummary');
    
    if (disableSummaryAI) {
      if (btnRegenerate) {
        btnRegenerate.style.opacity = '0.5';
        btnRegenerate.style.pointerEvents = 'none';
      }
    } else {
      if (btnRegenerate) {
        btnRegenerate.style.opacity = '0.8';
        btnRegenerate.style.pointerEvents = 'auto';
      }
    }
  });
  
  document.getElementById('disableFlashcardsAI').addEventListener('change', function(e) {
    disableFlashcardsAI = e.target.checked;
    console.log(`[AI Control] Flashcards AI ${disableFlashcardsAI ? 'disabled' : 'enabled'}`);
    
    // Update badge appearance
    updateAIBadge('flashcardsAIBadge', 'disableFlashcardsAI', !disableFlashcardsAI);
    
    // Disable/enable regenerate button
    const btnRegenerate = document.getElementById('btnRegenerateFlashcards');
    if (disableFlashcardsAI) {
      btnRegenerate.style.opacity = '0.5';
      btnRegenerate.style.pointerEvents = 'none';
    } else {
      btnRegenerate.style.opacity = '0.8';
      btnRegenerate.style.pointerEvents = 'auto';
    }
  });
  
  // ---- Summary Dropdown (Summarize/Explain toggle) ----
  let currentSummaryMode = 'summarize';
  const summaryDropdown = document.getElementById('summaryDropdown');
  
  if (summaryDropdown) {
    const dropdownOptions = summaryDropdown.querySelectorAll('.summaryDropdownOption');
    const dropdownMenu = summaryDropdown.querySelector('.summaryDropdownMenu');
    const summaryTitle = summaryDropdown.querySelector('.summaryTitle');
    
    // Initialize dropdown
    dropdownOptions[0].classList.add('active');
    if (summaryTitle) summaryTitle.textContent = 'Summary';
    
    // Handle dropdown toggle - only toggle if clicking the title/arrow area, not the menu
    const summaryTitleEl = summaryDropdown.querySelector('.summaryTitle');
    const dropdownArrowEl = summaryDropdown.querySelector('.dropdownArrow');
    
    if (summaryTitleEl) {
      summaryTitleEl.addEventListener('click', (e) => {
        e.stopPropagation();
        summaryDropdown.classList.toggle('active');
      });
    }
    
    if (dropdownArrowEl) {
      dropdownArrowEl.addEventListener('click', (e) => {
        e.stopPropagation();
        summaryDropdown.classList.toggle('active');
      });
    }
    
    // Handle option selection
    dropdownOptions.forEach(option => {
      option.onclick = (e) => {
        e.stopPropagation();
        e.preventDefault();
        
        const mode = option.dataset.mode;
        
        // Close dropdown first
        summaryDropdown.classList.remove('active');
        
        if (disableSummaryAI) {
          alert('AI generation is disabled. Enable AI generation to use this feature.');
          return;
        }
        
    if (!currentPage || !currentPageText) {
      alert('No page text available. Please wait for the page to load.');
      return;
    }
        
        // Don't do anything if clicking the already active mode
        if (mode === currentSummaryMode) {
          return;
        }
        
        // Switch modes
        currentSummaryMode = mode;
        dropdownOptions.forEach(opt => opt.classList.remove('active'));
        option.classList.add('active');
        
        // Update header text
        if (summaryTitle) {
          summaryTitle.textContent = mode === 'summarize' ? 'Summary' : 'Explanation';
        }
        
        // Trigger action when switching
        console.log(`[Summary Dropdown] Switched to ${mode} for page ${currentPage}`);
        doPageSummary(currentPage, currentPageText, mode);
      };
    });
    
    // Close dropdown when clicking outside
    document.addEventListener('click', (e) => {
      if (!summaryDropdown.contains(e.target)) {
        summaryDropdown.classList.remove('active');
      }
    });
  }
  
  // ---- Regenerate button handlers ----
  const btnRegenerateSummary = document.getElementById('btnRegenerateSummary');
  const summaryExtraInstruction = document.getElementById('summaryExtraInstruction');
  
  if (btnRegenerateSummary) {
    btnRegenerateSummary.onclick = async () => {
      if (disableSummaryAI) {
        alert('AI generation is disabled. Uncheck "AI off / Notes mode" to use this feature.');
        return;
      }
    if (!currentPage || !currentPageText) {
      alert('No page text available. Please wait for the page to load.');
      return;
    }
      const extraInstruction = summaryExtraInstruction ? summaryExtraInstruction.value.trim() : '';
      console.log(`[btnRegenerateSummary] Regenerating ${currentSummaryMode} for page ${currentPage}${extraInstruction ? ` with instruction: "${extraInstruction}"` : ''}`);
      // Remove from library data tracking so it regenerates
      pagesWithLibraryData.delete(currentPage);
      pageSummaryCache.delete(currentPage);
      // Force regenerate with current mode and extra instruction
      await doPageSummary(currentPage, currentPageText, currentSummaryMode, extraInstruction);
    };
  }
  
  // Allow Enter key in instruction input to trigger regenerate
  if (summaryExtraInstruction) {
    summaryExtraInstruction.addEventListener('keydown', async (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (btnRegenerateSummary && !disableSummaryAI && currentPage && currentPageText) {
          btnRegenerateSummary.click();
        }
      }
    });
  }
  
  document.getElementById('btnRegenerateFlashcards').onclick = async () => {
      if (disableFlashcardsAI) {
        alert('AI generation is disabled. Uncheck "AI off / Notes mode" to use this feature.');
        return;
      }
      if (!currentPage || !currentPageText) {
        alert('No page text available. Please wait for the page to load.');
        return;
      }
    const flashcardsExtraInstruction = document.getElementById('flashcardsExtraInstruction');
      const extraInstruction = flashcardsExtraInstruction ? flashcardsExtraInstruction.value.trim() : '';
      console.log(`[btnRegenerateFlashcards] Regenerating flashcards for page ${currentPage}${extraInstruction ? ` with instruction: "${extraInstruction}"` : ''}`);
      // Remove from library data tracking so it regenerates
      pagesWithLibraryData.delete(currentPage);
      setPageCards(currentPage, []);
    // Force regenerate - note: doGenerateFlashcards doesn't support extraInstruction yet
    await doGenerateFlashcards(currentPage, currentPageText);
    };
  
  // Allow Enter key in flashcards instruction input to trigger regenerate
  const flashcardsExtraInstruction = document.getElementById('flashcardsExtraInstruction');
  if (flashcardsExtraInstruction) {
    flashcardsExtraInstruction.addEventListener('keydown', async (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        const btnRegenerateFlashcards = document.getElementById('btnRegenerateFlashcards');
        if (btnRegenerateFlashcards && !disableFlashcardsAI && currentPage && currentPageText) {
          btnRegenerateFlashcards.click();
        }
      }
    });
  }
  
  // ---- Save/Clear button handlers ----
  document.getElementById('clearSummary').onclick = clearSummary;
  document.getElementById('saveExplain').onclick = saveCurrentExplain;
  document.getElementById('clearExplain').onclick = clearExplain;
  
  // ---- Ask AI functionality ----
  async function askAI(){
    const question = aiQuestion.value.trim();
    if (!question) return;
    
    try {
      setBox(aiAnswer, 'Thinking...');
      if (!window.pywebview || !window.pywebview.api) { 
        setBox(aiAnswer, 'Bridge unavailable.'); 
        return; 
      }
      
      // Build context from current page and nearby pages
      let context = '';
      
      // Current page summary (prioritize)
      if (currentPage && pageSummaryCache.has(currentPage)) {
        context = `**Page ${currentPage}:**\n${pageSummaryCache.get(currentPage).summary}\n\n`;
      }
      
      // Add current page full text if available
      if (currentPage && documentPages[currentPage]) {
        const pageText = documentPages[currentPage].substring(0, 3000);
        context += `**Page ${currentPage} full text:**\n${pageText}\n\n`;
      }
      
      // Add context from other pages if available (limit to first 3-4 pages worth)
      const pageNums = Object.keys(documentPages).map(Number).sort((a,b)=>a-b);
      let remainingChars = 4000;
      for (const pg of pageNums) {
        if (pg === currentPage) continue; // Already added
        if (remainingChars <= 0) break;
        const text = documentPages[pg].substring(0, Math.min(1000, remainingChars));
        context += `**Page ${pg}:** ${text}\n\n`;
        remainingChars -= text.length;
      }
      
      // Enable streaming for Ask AI
      const res = await window.pywebview.api.ask_ai(question, context, currentPage, true);  // stream=true
      if (res && res.ok) {
        if (res.streaming) {
          // Streaming mode - initialize UI
          streamingAIBuffer = ''; // Clear buffer for new stream
          setBox(aiAnswer, ''); // Clear "Thinking..." message
          aiAnswer.classList.remove('rsEmpty');
          return; // Updates will come via window.updateStreamingAI
        } else {
          setBox(aiAnswer, res.answer.trim());
        }
      } else {
        setBox(aiAnswer, (res && res.error) ? ('Error: ' + res.error) : 'Failed.');
        aiAnswer.classList.remove('rsEmpty');
      }
    } catch(e) {
      console.error('Ask AI error:', e);
      setBox(aiAnswer, 'Error: ' + e.message);
    }
  }
  
  document.getElementById('btnAskAI').onclick = askAI;
  document.getElementById('clearAiAnswer').onclick = ()=> {
    setBox(aiAnswer, '');
    const aiActions = document.getElementById('aiActions');
    if (aiActions) aiActions.style.display = 'none';
  };
  
  // Allow Enter key to submit (Shift+Enter for new line)
  aiQuestion.addEventListener('keydown', (e)=>{
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      askAI();
    }
  });
  
  // Handle placeholder visibility for contenteditable elements
  function updatePlaceholderVisibility(el) {
    if (!el.hasAttribute('data-placeholder')) return;
    const hasContent = el.textContent.trim().length > 0;
    if (hasContent) {
      el.classList.remove('rsEmpty');
    } else {
      el.classList.add('rsEmpty');
    }
  }
  
  // ---- Auto-save on contenteditable changes ----
  rsSummary.addEventListener('input', () => {
    updatePlaceholderVisibility(rsSummary);
    if (currentPage && getBoxText(rsSummary).trim()) {
      // Auto-save as user types (with debouncing)
      clearTimeout(rsSummary.saveTimeout);
      rsSummary.saveTimeout = setTimeout(() => {
        savePageSummary(currentPage, getBoxText(rsSummary).trim());
      }, 1000);
    }
  });
  
  rsSummary.addEventListener('focus', () => {
    // If only placeholder text, clear it on focus
    const placeholder = rsSummary.getAttribute('data-placeholder');
    if (placeholder && rsSummary.textContent.trim() === placeholder.trim()) {
      rsSummary.textContent = '';
    }
  });
  
  rsSummary.addEventListener('blur', () => {
    updatePlaceholderVisibility(rsSummary);
  });
  
  rsExplain.addEventListener('input', () => {
    updatePlaceholderVisibility(rsExplain);
    // Auto-save term explanations
    clearTimeout(rsExplain.saveTimeout);
    rsExplain.saveTimeout = setTimeout(() => {
      console.log('Auto-saved term explanation');
    }, 1000);
  });
  
  rsExplain.addEventListener('focus', () => {
    // If only placeholder text, clear it on focus
    const placeholder = rsExplain.getAttribute('data-placeholder');
    if (placeholder && rsExplain.textContent.trim() === placeholder.trim()) {
      rsExplain.textContent = '';
    }
  });
  
  rsExplain.addEventListener('blur', () => {
    updatePlaceholderVisibility(rsExplain);
  });


  // ---- Inject selection/page hooks into PDF.js iframe ----
  frame.addEventListener('load', () => {
    try {
      const w = frame.contentWindow;
      const js = `
        (function(){
          const state = { mode: 'select', items: [] }; // items: {type:'stroke'|'text', page, ...}
          const pageTextCache = new Map(); // pageNo -> text
          function enableSelectionAndSidebar(){
            try {
              if (window.PDFViewerApplicationOptions && PDFViewerApplicationOptions.set) {
                PDFViewerApplicationOptions.set('textLayerMode', 2);
              }
              if (window.PDFViewerApplication && PDFViewerApplication.eventBus) {
                try { PDFViewerApplication.eventBus.dispatch('switchtool', { tool: 0 }); } catch(e) {}
                try { PDFViewerApplication.eventBus.dispatch('togglehandtool', { isActive: false }); } catch(e) {}
              }
              const style = document.createElement('style');
              style.textContent = '.textLayer, .textLayer *, #outerContainer:active .textLayer, #outerContainer:active .textLayer * { user-select: text !important; -webkit-user-select: text !important; -moz-user-select: text !important; -ms-user-select: text !important; } #viewerContainer, #viewerContainer:active { user-select: auto !important; -webkit-user-select: auto !important; } .annoLayer{position:absolute;inset:0;pointer-events:none;} /* Disable text selection during PDF.js sidebar resize to prevent blue flash */ #outerContainer[class*="resizing"], #outerContainer[class*="resizing"] *, #outerContainer[class*="moving"], #outerContainer[class*="moving"] *, #sidebarContainer[class*="resizing"], #sidebarContainer[class*="resizing"] *, body[class*="resizing"] #outerContainer, body[class*="resizing"] #outerContainer *, body.sidebar-resizing #outerContainer, body.sidebar-resizing #outerContainer *, #outerContainer.sidebar-resizing, #outerContainer.sidebar-resizing * { user-select: none !important; -webkit-user-select: none !important; -moz-user-select: none !important; -ms-user-select: none !important; } /* Disable text selection on sidebar drag handle and during drag - but allow text layer */ #sidebarResizer, #sidebarResizer *, #outerContainer:active:not(:has(.textLayer)) { user-select: none !important; -webkit-user-select: none !important; } /* Ensure sidebar buttons are clickable */ #toolbarSidebar button, #sidebarViewButtons button, #viewThumbnail, #viewOutline, #viewAttachments, #viewLayers { pointer-events: auto !important; cursor: pointer !important; }';
              document.head.appendChild(style);
              const app = window.PDFViewerApplication;
              const tryOpen = () => { try { app.pdfSidebar.open(); app.pdfSidebar.switchView(1); } catch(e){} };
              tryOpen(); setTimeout(tryOpen,200); setTimeout(tryOpen,600);
              
              // Disable text selection during sidebar resize to prevent blue flash
              let isSidebarResizing = false;
              const outerContainer = document.getElementById('outerContainer');
              if (outerContainer) {
                // Listen for mousedown on sidebar resize handle - try multiple selectors
                const findResizeHandle = () => {
                  return document.querySelector('#outerContainer .splitToolbarButton, #outerContainer .sidebarResizer, #outerContainer [class*="resizer"], #outerContainer [class*="Resizer"], #outerContainer [style*="cursor: ew-resize"], #outerContainer [style*="cursor: col-resize"]') ||
                         document.querySelector('#outerContainer > div:first-child') || // First child might be resize handle
                         outerContainer; // Fallback to entire container
                };
                
                // Try to find resize handle after a delay (PDF.js might not be ready)
                setTimeout(() => {
                  const resizeHandle = findResizeHandle();
                  if (resizeHandle) {
                    resizeHandle.addEventListener('mousedown', function(e) {
                      // Check if cursor indicates resize or if clicking near sidebar edge
                      const style = window.getComputedStyle(e.target);
                      if (style.cursor === 'ew-resize' || style.cursor === 'col-resize' || 
                          e.target.classList.toString().includes('resizer') ||
                          e.target.classList.toString().includes('Resizer')) {
                        isSidebarResizing = true;
                        outerContainer.classList.add('sidebar-resizing');
                        document.body.classList.add('sidebar-resizing');
                      }
                    }, true); // Use capture phase to catch early
                  }
                  
                  // Also listen on entire container for mousedown with resize cursor
                  outerContainer.addEventListener('mousedown', function(e) {
                    const style = window.getComputedStyle(e.target);
                    if (style.cursor === 'ew-resize' || style.cursor === 'col-resize') {
                      isSidebarResizing = true;
                      outerContainer.classList.add('sidebar-resizing');
                      document.body.classList.add('sidebar-resizing');
                    }
                  }, true);
                }, 500);
                
                // Listen for mouseup to re-enable text selection
                document.addEventListener('mouseup', function() {
                  if (isSidebarResizing) {
                    setTimeout(() => {
                      isSidebarResizing = false;
                      outerContainer.classList.remove('sidebar-resizing');
                      document.body.classList.remove('sidebar-resizing');
                    }, 50); // Small delay to prevent flicker
                  }
                });
              }
            } catch(e){}
          }
          function ensureAnnoLayers(){
            document.querySelectorAll('.page').forEach(pageDiv=>{
              if (pageDiv.querySelector('canvas.annoLayer')) return;
              const wrap = pageDiv.querySelector('.canvasWrapper') || pageDiv;
              const c = document.createElement('canvas');
              c.className = 'annoLayer';
              c.style.zIndex = 50;
              wrap.style.position = wrap.style.position || 'relative';
              wrap.appendChild(c);
              // Debounce resize to prevent lag during sidebar animation
              let resizeTimeout = null;
              const resize = ()=>{
                // Skip resize if sidebar is animating
                const work = document.getElementById('work');
                if (work && work.classList.contains('sidebar-animating')) {
                  return;
                }
                
                // Debounce resize calls
                if (resizeTimeout) {
                  clearTimeout(resizeTimeout);
                }
                resizeTimeout = setTimeout(() => {
                const r = wrap.getBoundingClientRect();
                c.width = Math.max(1, Math.floor(r.width));
                c.height = Math.max(1, Math.floor(r.height));
                redrawPageOverlays(pageDiv);
                }, 16); // ~60fps debounce
              };
              new ResizeObserver(resize).observe(wrap);
              resize();

              // Draw tool
              let drawing = false; let points = [];
              c.addEventListener('mousedown', (e)=>{
                if (state.mode!=='draw') return;
                drawing = true; points = [];
                const r = c.getBoundingClientRect();
                const xf = (e.clientX - r.left) / r.width;
                const yf = (e.clientY - r.top)  / r.height;
                points.push([xf, yf]);
                c.getContext('2d').beginPath();
                c.getContext('2d').moveTo(xf*r.width, yf*r.height);
              });
              c.addEventListener('mousemove', (e)=>{
                if (!drawing) return;
                const r = c.getBoundingClientRect();
                const xf = (e.clientX - r.left) / r.width;
                const yf = (e.clientY - r.top)  / r.height;
                points.push([xf, yf]);
                const ctx = c.getContext('2d');
                ctx.lineWidth = Math.max(2, r.height * 0.003);
                ctx.strokeStyle = '#ff2d55';
                ctx.lineTo(xf*r.width, yf*r.height);
                ctx.stroke();
              });
              c.addEventListener('mouseup', ()=>{
                if (!drawing) return;
                drawing = false;
                const page = pageDiv.dataset.pageNumber || null;
                if (points.length>1) state.items.push({type:'stroke', page: Number(page), points: points, color:'#ff2d55', width_frac: 0.003});
                redrawPageOverlays(pageDiv);
              });

              // Text tool
              c.addEventListener('click', (e)=>{
                if (state.mode!=='text') return;
                const text = prompt('Enter text:'); if (!text) return;
                const r = c.getBoundingClientRect();
                const xf = (e.clientX - r.left) / r.width;
                const yf = (e.clientY - r.top)  / r.height;
                const page = pageDiv.dataset.pageNumber || null;
                state.items.push({type:'text', page: Number(page), x: xf, y: yf, text: text, font_size_frac: 0.02, color:'#000000'});
                redrawPageOverlays(pageDiv);
              });
            });
          }
          function redrawPageOverlays(pageDiv){
            const c = pageDiv.querySelector('canvas.annoLayer'); if (!c) return;
            const ctx = c.getContext('2d'); ctx.clearRect(0,0,c.width,c.height);
            const pageNo = Number(pageDiv.dataset.pageNumber || 0);
            state.items.filter(it=>it.page===pageNo).forEach(it=>{
              if (it.type==='stroke') {
                const w = c.width, h = c.height;
                ctx.beginPath();
                let first=true;
                it.points.forEach(p=>{
                  const x=p[0]*w, y=p[1]*h;
                  if (first){ ctx.moveTo(x,y); first=false; } else { ctx.lineTo(x,y); }
                });
                ctx.lineWidth = Math.max(2, h*(it.width_frac||0.003));
                ctx.strokeStyle = it.color || '#ff2d55';
                ctx.stroke();
              } else if (it.type==='text') {
                const w = c.width, h = c.height;
                ctx.fillStyle = it.color || '#000000';
                const px = Math.max(10, Math.floor(h*(it.font_size_frac||0.02)));
                ctx.font = px + 'px Helvetica, Arial, sans-serif';
                ctx.fillText(it.text, it.x*w, it.y*h);
              }
            });
          }
          function redrawAll(){ document.querySelectorAll('.page').forEach(redrawPageOverlays); }

          // Messaging with parent
          window.addEventListener('message', (ev)=>{
            const d = ev.data || {};
            if (d.type==='ann-set-mode'){
              state.mode = d.mode || 'select';
              const canvases = document.querySelectorAll('canvas.annoLayer');
              canvases.forEach(c=> c.style.pointerEvents = (state.mode==='draw'||state.mode==='text') ? 'auto' : 'none');
              try {
                if (window.PDFViewerApplication && PDFViewerApplication.eventBus) {
                  PDFViewerApplication.eventBus.dispatch('switchtool', { tool: 0 });
                }
              } catch(e){}
            }
            if (d.type==='ann-request-export'){
              parent.postMessage({type:'ann-export', payload: { items: state.items } }, '*');
            }
            if (d.type==='enter-presentation'){
              try { window.PDFViewerApplication.requestPresentationMode(); } catch(e){}
            }
            if (d.type==='get-all-text'){
              // Collect all cached page text and send to parent
              const allText = {};
              pageTextCache.forEach((text, pageNum) => {
                allText[pageNum] = text;
              });
              parent.postMessage({type:'all-text', pages: allText}, '*');
            }
            if (d.type==='request-page-text' && d.page){
              // Parent is requesting text for a specific page
              console.log(\`[PDF.js] Parent requested text for page \${d.page}\`);
              sendPageText(d.page);
            }
          });

          // Selection: word -> definition; phrase -> summary
          let lastSelection = '';
          let selectionTimeout = null;
          let currentSelectionRange = null; // Store current selection range for highlighting
          
          // Function to highlight selected text in PDF
          window.highlightSelectedText = function() {
            try {
              const sel = window.getSelection();
              let range = null;
              
              if (sel && sel.rangeCount > 0) {
                range = sel.getRangeAt(0);
              } else if (currentSelectionRange) {
                // Use stored range if selection was cleared
                range = currentSelectionRange.cloneRange();
                sel.removeAllRanges();
                sel.addRange(range);
              } else {
                console.warn('[PDF.js] No selection range available');
                return;
              }
              
              const selectedText = range.toString().trim();
              if (!selectedText) {
                console.warn('[PDF.js] No text selected');
                return;
              }
              
              // Try to use surroundContents first (simplest approach)
              try {
                // Check if range can be surrounded (must be within a single element or have a common ancestor)
                const highlightSpan = document.createElement('span');
                highlightSpan.className = 'highlight';
                highlightSpan.setAttribute('data-highlighted', 'true');
                
                // Clone range to avoid modifying original
                const rangeClone = range.cloneRange();
                
                // Try surroundContents - this works if selection is within a single parent
                rangeClone.surroundContents(highlightSpan);
                
                // Clear selection
                if (sel.rangeCount > 0) {
                  sel.removeAllRanges();
                }
                
                console.log('[PDF.js] ✅ Text highlighted successfully using surroundContents');
                return;
              } catch(e) {
                console.log('[PDF.js] surroundContents failed, trying manual approach:', e.message);
              }
              
              // Fallback: manual highlighting for complex selections
              try {
                const startContainer = range.startContainer;
                const endContainer = range.endContainer;
                const startOffset = range.startOffset;
                const endOffset = range.endOffset;
                
                // If selection is within a single text node
                if (startContainer === endContainer && startContainer.nodeType === 3) {
                  const textNode = startContainer;
                  const text = textNode.textContent;
                  const before = text.substring(0, startOffset);
                  const selected = text.substring(startOffset, endOffset);
                  const after = text.substring(endOffset);
                  
                  if (selected) {
                    const parent = textNode.parentNode;
                    const highlightSpan = document.createElement('span');
                    highlightSpan.className = 'highlight';
                    highlightSpan.setAttribute('data-highlighted', 'true');
                    
                    // Copy styles from parent if it's a span
                    if (parent && parent.tagName === 'SPAN') {
                      const style = window.getComputedStyle(parent);
                      ['position', 'left', 'top', 'fontSize', 'fontFamily', 'transform', 'transformOrigin', 'lineHeight'].forEach(prop => {
                        const value = style[prop] || style.getPropertyValue(prop);
                        if (value) {
                          highlightSpan.style[prop] = value;
                        }
                      });
                    }
                    
                    highlightSpan.textContent = selected;
                    
                    // Replace text node
                    if (before) {
                      parent.insertBefore(document.createTextNode(before), textNode);
                    }
                    parent.insertBefore(highlightSpan, textNode);
                    if (after) {
                      parent.insertBefore(document.createTextNode(after), textNode);
                    }
                    parent.removeChild(textNode);
                    
                    // Clear selection
                    if (sel.rangeCount > 0) {
                      sel.removeAllRanges();
                    }
                    
                    console.log('[PDF.js] ✅ Text highlighted successfully (single node)');
                    return;
                  }
                }
                
                // Multi-node selection - wrap each affected span
                const walker = document.createTreeWalker(
                  range.commonAncestorContainer,
                  NodeFilter.SHOW_TEXT,
                  null
                );
                
                const textNodes = [];
                let node;
                while (node = walker.nextNode()) {
                  const nodeRange = document.createRange();
                  nodeRange.selectNodeContents(node);
                  
                  // Check if this node intersects with selection
                  if (range.compareBoundaryPoints(Range.START_TO_END, nodeRange) <= 0 &&
                      range.compareBoundaryPoints(Range.END_TO_START, nodeRange) >= 0) {
                    textNodes.push(node);
                  } else if (node === startContainer || node === endContainer) {
                    textNodes.push(node);
                  }
                }
                
                // Process text nodes
                for (let i = 0; i < textNodes.length; i++) {
                  const textNode = textNodes[i];
                  const parent = textNode.parentNode;
                  const text = textNode.textContent;
                  
                  let startIdx = 0;
                  let endIdx = text.length;
                  
                  if (textNode === startContainer) {
                    startIdx = startOffset;
                  }
                  if (textNode === endContainer) {
                    endIdx = endOffset;
                  }
                  
                  if (startIdx >= endIdx) continue;
                  
                  const before = text.substring(0, startIdx);
                  const selected = text.substring(startIdx, endIdx);
                  const after = text.substring(endIdx);
                  
                  if (selected) {
                    const highlightSpan = document.createElement('span');
                    highlightSpan.className = 'highlight';
                    highlightSpan.setAttribute('data-highlighted', 'true');
                    
                    // Copy styles from parent
                    if (parent && parent.tagName === 'SPAN') {
                      const style = window.getComputedStyle(parent);
                      ['position', 'left', 'top', 'fontSize', 'fontFamily', 'transform', 'transformOrigin', 'lineHeight'].forEach(prop => {
                        const value = style[prop] || style.getPropertyValue(prop);
                        if (value) {
                          highlightSpan.style[prop] = value;
                        }
                      });
                    }
                    
                    highlightSpan.textContent = selected;
                    
                    // Replace text node
                    if (before) {
                      parent.insertBefore(document.createTextNode(before), textNode);
                    }
                    parent.insertBefore(highlightSpan, textNode);
                    if (after) {
                      parent.insertBefore(document.createTextNode(after), textNode);
                    }
                    parent.removeChild(textNode);
                  }
                }
                
                // Clear selection
                if (sel.rangeCount > 0) {
                  sel.removeAllRanges();
                }
                
                console.log('[PDF.js] ✅ Text highlighted successfully (multi-node)');
              } catch(e) {
                console.error('[PDF.js] Manual highlight error:', e);
              }
            } catch(e) {
              console.error('[PDF.js] Highlight error:', e);
            }
          };
          
          document.addEventListener('mouseup', ()=>{
            clearTimeout(selectionTimeout);
            selectionTimeout = setTimeout(()=>{
              try {
                const sel = window.getSelection ? window.getSelection() : null;
                const raw = sel ? (sel.toString()||'').trim() : '';
                
                // Store selection range for highlighting
                if (sel && sel.rangeCount > 0) {
                  currentSelectionRange = sel.getRangeAt(0).cloneRange();
                } else {
                  currentSelectionRange = null;
                }
                
                // Ignore if no selection or same as last
                if (!raw || raw === lastSelection) return;
                lastSelection = raw;
                
                console.log(\`[PDF.js] 📝 Selection: "\${raw.substring(0, 50)}\${raw.length > 50 ? '...' : ''}"\`);
                
                let page=null;
                try {
                  const node = sel.anchorNode && (sel.anchorNode.nodeType===1 ? sel.anchorNode : sel.anchorNode.parentElement);
                  const pg = node ? node.closest('.page') : null;
                  page = pg ? (pg.dataset.pageNumber || null) : null;
                } catch(e){}
                
                const isSingleWord = !/\s/.test(raw);
                console.log(\`[PDF.js] Selection is \${isSingleWord ? 'single word' : 'phrase'}, page: \${page}\`);
                
                if (isSingleWord) {
                  console.log(\`[PDF.js] 🔤 Sending define-term for: "\${raw}"\`);
                  // Store range for highlighting
                  if (sel && sel.rangeCount > 0) {
                    currentSelectionRange = sel.getRangeAt(0).cloneRange();
                  }
                  parent.postMessage({ type: 'define-term', text: raw, page: page, highlight: true }, '*');
                } else {
                  console.log(\`[PDF.js] 📄 Sending summarize-snippet for: "\${raw.substring(0, 30)}..."\`);
                  parent.postMessage({ type: 'summarize-snippet', text: raw, page: page }, '*');
                }
                
                // Reset lastSelection after 2 seconds so same word can be selected again
                setTimeout(() => { lastSelection = ''; }, 2000);
              } catch(e) {
                console.error('[PDF.js] Selection error:', e);
              }
            }, 50);
          });

          // Page text → parent (with fallback if not cached)
          function sendPageText(pg){
            try {
              const pageDiv = document.querySelector(\`.page[data-page-number="\${pg}"]\`);
              const tl = pageDiv ? pageDiv.querySelector('.textLayer') : null;
              const txt = tl ? (tl.innerText || '') : '';
              console.log(\`[sendPageText] Page \${pg}: found textLayer=\${!!tl}, text length=\${txt.length}\`);
              
              if (txt && txt.trim()) {
                console.log(\`[sendPageText] ✓ Sending \${txt.length} chars for page \${pg}\`);
                parent.postMessage({ type:'page-text', page: pg, text: txt }, '*');
              } else {
                console.log(\`[sendPageText] ⚠️ No text found for page \${pg}\`);
              }
            } catch(e){
              console.log(\`[sendPageText] ❌ Error for page \${pg}:\`, e);
            }
          }

          // Track page changes with multiple events for reliability
          let lastReportedPage = null;
          
          const handlePageChange = (pg) => {
            if (!pg || pg === lastReportedPage) return;
            lastReportedPage = pg;
            
            console.log(\`[PDF.js] ====== PAGE CHANGED TO: \${pg} ======\`);
            
            // Send page change event to parent
            parent.postMessage({ type: 'page-changed', page: pg }, '*');
            
            const txt = pageTextCache.get(pg);
            if (txt && txt.trim()) {
              console.log(\`[PDF.js] Using cached text for page \${pg}\`);
              parent.postMessage({ type:'page-text', page: pg, text: txt }, '*');
            } else {
              console.log(\`[PDF.js] No cached text for page \${pg}, scheduling extraction\`);
              // Only schedule one attempt, not two (prevents duplicate API calls)
              setTimeout(()=>sendPageText(pg), 200);
            }
          };

          try {
            const eb = window.PDFViewerApplication.eventBus;

            eb.on('textlayerrendered', (e)=>{
              try {
                const pg = e.pageNumber;
                const pageDiv = document.querySelector(\`.page[data-page-number="\${pg}"]\`);
                const tl = pageDiv ? pageDiv.querySelector('.textLayer') : null;
                if (tl) {
                  const txt = tl.innerText || '';
                  if (txt && txt.trim()) {
                    console.log(\`[PDF.js] Text layer rendered for page \${pg}, length: \${txt.length}\`);
                    pageTextCache.set(pg, txt);
                    const current = window.PDFViewerApplication.pdfViewer.currentPageNumber;
                    // Always send when text layer is rendered - this ensures initial page and navigation work
                    if (current === pg) {
                      console.log(\`[PDF.js] Sending text for current page \${pg}\`);
                      parent.postMessage({ type:'page-text', page: pg, text: txt }, '*');
                    }
                  }
                }
              } catch(_) {}
            });
            
            // Listen to multiple page change events for maximum reliability
            // Throttle page change events to prevent excessive updates
            let lastPageChangeTime = 0;
            let pageChangeTimeout = null;
            const throttledHandlePageChange = (pageNum) => {
              const now = Date.now();
              // Throttle to max once per 200ms
              if (now - lastPageChangeTime < 200) {
                clearTimeout(pageChangeTimeout);
                pageChangeTimeout = setTimeout(() => {
                  if (pageNum !== lastReportedPage) {
                    handlePageChange(pageNum);
                  }
                }, 200);
                return;
              }
              lastPageChangeTime = now;
              if (pageNum !== lastReportedPage) {
                handlePageChange(pageNum);
              }
            };
            
            eb.on('pagechanging', (e)=> throttledHandlePageChange(e.pageNumber));
            eb.on('pagechange', (e)=> throttledHandlePageChange(e.pageNumber));
            eb.on('updateviewarea', (e)=> {
              if (e.location && e.location.pageNumber) {
                throttledHandlePageChange(e.location.pageNumber);
              }
            });
            
            // Minimal polling fallback - check page every 1000ms only if events fail
            // Events should handle 99% of cases, this is just safety net
            setInterval(() => {
              try {
                const viewer = window.PDFViewerApplication && window.PDFViewerApplication.pdfViewer;
                if (viewer && viewer.currentPageNumber) {
                  const pg = viewer.currentPageNumber;
                  if (pg !== lastReportedPage) {
                    console.log(\`[PDF.js] ⚡ Polling fallback detected page change to \${pg}\`);
                    handlePageChange(pg);
                  }
                }
              } catch(e) {}
            }, 1000); // Much slower - events should catch everything
          } catch(_) {}

          function boot(){
            enableSelectionAndSidebar();
            ensureAnnoLayers();
            redrawAll();
            try {
              const eb = window.PDFViewerApplication.eventBus;
              eb.on('pagerendered', ensureAnnoLayers);
              
              // Throttle scalechanging to avoid expensive operations during zoom
              let scaleChangeTimeout = null;
              let scaleChangeAnimationFrame = null;
              eb.on('scalechanging', ()=>{ 
                // Cancel any pending updates
                if (scaleChangeTimeout) {
                  clearTimeout(scaleChangeTimeout);
                  scaleChangeTimeout = null;
                }
                if (scaleChangeAnimationFrame) {
                  cancelAnimationFrame(scaleChangeAnimationFrame);
                  scaleChangeAnimationFrame = null;
                }
                
                // Only update after zoom settles (debounce)
                scaleChangeTimeout = setTimeout(() => {
                  scaleChangeAnimationFrame = requestAnimationFrame(() => {
                    ensureAnnoLayers(); 
                    redrawAll();
                    scaleChangeAnimationFrame = null;
                  });
                  scaleChangeTimeout = null;
                }, 300); // Wait 300ms after last zoom event
              });
              
              // Trigger initial page load after boot
              setTimeout(()=>{
                try {
                  const pg = window.PDFViewerApplication.pdfViewer.currentPageNumber || 1;
                  console.log(\`[PDF.js] Initial boot, triggering page change for page \${pg}\`);
                  handlePageChange(pg);
                } catch(e){ console.log('[PDF.js] Initial page error:', e); }
              }, 500);
            } catch(e){}
          }

          const app = window.PDFViewerApplication;
          if (app && app.initializedPromise) { app.initializedPromise.then(boot).catch(boot); }
          else {
            const t = setInterval(()=>{ if (window.PDFViewerApplication && PDFViewerApplication.initializedPromise){ clearInterval(t); PDFViewerApplication.initializedPromise.then(boot).catch(boot);} },150);
          }
        })();
      `;
      w.eval(js);
    } catch (e) {}
    
    // Load library data for current filename when iframe is ready
    loadLibraryDataForFilename();
  });
  
  // Also try loading immediately in case cached
  loadLibraryDataForFilename();
  
  // Wrapper-level polling: DISABLED - events handle all updates
  // Only keep minimal polling as absolute fallback (very slow, only for edge cases)
  setInterval(() => {
    try {
      // Skip if updating or if we updated recently
      if (isUpdatingPage) return;
      
      const iframe = frame.contentWindow;
      if (iframe && iframe.PDFViewerApplication && iframe.PDFViewerApplication.pdfViewer) {
        const pg = iframe.PDFViewerApplication.pdfViewer.currentPageNumber;
        
        // Only update if page actually changed (fallback only - events should catch this)
        if (pg && pg !== currentPage && pg !== lastUpdatedPage) {
          console.log(`[Wrapper Polling Fallback] ⚡ Detected page mismatch - correcting to ${pg}`);
          updatePageUI(pg);
          
          // Trigger text extraction if we don't have cached data
          if (!pageSummaryCache.has(pg)) {
            postToIframe({ type: 'request-page-text', page: pg });
          }
        }
      }
    } catch(e) {
      // Iframe might not be ready or cross-origin issues
    }
  }, 2000); // Very slow polling - only as absolute fallback

  // ---- Drag and Drop Functionality ----
  const dropZoneOverlay = document.getElementById('dropZoneOverlay');
  const dropZoneContent = document.getElementById('dropZoneContent');
  const dropZoneIcon = document.getElementById('dropZoneIcon');
  const dropZoneText = document.getElementById('dropZoneText');
  const dropZoneSubtext = document.getElementById('dropZoneSubtext');
  
  let dragCounter = 0; // Track drag enter/leave to handle nested elements
  
  function showDropZone() {
    dropZoneOverlay.classList.add('active');
    dropZoneIcon.textContent = '📄';
    dropZoneText.textContent = 'Drop PDF here';
    dropZoneSubtext.textContent = 'Release to open the PDF file';
  }
  
  function hideDropZone() {
    dropZoneOverlay.classList.remove('active');
    dragCounter = 0;
  }
  
  function handleDragOver(e) {
    // Don't interfere with other modals
    if (e.target.closest('#settingsModal') || 
        e.target.closest('#mindmapModal') ||
        e.target.closest('#duplicateModal') ||
        e.target.closest('#fcModal')) {
      return;
    }
    
    // If dragging inside library modal, check if it's library items being moved (not new files)
    if (e.target.closest('#libraryModal')) {
      // If dragging library items (text/plain data), let library handlers deal with it
      if (e.dataTransfer && e.dataTransfer.types && e.dataTransfer.types.includes('text/plain')) {
        return;
      }
      // If dragging new files, allow drop
      if (e.dataTransfer && e.dataTransfer.types && e.dataTransfer.types.includes('Files')) {
        e.preventDefault();
        e.stopPropagation();
        e.dataTransfer.dropEffect = 'copy';
        return;
      }
      return;
    }
    
    e.preventDefault();
    e.stopPropagation();
    
    // Check if dragging files
    if (e.dataTransfer && e.dataTransfer.types && e.dataTransfer.types.includes('Files')) {
      e.dataTransfer.dropEffect = 'copy';
      if (!dropZoneOverlay.classList.contains('active')) {
        showDropZone();
      }
    }
  }
  
  function handleDragEnter(e) {
    // Don't interfere with other modals
    if (e.target.closest('#settingsModal') || 
        e.target.closest('#mindmapModal') ||
        e.target.closest('#duplicateModal') ||
        e.target.closest('#fcModal')) {
      return;
    }
    
    // If dragging inside library modal, check if it's library items being moved (not new files)
    if (e.target.closest('#libraryModal')) {
      // If dragging library items (text/plain data), let library handlers deal with it
      if (e.dataTransfer && e.dataTransfer.types && e.dataTransfer.types.includes('text/plain')) {
        return;
      }
      // If dragging new files, allow drop
      if (e.dataTransfer && e.dataTransfer.types && e.dataTransfer.types.includes('Files')) {
        e.preventDefault();
        e.stopPropagation();
        dragCounter++;
        return;
      }
      return;
    }
    
    e.preventDefault();
    e.stopPropagation();
    dragCounter++;
    
    // Check if dragging files
    if (e.dataTransfer && e.dataTransfer.types && e.dataTransfer.types.includes('Files')) {
      showDropZone();
    }
  }
  
  function handleDragLeave(e) {
    e.preventDefault();
    e.stopPropagation();
    dragCounter--;
    
    // Only hide if we've left the window entirely (not just moved to a child element)
    if (dragCounter <= 0) {
      dragCounter = 0;
      hideDropZone();
    }
  }
  
  async function handleDrop(e) {
    // Don't interfere with other modals
    if (e.target.closest('#settingsModal') || 
        e.target.closest('#mindmapModal') ||
        e.target.closest('#duplicateModal') ||
        e.target.closest('#fcModal')) {
      return;
    }
    
    // Check if dropping inside library modal
    const isInLibrary = e.target.closest('#libraryModal');
    
    // If dragging library items (text/plain data), let library handlers deal with it
    if (e.dataTransfer && e.dataTransfer.types && e.dataTransfer.types.includes('text/plain')) {
      return;
    }
    
    e.preventDefault();
    e.stopPropagation();
    dragCounter = 0;
    hideDropZone();
    
    const files = e.dataTransfer.files;
    if (!files || files.length === 0) {
      return;
    }
    
    // Find first PDF file
    let pdfFile = null;
    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      if (file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')) {
        pdfFile = file;
        break;
      }
    }
    
    if (!pdfFile) {
      // Show error message
      dropZoneOverlay.classList.add('active');
      dropZoneIcon.textContent = '❌';
      dropZoneText.textContent = 'Invalid file type';
      dropZoneSubtext.textContent = 'Please drop a PDF file';
      setTimeout(() => {
        hideDropZone();
      }, 2000);
      return;
    }
    
    // Show loading state (only if not in library modal)
    if (!isInLibrary) {
      dropZoneOverlay.classList.add('active');
      dropZoneIcon.textContent = '⏳';
      dropZoneText.textContent = 'Loading PDF...';
      dropZoneSubtext.textContent = pdfFile.name;
    }
    
    try {
      // For desktop apps, we need to get the file path
      // Since we're in a desktop app (pywebview), we can try to get the path
      // If that's not available, we'll read the file and send it
      
      let filePath = null;
      
      // Try to get file path (works in some desktop environments)
      if (pdfFile.path) {
        filePath = pdfFile.path;
      } else if (pdfFile.webkitRelativePath) {
        // Try webkitRelativePath as fallback
        filePath = pdfFile.webkitRelativePath;
      }
      
      if (filePath && window.pywebview && window.pywebview.api) {
        // Call Python API with file path
        console.log(`[Drag & Drop] Loading PDF from path: ${filePath}`);
        const result = await window.pywebview.api.load_pdf_from_path(filePath);
        
        if (result && result.ok) {
          if (isInLibrary) {
            // Refresh library to show the new file
            await openLibrary();
          } else {
            dropZoneIcon.textContent = '✓';
            dropZoneText.textContent = 'PDF loaded successfully';
            dropZoneSubtext.textContent = '';
            setTimeout(() => {
              hideDropZone();
            }, 1000);
          }
        } else {
          throw new Error(result?.error || 'Failed to load PDF');
        }
      } else {
        // Fallback: read file and send to Python
        console.log(`[Drag & Drop] Reading file data (path not available)`);
        const arrayBuffer = await pdfFile.arrayBuffer();
        const uint8Array = new Uint8Array(arrayBuffer);
        // Convert to regular array for Python compatibility
        const fileDataArray = Array.from(uint8Array);
        
        if (window.pywebview && window.pywebview.api) {
          const result = await window.pywebview.api.load_pdf_from_data(fileDataArray, pdfFile.name);
          
          if (result && result.ok) {
            if (isInLibrary) {
              // Refresh library to show the new file
              await openLibrary();
            } else {
              dropZoneIcon.textContent = '✓';
              dropZoneText.textContent = 'PDF loaded successfully';
              dropZoneSubtext.textContent = '';
              setTimeout(() => {
                hideDropZone();
              }, 1000);
            }
          } else {
            throw new Error(result?.error || 'Failed to load PDF');
          }
        } else {
          throw new Error('API not available');
        }
      }
    } catch (error) {
      console.error('[Drag & Drop] Error:', error);
      dropZoneIcon.textContent = '❌';
      dropZoneText.textContent = 'Error loading PDF';
      dropZoneSubtext.textContent = error.message || 'Unknown error';
      setTimeout(() => {
        hideDropZone();
      }, 3000);
    }
  }
  
  // Add event listeners to the entire window
  window.addEventListener('dragover', handleDragOver);
  window.addEventListener('dragenter', handleDragEnter);
  window.addEventListener('dragleave', handleDragLeave);
  window.addEventListener('drop', handleDrop);
  
  // Also add to root and work elements for better coverage
  const root = document.getElementById('root');
  const work = document.getElementById('work');
  if (root) {
    root.addEventListener('dragover', handleDragOver);
    root.addEventListener('dragenter', handleDragEnter);
    root.addEventListener('dragleave', handleDragLeave);
    root.addEventListener('drop', handleDrop);
  }
  if (work) {
    work.addEventListener('dragover', handleDragOver);
    work.addEventListener('dragenter', handleDragEnter);
    work.addEventListener('dragleave', handleDragLeave);
    work.addEventListener('drop', handleDrop);
  }
})();
</script>
</body>
</html>
"""
    WRAPPER_HTML.write_text(html, encoding="utf-8")
    
    # Copy all icons to PDFJS_DIR/icons so they can be served
    icons_dir = PDFJS_DIR / "icons"
    icons_dir.mkdir(exist_ok=True)
    icons_src_dir = Path(__file__).parent / "icons"
    
    # List of icon files to copy
    icon_files = ["copy_icon.png", "akson.png", "library.svg", "open_folder.svg", "settings.svg", "flashcard.png"]
    
    for icon_file in icon_files:
        icon_src = icons_src_dir / icon_file
        icon_dest = icons_dir / icon_file
        if icon_src.exists():
            shutil.copy2(icon_src, icon_dest)


def choose_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class PDFStaticHandler(SimpleHTTPRequestHandler):
    # Serve from our cache root so /web/* and /build/* and /docs/* and wrapper are available
    def __init__(self, *args, directory=None, **kwargs):
        super().__init__(*args, directory=str(PDFJS_DIR), **kwargs)

    # Ensure correct MIME types (especially for .wasm)
    def guess_type(self, path):
        ctype = super().guess_type(path)
        if path.endswith(".wasm"):
            return "application/wasm"
        if path.endswith(".map"):
            return "application/json"
        return ctype

    def translate_path(self, path):
        """Translate URL path to filesystem path, handling /docs/ specially."""
        import urllib.parse
        # Strip query/fragment
        path = path.split('?', 1)[0]
        path = path.split('#', 1)[0]
        path = urllib.parse.unquote(path)
        if path.startswith('/docs/'):
            relative_path = path[6:]
            return str(DOCS_DIR / relative_path)
        # Handle mindmap images
        if path.startswith('/mindmap_images/'):
            relative_path = path[len('/mindmap_images/'):]
            return str(CACHE_ROOT / "library" / relative_path)
        # Serve bundled icons
        if path.startswith('/icons/'):
            relative_path = path[len('/icons/'):]
            return str(ICONS_DIR / relative_path)
        return super().translate_path(path)


class LocalServer:
    def __init__(self, port: int):
        self.port = port
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), PDFStaticHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        print(f"Server started at http://127.0.0.1:{self.port}")

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None


# ---- Selection enabler for direct viewer mode (fallback) ----
def enable_selection_js(window: webview.Window):
    js = r"""
    (function waitForPDFJS(){
      function go(){
        try {
          if (window.PDFViewerApplicationOptions && PDFViewerApplicationOptions.set) {
            PDFViewerApplicationOptions.set('textLayerMode', 2);
          }
          if (window.PDFViewerApplication && PDFViewerApplication.eventBus) {
            try { PDFViewerApplication.eventBus.dispatch('switchtool', { tool: 0 }); } catch(e) {}
            try { PDFViewerApplication.eventBus.dispatch('togglehandtool', { isActive: false }); } catch(e) {}
          }
          const style = document.createElement('style');
          style.textContent = '.textLayer, .textLayer *, #outerContainer:active .textLayer, #outerContainer:active .textLayer * { user-select: text !important; -webkit-user-select: text !important; -moz-user-select: text !important; -ms-user-select: text !important; } #viewerContainer, #viewerContainer:active { user-select: auto !important; -webkit-user-select: auto !important; } .annoLayer{position:absolute;inset:0;pointer-events:none;} /* Disable text selection during PDF.js sidebar resize to prevent blue flash */ #outerContainer[class*="resizing"], #outerContainer[class*="resizing"] *, #outerContainer[class*="moving"], #outerContainer[class*="moving"] *, #sidebarContainer[class*="resizing"], #sidebarContainer[class*="resizing"] *, body[class*="resizing"] #outerContainer, body[class*="resizing"] #outerContainer *, body.sidebar-resizing #outerContainer, body.sidebar-resizing #outerContainer *, #outerContainer.sidebar-resizing, #outerContainer.sidebar-resizing * { user-select: none !important; -webkit-user-select: none !important; -moz-user-select: none !important; -ms-user-select: none !important; } /* Disable text selection on sidebar drag handle - but allow text layer */ #sidebarResizer, #sidebarResizer * { user-select: none !important; -webkit-user-select: none !important; } /* Ensure sidebar buttons are clickable */ #toolbarSidebar button, #sidebarViewButtons button, #viewThumbnail, #viewOutline, #viewAttachments, #viewLayers { pointer-events: auto !important; cursor: pointer !important; }';
          document.head.appendChild(style);
          try {
            const app = window.PDFViewerApplication;
            const tryOpen = () => { if (app && app.pdfSidebar){ app.pdfSidebar.open(); app.pdfSidebar.switchView(1); } };
            tryOpen(); setTimeout(tryOpen,200); setTimeout(tryOpen,600);
            
            // Disable text selection during sidebar resize to prevent blue flash
            let isSidebarResizing = false;
            const outerContainer = document.getElementById('outerContainer');
            if (outerContainer) {
              // Listen for mousedown on sidebar resize handle - try multiple selectors
              const findResizeHandle = () => {
                return document.querySelector('#outerContainer .splitToolbarButton, #outerContainer .sidebarResizer, #outerContainer [class*="resizer"], #outerContainer [class*="Resizer"], #outerContainer [style*="cursor: ew-resize"], #outerContainer [style*="cursor: col-resize"]') ||
                       document.querySelector('#outerContainer > div:first-child') || // First child might be resize handle
                       outerContainer; // Fallback to entire container
              };
              
              // Try to find resize handle after a delay (PDF.js might not be ready)
              setTimeout(() => {
                const resizeHandle = findResizeHandle();
                if (resizeHandle) {
                  resizeHandle.addEventListener('mousedown', function(e) {
                    // Check if cursor indicates resize or if clicking near sidebar edge
                    const style = window.getComputedStyle(e.target);
                    if (style.cursor === 'ew-resize' || style.cursor === 'col-resize' || 
                        e.target.classList.toString().includes('resizer') ||
                        e.target.classList.toString().includes('Resizer')) {
                      isSidebarResizing = true;
                      outerContainer.classList.add('sidebar-resizing');
                      document.body.classList.add('sidebar-resizing');
                    }
                  }, true); // Use capture phase to catch early
                }
                
                // Also listen on entire container for mousedown with resize cursor
                outerContainer.addEventListener('mousedown', function(e) {
                  const style = window.getComputedStyle(e.target);
                  if (style.cursor === 'ew-resize' || style.cursor === 'col-resize') {
                    isSidebarResizing = true;
                    outerContainer.classList.add('sidebar-resizing');
                    document.body.classList.add('sidebar-resizing');
                  }
                }, true);
              }, 500);
              
              // Listen for mouseup to re-enable text selection
              document.addEventListener('mouseup', function() {
                if (isSidebarResizing) {
                  setTimeout(() => {
                    isSidebarResizing = false;
                    outerContainer.classList.remove('sidebar-resizing');
                    document.body.classList.remove('sidebar-resizing');
                  }, 50); // Small delay to prevent flicker
                }
              });
            }
          } catch(e){}
        } catch (e) { /* ignore */ }
      }
      (function poll(){
        if (window.PDFViewerApplication && PDFViewerApplication.initializedPromise) {
          PDFViewerApplication.initializedPromise.then(go).catch(go);
        } else {
          setTimeout(poll, 150);
        }
      })();
    })();
    """
    def inject():
        try:
            window.evaluate_js(js)
        except Exception:
            pass
    inject()
    threading.Timer(0.5, inject).start()
    threading.Timer(1.5, inject).start()


# ---- PDF flattening (burn annotations) ----
def flatten_pdf_with_annotations(src: Path, items: list[dict]) -> Path:
    """
    items: list like
      {'type':'stroke','page':1,'points':[[x_frac,y_frac],...],'width_frac':0.003,'color':'#rrggbb'}
      {'type':'text','page':1,'x':0.2,'y':0.3,'text':'hello','font_size_frac':0.02,'color':'#000000'}
    Returns path to new annotated PDF.
    """
    import fitz  # PyMuPDF
    from reportlab.pdfgen import canvas as rl_canvas  # pyright: ignore[reportMissingModuleSource]
    from reportlab.lib.colors import black  # pyright: ignore[reportMissingModuleSource]

    doc = fitz.open(str(src))

    # group annotations by page number (1-based)
    by_page: dict[int, list[dict]] = {}
    for it in items or []:
        try:
            p = int(it.get("page", 0))
            if p <= 0:
                continue
            by_page.setdefault(p, []).append(it)
        except Exception:
            continue

    tmpdir = Path(tempfile.mkdtemp(prefix="pdfann_"))
    new_doc = fitz.open()  # Create new document

    for idx in range(len(doc)):
        page = doc[idx]
        if (idx + 1) not in by_page:
            new_doc.insert_pdf(doc, from_page=idx, to_page=idx)
            continue

        # create single-page overlay PDF with reportlab
        w = float(page.rect.width)
        h = float(page.rect.height)
        overlay_path = tmpdir / f"overlay_{idx}.pdf"
        c = rl_canvas.Canvas(str(overlay_path), pagesize=(w, h))

        for it in by_page[idx + 1]:
            if it.get("type") == "stroke":
                pts = it.get("points") or []
                if len(pts) < 2:
                    continue
                lw = max(1.5, h * float(it.get("width_frac", 0.003)))
                c.setLineWidth(lw)
                col = it.get("color", "#000000")
                try:
                    r = int(col[1:3], 16) / 255.0
                    g = int(col[3:5], 16) / 255.0
                    b = int(col[5:7], 16) / 255.0
                    c.setStrokeColorRGB(r, g, b)
                except Exception:
                    c.setStrokeColor(black)
                # PDF coordinate origin is bottom-left; y_frac top-based → invert
                x0 = float(pts[0][0]) * w
                y0 = (1.0 - float(pts[0][1])) * h
                c.moveTo(x0, y0)
                for p in pts[1:]:
                  x = float(p[0]) * w
                  y = (1.0 - float(p[1])) * h
                  c.lineTo(x, y)
                c.stroke()

            elif it.get("type") == "text":
                x = float(it.get("x", 0.1)) * w
                y = (1.0 - float(it.get("y", 0.1))) * h
                fs = max(8.0, float(it.get("font_size_frac", 0.02)) * h)
                c.setFont("Helvetica", fs)
                col = it.get("color", "#000000")
                try:
                    r = int(col[1:3], 16) / 255.0
                    g = int(col[3:5], 16) / 255.0
                    b = int(col[5:7], 16) / 255.0
                    c.setFillColorRGB(r, g, b)
                except Exception:
                    c.setFillColor(black)
                c.drawString(x, y, str(it.get("text", ""))[:1000])
            
            elif it.get("type") == "highlight":
                # Add highlight annotation using PyMuPDF
                position = it.get("position", {})
                if position:
                    # Convert fractional coordinates to PDF coordinates
                    x1 = position.get("x", 0) * w if isinstance(position.get("x"), float) else float(position.get("x", 0))
                    y1 = (1.0 - position.get("y", 0)) * h if isinstance(position.get("y"), float) else h - float(position.get("y", 0))
                    x2 = x1 + (position.get("width", 100) if isinstance(position.get("width"), (int, float)) else 100)
                    y2 = y1 + (position.get("height", 20) if isinstance(position.get("height"), (int, float)) else 20)
                    
                    # Create highlight rectangle
                    rect = fitz.Rect(x1, y1, x2, y2)
                    
                    # Get color from highlight color name
                    color_name = it.get("color", "yellow")
                    color_map = {
                        "yellow": (1.0, 0.92, 0.23),
                        "green": (0.3, 0.69, 0.31),
                        "blue": (0.13, 0.59, 0.95),
                        "pink": (1.0, 0.25, 0.51),
                        "orange": (1.0, 0.6, 0),
                        "purple": (0.61, 0.15, 0.69),
                        "red": (0.96, 0.26, 0.21),
                        "cyan": (0, 0.74, 0.83)
                    }
                    rgb_color = color_map.get(color_name, (1.0, 0.92, 0.23))
                    
                    # Add highlight annotation using PyMuPDF's add_highlight_annot
                    # This requires quads (quadrilaterals) for the highlight area
                    quads = [rect.quad]  # Simple rectangular highlight
                    page.add_highlight_annot(quads)
                    
                    # Set the highlight color
                    annots = page.annots()
                    if annots:
                        for annot in annots:
                            if annot.type[0] == 8:  # Highlight annotation type
                                annot.set_colors(stroke=rgb_color)
                                annot.update()

        c.showPage()
        c.save()

        # merge overlay into page
        overlay_doc = fitz.open(str(overlay_path))
        page.show_pdf_page(page.rect, overlay_doc, 0)
        overlay_doc.close()
        new_doc.insert_pdf(doc, from_page=idx, to_page=idx)

    doc.close()
    out_path = src.with_name(src.stem + "-annotated.pdf")
    new_doc.save(str(out_path))
    new_doc.close()
    return out_path


# ---- App logic & bridge ----
class AppAPI:
    """JS↔Python bridge."""
    def __init__(self, window: webview.Window, port: int):
        self.window = window
        self.port = port
        self._current_dest_path: Path | None = None  # Internal Path object
        self._akson_process = None  # Track Akson widget process
        
        # Initialize Akson Cards store (private to avoid pywebview serialization issues)
        akson_data_dir = CACHE_ROOT / "akson_cards"
        self._akson_store = AksonCardsStore(akson_data_dir)
        self._study_sessions: dict[str, StudySession] = {}  # deck_id -> session

        # Cleanup duplicate/orphan PDFs on startup (non-fatal)
        try:
            removed = self._cleanup_orphan_pdfs()
            if removed:
                print(f"🧹 Cleaned {removed} orphan/duplicate PDFs from docs folder.")
        except Exception as e:
            print(f"Warning: cleanup skipped: {e}")
    
    @property
    def current_dest(self) -> str | None:
        """Return string path to avoid pywebview serialization issues."""
        return str(self._current_dest_path) if self._current_dest_path else None

    # Internal helpers
    def _copy_pdf_to_docs(self, path: Path) -> Path | None:
        """Copy a PDF into DOCS_DIR with collision handling; return destination."""
        if not path.exists() or path.suffix.lower() != ".pdf":
            return None
        DOCS_DIR.mkdir(parents=True, exist_ok=True)
        dest = DOCS_DIR / path.name
        if dest.exists():
            base = dest.stem
            ext = dest.suffix
            i = 1
            while True:
                candidate = DOCS_DIR / f"{base}-{i}{ext}"
                if not candidate.exists():
                    dest = candidate
                    break
                i += 1
        shutil.copy2(path, dest)
        return dest

    def _sanitize_library_name(self, name: str) -> str:
        """Normalize library file identifiers and ensure a non-empty, safe value."""
        raw = str(name) if name is not None else ""
        safe = "".join(c for c in raw if c.isalnum() or c in (" ", "-", "_")).strip()
        if safe:
            return safe
        trimmed = raw.strip()
        if trimmed:
            import hashlib
            digest = hashlib.sha1(trimmed.encode("utf-8", errors="ignore")).hexdigest()[:8]
            return f"library_{digest}"
        return "library_unknown"

    def _ensure_library_stub(self, dest: Path) -> str:
        """Ensure a minimal library JSON exists for the given PDF."""
        from datetime import datetime
        library_dir = CACHE_ROOT / "library"
        library_dir.mkdir(parents=True, exist_ok=True)
        safe_filename = self._sanitize_library_name(dest.stem)
        library_file = library_dir / f"{safe_filename}.json"
        data = {
            "summaries": {},
            "flashcards": {},
            "lastModified": datetime.now().isoformat(),
            "pdf_path": str(dest),
            "doc_name": dest.name
        }
        if library_file.exists():
            try:
                with open(library_file, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                # Preserve existing content but ensure pdf_path/lastModified exist
                data.update(existing)
                data["pdf_path"] = str(dest)
                data["doc_name"] = existing.get("doc_name", dest.name)
                data.setdefault("lastModified", datetime.now().isoformat())
            except Exception:
                pass
        with open(library_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return safe_filename

    def _add_files_to_folder(self, folder_name: str, filenames: list[str]):
        """Ensure folder exists and contains the given filenames (unique)."""
        library_dir = CACHE_ROOT / "library"
        metadata_file = library_dir / "metadata.json"
        metadata = {"favorites": [], "folders": {}}
        if metadata_file.exists():
            try:
                with open(metadata_file, "r", encoding="utf-8") as f:
                    metadata = json.load(f)
            except Exception:
                pass
        folders = metadata.get("folders", {})

        # Remove filenames from any other folders to keep single association
        for files in folders.values():
            for name in filenames:
                if name in files:
                    files.remove(name)

        target = folders.get(folder_name, [])
        for name in filenames:
            if name not in target:
                target.append(name)
        folders[folder_name] = target
        metadata["folders"] = folders

        library_dir.mkdir(parents=True, exist_ok=True)
        with open(metadata_file, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

    # UI actions
    def open_file(self):
        """Open a PDF file using native file picker. No return value to avoid serialization issues."""
        try:
            # Native macOS file dialog via AppleScript for best UX
            if sys.platform == 'darwin':
                import subprocess
                applescript = (
                    'set pdfTypes to {"public.pdf", "com.adobe.pdf"}\n'  # UTIs for PDF
                    'set chosenFile to choose file with prompt "Select a PDF file" of type pdfTypes\n'
                    'return POSIX path of chosenFile'
                )
                result = subprocess.run(
                    ['osascript', '-e', applescript],
                    capture_output=True,
                    text=True
                )
                if result.returncode != 0:
                    print("User cancelled file selection")
                    return
                file_path = (result.stdout or '').strip()
                if not file_path:
                    print("No file selected")
                    return
                print(f"Selected file: {file_path}")
                self.load_pdf(Path(file_path))
                return

            # Fallback for Windows/Linux: pywebview dialog
            result = self.window.create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=False, file_types=['*.pdf']
            )
            if not result:
                print("No file selected")
                return
            print(f"Selected file: {result[0]}")
            self.load_pdf(Path(result[0]))
        except Exception as e:
            print(f"Error opening file: {e}")
            import traceback
            traceback.print_exc()

    def open_folder(self):
        """Open a folder, import all PDFs, create a matching library folder, and load the first PDF."""
        try:
            folder_path: Path | None = None
            if sys.platform == 'darwin':
                import subprocess
                applescript = (
                    'set chosenFolder to choose folder with prompt "Select a folder containing PDFs"\n'
                    'return POSIX path of chosenFolder'
                )
                result = subprocess.run(
                    ['osascript', '-e', applescript],
                    capture_output=True,
                    text=True
                )
                if result.returncode != 0:
                    print("User cancelled folder selection")
                    return None
                selected = (result.stdout or '').strip()
                if not selected:
                    print("No folder selected")
                    return None
                folder_path = Path(selected)
            else:
                result = self.window.create_file_dialog(webview.FOLDER_DIALOG)
                if not result:
                    print("No folder selected")
                    return None
                folder_path = Path(result[0])

            if not folder_path or not folder_path.exists():
                return {"ok": False, "error": "Folder not found"}

            pdfs = sorted([p for p in folder_path.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"])
            if not pdfs:
                print(f"No PDFs found in folder: {folder_path}")
                return {"ok": False, "error": "No PDFs found in the selected folder"}

            imported_files = []
            imported_paths = []
            for pdf in pdfs:
                dest = self._copy_pdf_to_docs(pdf)
                if not dest:
                    continue
                imported_paths.append(str(dest))
                safe_name = self._ensure_library_stub(dest)
                imported_files.append(safe_name)

            if not imported_files:
                return {"ok": False, "error": "No PDFs could be imported"}

            folder_label = folder_path.name.strip() or "Imported Folder"
            self._add_files_to_folder(folder_label, imported_files)

            # Load the first imported PDF into the viewer with folder context
            import base64
            payload = json.dumps({
                "folder": folder_label,
                "files": [Path(p).name for p in imported_paths]
            })
            payload_b64 = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("utf-8")

            first_dest = Path(imported_paths[0])
            self.load_pdf(first_dest, extra_params={"folderImport": payload_b64})
            # Frontend toast (self-contained to avoid missing showMessage)
            try:
                msg = f"Imported {len(imported_files)} file(s) into {folder_label}"
                msg_js = json.dumps(msg)
                toast_js = f"""
(function() {{
  const msg = {msg_js};
  let c = document.querySelector('.toast-container');
  if (!c) {{
    c = document.createElement('div');
    c.className = 'toast-container';
    c.style.position = 'fixed';
    c.style.top = '18px';
    c.style.right = '18px';
    c.style.zIndex = '2147483647';
    c.style.display = 'flex';
    c.style.flexDirection = 'column';
    c.style.gap = '10px';
    c.style.pointerEvents = 'none';
    document.body.appendChild(c);
  }}
  const t = document.createElement('div');
  t.className = 'toast success';
  t.style.background = 'linear-gradient(135deg, rgba(46,204,113,0.92), rgba(38,166,91,0.92))';
  t.style.border = '1px solid rgba(255,255,255,0.07)';
  t.style.borderRadius = '12px';
  t.style.padding = '12px 16px';
  t.style.minWidth = '240px';
  t.style.maxWidth = '420px';
  t.style.boxShadow = '0 10px 28px rgba(0,0,0,0.4)';
  t.style.display = 'flex';
  t.style.alignItems = 'center';
  t.style.gap = '10px';
  t.style.pointerEvents = 'auto';
  t.style.animation = 'toastSlideIn 0.25s ease';
  t.innerHTML = '<div class="toast-icon-logo" style="width:38px;height:38px;border-radius:12px;background:rgba(255,255,255,0.1);overflow:hidden;display:flex;align-items:center;justify-content:center;"><img src="icons/akson.png" style="width:100%;height:100%;object-fit:cover;" /></div>' + '<span class="toast-content" style="flex:1;color:#fff;font-size:13px;">' + msg + '</span>' + '<button class="toast-close" style="background:none;border:none;color:#fff;font-size:14px;cursor:pointer;" onclick="this.parentElement.remove()">×</button>';
  c.appendChild(t);
  setTimeout(function() {{ t.style.animation = 'toastSlideOut 0.3s ease'; setTimeout(function() {{ t.remove(); }}, 280); }}, 5200);
}})();
"""
                self.window.evaluate_js(toast_js)
            except Exception:
                pass

            return {
                "ok": True,
                "selected": str(folder_path),
                "imported": len(imported_files),
                "files": imported_paths,
                "folder": folder_label,
                "names": imported_files
            }
        except Exception as e:
            print(f"Error opening folder: {e}")
            import traceback
            traceback.print_exc()
            return {"ok": False, "error": str(e)}

    def open_docs_folder(self):
        """Open the managed docs folder in the system file explorer."""
        try:
            import subprocess
            import os
            DOCS_DIR.mkdir(parents=True, exist_ok=True)
            if sys.platform == "darwin":
                subprocess.run(["open", str(DOCS_DIR)], check=False)
            elif sys.platform.startswith("win"):
                os.startfile(str(DOCS_DIR))
            else:
                subprocess.run(["xdg-open", str(DOCS_DIR)], check=False)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _cleanup_orphan_pdfs(self) -> int:
        """Move duplicate/unreferenced PDFs out of DOCS_DIR into a trash folder."""
        DOCS_DIR.mkdir(parents=True, exist_ok=True)
        library_dir = CACHE_ROOT / "library"
        refs: set[str] = set()

        # Collect referenced filenames from library JSONs
        if library_dir.exists():
            for jf in library_dir.glob("*.json"):
                try:
                    with open(jf, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    doc_name = Path(data.get("doc_name", "")).name if data.get("doc_name") else None
                    pdf_path = Path(data.get("pdf_path", "")).name if data.get("pdf_path") else None
                    if doc_name:
                        refs.add(doc_name)
                    if pdf_path:
                        refs.add(pdf_path)
                    # also safe stem
                    refs.add(f"{jf.stem}.pdf")
                except Exception:
                    continue
            # metadata folders
            metadata_file = library_dir / "metadata.json"
            if metadata_file.exists():
                try:
                    with open(metadata_file, "r", encoding="utf-8") as mf:
                        meta = json.load(mf)
                    folders = meta.get("folders", {})
                    for safe_name_list in folders.values():
                        for safe in safe_name_list:
                            refs.add(f"{safe}.pdf")
                except Exception:
                    pass

        removed = 0

        for pdf in DOCS_DIR.glob("*.pdf"):
            if pdf.name in refs:
                continue
            base_match = re.match(r"(.+)-\d+\.pdf$", pdf.name)
            if base_match and f"{base_match.group(1)}.pdf" in refs:
                try:
                    pdf.unlink()
                    removed += 1
                except Exception:
                    pass
                continue
            # If not referenced at all, move to trash
            try:
                pdf.unlink()
                removed += 1
            except Exception:
                pass
        return removed

    # ---- Update checks (macOS only) ----
    def _compare_versions(self, current: str, latest: str) -> int:
        def parse(v: str):
            return [int(x) for x in v.split(".") if x.isdigit()]
        c = parse(current)
        l = parse(latest)
        for a, b in zip(c, l):
            if a < b:
                return -1
            if a > b:
                return 1
        if len(c) == len(l):
            return 0
        return -1 if len(c) < len(l) else 1

    def check_for_update(self):
        try:
            if sys.platform != "darwin":
                return {"ok": False, "error": "Updates are macOS-only right now."}
            import requests
            resp = requests.get(MANIFEST_URL, timeout=10)
            resp.raise_for_status()
            manifest = resp.json()
            latest = str(manifest.get("version", "")).strip()
            notes = manifest.get("notes", "")
            url = manifest.get("url", "")
            sha256 = manifest.get("sha256")
            if not latest or not url:
                return {"ok": False, "error": "Manifest missing version or url"}
            cmp = self._compare_versions(APP_VERSION, latest)
            needs = cmp < 0
            return {
                "ok": True,
                "current": APP_VERSION,
                "latest": latest,
                "needsUpdate": needs,
                "notes": notes,
                "downloadUrl": url,
                "sha256": sha256,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def download_update(self, url: str, expected_sha256: str | None = None):
        try:
            import requests, tempfile, hashlib
            if sys.platform != "darwin":
                return {"ok": False, "error": "Updates are macOS-only right now."}
            if not url:
                return {"ok": False, "error": "No download URL provided"}
            with requests.get(url, stream=True, timeout=20) as r:
                r.raise_for_status()
                fd, tmp_path = tempfile.mkstemp(suffix=os.path.splitext(url)[1] or ".zip")
                hash_obj = hashlib.sha256() if expected_sha256 else None
                with os.fdopen(fd, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            if hash_obj:
                                hash_obj.update(chunk)
                if hash_obj:
                    digest = hash_obj.hexdigest()
                    if digest.lower() != expected_sha256.lower():
                        Path(tmp_path).unlink(missing_ok=True)
                        return {"ok": False, "error": "Checksum mismatch"}
            return {"ok": True, "path": tmp_path}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def reveal_in_finder(self, path: str):
        try:
            if sys.platform != "darwin":
                return {"ok": False, "error": "Reveal only supported on macOS"}
            if not path:
                return {"ok": False, "error": "No path"}
            import subprocess
            subprocess.run(["open", "-R", path], check=False)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def load_pdf(self, path: Path, from_library: bool = False, extra_params: dict | None = None):
        if not path.exists() or path.suffix.lower() != ".pdf":
            return
        DOCS_DIR.mkdir(parents=True, exist_ok=True)
        in_docs_dir = path.resolve().parent == DOCS_DIR.resolve()
        if in_docs_dir:
            dest = path
        else:
            dest = DOCS_DIR / path.name
            if dest.exists():
                base = dest.stem
                ext = dest.suffix
                i = 1
                while True:
                    candidate = DOCS_DIR / f"{base}-{i}{ext}"
                    if not candidate.exists():
                        dest = candidate
                        break
                    i += 1
            shutil.copy2(path, dest)
        self._current_dest_path = dest
        file_param = urllib.parse.quote(f"/docs/{dest.name}")
        url = f"http://127.0.0.1:{self.port}/app_wrapper.html?file={file_param}"
        if from_library:
            url += "&fromLibrary=true"
        if extra_params:
            for key, value in extra_params.items():
                url += f"&{urllib.parse.quote(str(key))}={urllib.parse.quote(str(value))}"
        self.window.load_url(url)
        # Use original filename (without number suffix) for title
        original_name = path.name
        self.window.set_title(f"{APP_TITLE} — {original_name}")
        enable_selection_js(self.window)

    def load_pdf_from_path(self, file_path: str):
        """Load PDF from a file path string (for drag and drop)."""
        try:
            path = Path(file_path)
            if not path.exists():
                return {"ok": False, "error": f"File not found: {file_path}"}
            if path.suffix.lower() != ".pdf":
                return {"ok": False, "error": "File is not a PDF"}
            
            # Use the existing load_pdf method
            self.load_pdf(path)
            return {"ok": True}
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"ok": False, "error": str(e)}

    def load_pdf_from_data(self, file_data: list | bytes, filename: str):
        """Load PDF from file data (bytes array) and filename (for drag and drop)."""
        try:
            import tempfile
            import os
            
            # Convert list to bytes if needed (pywebview may send as list)
            if isinstance(file_data, list):
                file_bytes = bytes(file_data)
            else:
                file_bytes = file_data
            
            # Validate it's a PDF by checking magic bytes
            if not file_bytes.startswith(b'%PDF'):
                return {"ok": False, "error": "File is not a valid PDF"}
            
            # Save to temporary file first
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
                tmp_file.write(file_bytes)
                tmp_path = Path(tmp_file.name)
            
            try:
                # Use the existing load_pdf method
                # Create a Path object with the original filename for proper handling
                # We'll copy it to DOCS_DIR with the original filename
                path = Path(tmp_path)
                # Temporarily rename to preserve original filename
                original_path = path.parent / filename
                shutil.move(str(path), str(original_path))
                path = original_path
                
                self.load_pdf(path)
                
                # Clean up temp file after loading
                try:
                    if path.exists() and str(path).startswith(str(tempfile.gettempdir())):
                        path.unlink()
                except:
                    pass
                
                return {"ok": True}
            except Exception as e:
                # Clean up temp file on error
                try:
                    if path.exists() and str(path).startswith(str(tempfile.gettempdir())):
                        path.unlink()
                except:
                    pass
                raise e
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"ok": False, "error": str(e)}

    # called from JS: flatten annotations and return new relative path under /docs
    def flatten_annotations(self, rel_filename: str, payload: dict | list | str = None):
        try:
            if isinstance(payload, str):
                payload = json.loads(payload)
            if isinstance(payload, list):
                items = payload
            else:
                items = (payload or {}).get("items", [])
            pdf_path = DOCS_DIR / rel_filename
            if not pdf_path.exists():
                pdf_path = self._current_dest_path or None
            if not pdf_path or not pdf_path.exists():
                return {"ok": False, "error": "No file"}
            out_path = flatten_pdf_with_annotations(pdf_path, items)
            relpath = f"/docs/{out_path.name}"
            return {"ok": True, "path": str(out_path), "relpath": relpath}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---- OpenAI helpers ----
    def _require_key(self):
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY not set")

    # called from JS: summarize a text selection with OpenAI and return result
    def summarize_selection(self, text: str):
        try:
            if not text or not text.strip():
                return {"ok": False, "error": "Empty selection"}
            self._require_key()
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)

            system_msg = (
                """You summarise OCR text from lecture slides into SHORT, high-yield bullets for exam revision. 
                GOAL
                Return a compact list of independent facts in arrow/abbreviation style (memory notes), not prose.

                If you need to present structured data, comparisons, or lists with multiple attributes, use a markdown table format. Tables should use the pipe (|) separator format:
                | Header 1 | Header 2 | Header 3 |
                |----------|----------|----------|
                | Data 1   | Data 2   | Data 3   |
                
                Use tables when comparing items, showing relationships, or organizing structured information.

                HARD RULES (must obey)
                - start with a heading so we know what you will speak about.
                - Make it short but easy to understand. use words and wording that makes it easy to comprehend
                - use emojis to boost engagement and help with navigation. around 2-3 per slide.

                - 4–6 bullets total. One fact per bullet. No paragraphs.
                - Do NOT start bullets with category labels (e.g., "Epidemiology:", "Prevalence:", "Demographics:", "Skeletal destruction:").
                • Allowed labels only when logically required: **Criteria** and **Comparison**.
                - Never chain multiple facts with ";" or long commas. Split into separate bullets.
                - Use symbols & shorthand aggressively:
                ↑ increased/elevated, ↓ decreased/low, → leads to/causes, ~ approx, ≈ ratio, κ/λ light chains.
                - Prefer standard abbreviations when obvious from context.
                - Ratios compact: "M:F ≈ 3:1", "Blacks:Whites ≈ 2:1".
                - Minimal bold: only the key term in a bullet (e.g., **NSTEMI**, **Troponin I**, **Russell bodies**).
                - Zero filler or basic definitions.

                REASONING (internal; do not output)
                1) Identify intent (definition/essence, criteria, comparison, atypical features, pathology/morphology, epidemiology).
                2) Select the 4–6 highest-yield, exam-testable facts; prefer differentiators and criteria.
                3) Rewrite each as: **Label** → compressed fact(s) with symbols/abbrevs.
                4) Self-check before output:
                - Bullet count 4–6 or as little as possible.
                - No bullet starts with a category label (except **Criteria**/**Comparison** when necessary).
                - No ";" inside bullets.
                - Each bullet ≤ ~18–22 words.

                FORMATTING REQUIREMENTS (MUST USE WHEN SUITABLE)
                
                You MUST actively use these formatting options when appropriate. Do not skip them!
                
                1. **Bold (**text**):** REQUIRED for all key medical terms, conditions, drugs, or important concepts (e.g., **NSTEMI**, **Troponin I**, **Russell bodies**, **ACE inhibitors**).
                
                2. *Italics (*text*):* REQUIRED when:
                   - Defining a term (e.g., *also known as*, *synonym for*)
                   - Providing alternative names (e.g., *AKA: congestive heart failure*)
                   - Adding subtle emphasis or clarifications
                   - Example: "**MI** *or myocardial infarction* is..."
                
                3. `Inline Code (backticks):` REQUIRED for:
                   - Lab values (e.g., `CRP > 3mg/L`, `HbA1c < 7%`)
                   - Dosages (e.g., `500mg BID`, `2.5mg daily`)
                   - Measurements (e.g., `BP > 140/90`, `HR < 60 bpm`)
                   - Any specific numerical values that need emphasis
                
                4. ## Headings: REQUIRED when summarizing multiple distinct concepts or topics. Use ## for main sections (e.g., ## Pathophysiology, ## Clinical Features, ## Treatment). You MUST use headings if covering 2+ different aspects of a topic.
                
                5. ### Subheadings: Use ### for subsections within a main topic when needed.
                
                6. Numbered Lists (1., 2., 3.): REQUIRED for:
                   - Sequential processes (diagnostic steps, treatment protocols)
                   - Ordered criteria or stages
                   - Step-by-step procedures
                   - Example: "1. Initial assessment 2. Lab tests 3. Imaging"
                
                7. Horizontal Rules (---): REQUIRED to visually separate distinct topics when summarizing multiple unrelated concepts in one response. Use `---` between different topics or sections.
                
                8. Block Quotes (> text): REQUIRED for:
                   - Clinical pearls or important takeaways
                   - Exam tips or common pitfalls
                   - Key memorization points
                   - Example: "> Remember: This is a high-yield exam fact"
                
                9. Tables: Use markdown tables for comparisons, structured data, or multi-attribute lists. IMPORTANT: Tables can AND SHOULD be combined with explanatory text before or after. Do not use tables in isolation - add context bullets around them.
                   - Always use **bold** for table headers
                   - Use **bold** or `backticks` for critical values in cells
                   - Example format:
                     "Key differences:
                     | Feature | Type A | Type B |
                     |---------|--------|--------|
                     | Onset | Acute | Chronic |
                     Additional notes about the comparison..."
                
                10. Color spans (use when highlighting critical info):
                    - <span style="color: #ff6b6b;">⚠️ Critical warnings</span>
                    - <span style="color: #4ecdc4;">🔑 High-yield facts</span>
                    - <span style="color: #ffe66d;">💡 Clinical significance</span>
                
                FORMATTING CHECKLIST (before output):
                ✓ Did I use **bold** for key terms?
                ✓ Did I use *italics* for definitions/alternatives?
                ✓ Did I use `backticks` for lab values/measurements?
                ✓ If multiple topics: Did I use ## headings and/or --- separators?
                ✓ If sequential steps: Did I use numbered lists?
                ✓ If important note: Did I use > block quote?
                ✓ If comparison data: Did I use a table WITH explanatory text?
                
                - **Criteria:** Use a single bullet if it's the classic triad/"≥2 of 3". Otherwise split into bullets.
                - **Comparison:** Up to 3 bullets, one per entity, each starting with a bold entity then its defining line.

                Return ONLY the formatted bullets."""
            )
            user_msg = f"Summarise this selection for quick revision:\n\n{text.strip()}"

            resp = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "system", "content": system_msg},
                          {"role": "user", "content": user_msg}],
                temperature=0.7,
            )
            out = resp.choices[0].message.content.strip()
            return {"ok": True, "summary": out}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # called from JS: define a single term (word/short phrase)
    def define_term(self, text: str):
        try:
            if not text or not text.strip():
                return {"ok": False, "error": "Empty term"}
            
            # Clean and normalize text - handle edge cases
            cleaned = text.strip()
            # Remove leading/trailing punctuation that might interfere
            cleaned = cleaned.strip('.,!?;:()[]{}"\'').strip()
            # Handle common issues: extra whitespace, special chars
            import re
            cleaned = re.sub(r'\s+', ' ', cleaned)  # Normalize whitespace
            # Remove any non-printable chars
            cleaned = ''.join(c for c in cleaned if c.isprintable() or c.isspace()).strip()
            
            if not cleaned or len(cleaned) == 0:
                return {"ok": False, "error": "Invalid term"}
            
            # Limit length to prevent abuse
            if len(cleaned) > 150:
                cleaned = cleaned[:150]
                
            self._require_key()
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)

            system_msg = (
                """You are a helpful tutor. Explain or define the given text clearly and concisely for a student.
                
                If you need to present structured data, comparisons, or lists with multiple attributes, use a markdown table format. Tables should use the pipe (|) separator format:
                | Header 1 | Header 2 | Header 3 |
                |----------|----------|----------|
                | Data 1   | Data 2   | Data 3   |
                
                Use tables when comparing items, showing relationships, or organizing structured information.

RULES:
• Maximum 25-30 words. Keep it concise but complete and useful.
• If it's a term/phrase: define it clearly with key details a student needs.
• If it's a question: answer directly and precisely.
• If it expects a list (causes/treatments/methods/etc): give 3-6 specific items, comma-separated.
• Use bullet points where appropriate.
• No preamble, headings, or context mentions.
• Be flexible - handle single words, phrases, abbreviations, acronyms, or short questions.
• If the term is unclear or ambiguous, provide the most common/important definition."""
            )
            user_msg = f"Explain or define this for a student:\n\n{cleaned}"

            resp = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "system", "content": system_msg},
                          {"role": "user", "content": user_msg}],
                temperature=0.3,
                max_tokens=120,  # Limit response length
            )
            out = resp.choices[0].message.content.strip()
            if not out:
                return {"ok": False, "error": "Empty response"}
            return {"ok": True, "definition": out}
        except Exception as e:
            print(f"❌ [define_term] Error: {e}")
            import traceback
            traceback.print_exc()
            return {"ok": False, "error": str(e)}

    # called from JS: summarize the CURRENT PAGE text and return a short summary
    def summarize_page(self, text: str, page: int | None = None, extra_instruction: str = ""):
        try:
            print(f"\n🔵 [summarize_page] Called for page {page}")
            print(f"🔵 [summarize_page] Text length: {len(text) if text else 0}")
            if extra_instruction:
                print(f"🔵 [summarize_page] Extra instruction: {extra_instruction}")
            
            if not text or not text.strip():
                print(f"🔴 [summarize_page] Empty page text!")
                return {"ok": False, "error": "Empty page"}
            
            print(f"🔵 [summarize_page] Text preview: {text[:100]}...")
            
            self._require_key()
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            
            print(f"🔵 [summarize_page] OpenAI key set, calling API...")

            system_msg = (
                """Summarize the given page of a PDF into 4–6 high-yield bullets for exam revision.
                
                If you need to present structured data, comparisons, or lists with multiple attributes, use a markdown table format. Tables should use the pipe (|) separator format:
                | Header 1 | Header 2 | Header 3 |
                |----------|----------|----------|
                | Data 1   | Data 2   | Data 3   |
                
                Use tables when comparing items, showing relationships, or organizing structured information.
                
                ⚠️ CRITICAL: ONLY summarize content from the provided page text. DO NOT add external knowledge or make assumptions.
                
                IF the page contains no relevant content (e.g., title page, agenda, references, images only):
                → Output exactly: "No summary needed for this page."
                
                GOAL
                Return a compact list of independent facts in arrow/abbreviation style (memory notes), not prose.

                HARD RULES (must obey)
                - start with a heading so we know what you will speak about.
                - Make it short but easy to understand. use words and wording that makes it easy to comprehend
                - use emojis to boost engagement and help with navigation. around 2-3 per slide.

                - 4–6 bullets total. One fact per bullet. No paragraphs.
                - ONLY use information present in the provided text. DO NOT fabricate or add general knowledge.
                - Do NOT start bullets with category labels (e.g., "Epidemiology:", "Prevalence:", "Demographics:", "Skeletal destruction:").
                • Allowed labels only when logically required: **Criteria** and **Comparison**.
                - Never chain multiple facts with ";" or long commas. Split into separate bullets.
                - Use symbols & shorthand aggressively:
                ↑ increased/elevated, ↓ decreased/low, → leads to/causes, ~ approx, ≈ ratio, κ/λ light chains.
                - Prefer standard abbreviations when obvious from context.
                - Ratios compact: "M:F ≈ 3:1", "Blacks:Whites ≈ 2:1".
                - Minimal bold: only the key term in a bullet (e.g., **NSTEMI**, **Troponin I**, **Russell bodies**).
                - Zero filler or basic definitions.

                REASONING (internal; do not output)
                1) Identify intent (definition/essence, criteria, comparison, atypical features, pathology/morphology, epidemiology).
                2) Select the 4–6 highest-yield, exam-testable facts FROM THE TEXT; prefer differentiators and criteria.
                3) Rewrite each as: **Label** → compressed fact(s) with symbols/abbrevs.
                4) Self-check before output:
                - Bullet count 4–6 or as little as possible.
                - No bullet starts with a category label (except **Criteria**/**Comparison** when necessary).
                - No ";" inside bullets.
                - Each bullet ≤ ~18–22 words.

                FORMATTING REQUIREMENTS (MUST USE WHEN SUITABLE)
                
                You MUST actively use these formatting options when appropriate. Do not skip them!
                
                1. **Bold (**text**):** REQUIRED for all key medical terms, conditions, drugs, or important concepts (e.g., **NSTEMI**, **Troponin I**, **Russell bodies**, **ACE inhibitors**).
                
                2. *Italics (*text*):* REQUIRED when:
                   - Defining a term (e.g., *also known as*, *synonym for*)
                   - Providing alternative names (e.g., *AKA: congestive heart failure*)
                   - Adding subtle emphasis or clarifications
                   - Example: "**MI** *or myocardial infarction* is..."
                
                3. `Inline Code (backticks):` REQUIRED for:
                   - Lab values (e.g., `CRP > 3mg/L`, `HbA1c < 7%`)
                   - Dosages (e.g., `500mg BID`, `2.5mg daily`)
                   - Measurements (e.g., `BP > 140/90`, `HR < 60 bpm`)
                   - Any specific numerical values that need emphasis
                
                4. ## Headings: REQUIRED when summarizing multiple distinct concepts or topics. Use ## for main sections (e.g., ## Pathophysiology, ## Clinical Features, ## Treatment). You MUST use headings if covering 2+ different aspects of a topic.
                
                5. ### Subheadings: Use ### for subsections within a main topic when needed.
                
                6. Numbered Lists (1., 2., 3.): REQUIRED for:
                   - Sequential processes (diagnostic steps, treatment protocols)
                   - Ordered criteria or stages
                   - Step-by-step procedures
                   - Example: "1. Initial assessment 2. Lab tests 3. Imaging"
                
                7. Horizontal Rules (---): REQUIRED to visually separate distinct topics when summarizing multiple unrelated concepts in one response. Use `---` between different topics or sections.
                
                8. Block Quotes (> text): REQUIRED for:
                   - Clinical pearls or important takeaways
                   - Exam tips or common pitfalls
                   - Key memorization points
                   - Example: "> Remember: This is a high-yield exam fact"
                
                9. Tables: Use markdown tables for comparisons, structured data, or multi-attribute lists. IMPORTANT: Tables can AND SHOULD be combined with explanatory text before or after. Do not use tables in isolation - add context bullets around them.
                   - Always use **bold** for table headers
                   - Use **bold** or `backticks` for critical values in cells
                   - Example format:
                     "Key differences:
                     | Feature | Type A | Type B |
                     |---------|--------|--------|
                     | Onset | Acute | Chronic |
                     Additional notes about the comparison..."
                
                10. Color spans (use when highlighting critical info):
                    - <span style="color: #ff6b6b;">⚠️ Critical warnings</span>
                    - <span style="color: #4ecdc4;">🔑 High-yield facts</span>
                    - <span style="color: #ffe66d;">💡 Clinical significance</span>
                
                FORMATTING CHECKLIST (before output):
                ✓ Did I use **bold** for key terms?
                ✓ Did I use *italics* for definitions/alternatives?
                ✓ Did I use `backticks` for lab values/measurements?
                ✓ If multiple topics: Did I use ## headings and/or --- separators?
                ✓ If sequential steps: Did I use numbered lists?
                ✓ If important note: Did I use > block quote?
                ✓ If comparison data: Did I use a table WITH explanatory text?
                
                - **Criteria:** Use a single bullet if it's the classic triad/"≥2 of 3". Otherwise split into bullets.
                - **Comparison:** Up to 3 bullets, one per entity, each starting with a bold entity then its defining line.

                Return ONLY the formatted bullets from the page content, or "No summary needed for this page." if inappropriate."""
            )
            page_part = f"page {page}" if page else "this page"
            user_msg = f"Summarize {page_part} content for quick revision:\n\n{text.strip()}"
            if extra_instruction and extra_instruction.strip():
                user_msg += f"\n\nAdditional instruction: {extra_instruction.strip()}"

            resp = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "system", "content": system_msg},
                          {"role": "user", "content": user_msg}],
                temperature=0.7,
            )
            out = resp.choices[0].message.content.strip()
            print(f"🟢 [summarize_page] ✅ API Success! Summary length: {len(out)}")
            print(f"🟢 [summarize_page] Summary preview: {out[:100]}...")
            return {"ok": True, "summary": out}
        except Exception as e:
            print(f"🔴 [summarize_page] ❌ Exception: {e}")
            import traceback
            traceback.print_exc()
            return {"ok": False, "error": str(e)}

    # called from JS: EXPLAIN the CURRENT PAGE in a detailed, easy-to-understand way
    def explain_page(self, text: str, page: int | None = None, extra_instruction: str = ""):
        try:
            print(f"\n💡 [explain_page] Called for page {page}")
            print(f"💡 [explain_page] Text length: {len(text) if text else 0}")
            if extra_instruction:
                print(f"💡 [explain_page] Extra instruction: {extra_instruction}")
            
            if not text or not text.strip():
                print(f"🔴 [explain_page] Empty page text!")
                return {"ok": False, "error": "Empty page"}
            
            print(f"💡 [explain_page] Text preview: {text[:100]}...")
            
            self._require_key()
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            
            print(f"💡 [explain_page] OpenAI key set, calling API...")

            system_msg = (
                """Explain this slide simply and clearly. MAX 100-120 words.
                
                If you need to present structured data, comparisons, or lists with multiple attributes, use a markdown table format. Tables should use the pipe (|) separator format:
                | Header 1 | Header 2 | Header 3 |
                |----------|----------|----------|
                | Data 1   | Data 2   | Data 3   |
                
                Use tables when comparing items, showing relationships, or organizing structured information.

APPROACH:
• Start with 1 sentence overview
• Use an analogy if it helps (e.g., "Think of X like...")
• Break down into 2-4 key points
• Use a table for comparisons (format: | Column | Column |)
• Bold key terms: **term**
• 1-2 emojis max 🧠

EXAMPLE:
"**Synaptic transmission** 🧠 is how neurons communicate. Think of it like passing a note between desks.

| Step | What Happens |
|------|--------------|
| 1 | Vesicles release neurotransmitters |
| 2 | They cross the synaptic gap |
| 3 | Bind to receptors on next neuron |

Key: **Excitatory** (speed up) vs **Inhibitory** (slow down) signals."

IF title/reference page → "Title page."
KEEP IT SHORT & SIMPLE."""
            )
            page_part = f"page {page}" if page else "this page"
            user_msg = f"Explain {page_part} content in a way that helps students understand:\n\n{text.strip()}"
            if extra_instruction and extra_instruction.strip():
                user_msg += f"\n\nAdditional instruction: {extra_instruction.strip()}"

            resp = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "system", "content": system_msg},
                          {"role": "user", "content": user_msg}],
                temperature=0.8,
            )
            out = resp.choices[0].message.content.strip()
            print(f"🟢 [explain_page] ✅ API Success! Explanation length: {len(out)}")
            print(f"🟢 [explain_page] Explanation preview: {out[:100]}...")
            return {"ok": True, "summary": out}
        except Exception as e:
            print(f"🔴 [explain_page] ❌ Exception: {e}")
            import traceback
            traceback.print_exc()
            return {"ok": False, "error": str(e)}

    # Open Akson Widget (base.py)
    def open_akson_widget(self):
        """Launch the Akson screen capture widget (singleton - only one instance)."""
        try:
            import subprocess
            
            # Check if already running
            if self._akson_process is not None:
                # Check if still alive
                if self._akson_process.poll() is None:
                    print("ℹ️ Akson widget is already running")
                    return {"ok": True, "message": "Already running"}
                else:
                    print("Previous Akson instance closed, starting new one")
                    self._akson_process = None
            
            # Detect if running from PyInstaller bundle
            is_bundled = getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')
            
            if is_bundled:
                # When bundled, use sys._MEIPASS to find data files
                # PyInstaller extracts data files to sys._MEIPASS at runtime
                meipass = Path(sys._MEIPASS)
                base_path = meipass / "base.py"
                
                # If not found in _MEIPASS, try relative to executable (for one-dir mode)
                if not base_path.exists():
                    if sys.platform == "darwin":
                        # On macOS, executable is in .app/Contents/MacOS/
                        # base.py might be in .app/Contents/Resources/ or same as executable
                        exe_dir = Path(sys.executable).parent  # MacOS directory
                        base_path = exe_dir.parent / "Resources" / "base.py"
                        if not base_path.exists():
                            base_path = Path(sys.executable).parent / "base.py"
                    else:
                        # Windows/Linux: base.py should be in same directory as executable
                        base_path = Path(sys.executable).parent / "base.py"
                
                # Find Python interpreter - try system Python first
                python_cmd = None
                for cmd in ['python3', 'python']:
                    try:
                        result = subprocess.run([cmd, '--version'], 
                                              capture_output=True, 
                                              timeout=2)
                        if result.returncode == 0:
                            python_cmd = cmd
                            break
                    except (FileNotFoundError, subprocess.TimeoutExpired):
                        continue
                
                if python_cmd is None:
                    return {"ok": False, "error": "Python interpreter not found. Please ensure Python 3 is installed and has required packages (pynput, PyQt6, etc.)."}
            else:
                # Not bundled - use normal path
                base_path = Path(__file__).parent / "base.py"
                python_cmd = sys.executable
            
            if not base_path.exists():
                print(f"Error: base.py not found at {base_path}")
                return {"ok": False, "error": f"base.py not found at {base_path}"}
            
            # Verify required dependencies are available
            try:
                import subprocess as sp_check
                check_result = sp_check.run(
                    [python_cmd, "-c", "import pynput, PyQt6, mss, cv2, numpy, openai, requests, pytesseract, PIL, genanki"],
                    capture_output=True,
                    timeout=5
                )
                if check_result.returncode != 0:
                    missing_deps = check_result.stderr.decode('utf-8', errors='ignore')
                    return {
                        "ok": False, 
                        "error": f"Missing required dependencies. Please install: pip install pynput PyQt6 mss opencv-python numpy openai requests pytesseract Pillow genanki\n\nError: {missing_deps[:200]}"
                    }
            except Exception as e:
                print(f"⚠️ Warning: Could not verify dependencies: {e}")
            
            # Set up environment with API key
            env = os.environ.copy()
            env['OPENAI_API_KEY'] = OPENAI_API_KEY
            
            print(f"🚀 Launching Akson widget from: {base_path}")
            print(f"   Using Python: {python_cmd}")
            
            # Launch base.py with visible output for debugging
            if sys.platform == "darwin":  # macOS
                self._akson_process = subprocess.Popen(
                    [python_cmd, str(base_path)], 
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True
                )
            elif sys.platform == "win32":  # Windows
                self._akson_process = subprocess.Popen(
                    [python_cmd, str(base_path)],
                    env=env,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True
                )
            else:  # Linux
                self._akson_process = subprocess.Popen(
                    [python_cmd, str(base_path)],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True
                )
            
            print(f"✅ Akson widget launched (PID: {self._akson_process.pid})")
            
            # Monitor output in background thread
            def print_output():
                try:
                    for line in self._akson_process.stdout:
                        print(f"[Akson] {line.strip()}")
                except Exception:
                    pass
                finally:
                    print("Akson widget closed")
                    self._akson_process = None
            
            threading.Thread(target=print_output, daemon=True).start()
            
            return {"ok": True}
        except Exception as e:
            print(f"❌ Error launching Akson widget: {e}")
            import traceback
            traceback.print_exc()
            self._akson_process = None
            return {"ok": False, "error": str(e)}
    
    # Optional hook for Akson Flashcards button
    def open_flashcards(self):
        try:
            self.window.evaluate_js("alert('Akson Flashcards: wire this to your module.');")
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # called from JS: generate flashcards for a page using base.py-style prompt
    def generate_flashcards(self, text: str, page: int | None = None):
        try:
            if not text or not text.strip():
                return {"ok": False, "error": "Empty text"}
            self._require_key()
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)

            prompt = (
                f"""
You are a medical flashcard generator.

The following text is taken from a medical lecture slide:

"\"\"{text.strip()}"\"\"

Your task is to generate high-yield Q&A-style flashcards for medical students. Each flashcard must target content that could realistically appear in clinical MCQs or written exams.

🎯 INSTRUCTIONS:
Extract only medically relevant, exam-appropriate material from the slide.
Focus on diagnoses, mechanisms, symptoms, treatments, first-line drugs, investigations, key cutoffs, pathways, and classic clinical signs.
Avoid trivia or background info not relevant to exams.

✅ FORMAT:
For each card, output exactly:

Question: ...
Answer: ...

Each card must:
- Be concise, clear, and high-yield
- Use bullet-point style in answers — short, direct phrases (not full sentences)
- Follow Anki-style formatting: easy to read and memorize

❌ Do NOT:
- Add explanations or teaching
- Include general knowledge not found in exams
- Use fluffy or vague phrasing
- Include "fun facts" or contextless details

🎓 The final output must be clean, flashcard-ready, and focused entirely on what a student would need to recall under exam pressure.

Keep both question and answer very short (3-8 words for answers).
Answers:
• Single fact → just a few words
• Multiple facts → bullet points

Focus ONLY on exam-relevant content:
• Definitions
• Classic signs & symptoms
• Cutoffs & values
• First-line investigations
• First-line treatments/drugs
• Mechanisms of action
• Key complications

NOT EVERYTHING IN A SLIDE HAS TO BE A FLASHCARD. ONLY FOCUS ON 2-4 MAIN CONCEPTS THAT CAN COME UP IN AN EXAM. DO NOT INCLUDE VERY BASIC THINGS THE STUDENT SHOULD ALREADY KNOW.

Examples (DO NOT REPRODUCE - JUST FOR FORMATTING):
Question: What is the first-line treatment for strep throat?
Answer: Penicillin V

Question: Where is aldosterone produced?
Answer: Zona glomerulosa (adrenal cortex)

Question: Cutoff for diabetes diagnosis (fasting glucose)?
Answer: ≥7.0 mmol/L

Final Output:
Only clean, concise, flashcard-ready Q&A pairs. Nothing else.
"""
            )

            resp = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "You are a medical flashcard generator."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.5,
            )
            raw = resp.choices[0].message.content.strip()
            print(f"🃏 Flashcard Generation for Page {page} 🃏")
            print(f"Text length: {len(text)}")
            print(f"GPT Response:\n{raw}")
            print("─" * 50)

            # Check if AI explicitly said no cards should be generated
            if "NO_CARDS" in raw.upper() or not raw:
                print(f"⚠️ NO_CARDS detected or empty response")
                return {"ok": True, "cards": [], "page": page}

            # Parse using regex like base.py
            import re
            matches = re.findall(
                r'Question[:\s]+(.+?)\s+Answer[:\s]+(.+?)(?=\nQuestion[:\s]+|\Z)', 
                raw, 
                re.IGNORECASE | re.DOTALL
            )
            print(f"📝 Regex found {len(matches)} Q&A pairs")
            
            cards: list[dict] = []
            for q, a in matches:
                q_clean = q.strip()
                a_clean = a.strip()
                # Basic validation: skip if answer is too generic or suspiciously long
                if len(a_clean) > 200:  # Answers should be concise
                    print(f"⚠️ Skipping card with answer > 200 chars: {a_clean[:50]}...")
                    continue
                cards.append({"q": q_clean, "a": a_clean})

            # Sanitize and filter
            cards = [
                {"q": (c.get("q") or "").strip(), "a": (c.get("a") or "").strip()}
                for c in cards if (c.get("q") or c.get("a"))
            ]
            
            print(f"✅ Returning {len(cards)} flashcards")
            
            # If no valid cards were parsed, return empty
            if not cards:
                return {"ok": True, "cards": [], "page": page}

            return {"ok": True, "cards": cards, "page": page}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # Ask AI with document context (streaming version)
    def ask_ai(self, question: str, context: str = "", page: int | None = None, stream: bool = False):
        try:
            if not question or not question.strip():
                return {"ok": False, "error": "Empty question"}
            self._require_key()
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)

            # Balanced approach - use context when relevant, but don't over-rely on it
            system_msg = """You are a helpful AI tutor and study assistant for students.

**BALANCED ANSWERING STRATEGY:**
1. Always provide a comprehensive, well-rounded answer using your general knowledge as the foundation.
2. If document context is provided AND it directly relates to the question:
   - Use it to enhance your answer with specific details from the document
   - Mention key points from the context when they add value
   - Don't limit yourself to only what's in the context - supplement with general knowledge
3. If the question is general or the context is irrelevant:
   - Answer fully from your knowledge base without mentioning the context
4. Find the right balance: neither too document-specific nor too general
   - Include document details when they're directly relevant and add value
   - Always ensure the answer is complete and comprehensive even without the context
   - Avoid being counterproductive by over-quoting or ignoring the document entirely

Format clearly:
- **Bold** key terms
- Bullet points for lists
- Clear, student-friendly explanations
- Be thorough but concise"""

            user_msg = f"Question: {question.strip()}"
            # Include context but frame it as supplementary
            if context and context.strip():
                user_msg += f"\n\n[Optional context from document - use if relevant, but answer comprehensively regardless]:\n{context.strip()[:1800]}"

            if stream:
                # Streaming mode: use evaluate_js to push chunks
                import threading
                def stream_response():
                    try:
                        resp = client.chat.completions.create(
                            model=OPENAI_MODEL,
                            messages=[
                                {"role": "system", "content": system_msg},
                                {"role": "user", "content": user_msg}
                            ],
                            temperature=0.7,
                            stream=True,
                        )
                        full_text = ""
                        for chunk in resp:
                            delta = chunk.choices[0].delta if chunk.choices else None
                            content = delta.content if delta and delta.content else ""
                            if content:
                                full_text += content
                                # Push chunk to JS - escape properly
                                escaped = content.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "\\r")
                                js_code = f"""
                                if (window.updateStreamingAI) {{
                                    window.updateStreamingAI('{escaped}');
                                }}
                                """
                                try:
                                    self.window.evaluate_js(js_code)
                                except:
                                    pass
                        # Final update
                        try:
                            self.window.evaluate_js(f"""
                            if (window.finishStreamingAI) {{
                                window.finishStreamingAI();
                            }}
                            """)
                        except:
                            pass
                        return {"ok": True, "answer": full_text}
                    except Exception as e:
                        try:
                            self.window.evaluate_js(f"""
                            if (window.finishStreamingAI) {{
                                window.finishStreamingAI();
                            }}
                            """)
                        except:
                            pass
                        raise e
                
                thread = threading.Thread(target=stream_response, daemon=True)
                thread.start()
                return {"ok": True, "streaming": True}
            else:
                resp = client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": user_msg}
                    ],
                    temperature=0.7,
                )
                answer = resp.choices[0].message.content.strip()
                return {"ok": True, "answer": answer, "page": page}
        except Exception as e:
            print(f"❌ [ask_ai] Error: {e}")
            import traceback
            traceback.print_exc()
            return {"ok": False, "error": str(e)}

    # Library system: Save and load PDF data
    def save_library_data(self, filename: str, data: dict):
        """Save summaries and flashcards for a specific PDF file. Merges with existing data if file exists."""
        try:
            library_dir = CACHE_ROOT / "library"
            library_dir.mkdir(parents=True, exist_ok=True)
            
            # Sanitize filename for use as filepath
            safe_filename = self._sanitize_library_name(filename)
            library_file = library_dir / f"{safe_filename}.json"
            
            # If file exists, load existing data and merge with new data
            existing_data = {}
            if library_file.exists():
                try:
                    with open(library_file, 'r', encoding='utf-8') as f:
                        existing_data = json.load(f)
                    print(f"📚 Merging with existing library data for: {filename}")
                except Exception as e:
                    print(f"⚠️ Could not load existing data, will overwrite: {e}")
                    existing_data = {}
            
            # Merge summaries: new data takes precedence (updates existing pages, adds new pages)
            merged_summaries = existing_data.get('summaries', {})
            if 'summaries' in data:
                merged_summaries.update(data['summaries'])
            
            # Merge flashcards: new data takes precedence (updates existing pages, adds new pages)
            merged_flashcards = existing_data.get('flashcards', {})
            if 'flashcards' in data:
                merged_flashcards.update(data['flashcards'])
            
            # Create merged data
            merged_data = {
                'summaries': merged_summaries,
                'flashcards': merged_flashcards,
                'lastModified': data.get('lastModified', datetime.now().isoformat())
            }
            
            # Save the merged data
            with open(library_file, 'w', encoding='utf-8') as f:
                json.dump(merged_data, f, indent=2, ensure_ascii=False)
            
            print(f"📚 Saved library data for: {filename} ({len(merged_summaries)} summaries, {len(merged_flashcards)} flashcard pages)")
            return {"ok": True, "path": str(library_file)}
        except Exception as e:
            print(f"Error saving library data: {e}")
            return {"ok": False, "error": str(e)}
    
    def save_settings(self, settings: dict):
        """Save application settings (hotkey, sidebar state, etc.)"""
        try:
            settings_file = CACHE_ROOT / "settings.json"
            with open(settings_file, 'w') as f:
                json.dump(settings, f, indent=2)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    
    def load_settings(self):
        """Load application settings"""
        try:
            settings_file = CACHE_ROOT / "settings.json"
            if settings_file.exists():
                with open(settings_file, 'r') as f:
                    settings = json.load(f)
                return {"ok": True, "settings": settings}
            else:
                # Return defaults
                return {
                    "ok": True, 
                    "settings": {
                        "sidebarHotkey": "Ctrl+B",
                        "sidebarHidden": False,
                        "sidebarWidth": 380,
                        "animationSpeed": "normal",
                        "defaultSidebarState": "visible",
                        "autoSaveEnabled": True,
                        "fontSize": "medium",
                        "pdfZoom": "auto",
                        "pdfDarkMode": False
                    }
                }
        except Exception as e:
            return {"ok": False, "error": str(e)}
    
    def load_library_data(self, filename: str):
        """Load summaries and flashcards for a specific PDF file."""
        try:
            library_dir = CACHE_ROOT / "library"
            
            # Sanitize filename
            safe_filename = self._sanitize_library_name(filename)
            library_file = library_dir / f"{safe_filename}.json"
            
            if not library_file.exists():
                print(f"📚 No library data found for: {filename}")
                return {"ok": True, "data": {"summaries": {}, "flashcards": {}}}
            
            # Load the data
            with open(library_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            print(f"📚 Loaded library data for: {filename}")
            print(f"   - Summaries: {len(data.get('summaries', {}))} pages")
            print(f"   - Flashcards: {sum(len(cards) for cards in data.get('flashcards', {}).values())} cards")
            
            return {"ok": True, "data": data}
        except Exception as e:
            print(f"Error loading library data: {e}")
            return {"ok": False, "error": str(e)}
    
    def get_library_file(self, filename: str):
        """Get library file data (alias for load_library_data for consistency with frontend)."""
        return self.load_library_data(filename)
    
    def rename_library_file(self, old_filename: str, new_filename: str):
        """Rename a library file (both the JSON file and update references)."""
        try:
            library_dir = CACHE_ROOT / "library"
            
            # Sanitize filenames
            safe_old = self._sanitize_library_name(old_filename)
            safe_new = self._sanitize_library_name(new_filename)
            
            if not safe_new or safe_new == safe_old:
                return {"ok": False, "error": "Invalid new filename"}
            
            old_file = library_dir / f"{safe_old}.json"
            new_file = library_dir / f"{safe_new}.json"
            
            if not old_file.exists():
                return {"ok": False, "error": "Original file not found"}
            
            if new_file.exists() and new_file != old_file:
                return {"ok": False, "error": "New filename already exists"}
            
            # Load old data and update filename in it
            with open(old_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Update the filename in the data (if stored)
            data['filename'] = safe_new
            # Also update doc_name/pdf_path if present
            if data.get("doc_name"):
                data["doc_name"] = f"{safe_new}.pdf"
            if data.get("pdf_path"):
                try:
                    current_pdf = Path(data["pdf_path"])
                    if current_pdf.exists() and current_pdf.parent.resolve() == DOCS_DIR.resolve():
                        new_pdf = current_pdf.with_name(f"{safe_new}{current_pdf.suffix}")
                        # Avoid collision
                        if new_pdf.exists() and new_pdf != current_pdf:
                            return {"ok": False, "error": "Target PDF name already exists"}
                        current_pdf.rename(new_pdf)
                        data["pdf_path"] = str(new_pdf)
                        data["doc_name"] = new_pdf.name
                    else:
                        # If stored path is outside docs, do not touch it
                        data["pdf_path"] = data["pdf_path"]
                except Exception as e:
                    return {"ok": False, "error": f"Could not rename PDF: {e}"}
            
            # Write to new file
            with open(new_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            # Remove old file
            if old_file != new_file:
                old_file.unlink()

            # Update metadata folder references
            metadata_file = library_dir / "metadata.json"
            if metadata_file.exists():
                try:
                    with open(metadata_file, "r", encoding="utf-8") as mf:
                        metadata = json.load(mf)
                except Exception:
                    metadata = {}
                folders = metadata.get("folders", {})
                updated = False
                for fname, files in folders.items():
                    if safe_old in files:
                        files[:] = [safe_new if x == safe_old else x for x in files]
                        updated = True
                if updated:
                    metadata["folders"] = folders
                    with open(metadata_file, "w", encoding="utf-8") as mf:
                        json.dump(metadata, mf, indent=2, ensure_ascii=False)
            
            print(f"📚 Renamed library file: {old_filename} → {new_filename}")
            return {"ok": True, "newFilename": safe_new, "docName": data.get("doc_name"), "pdfPath": data.get("pdf_path")}
        except Exception as e:
            print(f"Error renaming library file: {e}")
            return {"ok": False, "error": str(e)}
    
    def delete_library_file(self, filename: str):
        """Delete a library file and all its summaries/flashcards."""
        try:
            library_dir = CACHE_ROOT / "library"
            
            # Sanitize filename
            safe_filename = self._sanitize_library_name(filename)
            library_file = library_dir / f"{safe_filename}.json"
            
            if not library_file.exists():
                return {"ok": False, "error": "Library file not found"}
            
            # Delete the file
            library_file.unlink()
            print(f"🗑️ Deleted library file: {filename}")
            return {"ok": True, "message": f"Lecture '{filename}' and all its summaries/flashcards deleted successfully"}
        except Exception as e:
            print(f"Error deleting library file: {e}")
            return {"ok": False, "error": str(e)}
    
    def generate_mindmap(self, filename: str):
        """Generate a mindmap structure from library file data with page images."""
        try:
            library_dir = CACHE_ROOT / "library"
            safe_filename = self._sanitize_library_name(filename)
            library_file = library_dir / f"{safe_filename}.json"
            
            if not library_file.exists():
                return {"ok": False, "error": "Library file not found"}
            
            with open(library_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Extract and process data
            summaries = data.get('summaries', {})
            flashcards = data.get('flashcards', {})
            pdf_path = data.get('pdf_path')  # Get PDF path from library data
            
            # Create images directory for this mindmap
            images_dir = library_dir / f"{safe_filename}_mindmap_images"
            images_dir.mkdir(exist_ok=True)
            
            # Helper function to capture PDF page image
            def capture_page_image(page_num: int) -> str | None:
                """Capture a PDF page as an image. Returns image path or None."""
                if not pdf_path or not isinstance(page_num, int):
                    return None
                try:
                    import fitz  # PyMuPDF
                    pdf_file = Path(pdf_path)
                    if not pdf_file.exists():
                        return None
                    
                    doc = fitz.open(str(pdf_file))
                    if page_num < 0 or page_num >= len(doc):
                        doc.close()
                        return None
                    
                    page = doc[page_num]
                    # Render page at 2x zoom for better quality
                    mat = fitz.Matrix(2.0, 2.0)
                    pix = page.get_pixmap(matrix=mat)
                    
                    # Save image
                    img_path = images_dir / f"page_{page_num}.png"
                    pix.save(str(img_path))
                    doc.close()
                    
                    # Return relative path for web serving
                    return f"/mindmap_images/{safe_filename}_mindmap_images/page_{page_num}.png"
                except Exception as e:
                    print(f"Error capturing page {page_num} image: {e}")
                    return None
            
            # Build mindmap structure
            mindmap = {
                "central_node": {
                    "id": "center",
                    "label": filename,
                    "type": "central",
                    "size": "large"
                },
                "branches": []
            }
            
            # Simplified color scheme - accent colors per branch
            accent_colors = [
                "#667eea", "#f093fb", "#4facfe", "#43e97b", 
                "#fa709a", "#30cfd0", "#a8edea", "#ffecd2"
            ]
            
            # Process summaries to extract main topics
            topic_map = {}
            all_summary_text = ""
            
            for page_num, summary in summaries.items():
                page_num_int = int(page_num) if page_num.isdigit() else 0
                summary_text = summary if isinstance(summary, str) else summary.get('summary', '')
                all_summary_text += f"Page {page_num}: {summary_text}\n\n"
                
                # Extract key concepts (simple keyword extraction)
                words = summary_text.lower().split()
                key_words = [w.strip('.,;:!?()[]{}') for w in words if len(w) > 4]
                
                for word in key_words[:5]:  # Top 5 keywords per page
                    if word not in topic_map:
                        topic_map[word] = {"pages": [], "summaries": []}
                    topic_map[word]["pages"].append(page_num_int)
                    topic_map[word]["summaries"].append(summary_text[:200])
            
            # Process flashcards for additional topics
            for page_num, cards in flashcards.items():
                page_num_int = int(page_num) if page_num.isdigit() else 0
                if not isinstance(cards, list):
                    continue
                    
                for card in cards[:3]:  # Top 3 cards per page
                    if not isinstance(card, dict):
                        continue
                    q = card.get('q', '')
                    a = card.get('a', '')
                    
                    # Extract topic from question
                    q_words = q.lower().split()
                    key_word = next((w.strip('.,;:!?()[]{}') for w in q_words if len(w) > 4), None)
                    
                    if key_word and key_word not in topic_map:
                        topic_map[key_word] = {"pages": [], "summaries": [], "flashcards": []}
                    
                    if key_word:
                        topic_map[key_word]["pages"].append(page_num_int)
                        if key_word not in topic_map:
                            topic_map[key_word] = {"pages": [], "summaries": [], "flashcards": []}
                        topic_map[key_word]["flashcards"] = topic_map[key_word].get("flashcards", [])
                        topic_map[key_word]["flashcards"].append({"q": q[:100], "a": a[:100]})
            
            # Sort topics by frequency/importance
            sorted_topics = sorted(topic_map.items(), key=lambda x: len(x[1]["pages"]), reverse=True)[:8]
            
            # Create branches
            for idx, (topic, info) in enumerate(sorted_topics):
                # Simple accent color per branch
                accent_color = accent_colors[idx % len(accent_colors)]
                
                # Get primary page for this branch (most common page)
                branch_pages = sorted(set(info["pages"]))[:5]
                primary_page = branch_pages[0] if branch_pages else None
                
                # Capture page image if available
                page_image = None
                if primary_page is not None:
                    page_image = capture_page_image(primary_page)
                
                # Create branch node with simplified colors
                branch = {
                    "id": f"branch_{idx}",
                    "label": topic.capitalize(),
                    "type": "branch",
                    "size": "medium",
                    "accent": accent_color,  # Single accent color for this branch
                    "pages": branch_pages,
                    "page_image": page_image,
                    "subnodes": []
                }
                
                # Add summary subnodes (up to 3 per branch)
                for i, summary_text in enumerate(info["summaries"][:3]):
                    # Get corresponding page number
                    page_idx = info["pages"][i] if i < len(info["pages"]) else None
                    subnode_page = int(page_idx) if page_idx is not None and isinstance(page_idx, (int, str)) and str(page_idx).isdigit() else primary_page
                    subnode_image = capture_page_image(subnode_page) if subnode_page is not None else None
                    
                    branch["subnodes"].append({
                        "id": f"branch_{idx}_sub_{i}",
                        "label": summary_text[:60] + "..." if len(summary_text) > 60 else summary_text,
                        "type": "subnode",
                        "size": "small",
                        "full_text": summary_text,
                        "accent": accent_color,  # Same accent as branch
                        "page": subnode_page,
                        "image": subnode_image
                    })
                
                # Add flashcard subnodes
                for i, fc in enumerate(info.get("flashcards", [])[:2]):
                    fc_page = int(fc.get("page", primary_page)) if isinstance(fc.get("page", primary_page), (int, str)) and str(fc.get("page", primary_page)).isdigit() else primary_page
                    fc_image = capture_page_image(fc_page) if fc_page is not None else None
                    
                    branch["subnodes"].append({
                        "id": f"branch_{idx}_fc_{i}",
                        "label": fc.get("q", "")[:50] + "?" if len(fc.get("q", "")) > 50 else fc.get("q", "") + "?",
                        "type": "flashcard",
                        "size": "small",
                        "full_text": f"Q: {fc.get('q', '')}\nA: {fc.get('a', '')}",
                        "accent": accent_color,  # Same accent as branch
                        "page": fc_page,
                        "image": fc_image
                    })
                
                mindmap["branches"].append(branch)
            
            # If no topics found, create a simple structure from summaries
            if len(mindmap["branches"]) == 0 and summaries:
                for idx, (page_num, summary) in enumerate(list(summaries.items())[:6]):
                    accent_color = accent_colors[idx % len(accent_colors)]
                    summary_text = summary if isinstance(summary, str) else summary.get('summary', '')
                    page_num_int = int(page_num) if page_num.isdigit() else 0
                    page_image = capture_page_image(page_num_int) if page_num_int >= 0 else None
                    
                    mindmap["branches"].append({
                        "id": f"branch_{idx}",
                        "label": f"Page {page_num}",
                        "type": "branch",
                        "size": "medium",
                        "accent": accent_color,
                        "pages": [page_num_int],
                        "page_image": page_image,
                        "subnodes": [{
                            "id": f"branch_{idx}_sub_0",
                            "label": summary_text[:60] + "..." if len(summary_text) > 60 else summary_text,
                            "type": "subnode",
                            "size": "small",
                            "full_text": summary_text,
                            "accent": accent_color
                        }]
                    })
            
            print(f"🗺️ Generated mindmap with {len(mindmap['branches'])} branches for: {filename}")
            return {"ok": True, "mindmap": mindmap}
            
        except Exception as e:
            print(f"Error generating mindmap: {e}")
            import traceback
            traceback.print_exc()
            return {"ok": False, "error": str(e)}
    
    def list_library_files(self):
        """List all PDF files in the library."""
        try:
            library_dir = CACHE_ROOT / "library"
            if not library_dir.exists():
                return {"ok": True, "files": []}
            
            files = []
            for file in library_dir.glob("*.json"):
                try:
                    with open(file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    # Extract module from stored data or infer from filename prefix
                    module = data.get('module')
                    if not module:
                        stem = file.stem
                        # Heuristic: take prefix before first space, dash, or underscore
                        import re
                        m = re.match(r"^([A-Za-z0-9]+)", stem)
                        module = m.group(1).upper() if m else "UNCATEGORIZED"
                        # Normalize some common module-like prefixes (e.g., GIHEP → GIHEP)
                        module = module.strip() or "UNCATEGORIZED"

                    files.append({
                        "name": file.stem,
                        "summaries": len(data.get('summaries', {})),
                        "flashcards": sum(len(cards) for cards in data.get('flashcards', {}).values()),
                        "lastModified": data.get('lastModified', 'Unknown'),
                        "last_opened": data.get('last_opened', data.get('lastModified', None)),
                        "module": module
                    })
                except Exception:
                    continue
            
            # Load favorites and folders from a separate metadata file
            metadata_file = library_dir / "metadata.json"
            favorites = []
            folders = {}
            
            if metadata_file.exists():
                try:
                    with open(metadata_file, 'r', encoding='utf-8') as f:
                        metadata = json.load(f)
                        favorites = metadata.get('favorites', [])
                        folders = metadata.get('folders', {})
                except Exception:
                    pass
            
            return {
                "ok": True,
                "files": sorted(files, key=lambda x: x.get('lastModified', ''), reverse=True),
                "favorites": favorites,
                "folders": folders
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}
    
    def toggle_favorite(self, filename: str, is_favorite: bool):
        """Toggle favorite status for a library file."""
        try:
            library_dir = CACHE_ROOT / "library"
            metadata_file = library_dir / "metadata.json"
            
            # Load existing metadata
            metadata = {"favorites": [], "folders": {}}
            if metadata_file.exists():
                try:
                    with open(metadata_file, 'r', encoding='utf-8') as f:
                        metadata = json.load(f)
                except Exception:
                    pass
            
            favorites = metadata.get('favorites', [])
            
            if is_favorite:
                if filename not in favorites:
                    favorites.append(filename)
            else:
                if filename in favorites:
                    favorites.remove(filename)
            
            metadata['favorites'] = favorites
            
            # Save metadata
            library_dir.mkdir(parents=True, exist_ok=True)
            with open(metadata_file, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)
            
            print(f"⭐ {'Favorited' if is_favorite else 'Unfavorited'}: {filename}")
            return {"ok": True}
        except Exception as e:
            print(f"Error toggling favorite: {e}")
            return {"ok": False, "error": str(e)}
    
    def create_folder(self, folder_name: str):
        """Create a new folder."""
        try:
            library_dir = CACHE_ROOT / "library"
            metadata_file = library_dir / "metadata.json"
            
            # Load existing metadata
            metadata = {"favorites": [], "folders": {}}
            if metadata_file.exists():
                try:
                    with open(metadata_file, 'r', encoding='utf-8') as f:
                        metadata = json.load(f)
                except Exception:
                    pass
            
            folders = metadata.get('folders', {})
            
            if folder_name in folders:
                return {"ok": False, "error": "Folder already exists"}
            
            folders[folder_name] = []
            metadata['folders'] = folders
            
            # Save metadata
            library_dir.mkdir(parents=True, exist_ok=True)
            with open(metadata_file, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)
            
            print(f"📁 Created folder: {folder_name}")
            return {"ok": True}
        except Exception as e:
            print(f"Error creating folder: {e}")
            return {"ok": False, "error": str(e)}
    
    def delete_folder(self, folder_name: str):
        """Delete a folder (files remain in library, just removed from folder)."""
        try:
            library_dir = CACHE_ROOT / "library"
            metadata_file = library_dir / "metadata.json"
            
            # Load existing metadata
            metadata = {"favorites": [], "folders": {}}
            if metadata_file.exists():
                try:
                    with open(metadata_file, 'r', encoding='utf-8') as f:
                        metadata = json.load(f)
                except Exception:
                    pass
            
            folders = metadata.get('folders', {})
            
            if folder_name not in folders:
                return {"ok": False, "error": "Folder not found"}
            
            # Remove folder (files remain in library)
            del folders[folder_name]
            metadata['folders'] = folders
            
            # Save metadata
            library_dir.mkdir(parents=True, exist_ok=True)
            with open(metadata_file, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)
            
            print(f"🗑️ Deleted folder: {folder_name}")
            return {"ok": True}
        except Exception as e:
            print(f"Error deleting folder: {e}")
            return {"ok": False, "error": str(e)}
    
    def rename_folder(self, old_name: str, new_name: str):
        """Rename a folder."""
        try:
            library_dir = CACHE_ROOT / "library"
            metadata_file = library_dir / "metadata.json"
            
            # Load existing metadata
            metadata = {"favorites": [], "folders": {}}
            if metadata_file.exists():
                try:
                    with open(metadata_file, 'r', encoding='utf-8') as f:
                        metadata = json.load(f)
                except Exception:
                    pass
            
            folders = metadata.get('folders', {})
            
            if old_name not in folders:
                return {"ok": False, "error": "Folder not found"}
            
            if new_name in folders:
                return {"ok": False, "error": "Folder with that name already exists"}
            
            # Rename folder by moving files to new key
            folders[new_name] = folders[old_name]
            del folders[old_name]
            metadata['folders'] = folders
            
            # Save metadata
            library_dir.mkdir(parents=True, exist_ok=True)
            with open(metadata_file, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)
            
            print(f"📝 Renamed folder: {old_name} → {new_name}")
            return {"ok": True, "newFolderName": new_name}
        except Exception as e:
            print(f"Error renaming folder: {e}")
            return {"ok": False, "error": str(e)}
    
    def move_to_folder(self, filename: str, folder_name: str):
        """Move a file to a folder (or remove from folder if folder_name is empty)."""
        try:
            library_dir = CACHE_ROOT / "library"
            metadata_file = library_dir / "metadata.json"
            
            # Load existing metadata
            metadata = {"favorites": [], "folders": {}}
            if metadata_file.exists():
                try:
                    with open(metadata_file, 'r', encoding='utf-8') as f:
                        metadata = json.load(f)
                except Exception:
                    pass
            
            folders = metadata.get('folders', {})
            
            # Remove from all folders first
            for folder, files in folders.items():
                if filename in files:
                    files.remove(filename)
            
            # Add to target folder if specified
            if folder_name:
                if folder_name not in folders:
                    folders[folder_name] = []
                if filename not in folders[folder_name]:
                    folders[folder_name].append(filename)
            
            metadata['folders'] = folders
            
            # Save metadata
            library_dir.mkdir(parents=True, exist_ok=True)
            with open(metadata_file, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)
            
            print(f"📦 Moved {filename} to folder: {folder_name if folder_name else 'none'}")
            return {"ok": True}
        except Exception as e:
            print(f"Error moving to folder: {e}")
            return {"ok": False, "error": str(e)}
    
    def open_library_file(self, filename: str):
        """Open a PDF file from the library by finding it in DOCS_DIR."""
        try:
            # Normalize filename key
            safe_filename = self._sanitize_library_name(filename)
            library_dir = CACHE_ROOT / "library"
            lib_data = {}
            pdf_file = None

            # Prefer stored pdf_path/doc_name if available
            try:
                lib_data_file = library_dir / f"{safe_filename}.json"
                if lib_data_file.exists():
                    with open(lib_data_file, 'r', encoding='utf-8') as f:
                        lib_data = json.load(f)
                    stored_path = lib_data.get("pdf_path")
                    if stored_path and Path(stored_path).exists():
                        pdf_file = Path(stored_path)
                    elif lib_data.get("doc_name"):
                        candidate = DOCS_DIR / lib_data["doc_name"]
                        if candidate.exists():
                            pdf_file = candidate
            except Exception as e:
                print(f"Warning: could not read library data for {filename}: {e}")

            # Find the PDF file in DOCS_DIR - it might have a number suffix
            # Try exact match first
            if pdf_file is None:
                pdf_file = DOCS_DIR / f"{filename}.pdf"
            
            if not pdf_file.exists():
                # Try with number suffixes
                base_name = filename
                # Remove any existing number suffix from filename
                import re
                base_name = re.sub(r'-\d+$', '', base_name)
                
                # Try base name first
                pdf_file = DOCS_DIR / f"{base_name}.pdf"
                
                if not pdf_file.exists():
                    # Try with number suffixes
                    i = 1
                    while i < 100:  # Limit to 100 attempts
                        candidate = DOCS_DIR / f"{base_name}-{i}.pdf"
                        if candidate.exists():
                            pdf_file = candidate
                            break
                        i += 1
                    
                    if not pdf_file.exists():
                        return {"ok": False, "error": "PDF file not found in documents directory"}
            
            # Update last_opened timestamp in library file
            try:
                from datetime import datetime
                library_dir = CACHE_ROOT / "library"
                library_file = library_dir / f"{filename}.json"
                if library_file.exists():
                    with open(library_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    data['last_opened'] = datetime.now().isoformat()
                    with open(library_file, 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
            except Exception as e:
                print(f"Warning: Could not update last_opened timestamp: {e}")
            
            # Build folder context if file is in a folder
            extra_params = None
            try:
                metadata_file = CACHE_ROOT / "library" / "metadata.json"
                library_dir = CACHE_ROOT / "library"
                if metadata_file.exists():
                    with open(metadata_file, 'r', encoding='utf-8') as f:
                        metadata = json.load(f)
                    folders = metadata.get("folders", {})
                    containing_folder = None
                    for fname in folders:
                        if filename in folders[fname]:
                            containing_folder = fname
                            break
                    if containing_folder:
                        files_in_folder = folders.get(containing_folder, [])
                        tab_files = []
                        for name in files_in_folder:
                            try:
                                safe_name = self._sanitize_library_name(name)
                                lib_file = library_dir / f"{safe_name}.json"
                                doc_name = None
                                if lib_file.exists():
                                    with open(lib_file, 'r', encoding='utf-8') as lf:
                                        data = json.load(lf)
                                        doc_name = data.get("doc_name") or Path(data.get("pdf_path", "")).name
                                tab_files.append(doc_name or f"{name}.pdf")
                            except Exception:
                                tab_files.append(f"{name}.pdf")
                        payload = json.dumps({
                            "folder": containing_folder,
                            "files": tab_files
                        })
                        import base64
                        payload_b64 = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("utf-8")
                        extra_params = {"folderImport": payload_b64}
            except Exception as e:
                print(f"Warning: could not build folder context: {e}")

            # If pdf_path in library data is reliable, prefer it
            try:
                lib_data_file = library_dir / f"{safe_filename}.json"
                if lib_data_file.exists():
                    with open(lib_data_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        stored_path = data.get("pdf_path")
                        if stored_path and Path(stored_path).exists():
                            pdf_file = Path(stored_path)
            except Exception as e:
                print(f"Warning: could not use stored pdf_path: {e}")

            # Load the PDF (mark as from library to skip duplicate prompt)
            self.load_pdf(pdf_file, from_library=True, extra_params=extra_params)
            print(f"📂 Opened library file: {filename}")
            return {"ok": True, "path": str(pdf_file)}
        except Exception as e:
            print(f"Error opening library file: {e}")
            return {"ok": False, "error": str(e)}
    
    def save_highlights_to_pdf(self, highlights_data: list):
        """Save highlights as annotations directly to the PDF using PyMuPDF."""
        try:
            if not self.current_pdf_path or not Path(self.current_pdf_path).exists():
                return {"ok": False, "error": "No PDF file loaded"}
            
            import fitz  # PyMuPDF
            
            pdf_path = Path(self.current_pdf_path)
            doc = fitz.open(str(pdf_path))
            
            # Color mapping from names to RGB tuples
            color_map = {
                "yellow": (1.0, 0.92, 0.23),
                "green": (0.3, 0.69, 0.31),
                "blue": (0.13, 0.59, 0.95),
                "pink": (1.0, 0.25, 0.51),
                "orange": (1.0, 0.6, 0),
                "purple": (0.61, 0.15, 0.69),
                "red": (0.96, 0.26, 0.21),
                "cyan": (0, 0.74, 0.83)
            }
            
            annotations_added = 0
            
            for highlight in highlights_data:
                try:
                    page_num = int(highlight.get('page', 1))
                    if page_num < 1 or page_num > len(doc):
                        continue
                    
                    page = doc[page_num - 1]  # PyMuPDF uses 0-based indexing
                    text = highlight.get('text', '')
                    color_name = highlight.get('color', 'yellow')
                    
                    # Search for the text in the page to get accurate position
                    text_instances = page.search_for(text)
                    
                    if text_instances:
                        # Use all instances or just the first one
                        for inst in text_instances[:1]:  # Just first occurrence for now
                            # Add highlight annotation
                            highlight_annot = page.add_highlight_annot(inst)
                            
                            # Set color
                            rgb_color = color_map.get(color_name, (1.0, 0.92, 0.23))
                            highlight_annot.set_colors(stroke=rgb_color)
                            highlight_annot.update()
                            annotations_added += 1
                
                except Exception as e:
                    print(f"Error adding highlight to page {highlight.get('page')}: {e}")
                    continue
            
            # Save the PDF with highlights
            if annotations_added > 0:
                # Save incrementally to preserve existing annotations
                doc.save(str(pdf_path), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
                doc.close()
                
                print(f"✨ Saved {annotations_added} highlights to PDF: {pdf_path.name}")
                return {"ok": True, "count": annotations_added}
            else:
                doc.close()
                return {"ok": True, "count": 0, "message": "No highlights added"}
                
        except Exception as e:
            print(f"Error saving highlights: {e}")
            import traceback
            traceback.print_exc()
            return {"ok": False, "error": str(e)}

    # Akson Cards Flashcards Center (FSRS-powered)
    def load_flashcards_center(self):
        """Load all decks with card counts"""
        try:
            decks = self._akson_store.get_decks()
            result = {}
            for deck_id, deck in decks.items():
                cards = self._akson_store.get_cards(deck_id=deck_id)
                result[deck.name] = {
                    "id": deck.id,
                    "name": deck.name,
                    "description": deck.description,
                    "total_cards": len(cards),
                    "due_cards": len(self._akson_store.get_due_cards(deck_id=deck_id))
                }
            return {"ok": True, "decks": result}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    
    def create_flashcards_deck(self, deck_name: str, description: str = ""):
        """Create a new deck"""
        try:
            deck_id = str(uuid.uuid4())
            deck = Deck(
                id=deck_id,
                name=deck_name.strip(),
                description=description.strip()
            )
            self._akson_store.save_deck(deck)
            return {"ok": True, "deck_id": deck_id}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    
    def import_flashcards_deck(self, deck_name: str, cards: list[dict]):
        """Import cards into a deck (creates deck if doesn't exist)"""
        try:
            # Find or create deck
            decks = self._akson_store.get_decks()
            deck = None
            for d in decks.values():
                if d.name == deck_name.strip():
                    deck = d
                    break
            
            if not deck:
                deck_id = str(uuid.uuid4())
                deck = Deck(id=deck_id, name=deck_name.strip())
                self._akson_store.save_deck(deck)
            
            # Get default model
            models = self._akson_store.get_models()
            model = models.get("basic")
            if not model:
                model = list(models.values())[0] if models else None
            
            if not model:
                return {"ok": False, "error": "No note model available"}
            
            # Import cards
            imported = 0
            for card_data in cards:
                # Create note
                note_id = str(uuid.uuid4())
                note = Note(
                    id=note_id,
                    deck_id=deck.id,
                    model_id=model.id,
                    fields={
                        "Front": card_data.get("q", ""),
                        "Back": card_data.get("a", "")
                    }
                )
                self._akson_store.save_note(note)
                
                # Create card
                card_id = str(uuid.uuid4())
                template_id = model.templates[0]["id"] if model.templates else "basic-1"
                card = Card(
                    id=card_id,
                    note_id=note_id,
                    template_id=template_id,
                    state="new"
                )
                self._akson_store.save_card(card)
                imported += 1
            
            return {"ok": True, "imported": imported}
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"ok": False, "error": str(e)}
    
    def rename_flashcards_deck(self, old_name: str, new_name: str):
        """Rename a deck"""
        try:
            decks = self._akson_store.get_decks()
            deck = None
            for d in decks.values():
                if d.name == old_name:
                    deck = d
                    break
            
            if not deck:
                return {"ok": False, "error": "Deck not found"}
            
            deck.name = new_name.strip()
            deck.updated_at = datetime.now()
            self._akson_store.save_deck(deck)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    
    def delete_flashcards_deck(self, name: str):
        """Delete a deck"""
        try:
            decks = self._akson_store.get_decks()
            deck = None
            for d in decks.values():
                if d.name == name:
                    deck = d
                    break
            
            if not deck:
                return {"ok": False, "error": "Deck not found"}
            
            self._akson_store.delete_deck(deck.id)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    
    def get_deck_details(self, deck_name: str):
        """Get all cards in a deck"""
        try:
            decks = self._akson_store.get_decks()
            deck = None
            for d in decks.values():
                if d.name == deck_name:
                    deck = d
                    break
            
            if not deck:
                return {"ok": False, "error": "Deck not found"}
            
            notes = self._akson_store.get_notes(deck_id=deck.id)
            cards = self._akson_store.get_cards(deck_id=deck.id)
            
            result = []
            for note in notes.values():
                note_cards = [c for c in cards.values() if c.note_id == note.id]
                for card in note_cards:
                    result.append({
                        "id": card.id,
                        "front": note.fields.get("Front", ""),
                        "back": note.fields.get("Back", ""),
                        "state": card.state,
                        "due": card.due.isoformat() if card.due else None,
                        "reps": card.reps,
                        "lapses": card.lapses
                    })
            
            return {"ok": True, "cards": result}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    
    def start_study_session(self, deck_name: str, limit: int = None, new_limit: int = None):
        """Start a study session for a deck"""
        try:
            decks = self._akson_store.get_decks()
            deck = None
            for d in decks.values():
                if d.name == deck_name:
                    deck = d
                    break
            
            if not deck:
                return {"ok": False, "error": "Deck not found"}
            
            session = StudySession(self._akson_store, deck_id=deck.id)
            started = session.start(limit=limit, new_limit=new_limit)
            
            if not started:
                return {"ok": False, "error": "No cards to study"}
            
            self._study_sessions[deck.id] = session
            
            # Get first card
            current = session.get_current_card()
            if not current:
                return {"ok": False, "error": "No cards available"}
            
            card, note = current
            progress = session.get_progress()
            
            return {
                "ok": True,
                "card": {
                    "id": card.id,
                    "front": note.fields.get("Front", ""),
                    "back": note.fields.get("Back", ""),
                    "state": card.state
                },
                "progress": {
                    "current": progress[0],
                    "total": progress[1]
                }
            }
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"ok": False, "error": str(e)}
    
    def answer_study_card(self, deck_name: str, card_id: str, rating: int):
        """Answer a card in study session (1=Again, 2=Hard, 3=Good, 4=Easy)"""
        try:
            decks = self._akson_store.get_decks()
            deck = None
            for d in decks.values():
                if d.name == deck_name:
                    deck = d
                    break
            
            if not deck:
                return {"ok": False, "error": "Deck not found"}
            
            session = self._study_sessions.get(deck.id)
            if not session:
                return {"ok": False, "error": "No active session"}
            
            # Answer current card
            result = session.answer_card(rating)
            
            if not result:
                # Session complete
                stats = session.get_stats()
                del self._study_sessions[deck.id]
                return {
                    "ok": True,
                    "complete": True,
                    "stats": stats
                }
            
            # Get next card
            card, note = result
            progress = session.get_progress()
            
            return {
                "ok": True,
                "complete": False,
                "card": {
                    "id": card.id,
                    "front": note.fields.get("Front", ""),
                    "back": note.fields.get("Back", ""),
                    "state": card.state
                },
                "progress": {
                    "current": progress[0],
                    "total": progress[1]
                },
                "stats": session.get_stats()
            }
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {"ok": False, "error": str(e)}

    def download_summary(self, filename: str, data: dict):
        """Download summaries as a formatted text document."""
        try:
            downloads_dir = Path.home() / "Downloads"
            safe_filename = self._sanitize_library_name(filename)
            output_path = downloads_dir / f"{safe_filename}_summary.txt"

            lines: list[str] = []
            lines.append("=" * 80)
            lines.append(f"SUMMARY: {filename}")
            lines.append("=" * 80 + "\n")

            summaries = data.get('summaries', {}) or {}
            if summaries:
                pages = sorted(int(p) for p in summaries.keys())
                for page in pages:
                    summary_data = summaries.get(str(page), summaries.get(page))
                    summary_text = summary_data if isinstance(summary_data, str) else (summary_data or {}).get('summary', '')
                    lines.append("\n" + "─" * 80)
                    lines.append(f"PAGE {page}")
                    lines.append("─" * 80 + "\n")
                    lines.append(summary_text)
                    lines.append("")
            else:
                lines.append("\nNo summaries available.\n")

            with open(output_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))

            print(f"📄 Summary downloaded to: {output_path}")
            return {"ok": True, "path": str(output_path)}
        except Exception as e:
            print(f"Error downloading summary: {e}")
            return {"ok": False, "error": str(e)}

    def download_flashcards_anki(self, filename: str, data: dict):
        """Download flashcards as Anki-compatible TSV (tab-separated)."""
        try:
            import csv
            safe_filename = self._sanitize_library_name(filename)
            default_filename = f"{safe_filename}_flashcards_anki.tsv"
            
            # Use file dialog to get save path
            save_path = self.window.create_file_dialog(
                webview.SAVE_DIALOG,
                directory=str(Path.home() / "Downloads"),
                allow_multiple=False,
                save_filename=default_filename,
                file_types=("TSV Files (*.tsv)", "Text Files (*.txt)", "All Files (*.*)")
            )
            
            if not save_path:
                return {"ok": False, "error": "Save cancelled by user"}

            flashcards = data.get('flashcards', {}) or {}

            with open(save_path, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f, delimiter='\t')
                if flashcards:
                    pages = sorted(int(p) for p in flashcards.keys())
                    for page in pages:
                        cards = flashcards.get(str(page), flashcards.get(page))
                        if isinstance(cards, list):
                            for card in cards:
                                question = (card.get('q', '') if isinstance(card, dict) else '').replace('\n', '<br>')
                                answer = (card.get('a', '') if isinstance(card, dict) else '').replace('\n', '<br>')
                                tag = f"{safe_filename}_page{page}"
                                writer.writerow([question, answer, tag])

            print(f"🗂️ Flashcards downloaded to: {save_path}")
            return {"ok": True, "path": str(save_path)}
        except Exception as e:
            print(f"Error downloading flashcards: {e}")
            return {"ok": False, "error": str(e)}


def main():
    # Detect macOS system theme before creating webview
    initial_theme = 'light'  # Default to light (more common)
    if sys.platform == "darwin":
        try:
            import subprocess
            result = subprocess.run(
                ['defaults', 'read', '-g', 'AppleInterfaceStyle'],
                capture_output=True,
                text=True,
                timeout=1
            )
            # If command succeeds and returns "Dark", system is in dark mode
            # If it fails (returns non-zero), system is in light mode
            initial_theme = 'dark' if result.returncode == 0 and 'Dark' in result.stdout else 'light'
            print(f"🌓 Detected macOS system theme: {initial_theme}")
        except Exception as e:
            print(f"⚠️  Could not detect system theme, defaulting to light: {e}")
    
    # Ensure PDF.js is downloaded and patched
    ensure_dirs()
    if not (PDFJS_DIR / "web" / "viewer.html").exists() or not (PDFJS_DIR / "build" / "pdf.js").exists():
        print("PDF.js not found, downloading...")
        download_pdfjs_if_needed()
    else:
        # Patch existing viewer.html and viewer.css with detected theme
        patch_viewer_html(initial_theme)
        patch_viewer_css()
    ensure_dirs()
    
    # Create splash page for instant loading
    ensure_splash_created()
    
    # Always create wrapper to ensure latest UI updates
    ensure_wrapper_created()
    
    # Start PDF.js download in background (non-blocking)
    download_pdfjs_async()
    
    port = choose_free_port()
    server = LocalServer(port)
    server.start()

    # Start with splash page for instant loading
    start_url = f"http://127.0.0.1:{port}/splash.html"
    api = AppAPI(None, port)
    win = webview.create_window(APP_TITLE, start_url, width=1280, height=900, js_api=api)
    api.window = win
    
    # Set app icon on macOS
    # Note: When running as a Python script (python3 slides_working.py), the dock icon
    # is controlled by the Python interpreter. To get a custom icon, package the app
    # as a .app bundle using PyInstaller (see packaging/build_mac.sh).
    # However, we can try to set it programmatically using PyObjC if available.
    if sys.platform == "darwin":
        try:
            icon_path = Path(__file__).parent / "icons" / "akson.png"
            if icon_path.exists():
                try:
                    # Try using PyObjC to set the dock icon (may not work when running as script)
                    import AppKit
                    from AppKit import NSImage, NSApplication
                    app = NSApplication.sharedApplication()
                    img = NSImage.alloc().initWithContentsOfFile_(str(icon_path))
                    if img:
                        app.setApplicationIconImage_(img)
                        print("✅ Set app icon (may only work when packaged as .app)")
                except ImportError:
                    # PyObjC not available - icon will only show when packaged
                    pass
                except Exception as e:
                    # Setting icon failed - this is expected when running as script
                    pass
        except Exception as e:
            pass  # Silently fail - icon setting is optional
    
    # Inject initial theme into webview after it loads
    def inject_initial_theme():
        """Inject initial theme to sync sidebar and PDF viewer on startup."""
        try:
            js_code = f"""
            (function() {{
                // Set initial theme immediately
                const initialTheme = '{initial_theme}';
                console.log('🌓 Injecting initial theme:', initialTheme);
                
                // Send to sidebar
                if (window.applyTheme) {{
                    window.applyTheme(initialTheme);
                }}
                
                // Send to PDF viewer iframe
                const pdfIframe = document.querySelector('iframe[src*="viewer.html"]');
                if (pdfIframe && pdfIframe.contentWindow) {{
                    pdfIframe.contentWindow.postMessage({{
                        type: 'theme-changed',
                        theme: initialTheme,
                        pdfPageTheme: false
                    }}, '*');
                }}
                
                // Also set it globally for immediate access
                window.initialSystemTheme = initialTheme;
                
                // Override matchMedia if it exists
                if (window.updateMatchMediaTheme) {{
                    window.updateMatchMediaTheme(initialTheme);
                }}
            }})();
            """
            win.evaluate_js(js_code)
        except Exception as e:
            print(f"⚠️  Error injecting initial theme: {e}")
    
    # Inject theme after a short delay to ensure webview is ready
    import threading
    def delayed_theme_injection():
        import time
        time.sleep(1)  # Wait for webview to load
        inject_initial_theme()
    
    threading.Thread(target=delayed_theme_injection, daemon=True).start()

    enable_selection_js(win)

    if len(sys.argv) > 1:
        pdf_to_open = Path(sys.argv[1])
        def on_shown():
            api.load_pdf(pdf_to_open)
        webview.start(func=on_shown)
    else:
        webview.start()


if __name__ == '__main__':
    main()


