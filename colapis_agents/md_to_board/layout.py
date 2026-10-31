"""Turns a plan into blueprint elements, with positions computed here: the model
never writes coordinates."""

from __future__ import annotations

import math

from ..blueprint import INK, MAX_ELEMENTS_PER_CALL, MUTED, TASK_CARD_WIDTH, TEXT_LIMIT, js_round, text_height

PAD = 48
GAP = 24
COL_GAP = 40
ROW_GAP = 40
LAYER_GAP = 120
NODE_GAP = 56
FRAME_GAP = 160
FRAMES_PER_ROW = 4
STICKY_WIDTH = 220
# Three task cards side by side
CONTENT_MIN_WIDTH = 3 * TASK_CARD_WIDTH + 2 * COL_GAP
# Frames are at most 5000 wide
CONTENT_MAX_WIDTH = 5000 - 2 * PAD
HEADER_WIDTH = 1200

STICKY_COLORS = {
    "yellow": "#fff3bf",
    "blue": "#d0ebff",
    "green": "#d3f9d8",
    "pink": "#ffdeeb",
    "orange": "#ffe8cc",
    "purple": "#e5dbff",
}
NODE_FILLS = {"rectangle": "#e7f5ff", "ellipse": "#ebfbee", "diamond": "#fff9db"}
FONT = {"title": 44, "intro": 20, "heading": 26, "text": 18, "notes": 14, "sticky": 18, "node": 18}


class LayoutError(RuntimeError):
    pass


def split_text(value: str, limit: int = TEXT_LIMIT) -> list[str]:
    """Splits a long text on line boundaries, within the catalog limit."""
    parts: list[str] = []
    current = ""
    for line in value.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
        rest = line
        while len(rest) > limit:
            parts.append(rest[:limit])
            rest = rest[limit:]
        current = rest
    if current:
        parts.append(current)
    return parts


def block_size(block: dict) -> dict:
    if block["kind"] == "sticky":
        return {
            "width": STICKY_WIDTH,
            "height": min(1200, max(160, text_height(block["text"], FONT["sticky"], STICKY_WIDTH - 32) + 40)),
        }
    if block["kind"] == "task":
        # As Colapis measures a task card: title 16px, description 13px, badges
        inner = TASK_CARD_WIDTH - 28
        height = (
            14 + text_height(block["title"], 16, inner) + 6 + text_height(block["description"] or "", 13, inner) + 48
        )
        return {"width": TASK_CARD_WIDTH, "height": min(2000, max(134, height + 8))}
    if block["kind"] == "node":
        width = min(300, max(160, len(block["label"]) * 10 + 48))
        height = max(80, text_height(block["label"], FONT["node"], width - 32) + 32)
        if block["shape"] == "diamond":
            width, height = js_round(width * 1.4), js_round(height * 1.6)
        elif block["shape"] == "ellipse":
            width, height = js_round(width * 1.2), js_round(height * 1.2)
        return {"width": width, "height": height}
    return {"width": 0, "height": 0}


def layer_run(items: list[dict], edges: list[dict]) -> list[list[dict]]:
    """Longest-path layers along the connections; a cycle does not stop it."""
    ids = [item["block"]["id"] for item in items]
    outgoing = {id_: [] for id_ in ids}
    indegree = {id_: 0 for id_ in ids}
    for edge in edges:
        outgoing[edge["from"]].append(edge["to"])
        indegree[edge["to"]] += 1
    layer: dict[str, int] = {}
    queue = [id_ for id_ in ids if indegree[id_] == 0]
    for id_ in queue:
        layer[id_] = 0
    done: set[str] = set()

    def visit(id_: str) -> None:
        done.add(id_)
        for following in outgoing[id_]:
            if following in done:  # a way back in a cycle must not push an earlier node forward
                continue
            layer[following] = max(layer.get(following, 0), layer[id_] + 1)
            indegree[following] -= 1
            if indegree[following] == 0:
                queue.append(following)

    while queue or len(done) < len(ids):
        if not queue:
            # A cycle: release the first node still waiting, in document order
            id_ = next(c for c in ids if c not in done and c not in queue)
            layer[id_] = layer.get(id_, 0)
            queue.append(id_)
        id_ = queue.pop(0)
        if id_ not in done:
            visit(id_)
    layers: dict[int, list[dict]] = {}
    for item in items:
        layers.setdefault(layer.get(item["block"]["id"], 0), []).append(item)
    return [layers[index] for index in sorted(layers)]


