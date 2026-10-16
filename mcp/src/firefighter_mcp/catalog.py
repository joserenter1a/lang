"""What the platform is made of: the products (parsed live from the repo) and
the stack (a curated map whose rules are pulled live from CLAUDE.md)."""

from __future__ import annotations

import re
from pathlib import Path

from . import repo

# --- CLAUDE.md --------------------------------------------------------------

GUIDANCE_SECTIONS = {
    "overview": "What this is",
    "stack": "Stack",
    "layout": "Layout",
    "commands": "Commands",
    "architecture": "Architecture essentials",
    "conventions": "Conventions",
    "workflow": "How Claude works here",
    "gotchas": "Gotchas",
}


def claude_md() -> str:
    return repo.read_text("CLAUDE.md")


def guidance(section: str) -> str:
    title = GUIDANCE_SECTIONS[section]
    text = repo.section_with_children(claude_md(), title)
    if text is None:
        raise repo.RepoError(f"CLAUDE.md has no '## {title}' section")
    return text


def rules(section: str) -> list[str]:
    """The bullet list under a CLAUDE.md section (one rule per bullet)."""
    body = guidance(section)
    if section == "workflow":  # numbered list
        return [m.strip() for m in re.split(r"\n(?=\d+\. )", body) if re.match(r"\d+\. ", m.strip())]
    return repo.bullets(body)


def matching_rules(keywords: list[str], sections_: tuple[str, ...] = ("architecture", "conventions", "gotchas")) -> list[dict]:
    out = []
    for sec in sections_:
        for rule in rules(sec):
            if any(re.search(rf"(?<!\w){re.escape(k)}", rule, re.I) for k in keywords):
                out.append({"section": sec, "rule": rule})
    return out


def spine_pieces() -> list[str]:
    m = re.search(r"The pieces that come with every login \(([^)]*)\)", " ".join(claude_md().split()))
    return [p.strip() for p in m.group(1).split(",")] if m else []


# --- products ---------------------------------------------------------------

_STR = r'((?:"(?:[^"\\]|\\.)*"\s*\+?\s*)+)'
_FIELDS = ("id", "slug", "name", "plainName", "tagline", "description", "group",
           "availability", "stage", "basePath", "price")


def _join_strings(raw: str) -> str:
    return "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', raw)).replace('\\"', '"')


def _registry_ids() -> list[str]:
    text = repo.read_text("config/modules.ts")
    return re.findall(r'from "@/modules/([\w-]+)/module"', text)


def _domains() -> dict[str, str]:
    text = repo.read_text("amplify/data/domains/assignments.ts")
    block = re.search(r"moduleDomains\s*=\s*\{(.*?)\}", text, re.S)
    return dict(re.findall(r"([\w-]+):\s*'(\w+)'", block.group(1))) if block else {}


def _parse_module(module_id: str) -> dict:
    text = repo.read_text(f"modules/{module_id}/module.ts")
    info: dict = {}
    for key in _FIELDS:
        m = re.search(rf"\b{key}:\s*{_STR}", text)
        if m:
            info[key] = _join_strings(m.group(1))
    m = re.search(r"\bdoor:\s*\"(\w+)\"", text)
    if m:
        info["first_door"] = m.group(1)
    m = re.search(r"worksWith:\s*\[([^\]]*)\]", text)
    info["works_with"] = re.findall(r'"([\w-]+)"', m.group(1)) if m else []
    nav = re.search(r"\bnav:\s*\[(.*?)\n\s*\]", text, re.S)
    info["nav"] = (
        [{"label": l, "href": h} for l, h in re.findall(r'label:\s*"([^"]*)",\s*href:\s*"([^"]*)"', nav.group(1))]
        if nav else []
    )
    doc = re.search(r"/\*\*(.*?)\*/\s*export const", text, re.S)
    if doc:
        info["notes"] = " ".join(line.strip().lstrip("*").strip() for line in doc.group(1).splitlines()).strip()
    return info


def list_products() -> list[dict]:
    domains = _domains()
    out = []
    for mid in _registry_ids():
        m = _parse_module(mid)
        out.append({
            "id": mid,
            "name": m.get("name"),
            "plain_name": m.get("plainName"),
            "group": m.get("group"),
            "stage": m.get("stage"),
            "availability": m.get("availability"),
            "data_domain": domains.get(mid, "foundation"),
            "tagline": m.get("tagline"),
        })
    return out


def _resolve_product_id(key: str) -> str:
    key = key.strip().lower()
    ids = _registry_ids()
    if key in ids:
        return key
    for mid in ids:
        m = _parse_module(mid)
        if key in {(m.get("name") or "").lower(), (m.get("slug") or "").lower(),
                   (m.get("plainName") or "").lower()}:
            return mid
    raise repo.RepoError(f"unknown product {key!r}; known ids: {', '.join(ids)}")


