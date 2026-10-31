"""Builders for the elements Colapis draws natively: its blueprint catalog, version 1.

Every element carries the properties the catalog requires, so Colapis accepts it
as it is. Agents compute positions and sizes themselves; Colapis compiles the
elements into native frames, texts, sticky notes, task cards, shapes and arrows.
"""

from __future__ import annotations

import math
import re
from typing import Any

# insert_elements and the blueprint accept at most this many elements per call
MAX_ELEMENTS_PER_CALL = 250
INK = "#1e1e1e"
MUTED = "#868e96"
TASK_CARD_WIDTH = 248  # task cards have a fixed width
TEXT_LIMIT = 2000

Element = dict[str, Any]


def js_round(value: float) -> int:
    """Rounds halves up, like JavaScript's Math.round."""
    return math.floor(value + 0.5)


def element_id(*parts: object) -> str:
    """An id the catalog accepts: letters, digits, - and _, at most 64."""
    value = "-".join(str(part) for part in parts)
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-")[:64] or "el"


def estimate_lines(text: str, font_size: float, width: float) -> int:
    """Lines a text takes at a width; errs on the generous side, so nothing overlaps."""
    per_line = max(4, math.floor(width / (font_size * 0.6)))
    return sum(max(1, math.ceil(len(line) / per_line)) for line in text.split("\n"))


def text_height(text: str, font_size: float, width: float) -> int:
    return math.ceil(estimate_lines(text, font_size, width) * font_size * 1.4) + 6


def frame(id: str, x: float, y: float, width: float, height: float, title: str) -> Element:
    return {
        "type": "frame",
        "id": id,
        "frameId": None,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "title": title[:200],
    }


def text(
    id: str,
    x: float,
    y: float,
    value: str,
    *,
    width: float = 400,
    font_size: float = 20,
    color: str = INK,
    align: str = "left",
    frame_id: str | None = None,
) -> Element:
    return {
        "type": "text",
        "id": id,
        "frameId": frame_id,
        "x": x,
        "y": y,
        "width": width,
        "text": value[:TEXT_LIMIT],
        "fontSize": font_size,
        "color": color,
        "align": align,
    }


def sticky_note(
    id: str,
    x: float,
    y: float,
    value: str,
    *,
    width: float = 200,
    height: float = 200,
    color: str = "#fff3bf",
    font_size: float = 18,
    frame_id: str | None = None,
) -> Element:
    return {
        "type": "stickynote",
        "id": id,
        "frameId": frame_id,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "text": value[:TEXT_LIMIT],
        "fontSize": font_size,
        "backgroundColor": color,
        "color": INK,
    }


def task_card(
    id: str,
    x: float,
    y: float,
    title: str,
    *,
    description: str = "",
    assignee: str = "",
    priority: str = "Medium",
    height: float = 134,
    frame_id: str | None = None,
) -> Element:
    return {
        "type": "task-card",
        "id": id,
        "frameId": frame_id,
        "x": x,
        "y": y,
        "height": height,
        "title": title[:300],
        "description": description[:TEXT_LIMIT],
        "assignee": assignee[:100],
        "priority": priority if priority in ("High", "Medium", "Low") else "Medium",
    }


def shape(
    kind: str,
    id: str,
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    label: str = "",
    background: str = "#ffffff",
    stroke: str = INK,
    stroke_width: float = 2,
    stroke_style: str = "solid",
    frame_id: str | None = None,
) -> Element:
    """A rectangle, ellipse or diamond, with its label bound inside."""
    element: Element = {
        "type": kind,
        "id": id,
        "frameId": frame_id,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "strokeColor": stroke,
        "strokeWidth": stroke_width,
        "strokeStyle": stroke_style,
        "backgroundColor": background,
    }
    if label:
        element["label"] = label[:300]
    return element


def arrow(
    id: str,
    x: float,
    y: float,
    points: list[dict],
    *,
    start_id: str | None = None,
    end_id: str | None = None,
    label: str = "",
    frame_id: str | None = None,
) -> Element:
    """An arrow through local points starting at (0, 0), bound to the shapes it connects."""
    element: Element = {
        "type": "arrow",
        "id": id,
        "frameId": frame_id,
        "x": x,
        "y": y,
        "strokeColor": INK,
        "strokeWidth": 2,
        "strokeStyle": "solid",
        "points": points,
        "startArrowhead": None,
        "endArrowhead": "arrow",
    }
    if start_id:
        element["startId"] = start_id
    if end_id:
        element["endId"] = end_id
    if label:
        element["label"] = label[:200]
    return element
