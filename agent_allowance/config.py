"""
Configuration loader for Agent Allowance.
Loads environment variables from .env using python-dotenv.
All secrets and provider configs are centralized here.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

from .llm_guard import parse_bool, parse_session_cap

# Base directory for the project
BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env file from the root directory
load_dotenv(dotenv_path=BASE_DIR / ".env")


class Config:
    """Application configuration and credentials."""

    # PayPal Developer Platform (Sandbox ONLY)
    PAYPAL_MODE: str = os.getenv("PAYPAL_MODE", "sandbox").lower()
    PAYPAL_CLIENT_ID: str = os.getenv("PAYPAL_CLIENT_ID", "")
    PAYPAL_CLIENT_SECRET: str = os.getenv("PAYPAL_CLIENT_SECRET", "")
    PAYPAL_API_BASE_URL: str = "https://api-m.sandbox.paypal.com"

    # LLM Provider Configuration
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "mock").lower()
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "mock-agent")

    # Runtime mode & demo safety
    # DEFAULT_MODE: "mock" or "gemini" for new sessions (Mock is forced when no key is set).
    DEFAULT_MODE: str = os.getenv("DEFAULT_MODE", "").lower()
    # Max real LLM requests per browser session; Mock mode is never capped.
    LLM_SESSION_CAP: int = parse_session_cap(os.getenv("LLM_SESSION_CAP"), default=10)
    # Public demo: gate Reset, never show raw exceptions.
    DEMO_PUBLIC: bool = parse_bool(os.getenv("DEMO_PUBLIC"))

    # Storage & App Settings
    DATABASE_PATH: str = os.getenv(
        "DATABASE_PATH", str(BASE_DIR / "agent_allowance.db")
    )
    DEFAULT_CURRENCY: str = os.getenv("DEFAULT_CURRENCY", "USD")
    APP_ENV: str = os.getenv("APP_ENV", "development")
    APP_BASE_URL: str = os.getenv("APP_BASE_URL", "http://localhost:8501").rstrip("/")

    @classmethod
    def llm_api_key(cls) -> str:
        return cls.LLM_API_KEY or os.getenv("GEMINI_API_KEY", "")

    @classmethod
    def validate_paypal_config(cls) -> bool:
        """Verify that basic PayPal sandbox credentials are provided."""
        return bool(cls.PAYPAL_CLIENT_ID and cls.PAYPAL_CLIENT_SECRET)
