"""Load a local, git-ignored .env at startup without overriding real environment variables.

Cloud Run / Secret Manager values always win. Values are never logged.
"""
from __future__ import annotations

import os

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")


def load_dotenv(path: str = ENV_PATH) -> list[str]:
    loaded: list[str] = []
    if not os.path.isfile(path):
        return loaded
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value
                loaded.append(key)
    return loaded


def maps_browser_key() -> str:
    return (os.environ.get("GOOGLE_MAPS_BROWSER_KEY") or os.environ.get("GOOGLE_MAPS_API_KEY") or "").strip()


def maps_server_key() -> str:
    return (os.environ.get("GOOGLE_MAPS_SERVER_KEY") or os.environ.get("GOOGLE_MAPS_API_KEY") or "").strip()
