"""Lays out an architecture as horizontal bands, one frame per layer, from top to
bottom, and emits blueprint elements. The model never writes coordinates."""

from __future__ import annotations

import math

from ..blueprint import INK, MAX_ELEMENTS_PER_CALL, MUTED, estimate_lines, js_round, text_height

PAD = 48
# Room above and below the rows for the arrows that go around them
PAD_TOP = 72
PAD_BOTTOM = 88
NODE_GAP = 64
ROW_GAP = 160
BAND_GAP = 160
MAX_PER_ROW = 6
NODE_WIDTH = 240
MIN_BAND_WIDTH = 1000
HEADER_WIDTH = 1400
CORRIDOR = 56
LANE = 18

STYLES = {
    "ui": {"shape": "rectangle", "fill": "#f3d9fa", "stroke": "#862e9c", "width": 2, "style": "solid"},
    "service": {"shape": "rectangle", "fill": "#d0ebff", "stroke": "#1971c2", "width": 2, "style": "solid"},
    "module": {"shape": "rectangle", "fill": "#e7f5ff", "stroke": "#1c7ed6", "width": 1, "style": "solid"},
    "datastore": {"shape": "ellipse", "fill": "#d3f9d8", "stroke": "#2f9e44", "width": 2, "style": "solid"},
    "external": {"shape": "rectangle", "fill": "#f1f3f5", "stroke": "#868e96", "width": 2, "style": "dashed"},
}

LEGEND = {
    "en": {
        "title": "Legend",
        "ui": "Interface",
        "service": "Service",
        "module": "Module",
        "datastore": "Data",
        "external": "External system",
        "languages": "Languages",
    },
    "it": {
        "title": "Legenda",
        "ui": "Interfaccia",
        "service": "Servizio",
        "module": "Modulo",
        "datastore": "Dati",
        "external": "Sistema esterno",
        "languages": "Linguaggi",
    },
}


class LayoutError(RuntimeError):
    pass


def node_label(component: dict) -> str:
    return "\n".join(part for part in (component["name"], component["description"], component["path"]) if part)[:300]


def node_size(component: dict) -> dict:
    label = node_label(component)
    # Bound text: 16px at line height 1.25, inside the padding of the shape
    height = max(80, math.ceil(estimate_lines(label, 16, NODE_WIDTH - 20) * 20) + 40)
    if STYLES[component["kind"]]["shape"] == "ellipse":
        return {"width": js_round(NODE_WIDTH * 1.3), "height": js_round(height * 1.4)}
    return {"width": NODE_WIDTH, "height": height}


def order_layers(layers: list[dict], flows: list[dict]) -> list[list[str]]:
    """Orders each layer by the mean position of its neighbours, down then up."""
    neighbours: dict[str, list[str]] = {}
    for flow in flows:
        neighbours.setdefault(flow["from"], []).append(flow["to"])
        neighbours.setdefault(flow["to"], []).append(flow["from"])
    order = [[c["id"] for c in layer["components"]] for layer in layers]
    position: dict[str, float] = {}

    def remember() -> None:
        for ids in order:
            for index, id_ in enumerate(ids):
                position[id_] = (index + 0.5) / len(ids)

    remember()

    def sweep(indexes: list[int], is_reference) -> None:
        for layer_index in indexes:
            reference = {id_ for index, ids in enumerate(order) if is_reference(index, layer_index) for id_ in ids}
            keyed = []
            for index, id_ in enumerate(order[layer_index]):
                linked = [other for other in neighbours.get(id_, []) if other in reference]
                key = (
                    sum(position[other] for other in linked) / len(linked)
                    if linked
                    else (index + 0.5) / len(order[layer_index])
                )
                keyed.append((key, id_))
            order[layer_index] = [id_ for _, id_ in sorted(keyed, key=lambda entry: entry[0])]
            remember()

    indexes = list(range(len(layers)))
    sweep(indexes[1:], lambda index, current: index < current)
    sweep(list(reversed(indexes[:-1])), lambda index, current: index > current)
    return order


