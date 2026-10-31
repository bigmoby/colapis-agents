"""What changes on the Kanban when the issues change, and how it is applied.

The first run imports the issues into a new Kanban (github_import_issues, no AI).
Later runs update each card in place (update_task_card: title, status, assignee,
priority) and add the issues that are new to the Board (insert_elements) in the
column of their status. MCP has no tool that moves a card between columns, so a
card whose issue changes status keeps its column and shows the new status first
in its description. Colapis never writes to GitHub.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..blueprint import element_id, task_card
from ..colapis import Colapis

COLUMNS = ("Open", "In progress", "Closed")
CARD_KEY = re.compile(r"^#(\d+)\b")
EXCERPT = 80


@dataclass
class Issue:
    number: int
    title: str
    status: str  # one of COLUMNS
    assignee: str
    priority: str
    excerpt: str

    @property
    def card_title(self) -> str:
        return f"#{self.number} · {self.title}"

    @property
    def card_description(self) -> str:
        return " · ".join(part for part in (self.status, self.excerpt) if part)

    @classmethod
    def from_tool(cls, item: dict) -> Issue:
        """An issue as github_search_issues returns it."""
        first_line = " ".join((item.get("description") or "").split())
        return cls(
            number=int(str(item.get("key")).lstrip("#")),
            title=(item.get("summary") or "").strip(),
            status=item.get("status") if item.get("status") in COLUMNS else "Open",
            assignee=item.get("assignee") or "",
            priority=item.get("priority") if item.get("priority") in ("High", "Medium", "Low") else "Medium",
            excerpt=first_line[:EXCERPT] + ("…" if len(first_line) > EXCERPT else ""),
        )


def board_title(repo: str) -> str:
    return f"{repo} · Kanban"


def flat(value: str) -> str:
    return " ".join((value or "").split())


def plan_changes(cards: list[dict], frames: list[dict], issues: list[Issue]) -> tuple[list[dict], list[dict]]:
    """The update_task_card calls, and the task cards to insert (with their column)."""
    by_number = {}
    for card in cards:
        match = CARD_KEY.match(flat(card.get("title", "")))
        if match:
            by_number[int(match.group(1))] = card
    columns = {f["name"]: f for f in frames if f.get("name") in COLUMNS}
    updates, inserts = [], []
    for issue in issues:
        card = by_number.get(issue.number)
        if card:
            current = (
                flat(card.get("title", "")),
                card.get("assignee", ""),
                card.get("priority"),
                flat(card.get("description", "")).split(" · ", 1)[0],
            )
            if current != (issue.card_title, issue.assignee, issue.priority, issue.status):
                updates.append(
                    {
                        "taskCardId": card["id"],
                        "title": issue.card_title,
                        "description": issue.card_description,
                        "assignee": issue.assignee,
                        "priority": issue.priority,
                    }
                )
        elif issue.status in columns:
            column = columns[issue.status]
            inserts.append(
                {
                    "frameId": column["id"],
                    "card": task_card(
                        element_id("issue", issue.number),
                        column["x"] + 16,
                        column["y"] + column["height"],
                        issue.card_title,
                        description=issue.card_description,
                        assignee=issue.assignee,
                        priority=issue.priority,
                        frame_id=column["id"],
                    ),
                }
            )
    return updates, inserts


async def reconcile(colapis: Colapis, repo: str) -> str:
    found = await colapis.call("list_boards", search=board_title(repo))
    boards = [b for b in found.get("boards", []) if b.get("name") == board_title(repo) and not b.get("isPrivate")]
    if not boards:
        made = await colapis.call("github_import_issues", reference=repo, boardTitle=board_title(repo))
        return f'Created the Kanban "{made.get("name")}" with {made.get("issueCount")} issues.'
    board_id = boards[0]["id"]
    scene = await colapis.call("get_board_scene", boardId=board_id)
    found_issues = await colapis.call("github_search_issues", reference=repo, includeClosed=True, maxResults=200)
    issues = [Issue.from_tool(item) for item in found_issues.get("issues", [])]
    updates, inserts = plan_changes(scene.get("taskCards", []), scene.get("frames", []), issues)
    for update in updates:
        await colapis.call("update_task_card", boardId=board_id, **update)
    for insert in inserts:
        # Colapis places the card below the last one of its column
        await colapis.call(
            "insert_elements",
            boardId=board_id,
            elements=[insert["card"]],
            targetFrameId=insert["frameId"],
            applyLayout=True,
        )
    return f"{board_title(repo)}: {len(updates)} cards updated, {len(inserts)} added."