def _existing(paths: list[str]) -> list[str]:
    root = repo.repo_root()
    return [p for p in paths if (root / p).exists()]


def _models(schema_rel: str) -> list[str]:
    return re.findall(r"(\w+):\s*a\s*\.model\(", repo.read_text(schema_rel))


def get_product(key: str, changelog_entries: int = 5) -> dict:
    mid = _resolve_product_id(key)
    root = repo.repo_root()
    info = _parse_module(mid)
    info["data_domain"] = _domains().get(mid, "foundation")

    schemas = sorted(
        str(p.relative_to(root)) for p in (root / "amplify/data/modules").glob(f"{mid}*.ts")
        if p.stem == mid or p.stem.startswith(f"{mid}-")
    )
    functions = sorted(
        str(p.relative_to(root)) for p in (root / "amplify/functions").glob(f"{mid}-*") if p.is_dir()
    )
    info["code"] = {
        "module_dir": f"modules/{mid}/",
        "routes": _existing([f"app/(app)/{mid}/"]),
        "schema_files": schemas,
        "lambdas": functions,
        "services": _existing([f"lib/services/{mid}/"]),
    }
    info["models"] = {s: _models(s) for s in schemas}

    spec, log = f"docs/{mid}-module.md", f"docs/{mid}-changelog.md"
    info["docs"] = _existing([spec, log])
    if (root / spec).is_file():
        text = repo.read_text(spec)
        info["provides"] = repo.section_with_children(text, "Provides")
        info["consumes"] = repo.section_with_children(text, "Consumes")
    if (root / log).is_file():
        heads = [s for s in repo.sections(repo.read_text(log)) if s.level == 2]
        info["recent_changes"] = [s.title for s in heads[:changelog_entries]]
    return info


# --- the stack --------------------------------------------------------------

