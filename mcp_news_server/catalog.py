"""Small, file-backed semantic catalog for database discovery."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

import yaml


_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP_WORDS = {
    "a",
    "about",
    "all",
    "an",
    "and",
    "are",
    "data",
    "database",
    "do",
    "find",
    "for",
    "from",
    "get",
    "give",
    "i",
    "in",
    "is",
    "me",
    "of",
    "on",
    "show",
    "table",
    "that",
    "the",
    "to",
    "want",
    "what",
    "where",
    "with",
}


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in _WORD_RE.findall(value.lower())
        if token not in _STOP_WORDS
    }


def _flatten_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        output: list[str] = []
        for key, nested_value in value.items():
            output.append(str(key).replace("_", " "))
            output.extend(_flatten_strings(nested_value))
        return output
    if isinstance(value, list):
        output = []
        for item in value:
            output.extend(_flatten_strings(item))
        return output
    return [] if value is None else [str(value)]


class DatabaseCatalog:
    """Load semantic table descriptions and find relevant entries."""

    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self._entries = entries

    @classmethod
    def from_yaml(cls, path: str | Path) -> "DatabaseCatalog":
        catalog_path = Path(path)
        with catalog_path.open("r", encoding="utf-8") as handle:
            document = yaml.safe_load(handle)

        if not isinstance(document, dict) or not isinstance(document.get("tables"), dict):
            raise ValueError("Database catalog must contain a 'tables' mapping.")

        entries: list[dict[str, Any]] = []
        for qualified_name, raw_entry in document["tables"].items():
            if not isinstance(qualified_name, str) or qualified_name.count(".") != 1:
                raise ValueError(f"Invalid catalog table name: {qualified_name!r}.")
            if not isinstance(raw_entry, dict):
                raise ValueError(f"Catalog entry for {qualified_name!r} must be a mapping.")

            schema, table = qualified_name.split(".", 1)
            description = raw_entry.get("description")
            if not schema or not table or not isinstance(description, str) or not description.strip():
                raise ValueError(
                    f"Catalog entry {qualified_name!r} requires a non-empty description."
                )

            entry = {
                "schema": schema,
                "table": table,
                "qualified_name": qualified_name,
                **raw_entry,
            }
            entries.append(entry)

        return cls(entries)

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    def search(
        self,
        query: str,
        limit: int = 5,
        is_available: Callable[[str, str], bool] | None = None,
    ) -> list[dict[str, Any]]:
        """Return catalog entries ranked by simple deterministic keyword matching."""
        normalized_query = query.strip().lower()
        if not normalized_query:
            raise ValueError("query must be a non-empty string.")
        if limit < 1 or limit > 20:
            raise ValueError("limit must be between 1 and 20.")

        query_tokens = _tokens(normalized_query)
        ranked: list[tuple[int, str, dict[str, Any]]] = []

        for entry in self._entries:
            aliases = [
                alias.lower()
                for alias in entry.get("aliases", [])
                if isinstance(alias, str)
            ]
            name_text = entry["qualified_name"].replace("_", " ").lower()
            description_text = entry["description"].lower()
            all_text = " ".join(_flatten_strings(entry)).lower()
            all_tokens = _tokens(all_text)

            score = 0
            if normalized_query == entry["qualified_name"].lower():
                score += 100
            if normalized_query in name_text:
                score += 30
            if any(alias == normalized_query for alias in aliases):
                score += 40
            score += 15 * sum(1 for alias in aliases if alias in normalized_query)
            score += 6 * len(query_tokens & _tokens(name_text))
            score += 4 * len(query_tokens & set().union(*(_tokens(alias) for alias in aliases)))
            score += 2 * len(query_tokens & _tokens(description_text))
            score += len(query_tokens & all_tokens)

            if score:
                result = dict(entry)
                result["relevance_score"] = score
                ranked.append((score, entry["qualified_name"], result))

        ranked.sort(key=lambda item: (-item[0], item[1]))
        results: list[dict[str, Any]] = []
        top_available_score: int | None = None
        for score, _, result in ranked:
            if is_available and not is_available(result["schema"], result["table"]):
                continue
            if top_available_score is None:
                top_available_score = score
            elif score * 20 < top_available_score * 7:
                break
            results.append(result)
            if len(results) == limit:
                break
        return results