def layout_architecture(
    plan: dict, *, repository: str | None = None, languages: list[str] | None = None, language: str = "en"
) -> dict:
    """Returns {"elements", "stats", "warnings"}."""
    words = LEGEND["it" if language.lower().startswith("it") else "en"]
    languages = languages or []
    warnings: list[str] = []
    elements: list[dict] = []
    counters: dict[str, int] = {}

    def next_id(prefix: str) -> str:
        counters[prefix] = counters.get(prefix, 0) + 1
        return f"{prefix}{counters[prefix]}"

    def add_text(
        value: str, x: float, y: float, width: float, font_size: float, color: str = INK, frame_id: str | None = None
    ) -> int:
        elements.append(
            {
                "type": "text",
                "id": next_id("txt"),
                "frameId": frame_id,
                "x": js_round(x),
                "y": js_round(y),
                "width": js_round(width),
                "text": value[:2000],
                "fontSize": font_size,
                "color": color,
                "align": "left",
            }
        )
        return text_height(value, font_size, width)

    # Header: title, summary, source and languages, legend
    y = 0
    y += add_text(plan["title"], 0, y, HEADER_WIDTH, 44)
    if plan.get("summary"):
        y += 16 + add_text(plan["summary"], 0, y + 16, HEADER_WIDTH, 20)
    source = "  ·  ".join(
        part
        for part in (
            repository and f"GitHub: {repository}",
            languages and f"{words['languages']}: {', '.join(languages)}",
        )
        if part
    )
    if source:
        y += 12 + add_text(source, 0, y + 12, HEADER_WIDTH, 16, MUTED)
    kinds = list(dict.fromkeys(c["kind"] for layer in plan["layers"] for c in layer["components"]))
    y += 24
    add_text(f"{words['title']}:", 0, y + 10, 100, 16, MUTED)
    for index, kind in enumerate(kinds):
        style = STYLES[kind]
        elements.append(
            {
                "type": style["shape"],
                "id": next_id("legend"),
                "frameId": None,
                "x": 110 + index * 190,
                "y": js_round(y),
                "width": 170,
                "height": 44,
                "strokeColor": style["stroke"],
                "strokeWidth": style["width"],
                "strokeStyle": style["style"],
                "backgroundColor": style["fill"],
                "label": words[kind],
            }
        )
    y += 44

    # Bands
    by_id = {c["id"]: c for layer in plan["layers"] for c in layer["components"]}
    order = order_layers(plan["layers"], plan["flows"])
    bands = []
    for layer_index, layer in enumerate(plan["layers"]):
        items = [{"component": by_id[id_], "size": node_size(by_id[id_])} for id_ in order[layer_index]]
        rows = [items[start : start + MAX_PER_ROW] for start in range(0, len(items), MAX_PER_ROW)]
        bands.append(
            {
                "layer": layer,
                "rows": [
                    {
                        "items": row,
                        "width": sum(item["size"]["width"] for item in row) + NODE_GAP * (len(row) - 1),
                        "height": max(item["size"]["height"] for item in row),
                    }
                    for row in rows
                ],
            }
        )
    band_width = max([MIN_BAND_WIDTH, *(row["width"] + 2 * PAD for band in bands for row in band["rows"])])

    placed: dict[str, dict] = {}
    band_y = y + 120
    for layer_index, band in enumerate(bands):
        frame = {
            "type": "frame",
            "id": next_id("frame"),
            "frameId": None,
            "x": 0,
            "y": js_round(band_y),
            "width": js_round(band_width),
            "height": 0,
            "title": band["layer"]["name"][:200],
        }
        elements.append(frame)
        row_y = band_y + PAD_TOP
        for row_index, row in enumerate(band["rows"]):
            x = (band_width - row["width"]) / 2
            for item in row["items"]:
                component, size = item["component"], item["size"]
                style = STYLES[component["kind"]]
                node_y = row_y + (row["height"] - size["height"]) / 2
                node = {
                    "type": style["shape"],
                    "id": next_id("node"),
                    "frameId": frame["id"],
                    "x": js_round(x),
                    "y": js_round(node_y),
                    "width": size["width"],
                    "height": size["height"],
                    "strokeColor": style["stroke"],
                    "strokeWidth": style["width"],
                    "strokeStyle": style["style"],
                    "backgroundColor": style["fill"],
                    "label": node_label(component),
                }
                elements.append(node)
                placed[component["id"]] = {
                    "node": node,
                    "layer": layer_index,
                    "row": row_index,
                    "row_top": row_y,
                    "row_bottom": row_y + row["height"],
                    "frame": frame,
                }
                x += size["width"] + NODE_GAP
            row_y += row["height"] + ROW_GAP
        frame["height"] = js_round(row_y - ROW_GAP + PAD_BOTTOM - band_y)
        band_y += frame["height"] + BAND_GAP

    # Arrows. First the route of each flow and the sides it uses, then the
    # attachment points, spread along each side in the order of the other end.
    routes = []
    for flow in plan["flows"]:
        s, t = placed[flow["from"]], placed[flow["to"]]
        span = t["layer"] - s["layer"]
        if span == 0:
            ids = order[s["layer"]]
            adjacent = s["row"] == t["row"] and abs(ids.index(flow["from"]) - ids.index(flow["to"])) == 1
            if adjacent:
                to_right = t["node"]["x"] > s["node"]["x"]
                routes.append(
                    {
                        "flow": flow,
                        "s": s,
                        "t": t,
                        "kind": "side",
                        "from": "right" if to_right else "left",
                        "to": "left" if to_right else "right",
                    }
                )
                continue
            # Around the row: below when going right, above when going left, so
            # the two directions do not share the same lines.
            side = "bottom" if t["node"]["x"] > s["node"]["x"] else "top"
            routes.append({"flow": flow, "s": s, "t": t, "kind": "around", "from": side, "to": side})
            continue
        down = span > 0
        routes.append(
            {
                "flow": flow,
                "s": s,
                "t": t,
                "kind": "straight" if abs(span) == 1 else "corridor",
                "down": down,
                "from": "bottom" if down else "top",
                "to": "top" if down else "bottom",
            }
        )

    def centre_x(entry: dict) -> float:
        return entry["node"]["x"] + entry["node"]["width"] / 2

    def centre_y(entry: dict) -> float:
        return entry["node"]["y"] + entry["node"]["height"] / 2

    side_uses: dict[str, list[dict]] = {}
    for route in routes:
        side_uses.setdefault(f"{route['s']['node']['id']}:{route['from']}", []).append(
            {"other": route["t"], "route": route, "end": "start"}
        )
        side_uses.setdefault(f"{route['t']['node']['id']}:{route['to']}", []).append(
            {"other": route["s"], "route": route, "end": "end"}
        )
    for key, uses in side_uses.items():
        side = key.split(":")[-1]
        horizontal = side in ("top", "bottom")
        uses.sort(key=lambda use: centre_x(use["other"]) if horizontal else centre_y(use["other"]))
        for index, use in enumerate(uses):
            node = use["route"]["s" if use["end"] == "start" else "t"]["node"]
            fraction = 0.5 if len(uses) == 1 else 0.2 + (0.6 * index) / (len(uses) - 1)
            use["route"][use["end"]] = (
                {
                    "x": node["x"] + node["width"] * fraction,
                    "y": node["y"] if side == "top" else node["y"] + node["height"],
                }
                if horizontal
                else {
                    "x": node["x"] if side == "left" else node["x"] + node["width"],
                    "y": node["y"] + node["height"] * fraction,
                }
            )

    right_lane = left_lane = 0
    around_lanes: dict[str, int] = {}
    gap_lanes: dict[str, int] = {}
    free_labels = []
    for route in routes:
        start, end, s, t, flow = route["start"], route["end"], route["s"], route["t"], route["flow"]

        def at(x: float, y: float, start: dict = start) -> dict:
            return {"x": js_round(x - start["x"]), "y": js_round(y - start["y"])}

        if route["kind"] in ("side", "straight"):
            points = [at(start["x"], start["y"]), at(end["x"], end["y"])]
        elif route["kind"] == "around":
            key = f"{s['frame']['id']}:{s['row']}:{route['from']}"
            lane = around_lanes.get(key, 0)
            around_lanes[key] = lane + 1
            depth = 16 + (lane % 4) * 12
            level = (
                max(s["row_bottom"], t["row_bottom"]) + depth
                if route["from"] == "bottom"
                else min(s["row_top"], t["row_top"]) - depth
            )
            points = [at(start["x"], start["y"]), at(start["x"], level), at(end["x"], level), at(end["x"], end["y"])]
        else:
            # Across more than one band. Straight through the bands in between
            # when a column is free of nodes there, turning in the gap next to the
            # target (or the source); otherwise along a corridor beside the bands.
            low, high = min(s["layer"], t["layer"]), max(s["layer"], t["layer"])
            between = [entry for entry in placed.values() if low < entry["layer"] < high]

            def is_free(x: float, between: list = between) -> bool:
                return all(x < e["node"]["x"] - 16 or x > e["node"]["x"] + e["node"]["width"] + 16 for e in between)

            def gap_next_to(entry: dict, towards: str) -> float:
                index = f"{entry['frame']['id']}:{towards}"
                count = gap_lanes.get(index, 0)
                gap_lanes[index] = count + 1
                offset = (count % 4) * 10
                return (
                    entry["frame"]["y"] - BAND_GAP / 2 + offset
                    if towards == "above"
                    else entry["frame"]["y"] + entry["frame"]["height"] + BAND_GAP / 2 - offset
                )

            if is_free(start["x"]):
                turn = gap_next_to(t, "above" if route["down"] else "below")
                points = [at(start["x"], start["y"]), at(start["x"], turn), at(end["x"], turn), at(end["x"], end["y"])]
                route["through"] = True
            elif is_free(end["x"]):
                turn = gap_next_to(s, "below" if route["down"] else "above")
                points = [at(start["x"], start["y"]), at(start["x"], turn), at(end["x"], turn), at(end["x"], end["y"])]
                route["through"] = True
            else:
                if route["down"]:
                    route["lane"] = right_lane
                    right_lane += 1
                    corridor_x = band_width + CORRIDOR + route["lane"] * LANE
                else:
                    route["lane"] = left_lane
                    left_lane += 1
                    corridor_x = -CORRIDOR - route["lane"] * LANE
                gap_out = gap_next_to(s, "below" if route["down"] else "above")
                gap_in = gap_next_to(t, "above" if route["down"] else "below")
                points = [
                    at(start["x"], start["y"]),
                    at(start["x"], gap_out),
                    at(corridor_x, gap_out),
                    at(corridor_x, gap_in),
                    at(end["x"], gap_in),
                    at(end["x"], end["y"]),
                ]
        if all(point["x"] == 0 and point["y"] == 0 for point in points):
            points[-1]["x"] = 1
        in_frame = route["kind"] in ("side", "around")
        label = (flow.get("label") or "")[:200]
        # A bound label sits in the middle of a straight arrow. On a bent one the
        # label becomes a text beside its middle segment, where Colapis would draw
        # it, since the compiler would place it elsewhere.
        if label and len(points) > 2:
            a, b = points[len(points) // 2 - 1], points[len(points) // 2]
            mid_x, mid_y = start["x"] + (a["x"] + b["x"]) / 2, start["y"] + (a["y"] + b["y"]) / 2
            width = max(40, math.ceil(len(label) * 14 * 0.62) + 8)
            vertical = a["x"] == b["x"]
            free_labels.append(
                {
                    "text": label,
                    "x": (mid_x + 8 if route.get("down") else mid_x - width - 8) if vertical else mid_x - width / 2,
                    # Corridors run side by side: their labels are staggered by lane
                    "y": (mid_y - 10 + (route.get("lane", 0) % 4) * 26)
                    if vertical
                    else (mid_y - 24 if route["from"] == "top" else mid_y + 4),
                    "width": width,
                    "frame_id": s["frame"]["id"] if in_frame else None,
                }
            )
        arrow = {
            "type": "arrow",
            "id": next_id("arrow"),
            "frameId": s["frame"]["id"] if in_frame else None,
            "x": js_round(start["x"]),
            "y": js_round(start["y"]),
            "strokeColor": INK,
            "strokeWidth": 2,
            "strokeStyle": "solid",
            "points": points,
            "startArrowhead": None,
            "endArrowhead": "arrow",
            "startId": s["node"]["id"],
            "endId": t["node"]["id"],
        }
        if label and len(points) == 2:
            arrow["label"] = label
        elements.append(arrow)
    for free in free_labels:
        add_text(free["text"], free["x"], free["y"], free["width"], 14, INK, free["frame_id"])

    if len(elements) > MAX_ELEMENTS_PER_CALL:
        raise LayoutError(
            f"The architecture has {len(elements)} elements, more than the {MAX_ELEMENTS_PER_CALL} "
            "of one insertion: use --focus on a part of the repository."
        )
    return {
        "elements": elements,
        "stats": {
            "layers": sum(1 for e in elements if e["type"] == "frame"),
            "components": len(placed),
            "flows": sum(1 for e in elements if e["type"] == "arrow"),
            "corridorFlows": sum(1 for r in routes if r["kind"] == "corridor" and not r.get("through")),
        },
        "warnings": warnings,
    }
