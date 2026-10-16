"""Deep Agent over the firefighter-platform context MCP server (../mcp), on Claude.

    uv run firefighter_agent.py "How does Shift's data reach AppSync?"   # one question
    uv run firefighter_agent.py                                        # chat loop

Secrets come from .env: CLAUDE_API_KEY, plus LANGSMITH_* for tracing.
"""

import asyncio
import os
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from deepagents import create_deep_agent
from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.checkpoint.memory import InMemorySaver

load_dotenv()

MODEL = os.getenv("FIREFIGHTER_AGENT_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("FIREFIGHTER_AGENT_EFFORT", "high")  # Opus 5.5 defaults to medium
MCP_DIR = Path(__file__).resolve().parent.parent / "mcp"

SYSTEM_PROMPT = """You are an engineer's assistant for the firefighter-platform repo: Brass Acre's \
multi-tenant fire department SaaS built on Next.js and AWS Amplify Gen 2 (Cognito, AppSync, \
DynamoDB, Lambda, S3, SES, CloudFront, Stripe).

Your firefighter-context tools read the repo live from the checked-out branch. Ground every answer \
in them rather than in general knowledge: start a new topic with platform_overview, use get_product \
for a product, get_stack_component for AWS wiring, search_rules for CLAUDE.md rules and gotchas, \
and search_docs then read_doc for design docs. Docs can lag the code: before you state that a file, \
model, function or setting exists, confirm it with list_files, search_code or read_source. Code wins \
over docs when they disagree.

Your built-in file tools (ls, read_file, write_file, edit_file, glob, grep) work on a private \
scratchpad, not the repo; use them only for your own notes.

Cite the files and doc sections you relied on. If the tools don't answer something, say so plainly \
instead of guessing. When asked to plan a change, include the branch name, the checks from \
pre_push_plan, and any Tier 1 areas that need a human reviewer."""


def build_model() -> ChatAnthropic:
    api_key = os.getenv("CLAUDE_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("Set CLAUDE_API_KEY in deep-agent/.env")
    # An org-level key (not scoped to one workspace) must name the workspace per request.
    workspace = os.getenv("CLAUDE_WORKSPACE_ID") or os.getenv("ANTHROPIC_WORKSPACE_ID")
    return ChatAnthropic(
        model=MODEL,
        api_key=api_key,
        max_tokens=16000,
        output_config={"effort": EFFORT},
        default_headers={"anthropic-workspace-id": workspace} if workspace else None,
    )


def mcp_client() -> MultiServerMCPClient:
    return MultiServerMCPClient({
        "firefighter": {
            "transport": "stdio",
            "command": "uv",
            "args": ["run", "--directory", str(MCP_DIR), "firefighter-mcp"],
        }
    })


@asynccontextmanager
async def firefighter_agent():
    """The agent, with one MCP server process kept open for every tool call it makes."""
    async with mcp_client().session("firefighter") as session:
        tools = await load_mcp_tools(session)
        yield create_deep_agent(
            model=build_model(),
            tools=tools,
            system_prompt=SYSTEM_PROMPT,
            checkpointer=InMemorySaver(),
        )


async def ask(agent, question: str, thread_id: str) -> str:
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": question}]},
        config={"configurable": {"thread_id": thread_id}},
    )
    return result["messages"][-1].text


async def main():
    thread_id = str(uuid.uuid4())
    question = " ".join(sys.argv[1:])
    async with firefighter_agent() as agent:
        if question:
            print(await ask(agent, question, thread_id))
            return
        print("firefighter agent; empty line or Ctrl-D to quit")
        while True:
            try:
                question = input("\n> ").strip()
            except EOFError:
                break
            if not question:
                break
            print(await ask(agent, question, thread_id))


if __name__ == "__main__":
    asyncio.run(main())
