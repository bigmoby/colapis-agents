"""The properties Colapis's blueprint catalog (version 1) requires for each element type."""

import re

COMMON = {"type", "id", "frameId", "x", "y"}
REQUIRED = {
    "frame": COMMON | {"width", "height", "title"},
    "text": COMMON | {"width", "text", "fontSize", "color", "align"},
    "stickynote": COMMON | {"width", "height", "text", "fontSize", "backgroundColor", "color"},
    "task-card": COMMON | {"height", "title", "description", "assignee", "priority"},
    "rectangle": COMMON | {"width", "height", "strokeColor", "strokeWidth", "strokeStyle", "backgroundColor"},
    "ellipse": COMMON | {"width", "height", "strokeColor", "strokeWidth", "strokeStyle", "backgroundColor"},
    "diamond": COMMON | {"width", "height", "strokeColor", "strokeWidth", "strokeStyle", "backgroundColor"},
    "arrow": COMMON | {"strokeColor", "strokeWidth", "strokeStyle", "points", "startArrowhead", "endArrowhead"},
}
OPTIONAL = {
    "task-card": {"jiraIssueId"},
    "rectangle": {"label"},
    "ellipse": {"label"},
    "diamond": {"label"},
    "arrow": {"startId", "endId", "label"},
}
ID = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def assert_valid(elements: list[dict]) -> None:
    ids = {e["id"] for e in elements}
    assert len(ids) == len(elements), "duplicate ids"
    for element in elements:
        kind = element["type"]
        assert kind in REQUIRED, kind
        keys = set(element)
        assert REQUIRED[kind] <= keys, (kind, REQUIRED[kind] - keys)
        assert keys <= REQUIRED[kind] | OPTIONAL.get(kind, set()), (kind, keys - REQUIRED[kind])
        assert ID.match(element["id"]), element["id"]
        if element["frameId"] is not None:
            assert element["frameId"] in ids
        for end in ("startId", "endId"):
            if end in element:
                assert element[end] in ids
