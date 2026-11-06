"""
Tests for environment-driven config: DB_PATH and LLM_DAILY_CAP.
Each case runs in a subprocess so the module-level Config is read fresh.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _config_value(attr, env_overrides, unset=()):
    env = {k: v for k, v in os.environ.items() if k not in unset}
    env.update(env_overrides)
    out = subprocess.run(
        [sys.executable, "-c", f"from agent_allowance.config import Config; print(Config.{attr})"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def test_db_path_is_used(tmp_path):
    target = str(tmp_path / "data" / "aa.db")
    assert _config_value("DATABASE_PATH", {"DB_PATH": target}) == target


def test_db_path_wins_over_legacy_database_path(tmp_path):
    a, b = str(tmp_path / "a.db"), str(tmp_path / "b.db")
    assert _config_value("DATABASE_PATH", {"DB_PATH": a, "DATABASE_PATH": b}) == a


def test_legacy_database_path_still_works(tmp_path):
    b = str(tmp_path / "b.db")
    assert _config_value("DATABASE_PATH", {"DATABASE_PATH": b}, unset=("DB_PATH",)) == b


def test_llm_daily_cap_parses_and_falls_back():
    assert _config_value("LLM_DAILY_CAP", {"LLM_DAILY_CAP": "7"}) == "7"
    assert _config_value("LLM_DAILY_CAP", {"LLM_DAILY_CAP": "not-a-number"}) == "30"
    assert _config_value("LLM_DAILY_CAP", {"LLM_DAILY_CAP": "-4"}) == "0"
