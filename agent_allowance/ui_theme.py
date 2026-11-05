"""
Presentation-layer design system for Agent Allowance (Redesign v4: Calm Neutral UI).

Centralizes:
- Calm, white, low-saturation design tokens (`TOKENS`) mirrored in `.streamlit/config.toml`
- WCAG 2.1 contrast helpers (`relative_luminance`, `contrast_ratio`)
- Budget progress bar threshold helpers (`gauge_level`, `gauge_color`)
- Single-row page header (`render_header`, `get_page_header_html`)
- Compact summary strip on every page (`render_metrics_banner`, `get_summary_strip_html`)
- 3-node pipeline strip (`pipeline_states`, `get_pipeline_html`)
- Unified decision card (`get_verdict_html`) for Chat and Approvals
- Neutral SVG empty-state illustrations (`get_empty_state_html`, `get_welcome_html`)
- AG Grid v4 theme configuration (`get_aggrid_theme_params`, `get_aggrid_custom_css`)

Presentation only — contains no policy, agent, database, or PayPal business logic.
"""

from decimal import Decimal
import html as _html
from typing import Any
import streamlit as st

from agent_allowance.db import get_dashboard_metrics


# ==============================================================================
# 1. V4 CALM NEUTRAL PALETTE & DESIGN TOKENS (Single Source of Truth)
# ==============================================================================
TOKENS: dict[str, str] = {
    # Surfaces & Borders
    "canvas": "#FAFAF9",
    "surface": "#FFFFFF",
    "border": "#E7E5E4",
    "border-strong": "#D6D3D1",
    "divider": "#F0EFED",
    # Text hierarchy (never lighter than #78716C for text)
    "ink": "#1C1917",
    "text-secondary": "#57534E",
    "text-muted": "#78716C",
    # Buttons
    "primary-bg": "#1C1917",
    "primary-hover": "#292524",
    "primary-text": "#FFFFFF",
    "secondary-bg": "#FFFFFF",
    "secondary-border": "#D6D3D1",
    "secondary-text": "#44403C",
    "secondary-hover": "#F5F5F4",
    # Accent (use sparingly: active nav item text & links, active nav bg, focus ring)
    "plum": "#4A3357",
    "accent": "#4A3357",
    "nav-active-bg": "#F3EFF5",
    "focus-ring-hex": "#6B4C7A",
    "focus-ring": "rgba(107, 76, 122, 0.35)",
    # Status (muted)
    # Approved
    "approved-fg": "#2F6B3E",
    "approved-bg": "#EEF5EF",
    "approved-border": "#CFE3D3",
    "approved-bar": "#5E9B6E",
    "approved-dot": "#5E9B6E",
    # Needs approval
    "pending-fg": "#8A5A12",
    "pending-bg": "#FBF5E8",
    "pending-border": "#EBDDBB",
    "pending-bar": "#C79A3B",
    "pending-dot": "#C79A3B",
    # Blocked
    "blocked-fg": "#9B3B3B",
    "blocked-bg": "#FBEFEF",
    "blocked-border": "#EBCFCF",
    "blocked-bar": "#B45454",
    "blocked-dot": "#B45454",
    # Rejected
    "rejected-fg": "#57534E",
    "rejected-bg": "#F5F5F4",
    "rejected-border": "#E7E5E4",
    "rejected-bar": "#A8A29E",
    "rejected-dot": "#A8A29E",
    # Executed (uses approved palette with label "Executed")
    "executed-fg": "#2F6B3E",
    "executed-bg": "#EEF5EF",
    "executed-border": "#CFE3D3",
    "executed-bar": "#5E9B6E",
    "executed-dot": "#5E9B6E",
    # Chat & Grid surfaces
    "user-bubble-bg": "#F1F0EE",
    "user-bubble-fg": "#1C1917",
    "grid-header-bg": "#F5F5F4",
    "grid-header-fg": "#1C1917",
    "grid-selected-bg": "#E9E3EE",
    "grid-hover-bg": "#F3EFF5",
    # Sidebar (white, 1px right border)
    "sidebar-bg": "#FFFFFF",
    "sidebar-surface": "#FAFAF9",
    "sidebar-border": "#E7E5E4",
    "sidebar-text": "#1C1917",
    "sidebar-captions": "#57534E",
    "sidebar-nav-inactive": "#57534E",
    # Summary bar / gauge thresholds (ink < 75%, amber 75-90%, red > 90%)
    "gauge-ok": "#1C1917",
    "gauge-warn": "#C79A3B",
    "gauge-danger": "#B45454",
    "gauge-track": "#F0EFED",
    # Radii, shadow & typography
    "radius-card": "10px",
    "radius-input": "10px",
    "radius-button": "7px",
    "radius-pill": "9999px",
    "shadow": "0 1px 2px rgba(0, 0, 0, 0.04)",
    "font-sans": "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    "font-mono": "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
    # Semantic aliases for compatibility
    "bg": "#FAFAF9",
    "surface-muted": "#F5F5F4",
    "border-solid": "#E7E5E4",
    "primary": "#1C1917",
    "text-heading": "#1C1917",
    "text": "#1C1917",
    "text-soft": "#57534E",
}


# ==============================================================================
# 2. WCAG 2.1 CONTRAST UTILITIES
# ==============================================================================
def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert a #RRGGBB hex string into an (R, G, B) integer tuple."""
    cleaned = hex_color.strip().lstrip("#")
    if len(cleaned) == 3:
        cleaned = "".join(ch * 2 for ch in cleaned)
    if len(cleaned) != 6:
        raise ValueError(f"Expected 6-digit hex color, got {hex_color!r}")
    return int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16)


def relative_luminance(hex_color: str) -> float:
    """Compute WCAG 2.1 relative luminance for a #RRGGBB hex color."""
    r, g, b = _hex_to_rgb(hex_color)
    channels = []
    for channel in (r, g, b):
        s = channel / 255.0
        channels.append(s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    """Compute WCAG 2.1 contrast ratio (>= 1.0) between two #RRGGBB hex colors."""
    lum1 = relative_luminance(fg_hex)
    lum2 = relative_luminance(bg_hex)
    brightest = max(lum1, lum2)
    darkest = min(lum1, lum2)
    return (brightest + 0.05) / (darkest + 0.05)


# ==============================================================================
# 3. ESCAPING, ICONS & FORMATTING HELPERS
# ==============================================================================
def html_text(value: object) -> str:
    """
    HTML-escape any dynamic text before interpolating into `st.markdown(..., unsafe_allow_html=True)`,
    and replace `$` with `&#36;` so Streamlit's Markdown renderer never pairs two dollar signs
    into a LaTeX math span.
    """
    if value is None:
        return ""
    return _html.escape(str(value), quote=True).replace("$", "&#36;")


def md_escape(value: object) -> str:
    """Escape `$` in strings passed to native Streamlit markdown widgets (`st.caption`, `st.toast`, etc.)."""
    if value is None:
        return ""
    return str(value).replace("$", r"\$")


def money_html(amount: object, currency: object = "USD") -> str:
    """
    Format a numeric amount as HTML-safe `&#36;X.XX` (or `X.XX CUR` when non-USD).
    If `amount` is non-numeric, escape both `amount` and `currency` safely.
    """
    cur = str(currency or "USD").strip()
    try:
        val = float(amount)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return f"{html_text(amount)} {html_text(cur)}".strip()
    if cur.upper() == "USD" or not cur:
        return f"&#36;{val:,.2f}"
    return f"{val:,.2f} {html_text(cur)}"


def render_html(markup: str, container: Any = None) -> None:
    """Render an HTML snippet through `st.markdown(..., unsafe_allow_html=True)`."""
    target = container if container is not None else st
    target.markdown(markup, unsafe_allow_html=True)


def svg_icon(name: str, size: int = 16, stroke_width: float = 1.7) -> str:
    """Return a clean inline SVG icon using `currentColor`."""
    sw = f"{stroke_width:.1f}"
    paths = {
        "check": f'<path d="M5 10.5L8.5 14L15 6.5" stroke="currentColor" stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round"/>',
        "hourglass": f'<circle cx="10" cy="10" r="7" stroke="currentColor" stroke-width="{sw}"/><path d="M10 6.5V10.3L12.5 11.8" stroke="currentColor" stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round"/>',
        "shield-x": f'<path d="M10 3L16 5.6V10.4C16 14 13.4 16.9 10 18C6.6 16.9 4 14 4 10.4V5.6L10 3Z" stroke="currentColor" stroke-width="{sw}" stroke-linejoin="round"/><path d="M7.8 7.8L12.2 12.2M12.2 7.8L7.8 12.2" stroke="currentColor" stroke-width="{sw}" stroke-linecap="round"/>',
        "shield": f'<path d="M10 3L16 5.6V10.4C16 14 13.4 16.9 10 18C6.6 16.9 4 14 4 10.4V5.6L10 3Z" stroke="currentColor" stroke-width="{sw}" stroke-linejoin="round"/><path d="M7.5 10.3L9.3 12.1L12.8 8.4" stroke="currentColor" stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round"/>',
        "package": f'<path d="M10 2.8L16.5 6.3V13.7L10 17.2L3.5 13.7V6.3L10 2.8Z" stroke="currentColor" stroke-width="{sw}" stroke-linejoin="round"/><path d="M3.7 6.5L10 10L16.3 6.5M10 10V17" stroke="currentColor" stroke-width="{sw}" stroke-linecap="round"/>',
        "wheat": f'<path d="M10 3.5V16.5M6.5 7C8 7 10 8.5 10 10.5C10 8.5 12 7 13.5 7M6.5 11C8 11 10 12.5 10 14.5C10 12.5 12 11 13.5 11" stroke="currentColor" stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round"/>',
        "gift": f'<rect x="3.5" y="7.5" width="13" height="9" rx="1.5" stroke="currentColor" stroke-width="{sw}"/><path d="M2.8 7.5H17.2M10 5.5V16.5M10 7.5C8 7.5 6.8 6.4 6.8 5.2C6.8 4.2 7.7 3.5 8.6 3.5C9.6 3.5 10 5 10 7.5ZM10 7.5C12 7.5 13.2 6.4 13.2 5.2C13.2 4.2 12.3 3.5 11.4 3.5C10.4 3.5 10 5 10 7.5Z" stroke="currentColor" stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round"/>',
        "wallet": f'<rect x="3" y="5" width="14" height="10" rx="2" stroke="currentColor" stroke-width="{sw}"/><path d="M13 10H15" stroke="currentColor" stroke-width="{sw}" stroke-linecap="round"/>',
        "store": f'<path d="M3.5 8.5V15.5H16.5V8.5M2.8 8.5L4.5 4.5H15.5L17.2 8.5H2.8Z" stroke="currentColor" stroke-width="{sw}" stroke-linejoin="round"/>',
        "info": f'<circle cx="10" cy="10" r="7" stroke="currentColor" stroke-width="{sw}"/><path d="M10 9V13.2" stroke="currentColor" stroke-width="{sw}" stroke-linecap="round"/><circle cx="10" cy="6.6" r="0.9" fill="currentColor"/>',
    }
    inner = paths.get(name, paths["info"])
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 20 20" fill="none" '
        f'xmlns="http://www.w3.org/2000/svg">{inner}</svg>'
    )


