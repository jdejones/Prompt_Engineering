"""Quick smoke test for the deployed MCP server via OpenAI Responses API."""

from __future__ import annotations

import os
import sys

from openai import OpenAI


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def main() -> None:
    model = os.getenv("OPENAI_MODEL", "o4-mini-deep-research")
    server_url = require_env("MCP_SERVER_URL")
    openai_api_key = require_env("OPENAI_API_KEY")

    client = OpenAI(api_key=openai_api_key)
    response = client.responses.create(
        model=model,
        input=(
            "For AAPL, identify today's news updates that could impact price. "
            "Use MCP tools, then give concise reasoning and cite sources."
        ),
        tools=[
            {
                "type": "mcp",
                "server_label": "mysql_news",
                "server_url": server_url,
                "allowed_tools": ["list_symbols", "search", "fetch", "get_symbol_news"],
                "require_approval": "never",
            }
        ],
    )
    print(response.output_text)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        raise
