"""firefighter-platform context server.

Read-only MCP tools that answer the questions an agent working in the
firefighter-platform repo asks before it changes anything: what the products
are and how they relate, how the AWS stack is put together, which rules and
gotchas apply, where the docs say it, and whether a commit or change set
follows the repo's working rules.
"""

from __future__ import annotations

from functools import wraps
from typing import Annotated, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from . import catalog, checks, repo

READ_ONLY = {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False}

mcp = FastMCP(
    name="firefighter-context",
    instructions=(
        "Context for the firefighter-platform repo (Brass Acre's multi-tenant fire department SaaS "
        "on Next.js + AWS Amplify Gen 2). Call platform_overview first in a new task. Before changing "
        "a product, call get_product; before touching AWS wiring, call get_stack_component; before "
        "committing, call check_commit_message and pre_push_plan. The repo is the source of truth: "
        "every answer is read live from the checked-out branch, and code wins over docs, so verify a "
        "doc's claim with search_code / read_source before relying on it."
    ),
)


def _tool(fn):
    """Register a read-only tool; repo errors surface as clean tool errors."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except repo.RepoError as e:
            raise ToolError(str(e)) from e

    return mcp.tool(annotations=READ_ONLY)(wrapper)


GuidanceSection = Literal["overview", "stack", "layout", "commands", "architecture", "conventions", "workflow", "gotchas"]
StackKey = Literal[tuple(catalog.STACK)]  # type: ignore[valid-type]
DocCategory = Literal["root", "module-spec", "changelog", "design", "playbook", "founder-package", "prototype"]


# --- orientation ------------------------------------------------------------

@_tool
def platform_overview() -> dict:
    """What the platform is, its product lineup by group, the Spine, the stack summary, and which branch/commit was read. Start here."""
    products = catalog.list_products()
    groups: dict[str, list[str]] = {}
    for p in products:
        groups.setdefault(p["group"] or "Ungrouped", []).append(f"{p['name']} ({p['id']})")
    return {
        "repo": repo.git_info(),
        "what_this_is": catalog.guidance("overview"),
        "product_groups": groups,
        "spine": catalog.spine_pieces(),
        "stack": catalog.rules("stack"),
        "stack_components": list(catalog.STACK),
        "data_domains": sorted({p["data_domain"] for p in products}),
    }


@_tool
def get_guidance(
    section: Annotated[GuidanceSection, Field(description="Which part of CLAUDE.md to return")],
) -> str:
    """A section of the repo's CLAUDE.md, verbatim: layout, commands, architecture essentials, conventions, the Claude workflow, or gotchas."""
    return repo.truncate(catalog.guidance(section))


@_tool
def search_rules(
    query: Annotated[str, Field(description="Words to look for, e.g. 'stream handler idempotent' or 'null update'")],
    sections: Annotated[list[GuidanceSection] | None, Field(description="Limit to these CLAUDE.md sections")] = None,
) -> list[dict]:
    """Find the individual rules and gotchas in CLAUDE.md that mention any of the query words, best match first."""
    terms = [t for t in query.lower().split() if len(t) > 2]
    if not terms:
        raise ToolError("query needs at least one word of three letters or more")
    out = []
    for sec in sections or ["architecture", "conventions", "gotchas", "workflow"]:
        for rule in catalog.rules(sec):
            low = rule.lower()
            score = sum(t in low for t in terms)
            if score:
                out.append({"section": sec, "matched": score, "rule": rule})
    out.sort(key=lambda r: -r["matched"])
    return out[:15]


# --- products ---------------------------------------------------------------

@_tool
def list_products(
    group: Annotated[Literal["Prepare", "Respond", "People", "Lead"] | None, Field(description="Only this product group")] = None,
) -> list[dict]:
    """Every product (module) in registry order with its group, stage, availability and data domain."""
    products = catalog.list_products()
    return [p for p in products if group is None or p["group"] == group]


