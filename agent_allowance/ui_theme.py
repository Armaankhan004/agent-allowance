"""
UI Theme and Design System for Agent Allowance.

Presentation only: design tokens, global CSS, and small HTML render helpers
(status pills, header, metric cards, budget bar, live agent steps, decision card).
No policy, agent, database, or PayPal logic lives here.

Theming strategy (Streamlit 1.65):
  * `.streamlit/config.toml` sets a LIGHT global theme and scopes the navy palette
    to the sidebar via `[theme.sidebar]`. The old config set
    `secondaryBackgroundColor = "#0F172A"`, which Streamlit also uses as the fill
    color for inputs/selects/chat input, producing black boxes with dark text.
  * This module adds the finer details (cards, pills, transitions) as CSS that
    reads every color from the TOKENS below via CSS custom properties.

Dollar signs: Streamlit markdown renders `$...$` as LaTeX. Use `md_escape()` for
markdown strings and `html_text()` / `money_html()` inside HTML blocks.
"""

import html
from typing import Any, Dict, Iterable, List, Optional

import streamlit as st

from agent_allowance.config import Config
from agent_allowance.db import get_dashboard_metrics


# ==============================================================================
# Design tokens
# ==============================================================================

TOKENS: Dict[str, str] = {
    # Surfaces & text
    "bg": "#F7F8FA",
    "surface": "#FFFFFF",
    "surface-muted": "#F8FAFC",
    "border": "#E2E8F0",
    "input-border": "#CBD5E1",
    "text": "#0F172A",
    "text-muted": "#64748B",
    "text-soft": "#334155",
    "placeholder": "#94A3B8",
    # Brand
    "primary": "#2563EB",
    "primary-hover": "#1D4ED8",
    "primary-soft": "#EFF6FF",
    "focus-ring": "rgba(37, 99, 235, 0.25)",
    # Sidebar
    "sidebar-bg": "#0F172A",
    "sidebar-surface": "#1E293B",
    "sidebar-surface-hover": "#334155",
    "sidebar-border": "#334155",
    "sidebar-text": "#E2E8F0",
    "sidebar-muted": "#94A3B8",
    "sidebar-accent": "#60A5FA",
    # Status pills (text on background; all >= 4.5:1, WCAG AA)
    "approved-fg": "#15803D",
    "approved-bg": "#DCFCE7",
    "pending-fg": "#B45309",
    "pending-bg": "#FEF3C7",
    "blocked-fg": "#B91C1C",
    "blocked-bg": "#FEE2E2",
    "rejected-fg": "#475569",
    "rejected-bg": "#E2E8F0",
    "executed-fg": "#1D4ED8",
    "executed-bg": "#DBEAFE",
    # Budget bar
    "bar-track": "#E2E8F0",
    "bar-ok": "#2563EB",
    "bar-warn": "#D97706",
    "bar-danger": "#DC2626",
    # Shape & motion
    "radius-card": "12px",
    "radius-input": "10px",
    "radius-pill": "9999px",
    "shadow-sm": "0 1px 2px rgba(15, 23, 42, 0.04), 0 1px 3px rgba(15, 23, 42, 0.06)",
    "shadow-md": "0 4px 12px rgba(15, 23, 42, 0.08), 0 2px 4px rgba(15, 23, 42, 0.04)",
    "font": "'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
    "ease": "cubic-bezier(0.2, 0.8, 0.2, 1)",
    "t-fast": "150ms",
    "t-page": "250ms",
}

# Budget bar thresholds (fraction of monthly budget used)
BAR_WARN_AT = 0.75
BAR_DANGER_AT = 0.90

STATUS_META: Dict[str, Dict[str, str]] = {
    "APPROVED": {"label": "Approved", "cls": "approved"},
    "NEEDS_APPROVAL": {"label": "Needs approval", "cls": "pending"},
    "BLOCKED": {"label": "Blocked", "cls": "blocked"},
    "REJECTED": {"label": "Rejected", "cls": "rejected"},
    "EXECUTED": {"label": "Executed", "cls": "executed"},
}


def _root_vars() -> str:
    return "\n".join(f"    --aa-{k}: {v};" for k, v in TOKENS.items())


# ==============================================================================
# Global CSS (all colors via var(--aa-*))
# ==============================================================================

_CSS_BODY = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

html, body, .stApp, .stApp button, .stApp input, .stApp textarea, .stApp select {
    font-family: var(--aa-font);
}
.stApp { background: var(--aa-bg); color: var(--aa-text); }

/* ---------- Chrome: hide Deploy, footer, decoration ---------- */
[data-testid="stAppDeployButton"], [data-testid="stDecoration"],
#MainMenu, footer, [data-testid="stStatusWidget"] { display: none !important; }
header[data-testid="stHeader"] { background: transparent; }
[data-testid="stMainBlockContainer"] { padding-top: 2.25rem; max-width: 1180px; }

/* ---------- Page transition ---------- */
@keyframes aa-fade-up {
    from { opacity: 0; transform: translateY(8px); }
    to   { opacity: 1; transform: translateY(0); }
}
[class*="st-key-aa-page-"] { animation: aa-fade-up var(--aa-t-page) var(--aa-ease) both; }

/* ---------- Typography helpers ---------- */
.aa-section { margin: 0.25rem 0 1rem 0; }
.aa-section h2 {
    font-size: 1.3rem; font-weight: 700; color: var(--aa-text);
    letter-spacing: -0.01em; margin: 0; padding: 0;
}
.aa-section p { color: var(--aa-text-muted); font-size: 0.92rem; margin: 0.25rem 0 0 0; }
.aa-muted { color: var(--aa-text-muted); }
.aa-label {
    font-size: 0.72rem; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.05em; color: var(--aa-text-muted);
}

/* ---------- Header ---------- */
.aa-header {
    display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem;
    padding-bottom: 1.1rem; margin-bottom: 1.25rem; border-bottom: 1px solid var(--aa-border);
}
.aa-header h1 {
    font-size: 1.85rem; font-weight: 700; letter-spacing: -0.025em;
    color: var(--aa-text); margin: 0; padding: 0; line-height: 1.15;
}
.aa-header .aa-tagline { color: var(--aa-text-muted); font-size: 0.98rem; margin: 0.3rem 0 0 0; }

