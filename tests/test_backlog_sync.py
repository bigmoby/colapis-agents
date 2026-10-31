from colapis_agents.backlog_sync.cli import valid_signature
from colapis_agents.backlog_sync.sync import Issue, plan_changes

from .catalog import assert_valid

FRAMES = [
    {"id": "f-open", "name": "Open", "x": 0, "y": 0, "width": 280, "height": 400},
    {"id": "f-closed", "name": "Closed", "x": 656, "y": 0, "width": 280, "height": 300},
]
CARDS = [
    {
        "id": "c16",
        "title": "#16 · Discussion\nsection",
        "description": "Open · The README",
        "assignee": "",
        "priority": "Medium",
    }
]


def issue(number, title, status="Open", assignee="", priority="Medium"):
    return Issue.from_tool(
        {
            "key": f"#{number}",
            "summary": title,
            "status": status,
            "assignee": assignee,
            "priority": priority,
            "description": "Some text",
        }
    )


def test_an_unchanged_issue_changes_nothing():
    assert plan_changes(CARDS, FRAMES, [issue(16, "Discussion section")]) == ([], [])


def test_a_closed_issue_updates_its_card():
    updates, inserts = plan_changes(CARDS, FRAMES, [issue(16, "Discussion section", "Closed", "giulia")])
    assert inserts == []
    assert updates == [
        {
            "taskCardId": "c16",
            "title": "#16 · Discussion section",
            "description": "Closed · Some text",
            "assignee": "giulia",
            "priority": "Medium",
        }
    ]


def test_a_new_issue_goes_in_the_column_of_its_status():
    updates, inserts = plan_changes(CARDS, FRAMES, [issue(17, "Dark mode", "Closed")])
    assert updates == []
    assert [i["frameId"] for i in inserts] == ["f-closed"]
    assert_valid(
        [
            *(
                {
                    "type": "frame",
                    "id": f["id"],
                    "frameId": None,
                    "x": 0,
                    "y": 0,
                    "width": 1,
                    "height": 1,
                    "title": f["name"],
                }
                for f in FRAMES
            ),
            inserts[0]["card"],
        ]
    )


def test_webhook_signature():
    body = b'{"action":"edited"}'
    import hashlib
    import hmac

    good = "sha256=" + hmac.new(b"secret-0123456789", body, hashlib.sha256).hexdigest()
    assert valid_signature("secret-0123456789", body, good)
    assert not valid_signature("secret-0123456789", body, "sha256=bad")
