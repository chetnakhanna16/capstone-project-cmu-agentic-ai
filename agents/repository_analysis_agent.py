import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from crewai import Agent, Task, Crew, LLM
from crewai.tools import tool
from tools.static_analysis import get_ranked_candidates, run_jdeps, Candidate

load_dotenv(Path(__file__).parent.parent / ".env")


def _get_llm() -> LLM:
    if os.getenv("ANTHROPIC_API_KEY"):
        return LLM(model="claude-sonnet-4-6", api_key=os.getenv("ANTHROPIC_API_KEY"))
    if os.getenv("OPENAI_API_KEY"):
        return LLM(model="gpt-4o", api_key=os.getenv("OPENAI_API_KEY"))
    raise EnvironmentError("Set either ANTHROPIC_API_KEY or OPENAI_API_KEY before running.")


@tool("run_static_analysis")
def run_static_analysis_tool(module: str) -> str:
    """
    Runs PMD and SpotBugs on the given Jenkins module and returns a ranked
    list of cleanup candidates (dead code, unused fields, bad practices).
    Input: module name (e.g. 'core'). Returns JSON list of candidates.
    """
    candidates = get_ranked_candidates(module)
    return json.dumps([
        {
            "file": c.file,
            "line": c.line,
            "rule": c.rule,
            "category": c.category,
            "severity": c.severity,
            "description": c.description,
            "source": c.source,
            "score": c.score,
        }
        for c in candidates[:50]  # top 50 by score
    ], indent=2)


@tool("run_dependency_analysis")
def run_dependency_analysis_tool(module: str) -> str:
    """
    Runs jdeps on the given Jenkins module to produce a module-level
    dependency summary. Useful for understanding blast radius before cleanup.
    Input: module name (e.g. 'core'). Returns JSON dependency map.
    """
    deps = run_jdeps(module)
    return json.dumps(deps, indent=2)


def build_repository_analysis_agent() -> Agent:
    return Agent(
        llm=_get_llm(),
        role="Repository Analysis Agent",
        goal=(
            "Scan a Jenkins module using static analysis tools, identify the top "
            "cleanup candidates (dead code, unused fields, unreachable code, bad "
            "practices), and produce a prioritized report with file locations, "
            "severity scores, and a short rationale for each finding."
        ),
        backstory=(
            "You are an expert Java static analysis engineer. You use PMD and "
            "SpotBugs to find technical debt in large enterprise codebases. "
            "You never recommend removing code based on a single signal — you "
            "always cross-reference findings and flag anything that might be "
            "dynamically loaded or part of an extension point."
        ),
        tools=[run_static_analysis_tool, run_dependency_analysis_tool],
        verbose=True,
    )


def build_analysis_task(agent: Agent, module: str) -> Task:
    return Task(
        description=(
            f"Analyze the Jenkins '{module}' module for technical debt. "
            "Steps:\n"
            "1. Run static analysis on the module to get ranked cleanup candidates.\n"
            "2. Run dependency analysis to understand the module's dependencies.\n"
            "3. From the candidates, identify the top 10 highest-confidence cleanup "
            "opportunities. Exclude anything that looks like it could be a plugin "
            "extension point or dynamically loaded class.\n"
            "4. For each of the top 10, provide: file path, line number, rule triggered, "
            "severity, and a one-sentence rationale."
        ),
        expected_output=(
            "A structured report listing the top 10 cleanup candidates, each with: "
            "file, line, rule, severity (HIGH/MEDIUM/LOW), and rationale. "
            "Followed by a one-paragraph summary of the module's overall technical debt profile."
        ),
        agent=agent,
    )


def run(module: str = "core") -> str:
    agent = build_repository_analysis_agent()
    task = build_analysis_task(agent, module)
    crew = Crew(agents=[agent], tasks=[task], verbose=True)
    result = crew.kickoff()
    return str(result)


if __name__ == "__main__":
    output = run("core")
    print("\n" + "=" * 60)
    print("REPOSITORY ANALYSIS REPORT")
    print("=" * 60)
    print(output)