def measure_layered(layers: list[list[dict]]) -> dict:
    columns = [
        {
            "width": max(item["size"]["width"] for item in column),
            "height": sum(item["size"]["height"] for item in column) + NODE_GAP * (len(column) - 1),
        }
        for column in layers
    ]
    return {
        "columns": columns,
        "width": sum(c["width"] for c in columns) + LAYER_GAP * (len(columns) - 1),
        "height": max(c["height"] for c in columns),
    }


def to_runs(blocks: list[dict], connections: list[dict]) -> list[dict]:
    """Groups the blocks of a section: single texts, and runs of connectable blocks."""
    runs: list[dict] = []
    for block in blocks:
        if block["kind"] in ("heading", "text"):
            runs.append({"kind": block["kind"], "block": block})
            continue
        item = {"block": block, "size": block_size(block)}
        if runs and runs[-1]["kind"] == "items":
            runs[-1]["items"].append(item)
        else:
            runs.append({"kind": "items", "items": [item]})
    # Inside a run, the connected items form a diagram and the others a grid,
    # in the order their first item appears.
    result = []
    for run in runs:
        if run["kind"] != "items":
            result.append(run)
            continue
        in_run = {item["block"]["id"] for item in run["items"]}
        edges = [e for e in connections if e["from"] in in_run and e["to"] in in_run]
        if not edges:
            result.append(run)
            continue
        connected = {end for e in edges for end in (e["from"], e["to"])}
        graph_items = [i for i in run["items"] if i["block"]["id"] in connected]
        grid_items = [i for i in run["items"] if i["block"]["id"] not in connected]
        layers = layer_run(graph_items, edges)
        graph = {"kind": "items", "items": graph_items, "layers": layers, "measure": measure_layered(layers)}
        if not grid_items:
            result.append(graph)
            continue
        grid = {"kind": "items", "items": grid_items}
        first_grid = next(i for i, item in enumerate(run["items"]) if item is grid_items[0])
        first_graph = next(i for i, item in enumerate(run["items"]) if item is graph_items[0])
        result.extend([grid, graph] if first_grid < first_graph else [graph, grid])
    return result


def content_width_of(runs: list[dict], minimum: float) -> float:
    return min(CONTENT_MAX_WIDTH, max([minimum, *(run["measure"]["width"] for run in runs if run.get("measure"))]))


