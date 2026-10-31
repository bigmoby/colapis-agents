from colapis_agents.retro_facilitator.cli import collect_notes, layout
from colapis_agents.standup_reporter.cli import board_state

from .catalog import assert_valid

SCENE = {
    "name": "Retro",
    "frames": [
        {"id": "f1", "name": "Went well", "x": 0, "y": 0, "width": 500, "height": 400},
        {"id": "f2", "name": "To improve", "x": 600, "y": 0, "width": 500, "height": 400},
    ],
    "stickyNotes": [
        {"text": "Shipped early", "frameId": "f1"},
        {"text": "Flaky tests", "frameId": "f2"},
        {"text": " ", "frameId": "f2"},
    ],
    "freeTexts": [{"text": "Loose idea", "frameId": None}],
    "taskCards": [{"id": "t1", "frameId": "f2", "title": "#3 · Fix\ntests", "assignee": "marco", "priority": "High"}],
}


def test_notes_are_grouped_by_area():
    assert collect_notes(SCENE) == {
        "Went well": ["Shipped early"],
        "To improve": ["Flaky tests"],
        "Board": ["Loose idea"],
    }


def test_outcome_frame_sits_right_of_the_board():
    outcome = {
        "themes": [{"title": "Quality", "summary": "Tests"}],
        "actions": [{"title": "Quarantine flaky tests", "description": "", "owner": "", "priority": "Urgent"}],
    }
    elements = layout(SCENE, outcome, "Themes and actions")
    assert_valid(elements)
    assert elements[0]["type"] == "frame" and elements[0]["x"] == 1100 + 160
    assert next(e for e in elements if e["type"] == "task-card")["priority"] == "Medium"


def test_standup_reads_each_card_with_its_column():
    assert board_state(SCENE)["cards"] == [
        {"column": "To improve", "title": "#3 · Fix tests", "assignee": "marco", "priority": "High"}
    ]
