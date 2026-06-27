import json
import sys
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from tools.static_analysis import get_ranked_candidates, run_jdeps
from tools.memory_client import save_decision, recall_decision, is_overridden, get_rule_pattern, get_summary
from agents.context_retrieval_agent import run as retrieve_context
from agents.thought_generator_agent import run as generate_branches
from agents.critic_agent import run as run_critic
from agents.recommendation_agent import run as recommend

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)


def _candidate_to_dict(c) -> dict:
    return {
        "file": c.file,
        "line": c.line,
        "rule": c.rule,
        "category": c.category,
        "severity": c.severity,
        "description": c.description,
        "source": c.source,
        "score": c.score,
    }


_DEAD_CODE_RULES = {
    "UnusedPrivateField", "UnusedLocalVariable",
    "UnusedPrivateMethod", "UnusedFormalParameter", "UnnecessaryImport",
}


def run_pipeline(module: str = "core", max_candidates: int = 5, mode: str = "diverse",
                 src_subpath: str = "src/main/java") -> list[dict]:
    print(f"\n{'='*60}")
    print(f"PIPELINE START — module: {module}, src: {src_subpath}, candidates: {max_candidates}, mode: {mode}")
    print(f"{'='*60}\n")

    # Phase 2: get ranked candidates
    print("[Phase 2] Running static analysis...")
    all_candidates = get_ranked_candidates(module, src_subpath)

    if mode == "dead-code":
        pool = [c for c in all_candidates if c.rule in _DEAD_CODE_RULES]
    else:
        pool = all_candidates

    # pick a diverse set — one per unique rule
    seen_rules = set()
    selected = []
    for c in pool:
        if c.rule not in seen_rules:
            selected.append(c)
            seen_rules.add(c.rule)
        if len(selected) >= max_candidates:
            break

    print(f"  Selected {len(selected)} diverse candidates\n")

    results = []
    for i, c in enumerate(selected, 1):
        candidate = _candidate_to_dict(c)
        print(f"\n{'─'*60}")
        print(f"Candidate {i}/{len(selected)}: {c.rule} in {Path(c.file).name}:{c.line}")
        print(f"{'─'*60}")

        try:
            # Phase 6 — PRE-CHECK: skip if overridden or already decided
            overridden, override_reason = is_overridden(candidate["file"])
            if overridden:
                print(f"  SKIPPED (developer override): {override_reason}")
                results.append({"candidate": candidate, "skipped": True, "reason": override_reason})
                continue

            past = recall_decision(file=candidate["file"], rule=candidate["rule"])
            if past:
                last = past[-1]
                print(f"  MEMORY HIT — previously decided: {last['action']} "
                      f"(confidence: {last['confidence']}, {last['timestamp'][:10]})")

            # hint: if this rule is historically RISKY, note it
            pattern = get_rule_pattern(candidate["rule"])
            if pattern.get("total", 0) >= 2:
                dominant = max(("SAFE", "RISKY", "UNCERTAIN"), key=lambda k: pattern.get(k, 0))
                print(f"  PATTERN HINT — rule '{candidate['rule']}' is historically {dominant} "
                      f"({pattern.get(dominant,0)}/{pattern.get('total',0)} cases)")

            # Phase 3: context retrieval
            print("[Phase 3] Retrieving context...")
            retrieval_verdict = retrieve_context(candidate)
            print(f"  Verdict: {retrieval_verdict['verdict']} ({retrieval_verdict['confidence']})")

            # Phase 4a: thought generator
            print("[Phase 4a] Generating branches...")
            branches = generate_branches(candidate, retrieval_verdict)
            print(f"  Generated {len(branches)} branches")

            # Phase 4b: critic
            print("[Phase 4b] Running critic...")
            critic_result = run_critic(candidate, branches, retrieval_verdict)
            winner = critic_result.get("winner", {"action": "KEEP", "description": "Defaulted to KEEP", "overall_score": 0.5})
            print(f"  Winner: {winner.get('action')}")

            # Phase 5: recommendation
            print("[Phase 5] Generating recommendation...")
            recommendation = recommend(candidate, retrieval_verdict, winner)

            result = {
                "candidate": candidate,
                "retrieval_verdict": retrieval_verdict,
                "branches": branches,
                "critic_winner": winner,
                "recommendation": recommendation,
            }
            results.append(result)

            # Phase 6 — POST-RUN: save decision to long-term memory
            rec = recommendation
            save_decision(
                file=candidate["file"],
                rule=candidate["rule"],
                action=str(rec.get("action", winner.get("action", "UNKNOWN"))),
                confidence=float(rec.get("confidence", 0.5)),
                verdict=retrieval_verdict["verdict"],
                rationale=str(rec.get("rationale", ""))[:200],
                escalated=bool(rec.get("escalate", False)),
            )

            escalated = rec.get("escalate", False)
            print(f"\n  ACTION:     {rec.get('action', winner.get('action'))}")
            print(f"  CONFIDENCE: {rec.get('confidence')}")
            print(f"  ESCALATE:   {'YES — ' + rec.get('escalation_reason','') if escalated else 'No'}")
            print(f"  RATIONALE:  {str(rec.get('rationale',''))[:120]}...")
            print(f"  MEMORY:     Decision saved to long-term store")

        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({"candidate": candidate, "error": str(e)})

    # save full results
    out_file = OUTPUT_DIR / f"pipeline_results_{module}.json"
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n\nFull results saved to: {out_file}")

    # print evaluation summary
    _print_evaluation_summary(results)
    return results


def _print_evaluation_summary(results: list[dict]):
    print(f"\n{'='*60}")
    print("EVALUATION SUMMARY")
    print(f"{'='*60}")

    total = len([r for r in results if "error" not in r])
    escalated = sum(1 for r in results if r.get("recommendation", {}).get("escalate"))
    actions = {}
    confidences = []

    for r in results:
        if "error" in r:
            continue
        action = r.get("recommendation", {}).get("action") or r.get("critic_winner", {}).get("action", "UNKNOWN")
        actions[action] = actions.get(action, 0) + 1
        conf = r.get("recommendation", {}).get("confidence")
        if conf:
            confidences.append(conf)

    print(f"  Total candidates processed: {total}")
    print(f"  Escalated for human review: {escalated} ({100*escalated//total if total else 0}%)")
    print(f"  Action distribution: {actions}")
    if confidences:
        print(f"  Avg confidence score: {sum(confidences)/len(confidences):.2f}")
        print(f"  Low confidence (<0.6): {sum(1 for c in confidences if c < 0.6)}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Enterprise Code Cleanup Agent Pipeline")
    parser.add_argument("--module", default="core", help="Jenkins module to analyze (default: core)")
    parser.add_argument("--candidates", type=int, default=5, help="Max candidates to process (default: 5)")
    parser.add_argument("--mode", choices=["diverse", "dead-code"], default="diverse",
                        help="diverse: one candidate per rule; dead-code: focus on unused fields/vars/methods")
    parser.add_argument("--src-subpath", default="src/main/java",
                        help="Source subdirectory to scan (default: src/main/java; use src/test/java for test modules)")
    args = parser.parse_args()
    run_pipeline(module=args.module, max_candidates=args.candidates, mode=args.mode,
                 src_subpath=args.src_subpath)