def layout_plan(plan: dict, *, notes_label: str = "Speaker notes") -> dict:
    """Returns {"title", "elements", "batches", "stats", "warnings"}."""
    warnings: list[str] = []
    elements: list[dict] = []
    counters: dict[str, int] = {}
    placed: dict[str, dict] = {}  # plan id -> {"id", "box", "frame_id", "bindable"}

    def next_id(prefix: str) -> str:
        counters[prefix] = counters.get(prefix, 0) + 1
        return f"{prefix}{counters[prefix]}"

    def clip(value: str, limit: int, what: str) -> str:
        if len(value) <= limit:
            return value
        warnings.append(f'{what} shortened to {limit} characters: "{value[:40]}…"')
        return f"{value[: limit - 1]}…"

    def add_text(
        value: str, x: float, y: float, width: float, font_size: float, color: str = INK, frame_id: str | None = None
    ) -> float:
        cursor = y
        for part in split_text(value):
            elements.append(
                {
                    "type": "text",
                    "id": next_id("txt"),
                    "frameId": frame_id,
                    "x": js_round(x),
                    "y": js_round(cursor),
                    "width": js_round(width),
                    "text": part,
                    "fontSize": font_size,
                    "color": color,
                    "align": "left",
                }
            )
            cursor += text_height(part, font_size, width) + GAP / 2
        return cursor - GAP / 2 - y

    def add_item(item: dict, x: float, y: float, frame_id: str | None) -> None:
        block, size = item["block"], item["size"]
        if block["kind"] == "sticky":
            element = {
                "type": "stickynote",
                "id": next_id("note"),
                "frameId": frame_id,
                "x": x,
                "y": y,
                "width": size["width"],
                "height": size["height"],
                "text": clip(block["text"], TEXT_LIMIT, "Sticky note"),
                "fontSize": FONT["sticky"],
                "backgroundColor": STICKY_COLORS[block["color"]],
                "color": INK,
            }
        elif block["kind"] == "task":
            element = {
                "type": "task-card",
                "id": next_id("task"),
                "frameId": frame_id,
                "x": x,
                "y": y,
                "height": size["height"],
                "title": clip(block["title"], 300, "Task title"),
                "description": clip(block["description"], 2000, "Task description"),
                "assignee": clip(block["assignee"], 100, "Assignee"),
                "priority": block["priority"],
            }
        else:
            element = {
                "type": block["shape"],
                "id": next_id("node"),
                "frameId": frame_id,
                "x": x,
                "y": y,
                "width": size["width"],
                "height": size["height"],
                "strokeColor": INK,
                "strokeWidth": 2,
                "strokeStyle": "solid",
                "backgroundColor": NODE_FILLS[block["shape"]],
                "label": clip(block["label"], 300, "Label"),
            }
        elements.append({**element, "x": js_round(x), "y": js_round(y)})
        placed[block["id"]] = {
            "id": element["id"],
            "frame_id": frame_id,
            "bindable": block["kind"] != "task",
            "box": {"x": x, "y": y, "width": size["width"], "height": size["height"]},
        }

    def place_runs(runs: list[dict], x: float, y: float, width: float, frame_id: str | None) -> float:
        """Places a section's runs from (x, y); returns the height used."""
        cursor = y
        for index, run in enumerate(runs):
            if index > 0:
                cursor += GAP * 1.5 if run["kind"] == "heading" else GAP
            if run["kind"] in ("heading", "text"):
                cursor += add_text(
                    run["block"]["text"],
                    x,
                    cursor,
                    width,
                    FONT["heading"] if run["kind"] == "heading" else FONT["text"],
                    frame_id=frame_id,
                )
                continue
            if run.get("measure") and run["measure"]["width"] <= width:
                column_x = x
                for column_index, column in enumerate(run["layers"]):
                    column_width = run["measure"]["columns"][column_index]["width"]
                    column_height = run["measure"]["columns"][column_index]["height"]
                    item_y = cursor + (run["measure"]["height"] - column_height) / 2
                    for item in column:
                        add_item(item, column_x + (column_width - item["size"]["width"]) / 2, item_y, frame_id)
                        item_y += item["size"]["height"] + NODE_GAP
                    column_x += column_width + LAYER_GAP
                cursor += run["measure"]["height"]
                continue
            if run.get("measure"):
                warnings.append("A diagram too wide for its frame was laid out as a grid.")
            cell = max(item["size"]["width"] for item in run["items"])
            per_row = max(1, math.floor((width + COL_GAP) / (cell + COL_GAP)))
            for start in range(0, len(run["items"]), per_row):
                row = run["items"][start : start + per_row]
                for column, item in enumerate(row):
                    add_item(item, x + column * (cell + COL_GAP), cursor, frame_id)
                cursor += max(item["size"]["height"] for item in row) + ROW_GAP
            cursor -= ROW_GAP
        return cursor - y

    def notes_run(notes: str) -> dict:
        return {"kind": "text", "block": {"text": f"{notes_label}: {notes}"}, "notes": True}

    # Title and introduction
    header_bottom = add_text(clip(plan["title"], TEXT_LIMIT, "Title"), 0, 0, HEADER_WIDTH, FONT["title"])
    for paragraph in plan["intro"]:
        header_bottom += GAP
        header_bottom += add_text(paragraph, 0, header_bottom, HEADER_WIDTH, FONT["intro"])

    # Frames, FRAMES_PER_ROW per row, so the slide order (top to bottom, left to
    # right) follows the document.
    framed = [s for s in plan["sections"] if s["title"] is not None]
    loose = [s for s in plan["sections"] if s["title"] is None]
    row_y = header_bottom + 100
    row_height = 0
    frame_x = 0
    frame_ids = []
    for index, section in enumerate(framed):
        if index > 0 and index % FRAMES_PER_ROW == 0:
            row_y += row_height + FRAME_GAP
            row_height = 0
            frame_x = 0
        runs = to_runs(section["blocks"], plan["connections"])
        width = content_width_of(runs, CONTENT_MIN_WIDTH)
        frame = {
            "type": "frame",
            "id": next_id("frame"),
            "frameId": None,
            "x": frame_x,
            "y": js_round(row_y),
            "width": 0,
            "height": 0,
            "title": clip(section["title"], 200, "Frame title"),
        }
        elements.append(frame)
        frame_ids.append(frame["id"])
        height = place_runs(runs, frame_x + PAD, row_y + PAD, width, frame["id"])
        if section["notes"]:
            height += GAP * 1.5
            height += add_text(
                notes_run(section["notes"])["block"]["text"],
                frame_x + PAD,
                row_y + PAD + height,
                width,
                FONT["notes"],
                MUTED,
                frame["id"],
            )
        frame["width"] = js_round(width + 2 * PAD)
        frame["height"] = js_round(max(200, height + 2 * PAD))
        row_height = max(row_height, frame["height"])
        frame_x += frame["width"] + FRAME_GAP

    # Content outside any frame, below the frames
    loose_y = row_y + row_height + FRAME_GAP if framed else header_bottom + 100
    for section in loose:
        runs = to_runs(section["blocks"], plan["connections"])
        if section["notes"]:
            runs.append(notes_run(section["notes"]))
        loose_y += place_runs(runs, 0, loose_y, content_width_of(runs, HEADER_WIDTH), None) + FRAME_GAP

    # Arrows, from edge to edge of the two boxes
    arrows = []
    drawn_pairs: set[str] = set()
    frame_tops = {e["id"]: e["y"] for e in elements if e["type"] == "frame"}
    for connection in plan["connections"]:
        source, target = placed.get(connection["from"]), placed.get(connection["to"])
        if not source or not target:
            continue
        s, t = source["box"], target["box"]
        dx = t["x"] + t["width"] / 2 - (s["x"] + s["width"] / 2)
        dy = t["y"] + t["height"] / 2 - (s["y"] + s["height"] / 2)
        pair_key = f"{connection['from']}>{connection['to']}"
        if source["frame_id"] != target["frame_id"]:
            # Between frames: up into the corridor above them, across, and down,
            # instead of a straight line through other elements.
            def top_of(item: dict) -> float:
                return frame_tops.get(item["frame_id"], item["box"]["y"])

            start = {"x": s["x"] + s["width"] / 2, "y": s["y"]}
            end = {"x": t["x"] + t["width"] / 2, "y": t["y"]}
            corridor = js_round(min(top_of(source), top_of(target)) - 60 - start["y"])
            ex = js_round(end["x"] - start["x"]) or 1
            points = [
                {"x": 0, "y": 0},
                {"x": 0, "y": corridor},
                {"x": ex, "y": corridor},
                {"x": ex, "y": js_round(end["y"] - start["y"])},
            ]
        elif f"{connection['to']}>{connection['from']}" in drawn_pairs:
            # The way back of a pair already drawn passes below both boxes, so the
            # two arrows do not lie on the same line.
            start = {"x": s["x"] + s["width"] / 2, "y": s["y"] + s["height"]}
            end = {"x": t["x"] + t["width"] / 2, "y": t["y"] + t["height"]}
            below = js_round(max(s["y"] + s["height"], t["y"] + t["height"]) + 40 - start["y"])
            ex = js_round(end["x"] - start["x"]) or 1
            points = [
                {"x": 0, "y": 0},
                {"x": 0, "y": below},
                {"x": ex, "y": below},
                {"x": ex, "y": js_round(end["y"] - start["y"])},
            ]
        else:
            if abs(dx) >= abs(dy):
                start = {"x": s["x"] + s["width"] if dx >= 0 else s["x"], "y": s["y"] + s["height"] / 2}
                end = {"x": t["x"] if dx >= 0 else t["x"] + t["width"], "y": t["y"] + t["height"] / 2}
            else:
                start = {"x": s["x"] + s["width"] / 2, "y": s["y"] + s["height"] if dy >= 0 else s["y"]}
                end = {"x": t["x"] + t["width"] / 2, "y": t["y"] if dy >= 0 else t["y"] + t["height"]}
            px, py = js_round(end["x"] - start["x"]), js_round(end["y"] - start["y"])
            if px == 0 and py == 0:
                px = 1
            points = [{"x": 0, "y": 0}, {"x": px, "y": py}]
        drawn_pairs.add(pair_key)
        arrow = {
            "type": "arrow",
            "id": next_id("arrow"),
            "frameId": source["frame_id"] if source["frame_id"] and source["frame_id"] == target["frame_id"] else None,
            "x": js_round(start["x"]),
            "y": js_round(start["y"]),
            "strokeColor": INK,
            "strokeWidth": 2,
            "strokeStyle": "solid",
            "points": points,
            "startArrowhead": None,
            "endArrowhead": "arrow",
        }
        if source["bindable"]:
            arrow["startId"] = source["id"]
        if target["bindable"]:
            arrow["endId"] = target["id"]
        if connection["label"] and len(points) == 2:
            arrow["label"] = clip(connection["label"], 200, "Arrow label")
        elements.append(arrow)
        if connection["label"] and len(points) > 2:
            # On a bent arrow the label becomes a text above its middle segment,
            # where Colapis would draw it, since the compiler would place it
            # between the two ends instead.
            a, b = points[len(points) // 2 - 1], points[len(points) // 2]
            label = clip(connection["label"], 200, "Arrow label")
            width = max(40, math.ceil(len(label) * 14 * 0.62) + 8)
            add_text(
                label,
                start["x"] + (a["x"] + b["x"]) / 2 - width / 2,
                start["y"] + (a["y"] + b["y"]) / 2 - 22,
                width,
                14,
                frame_id=arrow["frameId"],
            )
        arrows.append({"arrow": arrow, "ends": [source["id"], target["id"]]})

    batches = make_batches(elements, frame_ids, arrows, warnings)

    def count(kind: str) -> int:
        return sum(1 for e in elements if e["type"] == kind)

    return {
        "title": plan["title"],
        "elements": elements,
        "batches": batches,
        "stats": {
            "frames": count("frame"),
            "texts": count("text"),
            "stickyNotes": count("stickynote"),
            "taskCards": count("task-card"),
            "shapes": count("rectangle") + count("ellipse") + count("diamond"),
            "arrows": count("arrow"),
            "boundArrowEnds": sum(
                ("startId" in e) + ("endId" in e) for batch in batches for e in batch if e["type"] == "arrow"
            ),
        },
        "warnings": warnings,
    }


def make_batches(elements: list[dict], frame_ids: list[str], arrows: list[dict], warnings: list[str]) -> list[list]:
    """Splits the elements into insert_elements calls. A frame travels with its
    content, and an arrow binds only to shapes inserted in the same call."""
    if len(elements) <= MAX_ELEMENTS_PER_CALL:
        return [elements]
    group_of = {e["id"]: e["frameId"] or e["id"] for e in elements}
    for id_ in frame_ids:
        group_of[id_] = id_
    arrow_ids = {id(entry["arrow"]) for entry in arrows}
    batches: list[list] = []
    for frame_id in frame_ids:
        members = [e for e in elements if (e["id"] == frame_id or e["frameId"] == frame_id) and id(e) not in arrow_ids]
        inner = [
            entry["arrow"]
            for entry in arrows
            if entry["arrow"]["frameId"] == frame_id and all(group_of.get(end) == frame_id for end in entry["ends"])
        ]
        batch = members + inner
        if len(batch) > MAX_ELEMENTS_PER_CALL:
            title = next(e for e in elements if e["id"] == frame_id)["title"]
            raise LayoutError(
                f'The section "{title}" has {len(batch)} elements, more than the limit of '
                f'{MAX_ELEMENTS_PER_CALL} per frame: split it into more "##" sections.'
            )
        batches.append(batch)
    loose = [e for e in elements if e["frameId"] is None and e["type"] != "frame" and id(e) not in arrow_ids]
    in_frames = {id(e) for batch in batches for e in batch}
    crossing = []
    for entry in arrows:
        if id(entry["arrow"]) not in in_frames:
            # Its ends go in another call: the arrow keeps its position, unbound
            crossing.append({k: v for k, v in entry["arrow"].items() if k not in ("startId", "endId")})
    if crossing:
        warnings.append(
            f"{len(crossing)} arrows connect elements inserted in different calls: they keep their "
            "position but are not bound."
        )
    for group in (loose, crossing):
        for start in range(0, len(group), MAX_ELEMENTS_PER_CALL):
            batches.append(group[start : start + MAX_ELEMENTS_PER_CALL])
    return [batch for batch in batches if batch]