@_tool
def get_product(
    product: Annotated[str, Field(description="Module id, product name, slug or plain name, e.g. 'shift', 'Origin', 'investigations'")],
    changelog_entries: Annotated[int, Field(ge=0, le=20)] = 5,
) -> dict:
    """One product in depth: registry entry, data domain, code locations, DynamoDB models, Lambdas, its Provides/Consumes contract and recent changelog entries."""
    return catalog.get_product(product, changelog_entries)


# --- stack ------------------------------------------------------------------

@_tool
def get_stack_component(
    component: Annotated[StackKey, Field(description="Stack component to explain")],
) -> dict:
    """How one piece of the stack (Cognito, AppSync, DynamoDB, Lambda, CDK limits, Stripe...) is used here: its role, the files and docs, and every CLAUDE.md rule and gotcha that applies to it."""
    return catalog.stack_component(component)


# --- docs -------------------------------------------------------------------

@_tool
def list_docs(
    category: Annotated[DocCategory | None, Field(description="Only this kind of doc")] = None,
) -> list[dict]:
    """The repo's markdown docs with a category and a one-line summary each."""
    out = []
    for path in repo.doc_paths():
        cat = repo.doc_category(path)
        if category and cat != category:
            continue
        out.append({"path": path, "category": cat,
                    "summary": repo.first_paragraph(repo.read_text(path), 160)})
    return out


@_tool
def search_docs(
    query: Annotated[str, Field(description="Words to search for across every doc section")],
    category: Annotated[DocCategory | None, Field(description="Only search this kind of doc")] = None,
    limit: Annotated[int, Field(ge=1, le=30)] = 10,
) -> list[dict]:
    """Search every markdown doc in the repo, section by section. Returns path, section heading, line and a snippet; follow up with read_doc."""
    paths = [p for p in repo.doc_paths() if category is None or repo.doc_category(p) == category]
    return repo.search(query, paths, limit)


@_tool
def read_doc(
    path: Annotated[str, Field(description="Repo-relative markdown path, e.g. 'docs/data-domains.md' or 'CLAUDE.md'")],
    section: Annotated[str | None, Field(description="Heading to return with its subsections (prefix match); omit for the outline")] = None,
) -> str:
    """Read a markdown doc. Without a section, returns the doc's heading outline (long docs) or the whole doc (short ones); with one, that section in full."""
    if not path.endswith(".md"):
        raise ToolError("read_doc reads markdown docs only")
    text = repo.read_text(path)
    if section:
        found = repo.section_with_children(text, section)
        if found is None:
            raise ToolError(f"no section starting with {section!r} in {path}; call read_doc without a section for the outline")
        return repo.truncate(found)
    if len(text) <= 12_000:
        return text
    outline = "\n".join(f"{'  ' * (s.level - 1)}- {s.title} (line {s.start_line})"
                        for s in repo.sections(text) if s.level)
    return f"{path} is {len(text):,} chars; outline below. Pass `section` to read one.\n\n{outline}"



# --- source code ------------------------------------------------------------

@_tool
def list_files(
    pathspec: Annotated[str, Field(description="Directory or git pathspec, e.g. 'modules/kit/' or 'amplify/functions/*shift*'")],
    limit: Annotated[int, Field(ge=1, le=500)] = 200,
) -> dict:
    """Tracked files under a path (git ls-files). Use it to confirm a file exists before citing it."""
    files = repo.tracked_files(pathspec)
    return {"total": len(files), "files": files[:limit]}


@_tool
def search_code(
    pattern: Annotated[str, Field(description="Extended regex, case-insensitive, e.g. 'getModuleClient\\(\"kit\"'")],
    pathspec: Annotated[str | None, Field(description="Limit to a directory or glob, e.g. 'amplify/' or '*.ts'")] = None,
    limit: Annotated[int, Field(ge=1, le=200)] = 60,
) -> list[dict]:
    """Search the tracked source (git grep): path, line and the matching text. Follow up with read_source."""
    return repo.grep(pattern, pathspec, limit)


