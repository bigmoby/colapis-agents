"""board_to_md: turns a Colapis Board into Markdown, through MCP.

  colapis-board-to-md --board <boardId> [--out file.md]
  colapis-board-to-md --search "Sprint" [--out-dir output]
  colapis-board-to-md "Export the October retrospective, only the frames with action items"
  colapis-board-to-md --list [--search text]      list the Boards (no AI)
  colapis-board-to-md --tools                     the read-only MCP tools (no AI)

The agent only reads: the model is offered list_boards, get_board_scene and
export_board_presentation, never the tools that write.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

from ..colapis import Colapis, ColapisError
from ..config import ColapisSettings, ConfigError, LLMSettings, load_dotenv
from ..llm import LLMError
from ..log import logger
from .agent import AgentError, board_to_markdown, read_only_tools

log = logger("board_to_md")


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")[:80] or "board"


def build_request(board: str | None, search: str | None, prompt: str) -> str:
    if prompt:
        return f"{prompt}\n\nBoard id: {board}" if board else prompt
    if board:
        return f'Export the Colapis Board with id "{board}" to Markdown.'
    return f'Find the Colapis Board whose name best matches "{search}" and export it to Markdown.'


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="colapis-board-to-md", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("prompt", nargs="*", help="a free request, instead of --board or --search")
    parser.add_argument("-b", "--board", help="the Board's id")
    parser.add_argument("-s", "--search", help="find the Board by name")
    parser.add_argument("-o", "--out", help='the Markdown file to write ("-" = stdout)')
    parser.add_argument("-d", "--out-dir", default="output", help="the folder to write to (default: output/)")
    parser.add_argument("-l", "--lang", help="language of headings and metadata (default: the Board's)")
    parser.add_argument("-m", "--model", help="the LLM model (default: LLM_MODEL or the provider's default)")
    parser.add_argument("--list", action="store_true", help="list the Boards")
    parser.add_argument("--tools", action="store_true", help="list the read-only MCP tools")
    args = parser.parse_args()
    args.prompt = " ".join(args.prompt).strip()
    if not (args.list or args.tools or args.board or args.search or args.prompt):
        parser.print_help()
        sys.exit(0)
    return args


async def run(args: argparse.Namespace) -> None:
    load_dotenv()
    llm = None if args.list or args.tools else LLMSettings.from_env(args.model)
    settings = ColapisSettings.from_env()
    async with Colapis(settings) as colapis:
        log(f"Connected to {settings.server_url} ({colapis.transport}).")
        if args.tools:
            for tool in await read_only_tools(colapis):
                print(f"- {tool['name']}: {tool['description']}")
            return
        if args.list:
            found = await colapis.call("list_boards", limit=200, search=args.search)
            for board in found.get("boards", []):
                flag = " [private]" if board.get("isPrivate") else ""
                print(
                    f"{board['id']}\t{str(board.get('updatedAt', ''))[:10]}\t{board.get('elementCount', 0)} el."
                    f"\t{board['name']}{flag}"
                )
            log(f"{len(found.get('boards', []))} Boards.")
            return

        started = time.monotonic()
        result = await board_to_markdown(
            colapis,
            llm,
            build_request(args.board, args.search, args.prompt),
            language=args.lang,
            on_tool_call=lambda name, arguments: log(f"→ {name} {json.dumps(arguments, ensure_ascii=False)}"),
        )
        markdown = result["markdown"]
        if args.out == "-":
            sys.stdout.write(markdown)
        else:
            heading = re.search(r"^#\s+(.+)$", markdown, re.M)
            title = heading.group(1) if heading else (result["board_ids"] or ["board"])[0]
            out = Path(args.out) if args.out else Path(args.out_dir) / f"{slugify(title)}.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(markdown, encoding="utf-8")
            log(f"Wrote {out.resolve()}")
        usage = result["usage"]
        log(
            f"{time.monotonic() - started:.1f} s, tokens in {usage['input_tokens']} "
            f"(cached {usage['cache_read_input_tokens']}), out {usage['output_tokens']}."
        )


def main() -> None:
    args = parse_args()
    try:
        asyncio.run(run(args))
    except (ConfigError, ColapisError, LLMError, AgentError, OSError, ValueError) as error:
        log(str(error))
        sys.exit(1)
