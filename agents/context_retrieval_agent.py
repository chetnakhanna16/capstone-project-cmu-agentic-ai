import json
import re
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from crewai import Agent, Task, Crew, LLM
from crewai.tools import tool
from tools.doc_indexer import retrieve

load_dotenv(Path(__file__).parent.parent / ".env")


def _get_llm() -> LLM:
    if os.getenv("ANTHROPIC_API_KEY"):
        return LLM(model="claude-sonnet-4-6", api_key=os.getenv("ANTHROPIC_API_KEY"))
    if os.getenv("OPENAI_API_KEY"):
        return LLM(model="gpt-4o", api_key=os.getenv("OPENAI_API_KEY"))
    raise EnvironmentError("Set either ANTHROPIC_API_KEY or OPENAI_API_KEY.")


@tool("retrieve_context")
def retrieve_context_tool(query: str) -> str:
    """
    Searches the Jenkins documentation index (Javadoc, CONTRIBUTING.md, package-info,
    pom.xml dependency manifests) for context relevant to a cleanup candidate.
    Use this to check if a class/method is a plugin extension point, dynamically
    loaded, or otherwise unsafe to remove.
    Input: a natural language query describing the candidate. Returns top 5 matching docs.
    """
    results = retrieve(query, n_results=5)
    return json.dumps(results, indent=2)


def build_context_retrieval_agent() -> Agent:
    return Agent(
        role="Context Retrieval Agent",
        goal=(
            "Given a cleanup candidate (file, rule, description) from static analysis, "
            "retrieve the most relevant documentation from the Jenkins knowledge base. "
            "Determine whether the candidate is safe to clean up or if it is a plugin "
            "extension point, dynamically loaded class, or otherwise risky to touch."
        ),
        backstory=(
            "You are a Jenkins architecture expert who deeply understands how Jenkins "
            "plugins work, how extension points are dynamically loaded at runtime, and "
            "which parts of the codebase are safe to modify vs. which are load-bearing "
            "infrastructure. You use documentation to verify assumptions before any "
            "cleanup recommendation is made."
        ),
        tools=[retrieve_context_tool],
        verbose=True,
        llm=_get_llm(),
    )


def build_retrieval_task(agent: Agent, candidate: dict) -> Task:
    return Task(
        description=(
            f"Evaluate this cleanup candidate from static analysis:\n"
            f"  File: {candidate['file']}\n"
            f"  Line: {candidate['line']}\n"
            f"  Rule: {candidate['rule']}\n"
            f"  Description: {candidate['description']}\n\n"
            "Steps:\n"
            "1. Search the Jenkins knowledge base for documentation about this file, "
            "class, or pattern (use 2-3 targeted queries).\n"
            "2. Determine if this candidate is:\n"
            "   a) SAFE — no documentation suggests it is load-bearing or dynamic\n"
            "   b) RISKY — it is an extension point, plugin hook, or dynamically loaded\n"
            "   c) UNCERTAIN — conflicting or insufficient evidence\n"
            "3. Return your verdict with the supporting evidence from the retrieved docs."
        ),
        expected_output=(
            "A structured verdict with:\n"
            "- verdict: SAFE | RISKY | UNCERTAIN\n"
            "- confidence: HIGH | MEDIUM | LOW\n"
            "- evidence: list of retrieved doc excerpts that support the verdict\n"
            "- reasoning: 2-3 sentences explaining the verdict"
        ),
        agent=agent,
    )


_DEAD_CODE_RULES = {
    "UnusedPrivateField", "UnusedLocalVariable",
    "UnusedPrivateMethod", "UnusedFormalParameter", "UnnecessaryImport",
}


def run(candidate: dict) -> dict:
    # pure dead code rules are definitionally SAFE — private/local by definition
    # can't be accessed externally, no doc evidence needed
    if candidate.get("rule") in _DEAD_CODE_RULES:
        return {
            "verdict": "SAFE",
            "confidence": "HIGH",
            "reasoning": (
                f"{candidate['rule']} flags code that is private or local by definition. "
                "It cannot be referenced outside its declaring class, so removal is safe "
                "without requiring documentation evidence."
            ),
        }

    agent = build_context_retrieval_agent()
    task = build_retrieval_task(agent, candidate)
    crew = Crew(agents=[agent], tasks=[task], verbose=False)
    raw = str(crew.kickoff())

    # case-insensitive match for **Verdict:** SAFE or "verdict": "SAFE"
    verdict_match = re.search(
        r'\*\*[Vv]erdict\*\*[:\s]+([A-Z]+)|"verdict"[:\s]+"?([A-Z]+)', raw)
    confidence_match = re.search(
        r'\*\*[Cc]onfidence\*\*[:\s]+([A-Z]+)|"confidence"[:\s]+"?([A-Z]+)', raw)
    reasoning_match = re.search(
        r'\*\*[Rr]easoning\*\*[:\s]+(.*?)(?:\n\n|\Z)', raw, re.DOTALL)

    verdict = next((g for g in (verdict_match.groups() if verdict_match else []) if g), "UNCERTAIN")
    confidence = next((g for g in (confidence_match.groups() if confidence_match else []) if g), "LOW")
    reasoning = reasoning_match.group(1).strip()[:300] if reasoning_match else raw[:300]

    return {"verdict": verdict, "confidence": confidence, "reasoning": reasoning}


if __name__ == "__main__":
    # Test with ExtensionComponent — the highest-risk candidate from Phase 2
    test_candidate = {
        "file": "core/src/main/java/hudson/ExtensionComponent.java",
        "line": 42,
        "rule": "EQ_COMPARETO_USE_OBJECT_EQUALS",
        "description": "hudson.ExtensionComponent defines compareTo(ExtensionComponent) and uses Object.equals()",
    }
    result = run(test_candidate)
    print("\n" + "=" * 60)
    print("CONTEXT RETRIEVAL VERDICT")
    print("=" * 60)
    print(result)
