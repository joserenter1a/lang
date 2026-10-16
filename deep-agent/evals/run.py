"""Eval: can the firefighter agent answer what the founder decided?

Cases come from docs/founder-packages-plan.md (the team's record of Mike's
v4.3 and v4.6 packages) in founder_plan_cases.json. Each run syncs them to a
LangSmith dataset and scores the agent with three evaluators:

  key_facts    deterministic: share of required facts present in the answer
  correctness  Claude grades the answer against the reference (1, 0.5 or 0)
  grounded     the agent read the repo through its tools instead of guessing

    uv run evals/run.py --check            # offline: validate cases and scorers, no API calls
    uv run evals/run.py --limit 3          # a few cases
    uv run evals/run.py                    # every case
"""

import argparse
import asyncio
import json
import re
import sys
import uuid
from pathlib import Path

from langsmith import Client, aevaluate
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from firefighter_agent import ask_with_trace, build_model, firefighter_agent  # noqa: E402

CASES_FILE = Path(__file__).with_name("founder_plan_cases.json")
DATASET = "firefighter-founder-plan"
CASE_NS = uuid.UUID("6f1c2b0e-4d1a-4f43-9c55-0b8e2f6d7a10")  # stable example ids across syncs


def load_cases() -> list[dict]:
    cases = json.loads(CASES_FILE.read_text())
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate case id"
    for c in cases:
        assert c["question"] and c["reference"] and c["must_include"], c["id"]
    return cases


def sync_dataset(client: Client, cases: list[dict]) -> None:
    """Create or update the LangSmith dataset so it matches the JSON file."""
    if not client.has_dataset(dataset_name=DATASET):
        client.create_dataset(DATASET, description="Questions from docs/founder-packages-plan.md (Mike's v4.3/v4.6 decisions)")
    existing = {str(e.id) for e in client.list_examples(dataset_name=DATASET)}
    rows = [{
        "id": str(uuid.uuid5(CASE_NS, c["id"])),
        "inputs": {"question": c["question"]},
        "outputs": {"reference": c["reference"], "must_include": c["must_include"]},
        "metadata": {"case_id": c["id"], "source": c["source"]},
    } for c in cases]
    new = [r for r in rows if r["id"] not in existing]
    old = [r for r in rows if r["id"] in existing]
    if new:
        client.create_examples(dataset_name=DATASET, examples=new)
    if old:
        client.update_examples(dataset_name=DATASET, updates=old)
    stale = existing - {r["id"] for r in rows}
    for example_id in stale:
        client.delete_example(example_id)
    print(f"dataset {DATASET}: {len(new)} new, {len(old)} updated, {len(stale)} removed")


# --- evaluators -------------------------------------------------------------

def _norm(text: str) -> str:
    text = text.lower().replace("–", "-").replace("—", "-").replace("‑", "-")
    return re.sub(r"\s+", " ", text)


def key_facts(outputs: dict, reference_outputs: dict) -> dict:
    answer = _norm(outputs.get("answer", ""))
    groups = reference_outputs["must_include"]
    missing = [alts[0] for alts in groups if not any(_norm(a) in answer for a in alts)]
    score = 1 - len(missing) / len(groups)
    return {"key": "key_facts", "score": score,
            "comment": "all present" if not missing else "missing: " + ", ".join(missing)}


# Tools that read the repo; the deepagents scratchpad tools don't count.
REPO_TOOLS = {"platform_overview", "get_guidance", "search_rules", "list_products", "get_product",
              "get_stack_component", "list_docs", "search_docs", "read_doc", "list_files",
              "search_code", "read_source", "pre_push_plan", "check_commit_message", "check_branch_name"}


def grounded(outputs: dict) -> dict:
    used = sorted(set(outputs.get("tools", [])) & REPO_TOOLS)
    return {"key": "grounded", "score": 1.0 if used else 0.0,
            "comment": ", ".join(used) if used else "answered without reading the repo"}


class Grade(BaseModel):
    reasoning: str = Field(description="One short paragraph: what matches the reference, what is missing or wrong")
    grade: str = Field(description="correct, partial or incorrect")


JUDGE_PROMPT = """You grade an engineering assistant's answer about the firefighter-platform repo \
against a reference answer written from the team's decision record.

Question: {question}

Reference answer: {reference}

Assistant's answer: {answer}

Grade it:
- correct: every key point in the reference is present or clearly implied, and nothing contradicts it.
- partial: the main point is right but a key detail is missing, or a minor detail is wrong.
- incorrect: the main point is wrong or missing, it contradicts the reference, it states an open \
decision as settled, or it invents a fact (a price, a date, a decision) the reference says does not exist.
Extra correct detail beyond the reference is fine. Judge substance, not wording or length."""

_SCORE = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}
_judge = None


async def correctness(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    global _judge
    if _judge is None:
        # json_schema, not function_calling: Opus 5.5 rejects a forced tool choice.
        _judge = build_model(effort="low").with_structured_output(Grade, method="json_schema")
    grade: Grade = await _judge.ainvoke(JUDGE_PROMPT.format(
        question=inputs["question"], reference=reference_outputs["reference"],
        answer=outputs.get("answer", "")))
    label = grade.grade.strip().lower()
    return {"key": "correctness", "score": _SCORE.get(label, 0.0), "value": label, "comment": grade.reasoning}


# --- run --------------------------------------------------------------------

def check_offline(cases: list[dict]) -> None:
    """Validate the cases and the deterministic scorers on each reference answer."""
    bad = []
    for c in cases:
        ref = {"must_include": c["must_include"]}
        result = key_facts({"answer": c["reference"]}, ref)
        if result["score"] < 1:
            bad.append(f"{c['id']}: reference fails its own key facts ({result['comment']})")
    assert grounded({"tools": ["read_doc", "ls"]})["score"] == 1.0
    assert grounded({"tools": ["ls", "read_file"]})["score"] == 0.0
    assert key_facts({"answer": "Oct 14 at 9pm"}, {"must_include": [["oct 14"], ["9pm"]]})["score"] == 1.0
    if bad:
        sys.exit("\n".join(bad))
    print(f"{len(cases)} cases OK; every reference passes its own key facts")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="validate offline; no API calls")
    parser.add_argument("--limit", type=int, help="run only the first N cases")
    parser.add_argument("--concurrency", type=int, default=3)
    args = parser.parse_args()

    cases = load_cases()
    if args.check:
        check_offline(cases)
        return

    client = Client()
    sync_dataset(client, cases)
    wanted = {str(uuid.uuid5(CASE_NS, c["id"])) for c in cases[: args.limit]}
    examples = [e for e in client.list_examples(dataset_name=DATASET) if str(e.id) in wanted]

    async with firefighter_agent() as agent:
        async def target(inputs: dict) -> dict:
            return await ask_with_trace(agent, inputs["question"], str(uuid.uuid4()))

        results = await aevaluate(
            target,
            data=examples,
            evaluators=[key_facts, grounded, correctness],
            experiment_prefix="founder-plan",
            metadata={"cases": len(examples)},
            max_concurrency=args.concurrency,
            client=client,
        )
        scores: dict[str, list[float]] = {}
        async for row in results:
            for r in row["evaluation_results"]["results"]:
                if r.score is not None:
                    scores.setdefault(r.key, []).append(float(r.score))

    print(f"\nexperiment: {results.experiment_name}")
    for key, vals in sorted(scores.items()):
        print(f"  {key:12s} {sum(vals) / len(vals):.2f}  (n={len(vals)})")

if __name__ == "__main__":
    asyncio.run(main())
