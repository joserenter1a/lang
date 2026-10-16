import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from firefighter_mcp import checks, mcp, repo


def _has_repo() -> bool:
    try:
        repo.repo_root()
        return True
    except repo.RepoError:
        return False


needs_repo = pytest.mark.skipif(not _has_repo(), reason="firefighter-platform checkout not found")


# --- pure logic -------------------------------------------------------------

def test_sections_ignore_headings_in_code_fences():
    text = "# Title\nintro\n```bash\n# not a heading\n```\n## Child\nbody\n"
    secs = repo.sections(text)
    assert [s.title for s in secs] == ["Title", "Child"]
    assert "# not a heading" in secs[0].body


def test_section_with_children_stops_at_sibling():
    text = "## A\na\n### A1\na1\n## B\nb\n"
    out = repo.section_with_children(text, "a")
    assert "a1" in out and "## B" not in out


def test_bullets_join_continuation_lines():
    assert repo.bullets("- one\n  more\n- two\n\nafter") == ["one more", "two"]


@pytest.mark.parametrize("msg", [
    "shift: add the relief board [V45-850]",
    "docs: record the PR 208 review in the Response changelog [V45-803]",
    "fix: clear strings with an empty value",
])
def test_good_commit_messages(msg):
    assert checks.check_commit_message(msg)["ok"]


def test_foundation_commit_rejects_product_words_in_diff():
    out = checks.check_commit_message("foundation: add a theme seam", staged_diff="+ const name = 'Brass Acre'")
    assert not out["ok"] and "brass" in out["problems"][0]


def test_bad_commit_message():
    out = checks.check_commit_message("Updated stuff")
    assert not out["ok"]


def test_branch_names():
    assert checks.check_branch_name("ba-v45-803-apparatus-picker")["ok"]
    assert checks.check_branch_name("fix/null-updates")["ok"]
    assert not checks.check_branch_name("staging")["ok"]
    assert not checks.check_branch_name("my-feature")["ok"]


def test_pre_push_plan_flags_tier1_and_secrets():
    plan = checks.pre_push_plan(["amplify/data/modules/shift.ts", ".env.local", "modules/shift/lib/x.test.ts"])
    assert "npx tsc --noEmit -p amplify/tsconfig.json" in plan["commands"]
    assert ".env.local" in plan["never_commit"]
    assert "schema" in plan["tier1_review_required"]
    assert "changes to a test" in plan["tier1_review_required"]
    assert plan["docs_to_update"] == ["docs/shift-changelog.md"]


# --- against the checkout ---------------------------------------------------

@needs_repo
async def test_tools_are_read_only():
    async with Client(mcp) as c:
        tools = await c.list_tools()
    assert len(tools) >= 10
    assert all(t.annotations and t.annotations.read_only_hint for t in tools)


@needs_repo
async def test_products_cover_the_registry():
    async with Client(mcp) as c:
        products = (await c.call_tool("list_products", {})).data
        origin = (await c.call_tool("get_product", {"product": "origin"})).data
    assert len(products) == 17
    assert {p["group"] for p in products} == {"Prepare", "Respond", "People", "Lead"}
    assert origin["id"] == "investigations" and origin["data_domain"] == "respond"


@needs_repo
async def test_read_doc_refuses_paths_outside_repo():
    async with Client(mcp) as c:
        with pytest.raises(ToolError):
            await c.call_tool("read_doc", {"path": "../../etc/passwd.md"})


@needs_repo
async def test_search_rules_finds_gotcha():
    async with Client(mcp) as c:
        hits = (await c.call_tool("search_rules", {"query": "null update"})).data
    assert any("nullAllowedFields" in h["rule"] for h in hits)


@needs_repo
def test_source_tools_only_see_tracked_files():
    assert "amplify/data/domains/assignments.ts" in repo.tracked_files("amplify/data/domains/")
    assert not any(".env" == f or f.startswith(".env.") and not f.endswith("example") for f in repo.tracked_files())
    with pytest.raises(repo.RepoError):
        repo.read_source(".env")
    with pytest.raises(repo.RepoError):
        repo.read_source("../lang/README.md")
    text = repo.read_source("amplify/data/domains/assignments.ts", 1, 3)
    assert text.startswith("amplify/data/domains/assignments.ts (lines 1-3")
    assert any(h["path"] == "amplify/data/domains/assignments.ts" for h in repo.grep("kit: 'prepare'"))
