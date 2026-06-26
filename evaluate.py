"""
Evaluation framework for the Enterprise Code Cleanup Agent.

Runs the full pipeline on 20 candidates, compares results against
ground truth labels, and measures:
  - Accuracy       : did the system recommend the right action?
  - False-positive : did it recommend REMOVE on something that should be kept?
  - Calibration    : do confidence scores match actual correctness?
  - Escalation rate: what fraction was flagged for human review?
"""

import json
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
sys.path.insert(0, str(Path(__file__).parent))

from tools.memory_client import save_decision, is_overridden
from agents.context_retrieval_agent import run as retrieve_context
from agents.thought_generator_agent import run as generate_branches
from agents.critic_agent import run as run_critic
from agents.recommendation_agent import run as recommend

OUTPUT_DIR = Path(__file__).parent / "output"

# Ground truth labels:
# For pure dead code (private/local, never referenced) → correct action is REMOVE or REFACTOR
# For plugin-adjacent code → correct action is KEEP or DEPRECATE (never REMOVE)
# Label format: (file_snippet, rule, ground_truth_action, is_safe_to_remove)
GROUND_TRUTH = [
    # --- Clear dead code in utility classes (should recommend REMOVE/REFACTOR) ---
    ("DependencyRunner.java",  56,  "UnusedLocalVariable",  "REFACTOR", True),
    ("StructuredForm.java",    70,  "UnusedLocalVariable",  "REMOVE",   True),
    ("Proc.java",             183,  "UnusedPrivateField",   "REMOVE",   True),
    ("WebAppMain.java",       134,  "UnusedPrivateField",   "REMOVE",   True),
    ("FilePath.java",        2738,  "UnusedPrivateMethod",  "REMOVE",   True),
    ("CLICommand.java",       281,  "UnusedLocalVariable",  "REMOVE",   True),
    ("CLICommand.java",       286,  "UnusedLocalVariable",  "REMOVE",   True),
    ("CLICommand.java",       290,  "UnusedLocalVariable",  "REMOVE",   True),
    ("WebAppMain.java",       406,  "UnusedLocalVariable",  "REMOVE",   True),

    # --- SpotBugs style issues in plain classes (should recommend REFACTOR) ---
    ("BuildCommand.java",     204,  "NP_NULL_ON_SOME_PATH_FROM_RETURN_VALUE", "REFACTOR", True),
    ("CrontabParser.java",    382,  "SF_SWITCH_NO_DEFAULT",  "REFACTOR", True),

    # --- Plugin-adjacent code (should NOT recommend REMOVE — escalate or KEEP) ---
    ("ExtensionComponent.java", 42, "EQ_COMPARETO_USE_OBJECT_EQUALS", "KEEP", False),
    ("PluginWrapper.java",     111, "EQ_COMPARETO_USE_OBJECT_EQUALS", "KEEP", False),
    ("Functions.java",        1331, "EQ_COMPARETO_USE_OBJECT_EQUALS", "KEEP", False),
    ("MarkupText.java",         54, "EQ_COMPARETO_USE_OBJECT_EQUALS", "KEEP", False),
    ("FilePath.java",         3839, "NP_NULL_ON_SOME_PATH_FROM_RETURN_VALUE", "KEEP", False),
    ("AbstractBuild.java",     842, "NP_NULL_ON_SOME_PATH_FROM_RETURN_VALUE", "KEEP", False),
    ("Computer.java",         1508, "NP_NULL_ON_SOME_PATH_FROM_RETURN_VALUE", "KEEP", False),
    ("Descriptor.java",        732, "NP_NULL_ON_SOME_PATH_FROM_RETURN_VALUE", "KEEP", False),
    ("UpdateSite.java",       1090, "NP_NULL_ON_SOME_PATH_FROM_RETURN_VALUE", "KEEP", False),
]

JAVA_SRC = "core/src/main/java"
FILE_MAP = {
    "DependencyRunner.java": f"{JAVA_SRC}/jenkins/model/lazy/DependencyRunner.java",
    "StructuredForm.java":   f"{JAVA_SRC}/hudson/StructuredForm.java",
    "Proc.java":             f"{JAVA_SRC}/hudson/Proc.java",
    "WebAppMain.java":       f"{JAVA_SRC}/hudson/WebAppMain.java",
    "FilePath.java":         f"{JAVA_SRC}/hudson/FilePath.java",
    "CLICommand.java":       f"{JAVA_SRC}/hudson/cli/CLICommand.java",
    "BuildCommand.java":     f"{JAVA_SRC}/hudson/cli/BuildCommand.java",
    "CrontabParser.java":    f"{JAVA_SRC}/antlr/crontab/CrontabParser.java",
    "ExtensionComponent.java": f"{JAVA_SRC}/hudson/ExtensionComponent.java",
    "PluginWrapper.java":    f"{JAVA_SRC}/hudson/PluginWrapper.java",
    "Functions.java":        f"{JAVA_SRC}/hudson/Functions.java",
    "MarkupText.java":       f"{JAVA_SRC}/hudson/MarkupText.java",
    "AbstractBuild.java":    f"{JAVA_SRC}/hudson/model/AbstractBuild.java",
    "Computer.java":         f"{JAVA_SRC}/hudson/model/Computer.java",
    "Descriptor.java":       f"{JAVA_SRC}/hudson/model/Descriptor.java",
    "UpdateSite.java":       f"{JAVA_SRC}/hudson/model/UpdateSite.java",
}

ACTIONABLE = {"REMOVE", "REFACTOR"}


