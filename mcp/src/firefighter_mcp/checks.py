"""Deterministic checks of the repo's working rules (CLAUDE.md → Conventions,
How Claude works here). Pure functions, so an agent loop or an eval can score
a proposed commit, branch or change set without running anything."""

from __future__ import annotations

import re

# `git diff --cached | grep -niE 'brass|acre|firefight|investigat|ignis|maplewood'`
PRODUCT_WORDS = re.compile(r"brass|acre|firefight|investigat|ignis|maplewood", re.I)

_COMMIT = re.compile(r"^(?P<area>[a-z][a-z0-9-]*): (?P<subject>.+?)(?P<tickets>(?: \[[^\]]+\])*)$")
_BRANCH = re.compile(r"^(ba-(?P<ticket>[a-z0-9]+(?:-\d+)?)-(?P<slug>[a-z0-9-]+)|fix/[a-z0-9][a-z0-9-]*)$", re.I)
_NOT_IMPERATIVE = re.compile(r"^(added|adds|adding|fixed|fixes|fixing|updated|updates|updating|removed|removes|changed|changes)\b", re.I)


def check_commit_message(message: str, staged_diff: str | None = None) -> dict:
    subject = message.strip().splitlines()[0] if message.strip() else ""
    problems: list[str] = []
    m = _COMMIT.match(subject)
    if not m:
        problems.append("subject must be '<area>: <imperative subject> [<ticket>]', e.g. 'shift: add the relief board [V45-850]'")
    else:
        if _NOT_IMPERATIVE.match(m["subject"]):
            problems.append(f"subject should be imperative ('add', 'fix'), not {m['subject'].split()[0]!r}")
        if m["subject"][:1].isupper():
            problems.append("subject should start lowercase after the area (repo style)")
        if m["area"] == "foundation":
            if PRODUCT_WORDS.search(message):
                problems.append("foundation: commit message contains product words")
            if staged_diff and (hits := sorted({w.lower() for w in PRODUCT_WORDS.findall(staged_diff)})):
                problems.append(f"foundation: diff contains product words: {', '.join(hits)} (never mix foundation-generic and Brass Acre changes)")
    if len(subject) > 100:
        problems.append("subject is over 100 characters")
    return {
        "ok": not problems,
        "area": m["area"] if m else None,
        "tickets": re.findall(r"\[([^\]]+)\]", m["tickets"]) if m else [],
        "problems": problems,
    }


def check_branch_name(branch: str) -> dict:
    problems = []
    if branch in {"main", "staging"}:
        problems.append("never commit to main or staging directly; branch from staging")
    elif not _BRANCH.match(branch):
        problems.append("use 'ba-<id>-<slug>' for ticketed work or 'fix/<topic>' otherwise")
    return {"ok": not problems, "problems": problems,
            "pr_base": "staging", "pr_title_format": "[<id>] <short title>"}


# Paths whose changes need a person, never only an agent (Tier 1 review).
_TIER1 = [
    (r"^amplify/auth/|sign-in|mfa|auth-policy|org-auth-policy", "sign-in, sessions, permissions"),
    (r"^amplify/data/(entitlements|record-access|.*-access)", "permissions (entitlements / record access)"),
    (r"^amplify/data/(resource|vertical)\.ts$|^amplify/data/modules/|^amplify/data/domains/", "schema"),
    (r"^modules/run/|^amplify/functions/run-|^amplify/data/run-keys/", "patient data and the covered stack (Run)"),
    (r"stripe|checkout|pricing|price-book|sellable", "pay"),
    (r"^modules/investigations/|evidence|custody", "evidence and custody"),
    (r"notify|ses-|alert-cascade|shift-offer-sender", "anything that sends outside the agency"),
    (r"\.test\.ts$", "changes to a test"),
]

_NEVER_COMMIT = re.compile(r"(^|/)\.env|amplify_outputs|\.sandbox-operators")


def pre_push_plan(changed_paths: list[str]) -> dict:
    """The checks CI runs plus the extra ones the touched paths call for."""
    paths = [p.strip().removeprefix("./") for p in changed_paths if p.strip()]
    commands = [
        "npm test",
        "npx tsc --noEmit",
        "npm run check:copy",
        "npx eslint . --max-warnings=0",
        "npm run check:backend",
    ]
    reasons: dict[str, str] = {}
    if any(p.startswith(("app/", "components/")) or "/components/" in p for p in paths):
        commands.append("npx next build")
        reasons["npx next build"] = "UI files changed"
    if any(p.startswith("amplify/") for p in paths):
        commands.append("npx tsc --noEmit -p amplify/tsconfig.json")
        reasons["npx tsc --noEmit -p amplify/tsconfig.json"] = "amplify/ changed: the deploy type-check has no @/ alias"
    if any(p.startswith("amplify/") and p.endswith(".js") for p in paths):
        commands.append("npm run check:resolvers -- --profile <aws-profile>")
        reasons["npm run check:resolvers -- --profile <aws-profile>"] = "APPSYNC_JS changed: vitest passes code AppSync rejects (needs AWS creds)"
    if any("entitlements" in p for p in paths):
        commands.append("npm run check:entitlements")
        reasons["npm run check:entitlements"] = "entitlement logic changed"
    if any(p.startswith("amplify/data/domains/") or p.startswith("amplify/data/modules/") for p in paths):
        commands.append("npm run generate:domains")
        reasons["npm run generate:domains"] = "domain schemas changed: regenerate the client introspection"

    tier1 = sorted({label for p in paths for pat, label in _TIER1 if re.search(pat, p, re.I)})
    blocked = [p for p in paths if _NEVER_COMMIT.search(p)]
    docs_needed = sorted({f"docs/{p.split('/')[1]}-changelog.md" for p in paths
                          if p.startswith("modules/") and p.count("/") >= 2})
    if paths and any(p.startswith("modules/run/lib/generated/") for p in paths):
        reasons["generated"] = "modules/run/lib/generated/ changes only via npm run generate:neris"
    return {
        "commands": commands,
        "why": reasons,
        "tier1_review_required": tier1,
        "never_commit": blocked,
        "docs_to_update": docs_needed,
        "pr": "draft PR against staging with a plain-language before/after summary and the checks you ran; say plainly what has not run against a sandbox",
    }
