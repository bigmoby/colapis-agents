"""backlog_sync: keeps a Colapis Kanban in step with a GitHub repository's issues.

  colapis-backlog-sync reconcile owner/repo   # once, for example from cron
  colapis-backlog-sync serve [--port 8787]    # a GitHub webhook on /webhooks/github

No AI: a deterministic sync. GitHub must be connected to your Colapis account
(Board Hub → Integrations); the webhook needs GITHUB_WEBHOOK_SECRET.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import sys

from ..colapis import Colapis, ColapisError
from ..config import ColapisSettings, ConfigError, env, load_dotenv
from ..log import logger
from .sync import reconcile

log = logger("backlog_sync")


def valid_signature(secret: str, body: bytes, signature: str) -> bool:
    """GitHub signs each delivery with the webhook's secret (X-Hub-Signature-256)."""
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def make_app(settings: ColapisSettings, secret: str):
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    lock = asyncio.Lock()  # one sync at a time: two deliveries must not add the same card twice

    async def github(request: Request) -> JSONResponse:
        body = await request.body()
        if not valid_signature(secret, body, request.headers.get("x-hub-signature-256", "")):
            return JSONResponse({"error": "BAD_SIGNATURE"}, status_code=401)
        event = request.headers.get("x-github-event")
        if event == "ping":
            return JSONResponse({"ok": True})
        if event != "issues":
            return JSONResponse({"ignored": event})
        repo = json.loads(body)["repository"]["full_name"]
        async with lock, Colapis(settings) as colapis:
            result = await reconcile(colapis, repo)
        log(result)
        return JSONResponse({"result": result})

    return Starlette(routes=[Route("/webhooks/github", github, methods=["POST"])])


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="colapis-backlog-sync", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("reconcile", help="sync once").add_argument("repo", help="owner/repo")
    serve = commands.add_parser("serve", help="listen for GitHub webhooks")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    try:
        load_dotenv()
        settings = ColapisSettings.from_env()
        if args.command == "reconcile":

            async def run() -> None:
                async with Colapis(settings) as colapis:
                    print(await reconcile(colapis, args.repo))

            asyncio.run(run())
            return
        secret = env("GITHUB_WEBHOOK_SECRET")
        if len(secret) < 16:
            raise ConfigError("Set GITHUB_WEBHOOK_SECRET (16 characters or more), the secret of the GitHub webhook.")
        import uvicorn

        uvicorn.run(make_app(settings, secret), host=args.host, port=args.port)
    except (ConfigError, ColapisError, OSError) as error:
        log(str(error))
        sys.exit(1)
