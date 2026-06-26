import json
import re
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from crewai import Agent, Task, Crew, LLM

load_dotenv(Path(__file__).parent.parent / ".env")


def _get_llm() -> LLM:
    # Critic uses o3-mini — best at complex conditional reasoning
    if os.getenv("OPENAI_API_KEY"):
        return LLM(model="o3-mini", api_key=os.getenv("OPENAI_API_KEY"))
    if os.getenv("ANTHROPIC_API_KEY"):
        return LLM(model="claude-sonnet-4-6", api_key=os.getenv("ANTHROPIC_API_KEY"))
    raise EnvironmentError("Set either OPENAI_API_KEY or ANTHROPIC_API_KEY.")


def build_critic_agent() -> Agent:
    return Agent(
        role="Critic Agent",
        goal=(
            "Review action branches proposed by the Thought Generator. "
            "Challenge every assumption, penalize branches that underestimate risk, "
            "and eliminate any branch that could cause a production incident. "
            "Return a revised scoring with a PRUNED or APPROVED status for each branch, "
            "and select the single strongest surviving branch."
        ),
        backstory=(
            "You are a calibrated senior engineer whose job is to prevent bad cleanup "
            "decisions from reaching production. Your skepticism is proportional to risk: "
            "when the RAG verdict is RISKY or UNCERTAIN you are very conservative and "
            "prefer KEEP or DEPRECATE over REMOVE. But when the RAG verdict is SAFE with "
            "HIGH confidence — especially for pure dead code rules like UnusedPrivateField, "
            "UnusedLocalVariable, or UnusedPrivateMethod — you trust that signal and "
            "support REMOVE or REFACTOR. Private and local code cannot be accessed "
            "externally, so removal risk is genuinely low. Do not manufacture risk where "
            "the evidence says there is none."
        ),
        llm=_get_llm(),
        verbose=True,
    )


_DEAD_CODE_RULES = {
    "UnusedPrivateField", "UnusedLocalVariable",
    "UnusedPrivateMethod", "UnusedFormalParameter", "UnnecessaryImport",
}


def build_critic_task(agent: Agent, candidate: dict, branches: list[dict], retrieval_verdict: dict) -> Task:
    rule = candidate.get("rule", "")
    verdict = retrieval_verdict.get("verdict", "UNCERTAIN")
    rag_confidence = retrieval_verdict.get("confidence", "LOW")

    is_dead_code = rule in _DEAD_CODE_RULES
    is_safe_signal = verdict == "SAFE" and rag_confidence == "HIGH"

    if is_dead_code and is_safe_signal:
        pruning_rule = (
            "IMPORTANT: The RAG verdict is SAFE with HIGH confidence and the rule is "
            f"'{rule}' — a pure dead code rule where the symbol is private/local by "
            "definition and cannot be externally referenced. In this case:\n"
            "  - REMOVE and REFACTOR branches should receive high safety scores (>= 0.7)\n"
            "  - Only PRUNE a REMOVE branch if you find specific evidence it IS referenced\n"
            "  - Do NOT manufacture risk where none exists\n"
            "  - Pruning threshold: safety < 0.3 (not 0.5)"
        )
    else:
        pruning_rule = (
            f"The RAG verdict is {verdict} (confidence: {rag_confidence}). "
            "Apply standard skepticism: PRUNE any branch with revised safety < 0.5 "
            "or that contradicts the retrieval verdict."
        )

    return Task(
        description=(
            "Review these action branches for a cleanup candidate. "
            "Your job is to challenge assumptions and prune risky branches.\n\n"
            f"CANDIDATE:\n"
            f"  File: {candidate['file']}\n"
            f"  Rule: {candidate['rule']}\n"
            f"  Description: {candidate['description']}\n\n"
            f"RETRIEVAL VERDICT: {verdict} (confidence: {rag_confidence})\n"
            f"  {retrieval_verdict.get('reasoning', '')}\n\n"
            f"{pruning_rule}\n\n"
            f"BRANCHES TO REVIEW:\n{json.dumps(branches, indent=2)}\n\n"
            "For each branch:\n"
            "1. Identify any real flaw or hidden risk (skip this for dead code with SAFE verdict)\n"
            "2. Re-score safety based on the pruning rule above\n"
            "3. Set status to PRUNED or APPROVED per the pruning rule\n"
            "4. From the APPROVED branches, select the strongest one as the WINNER\n"
            "5. If ALL branches are pruned, the winner is automatically KEEP\n\n"
            "Return ONLY valid JSON — no explanation outside the JSON block."
        ),
        expected_output=(
            "A JSON object with two fields:\n"
            "- reviewed_branches: list of branches each with added fields: "
            "status (PRUNED|APPROVED), critic_note (string), revised_safety (float)\n"
            "- winner: the single best branch object (with status APPROVED), "
            "or a KEEP branch if all were pruned\n"
            "Example: {\"reviewed_branches\": [{...}], \"winner\": {...}}"
        ),
        agent=agent,
    )


def run(candidate: dict, branches: list[dict], retrieval_verdict: dict) -> dict:
    agent = build_critic_agent()
    task = build_critic_task(agent, candidate, branches, retrieval_verdict)
    crew = Crew(agents=[agent], tasks=[task], verbose=True)
    raw = str(crew.kickoff())

    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        return json.loads(match.group())
    return {"reviewed_branches": branches, "winner": {"action": "KEEP", "description": "Critic could not parse branches — defaulting to KEEP"}}


if __name__ == "__main__":
    from agents.thought_generator_agent import run as generate_branches

    candidate = {
        "file": "core/src/main/java/hudson/cli/BuildCommand.java",
        "line": 204,
        "rule": "NP_NULL_ON_SOME_PATH_FROM_RETURN_VALUE",
        "severity": "HIGH",
        "description": "Possible null pointer dereference in hudson.cli.BuildCommand.run()",
    }
    retrieval_verdict = {
        "verdict": "SAFE",
        "confidence": "MEDIUM",
        "reasoning": "BuildCommand is a CLI command handler with no dynamic loading. "
                     "The null dereference is in a return value from a standard API call.",
    }

    print("Generating branches...")
    branches = generate_branches(candidate, retrieval_verdict)

    print("\nRunning Critic...")
    result = run(candidate, branches, retrieval_verdict)

    print("\n" + "=" * 60)
    print("CRITIC VERDICT")
    print("=" * 60)
    for b in result.get("reviewed_branches", []):
        status = b.get("status", "?")
        print(f"  [{status}] {b['action']} — {b.get('critic_note', '')}")
    winner = result.get("winner", {})
    print(f"\nWINNER: {winner.get('action')} — {winner.get('description', '')}")
