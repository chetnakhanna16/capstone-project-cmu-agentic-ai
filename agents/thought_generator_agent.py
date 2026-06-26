import json
import re
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from crewai import Agent, Task, Crew, LLM
from crewai.tools import tool

load_dotenv(Path(__file__).parent.parent / ".env")

# Valid actions the agent can propose (ToT branches)
ACTIONS = ["REMOVE", "REFACTOR", "DEPRECATE", "KEEP"]


def _get_llm() -> LLM:
    if os.getenv("ANTHROPIC_API_KEY"):
        return LLM(model="claude-sonnet-4-6", api_key=os.getenv("ANTHROPIC_API_KEY"))
    if os.getenv("OPENAI_API_KEY"):
        return LLM(model="gpt-4o", api_key=os.getenv("OPENAI_API_KEY"))
    raise EnvironmentError("Set either ANTHROPIC_API_KEY or OPENAI_API_KEY.")


def build_thought_generator_agent() -> Agent:
    return Agent(
        role="Thought Generator Agent",
        goal=(
            "Given a cleanup candidate and its retrieved context, generate 3 to 5 "
            "distinct action branches using Tree-of-Thoughts reasoning. Each branch "
            "must be one of: REMOVE, REFACTOR, DEPRECATE, or KEEP. Score each branch "
            "on feasibility, safety risk, and impact. The top branches will be passed "
            "to the Critic Agent for pruning."
        ),
        backstory=(
            "You are a senior Java architect who specializes in safely modernizing "
            "large enterprise codebases. You think in multiple parallel strategies "
            "before committing to one. You never recommend removing code without "
            "considering the blast radius, and you always weigh the cost of action "
            "against the cost of leaving technical debt in place."
        ),
        llm=_get_llm(),
        verbose=True,
    )


def build_thought_generator_task(agent: Agent, candidate: dict, retrieval_verdict: dict) -> Task:
    return Task(
        description=(
            "You are applying Tree-of-Thoughts reasoning to this cleanup candidate.\n\n"
            f"CANDIDATE:\n"
            f"  File: {candidate['file']}\n"
            f"  Line: {candidate['line']}\n"
            f"  Rule: {candidate['rule']}\n"
            f"  Severity: {candidate['severity']}\n"
            f"  Description: {candidate['description']}\n\n"
            f"RETRIEVAL VERDICT (from RAG):\n"
            f"  Verdict: {retrieval_verdict['verdict']}\n"
            f"  Confidence: {retrieval_verdict['confidence']}\n"
            f"  Reasoning: {retrieval_verdict['reasoning']}\n\n"
            "Generate 3 to 5 action branches. For each branch:\n"
            "1. Choose an action: REMOVE, REFACTOR, DEPRECATE, or KEEP\n"
            "2. Write a concrete description of what the action entails for this specific candidate\n"
            "3. List assumptions the action relies on\n"
            "4. Score the branch on three dimensions (0.0 to 1.0):\n"
            "   - feasibility: how easy is this to implement safely?\n"
            "   - safety: how low is the risk of breaking something?\n"
            "   - impact: how much does this improve code quality?\n"
            "5. Compute an overall score: (feasibility + safety + impact) / 3\n\n"
            "Apply Beam Search: keep only the top 3 branches by overall score.\n"
            "Return ONLY valid JSON — no explanation outside the JSON block."
        ),
        expected_output=(
            "A JSON array of 3 branches, each with fields: "
            "action, description, assumptions (list), "
            "feasibility (float), safety (float), impact (float), overall_score (float). "
            "Example: [{\"action\": \"REFACTOR\", \"description\": \"...\", "
            "\"assumptions\": [\"...\"], \"feasibility\": 0.8, \"safety\": 0.9, "
            "\"impact\": 0.7, \"overall_score\": 0.8}]"
        ),
        agent=agent,
    )


def run(candidate: dict, retrieval_verdict: dict) -> list[dict]:
    agent = build_thought_generator_agent()
    task = build_thought_generator_task(agent, candidate, retrieval_verdict)
    crew = Crew(agents=[agent], tasks=[task], verbose=True)
    raw = str(crew.kickoff())

    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if match:
        return json.loads(match.group())
    return []


if __name__ == "__main__":
    # Test with a SAFE candidate (unused local variable — low risk)
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

    branches = run(candidate, retrieval_verdict)
    print("\n" + "=" * 60)
    print("THOUGHT GENERATOR — BRANCHES")
    print("=" * 60)
    for i, b in enumerate(branches, 1):
        print(f"\nBranch {i}: {b['action']} (score: {b['overall_score']})")
        print(f"  {b['description']}")
        print(f"  Assumptions: {b['assumptions']}")
        print(f"  Feasibility={b['feasibility']} Safety={b['safety']} Impact={b['impact']}")
