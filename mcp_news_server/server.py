"""Remote MCP server exposing tools for MySQL news data and generic table reads."""

from __future__ import annotations

import logging
import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from mcp_news_server.auth import build_auth_settings, build_token_verifier
from mcp_news_server.config import Settings
from mcp_news_server.db import NewsRepository

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)

SETTINGS = Settings.from_env()
REPOSITORY = NewsRepository.from_settings(SETTINGS)
def _csv_env(name: str) -> list[str]:
    """Read a comma-separated environment variable."""
    return [
        item.strip()
        for item in os.getenv(name, "").split(",")
        if item.strip()
    ]


_ALLOWED_HOSTS = _csv_env("MCP_ALLOWED_HOSTS")

TRANSPORT_SECURITY = (
    TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_ALLOWED_HOSTS,
        allowed_origins=_csv_env("MCP_ALLOWED_ORIGINS"),
    )
    if _ALLOWED_HOSTS
    else None
)

READ_TOOL_INSTRUCTIONS = """
This MCP server provides access to stock-news tables in a MySQL schema.
Use list_symbols to discover available stock specific news tables, get_symbol_news 
for direct reads, search for keyword-based discovery, and fetch for full row retrieval by canonical id.
Use select_schema_tables to discover schemas and tables when you don't know names ahead of time.
Use describe_table and query_table for generic reads from other schemas/tables.
Use search_business_summaries to find stock symbols whose business summary contains a keyword.
Use scripts/create_stocks_views.sql for large queries on business summaries by industry.
"""

WRITE_TOOL_INSTRUCTIONS = """
Use update_event_summary to update only the event_summary column in stocks.recent_events for a single symbol/date row.
Use update_current_event_summary to update only the event_summary column in stocks.current_events for a single symbol/date row.
Use update_new_ep_event_summary to update only the event_summary column in stocks.new_ep for a single symbol row.
Use create_business_analytics_table, insert_business_analytics_rows, and update_business_analytics_rows
to write only within the business_analytics schema.
"""

SERVER_INSTRUCTIONS = READ_TOOL_INSTRUCTIONS
if SETTINGS.write_tools_enabled:
    SERVER_INSTRUCTIONS = f"{SERVER_INSTRUCTIONS.rstrip()}\n{WRITE_TOOL_INSTRUCTIONS}"

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
    transport_security=TRANSPORT_SECURITY,
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


def update_event_summary(symbol: str, date: str, event_summary: str) -> dict[str, Any]:
    """
    Update only the event_summary column in stocks.recent_events for one symbol/date row.

    Both `symbol` and `date` are required. `date` must be in YYYY-MM-DD format.
    The update is refused if the symbol/date pair does not identify exactly one row.
    """
    return REPOSITORY.update_event_summary(symbol=symbol, date=date, event_summary=event_summary)


def update_current_event_summary(symbol: str, date: str, event_summary: str) -> dict[str, Any]:
    """
    Update only the event_summary column in stocks.current_events for one symbol/date row.

    Both `symbol` and `date` are required. `date` must be in YYYY-MM-DD format.
    The update is refused if the symbol/date pair does not identify exactly one row.
    """
    return REPOSITORY.update_current_event_summary(
        symbol=symbol,
        date=date,
        event_summary=event_summary,
    )


def update_new_ep_event_summary(symbol: str, event_summary: str) -> dict[str, Any]:
    """
    Update only the event_summary column in stocks.new_ep for one symbol row.

    `symbol` is required and must identify exactly one row.
    """
    return REPOSITORY.update_new_ep_event_summary(symbol=symbol, event_summary=event_summary)


def create_business_analytics_table(
    table: str,
    columns: list[dict[str, Any]],
    primary_key: list[str] | None = None,
    if_not_exists: bool = True,
) -> dict[str, Any]:
    """
    Create a table in business_analytics using structured column definitions.

    Column definitions require `name` and `type`. Supported types include integer, bigint,
    varchar/char with optional `length`, text, decimal with optional precision/scale,
    float, double, boolean, date, datetime, timestamp, time, and json.
    """
    return REPOSITORY.create_business_analytics_table(
        table=table,
        columns=columns,
        primary_key=primary_key,
        if_not_exists=if_not_exists,
    )


def insert_business_analytics_rows(table: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Insert one or more rows into a business_analytics table.

    All rows must use the same column set. Table and column names are validated before insert.
    """
    return REPOSITORY.insert_business_analytics_rows(table=table, rows=rows)


def update_business_analytics_rows(
    table: str,
    values: dict[str, Any],
    where: dict[str, Any],
    limit: int = 100,
) -> dict[str, Any]:
    """
    Update rows in a business_analytics table.

    `where` is required and supports equality filters only, plus list values for IN (...).
    """
    return REPOSITORY.update_business_analytics_rows(
        table=table,
        values=values,
        where=where,
        limit=limit,
    )


if SETTINGS.write_tools_enabled:
    update_event_summary = mcp.tool()(update_event_summary)
    update_current_event_summary = mcp.tool()(update_current_event_summary)
    update_new_ep_event_summary = mcp.tool()(update_new_ep_event_summary)
    create_business_analytics_table = mcp.tool()(create_business_analytics_table)
    insert_business_analytics_rows = mcp.tool()(insert_business_analytics_rows)
    update_business_analytics_rows = mcp.tool()(update_business_analytics_rows)


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