@_tool
def read_source(
    path: Annotated[str, Field(description="Repo-relative path of a tracked file, e.g. 'amplify/data/modules/kit.ts'")],
    start: Annotated[int, Field(ge=1)] = 1,
    end: Annotated[int | None, Field(ge=1, description="Last line (inclusive); omit for the end of the file")] = None,
) -> str:
    """Read a tracked source file with line numbers, optionally a line range. Code wins over docs."""
    return repo.read_source(path, start, end)

# --- working rules ----------------------------------------------------------

@_tool
def check_commit_message(
    message: Annotated[str, Field(description="The full commit message")],
    staged_diff: Annotated[str | None, Field(description="Optional `git diff --cached` output, checked for product words on foundation commits")] = None,
) -> dict:
    """Validate a commit message against the repo's format '<area>: <imperative subject> [<ticket>]' and the foundation no-product-words rule."""
    return checks.check_commit_message(message, staged_diff)


@_tool
def check_branch_name(branch: str) -> dict:
    """Validate a branch name ('ba-<id>-<slug>' or 'fix/<topic>') and return where its PR must target."""
    return checks.check_branch_name(branch)


@_tool
def pre_push_plan(
    changed_paths: Annotated[list[str], Field(description="Repo-relative paths the change touches (e.g. from `git diff --name-only staging`)")],
) -> dict:
    """The checks to run before pushing, which areas need a human Tier 1 reviewer, files that must never be committed, and docs to update in the same change."""
    return checks.pre_push_plan(changed_paths)


# --- resources --------------------------------------------------------------

@mcp.resource("firefighter://claude-md", mime_type="text/markdown")
def claude_md_resource() -> str:
    """The repo's CLAUDE.md, verbatim."""
    return catalog.claude_md()


@mcp.resource("firefighter://docs/{name}", mime_type="text/markdown")
def doc_resource(name: str) -> str:
    """A doc under docs/ by file name, e.g. firefighter://docs/data-domains.md."""
    return repo.read_text(f"docs/{name}")


@mcp.resource("firefighter://products/{product_id}", mime_type="application/json")
def product_resource(product_id: str) -> dict:
    """A product's full context, as get_product returns it."""
    return catalog.get_product(product_id)


# --- prompts ----------------------------------------------------------------

@mcp.prompt
def implement_ticket(ticket_id: str, summary: str, product: str | None = None) -> str:
    """Start work on a founder ticket the way the repo expects."""
    focus = f" in the {product} product" if product else ""
    return f"""You are implementing ticket [{ticket_id}]{focus}: {summary}

Before writing code:
1. Call platform_overview, then get_product{f'("{product}")' if product else ''} for the product you are changing.
2. Call search_rules and search_docs with the ticket's key nouns; read the relevant sections with read_doc.
3. If the change touches AWS wiring, call get_stack_component for each service involved.

While working: branch `ba-{ticket_id.lower()}-<slug>` from up-to-date staging (check_branch_name), keep the diff on the ticket,
add colocated *.test.ts for logic you change, and follow "repo first": if the brief names something the repo does not have,
build it the repo's way and say so in the PR.

Before pushing: call pre_push_plan with the changed paths and run every command it lists; call check_commit_message on each
commit. Open a draft PR against staging titled "[{ticket_id}] <short title>" with a plain before/after summary, the checks you
ran, and what has not been exercised against a sandbox."""


@mcp.prompt
def review_pr(pr_number: int) -> str:
    """Review a teammate's PR following the repo's playbook."""
    return f"""Review PR #{pr_number} in firefighter-platform.

Read the playbook first: read_doc("docs/playbooks/reviewing-a-pr.md"). Then, for the PR's changed paths, call pre_push_plan to get
the checks to run and the Tier 1 areas that need a person, get_product for each product touched, and search_rules for anything
the diff does that CLAUDE.md has a gotcha about (APPSYNC_JS limits, null updates, ids on create, stack caps, access tokens).
Report each violation with the rule it breaks and where."""


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="firefighter-platform context MCP server")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.transport == "http":
        mcp.run(transport="http", host=args.host, port=args.port, show_banner=False)
    else:
        mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
