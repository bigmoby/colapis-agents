"""Settings, read from the environment or from a .env file in the working directory."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Every provider's endpoint is fixed: users choose a provider, a key and a model,
# never an address.
PROVIDERS = {
    "deepseek": ("https://api.deepseek.com/chat/completions", "deepseek-flash"),
    "anthropic": ("https://api.anthropic.com/v1/messages", "claude-sonnet-5-5"),
    "openai": ("https://api.openai.com/v1/chat/completions", "gpt-6.1-sol"),
    "mistral": ("https://api.mistral.ai/v1/chat/completions", "mistral-large-latest"),
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "~openai/gpt-sol-latest"),
}
DEFAULT_SERVER_URL = "https://colapis.it"


class ConfigError(RuntimeError):
    pass


def load_dotenv(path: Path = Path(".env")) -> None:
    """Loads KEY=value lines, without overriding variables already set."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


@dataclass(frozen=True)
class ColapisSettings:
    server_url: str
    api_key: str

    @classmethod
    def from_env(cls) -> ColapisSettings:
        load_dotenv()
        api_key = env("COLAPIS_API_KEY")
        if not api_key.startswith("col_sk_"):
            raise ConfigError(
                "COLAPIS_API_KEY is missing: create an agent key (col_sk_...) in Colapis, "
                "Board Hub → AI & MCP → 'Generate New Key'."
            )
        return cls(server_url=env("COLAPIS_SERVER_URL", DEFAULT_SERVER_URL).rstrip("/"), api_key=api_key)


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    api_key: str
    model: str

    @classmethod
    def from_env(cls, model: str | None = None) -> LLMSettings:
        """Your own key for your own provider: Colapis never lends one."""
        load_dotenv()
        provider = env("LLM_PROVIDER", "deepseek").lower()
        if provider not in PROVIDERS:
            raise ConfigError(f"LLM_PROVIDER must be one of: {', '.join(PROVIDERS)}.")
        api_key = env("LLM_API_KEY")
        if not api_key:
            raise ConfigError(f"LLM_API_KEY is missing: the API key of your {provider} account.")
        return cls(provider=provider, api_key=api_key, model=model or env("LLM_MODEL") or PROVIDERS[provider][1])