def _is_correct(predicted: str, ground_truth: str, is_safe: bool) -> bool:
    predicted = (predicted or "").upper().split()[0]  # normalize
    # false positive: recommended REMOVE on something that shouldn't be removed
    if not is_safe and predicted in ACTIONABLE:
        return False
    # for safe candidates: accept REMOVE or REFACTOR as both correct
    if is_safe and predicted in ACTIONABLE:
        return True
    # KEEP/DEPRECATE are always acceptable for unsafe candidates
    if not is_safe and predicted not in ACTIONABLE:
        return True
    # exact match fallback
    return predicted == ground_truth.upper()


def run_evaluation(max_candidates: int = 20):
    candidates_to_run = GROUND_TRUTH[:max_candidates]
    results = []

    print(f"\n{'='*60}")
    print(f"EVALUATION RUN — {len(candidates_to_run)} candidates")
    print(f"{'='*60}\n")

    for i, (fname, line, rule, gt_action, is_safe) in enumerate(candidates_to_run, 1):
        file_rel = FILE_MAP.get(fname, "")
        # find CrontabParser dynamically
        if fname == "CrontabParser.java":
            file_rel = "core/target/generated-sources/antlr4/hudson/scheduler/CrontabParser.java"

        candidate = {
            "file": file_rel,
            "line": line,
            "rule": rule,
            "category": "DEAD_CODE" if "Unused" in rule else "STYLE",
            "severity": "MEDIUM",
            "description": f"Static analysis: {rule} at line {line} in {fname}",
            "source": "pmd" if "Unused" in rule else "spotbugs",
            "score": 2.4,
        }

        print(f"[{i:2d}/{len(candidates_to_run)}] {fname}:{line} — {rule}")
        print(f"       Ground truth: {gt_action} | safe_to_remove={is_safe}")

        overridden, reason = is_overridden(candidate["file"])
        if overridden:
            print(f"       SKIPPED (override): {reason}\n")
            results.append({"fname": fname, "line": line, "rule": rule,
                            "gt": gt_action, "predicted": "SKIP", "correct": None,
                            "confidence": 0, "escalated": False, "skipped": True})
            continue

        try:
            retrieval_verdict = retrieve_context(candidate)
            branches = generate_branches(candidate, retrieval_verdict)
            critic_result = run_critic(candidate, branches, retrieval_verdict)
            winner = critic_result.get("winner", {"action": "KEEP", "overall_score": 0.5})
            rec = recommend(candidate, retrieval_verdict, winner)

            predicted = str(rec.get("action", winner.get("action", "KEEP")))
            confidence = float(rec.get("confidence", 0.5))
            escalated = bool(rec.get("escalate", False))
            correct = _is_correct(predicted, gt_action, is_safe)

            save_decision(file=candidate["file"], rule=rule, action=predicted,
                         confidence=confidence, verdict=retrieval_verdict["verdict"],
                         rationale=str(rec.get("rationale", ""))[:200], escalated=escalated)

            status = "✓" if correct else "✗"
            print(f"       Predicted: {predicted} (conf={confidence}) | {status} | escalate={escalated}\n")

            results.append({
                "fname": fname, "line": line, "rule": rule,
                "gt": gt_action, "is_safe": is_safe,
                "predicted": predicted, "correct": correct,
                "confidence": confidence, "escalated": escalated,
                "verdict": retrieval_verdict["verdict"],
                "diff": rec.get("suggested_diff", ""),
            })

        except Exception as e:
            print(f"       ERROR: {e}\n")
            results.append({"fname": fname, "line": line, "rule": rule,
                            "gt": gt_action, "predicted": "ERROR", "correct": False,
                            "confidence": 0, "escalated": False})

    _print_metrics(results)

    with open(OUTPUT_DIR / "evaluation_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nFull results saved to output/evaluation_results.json")
    return results


def _print_metrics(results: list[dict]):
    valid = [r for r in results if r.get("predicted") not in ("ERROR", "SKIP")]
    total = len(valid)
    if not total:
        return

    correct = [r for r in valid if r.get("correct")]
    false_positives = [r for r in valid if not r.get("is_safe", True)
                       and str(r.get("predicted","")).upper().split()[0] in ACTIONABLE]
    escalated = [r for r in valid if r.get("escalated")]
    confidences = [r["confidence"] for r in valid]

    # calibration: avg confidence when correct vs incorrect
    conf_correct = [r["confidence"] for r in valid if r.get("correct")]
    conf_wrong   = [r["confidence"] for r in valid if not r.get("correct")]

    print(f"\n{'='*60}")
    print("EVALUATION METRICS")
    print(f"{'='*60}")
    print(f"  Total evaluated:          {total}")
    print(f"  Accuracy:                 {len(correct)}/{total} = {100*len(correct)//total}%")
    print(f"  False-positive rate:      {len(false_positives)}/{total} = {100*len(false_positives)//total}%")
    print(f"  Escalation rate:          {len(escalated)}/{total} = {100*len(escalated)//total}%")
    print(f"  Avg confidence (all):     {sum(confidences)/len(confidences):.2f}")
    if conf_correct:
        print(f"  Avg confidence (correct): {sum(conf_correct)/len(conf_correct):.2f}")
    if conf_wrong:
        print(f"  Avg confidence (wrong):   {sum(conf_wrong)/len(conf_wrong):.2f}")

    print(f"\n  Per-candidate results:")
    for r in valid:
        status = "✓" if r.get("correct") else "✗"
        fp = " [FALSE POSITIVE]" if r in false_positives else ""
        print(f"    {status} {r['fname']}:{r['line']} → {r['predicted']} "
              f"(gt={r['gt']}, conf={r['confidence']}){fp}")


if __name__ == "__main__":
    run_evaluation(max_candidates=20)
