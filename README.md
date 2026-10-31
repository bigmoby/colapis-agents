# colapis-agents

Example AI agents that work on [Colapis](https://colapis.it) Boards through its **MCP server**. Each one is a small command-line program you can run as it is, or read and adapt to build your own.

| Agent | What it does | AI |
|---|---|---|
| [`md_to_board`](#md_to_board) | Turns a Markdown document into a Board: frames, task cards, sticky notes, shapes and arrows | yes |
| [`board_to_md`](#board_to_md) | Turns a Board into a faithful Markdown document, reading it only | yes |
| [`repo_to_board`](#repo_to_board) | Draws a GitHub repository's architecture, layer by layer | yes |
| [`retro_facilitator`](#retro_facilitator) | Groups a retrospective's notes into themes and adds the actions as task cards | yes |
| [`standup_reporter`](#standup_reporter) | Writes the daily standup from the sprint Board, and posts it to Slack | yes |
| [`backlog_sync`](#backlog_sync) | Keeps a Kanban in step with a GitHub repository's issues, from cron or a webhook | no |

## How they talk to Colapis

```
agent ──MCP (Streamable HTTP, Authorization: Bearer col_sk_…)──► https://colapis.it/api/mcp
  │       list_boards · get_board_scene · insert_elements · update_task_card · …
  │
  └──REST, same key──► POST /api/v1/boards     (MCP has no tool that creates an empty Board)
```

- **One agent key** (`col_sk_…`) per account: it travels only in the `Authorization` header, never in a URL. Agent keys cannot create other keys or change the account.
- **Your own LLM key** (bring your own key): DeepSeek, Anthropic, OpenAI, Mistral or OpenRouter. Colapis never lends one, and the agents never use the account's AI key.
- **The model never writes coordinates.** It decides *what* goes on the Board; the agent computes positions, sizes and arrow routes, then sends elements of the Colapis blueprint catalog, which Colapis compiles into native elements.
- **Private Boards** are end-to-end encrypted in the browser: the server cannot read them, so no agent can.

## Install

Python 3.11 or later.

```bash
git clone https://github.com/bigmoby/colapis-agents
cd colapis-agents
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env    # then fill in your keys; .env is never committed
```

| Variable | Required | Description |
|---|---|---|
| `COLAPIS_API_KEY` | yes | Agent key `col_sk_…`: Colapis → Board Hub → AI & MCP → "Generate New Key" |
| `COLAPIS_SERVER_URL` | no | Default `https://colapis.it`; for a local instance `http://localhost:3000` |
| `LLM_PROVIDER` | no | `deepseek` (default), `anthropic`, `openai`, `mistral` or `openrouter` |
| `LLM_API_KEY` | for AI agents | Your key for that provider |
| `LLM_MODEL` | no | Default: `deepseek-flash`, `claude-sonnet-5-5`, `gpt-6.1-sol`, `mistral-large-latest`, `~openai/gpt-sol-latest` |

The GitHub tools (`repo_to_board`, `backlog_sync`) read GitHub with the token connected to your Colapis account: **Board Hub → Integrations → GitHub**. It needs Issues and Metadata, plus Contents for a private repository's architecture. Colapis never writes to GitHub.

Every command writes its progress to stderr and its result to stdout (a Board id, a document), so it fits in scripts.

## md_to_board

Reads a Markdown file and creates a Board with all its native elements and the connections between them.

```bash
colapis-md-to-board examples/launch-plan.md
colapis-md-to-board notes.md --title "Sprint 24 retro" --theme dark
colapis-md-to-board notes.md --dry-run -o preview.json      # plan and blueprint, nothing created
colapis-md-to-board notes.md --save-plan plan.json          # edit the plan by hand, then:
colapis-md-to-board --from-plan plan.json                   # no AI
```

| Markdown | Board |
|---|---|
| `# Title` | The Board's name and a title on top |
| Paragraphs before the first `##` | Introduction texts |
| `## Section` | A frame (a slide) |
| A section "Outside frames" | Elements below the frames |
| A table of work items (Title, Description, Priority, Assignee…) and `- [ ]` | Task cards, one per row |
| Lists of ideas, risks, questions, decisions | Sticky notes, coloured by meaning |
| ` ```mermaid ` flowcharts, `A → B` lines | Shapes (ellipses for start and end, diamonds for decisions) and labelled arrows |
| "Speaker notes" | A grey text at the bottom of the frame |

How it works: the LLM writes a plan (what each part becomes); if the plan has errors, such as duplicate ids or connections to missing blocks, it gets them back and has one turn to fix them. The layout is deterministic: frames side by side, four per row, so the slide order follows the document; connected items are laid out in columns from left to right, the others in a grid. A way back in a cycle passes below the two shapes, an arrow between frames runs in a corridor above them. Beyond 250 elements the Board is filled in several insertions, one per frame. Finally the agent reads the Board back and compares frames, task cards and sticky notes with what it expected.

Limits: an arrow is not bound to a task card (Colapis binds arrows to shapes and sticky notes only); a label on a bent arrow is a separate text; speaker notes become a text, since the catalog cannot set them on a frame.

## board_to_md

Reads a Board and writes a Markdown document: frames in slide order, speaker notes, task cards in tables, sticky notes, free texts and connections.

```bash
colapis-board-to-md --list --search Sprint        # no AI
colapis-board-to-md --board <boardId>             # writes output/<board-title>.md
colapis-board-to-md --search "Retrospective" --out retro.md
colapis-board-to-md --board <boardId> --out - > sprint.md
colapis-board-to-md "Export the Q1 roadmap Board, with the connections in Mermaid"
```

The agent **only reads**: the model is offered `list_boards`, `get_board_scene` and `export_board_presentation`, never the tools that write, and a call to them is refused by the agent too. A tool result over 400,000 characters is cut, and the model is told so.

## repo_to_board

Reads a GitHub repository through Colapis (`github_repository_context`: description, languages, structure, source file names, README, build files) and draws its architecture: real components laid out by layer, connected by arrows labelled with the protocol or the data that flows.

```bash
colapis-repo-to-board octo-org/app
colapis-repo-to-board https://github.com/octo-org/app --focus "the backend and its integrations"
colapis-repo-to-board octo-org/app --lang it --theme dark
colapis-repo-to-board octo-org/app --save-context context.json --save-plan plan.json --dry-run
colapis-repo-to-board --from-plan plan.json          # redraw an edited plan, no AI
colapis-repo-to-board --context context.json --focus "authentication"
```

| Kind | Shape |
|---|---|
| Interface (`ui`) | Purple rectangle |
| Service (`service`) | Blue rectangle, thick border |
| Module (`module`) | Light blue rectangle |
| Data (`datastore`) | Green ellipse |
| External system (`external`) | Grey dashed rectangle |

How it works: the LLM picks 2 to 6 layers along the main flow, with at most 36 components named after the repository's real folders, packages and services. Every `path` is checked against the repository data; an invented one goes back to the model for one correction turn. Each layer is a full-width frame; the components of each band are ordered by the position of their neighbours to reduce crossings, and attachment points are spread along each side. Arrows between neighbouring bands go straight; those that skip bands go down where the bands in between are free, or along a side corridor. All arrows stay bound to their nodes.

The model reads names, dependencies and documentation, not the code of the files, so check the result before you share it. The repository data is treated as untrusted: instructions found in it are never followed.

## retro_facilitator

```bash
colapis-retro <boardId>
```

Reads a retrospective's sticky notes by area, asks the LLM to group them into 3 to 6 themes and to propose 2 to 5 actions for the next sprint, then adds a frame beside the Board: one sticky note per theme, one task card per action. Try it on a Board made from [`examples/sprint-retro.md`](examples/sprint-retro.md).

## standup_reporter

```bash
colapis-standup "Sprint 43"
colapis-standup "Sprint 43" --slack      # also posts to SLACK_WEBHOOK_URL
```

Finds the most recent Board with that name, reads each task card with its column and writes a short standup: done, in progress and by whom, what needs attention. [`examples/standup-workflow.yml`](examples/standup-workflow.yml) runs it every weekday from GitHub Actions, with the keys in the repository's secrets.

## backlog_sync

```bash
colapis-backlog-sync reconcile octo-org/app       # once, for example from cron
colapis-backlog-sync serve --port 8787            # GitHub webhook on /webhooks/github
```

The first run imports the repository's issues into a Kanban named `octo-org/app · Kanban` (`github_import_issues`, no AI). Later runs update each card in place with `update_task_card` (title, status, assignee, priority) and add new issues to the column of their status with `insert_elements`. MCP has no tool that moves a card between columns yet, so a card whose issue changes status keeps its column and shows the new status first in its description.

For the webhook, set `GITHUB_WEBHOOK_SECRET` and add a webhook to the repository (Settings → Webhooks) with that secret, content type `application/json` and the **Issues** event. Deliveries with a wrong signature are refused.

## Develop

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check . && pytest
```

The tests check every element the agents build against the properties the Colapis blueprint catalog requires. The layouts of `md_to_board` and `repo_to_board` started as Node.js agents and were ported to Python with element-by-element identical output.

## Security

- Keys live in `.env`, which `.gitignore` keeps out of the repository. Never commit a key; in CI use the platform's secrets.
- The Colapis key goes only in the `Authorization` header; Colapis refuses keys in URLs.
- Documents, repositories, issues and Board contents are treated as data, never as instructions to the model.

## License

[MIT](LICENSE). COLAPIS™ is a trademark of Fabio Mauro; the license covers this code, not the name or the logo.
