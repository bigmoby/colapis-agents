"""The Board plan of a Markdown document: what each piece becomes, decided by the LLM and checked here."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from ..config import LLMSettings
from ..llm import Conversation, parse_json

SYSTEM_PROMPT = """You convert a Markdown document into a JSON plan for a Colapis Board, a collaborative whiteboard. You decide what each piece of content becomes; positions and sizes are computed later, never by you. Reply with one JSON object and nothing else.

JSON shape:
{
  "title": string,
  "intro": [string],
  "sections": [
    {
      "title": string | null,
      "notes": string | null,
      "blocks": [Block]
    }
  ],
  "connections": [{ "from": id, "to": id, "label": string | null }]
}

Block is one of:
- { "kind": "heading", "text": string }  a sub-heading inside a section (###, ####)
- { "kind": "text", "text": string }  a paragraph, quote, code block, or a list that reads as prose
- { "kind": "sticky", "id": id, "text": string, "color": "yellow" | "blue" | "green" | "pink" | "orange" | "purple" }
- { "kind": "task", "id": id, "title": string, "description": string, "priority": "High" | "Medium" | "Low", "assignee": string }
- { "kind": "node", "id": id, "label": string, "shape": "rectangle" | "ellipse" | "diamond" }

Rules:
- title: the "#" heading, or a short title taken from the content.
- Each "##" heading starts a section, which becomes a frame. Content between the title and the first "##" goes in intro, one string per paragraph. A section called "Outside frames", "Fuori dai frame" or similar has title null.
- No "##" headings: make sections from "###" headings, or one section with the document title.
- notes: the section's speaker notes ("Speaker notes", "Note del relatore"), otherwise null.
- Tasks: rows of a table of work items (columns such as Title, Description, Priority, Assignee, Status, in any language) and checkbox items "- [ ]" / "- [x]" become one task block each. Priority: high/critical/urgent (or alta/critica) → High, low (or bassa) → Low, otherwise Medium. Assignee without "@", "" when missing. Put other columns (status, dates, estimates) in description as "Status: …", in the document's language.
- Other tables: one text block, one line per row, cells joined with " · ".
- Bullet items that are short independent ideas, notes, risks, questions or decisions become one sticky block each; their nested items stay in the same sticky on new lines starting with "– ". Lists that explain something step by step stay one text block. Choose colours by meaning and keep them consistent: yellow for ideas and notes, green for positives, pink for problems and risks, blue for questions and information, orange for decisions and actions, purple for everything else.
- Diagrams: a ```mermaid flowchart or graph, and lists of connections such as "A → B", "A -> B", "A --> B: label", become one node block per distinct node plus connections. Shape: diamond for decisions ({…} in Mermaid, or a question), ellipse for start and end ((…) or ([…]) in Mermaid), otherwise rectangle. When a connection names an item that already exists as a sticky or task in the document (same text), connect that item instead of creating a node. For other Mermaid kinds, participants, entities and states become nodes; messages, relations and transitions become connections with their label.
- Add a connection from prose only when the document states it explicitly ("X depends on Y", "X feeds Y").
- Ignore export metadata: blockquotes giving a Board id, last update, element count or export date, and horizontal rules.
- Keep all content, in document order, in the document's language and exact wording. Never summarise, translate, merge or invent items. Remove Markdown markup (**, _, `, links become their text) but keep line breaks inside a block.
- ids: unique, made of letters, digits, "_" or "-" (for example "n1", "t3", "s2"). Every connection's from and to must be the id of a sticky, task or node block."""

COLORS = {"yellow", "blue", "green", "pink", "orange", "purple"}
SHAPES = {"rectangle", "ellipse", "diamond"}
PRIORITIES = {"High", "Medium", "Low"}
CONNECTABLE = {"sticky", "task", "node"}
ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class PlanError(RuntimeError):
    pass


def as_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return "" if value is None else str(value).strip()


