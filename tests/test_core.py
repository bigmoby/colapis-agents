import pytest

from colapis_agents.blueprint import element_id, js_round
from colapis_agents.board_to_md.agent import strip_fence
from colapis_agents.board_to_md.cli import slugify
from colapis_agents.config import ColapisSettings, ConfigError, LLMSettings
from colapis_agents.llm import Conversation, parse_json


def test_js_round_rounds_halves_up():
    assert [js_round(v) for v in (0.5, 1.5, 2.5, -0.5, -1.5)] == [1, 2, 3, 0, -1]


def test_element_id():
    assert element_id("issue", 17) == "issue-17"
    assert element_id("a b/c", "é") == "a-b-c"


def test_parse_json_reads_fenced_answers():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json("no json") is None


def test_settings_need_keys(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in ("COLAPIS_API_KEY", "LLM_API_KEY", "LLM_PROVIDER", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ConfigError):
        ColapisSettings.from_env()
    with pytest.raises(ConfigError):
        LLMSettings.from_env()
    monkeypatch.setenv("LLM_API_KEY", "x")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert LLMSettings.from_env().model == "claude-sonnet-5-5"


def test_tool_results_follow_the_provider_format():
    tools = [{"name": "list_boards", "description": "", "input_schema": {"type": "object"}}]
    openai = Conversation(LLMSettings("deepseek", "k", "m"), "s", tools=tools)
    openai.add_tool_results([("call1", "ok")])
    assert openai.messages == [{"role": "tool", "tool_call_id": "call1", "content": "ok"}]
    assert openai._openai_body()["tools"][0]["function"]["name"] == "list_boards"
    anthropic = Conversation(LLMSettings("anthropic", "k", "m"), "s", tools=tools)
    anthropic.add_tool_results([("call1", "ok")])
    assert anthropic.messages[0]["content"][0] == {"type": "tool_result", "tool_use_id": "call1", "content": "ok"}


def test_board_to_md_helpers():
    assert strip_fence("```markdown\n# Board\n```") == "# Board\n"
    assert slugify("Retrospettiva · Sprint 24") == "retrospettiva-sprint-24"
