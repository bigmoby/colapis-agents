from colapis_agents.md_to_board.layout import layout_plan, split_text
from colapis_agents.md_to_board.planner import normalize_plan

from .catalog import assert_valid

RAW = {
    "title": "Launch",
    "intro": ["The plan."],
    "sections": [
        {
            "title": "Backlog",
            "notes": "Start high.",
            "blocks": [
                {
                    "kind": "task",
                    "id": "t1",
                    "title": "Passkeys",
                    "description": "",
                    "priority": "High",
                    "assignee": "@giulia",
                },
                {"kind": "sticky", "id": "s1", "text": "Idea", "color": "teal"},
            ],
        },
        {
            "title": "Flow",
            "notes": None,
            "blocks": [
                {"kind": "node", "id": "a", "label": "Start", "shape": "ellipse"},
                {"kind": "node", "id": "b", "label": "Tests passed?", "shape": "diamond"},
                {"kind": "node", "id": "c", "label": "Store", "shape": "rectangle"},
            ],
        },
        {"title": None, "notes": None, "blocks": [{"kind": "text", "text": "Outside"}]},
    ],
    "connections": [
        {"from": "a", "to": "b"},
        {"from": "b", "to": "c", "label": "yes"},
        {"from": "b", "to": "a", "label": "no"},
        {"from": "t1", "to": "a"},
    ],
}


def test_normalize_fills_defaults():
    result = normalize_plan(RAW)
    assert not result["errors"]
    backlog = result["plan"]["sections"][0]["blocks"]
    assert backlog[0]["assignee"] == "giulia"
    assert backlog[1]["color"] == "yellow"


def test_normalize_reports_what_the_model_must_fix():
    raw = {
        **RAW,
        "connections": [{"from": "a", "to": "missing"}],
        "sections": [
            {
                "title": "S",
                "blocks": [
                    {"kind": "sticky", "id": "x", "text": "1"},
                    {"kind": "sticky", "id": "x", "text": "2"},
                    {"kind": "chart"},
                ],
            }
        ],
    }
    errors = normalize_plan(raw)["errors"]
    assert any("Duplicate id" in e for e in errors)
    assert any('unknown kind "chart"' in e for e in errors)
    assert any('"missing"' in e for e in errors)


def test_layout_is_valid_and_does_not_bind_task_cards():
    layout = layout_plan(normalize_plan(RAW)["plan"])
    assert_valid(layout["elements"])
    assert layout["stats"] == {
        **layout["stats"],
        "frames": 2,
        "taskCards": 1,
        "stickyNotes": 1,
        "shapes": 3,
        "arrows": 4,
    }
    from_task = next(e for e in layout["elements"] if e["type"] == "arrow" and e["frameId"] is None)
    assert "startId" not in from_task and "endId" in from_task  # an arrow never binds to a task card
    assert any(e["type"] == "text" and e["text"] == "Speaker notes: Start high." for e in layout["elements"])


def test_split_text_keeps_the_catalog_limit():
    parts = split_text("a" * 4500 + "\nend")
    assert all(len(part) <= 2000 for part in parts) and "".join(parts).replace("\n", "") == "a" * 4500 + "end"
