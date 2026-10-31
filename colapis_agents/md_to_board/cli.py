"""md_to_board: creates a Colapis Board from a Markdown file, through MCP.

colapis-md-to-board document.md [--title "Title"] [--theme dark]
colapis-md-to-board document.md --dry-run [-o preview.json]   plan and blueprint, nothing created
colapis-md-to-board --from-plan plan.json                     create the Board from a saved plan, without AI
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from ..colapis import Colapis, ColapisError
from ..config import ColapisSettings, ConfigError, LLMSettings, load_dotenv
from ..llm import LLMError
from ..log import logger
from .layout import LayoutError, layout_plan
from .planner import PlanError, normalize_plan, plan_board

log = logger("md_to_board")


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="colapis-md-to-board", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("markdown", nargs="?", help="the Markdown file")
    parser.add_argument("-t", "--title", help="the Board's name (default: the document's title)")
    parser.add_argument("--theme", choices=("light", "dark"), default="light")
    parser.add_argument("-m", "--model", help="the LLM model (default: LLM_MODEL or the provider's default)")
    parser.add_argument("--notes-label", default="Speaker notes", help="the label of speaker notes on the Board")
    parser.add_argument("--dry-run", action="store_true", help="create nothing; write the plan and the blueprint")
    parser.add_argument("-o", "--output", help="file for the --dry-run preview (default: stdout)")
    parser.add_argument("--save-plan", help="save the plan, to reuse (and edit) with --from-plan")
    parser.add_argument("--from-plan", help="use a saved plan instead of asking the LLM")
    args = parser.parse_args()
    if not args.markdown and not args.from_plan:
        parser.print_help()
        sys.exit(0)
    return args


async def run(args: argparse.Namespace) -> None:
    load_dotenv()
    # 1. The plan: from the LLM, or from a saved file
    if args.from_plan:
        result = normalize_plan(json.loads(Path(args.from_plan).read_text(encoding="utf-8")))
        if result["errors"]:
            raise PlanError(f"The plan is not valid: {' '.join(result['errors'])}")
        plan, warnings = result["plan"], result["warnings"]
    else:
        path = Path(args.markdown)
        if not path.is_file():
            raise PlanError(f"File not found: {args.markdown}")
        markdown = path.read_text(encoding="utf-8")
        if not markdown.strip():
            raise PlanError(f"The file {args.markdown} is empty.")
        llm = LLMSettings.from_env(args.model)
        if not args.dry_run:
            ColapisSettings.from_env()  # fail before spending tokens
        started = time.monotonic()
        result = await plan_board(llm, markdown, file_name=path.name, on_step=lambda step: log(f"{step}…"))
        plan, warnings, usage = result["plan"], result["warnings"], result["usage"]
        log(
            f"Plan ready in {time.monotonic() - started:.1f} s (tokens in {usage['input_tokens']}, "
            f"cached {usage['cache_read_input_tokens']}, out {usage['output_tokens']})."
        )
        if args.save_plan:
            Path(args.save_plan).write_text(dumps(plan), encoding="utf-8")
            log(f"Plan saved in {Path(args.save_plan).resolve()}")
    if args.title:
        plan["title"] = args.title.strip()

    # 2. Positions, sizes and arrows
    layout = layout_plan(plan, notes_label=args.notes_label)
    warnings += layout["warnings"]
    stats, batches = layout["stats"], layout["batches"]
    log(
        f'Board "{plan["title"]}": {stats["frames"]} frames, {stats["taskCards"]} task cards, '
        f"{stats['stickyNotes']} sticky notes, {stats['shapes']} shapes, {stats['texts']} texts, "
        f"{stats['arrows']} arrows ({len(batches)} insertion{'' if len(batches) == 1 else 's'})."
    )
    for warning in warnings:
        log(f"Warning: {warning}")

    if args.dry_run:
        preview = dumps(
            {
                "plan": plan,
                "blueprint": {"version": 1, "title": plan["title"], "elements": layout["elements"]},
                "batches": [len(batch) for batch in batches],
                "stats": stats,
            }
        )
        if args.output:
            Path(args.output).write_text(preview, encoding="utf-8")
            log(f"Preview written to {Path(args.output).resolve()}")
        else:
            sys.stdout.write(preview)
        return

    # 3. The Board: created empty, then filled through MCP
    settings = ColapisSettings.from_env()
    async with Colapis(settings) as colapis:
        log(f"Connected to {settings.server_url} ({colapis.transport}).")
        tools = await colapis.tool_names()
        for name in ("insert_elements", "get_board_scene"):
            if name not in tools:
                raise ColapisError(f"The MCP server does not offer the tool {name}.")
        board = await colapis.create_board(plan["title"], theme=args.theme)
        log(f'Created the Board "{board["name"]}" ({board["id"]}).')
        inserted = 0
        for index, batch in enumerate(batches, 1):
            try:
                result = await colapis.call("insert_elements", boardId=board["id"], elements=batch, applyLayout=False)
            except ColapisError as error:
                raise ColapisError(
                    f"Insertion {index}/{len(batches)} failed: {error}\nThe Board {board['id']} is "
                    "incomplete: delete it from the Board Hub or try again with --from-plan."
                ) from None
            inserted += result.get("insertedCount", 0)
            if len(batches) > 1:
                log(f"Insertion {index}/{len(batches)} done.")

        # 4. Read it back, to check that everything arrived
        summary = await colapis.call("get_board_scene", boardId=board["id"], format="summary")
        checks = [
            ("frames", stats["frames"], len(summary.get("frames") or [])),
            ("task cards", stats["taskCards"], len(summary.get("taskCards") or [])),
            ("sticky notes", stats["stickyNotes"], len(summary.get("stickyNotes") or [])),
        ]
        mismatches = [check for check in checks if check[1] != check[2]]
        for what, expected, actual in mismatches:
            log(f"Warning: expected {expected} {what}, the Board has {actual}.")
        log(
            f'{"Done with differences" if mismatches else "Done"}: {inserted} native elements on "{board["name"]}". '
            "Open it from the Colapis Board Hub."
        )
        print(board["id"])


def main() -> None:
    args = parse_args()
    try:
        asyncio.run(run(args))
    except (ConfigError, ColapisError, LLMError, PlanError, LayoutError, OSError, ValueError) as error:
        log(str(error))
        sys.exit(1)