.aa-mode-pill {
    display: inline-flex; align-items: center; gap: 0.45rem; white-space: nowrap;
    padding: 0.38rem 0.85rem; border-radius: var(--aa-radius-pill);
    font-size: 0.8rem; font-weight: 600; border: 1px solid var(--aa-border);
    background: var(--aa-surface); color: var(--aa-text-soft); box-shadow: var(--aa-shadow-sm);
}
.aa-mode-pill .aa-dot { width: 8px; height: 8px; border-radius: 50%; background: #94A3B8; }
.aa-mode-pill.gemini { color: #1E40AF; border-color: #BFDBFE; background: var(--aa-primary-soft); }
.aa-mode-pill.gemini .aa-dot { background: var(--aa-primary); }

/* ---------- Metric cards ---------- */
.aa-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0.9rem; }
@media (max-width: 900px) { .aa-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.aa-metric {
    background: var(--aa-surface); border: 1px solid var(--aa-border);
    border-radius: var(--aa-radius-card); padding: 1rem 1.1rem; box-shadow: var(--aa-shadow-sm);
    transition: transform var(--aa-t-fast) var(--aa-ease), box-shadow var(--aa-t-fast) var(--aa-ease),
                border-color var(--aa-t-fast) var(--aa-ease);
}
.aa-metric:hover { transform: translateY(-2px); box-shadow: var(--aa-shadow-md); }
.aa-metric .aa-value {
    font-size: 1.55rem; font-weight: 700; letter-spacing: -0.02em; color: var(--aa-text);
    margin-top: 0.3rem; line-height: 1.2; transition: color var(--aa-t-fast) ease;
}
.aa-metric .aa-sub { font-size: 0.8rem; color: var(--aa-text-muted); margin-top: 0.3rem; }
.aa-metric.attention { border-color: #FCD34D; }
.aa-metric.attention .aa-value { color: var(--aa-pending-fg); }

/* ---------- Budget bar ---------- */
.aa-budget {
    background: var(--aa-surface); border: 1px solid var(--aa-border); border-radius: var(--aa-radius-card);
    padding: 0.85rem 1.1rem; margin: 0.9rem 0 1.6rem 0; box-shadow: var(--aa-shadow-sm);
}
.aa-budget-row { display: flex; justify-content: space-between; align-items: baseline; gap: 1rem; margin-bottom: 0.55rem; }
.aa-budget-row .aa-budget-text { font-size: 0.88rem; color: var(--aa-text-soft); }
.aa-budget-row .aa-budget-text strong { color: var(--aa-text); }
.aa-budget-row .aa-budget-pct { font-size: 0.85rem; font-weight: 700; }
.aa-bar-track { height: 10px; background: var(--aa-bar-track); border-radius: var(--aa-radius-pill); overflow: hidden; }
@keyframes aa-bar-grow { from { width: var(--aa-bar-from); } }
.aa-bar-fill {
    height: 100%; border-radius: var(--aa-radius-pill);
    animation: aa-bar-grow 700ms var(--aa-ease) both;
    transition: width 700ms var(--aa-ease), background-color 300ms ease;
}
.aa-bar-fill.ok { background: var(--aa-bar-ok); }
.aa-bar-fill.warn { background: var(--aa-bar-warn); }
.aa-bar-fill.danger { background: var(--aa-bar-danger); }
.aa-budget-pct.ok { color: var(--aa-bar-ok); }
.aa-budget-pct.warn { color: #B45309; }
.aa-budget-pct.danger { color: var(--aa-blocked-fg); }

/* ---------- Status pills ---------- */
.aa-pill {
    display: inline-flex; align-items: center; gap: 0.38rem; white-space: nowrap;
    padding: 0.22rem 0.65rem; border-radius: var(--aa-radius-pill);
    font-size: 0.74rem; font-weight: 600; letter-spacing: 0.02em; line-height: 1.4;
    transition: background-color var(--aa-t-fast) ease, color var(--aa-t-fast) ease;
}
.aa-pill::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.aa-pill.approved { color: var(--aa-approved-fg); background: var(--aa-approved-bg); }
.aa-pill.pending  { color: var(--aa-pending-fg);  background: var(--aa-pending-bg); }
.aa-pill.blocked  { color: var(--aa-blocked-fg);  background: var(--aa-blocked-bg); }
.aa-pill.rejected { color: var(--aa-rejected-fg); background: var(--aa-rejected-bg); }
.aa-pill.executed { color: var(--aa-executed-fg); background: var(--aa-executed-bg); }

/* ---------- Cards (bordered st.container with key "aa-card-*") ---------- */
[class*="st-key-aa-card"] {
    background: var(--aa-surface) !important; border: 1px solid var(--aa-border) !important;
    border-radius: var(--aa-radius-card) !important; box-shadow: var(--aa-shadow-sm);
    padding: 1.1rem 1.2rem !important;
    transition: transform var(--aa-t-fast) var(--aa-ease), box-shadow var(--aa-t-fast) var(--aa-ease);
}
[class*="st-key-aa-card"]:hover { transform: translateY(-2px); box-shadow: var(--aa-shadow-md); }
[class*="st-key-aa-card-static"]:hover { transform: none; box-shadow: var(--aa-shadow-sm); }
[class*="st-key-aa-card-decision-approved"], [class*="st-key-aa-card-pending"] { border-left-width: 4px !important; }
[class*="st-key-aa-card-decision-approved"] { border-left: 4px solid var(--aa-approved-fg) !important; }
[class*="st-key-aa-card-decision-pending"]  { border-left: 4px solid #F59E0B !important; }
[class*="st-key-aa-card-decision-blocked"]  { border-left: 4px solid var(--aa-blocked-fg) !important; }
[class*="st-key-aa-card-decision-none"]     { border-left: 4px solid #94A3B8 !important; }
[class*="st-key-aa-card-pending"]           { border-left: 4px solid #F59E0B !important; }

.aa-card-title { font-size: 1rem; font-weight: 700; color: var(--aa-text); margin: 0; }
.stApp p.aa-card-desc { font-size: 0.86rem !important; color: var(--aa-text-muted); margin: 0.25rem 0 0.75rem 0; line-height: 1.45; }
.stApp p.aa-reason, .stApp .aa-reasons li { font-size: 0.88rem !important; }
.stApp .aa-header p.aa-tagline, .stApp .aa-section p, .stApp .aa-empty p { font-size: 0.92rem !important; }
.aa-card-head { display: flex; justify-content: space-between; align-items: center; gap: 0.75rem; }
.aa-icon-chip {
    display: inline-flex; align-items: center; justify-content: center; width: 36px; height: 36px;
    border-radius: 10px; background: var(--aa-primary-soft); color: var(--aa-primary);
    font-size: 1.05rem; font-weight: 700; margin-bottom: 0.6rem;
}
.aa-msym {
    font-family: 'Material Symbols Rounded'; font-weight: normal; font-style: normal; font-size: 20px;
    line-height: 1; letter-spacing: normal; text-transform: none; white-space: nowrap;
    -webkit-font-feature-settings: 'liga'; font-feature-settings: 'liga'; -webkit-font-smoothing: antialiased;
}
.aa-empty .aa-msym { font-size: 26px; }
/* Equal-height scenario cards with buttons pinned to the bottom */
[data-testid="stHorizontalBlock"]:has([class*="st-key-aa-card-scn"]) > [data-testid="stColumn"] > div { height: 100%; }
[class*="st-key-aa-card-scn"] { height: 100%; justify-content: space-between; }
[class*="st-key-aa-card-scn"] [data-testid="stElementContainer"]:has(button) { margin-top: auto; }

/* ---------- How it works strip ---------- */
.aa-how {
    display: grid; grid-template-columns: 1fr auto 1fr auto 1fr; align-items: center; gap: 0.75rem;
    background: var(--aa-surface); border: 1px solid var(--aa-border); border-radius: var(--aa-radius-card);
    padding: 1.1rem 1.3rem; margin-bottom: 1.25rem; box-shadow: var(--aa-shadow-sm);
}
@media (max-width: 900px) { .aa-how { grid-template-columns: 1fr; } .aa-how .aa-arrow { display: none; } }
.aa-how .aa-step { display: flex; gap: 0.75rem; align-items: flex-start; }
.aa-how .aa-step-num {
    flex: 0 0 28px; height: 28px; border-radius: 50%; background: var(--aa-primary-soft);
    color: var(--aa-primary); font-weight: 700; font-size: 0.82rem;
    display: flex; align-items: center; justify-content: center;
}
.aa-how .aa-step-title { font-weight: 700; font-size: 0.93rem; color: var(--aa-text); }
.aa-how .aa-step-desc { font-size: 0.8rem; color: var(--aa-text-muted); line-height: 1.4; margin-top: 0.1rem; }
.aa-how .aa-arrow { color: #94A3B8; font-size: 1.1rem; }

/* ---------- Live agent steps ---------- */
.aa-steps { display: flex; flex-direction: column; gap: 0.45rem; margin: 0.25rem 0 0.75rem 0; }
.aa-steprow {
    display: flex; align-items: center; gap: 0.6rem; font-size: 0.9rem; color: var(--aa-text-soft);
    transition: color var(--aa-t-fast) ease, opacity var(--aa-t-fast) ease;
}
.aa-steprow .aa-ico {
    width: 20px; height: 20px; border-radius: 50%; flex: 0 0 20px;
    display: inline-flex; align-items: center; justify-content: center; font-size: 0.7rem; font-weight: 700;
}
.aa-steprow.pending { color: var(--aa-placeholder); }
.aa-steprow.pending .aa-ico { border: 2px solid var(--aa-border); }
.aa-steprow.active { color: var(--aa-text); font-weight: 600; }
.aa-steprow.active .aa-ico {
    border: 2px solid var(--aa-primary-soft); border-top-color: var(--aa-primary);
    animation: aa-spin 0.8s linear infinite;
}
.aa-steprow.done .aa-ico { background: var(--aa-primary-soft); color: var(--aa-primary); }
.aa-steprow.skipped { color: var(--aa-placeholder); text-decoration: line-through; }
.aa-steprow.skipped .aa-ico { background: var(--aa-border); color: var(--aa-text-muted); }
.aa-steprow .aa-detail { color: var(--aa-text-muted); font-weight: 400; }
@keyframes aa-spin { to { transform: rotate(360deg); } }

/* ---------- Outcome banner ---------- */
.aa-outcome { display: inline-flex; align-items: center; gap: 0.5rem; font-weight: 700; font-size: 0.95rem; margin-bottom: 0.5rem; }
.aa-outcome .aa-oico {
    width: 22px; height: 22px; border-radius: 50%; display: inline-flex; align-items: center;
    justify-content: center; font-size: 0.75rem; color: #FFFFFF;
}
.aa-outcome.approved { color: var(--aa-approved-fg); } .aa-outcome.approved .aa-oico { background: var(--aa-approved-fg); }
.aa-outcome.pending  { color: var(--aa-pending-fg); }  .aa-outcome.pending .aa-oico  { background: #F59E0B; }
.aa-outcome.blocked  { color: var(--aa-blocked-fg); }  .aa-outcome.blocked .aa-oico  { background: var(--aa-blocked-fg); }
.aa-outcome.none     { color: var(--aa-rejected-fg); } .aa-outcome.none .aa-oico     { background: #94A3B8; }
.aa-outcome.error    { color: var(--aa-blocked-fg); }  .aa-outcome.error .aa-oico    { background: var(--aa-blocked-fg); }

/* ---------- Decision / detail content ---------- */
.aa-kv { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0.75rem; margin: 0.85rem 0; }
@media (max-width: 700px) { .aa-kv { grid-template-columns: 1fr; } }
.aa-kv .aa-v { font-size: 0.98rem; font-weight: 600; color: var(--aa-text); margin-top: 0.15rem; overflow-wrap: anywhere; }
.aa-kv .aa-v.big { font-size: 1.2rem; font-weight: 700; }
.aa-reason { font-size: 0.88rem; color: var(--aa-text-soft); line-height: 1.5; margin: 0 0 0.6rem 0; overflow-wrap: anywhere; }
.aa-reasons { margin: 0.35rem 0 0.75rem 0; padding-left: 1.1rem; }
.aa-reasons li { font-size: 0.88rem; color: var(--aa-text-soft); line-height: 1.5; margin: 0.15rem 0; overflow-wrap: anywhere; }
.aa-next {
    display: flex; gap: 0.5rem; align-items: flex-start; background: var(--aa-surface-muted);
    border: 1px solid var(--aa-border); border-radius: var(--aa-radius-input);
    padding: 0.65rem 0.85rem; font-size: 0.87rem; color: var(--aa-text-soft); margin-bottom: 0.75rem;
}
.aa-next strong { color: var(--aa-text); white-space: nowrap; }
.aa-mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.82rem; overflow-wrap: anywhere; }

/* ---------- Empty state ---------- */
.aa-empty {
    text-align: center; background: var(--aa-surface); border: 1px dashed var(--aa-input-border);
    border-radius: var(--aa-radius-card); padding: 2.5rem 1.5rem; margin-bottom: 1rem;
}
.aa-empty .aa-empty-icon {
    width: 52px; height: 52px; margin: 0 auto 0.75rem auto; border-radius: 50%;
    background: var(--aa-approved-bg); color: var(--aa-approved-fg); font-size: 1.5rem;
    display: flex; align-items: center; justify-content: center;
}
.aa-empty h3 { font-size: 1.1rem; font-weight: 700; color: var(--aa-text); margin: 0; padding: 0; }
.aa-empty p { color: var(--aa-text-muted); font-size: 0.9rem; margin: 0.35rem 0 0 0; }

/* ---------- Merchant chips / inline validation ---------- */
.aa-chips { display: flex; flex-wrap: wrap; gap: 0.4rem; margin: 0.35rem 0 0.25rem 0; }
.aa-chip {
    background: var(--aa-primary-soft); color: #1E40AF; border: 1px solid #BFDBFE;
    border-radius: var(--aa-radius-pill); padding: 0.18rem 0.6rem; font-size: 0.78rem; font-weight: 500;
}
.aa-field-error { color: var(--aa-blocked-fg); font-size: 0.8rem; margin: -0.4rem 0 0.6rem 0; }
.aa-field-hint  { color: var(--aa-pending-fg); font-size: 0.8rem; margin: -0.2rem 0 0.6rem 0; }
.aa-dirty { color: var(--aa-pending-fg); font-size: 0.85rem; font-weight: 600; }


/* ---------- Inputs (main area) ---------- */
.stApp [data-baseweb="input"], .stApp [data-baseweb="base-input"], .stApp [data-baseweb="textarea"],
.stApp [data-baseweb="select"] > div {
    background-color: var(--aa-surface) !important; border-radius: var(--aa-radius-input) !important;
}
[data-testid="stMain"] [data-baseweb="input"], [data-testid="stMain"] [data-baseweb="textarea"],
[data-testid="stMain"] [data-baseweb="select"] > div,
div[role="dialog"] [data-baseweb="input"], div[role="dialog"] [data-baseweb="textarea"] {
    border: 1px solid var(--aa-input-border) !important;
    transition: border-color var(--aa-t-fast) ease, box-shadow var(--aa-t-fast) ease;
}
[data-testid="stMain"] [data-baseweb="input"]:focus-within,
[data-testid="stMain"] [data-baseweb="textarea"]:focus-within,
[data-testid="stMain"] [data-baseweb="select"] > div:focus-within {
    border-color: var(--aa-primary) !important; box-shadow: 0 0 0 3px var(--aa-focus-ring) !important;
}
[data-testid="stMain"] input, [data-testid="stMain"] textarea {
    color: var(--aa-text) !important; -webkit-text-fill-color: var(--aa-text) !important;
    background-color: var(--aa-surface) !important; caret-color: var(--aa-primary);
}
[data-testid="stMain"] input::placeholder, [data-testid="stMain"] textarea::placeholder,
[data-testid="stBottom"] textarea::placeholder {
    color: var(--aa-placeholder) !important; -webkit-text-fill-color: var(--aa-placeholder) !important; opacity: 1;
}
[data-testid="stMain"] [data-testid="stNumberInputContainer"] button {
    background: var(--aa-surface-muted) !important; color: var(--aa-text-soft) !important;
}
[data-testid="stMain"] [data-testid="stNumberInputContainer"] button:hover { background: var(--aa-border) !important; }
[data-testid="stWidgetLabel"] p { color: var(--aa-text); font-weight: 600; font-size: 0.88rem; }

/* Chat input (lives in stBottom) */
[data-testid="stBottom"] > div, [data-testid="stBottomBlockContainer"] { background: var(--aa-bg) !important; }
[data-testid="stChatInput"] {
    background: var(--aa-surface) !important; border: 1px solid var(--aa-input-border) !important;
    border-radius: var(--aa-radius-card) !important; box-shadow: var(--aa-shadow-sm);
    transition: border-color var(--aa-t-fast) ease, box-shadow var(--aa-t-fast) ease;
}
[data-testid="stChatInput"]:focus-within {
    border-color: var(--aa-primary) !important; box-shadow: 0 0 0 3px var(--aa-focus-ring) !important;
}
[data-testid="stChatInput"] > div, [data-testid="stChatInput"] [data-baseweb="textarea"],
[data-testid="stChatInput"] [data-baseweb="base-input"] { background: transparent !important; border: none !important; }
[data-testid="stChatInput"] textarea {
    color: var(--aa-text) !important; -webkit-text-fill-color: var(--aa-text) !important; background: transparent !important;
}
[data-testid="stChatInputSubmitButton"] { color: var(--aa-primary) !important; }

/* ---------- Buttons (main area + dialogs) ---------- */
.stApp button[kind], .stApp a[data-testid^="stBaseLinkButton"] {
    border-radius: var(--aa-radius-input) !important; font-weight: 600 !important;
    transition: background-color var(--aa-t-fast) ease, border-color var(--aa-t-fast) ease,
                color var(--aa-t-fast) ease, transform 100ms ease, box-shadow var(--aa-t-fast) ease !important;
}
.stApp button[kind]:active, .stApp a[data-testid^="stBaseLinkButton"]:active { transform: scale(0.98); }
[data-testid="stMain"] button[kind^="primary"], div[role="dialog"] button[kind^="primary"],
[data-testid="stMain"] a[data-testid="stBaseLinkButton-primary"] {
    background: var(--aa-primary) !important; border: 1px solid var(--aa-primary) !important; color: #FFFFFF !important;
    box-shadow: 0 1px 2px rgba(37, 99, 235, 0.25);
}
[data-testid="stMain"] button[kind^="primary"]:hover, div[role="dialog"] button[kind^="primary"]:hover,
[data-testid="stMain"] a[data-testid="stBaseLinkButton-primary"]:hover {
    background: var(--aa-primary-hover) !important; border-color: var(--aa-primary-hover) !important;
    box-shadow: 0 4px 10px rgba(37, 99, 235, 0.25);
}
[data-testid="stMain"] button[kind^="primary"] p, [data-testid="stMain"] a[data-testid="stBaseLinkButton-primary"] p,
div[role="dialog"] button[kind^="primary"] p { color: #FFFFFF !important; }
[data-testid="stMain"] button[kind^="secondary"], div[role="dialog"] button[kind^="secondary"],
[data-testid="stMain"] a[data-testid="stBaseLinkButton-secondary"] {
    background: var(--aa-surface) !important; border: 1px solid var(--aa-input-border) !important; color: var(--aa-text) !important;
}
[data-testid="stMain"] button[kind^="secondary"]:hover, div[role="dialog"] button[kind^="secondary"]:hover {
    border-color: var(--aa-primary) !important; color: var(--aa-primary) !important; background: var(--aa-primary-soft) !important;
}
[data-testid="stMain"] button[kind^="secondary"]:hover p, div[role="dialog"] button[kind^="secondary"]:hover p { color: var(--aa-primary) !important; }
[data-testid="stMain"] button[kind]:focus-visible { box-shadow: 0 0 0 3px var(--aa-focus-ring) !important; }
button.aa-danger, .st-key-aa-confirm-reset button {
    background: var(--aa-blocked-fg) !important; border-color: var(--aa-blocked-fg) !important; color: #FFFFFF !important;
}
.st-key-aa-confirm-reset button p { color: #FFFFFF !important; }
.st-key-aa-confirm-reset button:hover { background: #991B1B !important; border-color: #991B1B !important; }

/* Page links styled as subtle buttons */
[data-testid="stMain"] [data-testid="stPageLink"] a {
    border: 1px solid var(--aa-input-border); border-radius: var(--aa-radius-input); background: var(--aa-surface);
    padding: 0.35rem 0.8rem; transition: border-color var(--aa-t-fast) ease, background-color var(--aa-t-fast) ease;
}
[data-testid="stMain"] [data-testid="stPageLink"] a:hover { border-color: var(--aa-primary); background: var(--aa-primary-soft); }
[data-testid="stMain"] [data-testid="stPageLink"] a p { color: var(--aa-primary) !important; font-weight: 600; }

/* ---------- Chat messages ---------- */
[data-testid="stChatMessage"] { background: transparent; padding: 0.5rem 0; }
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] p { color: var(--aa-text); line-height: 1.6; }
[data-testid="stChatMessageAvatarUser"] { background: #E2E8F0 !important; color: var(--aa-text-soft) !important; }
[data-testid="stChatMessageAvatarAssistant"] { background: var(--aa-primary) !important; color: #FFFFFF !important; }

/* Inline code in main area: never a dark chip */
[data-testid="stMain"] code { background: #F1F5F9 !important; color: var(--aa-text) !important; border-radius: 6px; }

/* Toasts & dialogs */
[data-testid="stToast"] {
    background: var(--aa-surface) !important; color: var(--aa-text) !important;
    border: 1px solid var(--aa-border); box-shadow: var(--aa-shadow-md); border-radius: var(--aa-radius-card) !important;
}
[data-testid="stToast"] p { color: var(--aa-text) !important; }
div[role="dialog"] { border-radius: var(--aa-radius-card) !important; }

/* ---------- Sidebar ---------- */
section[data-testid="stSidebar"] { background: var(--aa-sidebar-bg) !important; border-right: 1px solid #1E293B; }
section[data-testid="stSidebar"] [data-testid="stSidebarHeader"] { padding-top: 1.25rem; padding-bottom: 0.5rem; }
section[data-testid="stSidebar"] [data-testid="stSidebarHeader"] img { max-width: 100%; }
section[data-testid="stSidebar"] p, section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] label, section[data-testid="stSidebar"] summary { color: var(--aa-sidebar-text); }
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"],
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p { color: var(--aa-sidebar-muted) !important; }
section[data-testid="stSidebar"] hr { border-color: #1E293B !important; margin: 0.75rem 0; }
section[data-testid="stSidebar"] [data-testid="stSidebarNavSeparator"] { border-color: #1E293B; }

/* Nav links */
section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"] {
    position: relative; border-radius: 8px; margin: 1px 0; padding-left: 0.85rem;
    transition: background-color var(--aa-t-fast) ease, color var(--aa-t-fast) ease;
}
section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"] span { color: #CBD5E1 !important; font-weight: 500; }
section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"]:hover { background: rgba(148, 163, 184, 0.12) !important; }
section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"]:hover span { color: #FFFFFF !important; }
section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"][aria-current="page"] {
    background: rgba(96, 165, 250, 0.16) !important;
}
section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"][aria-current="page"] span { color: #FFFFFF !important; font-weight: 600; }
section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"][aria-current="page"]::before {
    content: ""; position: absolute; left: 0; top: 7px; bottom: 7px; width: 3px;
    border-radius: 3px; background: var(--aa-sidebar-accent);
}

/* Sidebar expander (Demo tools) */
section[data-testid="stSidebar"] [data-testid="stExpander"] details {
    background: rgba(30, 41, 59, 0.6) !important; border: 1px solid var(--aa-sidebar-border) !important;
    border-radius: var(--aa-radius-input) !important;
}
section[data-testid="stSidebar"] [data-testid="stExpander"] summary { color: var(--aa-sidebar-text) !important; }
section[data-testid="stSidebar"] [data-testid="stExpander"] summary:hover { background: rgba(148, 163, 184, 0.10) !important; }
section[data-testid="stSidebar"] [data-testid="stExpander"] summary p { font-weight: 600; color: var(--aa-sidebar-text) !important; }
section[data-testid="stSidebar"] [data-testid="stExpander"] summary svg { fill: var(--aa-sidebar-muted); color: var(--aa-sidebar-muted); }

/* Sidebar buttons: always readable */
section[data-testid="stSidebar"] button[kind] {
    background: var(--aa-sidebar-surface) !important; border: 1px solid #475569 !important;
    color: #F8FAFC !important; border-radius: var(--aa-radius-input) !important;
    transition: background-color var(--aa-t-fast) ease, border-color var(--aa-t-fast) ease, transform 100ms ease !important;
}
section[data-testid="stSidebar"] button[kind] p { color: #F8FAFC !important; font-weight: 600; }
section[data-testid="stSidebar"] button[kind]:hover {
    background: var(--aa-sidebar-surface-hover) !important; border-color: var(--aa-sidebar-accent) !important;
}
section[data-testid="stSidebar"] button[kind]:active { transform: scale(0.98); }
section[data-testid="stSidebar"] button[kind]:focus-visible { box-shadow: 0 0 0 3px rgba(96, 165, 250, 0.4) !important; }
section[data-testid="stSidebar"] .st-key-side_reset_btn button { border-color: #7F1D1D !important; }
section[data-testid="stSidebar"] .st-key-side_reset_btn button:hover { background: #7F1D1D !important; border-color: #F87171 !important; }

.aa-side-status {
    display: flex; align-items: center; gap: 0.5rem; font-size: 0.8rem; color: var(--aa-sidebar-muted);
    padding: 0.4rem 0.2rem;
}
.aa-side-status .aa-sdot { width: 8px; height: 8px; border-radius: 50%; flex: 0 0 8px; }
.aa-side-status .aa-sdot.ok { background: #22C55E; box-shadow: 0 0 0 3px rgba(34, 197, 94, 0.2); }
.aa-side-status .aa-sdot.warn { background: #F59E0B; box-shadow: 0 0 0 3px rgba(245, 158, 11, 0.2); }
.aa-side-alert {
    background: #1E293B; border-left: 3px solid #F87171; border-radius: 8px;
    padding: 0.7rem 0.8rem; margin-top: 0.6rem; font-size: 0.8rem; color: #CBD5E1;
}
.aa-side-alert .aa-side-alert-title { font-weight: 700; color: #FCA5A5; margin-bottom: 0.3rem; }
.aa-side-label {
    font-size: 0.7rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em;
    color: var(--aa-sidebar-muted); margin: 0.35rem 0 0.35rem 0.1rem;
}

/* ---------- Reduced motion ---------- */
@media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
        animation-duration: 0.001ms !important; animation-iteration-count: 1 !important;
        transition-duration: 0.001ms !important;
    }
    [class*="st-key-aa-card"]:hover, .aa-metric:hover { transform: none !important; }
}
"""


def build_theme_css() -> str:
    """Return the full <style> block with design tokens emitted as CSS variables."""
    return f"<style>\n:root {{\n{_root_vars()}\n}}\n{_CSS_BODY}\n</style>"


# Kept for backward compatibility with any importer of the old constant.
THEME_CSS = build_theme_css()


def inject_theme_css() -> None:
    """Inject consolidated theme CSS into the Streamlit session."""
    st.markdown(THEME_CSS, unsafe_allow_html=True)


# ==============================================================================
# Escaping helpers ($ must never become LaTeX)
# ==============================================================================

def md_escape(text: Any) -> str:
    """Escape dollar signs for st.markdown so amounts never render as LaTeX."""
    if text is None:
        return ""
    s = str(text)
    # Normalize already-escaped dollars first so we never double escape.
    return s.replace("\\$", "$").replace("$", "\\$")


def html_text(text: Any) -> str:
    """HTML-escape text and neutralize '$' (as an entity) for use inside HTML markdown blocks."""
    if text is None:
        return ""
    return html.escape(str(text)).replace("$", "&#36;")


def money_html(amount: Any, currency: Optional[str] = None) -> str:
    """Format a monetary amount for HTML blocks (LaTeX-safe)."""
    try:
        value = f"{float(amount):,.2f}"
    except (TypeError, ValueError):
        value = html.escape(str(amount))
    suffix = f" {html.escape(currency)}" if currency and currency.upper() != "USD" else ""
    return f"&#36;{value}{suffix}"


def render_html(markup: str, container: Any = None) -> None:
    """Render a trusted HTML snippet via st.markdown (on an optional container)."""
    target = container if container is not None else st
    # Collapse indentation so markdown never treats lines as code blocks.
    cleaned = "\n".join(line.strip() for line in markup.strip().splitlines())
    target.markdown(cleaned, unsafe_allow_html=True)


# ==============================================================================
# Pills
# ==============================================================================

def get_status_pill_html(status: str) -> str:
    """WCAG AA status pill (text on tinted background, no emoji)."""
    s = (status or "").upper().strip()
    meta = STATUS_META.get(s)
    if meta:
        return f'<span class="aa-pill {meta["cls"]}">{meta["label"]}</span>'
    return f'<span class="aa-pill rejected">{html_text(s.replace("_", " ").title() or "Unknown")}</span>'


def get_mode_pill_html() -> str:
    """Single header mode pill: Mock or Gemini."""
    provider = (Config.LLM_PROVIDER or "mock").lower()
    if provider == "gemini":
        model_name = (
            Config.LLM_MODEL
            if Config.LLM_MODEL and Config.LLM_MODEL != "mock-agent"
            else "gemini-2.5-flash"
        )
        return (
            f'<span class="aa-mode-pill gemini" title="Live LLM: {html_text(model_name)}">'
            f'<span class="aa-dot"></span>Gemini · {html_text(model_name)}</span>'
        )
    return '<span class="aa-mode-pill mock" title="Deterministic offline agent"><span class="aa-dot"></span>Mock mode</span>'


# ==============================================================================
# Header, metrics, budget bar
# ==============================================================================

def render_header() -> None:
    """Product header: name, tagline, single mode pill."""
    render_html(
        f"""
        <div class="aa-header">
            <div>
                <h1>Agent Allowance</h1>
                <p class="aa-tagline">An allowance for your AI agent</p>
            </div>
            <div>{get_mode_pill_html()}</div>
        </div>
        """
    )


def bar_level(pct: float) -> str:
    """Return 'ok' | 'warn' | 'danger' for the given fraction of budget used."""
    if pct > BAR_DANGER_AT:
        return "danger"
    if pct > BAR_WARN_AT:
        return "warn"
    return "ok"


def get_budget_bar_html(spent: Any, budget: Any, pct: float, from_pct: Optional[float] = None) -> str:
    """Custom budget bar: light track, blue fill, amber > 75%, red > 90%, animated width."""
    pct = max(0.0, min(float(pct or 0.0), 1.0))
    start = pct if from_pct is None else max(0.0, min(float(from_pct), 1.0))
    level = bar_level(pct)
    return f"""
        <div class="aa-budget" role="group" aria-label="Monthly allowance usage">
            <div class="aa-budget-row">
                <span class="aa-budget-text">Monthly allowance used: <strong>{money_html(spent)}</strong> of {money_html(budget)}</span>
                <span class="aa-budget-pct {level}">{pct * 100:.1f}%</span>
            </div>
            <div class="aa-bar-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{pct * 100:.1f}">
                <div class="aa-bar-fill {level}" style="width: {pct * 100:.2f}%; --aa-bar-from: {start * 100:.2f}%;"></div>
            </div>
        </div>
    """


def render_metrics_banner(db_path: Optional[str] = None) -> None:
    """Four metric cards plus the custom budget bar, on every page."""
    metrics = get_dashboard_metrics(db_path=db_path)
    approved_spend = metrics["approved_spend"]
    monthly_budget = metrics["monthly_budget"]
    budget_remaining = metrics["budget_remaining"]
    budget_used_pct = float(metrics["budget_used_pct"])
    pending_count = metrics["pending_count"]
    blocked_count = metrics["blocked_count"]

    pending_cls = " attention" if pending_count > 0 else ""
    pending_sub = "Awaiting your review" if pending_count > 0 else "All cleared"

    render_html(
        f"""
        <div class="aa-metrics">
            <div class="aa-metric">
                <div class="aa-label">Approved spend this month</div>
                <div class="aa-value">{money_html(approved_spend)}</div>
                <div class="aa-sub">{budget_used_pct * 100:.1f}% of monthly budget</div>
            </div>
            <div class="aa-metric">
                <div class="aa-label">Budget remaining</div>
                <div class="aa-value">{money_html(budget_remaining)}</div>
                <div class="aa-sub">Monthly cap {money_html(monthly_budget)}</div>
            </div>
            <div class="aa-metric{pending_cls}">
                <div class="aa-label">Pending approvals</div>
                <div class="aa-value">{pending_count}</div>
                <div class="aa-sub">{pending_sub}</div>
            </div>
            <div class="aa-metric">
                <div class="aa-label">Blocked this month</div>
                <div class="aa-value">{blocked_count}</div>
                <div class="aa-sub">Stopped by policy rules</div>
            </div>
        </div>
        """
    )

    prev = st.session_state.get("_aa_prev_budget_pct")
    render_html(get_budget_bar_html(approved_spend, monthly_budget, budget_used_pct, from_pct=prev if prev is not None else 0.0))
    st.session_state["_aa_prev_budget_pct"] = budget_used_pct


def render_section_title(title: str, subtitle: str = "") -> None:
    """Page section heading (h2) with optional muted subtitle."""
    sub = f"<p>{html_text(subtitle)}</p>" if subtitle else ""
    render_html(f'<div class="aa-section"><h2>{html_text(title)}</h2>{sub}</div>')


# ==============================================================================
# Chat components
# ==============================================================================

def render_how_it_works_strip() -> None:
    """Empty-state strip: Agent proposes -> Rules decide -> PayPal pays."""
    render_html(
        """
        <div class="aa-how" aria-label="How it works">
            <div class="aa-step"><div class="aa-step-num">1</div><div>
                <div class="aa-step-title">Agent proposes</div>
                <div class="aa-step-desc">Searches the verified catalog and proposes one purchase.</div></div></div>
            <div class="aa-arrow">&rarr;</div>
            <div class="aa-step"><div class="aa-step-num">2</div><div>
                <div class="aa-step-title">Rules decide</div>
                <div class="aa-step-desc">Deterministic Python checks caps, budget and allowed merchants.</div></div></div>
            <div class="aa-arrow">&rarr;</div>
            <div class="aa-step"><div class="aa-step-num">3</div><div>
                <div class="aa-step-title">PayPal pays</div>
                <div class="aa-step-desc">A sandbox order is created only for approved purchases.</div></div></div>
        </div>
        """
    )


LIVE_STEPS = [
    ("search", "Searching catalog"),
    ("propose", "Proposing purchase"),
    ("policy", "Checking policy"),
]

_STEP_ICON = {"pending": "", "active": "", "done": "&#10003;", "skipped": "&ndash;"}


def get_live_steps_html(states: Dict[str, str], details: Optional[Dict[str, str]] = None) -> str:
    """Render the three agent steps. states: step_id -> pending|active|done|skipped."""
    details = details or {}
    rows: List[str] = []
    for step_id, label in LIVE_STEPS:
        state = states.get(step_id, "pending")
        detail = details.get(step_id)
        detail_html = f' <span class="aa-detail">· {html_text(detail)}</span>' if detail else ""
        rows.append(
            f'<div class="aa-steprow {state}"><span class="aa-ico">{_STEP_ICON[state]}</span>'
            f"<span>{label}{detail_html}</span></div>"
        )
    return f'<div class="aa-steps">{"".join(rows)}</div>'


# outcome label -> (css class, icon glyph)
_OUTCOME_STYLE = {
    "Purchase approved": ("approved", "&#10003;"),
    "Needs your approval": ("pending", "!"),
    "Blocked by policy": ("blocked", "&#10005;"),
    "No purchase proposed": ("none", "&ndash;"),
    "Agent error": ("error", "!"),
}


def get_outcome_html(label: str, state: str) -> str:
    """Outcome line. A green check is shown only when state == 'complete' (real approval)."""
    cls, icon = _OUTCOME_STYLE.get(label, ("none", "&ndash;"))
    if state != "complete" and cls == "approved":  # defensive: never a check without approval
        cls, icon = "none", "&ndash;"
    return f'<div class="aa-outcome {cls}"><span class="aa-oico">{icon}</span>{html_text(label)}</div>'


def status_card_key(status: str) -> str:
    """Map a decision status to the decision card key suffix (controls the left border color)."""
    return {
        "APPROVED": "approved",
        "NEEDS_APPROVAL": "pending",
        "BLOCKED": "blocked",
    }.get((status or "").upper(), "none")


def get_decision_body_html(
    status: str,
    merchant: str,
    amount: Any,
    currency: str,
    category: str,
    reason: str,
    policy_reasons: Iterable[str],
    next_step: str,
) -> str:
    """Decision card content (rendered inside a keyed bordered container)."""
    reasons_html = "".join(f"<li>{html_text(r)}</li>" for r in policy_reasons if r)
    reasons_block = (
        f'<div class="aa-label" style="margin-top:0.25rem;">Why</div><ul class="aa-reasons">{reasons_html}</ul>'
        if reasons_html
        else ""
    )
    reason_block = (
        f'<div class="aa-label">Agent&#39;s reason</div><p class="aa-reason">{html_text(reason)}</p>'
        if reason
        else ""
    )
    return f"""
        <div class="aa-card-head">
            <div class="aa-card-title">Policy decision</div>
            {get_status_pill_html(status)}
        </div>
        <div class="aa-kv">
            <div><div class="aa-label">Merchant</div><div class="aa-v">{html_text(merchant)}</div></div>
            <div><div class="aa-label">Amount</div><div class="aa-v big">{money_html(amount, currency)}</div></div>
            <div><div class="aa-label">Category</div><div class="aa-v">{html_text(category)}</div></div>
        </div>
        {reason_block}
        {reasons_block}
        <div class="aa-next"><strong>What happens next:</strong><span>{html_text(next_step)}</span></div>
    """


def get_empty_state_html(title: str, body: str, icon: str = "&#10003;") -> str:
    return f"""
        <div class="aa-empty">
            <div class="aa-empty-icon">{icon}</div>
            <h3>{html_text(title)}</h3>
            <p>{html_text(body)}</p>
        </div>
    """


# ==============================================================================
# AG Grid (Ledger) theme
# ==============================================================================
# The grid renders inside the component's iframe, so the page CSS above does not
# reach it. These helpers feed the same TOKENS to AG Grid's theming params and to
# the component's `custom_css` (injected into the iframe).

def get_aggrid_theme_params() -> Dict[str, Any]:
    """AG Grid (v34 Quartz) theme params matching the light app theme."""
    t = TOKENS
    return {
        "fontFamily": [{"googleFont": "Inter"}, "system-ui", "sans-serif"],
        "fontSize": 13,
        "headerFontSize": 11,
        "headerFontWeight": 600,
        "backgroundColor": t["surface"],
        "foregroundColor": t["text-soft"],
        "headerTextColor": t["text-muted"],
        "headerBackgroundColor": t["surface-muted"],
        "chromeBackgroundColor": t["surface-muted"],
        "borderColor": t["border"],
        "accentColor": t["primary"],
        "rowHoverColor": t["surface-muted"],
        "selectedRowBackgroundColor": t["primary-soft"],
        "wrapperBorderRadius": 12,
        "borderRadius": 8,
        "columnBorder": False,
        "spacing": 7,
    }


def get_aggrid_custom_css() -> Dict[str, Dict[str, str]]:
    """Iframe CSS for status pills, wrapped reasons and the pinned total row."""
    t = TOKENS
    css: Dict[str, Dict[str, str]] = {
        ".ag-header-cell-text": {"text-transform": "uppercase", "letter-spacing": "0.05em"},
        ".aa-pill": {
            "display": "inline-flex", "align-items": "center", "gap": "6px", "white-space": "nowrap",
            "padding": "2px 10px", "border-radius": t["radius-pill"], "font-size": "11.5px",
            "font-weight": "600", "letter-spacing": "0.02em", "line-height": "1.5",
        },
        ".aa-pill::before": {
            "content": '""', "width": "6px", "height": "6px", "border-radius": "50%", "background": "currentColor",
        },
        ".aa-merchant": {"font-weight": "600", "color": t["text"]},
        ".aa-amount": {"font-weight": "600", "color": t["text"], "font-variant-numeric": "tabular-nums"},
        ".aa-mono": {"font-family": "ui-monospace, SFMono-Regular, Menlo, monospace", "font-size": "11.5px"},
        ".aa-muted": {"color": t["placeholder"]},
        # Wrapped cells otherwise inherit the full row height as line-height.
        ".ag-cell-wrap-text": {"line-height": "1.45", "padding-top": "10px", "padding-bottom": "10px",
                               "word-break": "normal"},
        ".aa-reasons-cell .aa-r": {"white-space": "normal", "overflow-wrap": "anywhere"},
        ".aa-reasons-cell .aa-r + .aa-r": {"margin-top": "4px"},
        ".ag-row-pinned": {"font-weight": "700", "color": t["text"], "background": t["surface-muted"]},
        ".aa-total-label": {"font-size": "11px", "font-weight": "600", "text-transform": "uppercase",
                            "letter-spacing": "0.05em", "color": t["text-muted"]},
    }
    for meta in STATUS_META.values():
        cls = meta["cls"]
        css[f".aa-pill.{cls}"] = {"color": t[f"{cls}-fg"], "background": t[f"{cls}-bg"]}
    return css
