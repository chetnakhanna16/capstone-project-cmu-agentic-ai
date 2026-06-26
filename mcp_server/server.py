"""
MCP Server — shared memory layer for the Enterprise Code Cleanup Agent.

Exposes four tools to all agents:
  save_decision    — persist a pipeline recommendation to long-term memory
  recall_decision  — look up past decisions for a file or rule
  add_override     — developer marks a file/package as never-touch
  get_patterns     — return aggregate statistics (which rules are usually RISKY)
"""

import json
import asyncio
from datetime import datetime
from pathlib import Path
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

MEMORY_FILE = Path(__file__).parent.parent / "output" / "memory.json"


def _load() -> dict:
    if MEMORY_FILE.exists():
        return json.loads(MEMORY_FILE.read_text())
    return {"decisions": {}, "overrides": [], "patterns": {}, "risky_modules": []}


def _save(store: dict):
    MEMORY_FILE.write_text(json.dumps(store, indent=2))


server = Server("jenkins-memory")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="save_decision",
            description=(
                "Persist a pipeline recommendation to long-term memory. "
                "Call this after every completed pipeline run."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "Relative file path"},
                    "rule": {"type": "string", "description": "Static analysis rule"},
                    "action": {"type": "string", "description": "Final action taken"},
                    "confidence": {"type": "number", "description": "Confidence score 0-1"},
                    "verdict": {"type": "string", "description": "RAG verdict: SAFE/RISKY/UNCERTAIN"},
                    "rationale": {"type": "string", "description": "One-sentence rationale"},
                    "escalated": {"type": "boolean", "description": "Was this escalated to human?"},
                },
                "required": ["file", "rule", "action", "confidence", "verdict"],
            },
        ),
        types.Tool(
            name="recall_decision",
            description=(
                "Look up past decisions for a file path or rule. "
                "Use this BEFORE running the full pipeline to avoid re-analyzing "
                "something already decided."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file": {"type": "string", "description": "File path to look up (optional)"},
                    "rule": {"type": "string", "description": "Rule name to look up (optional)"},
                },
            },
        ),
        types.Tool(
            name="add_override",
            description=(
                "Developer marks a file, class, or package as never-touch. "
                "The pipeline will skip any candidate matching this pattern."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "File path or package pattern to block"},
                    "reason": {"type": "string", "description": "Why this is off-limits"},
                },
                "required": ["pattern", "reason"],
            },
        ),
        types.Tool(
            name="get_patterns",
            description=(
                "Return aggregate statistics: which rules are usually RISKY vs SAFE, "
                "which files have been flagged most often, and current override list."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    store = _load()

    if name == "save_decision":
        file_key = arguments["file"]
        record = {
            "rule": arguments["rule"],
            "action": arguments["action"],
            "confidence": arguments["confidence"],
            "verdict": arguments["verdict"],
            "rationale": arguments.get("rationale", ""),
            "escalated": arguments.get("escalated", False),
            "timestamp": datetime.utcnow().isoformat(),
        }
        if file_key not in store["decisions"]:
            store["decisions"][file_key] = []
        store["decisions"][file_key].append(record)

        # update pattern stats
        rule = arguments["rule"]
        verdict = arguments["verdict"]
        if rule not in store["patterns"]:
            store["patterns"][rule] = {"SAFE": 0, "RISKY": 0, "UNCERTAIN": 0, "total": 0}
        store["patterns"][rule][verdict] = store["patterns"][rule].get(verdict, 0) + 1
        store["patterns"][rule]["total"] += 1

        _save(store)
        return [types.TextContent(type="text", text=f"Saved decision for {file_key}: {arguments['action']}")]

    elif name == "recall_decision":
        results = []
        file_filter = arguments.get("file", "")
        rule_filter = arguments.get("rule", "")

        for file_path, records in store["decisions"].items():
            if file_filter and file_filter not in file_path:
                continue
            for r in records:
                if rule_filter and rule_filter not in r.get("rule", ""):
                    continue
                results.append({"file": file_path, **r})

        if not results:
            return [types.TextContent(type="text", text="No past decisions found.")]
        return [types.TextContent(type="text", text=json.dumps(results, indent=2))]

    elif name == "add_override":
        pattern = arguments["pattern"]
        reason = arguments["reason"]
        existing = [o["pattern"] for o in store["overrides"]]
        if pattern not in existing:
            store["overrides"].append({
                "pattern": pattern,
                "reason": reason,
                "added": datetime.utcnow().isoformat(),
            })
            _save(store)
            return [types.TextContent(type="text", text=f"Override added: {pattern} — {reason}")]
        return [types.TextContent(type="text", text=f"Override already exists for: {pattern}")]

    elif name == "get_patterns":
        summary = {
            "total_decisions": sum(len(v) for v in store["decisions"].values()),
            "files_analyzed": len(store["decisions"]),
            "overrides": store["overrides"],
            "rule_patterns": store["patterns"],
            "most_analyzed_files": sorted(
                [(f, len(r)) for f, r in store["decisions"].items()],
                key=lambda x: x[1], reverse=True
            )[:5],
        }
        return [types.TextContent(type="text", text=json.dumps(summary, indent=2))]

    return [types.TextContent(type="text", text=f"Unknown tool: {name}")]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