def normalize_plan(raw: Any) -> dict:
    """Checks the plan and fills in defaults: {"errors", "warnings", "plan"}.

    Errors are problems the model must fix; small ones are corrected and reported as warnings.
    """
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(raw, dict):
        return {"errors": ["The answer is not a JSON object."], "warnings": warnings, "plan": None}

    ids: dict[str, str] = {}
    generated = 0

    def claim_id(wanted: Any, kind: str) -> str:
        nonlocal generated
        id_ = as_text(wanted)
        if not ID_PATTERN.match(id_) or id_ in ids:
            if id_ and id_ in ids:
                errors.append(f'Duplicate id "{id_}": every block needs its own id.')
            while True:
                generated += 1
                id_ = f"{kind[0]}_auto{generated}"
                if id_ not in ids:
                    break
        ids[id_] = kind
        return id_

    sections = []
    for section_index, section in enumerate(raw.get("sections") if isinstance(raw.get("sections"), list) else []):
        section = section if isinstance(section, dict) else {}
        blocks = []
        for block_index, block in enumerate(section.get("blocks") if isinstance(section.get("blocks"), list) else []):
            where = f"sections[{section_index}].blocks[{block_index}]"
            block = block if isinstance(block, dict) else {}
            kind = block.get("kind")
            if kind in ("heading", "text"):
                value = as_text(block.get("text"))
                if value:
                    blocks.append({"kind": kind, "text": value})
            elif kind == "sticky":
                value = as_text(block.get("text"))
                if value:
                    blocks.append(
                        {
                            "kind": "sticky",
                            "id": claim_id(block.get("id"), "sticky"),
                            "text": value,
                            "color": block.get("color") if block.get("color") in COLORS else "yellow",
                        }
                    )
            elif kind == "task":
                title = as_text(block.get("title"))
                if not title:
                    errors.append(f"{where}: a task needs a title.")
                    continue
                blocks.append(
                    {
                        "kind": "task",
                        "id": claim_id(block.get("id"), "task"),
                        "title": title,
                        "description": as_text(block.get("description")),
                        "priority": block.get("priority") if block.get("priority") in PRIORITIES else "Medium",
                        "assignee": re.sub(r"^@", "", as_text(block.get("assignee"))),
                    }
                )
            elif kind == "node":
                label = as_text(block.get("label"))
                if not label:
                    errors.append(f"{where}: a node needs a label.")
                    continue
                blocks.append(
                    {
                        "kind": "node",
                        "id": claim_id(block.get("id"), "node"),
                        "label": label,
                        "shape": block.get("shape") if block.get("shape") in SHAPES else "rectangle",
                    }
                )
            else:
                errors.append(f'{where}: unknown kind "{kind}".')
        sections.append(
            {
                "title": None if section.get("title") is None else as_text(section.get("title")) or None,
                "notes": as_text(section.get("notes")) or None,
                "blocks": blocks,
            }
        )

    connections = []
    for index, connection in enumerate(raw.get("connections") if isinstance(raw.get("connections"), list) else []):
        connection = connection if isinstance(connection, dict) else {}
        source, target = as_text(connection.get("from")), as_text(connection.get("to"))
        problems = [f'"{id_}"' for id_ in (source, target) if ids.get(id_) not in CONNECTABLE]
        if problems:
            errors.append(
                f"connections[{index}]: {' and '.join(problems)} is not the id of a sticky, task or node block."
            )
            continue
        if source == target:
            warnings.append(f'Ignored a connection of "{source}" to itself.')
            continue
        connections.append({"from": source, "to": target, "label": as_text(connection.get("label")) or None})

    intro = [
        part for part in (as_text(p) for p in (raw.get("intro") if isinstance(raw.get("intro"), list) else [])) if part
    ]
    plan = {
        "title": as_text(raw.get("title")) or "Board from Markdown",
        "intro": intro,
        "sections": [s for s in sections if s["blocks"] or s["notes"]],
        "connections": connections,
    }
    if not plan["intro"] and not plan["sections"]:
        errors.append("The plan has no content: fill sections with the document's blocks.")
    return {"errors": errors, "warnings": warnings, "plan": plan}


async def plan_board(
    settings: LLMSettings,
    markdown: str,
    *,
    file_name: str | None = None,
    on_step: Callable[[str], None] = lambda step: None,
) -> dict:
    """Asks the LLM for the plan of a Markdown document, with one correction round
    when the plan is not valid. Returns {"plan", "warnings", "usage"}."""
    conversation = Conversation(settings, SYSTEM_PROMPT, json_mode=True)
    conversation.add_user(
        f"Convert this Markdown document{f' ({file_name})' if file_name else ''} into the JSON "
        f"plan.\n\n<document>\n{markdown}\n</document>"
    )
    for attempt in (1, 2):
        on_step("Reading the Markdown" if attempt == 1 else "Correcting the plan")
        reply = await conversation.reply()
        result = normalize_plan(parse_json(reply.content))
        if not result["errors"]:
            return {**result, "usage": conversation.usage}
        if attempt == 2:
            plan = result["plan"]
            if not plan or (not plan["intro"] and not plan["sections"]):
                raise PlanError(f"The plan is not valid: {' '.join(result['errors'])}")
            # Keep what is valid; what the model could not fix is reported
            return {
                "plan": plan,
                "warnings": result["warnings"] + [f"Dropped: {e}" for e in result["errors"]],
                "usage": conversation.usage,
            }
        conversation.add_user(
            "The plan has these problems:\n- "
            + "\n- ".join(result["errors"])
            + "\n\nReply with the complete corrected JSON plan."
        )
    raise PlanError("No plan.")
