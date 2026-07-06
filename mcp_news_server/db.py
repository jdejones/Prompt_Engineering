"""MySQL query layer for symbol-table news data and safe schema-qualified reads."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from mcp_news_server.config import Settings

PREFERRED_TEXT_COLUMNS = (
    "Title",
    "title",
    "headline",
    "Headline",
    "summary",
    "Summary",
    "description",
    "Description",
    "content",
    "Content",
    "body",
    "Body",
)

PREFERRED_DATE_COLUMNS = (
    "date",
    "Date",
    "published_at",
    "publishedAt",
    "created_at",
    "datetime",
)

TEXT_DATA_TYPES = {
    "char",
    "varchar",
    "tinytext",
    "text",
    "mediumtext",
    "longtext",
}

SYSTEM_SCHEMAS = {
    "information_schema",
    "mysql",
    "performance_schema",
    "sys",
}

EVENT_SUMMARY_SCHEMA = "stocks"
EVENT_SUMMARY_TABLE = "recent_events"
NEW_EP_SCHEMA = "stocks"
NEW_EP_TABLE = "new_ep"
BUSINESS_ANALYTICS_SCHEMA = "business_analytics"

INTEGER_COLUMN_TYPES = {
    "bigint",
    "int",
    "integer",
    "smallint",
    "tinyint",
}
SIMPLE_COLUMN_TYPES = {
    "boolean",
    "date",
    "datetime",
    "double",
    "float",
    "json",
    "longtext",
    "mediumtext",
    "text",
    "time",
    "timestamp",
}


class NewsRepository:
    """Repository for read-only queries across symbol-named MySQL tables."""

    def __init__(
        self,
        engine: Engine,
        schema: str,
        max_rows: int,
        max_scan_symbols: int,
    ) -> None:
        self.engine = engine
        self.schema = schema
        self.max_rows = max_rows
        self.max_scan_symbols = max_scan_symbols

        self._symbols_cache: set[str] = set()
        self._symbol_lookup_cache: dict[str, str] = {}
        self._columns_cache: dict[str, list[dict[str, str]]] = {}
        self._primary_key_cache: dict[str, str | None] = {}

        self._schemas_cache: set[str] = set()
        self._schema_lookup_cache: dict[str, str] = {}
        self._tables_cache_by_schema: dict[str, set[str]] = {}
        self._table_lookup_cache_by_schema: dict[str, dict[str, str]] = {}

        self._columns_cache_by_table: dict[tuple[str, str], list[dict[str, str]]] = {}
        self._column_lookup_cache_by_table: dict[tuple[str, str], dict[str, str]] = {}
        self._primary_key_cache_by_table: dict[tuple[str, str], str | None] = {}

    @classmethod
    def from_settings(cls, settings: Settings) -> "NewsRepository":
        engine = create_engine(
            settings.sqlalchemy_url,
            pool_pre_ping=True,
            connect_args={
                "connect_timeout": settings.mysql_connect_timeout,
                "read_timeout": settings.mysql_read_timeout,
                "write_timeout": settings.mysql_read_timeout,
            },
        )
        return cls(
            engine=engine,
            schema=settings.mysql_database,
            max_rows=settings.max_rows,
            max_scan_symbols=settings.max_scan_symbols,
        )

    def list_symbols(self, limit: int | None = None) -> list[str]:
        self._refresh_symbols_cache()
        ordered = sorted(self._symbols_cache)
        if limit is None:
            return ordered
        return ordered[: self._safe_limit(limit)]

    def list_schemas(self, limit: int | None = None, include_system: bool = False) -> list[str]:
        """List schemas visible to the configured MySQL user."""
        self._refresh_schemas_cache()
        ordered = sorted(self._schemas_cache)
        if not include_system:
            ordered = [name for name in ordered if name.lower() not in SYSTEM_SCHEMAS]
        if limit is None:
            return ordered
        return ordered[: self._safe_limit(limit)]

    def resolve_schema(self, schema: str) -> str:
        """Return canonical schema name (case-preserving), or raise if unknown."""
        self._refresh_schemas_cache()
        normalized = schema.strip()
        if not normalized:
            raise ValueError("schema must be a non-empty string.")
        if normalized in self._schemas_cache:
            return normalized
        fallback = self._schema_lookup_cache.get(normalized.lower())
        if fallback:
            return fallback
        raise ValueError(f"Unknown schema '{schema}'.")

    def list_tables(self, schema: str | None = None, limit: int | None = None) -> list[str]:
        """List tables and views for a schema (defaults to the configured database)."""
        target_schema = self.resolve_schema(schema or self.schema)
        self._refresh_tables_cache(target_schema)
        ordered = sorted(self._tables_cache_by_schema[target_schema])
        if limit is None:
            return ordered
        return ordered[: self._safe_limit(limit)]

    def resolve_table(self, schema: str, table: str) -> str:
        """Return canonical table name for a schema (case-preserving), or raise if unknown."""
        resolved_schema = self.resolve_schema(schema)
        self._refresh_tables_cache(resolved_schema)
        normalized = table.strip()
        if not normalized:
            raise ValueError("table must be a non-empty string.")
        available = self._tables_cache_by_schema[resolved_schema]
        if normalized in available:
            return normalized
        fallback = self._table_lookup_cache_by_schema[resolved_schema].get(normalized.lower())
        if fallback:
            return fallback
        raise ValueError(f"Unknown table '{table}' in schema '{resolved_schema}'.")

    def describe_table(self, schema: str, table: str) -> dict[str, Any]:
        """Return column metadata for a table in a given schema."""
        resolved_schema = self.resolve_schema(schema)
        resolved_table = self.resolve_table(resolved_schema, table)
        columns = self._resolve_columns_for_table(resolved_schema, resolved_table)
        primary_key = self._resolve_primary_key_for_table(resolved_schema, resolved_table)
        return {
            "schema": resolved_schema,
            "table": resolved_table,
            "primary_key": primary_key,
            "columns": columns,
        }

    def query_table(
        self,
        schema: str,
        table: str,
        where: dict[str, Any] | None = None,
        columns: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
        order_by: str | None = None,
        desc: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Run a safe, read-only SELECT against one schema-qualified table.

        - schema/table/column identifiers are validated against information_schema.
        - where supports equality filters only (plus IS NULL when value is null).
        """
        resolved_schema = self.resolve_schema(schema)
        resolved_table = self.resolve_table(resolved_schema, table)

        row_limit = self._safe_limit(limit)
        if offset < 0:
            raise ValueError("offset must be zero or greater.")
        safe_offset = offset

        select_clause = "*"
        if columns:
            resolved_columns = [self.resolve_column(resolved_schema, resolved_table, column) for column in columns]
            # Preserve caller order, remove duplicates.
            resolved_columns = list(dict.fromkeys(resolved_columns))
            select_clause = ", ".join(f"`{self._quote_identifier(column)}`" for column in resolved_columns)

        sql = f"SELECT {select_clause} FROM {self._qualified_table(resolved_schema, resolved_table)}"
        query_params: dict[str, Any] = {"limit": row_limit, "offset": safe_offset}

        if where:
            if len(where) > 25:
                raise ValueError("where supports up to 25 columns.")
            clauses: list[str] = []
            for idx, (raw_column, value) in enumerate(where.items()):
                column = self.resolve_column(resolved_schema, resolved_table, raw_column)
                if value is None:
                    clauses.append(f"`{self._quote_identifier(column)}` IS NULL")
                    continue
                if isinstance(value, dict):
                    raise ValueError("where values must be scalar or a list of scalars.")
                if isinstance(value, list):
                    if not value:
                        raise ValueError("where list values must be non-empty.")
                    if len(value) > 200:
                        raise ValueError("where list values support up to 200 items.")
                    if any(item is None for item in value):
                        raise ValueError("where list values cannot include null.")
                    if any(isinstance(item, (dict, list)) for item in value):
                        raise ValueError("where list values must be scalar (no nested lists/dicts).")

                    param_names: list[str] = []
                    for j, item in enumerate(value):
                        param_name = f"w{idx}_{j}"
                        param_names.append(f":{param_name}")
                        query_params[param_name] = item
                    clauses.append(f"`{self._quote_identifier(column)}` IN ({', '.join(param_names)})")
                    continue
                param_name = f"w{idx}"
                clauses.append(f"`{self._quote_identifier(column)}` = :{param_name}")
                query_params[param_name] = value
            sql = f"{sql} WHERE {' AND '.join(clauses)}"

        if order_by:
            order_column = self.resolve_column(resolved_schema, resolved_table, order_by)
            direction = "DESC" if desc else "ASC"
            sql = f"{sql} ORDER BY `{self._quote_identifier(order_column)}` {direction}"

        sql = f"{sql} LIMIT :limit OFFSET :offset"
        return self._query(sql, query_params)

    def search_business_summaries(
        self,
        keyword: str,
        schema: str = "stocks",
        table: str = "symbol_business_summary",
        symbol_column: str = "symbol",
        summary_column: str = "business_summary",
        limit: int = 50,
        offset: int = 0,
    ) -> list[str]:
        """
        Return symbols whose business summary contains a keyword.

        This uses a parameterized LIKE '%keyword%' filter. Identifiers are validated
        against information_schema (same as query_table).
        """
        cleaned_keyword = keyword.strip()
        if not cleaned_keyword:
            return []

        row_limit = self._safe_limit(limit)
        if offset < 0:
            raise ValueError("offset must be zero or greater.")
        safe_offset = offset

        resolved_schema = self.resolve_schema(schema)
        resolved_table = self.resolve_table(resolved_schema, table)
        resolved_symbol_column = self.resolve_column(resolved_schema, resolved_table, symbol_column)
        resolved_summary_column = self.resolve_column(resolved_schema, resolved_table, summary_column)

        quoted_symbol = self._quote_identifier(resolved_symbol_column)
        quoted_summary = self._quote_identifier(resolved_summary_column)

        sql = (
            f"SELECT `{quoted_symbol}` AS symbol "
            f"FROM {self._qualified_table(resolved_schema, resolved_table)} "
            f"WHERE `{quoted_summary}` LIKE :pattern "
            f"ORDER BY `{quoted_symbol}` ASC "
            f"LIMIT :limit OFFSET :offset"
        )
        rows = self._query(
            sql,
            {
                "pattern": f"%{cleaned_keyword}%",
                "limit": row_limit,
                "offset": safe_offset,
            },
        )

        seen: set[str] = set()
        symbols: list[str] = []
        for row in rows:
            value = row.get("symbol")
            if value is None:
                continue
            symbol_value = str(value)
            if symbol_value in seen:
                continue
            seen.add(symbol_value)
            symbols.append(symbol_value)

        return symbols

    def get_symbol_news(self, symbol: str, date_from: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        normalized_symbol = self._validate_symbol(symbol)
        row_limit = self._safe_limit(limit)
        date_column = self._resolve_date_column(normalized_symbol)
        query_params: dict[str, Any] = {"limit": row_limit}

        where_clause = ""
        order_clause = ""
        if date_from and date_column:
            self._validate_date(date_from)
            where_clause = f" WHERE `{self._quote_identifier(date_column)}` >= :date_from"
            query_params["date_from"] = f"{date_from} 00:00:00"
            order_clause = f" ORDER BY `{self._quote_identifier(date_column)}` DESC"
        elif date_column:
            order_clause = f" ORDER BY `{self._quote_identifier(date_column)}` DESC"

        sql = (
            f"SELECT * FROM {self._qualified_table(self.schema, normalized_symbol)}"
            f"{where_clause}{order_clause} LIMIT :limit"
        )
        rows = self._query(sql, query_params)
        return [self._augment_row(normalized_symbol, row) for row in rows]

    def update_event_summary(self, symbol: str, date: str, event_summary: str) -> dict[str, Any]:
        """Update event_summary in stocks.recent_events for the single symbol/date row."""
        normalized_symbol = symbol.strip()
        if not normalized_symbol:
            raise ValueError("symbol must be a non-empty string.")
        self._validate_date(date, field_name="date")

        resolved_schema = self.resolve_schema(EVENT_SUMMARY_SCHEMA)
        resolved_table = self.resolve_table(resolved_schema, EVENT_SUMMARY_TABLE)
        symbol_column = self.resolve_column(resolved_schema, resolved_table, "symbol")
        date_column = self.resolve_column(resolved_schema, resolved_table, "date")
        event_summary_column = self.resolve_column(resolved_schema, resolved_table, "event_summary")
        quoted_symbol_column = self._quote_identifier(symbol_column)
        quoted_date_column = self._quote_identifier(date_column)
        quoted_event_summary_column = self._quote_identifier(event_summary_column)
        table_name = self._qualified_table(resolved_schema, resolved_table)

        count_sql = (
            f"SELECT COUNT(*) AS row_count FROM {table_name} "
            f"WHERE `{quoted_symbol_column}` = :symbol "
            f"AND DATE(`{quoted_date_column}`) = :date"
        )
        update_sql = (
            f"UPDATE {table_name} "
            f"SET `{quoted_event_summary_column}` = :event_summary "
            f"WHERE `{quoted_symbol_column}` = :symbol "
            f"AND DATE(`{quoted_date_column}`) = :date"
        )

        with self.engine.begin() as connection:
            count_result = connection.execute(
                text(count_sql),
                {
                    "symbol": normalized_symbol,
                    "date": date,
                },
            ).one()
            row_count = int(count_result._mapping["row_count"])
            if row_count == 0:
                raise ValueError(f"No row found for symbol '{normalized_symbol}' on date '{date}'.")
            if row_count > 1:
                raise ValueError(
                    f"Found {row_count} rows for symbol '{normalized_symbol}' on date '{date}'. "
                    "Refusing to update event_summary without a unique row."
                )

            result = connection.execute(
                text(update_sql),
                {
                    "symbol": normalized_symbol,
                    "date": date,
                    "event_summary": event_summary,
                },
            )

        return {
            "schema": resolved_schema,
            "table": resolved_table,
            "symbol": normalized_symbol,
            "date": date,
            "symbol_column": symbol_column,
            "date_column": date_column,
            "updated_column": event_summary_column,
            "rows_updated": result.rowcount,
        }

    def update_new_ep_event_summary(self, symbol: str, event_summary: str) -> dict[str, Any]:
        """Update event_summary in stocks.new_ep for the single symbol row."""
        normalized_symbol = symbol.strip()
        if not normalized_symbol:
            raise ValueError("symbol must be a non-empty string.")

        resolved_schema = self.resolve_schema(NEW_EP_SCHEMA)
        resolved_table = self.resolve_table(resolved_schema, NEW_EP_TABLE)
        symbol_column = self.resolve_column(resolved_schema, resolved_table, "symbol")
        event_summary_column = self.resolve_column(resolved_schema, resolved_table, "event_summary")
        quoted_symbol_column = self._quote_identifier(symbol_column)
        quoted_event_summary_column = self._quote_identifier(event_summary_column)
        table_name = self._qualified_table(resolved_schema, resolved_table)

        count_sql = (
            f"SELECT COUNT(*) AS row_count FROM {table_name} "
            f"WHERE `{quoted_symbol_column}` = :symbol"
        )
        update_sql = (
            f"UPDATE {table_name} "
            f"SET `{quoted_event_summary_column}` = :event_summary "
            f"WHERE `{quoted_symbol_column}` = :symbol"
        )

        with self.engine.begin() as connection:
            count_result = connection.execute(text(count_sql), {"symbol": normalized_symbol}).one()
            row_count = int(count_result._mapping["row_count"])
            if row_count == 0:
                raise ValueError(f"No row found for symbol '{normalized_symbol}'.")
            if row_count > 1:
                raise ValueError(
                    f"Found {row_count} rows for symbol '{normalized_symbol}'. "
                    "Refusing to update event_summary without a unique row."
                )

            result = connection.execute(
                text(update_sql),
                {
                    "symbol": normalized_symbol,
                    "event_summary": event_summary,
                },
            )

        return {
            "schema": resolved_schema,
            "table": resolved_table,
            "symbol": normalized_symbol,
            "symbol_column": symbol_column,
            "updated_column": event_summary_column,
            "rows_updated": result.rowcount,
        }

    def create_business_analytics_table(
        self,
        table: str,
        columns: list[dict[str, Any]],
        primary_key: list[str] | None = None,
        if_not_exists: bool = True,
    ) -> dict[str, Any]:
        """Create a table in business_analytics from a structured column definition."""
        resolved_schema = self.resolve_schema(BUSINESS_ANALYTICS_SCHEMA)
        table_name = self._validate_new_identifier(table, "table")
        if not columns:
            raise ValueError("columns must include at least one column definition.")
        if len(columns) > 100:
            raise ValueError("create table supports up to 100 columns.")

        column_sql: list[str] = []
        column_names: list[str] = []
        inline_primary_key: list[str] = []
        auto_increment_columns: list[str] = []
        auto_increment_count = 0

        for raw_column in columns:
            if not isinstance(raw_column, dict):
                raise ValueError("each column definition must be an object.")
            column_name = self._validate_new_identifier(str(raw_column.get("name", "")), "column")
            if column_name in column_names:
                raise ValueError(f"Duplicate column '{column_name}'.")

            column_type = self._render_column_type(raw_column)
            nullable = bool(raw_column.get("nullable", True))
            auto_increment = bool(raw_column.get("auto_increment", False))
            if auto_increment:
                auto_increment_count += 1
                auto_increment_columns.append(column_name)
                if str(raw_column.get("type", "")).strip().lower() not in INTEGER_COLUMN_TYPES:
                    raise ValueError("auto_increment is only supported on integer columns.")
                nullable = False

            parts = [f"`{self._quote_identifier(column_name)}`", column_type]
            if not nullable:
                parts.append("NOT NULL")
            if auto_increment:
                parts.append("AUTO_INCREMENT")
            column_sql.append(" ".join(parts))
            column_names.append(column_name)

            if bool(raw_column.get("primary_key", False)):
                inline_primary_key.append(column_name)

        if auto_increment_count > 1:
            raise ValueError("Only one auto_increment column is supported.")

        primary_key_columns = primary_key or inline_primary_key
        if primary_key_columns:
            normalized_pk = [self._validate_new_identifier(column, "primary key column") for column in primary_key_columns]
            unknown_pk = [column for column in normalized_pk if column not in column_names]
            if unknown_pk:
                raise ValueError(f"Primary key column(s) not found: {', '.join(unknown_pk)}.")
            normalized_pk = list(dict.fromkeys(normalized_pk))
            missing_auto_increment_pk = [column for column in auto_increment_columns if column not in normalized_pk]
            if missing_auto_increment_pk:
                raise ValueError("auto_increment columns must be included in the primary key.")
            quoted_pk = ", ".join(f"`{self._quote_identifier(column)}`" for column in normalized_pk)
            column_sql.append(f"PRIMARY KEY ({quoted_pk})")
        elif auto_increment_columns:
            raise ValueError("auto_increment columns must be included in the primary key.")

        existence_clause = "IF NOT EXISTS " if if_not_exists else ""
        sql = (
            f"CREATE TABLE {existence_clause}{self._qualified_table(resolved_schema, table_name)} "
            f"({', '.join(column_sql)})"
        )

        with self.engine.begin() as connection:
            connection.execute(text(sql))

        self._invalidate_table_metadata(resolved_schema, table_name)
        return {
            "schema": resolved_schema,
            "table": table_name,
            "columns": column_names,
            "primary_key": primary_key_columns,
            "created": True,
        }

    def insert_business_analytics_rows(self, table: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Insert one or more rows into a table in business_analytics."""
        resolved_schema = self.resolve_schema(BUSINESS_ANALYTICS_SCHEMA)
        resolved_table = self.resolve_table(resolved_schema, table)
        if not rows:
            raise ValueError("rows must include at least one row.")
        if len(rows) > self.max_rows:
            raise ValueError(f"insert supports up to {self.max_rows} rows.")
        if any(not isinstance(row, dict) for row in rows):
            raise ValueError("each row must be an object.")

        first_columns = list(rows[0].keys())
        if not first_columns:
            raise ValueError("rows must include at least one column.")
        if len(first_columns) > 100:
            raise ValueError("insert supports up to 100 columns.")

        expected_columns = set(first_columns)
        if len(expected_columns) != len(first_columns):
            raise ValueError("row columns must be unique.")
        for row in rows:
            if set(row.keys()) != expected_columns:
                raise ValueError("all inserted rows must include the same columns.")

        resolved_columns = [self.resolve_column(resolved_schema, resolved_table, column) for column in first_columns]
        quoted_columns = ", ".join(f"`{self._quote_identifier(column)}`" for column in resolved_columns)

        values_sql: list[str] = []
        query_params: dict[str, Any] = {}
        for row_index, row in enumerate(rows):
            param_names: list[str] = []
            for column_index, raw_column in enumerate(first_columns):
                param_name = f"r{row_index}_c{column_index}"
                query_params[param_name] = row[raw_column]
                param_names.append(f":{param_name}")
            values_sql.append(f"({', '.join(param_names)})")

        sql = (
            f"INSERT INTO {self._qualified_table(resolved_schema, resolved_table)} "
            f"({quoted_columns}) VALUES {', '.join(values_sql)}"
        )

        with self.engine.begin() as connection:
            result = connection.execute(text(sql), query_params)

        return {
            "schema": resolved_schema,
            "table": resolved_table,
            "columns": resolved_columns,
            "rows_inserted": result.rowcount,
        }

    def update_business_analytics_rows(
        self,
        table: str,
        values: dict[str, Any],
        where: dict[str, Any],
        limit: int = 100,
    ) -> dict[str, Any]:
        """Update rows in a business_analytics table using equality-only filters."""
        resolved_schema = self.resolve_schema(BUSINESS_ANALYTICS_SCHEMA)
        resolved_table = self.resolve_table(resolved_schema, table)
        if not values:
            raise ValueError("values must include at least one column to update.")
        if not where:
            raise ValueError("where is required for updates.")
        if len(values) > 25:
            raise ValueError("values supports up to 25 columns.")
        if len(where) > 25:
            raise ValueError("where supports up to 25 columns.")
        row_limit = self._safe_limit(limit)

        query_params: dict[str, Any] = {"limit": row_limit}
        assignments: list[str] = []
        for idx, (raw_column, value) in enumerate(values.items()):
            column = self.resolve_column(resolved_schema, resolved_table, raw_column)
            param_name = f"v{idx}"
            assignments.append(f"`{self._quote_identifier(column)}` = :{param_name}")
            query_params[param_name] = value

        where_clause = self._equality_where_clause(resolved_schema, resolved_table, where, query_params)
        sql = (
            f"UPDATE {self._qualified_table(resolved_schema, resolved_table)} "
            f"SET {', '.join(assignments)} "
            f"WHERE {where_clause} "
            f"LIMIT :limit"
        )

        with self.engine.begin() as connection:
            result = connection.execute(text(sql), query_params)

        return {
            "schema": resolved_schema,
            "table": resolved_table,
            "rows_updated": result.rowcount,
            "limit": row_limit,
        }

    def search(
        self,
        query: str,
        symbols: list[str] | None = None,
        date_from: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        cleaned_query = query.strip()
        if not cleaned_query:
            return []

        row_limit = self._safe_limit(limit)
        target_symbols = self._resolve_target_symbols(symbols)
        if date_from:
            self._validate_date(date_from)

        results: list[dict[str, Any]] = []
        for symbol in target_symbols:
            if len(results) >= row_limit:
                break

            search_columns = self._resolve_searchable_columns(symbol)
            if not search_columns:
                continue

            date_column = self._resolve_date_column(symbol)
            text_clauses = [f"`{self._quote_identifier(column)}` LIKE :pattern" for column in search_columns]
            query_params: dict[str, Any] = {"pattern": f"%{cleaned_query}%"}
            where_sql = f"({' OR '.join(text_clauses)})"

            if date_from and date_column:
                where_sql = f"{where_sql} AND `{self._quote_identifier(date_column)}` >= :date_from"
                query_params["date_from"] = f"{date_from} 00:00:00"

            table_limit = min(row_limit - len(results), self.max_rows)
            query_params["limit"] = table_limit
            order_clause = f" ORDER BY `{self._quote_identifier(date_column)}` DESC" if date_column else ""
            sql = (
                f"SELECT * FROM {self._qualified_table(self.schema, symbol)} "
                f"WHERE {where_sql}"
                f"{order_clause} LIMIT :limit"
            )
            rows = self._query(sql, query_params)
            for row in rows:
                results.append(self._search_result(symbol, row))

        return results[:row_limit]

    def fetch(self, identifier: str) -> dict[str, Any]:
        if ":" not in identifier:
            raise ValueError("Expected id format '<SYMBOL>:<PRIMARY_KEY_VALUE>'.")

        symbol, raw_pk = identifier.split(":", 1)
        normalized_symbol = self._validate_symbol(symbol)
        primary_key = self._resolve_primary_key(normalized_symbol)
        if not primary_key:
            raise ValueError(
                f"Table '{normalized_symbol}' has no primary key. "
                "Use get_symbol_news() to read rows for this symbol."
            )

        sql = (
            f"SELECT * FROM {self._qualified_table(self.schema, normalized_symbol)} "
            f"WHERE `{self._quote_identifier(primary_key)}` = :pk LIMIT 1"
        )
        rows = self._query(sql, {"pk": raw_pk})
        if not rows:
            raise ValueError(f"No row found for id '{identifier}'.")

        row = rows[0]
        title = self._extract_title(row) or f"{normalized_symbol} news item"
        body = self._extract_body(row)
        return {
            "id": self._document_id(normalized_symbol, row),
            "title": title,
            "text": body,
            "url": f"mysql://{self.schema}/{normalized_symbol}/{raw_pk}",
            "metadata": {
                "symbol": normalized_symbol,
                "schema": self.schema,
                "primary_key_column": primary_key,
                "raw_row": row,
            },
        }

    def _resolve_target_symbols(self, symbols: list[str] | None) -> list[str]:
        self._refresh_symbols_cache()
        if not symbols:
            return sorted(self._symbols_cache)[: self.max_scan_symbols]
        validated = [self._validate_symbol(symbol) for symbol in symbols]
        # Keep order predictable and remove duplicates.
        return list(dict.fromkeys(validated))[: self.max_scan_symbols]

    def _validate_symbol(self, symbol: str) -> str:
        self._refresh_symbols_cache()
        normalized = symbol.strip()
        if normalized in self._symbols_cache:
            return normalized

        fallback = self._symbol_lookup_cache.get(normalized.lower())
        if fallback:
            return fallback

        raise ValueError(f"Unknown symbol table '{symbol}'.")

    def _safe_limit(self, requested: int) -> int:
        if requested <= 0:
            raise ValueError("limit must be greater than zero.")
        return min(requested, self.max_rows)

    def _validate_date(self, value: str, field_name: str = "date_from") -> None:
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"{field_name} must be in YYYY-MM-DD format.") from exc

    def _validate_new_identifier(self, identifier: str, field_name: str) -> str:
        normalized = identifier.strip()
        if not normalized:
            raise ValueError(f"{field_name} must be a non-empty string.")
        if len(normalized) > 64:
            raise ValueError(f"{field_name} must be 64 characters or fewer.")
        if not (normalized[0].isalpha() or normalized[0] == "_"):
            raise ValueError(f"{field_name} must start with a letter or underscore.")
        if not all(char.isalnum() or char == "_" for char in normalized):
            raise ValueError(f"{field_name} may only contain letters, numbers, and underscores.")
        return normalized

    def _render_column_type(self, column: dict[str, Any]) -> str:
        raw_type = str(column.get("type", "")).strip().lower()
        if raw_type in INTEGER_COLUMN_TYPES:
            return "INTEGER" if raw_type == "integer" else raw_type.upper()
        if raw_type in SIMPLE_COLUMN_TYPES:
            return "TINYINT(1)" if raw_type == "boolean" else raw_type.upper()
        if raw_type in {"varchar", "char"}:
            length = int(column.get("length", 255))
            if length <= 0 or length > 65535:
                raise ValueError(f"{raw_type} length must be between 1 and 65535.")
            return f"{raw_type.upper()}({length})"
        if raw_type == "decimal":
            precision = int(column.get("precision", 18))
            scale = int(column.get("scale", 4))
            if precision <= 0 or precision > 65:
                raise ValueError("decimal precision must be between 1 and 65.")
            if scale < 0 or scale > 30 or scale > precision:
                raise ValueError("decimal scale must be between 0 and 30 and no greater than precision.")
            return f"DECIMAL({precision}, {scale})"
        raise ValueError(
            "Unsupported column type. Use integer, bigint, smallint, tinyint, "
            "varchar, char, text, mediumtext, longtext, decimal, float, double, "
            "boolean, date, datetime, timestamp, time, or json."
        )

    def _equality_where_clause(
        self,
        schema: str,
        table: str,
        where: dict[str, Any],
        query_params: dict[str, Any],
    ) -> str:
        clauses: list[str] = []
        for idx, (raw_column, value) in enumerate(where.items()):
            column = self.resolve_column(schema, table, raw_column)
            if value is None:
                clauses.append(f"`{self._quote_identifier(column)}` IS NULL")
                continue
            if isinstance(value, dict):
                raise ValueError("where values must be scalar or a list of scalars.")
            if isinstance(value, list):
                if not value:
                    raise ValueError("where list values must be non-empty.")
                if len(value) > 200:
                    raise ValueError("where list values support up to 200 items.")
                if any(item is None for item in value):
                    raise ValueError("where list values cannot include null.")
                if any(isinstance(item, (dict, list)) for item in value):
                    raise ValueError("where list values must be scalar (no nested lists/dicts).")
                param_names: list[str] = []
                for j, item in enumerate(value):
                    param_name = f"w{idx}_{j}"
                    param_names.append(f":{param_name}")
                    query_params[param_name] = item
                clauses.append(f"`{self._quote_identifier(column)}` IN ({', '.join(param_names)})")
                continue
            param_name = f"w{idx}"
            clauses.append(f"`{self._quote_identifier(column)}` = :{param_name}")
            query_params[param_name] = value
        return " AND ".join(clauses)

    def _invalidate_table_metadata(self, schema: str, table: str) -> None:
        self._tables_cache_by_schema.pop(schema, None)
        self._table_lookup_cache_by_schema.pop(schema, None)
        self._columns_cache_by_table.pop((schema, table), None)
        self._column_lookup_cache_by_table.pop((schema, table), None)
        self._primary_key_cache_by_table.pop((schema, table), None)

    @staticmethod
    def _ci_get(row: dict[str, Any], key: str) -> Any:
        if key in row:
            return row[key]
        lower_map = {str(k).lower(): v for k, v in row.items()}
        return lower_map[key.lower()]

    def _refresh_symbols_cache(self) -> None:
        if self._symbols_cache:
            return

        sql = """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = :schema
            AND table_type = 'BASE TABLE'
        """
        rows = self._query(sql, {"schema": self.schema})
        self._symbols_cache = {str(self._ci_get(row, "table_name")) for row in rows}
        self._symbol_lookup_cache = {name.lower(): name for name in self._symbols_cache}

    def _refresh_schemas_cache(self) -> None:
        if self._schemas_cache:
            return

        sql = """
            SELECT schema_name
            FROM information_schema.schemata
        """
        rows = self._query(sql, {})
        self._schemas_cache = {str(self._ci_get(row, "schema_name")) for row in rows}
        self._schema_lookup_cache = {name.lower(): name for name in self._schemas_cache}

    def _refresh_tables_cache(self, schema: str) -> None:
        if schema in self._tables_cache_by_schema:
            return

        sql = """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = :schema
            AND table_type IN ('BASE TABLE', 'VIEW')
        """
        rows = self._query(sql, {"schema": schema})
        tables = {str(self._ci_get(row, "table_name")) for row in rows}
        self._tables_cache_by_schema[schema] = tables
        self._table_lookup_cache_by_schema[schema] = {name.lower(): name for name in tables}

    def _resolve_columns(self, symbol: str) -> list[dict[str, str]]:
        if symbol in self._columns_cache:
            return self._columns_cache[symbol]

        sql = """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = :schema
            AND table_name = :table_name
            ORDER BY ordinal_position
        """
        rows = self._query(sql, {"schema": self.schema, "table_name": symbol})
        columns = [
            {
                "column_name": str(self._ci_get(row, "column_name")),
                "data_type": str(self._ci_get(row, "data_type")),
            }
            for row in rows
        ]
        self._columns_cache[symbol] = columns
        return columns

    def _resolve_columns_for_table(self, schema: str, table: str) -> list[dict[str, str]]:
        key = (schema, table)
        if key in self._columns_cache_by_table:
            return self._columns_cache_by_table[key]

        sql = """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = :schema
            AND table_name = :table_name
            ORDER BY ordinal_position
        """
        rows = self._query(sql, {"schema": schema, "table_name": table})
        columns = [
            {
                "column_name": str(self._ci_get(row, "column_name")),
                "data_type": str(self._ci_get(row, "data_type")),
            }
            for row in rows
        ]
        self._columns_cache_by_table[key] = columns
        self._column_lookup_cache_by_table[key] = {col["column_name"].lower(): col["column_name"] for col in columns}
        return columns

    def resolve_column(self, schema: str, table: str, column: str) -> str:
        resolved_schema = self.resolve_schema(schema)
        resolved_table = self.resolve_table(resolved_schema, table)
        normalized = column.strip()
        if not normalized:
            raise ValueError("column must be a non-empty string.")

        key = (resolved_schema, resolved_table)
        columns = self._resolve_columns_for_table(resolved_schema, resolved_table)
        available = {col["column_name"] for col in columns}
        if normalized in available:
            return normalized
        fallback = self._column_lookup_cache_by_table.get(key, {}).get(normalized.lower())
        if fallback:
            return fallback
        raise ValueError(f"Unknown column '{column}' in `{resolved_schema}`.`{resolved_table}`.")

    def _resolve_primary_key_for_table(self, schema: str, table: str) -> str | None:
        key = (schema, table)
        if key in self._primary_key_cache_by_table:
            return self._primary_key_cache_by_table[key]

        sql = """
            SELECT k.column_name
            FROM information_schema.table_constraints t
            JOIN information_schema.key_column_usage k
            ON t.constraint_name = k.constraint_name
            AND t.table_schema = k.table_schema
            AND t.table_name = k.table_name
            WHERE t.constraint_type = 'PRIMARY KEY'
            AND t.table_schema = :schema
            AND t.table_name = :table_name
            ORDER BY k.ordinal_position
            LIMIT 1
        """
        rows = self._query(sql, {"schema": schema, "table_name": table})
        primary_key = str(self._ci_get(rows[0], "column_name")) if rows else None
        self._primary_key_cache_by_table[key] = primary_key
        return primary_key

    def _resolve_primary_key(self, symbol: str) -> str | None:
        if symbol in self._primary_key_cache:
            return self._primary_key_cache[symbol]

        sql = """
            SELECT k.column_name
            FROM information_schema.table_constraints t
            JOIN information_schema.key_column_usage k
            ON t.constraint_name = k.constraint_name
            AND t.table_schema = k.table_schema
            AND t.table_name = k.table_name
            WHERE t.constraint_type = 'PRIMARY KEY'
            AND t.table_schema = :schema
            AND t.table_name = :table_name
            ORDER BY k.ordinal_position
            LIMIT 1
        """
        rows = self._query(sql, {"schema": self.schema, "table_name": symbol})
        primary_key = str(self._ci_get(rows[0], "column_name")) if rows else None
        self._primary_key_cache[symbol] = primary_key
        return primary_key

    def _resolve_searchable_columns(self, symbol: str) -> list[str]:
        columns = self._resolve_columns(symbol)
        available = {column["column_name"] for column in columns}
        preferred = [name for name in PREFERRED_TEXT_COLUMNS if name in available]
        if preferred:
            return preferred

        fallback = [
            column["column_name"]
            for column in columns
            if column["data_type"].lower() in TEXT_DATA_TYPES
        ]
        return fallback[:3]

    def _resolve_date_column(self, symbol: str) -> str | None:
        columns = self._resolve_columns(symbol)
        available = {column["column_name"] for column in columns}
        for candidate in PREFERRED_DATE_COLUMNS:
            if candidate in available:
                return candidate
        return None

    def _query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            result = connection.execute(text(sql), params)
            return [dict(row._mapping) for row in result]

    @staticmethod
    def _quote_identifier(identifier: str) -> str:
        if "\x00" in identifier:
            raise ValueError("Invalid SQL identifier.")
        return identifier.replace("`", "``")

    def _qualified_table(self, schema: str, table: str) -> str:
        return f"`{self._quote_identifier(schema)}`.`{self._quote_identifier(table)}`"

    def _extract_title(self, row: dict[str, Any]) -> str:
        for candidate in PREFERRED_TEXT_COLUMNS:
            value = row.get(candidate)
            if value:
                return str(value)
        return ""

    def _extract_body(self, row: dict[str, Any]) -> str:
        title = self._extract_title(row)
        body_parts: list[str] = []
        if title:
            body_parts.append(title)

        for candidate in ("summary", "Summary", "description", "Description", "content", "Content", "body", "Body"):
            value = row.get(candidate)
            if value:
                body_parts.append(str(value))
        return "\n\n".join(body_parts) if body_parts else str(row)

    def _document_id(self, symbol: str, row: dict[str, Any]) -> str:
        primary_key = self._resolve_primary_key(symbol)
        if primary_key and row.get(primary_key) is not None:
            return f"{symbol}:{row[primary_key]}"

        digest_input = f"{symbol}|{self._extract_title(row)}|{row.get('date', '')}"
        digest = hashlib.sha1(digest_input.encode("utf-8")).hexdigest()[:16]
        return f"{symbol}:{digest}"

    def _augment_row(self, symbol: str, row: dict[str, Any]) -> dict[str, Any]:
        output = dict(row)
        output["symbol"] = symbol
        output["document_id"] = self._document_id(symbol, row)
        return output

    def _search_result(self, symbol: str, row: dict[str, Any]) -> dict[str, Any]:
        doc_id = self._document_id(symbol, row)
        title = self._extract_title(row) or f"{symbol} news item"
        date_value = row.get("date") or row.get("Date")
        snippet = self._extract_body(row)
        if len(snippet) > 400:
            snippet = f"{snippet[:397]}..."

        return {
            "id": doc_id,
            "title": title,
            "url": f"mysql://{self.schema}/{symbol}/{doc_id.split(':', 1)[1]}",
            "text": snippet,
            "symbol": symbol,
            "date": str(date_value) if date_value is not None else None,
        }
