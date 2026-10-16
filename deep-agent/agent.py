"""Minimal Deep Agent research example, from the Deep Agents Python quickstart.

Uses Gemini's built-in Google Search instead of Tavily, so the only secret
needed is GOOGLE_API_KEY (in .env).
"""

import os
import sys

from dotenv import load_dotenv
from deepagents import create_deep_agent
from langchain.agents.middleware import wrap_model_call

load_dotenv()

MODEL = os.getenv("DEEP_AGENT_MODEL", "google_genai:gemini-3.5-flash")

# Google's built-in search: no extra install or API key needed
internet_search = {"google_search": {}}

research_instructions = """You are an expert researcher. Your job is to conduct \
thorough research and then write a polished report.

You have access to Google Search as your primary means of gathering information. \
Use it to find current, authoritative sources, then synthesize what you find into \
a clear, well-organized answer that cites its sources.
"""



@wrap_model_call
def enable_server_side_tools(request, handler):
    """Let Gemini mix built-in Google Search with the agent's function tools."""
    if MODEL.startswith("google_genai:"):
        settings = {
            **request.model_settings,
            "tool_config": {"include_server_side_tool_invocations": True},
        }
        request = request.override(model_settings=settings)
    return handler(request)


agent = create_deep_agent(
    model=MODEL,
    tools=[internet_search],
    system_prompt=research_instructions,
    middleware=[enable_server_side_tools],
)


def main():
    question = " ".join(sys.argv[1:]) or "What is langgraph?"
    result = agent.invoke({"messages": [{"role": "user", "content": question}]})
    # Print the agent's response
    print(result["messages"][-1].text)


if __name__ == "__main__":
    main()
