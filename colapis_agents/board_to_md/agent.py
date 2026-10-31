"""The agent loop: the LLM reads the Board through read-only Colapis MCP tools and
answers with the Markdown document."""

from __future__ import annotations

import asyncio
import datetime as dt
import re
from collections.abc import Callable

from ..colapis import Colapis
from ..config import LLMSettings
from ..llm import Conversation, LLMError, OutputTruncated

# The agent only reads: the tools that write (generate_board, insert_elements,
# update_task_card, the Jira and GitHub imports) are never offered to the model,
# and a call to them is refused here too.
READ_ONLY_TOOLS = ("list_boards", "get_board_scene", "export_board_presentation")
# Beyond this size a tool result is cut, and the model is told so
MAX_TOOL_RESULT_CHARS = 400_000
# Upper bound on model turns, so a confused run cannot loop forever
MAX_TURNS = 16

SYSTEM_PROMPT = """You are "board_to_md", an agent that turns a Colapis Board into a faithful Markdown document.

Colapis is a collaborative whiteboard. You reach it only through read-only MCP tools:
- list_boards: find a Board by name (use "search") when you are not given an id.
- get_board_scene: read a Board. Start with format "summary" (frames, task cards, sticky notes, free texts). Use format "raw_elements" only when the summary is not enough to rebuild the structure, for example to read shape labels, arrows (startBinding/endBinding, or boundElements) and which element sits in which frame.
- export_board_presentation: frames in slide order with speaker notes and the texts each one contains. Call it whenever the Board has frames.

How to work:
1. Identify exactly one Board. If the request names a Board by id, use it. If several Boards match a name, choose the closest match and say which one you picked in the document metadata; if none matches, stop and answer with a single line starting with "ERROR:" explaining why.
2. A Board that is private (isPrivate: true) is end-to-end encrypted and cannot be read by the server: answer with a single line starting with "ERROR:".
3. Read what you need, then write the document. Do not call more tools than needed.

How the document must look:
- First line: "# <Board name>".
- Then a short metadata block as a blockquote: Board id, last update, number of elements, and the date of this export.
- Then a one-paragraph overview of what the Board is about, based only on its content.
- One "##" section per frame, in slide order (customData.slideOrder), using the frame name as heading. Speaker notes go in a "> **Speaker notes:**" blockquote under the heading.
- Inside each section, in reading order (top to bottom, left to right):
  - Task cards as a Markdown table with columns Title, Description, Priority, Assignee (leave a cell empty when the value is missing).
  - Sticky notes as a bullet list, one note per item.
  - Free texts as paragraphs, headings or lists, following how they are written.
  - Connections from arrows as a list "A → B" (with the arrow label, if any), or a ```mermaid flowchart when there are more than five connections.
- Elements outside any frame go in a final "## Outside frames" section, omitted when empty.
- Keep the Board's own language and wording. Never invent, summarise away or reorder items beyond what is needed to read them in order; keep every task card and sticky note.
- Escape "|" inside table cells.

Your final message must contain only the Markdown document, with no preface, no comments and no surrounding code fence."""


class AgentError(RuntimeError):
    pass


def build_request(request: str, language: str | None) -> str:
    lines = [request.strip(), "", f"Export date: {dt.date.today().isoformat()}."]
    if language:
        lines.append(
            f"Write headings, metadata and connecting text in this language: {language}. "
            "Keep the Board's own content as it is."
        )
    return "\n".join(lines)


def strip_fence(text: str) -> str:
    match = re.match(r"^```(?:markdown|md)?\s*\n([\s\S]*?)\n```\s*$", text)
    return (match.group(1) if match else text).strip() + "\n"


def limit_text(text: str) -> str:
    if len(text) <= MAX_TOOL_RESULT_CHARS:
        return text
    return (
        f"{text[:MAX_TOOL_RESULT_CHARS]}\n\n[TRUNCATED: the result had {len(text)} characters; showing the "
        f'first {MAX_TOOL_RESULT_CHARS}. Prefer format "summary" and export_board_presentation.]'
    )


async def read_only_tools(colapis: Colapis) -> list[dict]:
    tools = [tool for tool in await colapis.tools() if tool["name"] in READ_ONLY_TOOLS]
    missing = [name for name in READ_ONLY_TOOLS if name not in {tool["name"] for tool in tools}]
    if missing:
        raise AgentError(f"The MCP server does not offer the tools: {', '.join(missing)}.")
    return tools


async def call_read_only(colapis: Colapis, name: str, arguments: dict) -> str:
    if name not in READ_ONLY_TOOLS:
        return f"TOOL ERROR: the tool {name} is not available: this agent only reads."
    try:
        text, is_error = await colapis.call_raw(name, arguments)
    except Exception as error:  # noqa: BLE001 - the model reads the error and can recover
        return f"TOOL ERROR: MCP error ({name}): {error}"
    text = limit_text(text)
    return f"TOOL ERROR: {text}" if is_error else text


async def board_to_markdown(
    colapis: Colapis,
    settings: LLMSettings,
    request: str,
    *,
    language: str | None = None,
    on_tool_call: Callable[[str, object], None] = lambda name, arguments: None,
) -> dict:
    """Returns {"markdown", "board_ids", "usage"}."""
    # The system prompt and the tools stay first and unchanged, so the provider's
    # context cache serves them from the second request on.
    conversation = Conversation(settings, SYSTEM_PROMPT, tools=await read_only_tools(colapis))
    conversation.add_user(build_request(request, language))
    board_ids: list[str] = []
    for _ in range(MAX_TURNS):
        try:
            reply = await conversation.reply()
        except OutputTruncated:
            raise AgentError(
                "The document exceeded the model's output limit: try a smaller Board, or one frame at a time."
            ) from None
        if reply.tool_calls:

            async def answer(call) -> tuple[str, str]:
                on_tool_call(call.name, call.arguments if call.arguments is not None else call.raw_arguments)
                if call.arguments is None:
                    return call.id, "Invalid arguments: send a JSON object that follows the tool's schema."
                board_id = call.arguments.get("boardId")
                if isinstance(board_id, str) and board_id not in board_ids:
                    board_ids.append(board_id)
                return call.id, await call_read_only(colapis, call.name, call.arguments)

            conversation.add_tool_results(list(await asyncio.gather(*(answer(c) for c in reply.tool_calls))))
            continue
        text = reply.content.strip()
        if re.match(r"^(ERROR|ERRORE):", text):
            raise AgentError(re.sub(r"^(ERROR|ERRORE):\s*", "", text))
        if not text:
            raise LLMError("The model returned no document.")
        return {"markdown": strip_fence(text), "board_ids": board_ids, "usage": conversation.usage}
    raise AgentError(f"No document after {MAX_TURNS} turns.")