# ==============================================================================
# 4. STATUS PILL METADATA & HTML
# ==============================================================================
STATUS_META = {
    "APPROVED": {
        "cls": "approved",
        "label": "Approved",
        "verdict_title": "Purchase approved",
        "icon": svg_icon("check", 16, 1.8),
        "pill_icon": '<svg width="11" height="11" viewBox="0 0 12 12" fill="none"><path d="M2.5 6.3L5 8.8L9.5 3.8" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    },
    "NEEDS_APPROVAL": {
        "cls": "pending",
        "label": "Needs approval",
        "verdict_title": "Needs your approval",
        "icon": svg_icon("hourglass", 16, 1.7),
        "pill_icon": '<svg width="11" height="11" viewBox="0 0 12 12" fill="none"><circle cx="6" cy="6" r="4.2" stroke="currentColor" stroke-width="1.4"/><path d="M6 3.8V6.2L7.6 7.1" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>',
    },
    "BLOCKED": {
        "cls": "blocked",
        "label": "Blocked",
        "verdict_title": "Blocked by policy",
        "icon": svg_icon("shield-x", 16, 1.7),
        "pill_icon": '<svg width="11" height="11" viewBox="0 0 12 12" fill="none"><path d="M3.5 3.5L8.5 8.5M8.5 3.5L3.5 8.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
    },
    "EXECUTED": {
        "cls": "executed",
        "label": "Executed",
        "verdict_title": "Executed",
        "icon": svg_icon("check", 16, 1.8),
        "pill_icon": '<svg width="11" height="11" viewBox="0 0 12 12" fill="none"><path d="M2 6.3L4.4 8.8L8.8 3.8M6.5 8.8L10.5 3.8" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    },
    "REJECTED": {
        "cls": "rejected",
        "label": "Rejected",
        "verdict_title": "Proposal rejected",
        "icon": '<svg width="16" height="16" viewBox="0 0 20 20" fill="none"><circle cx="10" cy="10" r="7" stroke="currentColor" stroke-width="1.7"/><path d="M7 10H13" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
        "pill_icon": '<svg width="11" height="11" viewBox="0 0 12 12" fill="none"><path d="M3.2 6H8.8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
    },
}


def status_card_key(status: str) -> str:
    """Map status string to semantic key ('approved', 'pending', 'blocked', 'rejected', 'executed')."""
    key = (status or "").upper()
    meta = STATUS_META.get(key)
    return meta["cls"] if meta else "rejected"


def get_status_pill_html(status: str) -> str:
    """Return a muted semantic status pill with small icon and text."""
    key = (status or "").upper()
    meta = STATUS_META.get(key)
    if not meta:
        fallback = str(status or "Unknown").replace("_", " ").title()
        return f'<span class="aa-pill rejected aa-pill-rejected">{html_text(fallback)}</span>'
    cls = meta["cls"]
    return (
        f'<span class="aa-pill {cls} aa-pill-{cls}">'
        f'{meta["pill_icon"]}<span>{meta["label"]}</span></span>'
    )


# ==============================================================================
# 5. SUMMARY BAR THRESHOLD HELPERS
# ==============================================================================
def gauge_level(pct: float | None) -> str:
    """
    Classify budget usage ratio (0.0..1.0+) into 'ok' (<75%), 'warn' (75%..90%),
    or 'danger' (>90%).
    """
    try:
        val = float(pct or 0.0)
    except (TypeError, ValueError):
        val = 0.0
    if val > 0.90:
        return "danger"
    if val >= 0.75:
        return "warn"
    return "ok"


def gauge_color(pct: float | None) -> str:
    """Return the hex color token for the budget bar fill based on usage ratio."""
    lvl = gauge_level(pct)
    if lvl == "danger":
        return TOKENS["gauge-danger"]
    if lvl == "warn":
        return TOKENS["gauge-warn"]
    return TOKENS["gauge-ok"]


def get_ring_gauge_svg(pct: float, size: int = 44) -> str:
    """Compact neutral ring SVG helper kept for backward compatibility."""
    clamped = max(0.0, min(float(pct or 0.0), 1.0))
    stroke = gauge_color(pct)
    r = 16
    c = 2 * 3.14159265 * r
    offset = c * (1.0 - clamped)
    pct_label = int(round(max(0.0, float(pct or 0.0)) * 100))
    return (
        f'<svg class="aa-ring-svg" width="{size}" height="{size}" viewBox="0 0 40 40" '
        f'role="img" aria-label="Budget used {pct_label}%">'
        f'<circle cx="20" cy="20" r="{r}" fill="none" stroke="{TOKENS["gauge-track"]}" stroke-width="3.5"/>'
        f'<circle cx="20" cy="20" r="{r}" fill="none" stroke="{stroke}" stroke-width="3.5" '
        f'stroke-linecap="round" stroke-dasharray="{c:.2f}" stroke-dashoffset="{offset:.2f}" '
        f'transform="rotate(-90 20 20)"/>'
        f'<text x="20" y="23.5" text-anchor="middle" fill="{TOKENS["ink"]}" '
        f'font-size="9.5" font-weight="500">{pct_label}%</text>'
        f"</svg>"
    )


# ==============================================================================
# 6. PIPELINE STRIP (Agent -> Rules -> PayPal)
# ==============================================================================
_PIPELINE_ICONS = {
    "agent": (
        '<svg width="14" height="14" viewBox="0 0 20 20" fill="none">'
        '<rect x="3.5" y="5.5" width="13" height="10" rx="2.5" stroke="currentColor" stroke-width="1.6"/>'
        '<circle cx="7.8" cy="10.5" r="1.1" fill="currentColor"/>'
        '<circle cx="12.2" cy="10.5" r="1.1" fill="currentColor"/>'
        '<path d="M10 3V5.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>'
        "</svg>"
    ),
    "rules": (
        '<svg width="14" height="14" viewBox="0 0 20 20" fill="none">'
        '<path d="M10 2.8L16 5.3V10C16 13.7 13.5 16.5 10 17.6C6.5 16.5 4 13.7 4 10V5.3L10 2.8Z" '
        'stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>'
        '<path d="M7.5 10.2L9.3 12L12.8 8.3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>'
        "</svg>"
    ),
    "paypal": (
        '<svg width="14" height="14" viewBox="0 0 20 20" fill="none">'
        '<rect x="2.5" y="4.5" width="15" height="11" rx="2" stroke="currentColor" stroke-width="1.6"/>'
        '<path d="M2.5 8.2H17.5" stroke="currentColor" stroke-width="1.6"/>'
        '<path d="M5.5 12.2H9" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>'
        "</svg>"
    ),
}


def pipeline_states(
    phase: str,
    status: str | None = None,
    has_order: bool = False,
) -> dict[str, tuple[str, str]]:
    """
    Compute `(visual_state, caption)` for each node of the Agent -> Rules -> PayPal
    pipeline strip.

    Security invariant: when `status` is `BLOCKED` or `NEEDS_APPROVAL`, the PayPal
    node is ALWAYS `("dark", "Not contacted")`.
    """
    st_upper = (status or "").upper()
    if phase == "searching":
        return {
            "agent": ("active", "Searching catalog..."),
            "rules": ("idle", "Waiting"),
            "paypal": ("idle", "Standby"),
        }
    if phase == "proposing":
        return {
            "agent": ("active", "Drafting proposal..."),
            "rules": ("idle", "Waiting"),
            "paypal": ("idle", "Standby"),
        }
    if phase == "checking":
        return {
            "agent": ("done", "Proposal drafted"),
            "rules": ("active", "Checking rules..."),
            "paypal": ("idle", "Standby"),
        }
    if phase == "ordering":
        return {
            "agent": ("done", "Proposal drafted"),
            "rules": ("approved", "Approved"),
            "paypal": ("active", "Creating order..."),
        }
    if phase == "decided":
        if st_upper in ("APPROVED", "EXECUTED"):
            pp_state = ("approved", "Order created") if has_order else ("dark", "No order ID")
            rule_lbl = "Executed" if st_upper == "EXECUTED" else "Approved"
            return {
                "agent": ("done", "Proposed"),
                "rules": ("approved", rule_lbl),
                "paypal": pp_state,
            }
        if st_upper == "NEEDS_APPROVAL":
            return {
                "agent": ("done", "Proposed"),
                "rules": ("pending", "Needs approval"),
                "paypal": ("dark", "Not contacted"),
            }
        if st_upper == "BLOCKED":
            return {
                "agent": ("done", "Proposed"),
                "rules": ("blocked", "Blocked"),
                "paypal": ("dark", "Not contacted"),
            }
    if phase == "no_proposal":
        return {
            "agent": ("done", "Answered"),
            "rules": ("idle", "No purchase"),
            "paypal": ("dark", "Not contacted"),
        }
    if phase == "error":
        return {
            "agent": ("error", "Stopped"),
            "rules": ("idle", "Skipped"),
            "paypal": ("dark", "Not contacted"),
        }
    return {
        "agent": ("idle", "Proposes"),
        "rules": ("idle", "Enforces limits"),
        "paypal": ("idle", "Only if approved"),
    }


def get_pipeline_html(
    states_or_phase: dict[str, tuple[str, str]] | str = "idle",
    agent_detail: str = "",
    compact: bool = False,
    status: str | None = None,
    has_order: bool = False,
) -> str:
    """
    Render the slim white 3-node pipeline strip (`Agent` -> `Rules` -> `PayPal`)
    joined by thin lines, with 24px icon tiles.
    """
    if isinstance(states_or_phase, dict):
        states = dict(states_or_phase)
    else:
        states = pipeline_states(str(states_or_phase), status=status, has_order=has_order)

    spec = [
        ("agent", "Agent"),
        ("rules", "Rules"),
        ("paypal", "PayPal"),
    ]
    parts: list[str] = []
    for idx, (key, title) in enumerate(spec):
        vstate, caption = states.get(key, ("idle", ""))
        if key == "agent" and agent_detail:
            caption = f"{caption} · {agent_detail}" if caption else agent_detail
        safe_cap = html_text(caption)
        node_html = (
            f'<div class="aa-pipe-node aa-pipe-{html_text(vstate)}">'
            f'<div class="aa-pipe-tile">{_PIPELINE_ICONS[key]}</div>'
            f'<div class="aa-pipe-meta">'
            f'<span class="aa-pipe-title">{title}</span>'
            f'<span class="aa-pipe-sub">{safe_cap}</span>'
            f"</div>"
            f"</div>"
        )
        parts.append(node_html)
        if idx < len(spec) - 1:
            next_state = states.get(spec[idx + 1][0], ("idle", ""))[0]
            conn_cls = "aa-pipe-conn"
            if next_state in ("done", "approved"):
                conn_cls += " aa-pipe-conn-lit"
            elif next_state == "active":
                conn_cls += " aa-pipe-conn-active"
            elif next_state == "dark":
                conn_cls += " aa-pipe-conn-dashed"
            parts.append(f'<div class="{conn_cls}"></div>')

    return f'<div class="aa-pipeline-strip">{"".join(parts)}</div>'


# ==============================================================================
# 7. DECISION CARD (Unified anatomy for Chat & Approvals)
# ==============================================================================
_INFO_ICON_SVG = (
    '<svg width="14" height="14" viewBox="0 0 16 16" fill="none">'
    '<circle cx="8" cy="8" r="6.2" stroke="currentColor" stroke-width="1.3"/>'
    '<path d="M8 7.2V11" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>'
    '<circle cx="8" cy="5" r="0.8" fill="currentColor"/>'
    "</svg>"
)


def get_verdict_html(
    result: dict[str, Any] | None = None,
    *,
    status: str = "BLOCKED",
    title: str = "",
    merchant: str = "",
    amount: Any = 0.0,
    currency: str = "USD",
    category: str = "",
    reason: str = "",
    policy_reasons: list[str] | None = None,
    next_step: str = "",
    subtitle: str = "Decided by rules, not the agent",
    order_id: str | None = None,
) -> str:
    """
    Build the v4 Decision Card HTML (`approved`, `needs approval`, `blocked`, `executed`, `rejected`):
    - White card with 1px hairline border (#E7E5E4), 10px radius, overflow hidden
    - 3px inner left status accent bar so corners stay clean
    - Header row: 28px tinted icon tile, title + subtitle ("Decided by rules, not the agent"),
      and muted status pill on the right
    - Below #F0EFED divider: merchant name and category on left, 20px tabular amount on right
    - Plain-English reasons list, each with a small colored status dot
    - Muted line with info icon: "What happens next: ..." (with 12px space before buttons)
    """
    if isinstance(result, dict):
        status = str(result.get("status") or status).upper()
        merchant = str(result.get("merchant") or merchant or "Unknown merchant")
        amount = result.get("amount", amount)
        currency = str(result.get("currency") or currency or "USD")
        category = str(result.get("category") or category or "General")
        reason = str(result.get("reason") or reason or "")
        raw_r = result.get("reasons")
        if isinstance(raw_r, list):
            policy_reasons = [str(r) for r in raw_r]
        order_id = result.get("paypal_order_id") or order_id

    st_upper = (status or "BLOCKED").upper()
    meta = STATUS_META.get(st_upper, STATUS_META["BLOCKED"])
    cls = meta["cls"]

    card_title = html_text(title or meta["verdict_title"])
    card_sub = html_text(subtitle or "Decided by rules, not the agent")
    safe_merchant = html_text(merchant or "Unknown merchant")
    safe_cat = html_text(category or "General")
    amount_str = money_html(amount, currency=currency)

    reasons_list: list[str] = []
    if policy_reasons:
        reasons_list.extend(str(r) for r in policy_reasons if str(r).strip())
    if reason and str(reason).strip() and str(reason).strip() not in reasons_list:
        reasons_list.append(str(reason).strip())
    if not reasons_list:
        reasons_list = ["Evaluated against active policy rules."]

    reasons_items = "".join(
        f'<li><span class="aa-reason-dot aa-dot-{cls}"></span><span>{html_text(r)}</span></li>'
        for r in reasons_list
    )

    order_html = ""
    if order_id:
        order_html = (
            f'<div class="aa-verdict-order-meta">'
            f'Sandbox order <code class="aa-mono">{html_text(order_id)}</code>'
            f"</div>"
        )

    safe_next = html_text(next_step or "Recorded in the audit ledger.")
    pill_html = get_status_pill_html(st_upper)

    return (
        f'<div class="aa-decision-card aa-decision-{cls} aa-verdict-{cls} aa-needs-you">'
        f'<div class="aa-decision-accent aa-accent-{cls}"></div>'
        f'<div class="aa-decision-inner">'
        f'<div class="aa-decision-head">'
        f'<div class="aa-decision-head-left">'
        f'<div class="aa-decision-icon aa-icon-{cls}">{meta["icon"]}</div>'
        f"<div>"
        f'<div class="aa-decision-title">{card_title}</div>'
        f'<div class="aa-decision-sub">{card_sub}</div>'
        f"</div>"
        f"</div>"
        f'<div class="aa-decision-head-right">{pill_html}</div>'
        f"</div>"
        f'<div class="aa-decision-divider"></div>'
        f'<div class="aa-decision-deal">'
        f'<div class="aa-decision-merchant-wrap">'
        f'<span class="aa-decision-merchant">{safe_merchant}</span>'
        f'<span class="aa-decision-cat">{safe_cat}</span>'
        f"</div>"
        f'<div class="aa-decision-amount">{amount_str}</div>'
        f"</div>"
        f'<ul class="aa-verdict-reasons">{reasons_items}</ul>'
        f"{order_html}"
        f'<div class="aa-decision-next">'
        f'<span class="aa-decision-next-icon">{_INFO_ICON_SVG}</span>'
        f'<span><strong>What happens next:</strong> {safe_next}</span>'
        f"</div>"
        f"</div>"
        f"</div>"
    )


# ==============================================================================
# 8. NEUTRAL EMPTY-STATE ILLUSTRATIONS (Greys & Ink with subtle muted accent)
# ==============================================================================
WELCOME_ILLUSTRATION = """
<svg width="64" height="48" viewBox="0 0 72 56" fill="none" xmlns="http://www.w3.org/2000/svg">
  <rect x="8" y="6" width="56" height="44" rx="8" fill="#FFFFFF" stroke="#E7E5E4" stroke-width="1.2"/>
  <rect x="15" y="13" width="22" height="4" rx="2" fill="#D6D3D1"/>
  <rect x="15" y="21" width="34" height="3" rx="1.5" fill="#E7E5E4"/>
  <rect x="15" y="28" width="28" height="3" rx="1.5" fill="#E7E5E4"/>
  <rect x="44" y="33" width="14" height="11" rx="4" fill="#F5F5F4" stroke="#D6D3D1"/>
  <path d="M51 35.5L54.2 36.8V39.6C54.2 41.5 52.8 43.1 51 43.7C49.2 43.1 47.8 41.5 47.8 39.6V36.8L51 35.5Z" stroke="#1C1917" stroke-width="1.2" stroke-linejoin="round"/>
</svg>
"""

APPROVALS_EMPTY_ILLUSTRATION = """
<svg width="64" height="48" viewBox="0 0 72 56" fill="none" xmlns="http://www.w3.org/2000/svg">
  <rect x="12" y="7" width="48" height="42" rx="8" fill="#FFFFFF" stroke="#E7E5E4" stroke-width="1.2"/>
  <circle cx="36" cy="25" r="10" fill="#EEF5EF" stroke="#CFE3D3" stroke-width="1.2"/>
  <path d="M31.8 25.2L34.7 28.1L40.5 22.3" stroke="#2F6B3E" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
  <rect x="24" y="40" width="24" height="3" rx="1.5" fill="#E7E5E4"/>
</svg>
"""

LEDGER_EMPTY_ILLUSTRATION = """
<svg width="64" height="48" viewBox="0 0 72 56" fill="none" xmlns="http://www.w3.org/2000/svg">
  <rect x="10" y="7" width="52" height="42" rx="8" fill="#FFFFFF" stroke="#E7E5E4" stroke-width="1.2"/>
  <line x1="10" y1="18" x2="62" y2="18" stroke="#E7E5E4" stroke-width="1.2"/>
  <rect x="17" y="24" width="22" height="3.5" rx="1.75" fill="#D6D3D1"/>
  <rect x="45" y="24" width="10" height="3.5" rx="1.75" fill="#E7E5E4"/>
  <rect x="17" y="33" width="28" height="3.5" rx="1.75" fill="#E7E5E4"/>
  <rect x="48" y="33" width="7" height="3.5" rx="1.75" fill="#E7E5E4"/>
</svg>
"""


def get_welcome_html() -> str:
    """Small neutral illustration and one line of copy for the Chat empty state."""
    return (
        f'<div class="aa-empty-state">'
        f'<div class="aa-empty-illus">{WELCOME_ILLUSTRATION}</div>'
        f'<div class="aa-empty-sub">Ask the agent to buy supplies, test an over-threshold request, or try a prompt injection.</div>'
        f"</div>"
    )


def get_empty_state_html(
    title: str,
    subtitle: str,
    illustration: str = WELCOME_ILLUSTRATION,
) -> str:
    """Render a quiet, centered empty-state block with neutral SVG illustration."""
    return (
        f'<div class="aa-empty-state">'
        f'<div class="aa-empty-illus">{illustration}</div>'
        f'<div class="aa-empty-title">{html_text(title)}</div>'
        f'<div class="aa-empty-sub">{html_text(subtitle)}</div>'
        f"</div>"
    )


# ==============================================================================
# 9. PAGE HEADER, MODE PILL & SIDEBAR METER
# ==============================================================================
def get_mode_pill_html(
    mode: str = "mock",
    note: str | None = None,
    mode_note: str | None = None,
) -> str:
    """Render a single neutral mode pill ('Demo mode' or 'Gemini live')."""
    is_live = (mode or "").lower() in ("live", "gemini")
    dot_cls = "aa-mode-dot-live" if is_live else "aa-mode-dot-mock"
    label = "Gemini live" if is_live else "Demo mode"
    final_note = note if note is not None else mode_note
    note_span = f' <span class="aa-mode-note">· {html_text(final_note)}</span>' if final_note else ""
    return (
        f'<span class="aa-mode-pill">'
        f'<span class="aa-mode-dot {dot_cls}"></span>'
        f"<span>{label}</span>"
        f"{note_span}"
        f"</span>"
    )


def get_side_meter_html(remaining: int, cap: int) -> str:
    """Sidebar 'Gemini requests left' with a neutral ink progress bar."""
    rem = max(0, int(remaining))
    total = max(1, int(cap))
    pct = max(0.0, min((rem / total) * 100.0, 100.0))
    return (
        f'<div class="aa-sidebar-quota">'
        f'<div class="aa-sidebar-quota-row">'
        f"<span>Gemini requests left</span>"
        f'<span class="aa-sidebar-quota-val">{rem} / {total}</span>'
        f"</div>"
        f'<div class="aa-sidebar-quota-track">'
        f'<div class="aa-sidebar-quota-fill" style="width:{pct:.1f}%;"></div>'
        f"</div>"
        f"</div>"
    )


def get_quota_notice_html(notice: str) -> str:
    """Render a quiet inline notice when Gemini falls back to Mock mode."""
    return (
        f'<div class="aa-quota-banner">'
        f'<span class="aa-quota-dot"></span>'
        f"<span>{html_text(notice)}</span>"
        f"</div>"
    )


def get_page_header_html(
    page_title: str,
    mode: str = "mock",
    note: str | None = None,
) -> str:
    """
    Single-row page header: 22px page title on the left and one neutral
    'Mock mode' or 'Gemini live' pill on the right. No hero banner.
    """
    pill = get_mode_pill_html(mode, note=note)
    return (
        f'<div class="aa-page-header">'
        f'<h1 class="aa-page-title">{html_text(page_title)}</h1>'
        f'<div class="aa-page-header-right">{pill}</div>'
        f"</div>"
    )


def render_header(
    mode: str = "mock",
    mode_note: str | None = None,
    show_tagline: bool = False,
    page_title: str = "Chat",
) -> None:
    """Render the single-row v4 page header."""
    st.markdown(
        get_page_header_html(page_title=page_title, mode=mode, note=mode_note),
        unsafe_allow_html=True,
    )


def render_section_title(title: str, subtitle: str = "") -> None:
    """Render a quiet subtitle line below the summary strip (page title is already in the header)."""
    if subtitle:
        st.markdown(
            f'<div class="aa-section-sub">{html_text(subtitle)}</div>',
            unsafe_allow_html=True,
        )


# ==============================================================================
# 10. SUMMARY STRIP ON EVERY PAGE
# ==============================================================================
def get_summary_strip_html(metrics: dict) -> str:
    """
    Render the single-row white summary strip used on EVERY page:
    - "This month $X / $Y" (approved spend this calendar month) with a thin 4px bar (ink fill, amber above 75%, red above 90%)
    - "Pending N" with a small status dot
    - "Blocked N" with a small status dot
    Wraps cleanly on narrow widths.
    """
    spent = float(metrics.get("approved_spend", Decimal("0.00")))
    budget = float(metrics.get("monthly_budget", Decimal("200.00")))
    pct = float(metrics.get("budget_used_pct", 0.0))
    pending = int(metrics.get("pending_count", 0))
    blocked = int(metrics.get("blocked_count", 0))

    bar_color = gauge_color(pct)
    bar_width = max(0.0, min(pct * 100.0, 100.0))

    pending_dot_cls = "aa-dot-pending" if pending > 0 else "aa-dot-rejected"
    blocked_dot_cls = "aa-dot-blocked" if blocked > 0 else "aa-dot-rejected"

    return (
        f'<div class="aa-summary-strip">'
        f'<div class="aa-summary-spend">'
        f'<span class="aa-summary-label">This month</span>'
        f'<span class="aa-summary-money">{money_html(spent)} <span class="aa-summary-cap">/ {money_html(budget)}</span></span>'
        f'<div class="aa-summary-bar-track" role="progressbar" aria-valuenow="{int(round(pct * 100))}" aria-valuemin="0" aria-valuemax="100">'
        f'<div class="aa-summary-bar-fill" style="width:{bar_width:.1f}%;background:{bar_color};"></div>'
        f"</div>"
        f"</div>"
        f'<div class="aa-summary-counts">'
        f'<div class="aa-summary-stat">'
        f'<span class="aa-summary-dot {pending_dot_cls}"></span>'
        f'<span class="aa-summary-stat-label">Pending</span>'
        f'<span class="aa-summary-stat-val">{pending}</span>'
        f"</div>"
        f'<div class="aa-summary-stat">'
        f'<span class="aa-summary-dot {blocked_dot_cls}"></span>'
        f'<span class="aa-summary-stat-label">Blocked</span>'
        f'<span class="aa-summary-stat-val">{blocked}</span>'
        f"</div>"
        f"</div>"
        f"</div>"
    )


def render_metrics_banner(
    db_path: str | None = None,
    compact_strip: bool = True,
    compact: bool = True,
) -> None:
    """Render the unified single-row summary strip on every page."""
    m = (
        get_dashboard_metrics(db_path=db_path)
        if db_path
        else get_dashboard_metrics()
    )
    st.markdown(get_summary_strip_html(m), unsafe_allow_html=True)


# ==============================================================================
# 11. AG GRID V4 THEME CONFIGURATION
# ==============================================================================
def get_aggrid_theme_params() -> dict:
    """
    Generate AG Grid theme parameters directly from `TOKENS`:
    - Header row `#F5F5F4` with ink (`#1C1917`) 500-weight text
    - White rows (`#FFFFFF`), hairline `#E7E5E4` borders
    - Selected row `#E9E3EE` (ink 13.9:1), hover `#F3EFF5`
    - No per-cell focus border (selection is by row)
    """
    return {
        "backgroundColor": TOKENS["surface"],
        "foregroundColor": TOKENS["ink"],
        "headerBackgroundColor": TOKENS["grid-header-bg"],
        "headerTextColor": TOKENS["grid-header-fg"],
        "headerFontWeight": 500,
        "oddRowBackgroundColor": TOKENS["surface"],
        "rowHoverColor": TOKENS["grid-hover-bg"],
        "selectedRowBackgroundColor": TOKENS["grid-selected-bg"],
        "rowHeight": 44,
        "cellHorizontalPadding": 14,
        # Drives the (suppressed) cell focus border in AG Grid 34; transparent as a backstop.
        "rangeSelectionBorderColor": "transparent",
        "borderColor": TOKENS["border"],
        "wrapperBorderRadius": 10,
        "fontFamily": TOKENS["font-sans"],
        "fontSize": 13,
    }


def get_aggrid_custom_css() -> dict:
    """Custom CSS rules injected into the st_aggrid iframe for v4 calm styling."""
    t = TOKENS
    return {
        ".ag-root-wrapper": {
            "border": f"1px solid {t['border']} !important",
            "border-radius": f"{t['radius-card']} !important",
            "background-color": f"{t['surface']} !important",
            "box-shadow": "none !important",
            "font-family": f"{t['font-sans']} !important",
        },
        ".ag-header": {
            "background-color": f"{t['grid-header-bg']} !important",
            "border-bottom": f"1px solid {t['border']} !important",
        },
        ".ag-header-cell-text": {
            "color": f"{t['grid-header-fg']} !important",
            "font-weight": "500 !important",
            "font-size": "12.5px !important",
        },
        ".ag-row": {
            "background-color": f"{t['surface']} !important",
            "border-bottom-color": f"{t['border']} !important",
            "color": f"{t['ink']} !important",
            "font-size": "13px !important",
        },
        ".ag-row-hover:not(.ag-row-selected)": {
            "background-color": f"{t['grid-hover-bg']} !important",
        },
        ".ag-row-selected, .ag-row-selected.ag-row-hover": {
            "background-color": f"{t['grid-selected-bg']} !important",
        },
        # 3px plum accent on the selected row's left edge (inset shadow on the first cell,
        # so it survives AG Grid's own ::before selection layer).
        ".ag-row-selected .ag-cell[col-id='id']": {
            "box-shadow": f"inset 3px 0 0 {t['plum']} !important",
        },
        ".ag-row-selected .aa-merchant": {
            "font-weight": "500 !important",
        },
        # Muted text fails 4.5:1 on the tints; step it up to secondary inside tinted rows.
        ".ag-row-selected .aa-muted, .ag-row-hover .aa-muted, .ag-row-selected .aa-more-chip, .ag-row-hover .aa-more-chip": {
            "color": f"{t['text-secondary']} !important",
        },
        ".ag-cell": {
            "color": f"{t['ink']} !important",
            "white-space": "nowrap !important",
            "overflow": "hidden !important",
            "text-overflow": "ellipsis !important",
        },
        # No cell focus box anywhere (suppressCellFocus also stops AG Grid adding the class).
        ".ag-cell-focus, .ag-cell-focus:not(.ag-cell-range-selected):focus-within, .ag-cell:focus, .ag-cell:focus-visible, .ag-cell:focus-within": {
            "border-color": "transparent !important",
            "outline": "none !important",
            "box-shadow": "none !important",
        },
        ".ag-row-selected .ag-cell[col-id='id'].ag-cell-focus, .ag-row-selected .ag-cell[col-id='id']:focus-within": {
            "box-shadow": f"inset 3px 0 0 {t['plum']} !important",
        },
        ".aa-muted": {
            "color": f"{t['text-muted']} !important",
        },
        ".aa-reason-line": {
            "display": "flex !important",
            "align-items": "center !important",
            "gap": "8px !important",
            "min-width": "0 !important",
            "height": "100% !important",
        },
        ".aa-reason-first": {
            "flex": "0 1 auto !important",
            "min-width": "0 !important",
            "overflow": "hidden !important",
            "text-overflow": "ellipsis !important",
            "white-space": "nowrap !important",
        },
        ".aa-more-chip": {
            "flex": "0 0 auto !important",
            "padding": "1px 7px !important",
            "border-radius": "9999px !important",
            "background-color": f"{t['surface-muted']} !important",
            "border": f"1px solid {t['border']} !important",
            "color": f"{t['text-muted']} !important",
            "font-size": "11.5px !important",
            "line-height": "18px !important",
        },
        ".ag-floating-bottom .ag-row, .ag-floating-bottom .ag-row-hover": {
            "background-color": f"{t['grid-header-bg']} !important",
        },
        ".aa-total-label": {
            "color": f"{t['text-secondary']} !important",
            "font-weight": "500 !important",
        },
        ".ag-tooltip": {
            "white-space": "pre-line !important",
            "max-width": "420px !important",
            "font-size": "12.5px !important",
            "line-height": "1.5 !important",
        },
        ".ag-cell[col-id='status']": {
            "display": "flex !important",
            "align-items": "center !important",
        },
        ".aa-pill": {
            "line-height": "18px !important",
            "display": "inline-flex !important",
            "align-items": "center !important",
            "padding": "2px 8px !important",
            "border-radius": "9999px !important",
            "font-size": "11.5px !important",
            "font-weight": "500 !important",
        },
        ".aa-pill.approved, .aa-pill.executed": {
            "background-color": f"{t['approved-bg']} !important",
            "color": f"{t['approved-fg']} !important",
            "border": f"1px solid {t['approved-border']} !important",
        },
        ".aa-pill.pending": {
            "background-color": f"{t['pending-bg']} !important",
            "color": f"{t['pending-fg']} !important",
            "border": f"1px solid {t['pending-border']} !important",
        },
        ".aa-pill.blocked": {
            "background-color": f"{t['blocked-bg']} !important",
            "color": f"{t['blocked-fg']} !important",
            "border": f"1px solid {t['blocked-border']} !important",
        },
        ".aa-pill.rejected": {
            "background-color": f"{t['rejected-bg']} !important",
            "color": f"{t['rejected-fg']} !important",
            "border": f"1px solid {t['rejected-border']} !important",
        },
        ".aa-amount": {
            "font-variant-numeric": "tabular-nums !important",
            "font-weight": "500 !important",
        },
        ".aa-mono": {
            "font-family": f"{t['font-mono']} !important",
            "font-size": "12px !important",
            "color": f"{t['text-secondary']} !important",
        },
    }


# ==============================================================================
# 12. GLOBAL CSS INJECTION (V4 Calm Neutral Stylesheet)
# ==============================================================================
def inject_custom_css() -> None:
    """Inject the v4 calm, white, low-saturation stylesheet derived from TOKENS."""
    t = TOKENS
    css = f"""
<style>
:root {{
    --aa-canvas: {t["canvas"]};
    --aa-surface: {t["surface"]};
    --aa-border: {t["border"]};
    --aa-border-strong: {t["border-strong"]};
    --aa-divider: {t["divider"]};
    --aa-ink: {t["ink"]};
    --aa-text: {t["ink"]};
    --aa-text-heading: {t["ink"]};
    --aa-text-secondary: {t["text-secondary"]};
    --aa-text-soft: {t["text-secondary"]};
    --aa-text-muted: {t["text-muted"]};
    --aa-plum: {t["plum"]};
    --aa-nav-active-bg: {t["nav-active-bg"]};
    --aa-focus-ring: {t["focus-ring"]};
    --aa-approved-fg: {t["approved-fg"]};
    --aa-pending-solid: {t["pending-bar"]};
    --aa-blocked-fg: {t["blocked-fg"]};
    --aa-rejected-solid: {t["rejected-bar"]};
    --aa-radius-card: {t["radius-card"]};
    --aa-radius-input: {t["radius-input"]};
    --aa-radius-button: {t["radius-button"]};
    --aa-radius-pill: {t["radius-pill"]};
    --aa-shadow: {t["shadow"]};
    --aa-font-sans: {t["font-sans"]};
    --aa-font-mono: {t["font-mono"]};
}}

/* Global typography & canvas — system font stack, weights 400 and 500 only */
html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {{
    background: {t["canvas"]} !important;
    color: {t["ink"]} !important;
    font-family: {t["font-sans"]} !important;
    font-size: 14px !important;
    font-weight: 400 !important;
    -webkit-font-smoothing: antialiased;
}}

/* Enforce two weights only (400 and 500) across headings and strong text */
h1, h2, h3, h4, h5, h6, strong, b, th, label {{
    font-family: {t["font-sans"]} !important;
    font-weight: 500 !important;
    color: {t["ink"]};
}}

/* Transparent Streamlit chrome header */
header[data-testid="stHeader"] {{
    background: transparent !important;
    height: 1.5rem !important;
}}

/* Tight top container so main content starts well within top third of 1440x900 */
[data-testid="stMainBlockContainer"], .main .block-container {{
    max-width: 1120px !important;
    padding-top: 0.5rem !important;
    padding-bottom: 5.5rem !important;
    padding-left: 1.75rem !important;
    padding-right: 1.75rem !important;
}}

/* ==========================================================================
   SIDEBAR: White (#FFFFFF), 1px right border (#E7E5E4), readable ink text
   ========================================================================== */
[data-testid="stSidebar"] {{
    background: {t["surface"]} !important;
    border-right: 1px solid {t["border"]} !important;
}}

[data-testid="stSidebar"] > div:first-child {{
    background: {t["surface"]} !important;
}}

/* Native Streamlit sidebar nav items: active has #F3EFF5 bg and plum #4A3357 text, inactive #57534E */
[data-testid="stSidebarNav"] a {{
    border-radius: {t["radius-button"]} !important;
    padding: 0.42rem 0.65rem !important;
    margin: 2px 0 !important;
    color: {t["text-secondary"]} !important;
    font-weight: 400 !important;
    transition: background-color 150ms ease !important;
}}

[data-testid="stSidebarNav"] a span {{
    color: {t["text-secondary"]} !important;
    font-size: 13.5px !important;
    font-weight: 400 !important;
}}

[data-testid="stSidebarNav"] a:hover {{
    background: {t["secondary-hover"]} !important;
}}

[data-testid="stSidebarNav"] a[aria-current="page"] {{
    background: {t["nav-active-bg"]} !important;
    color: {t["plum"]} !important;
    font-weight: 500 !important;
}}

[data-testid="stSidebarNav"] a[aria-current="page"] span {{
    color: {t["plum"]} !important;
    font-weight: 500 !important;
}}

.aa-side-label {{
    font-size: 12px;
    font-weight: 500;
    color: {t["text-secondary"]};
    margin: 4px 0 4px 0;
}}

.aa-side-note {{
    font-size: 12px;
    color: {t["text-secondary"]};
    background: {t["canvas"]};
    border: 1px solid {t["border"]};
    border-radius: 8px;
    padding: 8px 10px;
    margin-top: 6px;
}}

[data-testid="stSidebar"] [data-testid="stExpander"] {{
    background: {t["surface"]} !important;
    border: 1px solid {t["border"]} !important;
    border-radius: {t["radius-card"]} !important;
    box-shadow: none !important;
}}

[data-testid="stSidebar"] [data-testid="stExpander"] summary p {{
    color: {t["text-secondary"]} !important;
    font-size: 13px !important;
    font-weight: 500 !important;
}}

/* Sidebar Gemini quota block */
.aa-sidebar-quota {{
    margin: 8px 0 6px 0;
    padding: 8px 10px;
    background: {t["canvas"]};
    border: 1px solid {t["border"]};
    border-radius: 8px;
}}

.aa-sidebar-quota-row {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-size: 12px;
    color: {t["text-secondary"]};
    margin-bottom: 6px;
}}

.aa-sidebar-quota-val {{
    font-weight: 500;
    color: {t["ink"]};
    font-variant-numeric: tabular-nums;
}}

.aa-sidebar-quota-track {{
    width: 100%;
    height: 4px;
    background: {t["divider"]};
    border-radius: 9999px;
    overflow: hidden;
}}

.aa-sidebar-quota-fill {{
    height: 100%;
    background: {t["ink"]};
    border-radius: 9999px;
}}

.aa-side-alert {{
    margin-top: 8px;
    padding: 9px 11px;
    background: {t["surface"]};
    border: 1px solid {t["border"]};
    border-left: 3px solid {t["blocked-bar"]};
    border-radius: 8px;
    font-size: 12px;
    color: {t["text-secondary"]};
}}

.aa-side-alert-title {{
    display: flex;
    align-items: center;
    gap: 6px;
    font-weight: 500;
    color: {t["ink"]};
    margin-bottom: 4px;
}}

/* Sidebar PayPal status footer */
.aa-side-status {{
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 2px 4px 2px;
    margin-top: 8px;
    border-top: 1px solid {t["divider"]};
    font-size: 12px;
    color: {t["text-secondary"]};
}}

.aa-sdot {{
    width: 7px;
    height: 7px;
    border-radius: 9999px;
    display: inline-block;
    flex-shrink: 0;
}}

.aa-sdot.ok {{
    background: {t["approved-bar"]};
}}

.aa-sdot.warn {{
    background: {t["pending-bar"]};
}}

/* ==========================================================================
   PAGE HEADER: Single row, 22px title on left, neutral mode pill on right
   ========================================================================== */
.aa-page-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 0 0 8px 0;
}}

.aa-page-title {{
    margin: 0 !important;
    padding: 0 !important;
    font-size: 22px !important;
    line-height: 1.25 !important;
    font-weight: 500 !important;
    color: {t["ink"]} !important;
    letter-spacing: -0.015em;
}}

.aa-section-sub {{
    font-size: 13px;
    color: {t["text-secondary"]};
    margin: -2px 0 10px 0;
}}

.aa-mode-pill {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    border-radius: {t["radius-pill"]};
    background: {t["surface"]};
    border: 1px solid {t["border"]};
    color: {t["text-secondary"]};
    font-size: 12px;
    font-weight: 500;
}}

.aa-mode-dot {{
    width: 6px;
    height: 6px;
    border-radius: 9999px;
}}

.aa-mode-dot-live {{
    background: {t["approved-bar"]};
}}

.aa-mode-dot-mock {{
    background: {t["text-muted"]};
}}

/* ==========================================================================
   SUMMARY STRIP ON EVERY PAGE
   ========================================================================== */
.aa-summary-strip {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 12px 20px;
    background: {t["surface"]};
    border: 1px solid {t["border"]};
    border-radius: {t["radius-card"]};
    padding: 9px 14px;
    margin-bottom: 10px;
    box-shadow: {t["shadow"]};
}}

.aa-summary-spend {{
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
}}

.aa-summary-label {{
    font-size: 13px;
    color: {t["text-secondary"]};
    font-weight: 400;
}}

.aa-summary-money {{
    font-size: 14px;
    font-weight: 500;
    color: {t["ink"]};
    font-variant-numeric: tabular-nums;
}}

.aa-summary-cap {{
    color: {t["text-secondary"]};
    font-weight: 400;
}}

.aa-summary-bar-track {{
    width: 120px;
    height: 4px;
    background: {t["gauge-track"]};
    border-radius: 9999px;
    overflow: hidden;
}}

.aa-summary-bar-fill {{
    height: 100%;
    border-radius: 9999px;
    transition: width 150ms ease;
}}

.aa-summary-counts {{
    display: flex;
    align-items: center;
    gap: 16px;
    flex-wrap: wrap;
}}

.aa-summary-stat {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    color: {t["text-secondary"]};
}}

.aa-summary-dot {{
    width: 7px;
    height: 7px;
    border-radius: 9999px;
    display: inline-block;
}}

.aa-summary-stat-val {{
    font-weight: 500;
    color: {t["ink"]};
    font-variant-numeric: tabular-nums;
}}

/* ==========================================================================
   PIPELINE STRIP (Agent -> Rules -> PayPal)
   ========================================================================== */
.aa-pipeline-strip {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    background: {t["surface"]};
    border: 1px solid {t["border"]};
    border-radius: {t["radius-card"]};
    padding: 8px 14px;
    margin-bottom: 10px;
    box-shadow: {t["shadow"]};
}}

.aa-pipe-node {{
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
}}

.aa-pipe-tile {{
    width: 24px;
    height: 24px;
    border-radius: 6px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: {t["canvas"]};
    border: 1px solid {t["border"]};
    color: {t["text-secondary"]};
    flex-shrink: 0;
}}

.aa-pipe-meta {{
    display: flex;
    flex-direction: column;
    line-height: 1.2;
}}

.aa-pipe-title {{
    font-size: 13px;
    font-weight: 500;
    color: {t["ink"]};
}}

.aa-pipe-sub {{
    font-size: 11.5px;
    color: {t["text-muted"]};
}}

/* Lit pipeline node states */
.aa-pipe-active .aa-pipe-tile,
.aa-pipe-done .aa-pipe-tile {{
    background: {t["nav-active-bg"]};
    border-color: {t["border-strong"]};
    color: {t["plum"]};
}}

.aa-pipe-approved .aa-pipe-tile {{
    background: {t["approved-bg"]};
    border-color: {t["approved-border"]};
    color: {t["approved-fg"]};
}}

.aa-pipe-approved .aa-pipe-sub {{
    color: {t["approved-fg"]};
}}

.aa-pipe-pending .aa-pipe-tile {{
    background: {t["pending-bg"]};
    border-color: {t["pending-border"]};
    color: {t["pending-fg"]};
}}

.aa-pipe-pending .aa-pipe-sub {{
    color: {t["pending-fg"]};
}}

.aa-pipe-blocked .aa-pipe-tile,
.aa-pipe-error .aa-pipe-tile {{
    background: {t["blocked-bg"]};
    border-color: {t["blocked-border"]};
    color: {t["blocked-fg"]};
}}

.aa-pipe-blocked .aa-pipe-sub,
.aa-pipe-error .aa-pipe-sub {{
    color: {t["blocked-fg"]};
}}

.aa-pipe-dark .aa-pipe-tile {{
    background: {t["canvas"]};
    border: 1px dashed {t["border-strong"]};
    color: {t["text-muted"]};
}}

.aa-pipe-conn {{
    flex: 1;
    height: 1px;
    background: {t["border"]};
    min-width: 18px;
}}

.aa-pipe-conn-lit {{
    background: {t["approved-bar"]};
}}

.aa-pipe-conn-active {{
    background: {t["plum"]};
}}

.aa-pipe-conn-dashed {{
    background: transparent;
    border-top: 1px dashed {t["border-strong"]};
    height: 0;
}}

/* ==========================================================================
   STATUS PILLS & DOTS
   ========================================================================== */
.aa-pill {{
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 3px 9px;
    border-radius: {t["radius-pill"]};
    font-size: 12px;
    font-weight: 500;
    line-height: 1.3;
    white-space: nowrap;
}}

.aa-pill.approved, .aa-pill.executed,
.aa-pill-approved, .aa-pill-executed {{
    background: {t["approved-bg"]};
    color: {t["approved-fg"]};
    border: 1px solid {t["approved-border"]};
}}

.aa-pill.pending, .aa-pill-pending {{
    background: {t["pending-bg"]};
    color: {t["pending-fg"]};
    border: 1px solid {t["pending-border"]};
}}

.aa-pill.blocked, .aa-pill-blocked {{
    background: {t["blocked-bg"]};
    color: {t["blocked-fg"]};
    border: 1px solid {t["blocked-border"]};
}}

.aa-pill.rejected, .aa-pill-rejected {{
    background: {t["rejected-bg"]};
    color: {t["rejected-fg"]};
    border: 1px solid {t["rejected-border"]};
}}

.aa-dot-approved, .aa-dot-executed {{ background: {t["approved-dot"]}; }}
.aa-dot-pending {{ background: {t["pending-dot"]}; }}
.aa-dot-blocked {{ background: {t["blocked-dot"]}; }}
.aa-dot-rejected {{ background: {t["rejected-dot"]}; }}

/* ==========================================================================
   DECISION CARD (Chat & Approvals)
   ========================================================================== */
.aa-decision-card {{
    position: relative;
    display: flex;
    background: {t["surface"]};
    border: 1px solid {t["border"]};
    border-radius: {t["radius-card"]};
    overflow: hidden;
    box-shadow: {t["shadow"]};
    margin: 6px 0 12px 0;
}}

.aa-decision-accent {{
    width: 3px;
    flex-shrink: 0;
}}

.aa-accent-approved, .aa-accent-executed {{ background: {t["approved-bar"]}; }}
.aa-accent-pending {{ background: {t["pending-bar"]}; }}
.aa-accent-blocked {{ background: {t["blocked-bar"]}; }}
.aa-accent-rejected {{ background: {t["rejected-bar"]}; }}

.aa-decision-inner {{
    flex: 1;
    min-width: 0;
    padding: 14px 16px;
}}

.aa-decision-head {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
}}

.aa-decision-head-left {{
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 0;
}}

.aa-decision-icon {{
    width: 28px;
    height: 28px;
    border-radius: 7px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
}}

.aa-icon-approved, .aa-icon-executed {{
    background: {t["approved-bg"]};
    border: 1px solid {t["approved-border"]};
    color: {t["approved-fg"]};
}}

.aa-icon-pending {{
    background: {t["pending-bg"]};
    border: 1px solid {t["pending-border"]};
    color: {t["pending-fg"]};
}}

.aa-icon-blocked {{
    background: {t["blocked-bg"]};
    border: 1px solid {t["blocked-border"]};
    color: {t["blocked-fg"]};
}}

.aa-icon-rejected {{
    background: {t["rejected-bg"]};
    border: 1px solid {t["rejected-border"]};
    color: {t["rejected-fg"]};
}}

.aa-decision-title {{
    font-size: 14px;
    font-weight: 500;
    color: {t["ink"]};
    line-height: 1.25;
}}

.aa-decision-sub {{
    font-size: 12px;
    color: {t["text-secondary"]};
    line-height: 1.3;
}}

.aa-decision-divider {{
    height: 1px;
    background: {t["divider"]};
    margin: 11px 0;
}}

.aa-decision-deal {{
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 8px;
}}

.aa-decision-merchant-wrap {{
    display: flex;
    align-items: baseline;
    gap: 8px;
    flex-wrap: wrap;
    min-width: 0;
}}

.aa-decision-merchant {{
    font-size: 14px;
    font-weight: 500;
    color: {t["ink"]};
    overflow-wrap: anywhere;
}}

.aa-decision-cat {{
    font-size: 12px;
    color: {t["text-secondary"]};
    background: {t["canvas"]};
    border: 1px solid {t["border"]};
    padding: 1px 7px;
    border-radius: 9999px;
    overflow-wrap: anywhere;
}}

.aa-decision-amount {{
    font-size: 20px;
    font-weight: 500;
    color: {t["ink"]};
    font-variant-numeric: tabular-nums;
}}

.aa-verdict-reasons {{
    list-style: none;
    padding: 0;
    margin: 0 0 10px 0;
    display: flex;
    flex-direction: column;
    gap: 5px;
}}

.aa-verdict-reasons li {{
    display: flex;
    align-items: flex-start;
    gap: 8px;
    font-size: 13px;
    color: {t["ink"]};
    line-height: 1.45;
    overflow-wrap: anywhere;
}}

.aa-reason-dot {{
    width: 6px;
    height: 6px;
    border-radius: 9999px;
    margin-top: 6px;
    flex-shrink: 0;
}}

.aa-verdict-order-meta {{
    font-size: 12px;
    color: {t["text-secondary"]};
    margin-bottom: 8px;
}}

.aa-mono {{
    background: {t["rejected-bg"]};
    color: {t["ink"]};
    padding: 1px 5px;
    border-radius: 4px;
    font-family: {t["font-mono"]};
    font-size: 11.5px;
}}

.aa-decision-next {{
    display: flex;
    align-items: flex-start;
    gap: 6px;
    font-size: 12.5px;
    color: {t["text-secondary"]};
    line-height: 1.45;
    padding-top: 8px;
    border-top: 1px solid {t["divider"]};
    margin-bottom: 0;
}}

.aa-decision-next-icon {{
    color: {t["text-muted"]};
    margin-top: 1px;
    flex-shrink: 0;
}}

/* ==========================================================================
   CHAT BUBBLES & EMPTY STATE
   ========================================================================== */
[data-testid="stChatMessage"] {{
    background: transparent !important;
    border: none !important;
    padding: 0.25rem 0 !important;
}}

.aa-user-msg {{
    display: inline-block;
    max-width: 76%;
    background: {t["user-bubble-bg"]};
    color: {t["user-bubble-fg"]};
    border-radius: {t["radius-card"]};
    padding: 9px 13px;
    font-size: 13.5px;
    line-height: 1.45;
    overflow-wrap: anywhere;
}}

/* Agent has NO bubble: plain text next to small neutral avatar tile, never clipped */
.aa-agent-msg {{
    background: transparent;
    color: {t["ink"]};
    font-size: 13.5px;
    line-height: 1.5;
    padding: 2px 0;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
}}

/* Empty state & quiet scenario cards */
.aa-empty-state {{
    text-align: center;
    padding: 8px 12px 6px 12px;
}}

.aa-empty-illus {{
    display: inline-flex;
    margin-bottom: 4px;
}}

.aa-empty-title {{
    font-size: 14px;
    font-weight: 500;
    color: {t["ink"]};
    margin-bottom: 2px;
}}

.aa-empty-sub {{
    font-size: 13px;
    color: {t["text-secondary"]};
}}

.aa-card-head {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 6px;
}}

.aa-icon-chip {{
    width: 24px;
    height: 24px;
    border-radius: 6px;
    border: 1px solid {t["border"]};
    background: {t["canvas"]};
    color: {t["text-secondary"]};
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
}}

.aa-card-title {{
    font-size: 14px;
    font-weight: 500;
    color: {t["ink"]};
    margin-bottom: 2px;
}}

.aa-card-desc {{
    font-size: 13px;
    color: {t["text-secondary"]};
    line-height: 1.4;
    margin-bottom: 8px;
}}

[class*="st-key-aa-card-scn-"] .aa-card-desc {{
    min-height: 36px;
}}

.aa-label {{
    font-size: 12px;
    font-weight: 500;
    color: {t["text-secondary"]};
    margin-bottom: 3px;
}}

/* Rules & Ledger helpers */
.aa-field-error {{
    font-size: 12.5px;
    color: {t["blocked-fg"]};
    background: {t["blocked-bg"]};
    border: 1px solid {t["blocked-border"]};
    border-radius: 7px;
    padding: 5px 9px;
    margin-top: 4px;
    margin-bottom: 6px;
}}

.aa-field-hint {{
    font-size: 12.5px;
    color: {t["pending-fg"]};
    background: {t["pending-bg"]};
    border: 1px solid {t["pending-border"]};
    border-radius: 7px;
    padding: 6px 10px;
    margin-top: 6px;
}}

.aa-chips {{
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 4px;
}}

.aa-chip {{
    font-size: 12px;
    color: {t["ink"]};
    background: {t["canvas"]};
    border: 1px solid {t["border"]};
    border-radius: 9999px;
    padding: 2px 9px;
}}

.aa-saved {{
    display: inline-flex;
    align-items: center;
    gap: 5px;
    font-size: 12.5px;
    color: {t["approved-fg"]};
}}

.aa-dirty {{
    font-size: 12.5px;
    color: {t["pending-fg"]};
}}

.aa-kv {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 10px 16px;
    margin: 10px 0;
    padding: 10px 0;
    border-top: 1px solid {t["divider"]};
    border-bottom: 1px solid {t["divider"]};
}}

.aa-v {{
    font-size: 13.5px;
    color: {t["ink"]};
    overflow-wrap: anywhere;
}}

.aa-v.big {{
    font-size: 20px;
    font-weight: 500;
    font-variant-numeric: tabular-nums;
}}

/* Bottom spacer on chat page so fixed input never clips last card */
.aa-chat-bottom-spacer {{
    height: 76px;
}}

/* ==========================================================================
   BUTTONS, INPUTS, CARDS & MOTION
   ========================================================================== */
[data-testid="stBaseButton-primary"],
[data-testid="stFormSubmitButton"] button {{
    background: {t["primary-bg"]} !important;
    color: {t["primary-text"]} !important;
    border: 1px solid {t["primary-bg"]} !important;
    border-radius: {t["radius-button"]} !important;
    font-weight: 500 !important;
    font-size: 13px !important;
    padding: 0.42rem 0.85rem !important;
    box-shadow: none !important;
    transition: background-color 150ms ease, border-color 150ms ease !important;
}}

[data-testid="stBaseButton-primary"] p,
[data-testid="stBaseButton-primary"] span,
[data-testid="stFormSubmitButton"] button p,
[data-testid="stFormSubmitButton"] button span {{
    color: {t["primary-text"]} !important;
    font-weight: 500 !important;
}}

[data-testid="stBaseButton-primary"]:hover,
[data-testid="stFormSubmitButton"] button:hover {{
    background: {t["primary-hover"]} !important;
    border-color: {t["primary-hover"]} !important;
}}

[data-testid="stBaseButton-secondary"],
[data-testid="stDownloadButton"] button,
[data-testid="stLinkButton"] a,
[data-testid="stPageLink-NavLink"] {{
    background: {t["secondary-bg"]} !important;
    color: {t["secondary-text"]} !important;
    border: 1px solid {t["secondary-border"]} !important;
    border-radius: {t["radius-button"]} !important;
    font-weight: 500 !important;
    font-size: 13px !important;
    padding: 0.42rem 0.85rem !important;
    box-shadow: none !important;
    transition: background-color 150ms ease, border-color 150ms ease !important;
}}

[data-testid="stBaseButton-secondary"]:hover,
[data-testid="stDownloadButton"] button:hover,
[data-testid="stLinkButton"] a:hover,
[data-testid="stPageLink-NavLink"]:hover {{
    background: {t["secondary-hover"]} !important;
    color: {t["ink"]} !important;
}}

/* Inputs & Chat Input: white fill, 1px #D6D3D1 border, plum focus ring */
[data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input,
[data-testid="stTextArea"] textarea {{
    background: {t["surface"]} !important;
    color: {t["ink"]} !important;
    border: 1px solid {t["border-strong"]} !important;
    border-radius: {t["radius-input"]} !important;
    font-size: 13.5px !important;
}}

[data-testid="stTextInput"] input:focus,
[data-testid="stNumberInput"] input:focus,
[data-testid="stTextArea"] textarea:focus {{
    border-color: {t["plum"]} !important;
    box-shadow: 0 0 0 2px {t["focus-ring"]} !important;
    outline: none !important;
}}

/* Ledger search: ONE border on Streamlit's root element, icon inside, 36px text inset */
.st-key-aa_ledger_q [data-testid="stTextInputRootElement"] {{
    position: relative !important;
    background: {t["surface"]} !important;
    border: 1px solid {t["border-strong"]} !important;
    border-radius: {t["radius-input"]} !important;
    box-shadow: none !important;
    overflow: hidden !important;
}}
.st-key-aa_ledger_q [data-testid="stTextInputRootElement"]:focus-within {{
    border-color: {t["plum"]} !important;
    box-shadow: 0 0 0 2px {t["focus-ring"]} !important;
}}
.st-key-aa_ledger_q [data-testid="stTextInputRootElement"] > div:has([data-testid="stTextInputIcon"]) {{
    position: absolute !important;
    left: 12px !important;
    top: 50% !important;
    transform: translateY(-50%) !important;
    margin: 0 !important;
    padding: 0 !important;
    display: flex !important;
    align-items: center !important;
    pointer-events: none !important;
    z-index: 1 !important;
    color: {t["text-muted"]} !important;
}}
.st-key-aa_ledger_q [data-testid="stTextInput"] input,
.st-key-aa_ledger_q [data-testid="stTextInput"] input:focus {{
    border: none !important;
    box-shadow: none !important;
    outline: none !important;
    background: transparent !important;
    border-radius: 0 !important;
    padding-left: 36px !important;
    text-overflow: ellipsis !important;
}}

/* Ledger controls row: tighter content-width buttons so the row fits from ~1100px up */
.st-key-aa-ledger-controls {{
    flex-wrap: nowrap !important;
}}
.st-key-aa-ledger-controls [data-testid^="stBaseButton"],
.st-key-aa-ledger-controls [data-testid="stDownloadButton"] button {{
    padding-left: 12px !important;
    padding-right: 12px !important;
    white-space: nowrap !important;
}}
.st-key-aa-ledger-controls .st-key-aa_ledger_q {{
    flex: 1 1 180px !important;
    min-width: 160px !important;
}}

/* Ledger detail slot (above the grid): selected card or one muted line */
[class*="st-key-aa-ledger-card-"] {{
    background: {t["surface"]} !important;
    border: 1px solid {t["border"]} !important;
    border-left-width: 3px !important;
    border-radius: {t["radius-card"]} !important;
    padding: 8px 16px 12px 16px !important;
    gap: 4px !important;
    box-shadow: {t["shadow"]} !important;
}}
.st-key-aa-ledger-card-approved, .st-key-aa-ledger-card-executed {{ border-left-color: {t["approved-bar"]} !important; }}
.st-key-aa-ledger-card-pending {{ border-left-color: {t["pending-bar"]} !important; }}
.st-key-aa-ledger-card-blocked {{ border-left-color: {t["blocked-bar"]} !important; }}
.st-key-aa-ledger-card-rejected {{ border-left-color: {t["rejected-bar"]} !important; }}
.st-key-aa-ledger-card-empty {{
    border: 1px dashed {t["border-strong"]} !important;
    border-left-width: 1px !important;
    box-shadow: none !important;
    background: transparent !important;
    min-height: 50px !important;
    padding: 0 16px !important;
    justify-content: center !important;
}}
[class*="st-key-aa-ledger-card-"] [data-testid="stMarkdownContainer"] {{
    margin-bottom: 0 !important;
}}
.aa-ledger-detail-empty-line {{
    color: {t["text-muted"]};
    font-size: 13px;
}}
.aa-ledger-detail-title {{
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 0;
    font-size: 14px;
    font-weight: 500;
    color: {t["ink"]};
}}
.aa-ledger-detail-title > span:first-child {{
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}}
.aa-ledger-detail-body {{
    border-top: 1px solid {t["divider"]};
    padding-top: 10px;
}}
.aa-ledger-detail-body .aa-kv {{
    margin-bottom: 8px;
}}

[data-testid="stChatInput"] > div {{
    background: {t["surface"]} !important;
    border: 1px solid {t["border-strong"]} !important;
    border-radius: {t["radius-input"]} !important;
    box-shadow: {t["shadow"]} !important;
}}

[data-testid="stChatInput"] > div:focus-within {{
    border-color: {t["plum"]} !important;
    box-shadow: 0 0 0 2px {t["focus-ring"]} !important;
}}

[data-testid="stChatInput"] textarea {{
    color: {t["ink"]} !important;
    font-size: 13.5px !important;
}}

[data-testid="stChatInputSubmitButton"] {{
    background: {t["ink"]} !important;
    color: {t["surface"]} !important;
    border-radius: 6px !important;
}}

[data-testid="stChatInputSubmitButton"] svg {{
    fill: {t["surface"]} !important;
}}

/* Bordered containers (Scenario, Rules & Ledger cards) */
[data-testid="stVerticalBlockBorderWrapper"] {{
    background: {t["surface"]} !important;
    border: 1px solid {t["border"]} !important;
    border-radius: {t["radius-card"]} !important;
    box-shadow: {t["shadow"]} !important;
}}

/* Quota notice */
.aa-quota-banner {{
    display: flex;
    align-items: center;
    gap: 8px;
    background: {t["pending-bg"]};
    border: 1px solid {t["pending-border"]};
    color: {t["pending-fg"]};
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 12.5px;
    margin-bottom: 10px;
}}

.aa-quota-dot {{
    width: 6px;
    height: 6px;
    border-radius: 9999px;
    background: {t["pending-dot"]};
    flex-shrink: 0;
}}

/* Respect prefers-reduced-motion */
@media (prefers-reduced-motion: reduce) {{
    *, *::before, *::after {{
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.01ms !important;
        scroll-behavior: auto !important;
    }}
}}
</style>
"""
    st.markdown(css, unsafe_allow_html=True)


# Alias expected by app.py
inject_theme_css = inject_custom_css
