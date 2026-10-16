# firefighter-mcp

A FastMCP server that gives agents the context of the
[firefighter-platform](../../firefighter-platform) repo as tool calls: the
products and how they relate, the AWS stack (Cognito, AppSync, DynamoDB,
Lambda, S3/CloudFront, SES, CDK limits, Stripe), the rules and gotchas in
`CLAUDE.md`, every doc under `docs/`, and checks for the repo's working rules.

Everything is **read-only** and **read live** from the checked-out branch, so
answers follow the repo as it changes. Nothing is copied into this server
except a short curated map of what each stack component does here; the rules
attached to each component are pulled from `CLAUDE.md` at call time.

## Run

```bash
uv sync
uv run firefighter-mcp                    # stdio (Claude Code, langchain-mcp-adapters)
uv run firefighter-mcp --transport http   # streamable HTTP on 127.0.0.1:8765/mcp
uv run pytest                             # tests (the repo-backed ones skip without a checkout)
uv run fastmcp dev src/firefighter_mcp/server.py:mcp   # MCP Inspector
```

The repo is found at `../../firefighter-platform` relative to this folder;
set `FIREFIGHTER_REPO=/abs/path` to point elsewhere (e.g. a worktree).

`lang/.mcp.json` registers the server, so Claude Code started in `lang/`
picks it up.

## Tools

| Tool | Answers |
|---|---|
| `platform_overview` | What the platform is, products by group, the Spine, the stack, branch/commit read |
| `get_guidance(section)` | A `CLAUDE.md` section verbatim: overview, stack, layout, commands, architecture, conventions, workflow, gotchas |
| `search_rules(query)` | Individual `CLAUDE.md` rules/gotchas mentioning the query |
| `list_products(group?)` | The 17 products: group, stage, availability, data domain |
| `get_product(product)` | One product: registry entry, nav, code paths, DynamoDB models, Lambdas, Provides/Consumes, recent changelog |
| `get_stack_component(component)` | One stack piece's role here, its files and docs, and the rules that apply |
| `list_docs(category?)` / `search_docs(query)` / `read_doc(path, section?)` | The docs: index, section-level search, outline or section reads |
| `list_files(pathspec)` / `search_code(pattern, pathspec?)` / `read_source(path, start?, end?)` | The source itself, through git: tracked files only, so `.env*`, `amplify_outputs*` and `node_modules` never show |
| `check_commit_message(message, staged_diff?)` | `<area>: <imperative subject> [<ticket>]`, no product words in `foundation:` |
| `check_branch_name(branch)` | `ba-<id>-<slug>` / `fix/<topic>`, PR targets `staging` |
| `pre_push_plan(changed_paths)` | Checks to run, Tier 1 human-review areas, files never to commit, docs to update |

Resources: `firefighter://claude-md`, `firefighter://docs/{name}`,
`firefighter://products/{product_id}`.
Prompts: `implement_ticket(ticket_id, summary, product?)`, `review_pr(pr_number)`.

The check tools are deterministic, which makes them usable as eval scorers
for an agent loop (did the agent's commit pass `check_commit_message`, did it
run everything `pre_push_plan` listed).

## Using it from LangChain

```python
from langchain_mcp_adapters.client import MultiServerMCPClient

client = MultiServerMCPClient({
    "firefighter": {
        "transport": "stdio",
        "command": "uv",
        "args": ["run", "--directory", "/Volumes/joser_ssd/lang/mcp", "firefighter-mcp"],
    }
})
tools = await client.get_tools()   # pass to create_agent / create_deep_agent
```

For a deployed agent, run it with `--transport http` and use
`{"transport": "streamable_http", "url": "http://host:8765/mcp"}`.

## Layout

- `src/firefighter_mcp/server.py`: the FastMCP app, tools, resources, prompts
- `src/firefighter_mcp/catalog.py`: products parsed from `config/modules.ts`, `modules/<id>/module.ts`, `amplify/data/domains/assignments.ts`; the stack map; `CLAUDE.md` sections
- `src/firefighter_mcp/repo.py`: path-safe reads, markdown sectioning, doc search
- `src/firefighter_mcp/checks.py`: commit, branch and pre-push rules
