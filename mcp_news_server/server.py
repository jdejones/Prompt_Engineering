"""Remote MCP server exposing read-only tools for the MySQL `news` schema."""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_news_server.auth import build_auth_settings, build_token_verifier
from mcp_news_server.config import Settings
from mcp_news_server.db import NewsRepository

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)

SETTINGS = Settings.from_env()
REPOSITORY = NewsRepository.from_settings(SETTINGS)

SERVER_INSTRUCTIONS = """
This MCP server provides read-only access to stock-news tables in a MySQL schema.
Use list_symbols to discover available stock tables, get_symbol_news for direct reads,
search for keyword-based discovery, and fetch for full row retrieval by canonical id.
"""

mcp = FastMCP(
    name=SETTINGS.mcp_name,
    instructions=SERVER_INSTRUCTIONS.strip(),
    json_response=True,
    token_verifier=build_token_verifier(SETTINGS),
    auth=build_auth_settings(SETTINGS),
)


@mcp.tool()
def health() -> dict[str, str]:
    """Simple health check for deployment probes."""
    return {"status": "ok"}


@mcp.tool()
def list_symbols(limit: int = 500) -> dict[str, Any]:
    """List valid symbol table names from the configured MySQL schema."""
    symbols = REPOSITORY.list_symbols(limit=limit)
    return {"symbols": symbols, "count": len(symbols)}


@mcp.tool()
def get_symbol_news(symbol: str, date_from: str | None = None, limit: int = 50) -> dict[str, Any]:
    """Read rows from one symbol table, optionally filtering by start date."""
    rows = REPOSITORY.get_symbol_news(symbol=symbol, date_from=date_from, limit=limit)
    resolved_symbol = rows[0]["symbol"] if rows else symbol
    return {"symbol": resolved_symbol, "count": len(rows), "rows": rows}


@mcp.tool()
def search(
    query: str,
    date_from: str | None = None,
    symbols: list[str] | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """
    Search text-like columns across symbol tables.

    This returns `results` in a shape designed for ChatGPT research workflows.
    """
    results = REPOSITORY.search(query=query, symbols=symbols, date_from=date_from, limit=limit)
    return {"results": results}


@mcp.tool()
def fetch(id: str) -> dict[str, Any]:
    """Fetch one record by canonical id (`<SYMBOL>:<PRIMARY_KEY_VALUE>`)."""
    return REPOSITORY.fetch(id)


def main() -> None:
    transport = SETTINGS.mcp_transport
    if transport not in {"streamable-http", "sse"}:
        raise RuntimeError("MCP_TRANSPORT must be 'streamable-http' or 'sse'.")

    LOGGER.info("Starting MCP server on %s:%s (%s)", SETTINGS.mcp_host, SETTINGS.mcp_port, transport)
    mcp.run(transport=transport, host=SETTINGS.mcp_host, port=SETTINGS.mcp_port)


if __name__ == "__main__":
    main()
