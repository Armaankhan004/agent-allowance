"""
Unit tests for the non-UI helpers and Redesign v4 Calm Neutral palette/contrast design tokens in ui_theme:
- v4 Calm Neutral palette presence in TOKENS and synchronization with .streamlit/config.toml
- WCAG 2.1 contrast ratios (>= 4.5:1) for key text/background and status pill token pairs:
  ink on canvas, secondary on canvas and white, muted on canvas and white,
  white on primary button, plum on #F3EFF5, every status text on its pill background,
  and ink on the #F5F5F4 grid header
- gauge_level / gauge_color: summary bar percent-to-color thresholds (ink < 75%, amber <= 90%, red > 90%)
- pipeline_states: Agent -> Rules -> PayPal node states for each run phase
- AG Grid theme params generated from TOKENS (#F5F5F4 header with ink 500-weight text, white rows, #E7E5E4 borders, #E9E3EE selected row, #F3EFF5 hover)
- No stray old violet, peach, cream, blue, indigo, or slate hex values in app.py, ui_theme.py, assets, or config.toml
"""

from pathlib import Path
import re
import tomllib
import pytest

from agent_allowance.ui_theme import (
    APPROVALS_EMPTY_ILLUSTRATION,
    LEDGER_EMPTY_ILLUSTRATION,
    STATUS_META,
    TOKENS,
    WELCOME_ILLUSTRATION,
    contrast_ratio,
    gauge_color,
    gauge_level,
    get_aggrid_theme_params,
    pipeline_states,
    relative_luminance,
)

ROOT = Path(__file__).resolve().parent.parent

EXPECTED_V4_TOKENS = {
    "canvas": "#FAFAF9",
    "surface": "#FFFFFF",
    "border": "#E7E5E4",
    "border-strong": "#D6D3D1",
    "divider": "#F0EFED",
    "ink": "#1C1917",
    "text-secondary": "#57534E",
    "text-muted": "#78716C",
    "primary-bg": "#1C1917",
    "primary-hover": "#292524",
    "primary-text": "#FFFFFF",
    "secondary-bg": "#FFFFFF",
    "secondary-border": "#D6D3D1",
    "secondary-text": "#44403C",
    "secondary-hover": "#F5F5F4",
    "plum": "#4A3357",
    "nav-active-bg": "#F3EFF5",
    "focus-ring-hex": "#6B4C7A",
    "focus-ring": "rgba(107, 76, 122, 0.35)",
    "approved-fg": "#2F6B3E",
    "approved-bg": "#EEF5EF",
    "approved-border": "#CFE3D3",
    "approved-bar": "#5E9B6E",
    "pending-fg": "#8A5A12",
    "pending-bg": "#FBF5E8",
    "pending-border": "#EBDDBB",
    "pending-bar": "#C79A3B",
    "blocked-fg": "#9B3B3B",
    "blocked-bg": "#FBEFEF",
    "blocked-border": "#EBCFCF",
    "blocked-bar": "#B45454",
    "rejected-fg": "#57534E",
    "rejected-bg": "#F5F5F4",
    "rejected-border": "#E7E5E4",
    "rejected-bar": "#A8A29E",
    "executed-fg": "#2F6B3E",
    "executed-bg": "#EEF5EF",
    "executed-border": "#CFE3D3",
    "executed-bar": "#5E9B6E",
    "grid-header-bg": "#F5F5F4",
    "grid-header-fg": "#1C1917",
    "grid-selected-bg": "#E9E3EE",
    "grid-hover-bg": "#F3EFF5",
    "user-bubble-bg": "#F1F0EE",
    "user-bubble-fg": "#1C1917",
}


def test_tokens_contains_v4_palette():
    """TOKENS defines the v4 Calm Neutral palette values and assigns them to semantic roles."""
    for key, expected_val in EXPECTED_V4_TOKENS.items():
        assert TOKENS.get(key) == expected_val, f"TOKENS[{key!r}] expected {expected_val!r}, got {TOKENS.get(key)!r}"

    assert TOKENS["radius-card"] == "10px"
    assert TOKENS["radius-input"] == "10px"
    assert TOKENS["radius-button"] == "7px"
    assert TOKENS["radius-pill"] == "9999px"
    assert TOKENS["gauge-ok"] == "#1C1917"
    assert TOKENS["gauge-warn"] == "#C79A3B"
    assert TOKENS["gauge-danger"] == "#B45454"