# Curated: what each service does *in this platform* and where it lives. The
# rules attached to each come from CLAUDE.md at call time via `keywords`.
STACK: dict[str, dict] = {
    "amplify": {
        "name": "AWS Amplify Gen 2",
        "role": "Defines the whole backend in TypeScript (auth, data, storage, functions) and deploys it per branch through Amplify Hosting; staging and main deploy to separate AWS accounts.",
        "paths": ["amplify/backend.ts", "amplify/data/resource.ts", "amplify/auth/", "amplify/storage/", "amplify/functions/"],
        "docs": ["CDK_WIRING_DEPLOY.md", "docs/bootstrap.md", "docs/getting-started.md"],
        "keywords": ["ampx", "sandbox", "Hosting", "BACKEND_MODULES", "defineAuth", "amplify_outputs"],
    },
    "cognito": {
        "name": "Amazon Cognito",
        "role": "Sign-in, MFA and roles. Groups Operator/Admin/Member/Viewer; Amplify's data client authorizes AppSync with the access token, so claims must land there (pre token generation trigger V2_0). Group members assume the group's IAM role.",
        "paths": ["amplify/auth/", "amplify/shared/constants.ts", "config/site.ts", "lib/roles.ts"],
        "docs": ["docs/sign-in-and-mfa.md", "docs/onboarding-and-permissions.md", "docs/operator-grants.md"],
        "keywords": ["cognito", "access token", "pre token", "IAM role", "Operator", "sign-in"],
    },
    "appsync": {
        "name": "AWS AppSync (GraphQL)",
        "role": "The canonical API and the tenancy/auth enforcement point. One foundation API (hubs, users, entitlements, the Spine) plus one API per product group data domain. Entitlement and record-access checks are APPSYNC_JS pipeline steps.",
        "paths": ["amplify/data/resource.ts", "amplify/data/vertical.ts", "amplify/data/domains/", "amplify/data/entitlements/", "amplify/data/record-access/", "lib/data-client.ts"],
        "docs": ["docs/data-domains.md", "docs/record-access.md", "docs/core-data-model.md", "docs/architecture.md"],
        "keywords": ["appsync", "graphql", "resolver", "APPSYNC_JS", "pipeline", "data domain", "entitlement"],
    },
    "dynamodb": {
        "name": "Amazon DynamoDB (+ Streams)",
        "role": "Every model is a table carrying orgId with a ...ByOrg GSI. Server-owned columns are filled by idempotent stream handlers (client writes, field rules restrict, a stream handler reacts).",
        "paths": ["amplify/data/modules/", "amplify/data/stream-consumers.ts"],
        "docs": ["docs/core-data-model.md", "docs/submit-then-verify.md", "docs/modules.md"],
        "keywords": ["dynamo", "stream", "GSI", "orgId", "table", "event source mapping"],
    },
    "lambda": {
        "name": "AWS Lambda",
        "role": "Command mutations (a.handler.function), stream consumers, triggers and webhooks. Node 22. Raw NodejsFunction constructs in amplify/custom/ only when defineFunction cannot bundle a dependency.",
        "paths": ["amplify/functions/", "amplify/custom/", "amplify/shared/"],
        "docs": ["docs/modules.md", "CDK_WIRING_DEPLOY.md"],
        "keywords": ["lambda", "a.handler.function", "NodejsFunction", "defineFunction", "command mutation"],
    },
    "s3": {
        "name": "Amazon S3",
        "role": "Media and file storage. Reads never go to S3 directly: they go through the CloudFront media CDN (getMediaAccess).",
        "paths": ["amplify/storage/", "amplify/data/media-auth.ts", "amplify/data/media-ingest.ts"],
        "docs": ["docs/image-delivery.md", "docs/video-delivery.md"],
        "keywords": ["s3", "storage", "bucket", "getMediaAccess"],
    },
    "cloudfront": {
        "name": "Amazon CloudFront media CDN",
        "role": "Signed media delivery with a sharp transform; needs MEDIA_CDN_PRIVATE_KEY in every environment.",
        "paths": ["amplify/custom/media-cdn/", "lib/media-cdn.ts"],
        "docs": ["docs/image-delivery.md", "docs/video-delivery.md"],
        "keywords": ["cloudfront", "media cdn", "MEDIA_CDN", "WAF"],
    },
    "ses": {
        "name": "Amazon SES",
        "role": "Outbound email. All outbound notifications go through dispatch() in amplify/shared/notify.ts; never add a second sender.",
        "paths": ["amplify/shared/notify.ts", "amplify/functions/ses-webhook-handler/"],
        "docs": ["SES_CONFIGURATION.md"],
        "keywords": ["ses", "notify", "dispatch", "notification", "email"],
    },
    "eventbridge": {
        "name": "Amazon EventBridge",
        "role": "Scheduled and event-driven backend work wired in amplify/backend.ts.",
        "paths": ["amplify/backend.ts"],
        "docs": ["docs/architecture.md"],
        "keywords": ["eventbridge", "schedule"],
    },
    "cloudformation": {
        "name": "CDK / CloudFormation limits",
        "role": "Custom CDK wiring in amplify/backend.ts. The backend sits near hard caps: 500 resources per stack, 1 MB template, 10,240-byte inline IAM policies, 2,500 resources per stack operation. check:backend synthesizes and scans for cycles locally.",
        "paths": ["amplify/backend.ts", "scripts/"],
        "docs": ["CDK_WIRING_DEPLOY.md", "docs/data-domains.md", "docs/bootstrap.md"],
        "keywords": ["cloudformation", "cdk", "synth", "cycle", "inline polic", "check:backend", "500 resources"],
    },
    "stripe": {
        "name": "Stripe billing",
        "role": "Embedded checkout + webhook Lambda mirroring into OrgSubscription. Module prices are one secret, STRIPE_MODULE_PRICES; preview modules are never sellable.",
        "paths": ["amplify/functions/create-checkout-session/", "amplify/functions/stripe-webhook-handler/", "amplify/data/sellable.ts", "config/pricing.ts", "config/price-book.ts"],
        "docs": ["docs/subscriptions-and-payments.md", "docs/modules.md"],
        "keywords": ["stripe", "checkout", "price", "billing", "sellable"],
    },
    "nextjs": {
        "name": "Next.js 16 frontend",
        "role": "App Router, React 19, strict TypeScript, Tailwind v4, shadcn/ui, MapLibre. Thin route files under app/(app)/<module>/; real UI in modules/<id>/components/.",
        "paths": ["app/", "components/ui/", "modules/", "config/theme.css", "app/components/AppShell.tsx", "app/components/ModuleShell.tsx"],
        "docs": ["docs/shell.md", "docs/design-system.md", "docs/getting-started.md"],
        "keywords": ["Next.js", "next build", "tailwind", "route", "door", "toast", "app frame", "theme token", "shadcn"],
    },
    "offline": {
        "name": "Device outbox (offline)",
        "role": "Writes saved on the device and sent in order when there is signal; one sender per module in config/offline.ts.",
        "paths": ["lib/offline/", "config/offline.ts"],
        "docs": ["docs/offline.md"],
        "keywords": ["offline", "outbox"],
    },
}


def stack_component(key: str) -> dict:
    key = key.strip().lower()
    if key not in STACK:
        raise repo.RepoError(f"unknown stack component {key!r}; known: {', '.join(STACK)}")
    entry = dict(STACK[key])
    entry["rules"] = matching_rules(entry.pop("keywords"))
    entry["paths"] = _existing(entry["paths"])
    return entry

