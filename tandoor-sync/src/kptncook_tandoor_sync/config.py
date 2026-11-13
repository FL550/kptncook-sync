from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


class ConfigurationError(ValueError):
    pass


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"Required setting {name} is missing")
    return value


def _base_url(name: str) -> str:
    value = _required(name).rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ConfigurationError(f"{name} must use HTTPS (HTTP is allowed for localhost)")
    if not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigurationError(f"{name} must be a plain base URL without credentials or query")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


@dataclass(frozen=True)
class Settings:
    kptncook_api_key: str
    kptncook_access_token: str
    kptncook_account_id: str
    kptncook_api_url: str
    kptncook_favorites_complete: bool
    kptncook_lang: str
    kptncook_store: str
    tandoor_url: str
    tandoor_token: str
    tandoor_space_id: int
    interval_seconds: int
    state_path: Path
    request_timeout_seconds: float

    @classmethod
    def from_env(cls) -> Settings:
        try:
            space_id = int(_required("TANDOOR_SPACE_ID"))
            interval = int(os.environ.get("SYNC_INTERVAL_SECONDS", "86400"))
            timeout = float(os.environ.get("HTTP_TIMEOUT_SECONDS", "30"))
        except ValueError as exc:
            raise ConfigurationError("Space ID, sync interval, and timeout must be numeric") from exc
        if space_id < 1:
            raise ConfigurationError("TANDOOR_SPACE_ID must be positive")
        if interval < 1:
            raise ConfigurationError("SYNC_INTERVAL_SECONDS must be positive")
        if not 1 <= timeout <= 120:
            raise ConfigurationError("HTTP_TIMEOUT_SECONDS must be between 1 and 120")
        complete_value = os.environ.get("KPTNCOOK_FAVORITES_COMPLETE", "false").strip().lower()
        if complete_value not in {"true", "false"}:
            raise ConfigurationError("KPTNCOOK_FAVORITES_COMPLETE must be true or false")
        state_path = Path(os.environ.get("STATE_PATH", "/data/history.sqlite3"))
        return cls(
            kptncook_api_key=_required("KPTNCOOK_API_KEY"),
            kptncook_access_token=_required("KPTNCOOK_ACCESS_TOKEN"),
            kptncook_account_id=_required("KPTNCOOK_ACCOUNT_ID"),
            kptncook_api_url=_base_url("KPTNCOOK_API_URL")
            if os.environ.get("KPTNCOOK_API_URL", "").strip()
            else "https://mobile.kptncook.com",
            kptncook_favorites_complete=complete_value == "true",
            kptncook_lang=os.environ.get("KPTNCOOK_LANG", "de"),
            kptncook_store=os.environ.get("KPTNCOOK_STORE", "de"),
            tandoor_url=_base_url("TANDOOR_URL"),
            tandoor_token=_required("TANDOOR_TOKEN"),
            tandoor_space_id=space_id,
            interval_seconds=interval,
            state_path=state_path,
            request_timeout_seconds=timeout,
        )

    @property
    def destination_identity(self) -> str:
        return f"{self.tandoor_url}|space:{self.tandoor_space_id}"

