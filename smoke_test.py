"""
Smoke test for CI. Validates the pipeline components without a full LLM run.

Checks:
  1. All agents and tools import without error
  2. PMD finds candidates in jenkins/core (static analysis works)
  3. Dead-code pre-classifier returns SAFE/HIGH without an API call
  4. If OPENAI_API_KEY is set: runs one full pipeline candidate end-to-end

Exit code 0 = pass, 1 = fail.
"""

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
failures = []


def check(label: str, fn):
    try:
        fn()
        print(f"  {PASS}  {label}")
    except Exception as e:
        print(f"  {FAIL}  {label}: {e}")
        failures.append(label)


print("\n=== Smoke Test ===\n")

# 1. Imports
def _imports():
    from tools.static_analysis import get_ranked_candidates, run_pmd
    from tools.memory_client import recall_decision, save_decision
    from tools.diff_generator import generate_diff
    from agents.context_retrieval_agent import run as retrieve_context
    from agents.thought_generator_agent import run as generate_branches
    from agents.critic_agent import run as run_critic
    from agents.recommendation_agent import run as recommend

check("All modules import cleanly", _imports)

# 2. PMD finds candidates in jenkins/core
def _pmd_candidates():
    from tools.static_analysis import run_pmd
    candidates = run_pmd("core")
    assert len(candidates) > 10, f"Expected >10 PMD candidates, got {len(candidates)}"

check("PMD finds candidates in jenkins/core", _pmd_candidates)

# 3. Dead-code pre-classifier (no API call)
def _dead_code_classifier():
    from agents.context_retrieval_agent import run as retrieve_context
    result = retrieve_context({
        "file": "core/src/main/java/hudson/Proc.java",
        "line": 183,
        "rule": "UnusedPrivateField",
        "description": "Avoid unused private fields",
    })
    assert result["verdict"] == "SAFE", f"Expected SAFE, got {result['verdict']}"
    assert result["confidence"] == "HIGH", f"Expected HIGH, got {result['confidence']}"

check("Dead-code pre-classifier returns SAFE/HIGH without API call", _dead_code_classifier)

# 4. Diff generator produces a unified diff
def _diff_generator():
    from tools.diff_generator import generate_diff
    diff = generate_diff(
        "core/src/main/java/hudson/Proc.java", 183, "REMOVE", "UnusedPrivateField"
    )
    assert diff and diff.startswith("---"), f"Expected unified diff, got: {diff[:60]}"

check("Diff generator produces unified diff for REMOVE/UnusedPrivateField", _diff_generator)

# 5. Full pipeline candidate (only if API key is available)
if os.getenv("OPENAI_API_KEY"):
    def _full_pipeline():
        from agents.context_retrieval_agent import run as retrieve_context
        from agents.thought_generator_agent import run as generate_branches
        from agents.critic_agent import run as run_critic
        from agents.recommendation_agent import run as recommend

        candidate = {
            "file": "core/src/main/java/hudson/Proc.java",
            "line": 183,
            "rule": "UnusedPrivateField",
            "category": "DEAD_CODE",
            "severity": "MEDIUM",
            "description": "Avoid unused private fields such as 'proc'.",
            "source": "pmd",
            "score": 2.4,
        }
        verdict = retrieve_context(candidate)
        assert verdict["verdict"] in ("SAFE", "RISKY", "UNCERTAIN")

        branches = generate_branches(candidate, verdict)
        assert len(branches) > 0, "No branches generated"

        critic = run_critic(candidate, branches, verdict)
        winner = critic.get("winner", {})
        assert winner.get("action") in ("REMOVE", "REFACTOR", "DEPRECATE", "KEEP")

        rec = recommend(candidate, verdict, winner)
        assert "action" in rec
        assert "confidence" in rec

    check("Full pipeline end-to-end (Proc.java UnusedPrivateField)", _full_pipeline)
else:
    print(f"  -  Full pipeline skipped (OPENAI_API_KEY not set)")

# Summary
print(f"\n{'='*36}")
if failures:
    print(f"FAILED — {len(failures)} check(s): {', '.join(failures)}")
    sys.exit(1)
else:
    print(f"ALL CHECKS PASSED")
print()
