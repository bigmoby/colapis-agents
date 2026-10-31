from colapis_agents.repo_to_board.layout import layout_architecture
from colapis_agents.repo_to_board.planner import is_known_path, known_paths, normalize_architecture

from .catalog import assert_valid

CONTEXT = {
    "repository": "acme/shop",
    "structure": ["server/ (12 files)", "packages/orders (4 files)", "web/"],
    "sourceFiles": ["server/api.mjs", "web/src/App.tsx"],
    "manifests": [{"path": "package.json", "content": "{}"}],
}

PLAN = {
    "title": "Shop",
    "layers": [
        {"name": "Clients", "components": [{"id": "web", "name": "web", "kind": "ui", "path": "web"}]},
        {"name": "API", "components": [{"id": "api", "name": "api", "kind": "service", "path": "server/api.mjs"}]},
        {
            "name": "Domain",
            "components": [{"id": "orders", "name": "orders", "kind": "module", "path": "packages/orders"}],
        },
        {"name": "Data", "components": [{"id": "db", "name": "PostgreSQL", "kind": "datastore", "path": None}]},
    ],
    "flows": [
        {"from": "web", "to": "api", "label": "REST"},
        {"from": "api", "to": "orders"},
        {"from": "orders", "to": "db", "label": "SQL"},
        {"from": "web", "to": "db", "label": "cache"},
        {"from": "web", "to": "api", "label": "WebSocket"},
    ],
}


def test_known_paths_and_folders():
    paths = known_paths(CONTEXT)
    assert {"server", "packages/orders", "web", "server/api.mjs", "package.json"} <= paths
    assert is_known_path("./server/", paths, False)
    assert is_known_path("web/src", paths, False)  # a folder that contains a known file
    assert not is_known_path("mobile/app", paths, False)


def test_invented_paths_and_unknown_flows_are_errors():
    raw = {
        **PLAN,
        "layers": [
            *PLAN["layers"],
            {
                "name": "X",
                "components": [{"id": "ghost", "name": "ghost", "kind": "module", "path": "nowhere/ghost.py"}],
            },
        ],
        "flows": [*PLAN["flows"], {"from": "web", "to": "nobody"}],
    }
    result = normalize_architecture(raw, CONTEXT)
    assert any("nowhere/ghost.py" in error for error in result["errors"])
    assert any('"nobody"' in error for error in result["errors"])


def test_duplicate_flows_merge_their_labels():
    result = normalize_architecture(PLAN, CONTEXT)
    assert not result["errors"]
    web_api = [f for f in result["plan"]["flows"] if f["from"] == "web" and f["to"] == "api"]
    assert web_api == [{"from": "web", "to": "api", "label": "REST, WebSocket"}]


def test_layout_is_valid_and_binds_every_arrow():
    plan = normalize_architecture(PLAN, CONTEXT)["plan"]
    layout = layout_architecture(plan, repository="acme/shop", languages=["TypeScript"])
    assert_valid(layout["elements"])
    arrows = [e for e in layout["elements"] if e["type"] == "arrow"]
    assert len(arrows) == layout["stats"]["flows"] == 4
    assert all("startId" in a and "endId" in a for a in arrows)
    assert layout["stats"]["layers"] == 4
