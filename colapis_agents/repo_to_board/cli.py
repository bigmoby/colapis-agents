"""repo_to_board: draws a GitHub repository's architecture in a Colapis Board, through MCP.

  colapis-repo-to-board owner/repo [--focus "only the backend"] [--title "Title"]
  colapis-repo-to-board https://github.com/owner/repo --dry-run -o preview.json
  colapis-repo-to-board --from-plan plan.json        create the Board from a saved plan, without AI

GitHub must be connected to your Colapis account (Board Hub → Integrations); for a
private repository the token needs the Contents permission.
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
from .layout import LayoutError, layout_architecture
from .planner import PlanError, dumps, normalize_architecture, plan_architecture

log = logger("repo_to_board")
LANGUAGES = {"en": "English", "it": "Italian", "es": "Spanish", "fr": "French", "de": "German"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="colapis-repo-to-board", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("reference", nargs="?", help="owner/repo or the repository URL")
    parser.add_argument("-f", "--focus", help="the part of the repository, or the point of view, to draw")
    parser.add_argument("-t", "--title", help="the Board's name (default: the one the LLM proposes)")
    parser.add_argument("-l", "--lang", default="en", help="language of titles, layers and labels: en (default), it…")
    parser.add_argument("--theme", choices=("light", "dark"), default="light")
    parser.add_argument("-m", "--model", help="the LLM model (default: LLM_MODEL or the provider's default)")
    parser.add_argument("--dry-run", action="store_true", help="create nothing; write the plan and the blueprint")
    parser.add_argument("-o", "--output", help="file for the --dry-run preview (default: stdout)")
    parser.add_argument("--save-plan", help="save the plan, to reuse (and edit) with --from-plan")
    parser.add_argument("--from-plan", help="use a saved plan instead of asking the LLM")
    parser.add_argument("--context", help="read the repository context from a file instead of through MCP")
    parser.add_argument("--save-context", help="save the context read through MCP")
    args = parser.parse_args()
    if not args.reference and not args.from_plan and not args.context:
        parser.print_help()
        sys.exit(0)
    return args


async def run(args: argparse.Namespace) -> None:
    load_dotenv()
    language = LANGUAGES.get(args.lang.lower(), args.lang)
    needs_llm = not args.from_plan
    needs_mcp = not args.dry_run or (needs_llm and not args.context)
    llm = LLMSettings.from_env(args.model) if needs_llm else None
    colapis_settings = ColapisSettings.from_env() if needs_mcp else None

    async with Colapis(colapis_settings) if colapis_settings else _nothing() as colapis:
        if colapis:
            log(f"Connected to {colapis_settings.server_url} ({colapis.transport}).")

        # 1. The plan: saved, or designed by the LLM from the repository data
        if args.from_plan:
            saved = json.loads(Path(args.from_plan).read_text(encoding="utf-8"))
            result = normalize_architecture(saved.get("plan", saved), None)
            if result["errors"]:
                raise PlanError(f"The plan is not valid: {' '.join(result['errors'])}")
            plan, warnings, source = result["plan"], result["warnings"], saved.get("source", {})
        else:
            if args.context:
                context = json.loads(Path(args.context).read_text(encoding="utf-8"))
            else:
                log(f"Reading {args.reference} from GitHub…")
                try:
                    context = await colapis.call("github_repository_context", reference=args.reference)
                except ColapisError as error:
                    raise ColapisError(
                        f"{error}\nCheck that GitHub is connected in Board Hub → Integrations and "
                        "that its token can read the repository."
                    ) from None
                if args.save_context:
                    Path(args.save_context).write_text(dumps(context), encoding="utf-8")
                    log(f"Context saved in {Path(args.save_context).resolve()}")
            log(
                f"{context.get('repository')}: {len(context.get('sourceFiles') or [])} source files, "
                f"{len(context.get('manifests') or [])} manifests, "
                f"languages {', '.join(context.get('languages') or []) or 'n/a'}."
            )
            started = time.monotonic()
            result = await plan_architecture(
                llm, context, focus=args.focus, language=language, on_step=lambda step: log(f"{step}…")
            )
            plan, warnings = result["plan"], result["warnings"]
            source = {"repository": context.get("repository"), "languages": context.get("languages") or []}
            usage = result["usage"]
            log(
                f"Plan ready in {time.monotonic() - started:.1f} s (tokens in {usage['input_tokens']}, "
                f"cached {usage['cache_read_input_tokens']}, out {usage['output_tokens']})."
            )
            if args.save_plan:
                Path(args.save_plan).write_text(dumps({"source": source, "plan": plan}), encoding="utf-8")
                log(f"Plan saved in {Path(args.save_plan).resolve()}")
        if args.title:
            plan["title"] = args.title.strip()

        # 2. Bands, nodes and arrows
        layout = layout_architecture(
            plan, repository=source.get("repository"), languages=source.get("languages"), language=args.lang
        )
        warnings += layout["warnings"]
        stats = layout["stats"]
        log(
            f'"{plan["title"]}": {stats["layers"]} layers, {stats["components"]} components, {stats["flows"]} flows '
            f"({stats['corridorFlows']} along the side corridors)."
        )
        for warning in warnings:
            log(f"Warning: {warning}")

        if args.dry_run:
            preview = dumps(
                {
                    "source": source,
                    "plan": plan,
                    "stats": stats,
                    "blueprint": {"version": 1, "title": plan["title"], "elements": layout["elements"]},
                }
            )
            if args.output:
                Path(args.output).write_text(preview, encoding="utf-8")
                log(f"Preview written to {Path(args.output).resolve()}")
            else:
                sys.stdout.write(preview)
            return

        # 3. The Board: created empty, then filled through MCP in one call
        board = await colapis.create_board(plan["title"], theme=args.theme)
        log(f'Created the Board "{board["name"]}" ({board["id"]}).')
        try:
            inserted = await colapis.call(
                "insert_elements", boardId=board["id"], elements=layout["elements"], applyLayout=False
            )
        except ColapisError as error:
            raise ColapisError(
                f"Insertion failed: {error}\nThe Board {board['id']} is empty: delete it from the "
                "Board Hub or try again with --from-plan."
            ) from None

        # 4. Read it back: frames, shapes and bound arrows
        scene = await colapis.call("get_board_scene", boardId=board["id"], format="raw_elements")
        live = [e for e in scene.get("elements", []) if not e.get("isDeleted")]
        frames = sum(1 for e in live if e.get("type") == "frame")
        arrows = [e for e in live if e.get("type") == "arrow"]
        bound = sum(1 for a in arrows if a.get("startBinding") and a.get("endBinding"))
        if frames != stats["layers"] or len(arrows) != stats["flows"] or bound != len(arrows):
            log(
                f"Warning: expected {stats['layers']} layers and {stats['flows']} bound arrows; the Board has "
                f"{frames} layers and {bound}/{len(arrows)} bound arrows."
            )
        log(
            f'Done: {inserted.get("insertedCount", len(live))} native elements on "{board["name"]}". '
            "Open it from the Colapis Board Hub."
        )
        print(board["id"])


class _nothing:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc: object) -> None:
        return None


def main() -> None:
    args = parse_args()
    try:
        asyncio.run(run(args))
    except (ConfigError, ColapisError, LLMError, PlanError, LayoutError, OSError, ValueError) as error:
        log(str(error))
        sys.exit(1)