def test_config_toml_matches_tokens():
    """Verify .streamlit/config.toml mirrors TOKENS v4 values and keeps secondaryBackgroundColor light."""
    cfg_path = ROOT / ".streamlit" / "config.toml"
    cfg = tomllib.loads(cfg_path.read_text(encoding="utf-8"))

    theme = cfg["theme"]
    assert theme["base"] == "light"
    assert theme["primaryColor"].upper() == TOKENS["ink"].upper()
    assert theme["backgroundColor"].upper() == TOKENS["canvas"].upper()
    assert theme["secondaryBackgroundColor"].upper() == TOKENS["surface"].upper()
    assert theme["textColor"].upper() == TOKENS["ink"].upper()
    assert theme["borderColor"].upper() == TOKENS["border"].upper()
    assert theme["linkColor"].upper() == TOKENS["plum"].upper()
    assert theme["dataframeHeaderBackgroundColor"].upper() == TOKENS["grid-header-bg"].upper()
    assert theme["dataframeHeaderTextColor"].upper() == TOKENS["grid-header-fg"].upper()
    assert theme["dataframeBorderColor"].upper() == TOKENS["border"].upper()
    assert theme["baseRadius"] == "10px"
    assert theme["buttonRadius"] == "7px"
    assert theme["baseFontWeight"] == 400
    assert theme["headingFontWeights"] == 500

    # secondaryBackgroundColor must be light so input widgets stay readable
    assert relative_luminance(theme["secondaryBackgroundColor"]) > 0.85
    assert contrast_ratio(theme["textColor"], theme["secondaryBackgroundColor"]) >= 4.5

    sidebar = theme["sidebar"]
    assert sidebar["backgroundColor"].upper() == TOKENS["surface"].upper()
    assert sidebar["secondaryBackgroundColor"].upper() == TOKENS["canvas"].upper()
    assert sidebar["textColor"].upper() == TOKENS["ink"].upper()
    assert sidebar["primaryColor"].upper() == TOKENS["plum"].upper()
    assert sidebar["borderColor"].upper() == TOKENS["border"].upper()
    assert sidebar["linkColor"].upper() == TOKENS["plum"].upper()


@pytest.mark.parametrize(
    "label, fg_token, bg_token",
    [
        ("ink on canvas", "ink", "canvas"),
        ("secondary on canvas", "text-secondary", "canvas"),
        ("secondary on white", "text-secondary", "surface"),
        ("muted on canvas", "text-muted", "canvas"),
        ("muted on white", "text-muted", "surface"),
        ("white on primary button", "primary-text", "primary-bg"),
        ("white on primary button hover", "primary-text", "primary-hover"),
        ("secondary button text on white", "secondary-text", "secondary-bg"),
        ("secondary button text on hover", "secondary-text", "secondary-hover"),
        ("plum on #F3EFF5 active nav", "plum", "nav-active-bg"),
        ("plum on white", "plum", "surface"),
        ("ink on user chat bubble", "user-bubble-fg", "user-bubble-bg"),
        ("ink on #F5F5F4 grid header", "grid-header-fg", "grid-header-bg"),
        ("approved status pill", "approved-fg", "approved-bg"),
        ("pending status pill", "pending-fg", "pending-bg"),
        ("blocked status pill", "blocked-fg", "blocked-bg"),
        ("rejected status pill", "rejected-fg", "rejected-bg"),
        ("executed status pill", "executed-fg", "executed-bg"),
    ],
)
def test_wcag_contrast_ratios_at_least_aa(label, fg_token, bg_token):
    """All key text and background token pairs must meet WCAG AA (>= 4.5:1)."""
    ratio = contrast_ratio(TOKENS[fg_token], TOKENS[bg_token])
    assert ratio >= 4.5, f"{label} ({TOKENS[fg_token]} on {TOKENS[bg_token]}) is {ratio:.2f}:1 (< 4.5:1)"


def test_status_pill_all_meta_classes_meet_wcag_aa():
    for status, meta in STATUS_META.items():
        cls = meta["cls"]
        fg = TOKENS[f"{cls}-fg"]
        bg = TOKENS[f"{cls}-bg"]
        assert contrast_ratio(fg, bg) >= 4.5, f"{status} pill contrast < 4.5:1"


def test_aggrid_theme_uses_tokens():
    params = get_aggrid_theme_params()
    assert params["headerBackgroundColor"] == "#F5F5F4" == TOKENS["grid-header-bg"]
    assert params["headerTextColor"] == "#1C1917" == TOKENS["grid-header-fg"]
    assert params["headerFontWeight"] == 500
    assert params["backgroundColor"] == "#FFFFFF" == TOKENS["surface"]
    assert params["oddRowBackgroundColor"] == "#FFFFFF" == TOKENS["surface"]
    assert params["borderColor"] == "#E7E5E4" == TOKENS["border"]
    assert params["selectedRowBackgroundColor"] == "#E9E3EE" == TOKENS["grid-selected-bg"]
    assert params["rowHoverColor"] == "#F3EFF5" == TOKENS["grid-hover-bg"]
    assert params["rowHeight"] == 44
    assert contrast_ratio(params["headerTextColor"], params["headerBackgroundColor"]) >= 4.5
    assert contrast_ratio(params["foregroundColor"], params["backgroundColor"]) >= 4.5
    assert contrast_ratio(params["foregroundColor"], params["selectedRowBackgroundColor"]) >= 4.5


