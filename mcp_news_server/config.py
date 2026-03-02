"""Environment-driven configuration for the MCP server."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def _get_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be an integer.") from exc


def _get_csv(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    values = [item.strip() for item in raw.split(",")]
    return [item for item in values if item]


@dataclass(frozen=True)
class Settings:
    """Application settings sourced from environment variables."""

    mcp_name: str
    mcp_host: str
    mcp_port: int
    mcp_transport: str
    mcp_base_url: str | None
    auth_enabled: bool

    mysql_host: str
    mysql_port: int
    mysql_user: str
    mysql_password: str
    mysql_database: str
    mysql_connect_timeout: int
    mysql_read_timeout: int

    max_rows: int
    max_scan_symbols: int
    auth_required_scopes: list[str]

    auth_issuer_url: str | None
    auth_jwks_uri: str | None
    auth_audience: str | None

    @property
    def sqlalchemy_url(self) -> str:
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
        )

    @classmethod
    def from_env(cls) -> "Settings":
        # For local dev/prototyping, allow a repo-root `.env` file (optional).
        # On VPS/systemd, env should be provided by the process manager.
        try:
            from dotenv import load_dotenv  # type: ignore

            load_dotenv()
        except Exception:
            pass

        auth_enabled = _get_bool("MCP_AUTH_ENABLED", default=False)

        base_url = os.getenv("MCP_BASE_URL", "").strip() or None

        issuer_url = None
        jwks_uri = None
        audience = None
        required_scopes: list[str] = []

        if auth_enabled:
            issuer_url = _require_env("AUTH_ISSUER_URL")
            # When auth is enabled, MCP_BASE_URL is required because it's used
            # as the OAuth resource identifier and for protected resource metadata.
            base_url = _require_env("MCP_BASE_URL")
            jwks_uri = os.getenv("AUTH_JWKS_URI", "").strip() or f"{issuer_url.rstrip('/')}/.well-known/jwks.json"
            required_scopes = _get_csv("AUTH_REQUIRED_SCOPES", "news.read")
            audience = os.getenv("AUTH_AUDIENCE", str(base_url))

        return cls(
            mcp_name=os.getenv("MCP_SERVER_NAME", "MySQL News MCP"),
            mcp_host=os.getenv("MCP_HOST", "0.0.0.0"),
            mcp_port=_get_int("MCP_PORT", 8000),
            mcp_transport=os.getenv("MCP_TRANSPORT", "streamable-http"),
            mcp_base_url=base_url,
            auth_enabled=auth_enabled,
            mysql_host=os.getenv("MYSQL_HOST", "127.0.0.1"),
            mysql_port=_get_int("MYSQL_PORT", 3306),
            mysql_user=os.getenv("MYSQL_USER", "root"),
            mysql_password=_require_env("MYSQL_PASSWORD"),
            mysql_database=os.getenv("MYSQL_DATABASE", "news"),
            mysql_connect_timeout=_get_int("MYSQL_CONNECT_TIMEOUT", 8),
            mysql_read_timeout=_get_int("MYSQL_READ_TIMEOUT", 15),
            max_rows=_get_int("MCP_MAX_ROWS", 1200),
            max_scan_symbols=_get_int("MCP_MAX_SCAN_SYMBOLS", 50),
            auth_required_scopes=required_scopes,
            auth_issuer_url=issuer_url,
            auth_jwks_uri=jwks_uri,
            auth_audience=audience,
        )
