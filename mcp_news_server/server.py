"""Remote MCP server exposing read-only tools for MySQL news data and generic table reads."""

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
Use list_symbols to discover available stock specific news tables, get_symbol_news 
for direct reads, search for keyword-based discovery, and fetch for full row retrieval by canonical id.
Use select_schema_tables to discover schemas and tables when you don't know names ahead of time.
Use describe_table and query_table for generic reads from other schemas/tables.
Use search_business_summaries to find stock symbols whose business summary contains a keyword.
Use scripts/create_stocks_views.sql for large queries on business summaries by industry.
"""

auth_settings = build_auth_settings(SETTINGS) if SETTINGS.auth_enabled else None
token_verifier = build_token_verifier(SETTINGS) if SETTINGS.auth_enabled else None

mcp = FastMCP(
    name=SETTINGS.mcp_name,
    instructions=SERVER_INSTRUCTIONS.strip(),
    json_response=True,
    host=SETTINGS.mcp_host,
    port=SETTINGS.mcp_port,
    token_verifier=token_verifier,
    auth=auth_settings,
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
def select_schema_tables(
    schema: str | None = None,
    tables: list[str] | None = None,
    schema_limit: int = 200,
    table_limit: int = 500,
    include_system_schemas: bool = False,
) -> dict[str, Any]:
    """
    Discover schemas/tables and validate a selection.

    - Call with no args to list available schemas.
    - Call with `schema` to list tables in that schema.
    - Optionally pass `tables` to validate/normalize table names.
    """
    default_schema = REPOSITORY.schema
    available_schemas = REPOSITORY.list_schemas(limit=schema_limit, include_system=include_system_schemas)

    selected_schema: str | None = None
    available_tables: list[str] = []
    selected_tables: list[str] = []

    schema_requested = schema or (default_schema if tables else None)
    if schema_requested:
        selected_schema = REPOSITORY.resolve_schema(schema_requested)
        available_tables = REPOSITORY.list_tables(schema=selected_schema, limit=table_limit)

        if tables:
            resolved_tables = [REPOSITORY.resolve_table(selected_schema, table) for table in tables]
            selected_tables = list(dict.fromkeys(resolved_tables))

    return {
        "default_schema": default_schema,
        "selected_schema": selected_schema,
        "selected_tables": selected_tables,
        "available_schemas": available_schemas,
        "available_tables": available_tables,
    }


@mcp.tool()
def describe_table(schema: str, table: str) -> dict[str, Any]:
    """Describe a schema-qualified table (columns and primary key)."""
    return REPOSITORY.describe_table(schema=schema, table=table)


@mcp.tool()
def query_table(
    schema: str,
    table: str,
    where: dict[str, Any] | None = None,
    columns: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
    order_by: str | None = None,
    desc: bool = False,
) -> dict[str, Any]:
    """
    Safely read rows from any schema-qualified table.

    Identifiers are validated against information_schema. `where` supports equality filters only
    (plus list values for `IN (...)`).
    """
    rows = REPOSITORY.query_table(
        schema=schema,
        table=table,
        where=where,
        columns=columns,
        limit=limit,
        offset=offset,
        order_by=order_by,
        desc=desc,
    )
    return {"schema": schema, "table": table, "count": len(rows), "rows": rows}


@mcp.tool()
def search_business_summaries(
    query: str,
    schema: str = "stocks",
    table: str = "symbol_business_summary",
    summary_column: str = "business_summary",
    symbol_column: str = "symbol",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Return symbols whose business summary contains a keyword."""
    symbols = REPOSITORY.search_business_summaries(
        keyword=query,
        schema=schema,
        table=table,
        symbol_column=symbol_column,
        summary_column=summary_column,
        limit=limit,
        offset=offset,
    )
    return {
        "schema": schema,
        "table": table,
        "summary_column": summary_column,
        "symbol_column": symbol_column,
        "query": query,
        "count": len(symbols),
        "symbols": symbols,
    }


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
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
