"""
Orchestrator: a plain Python tool-calling loop over the Claude API.

No agent framework (LangGraph etc) -- with six tools and a handful of
branching steps, a raw loop is faster to build, easier to debug live,
and easier to explain to a judge than a framework adds value for.

This is the one genuinely "agentic" component in the system: it decides
WHAT TO PULL NEXT based on what earlier tool calls returned, rather than
running a fixed sequence. Every tool call it makes is logged into
`trace`, which becomes the evidence chain shown in the investigator's
case drill-down.
"""

import json
import os
import sqlite3

import anthropic

from agent.tools import TOOL_SCHEMAS, call_tool

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are a procurement investigation assistant. Given an
objective, use the available tools to find contracts worth flagging for
human review and assemble the evidence behind each one.

Rules you must follow:
- You never state that a vendor IS corrupt or guilty of anything. You
  only report which signals fired and what evidence supports them.
- Always explain WHY you are calling each tool -- what earlier finding
  prompted it. If a check comes back clean, say so and stop pursuing
  that thread rather than fishing for a flag.
- When you're done, produce a short case summary: which contract(s),
  which signals fired, what the vendor-graph/evidence findings were,
  and a plain-language note for the human investigator on what to check
  first. This is a recommendation for a human, not a verdict.
"""


def investigate(objective: str, db_path: str = "data/argus.db") -> dict:
    """Runs the orchestrator loop for one objective. Returns the final
    case summary text plus the full tool-call trace (the evidence chain).
    """
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    conn = sqlite3.connect(db_path)

    messages = [{"role": "user", "content": objective}]
    trace = []

    for _ in range(8):  # hard cap on tool-call rounds, safety net for the demo
        response = client.messages.create(
            model=MODEL,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            final_text = "".join(
                block.text for block in response.content if block.type == "text"
            )
            conn.close()
            return {"objective": objective, "summary": final_text, "trace": trace}

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            result = call_tool(conn, block.name, block.input)
            trace.append({"tool": block.name, "input": block.input, "result": result})
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, default=str),
                }
            )
        messages.append({"role": "user", "content": tool_results})

    conn.close()
    return {"objective": objective, "summary": "stopped: tool-call round limit reached", "trace": trace}


if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("Set ANTHROPIC_API_KEY before running the orchestrator.")

    result = investigate("Investigate power-sector contracts for anomalies")
    print("\n--- CASE SUMMARY ---")
    print(result["summary"])
    print(f"\n--- TOOL CALL TRACE ({len(result['trace'])} calls) ---")
    for step in result["trace"]:
        print(f"  {step['tool']}({step['input']})")
