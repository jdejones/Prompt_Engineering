"""MySQL query layer for symbol-table news data."""

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

    def get_symbol_news(self, symbol: str, date_from: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        normalized_symbol = self._validate_symbol(symbol)
        row_limit = self._safe_limit(limit)
        date_column = self._resolve_date_column(normalized_symbol)
        query_params: dict[str, Any] = {"limit": row_limit}

        where_clause = ""
        order_clause = ""
        if date_from and date_column:
            self._validate_date(date_from)
            where_clause = f" WHERE `{date_column}` >= :date_from"
            query_params["date_from"] = f"{date_from} 00:00:00"
            order_clause = f" ORDER BY `{date_column}` DESC"
        elif date_column:
            order_clause = f" ORDER BY `{date_column}` DESC"

        sql = f"SELECT * FROM `{normalized_symbol}`{where_clause}{order_clause} LIMIT :limit"
        rows = self._query(sql, query_params)
        return [self._augment_row(normalized_symbol, row) for row in rows]

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
            text_clauses = [f"`{column}` LIKE :pattern" for column in search_columns]
            query_params: dict[str, Any] = {"pattern": f"%{cleaned_query}%"}
            where_sql = f"({' OR '.join(text_clauses)})"

            if date_from and date_column:
                where_sql = f"{where_sql} AND `{date_column}` >= :date_from"
                query_params["date_from"] = f"{date_from} 00:00:00"

            table_limit = min(row_limit - len(results), self.max_rows)
            query_params["limit"] = table_limit
            order_clause = f" ORDER BY `{date_column}` DESC" if date_column else ""
            sql = (
                f"SELECT * FROM `{symbol}` "
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

        sql = f"SELECT * FROM `{normalized_symbol}` WHERE `{primary_key}` = :pk LIMIT 1"
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

    def _validate_date(self, value: str) -> None:
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("date_from must be in YYYY-MM-DD format.") from exc

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
        self._symbols_cache = {row["table_name"] for row in rows}
        self._symbol_lookup_cache = {name.lower(): name for name in self._symbols_cache}

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
        columns = [{"column_name": row["column_name"], "data_type": row["data_type"]} for row in rows]
        self._columns_cache[symbol] = columns
        return columns

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
        primary_key = rows[0]["column_name"] if rows else None
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
