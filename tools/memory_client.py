"""
Direct Python client for the MCP memory store.
Wraps the same JSON store the MCP server uses so the pipeline can
read/write memory without spawning a subprocess.
"""

import json
from datetime import datetime
from pathlib import Path

MEMORY_FILE = Path(__file__).parent.parent / "output" / "memory.json"


def _load() -> dict:
    if MEMORY_FILE.exists():
        return json.loads(MEMORY_FILE.read_text())
    return {"decisions": {}, "overrides": [], "patterns": {}, "risky_modules": []}


def _save(store: dict):
    MEMORY_FILE.write_text(json.dumps(store, indent=2))


def save_decision(file: str, rule: str, action: str, confidence: float,
                  verdict: str, rationale: str = "", escalated: bool = False):
    store = _load()
    record = {
        "rule": rule,
        "action": action,
        "confidence": confidence,
        "verdict": verdict,
        "rationale": rationale,
        "escalated": escalated,
        "timestamp": datetime.utcnow().isoformat(),
    }
    store["decisions"].setdefault(file, []).append(record)

    # update aggregate rule pattern stats
    store["patterns"].setdefault(rule, {"SAFE": 0, "RISKY": 0, "UNCERTAIN": 0, "total": 0})
    store["patterns"][rule][verdict] = store["patterns"][rule].get(verdict, 0) + 1
    store["patterns"][rule]["total"] += 1
    _save(store)


def recall_decision(file: str = "", rule: str = "") -> list[dict]:
    store = _load()
    results = []
    for file_path, records in store["decisions"].items():
        if file and file not in file_path:
            continue
        for r in records:
            if rule and rule not in r.get("rule", ""):
                continue
            results.append({"file": file_path, **r})
    return results


def is_overridden(file: str) -> tuple[bool, str]:
    """Check if a file matches any developer override pattern."""
    store = _load()
    for override in store["overrides"]:
        if override["pattern"] in file:
            return True, override["reason"]
    return False, ""


def add_override(pattern: str, reason: str):
    store = _load()
    existing = [o["pattern"] for o in store["overrides"]]
    if pattern not in existing:
        store["overrides"].append({
            "pattern": pattern,
            "reason": reason,
            "added": datetime.utcnow().isoformat(),
        })
        _save(store)


def get_rule_pattern(rule: str) -> dict:
    """Return historical SAFE/RISKY/UNCERTAIN distribution for a rule."""
    store = _load()
    return store["patterns"].get(rule, {})


def get_summary() -> dict:
    store = _load()
    return {
        "total_decisions": sum(len(v) for v in store["decisions"].values()),
        "files_analyzed": len(store["decisions"]),
        "overrides": store["overrides"],
        "rule_patterns": store["patterns"],
        "most_analyzed_files": sorted(
            [(f, len(r)) for f, r in store["decisions"].items()],
            key=lambda x: x[1], reverse=True
        )[:5],
    }
