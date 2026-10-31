"""A small client for Colapis: its MCP tools, and the REST call that creates a Board.

The MCP server has no tool that creates an empty Board, so that one call goes
through the REST API (POST /api/v1/boards) with the same agent key; the content
then arrives through MCP. The key travels only in the Authorization header,
never in a URL.
"""

from __future__ import annotations

import json
import re
from contextlib import AsyncExitStack
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client

from .config import ColapisSettings

CLIENT_TIMEOUT = httpx2.Timeout(30, read=300)


class ColapisError(RuntimeError):
    def __init__(self, message: str, code: str = "COLAPIS_ERROR") -> None:
        super().__init__(message)
        self.code = code


def _is_auth_error(error: BaseException) -> bool:
    return bool(re.search(r"\b401\b|AUTH_REQUIRED|INVALID_API_KEY", repr(error)))


class Colapis:
    """``async with Colapis(settings) as colapis: await colapis.call("list_boards")``

    Connects with Streamable HTTP on /api/mcp, or with the legacy SSE endpoint
    /api/mcp/sse on older instances, and closes the session on exit.
    """

    def __init__(self, settings: ColapisSettings) -> None:
        self.settings = settings
        self.transport = ""
        self._stack = AsyncExitStack()
        self._session: ClientSession | None = None
        self._http: httpx2.AsyncClient | None = None

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.settings.api_key}"}

    async def __aenter__(self) -> Colapis:
        base = self.settings.server_url
        self._http = await self._stack.enter_async_context(
            httpx2.AsyncClient(headers=self.headers, timeout=CLIENT_TIMEOUT)
        )
        try:
            self._session = await self._open(
                streamable_http_client(f"{base}/api/mcp", http_client=self._http), "streamable-http"
            )
        except Exception as streamable_error:  # noqa: BLE001 - every failure tries the fallback
            if _is_auth_error(streamable_error):
                await self._stack.aclose()
                raise ColapisError(
                    "Colapis refused the key: check that COLAPIS_API_KEY is valid and not revoked.",
                    "AUTH_REQUIRED",
                ) from None
            try:
                self._session = await self._open(sse_client(f"{base}/api/mcp/sse", headers=self.headers), "sse")
            except Exception as sse_error:  # noqa: BLE001
                await self._stack.aclose()
                raise ColapisError(
                    f"Could not connect to the MCP server at {base} "
                    f"(Streamable HTTP: {streamable_error!r}; SSE: {sse_error!r}).",
                    "MCP_UNREACHABLE",
                ) from None
        return self

    async def _open(self, transport, name: str) -> ClientSession:
        stack = AsyncExitStack()
        try:
            read, write = await stack.enter_async_context(transport)
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        except BaseException:
            await stack.aclose()
            raise
        self._stack.push_async_callback(stack.aclose)
        self.transport = name
        return session

    async def __aexit__(self, *exc: object) -> None:
        await self._stack.aclose()

    async def tool_names(self) -> list[str]:
        assert self._session, "use Colapis as an async context manager"
        return [tool.name for tool in (await self._session.list_tools()).tools]

    async def tools(self) -> list[dict[str, Any]]:
        """Every MCP tool as name, description and JSON Schema."""
        assert self._session, "use Colapis as an async context manager"
        return [
            {"name": tool.name, "description": tool.description or "", "input_schema": tool.input_schema}
            for tool in (await self._session.list_tools()).tools
        ]

    async def call_raw(self, tool: str, arguments: dict[str, Any] | None = None) -> tuple[str, bool]:
        """Calls an MCP tool and returns its text and whether it is an error."""
        assert self._session, "use Colapis as an async context manager"
        result = await self._session.call_tool(tool, arguments or {})
        text = "\n".join(
            part.text if getattr(part, "type", "") == "text" else json.dumps(part.model_dump(), ensure_ascii=False)
            for part in result.content
        )
        return text, bool(result.is_error)

    async def call(self, tool: str, **arguments: Any) -> Any:
        """Calls an MCP tool and decodes its JSON answer; a tool error raises."""
        text, is_error = await self.call_raw(tool, {k: v for k, v in arguments.items() if v is not None})
        if is_error:
            raise ColapisError(f"{tool}: {text}", "TOOL_ERROR")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}

    async def create_board(self, name: str, *, theme: str = "light", blueprint: dict | None = None) -> dict:
        """Creates a Board, empty or from a blueprint (POST /api/v1/boards)."""
        assert self._http, "use Colapis as an async context manager"
        body: dict[str, Any] = {"name": name[:100], "theme": theme}
        if blueprint:
            body["blueprint"] = blueprint
        response = await self._http.post(f"{self.settings.server_url}/api/v1/boards", json=body)
        try:
            answer = response.json()
        except ValueError:
            answer = {}
        if response.status_code >= 400 or not answer.get("board", {}).get("id"):
            hint = {401: "the Colapis key is not valid"}.get(response.status_code)
            if answer.get("code") == "BOARD_LIMIT_REACHED":
                hint = f"the account reached its maximum number of Boards ({answer.get('maxBoards', '?')})"
            raise ColapisError(
                f"Could not create the Board ({response.status_code}): {hint or answer.get('error') or 'error'}",
                answer.get("code") or "CREATE_FAILED",
            )
        return answer["board"]