def test_no_old_violet_peach_blue_indigo_slate_hex_in_theme_files():
    """Verify no legacy violet, peach, cream, plum-gradient, blue, indigo, or slate hex values remain."""
    old_hexes = {
        # v1/v2 blue, indigo, slate
        "#EDF1F9", "#F5F7FC", "#E8EDF7", "#D5DCEA", "#B4C0D6", "#8A97B1",
        "#0B1220", "#1E293B", "#475569", "#5B6678", "#2563EB", "#4F46E5",
        "#1D4ED8", "#4338CA", "#E6EDFF", "#1E3A8A", "#0A1230", "#1B2A6B",
        "#D6DDFF", "#0A1128", "#141D3A", "#1F2A50", "#2A3763", "#E2E8F0",
        "#A9B6D3", "#818CF8", "#64748B", "#DBEAFE", "#E3E8F3", "#0F172A",
        "#F1F5F9", "#93C5FD", "#60A5FA", "#F8FAFC", "#3B82F6", "#CBD5E1",
        "#A5B4FC", "#C7D2FE", "#6366F1", "#EEF2FF", "#E0E7FF", "#94A3B8",
        "#B8C8F5", "#F3F5FF", "#0D1633", "#16204A", "#3A4878", "#2A3766",
        "#26325A",
        # v2/v3 saturated violet, peach, cream, dark purple
        "#7C3AED", "#6D28D9", "#FFF7F2", "#FBE4D8", "#DFB6B2", "#854F6C",
        "#522B5B", "#2B124C", "#190019", "#EDE9FE", "#F3E8FF", "#DDD6FE",
        "#C4B5FD", "#3B1C63",
    }
    target_files = [
        ROOT / "agent_allowance" / "ui_theme.py",
        ROOT / "agent_allowance" / "app.py",
        ROOT / "agent_allowance" / "assets" / "logo.svg",
        ROOT / "agent_allowance" / "assets" / "avatar.svg",
        ROOT / ".streamlit" / "config.toml",
    ]
    for path in target_files:
        found = {h.upper() for h in re.findall(r"#[0-9A-Fa-f]{6}\b", path.read_text(encoding="utf-8"))}
        leftover = found & old_hexes
        assert not leftover, f"{path.name} still contains old hex colors: {sorted(leftover)}"
    for illus in (WELCOME_ILLUSTRATION, APPROVALS_EMPTY_ILLUSTRATION, LEDGER_EMPTY_ILLUSTRATION):
        found = {h.upper() for h in re.findall(r"#[0-9A-Fa-f]{6}\b", illus)}
        assert not (found & old_hexes)


@pytest.mark.parametrize("pct, level", [
    (0.0, "ok"), (0.5, "ok"), (0.7499, "ok"),
    (0.75, "warn"), (0.85, "warn"), (0.90, "warn"),
    (0.9001, "danger"), (1.0, "danger"), (1.5, "danger"),
])
def test_gauge_level_thresholds(pct, level):
    """Ink below 75%, amber from 75% up to 90%, red above 90%."""
    assert gauge_level(pct) == level


def test_gauge_level_handles_missing_value():
    assert gauge_level(None) == "ok"


def test_gauge_color_maps_to_tokens():
    assert gauge_color(0.10) == TOKENS["gauge-ok"] == TOKENS["ink"]
    assert gauge_color(0.80) == TOKENS["gauge-warn"] == "#C79A3B"
    assert gauge_color(0.95) == TOKENS["gauge-danger"] == "#B45454"


@pytest.mark.parametrize("status, rules_state", [
    ("BLOCKED", "blocked"),
    ("NEEDS_APPROVAL", "pending"),
])
def test_pipeline_paypal_stays_dark_when_not_approved(status, rules_state):
    """BLOCKED and NEEDS_APPROVAL never contact PayPal, even if has_order were passed."""
    states = pipeline_states("decided", status=status, has_order=True)
    assert states["rules"][0] == rules_state
    assert states["paypal"] == ("dark", "Not contacted")


def test_pipeline_paypal_lights_only_with_real_order():
    assert pipeline_states("decided", "APPROVED", has_order=True)["paypal"] == ("approved", "Order created")
    no_order = pipeline_states("decided", "APPROVED", has_order=False)["paypal"]
    assert no_order[0] == "dark"
    assert no_order[1] != "Order created"


def test_pipeline_live_phases_light_nodes_in_sequence():
    assert pipeline_states("searching")["agent"][0] == "active"
    assert pipeline_states("searching")["rules"][0] == "idle"
    checking = pipeline_states("checking")
    assert checking["agent"][0] == "done"
    assert checking["rules"][0] == "active"
    assert checking["paypal"][0] == "idle"


def test_pipeline_no_proposal_and_error_never_reach_paypal():
    for phase in ("no_proposal", "error"):
        assert pipeline_states(phase)["paypal"] == ("dark", "Not contacted")
    assert pipeline_states("error")["agent"][0] == "error"
