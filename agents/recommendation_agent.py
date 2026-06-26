import json
import re
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from crewai import Agent, Task, Crew, LLM
from tools.diff_generator import generate_diff

load_dotenv(Path(__file__).parent.parent / ".env")

JENKINS_ROOT = Path(__file__).parent.parent / "jenkins"
ESCALATION_CONFIDENCE_THRESHOLD = 0.6


def _get_llm() -> LLM:
    if os.getenv("ANTHROPIC_API_KEY"):
        return LLM(model="claude-sonnet-4-6", api_key=os.getenv("ANTHROPIC_API_KEY"))
    if os.getenv("OPENAI_API_KEY"):
        return LLM(model="gpt-4o", api_key=os.getenv("OPENAI_API_KEY"))
    raise EnvironmentError("Set either ANTHROPIC_API_KEY or OPENAI_API_KEY.")


def _compute_confidence(winner: dict, retrieval_verdict: dict) -> float:
    score = winner.get("overall_score", 0.5)

    verdict_adjustment = {"SAFE": 0.1, "UNCERTAIN": -0.1, "RISKY": -0.25}
    confidence_adjustment = {"HIGH": 0.05, "MEDIUM": 0.0, "LOW": -0.1}

    score += verdict_adjustment.get(retrieval_verdict.get("verdict", "UNCERTAIN"), 0)
    score += confidence_adjustment.get(retrieval_verdict.get("confidence", "LOW"), 0)

    # REMOVE is always high-stakes — cap confidence
    if winner.get("action") == "REMOVE":
        score = min(score, 0.65)

    return round(max(0.0, min(1.0, score)), 2)


def _should_escalate(confidence: float, winner: dict, retrieval_verdict: dict) -> tuple[bool, str]:
    if retrieval_verdict.get("verdict") == "RISKY":
        return True, "RAG verdict is RISKY — candidate touches plugin infrastructure"
    if winner.get("action") == "REMOVE":
        return True, "REMOVE actions always require human review"
    if retrieval_verdict.get("verdict") == "UNCERTAIN" and retrieval_verdict.get("confidence") == "LOW":
        return True, "Insufficient evidence — conflicting or missing documentation"
    if confidence < ESCALATION_CONFIDENCE_THRESHOLD:
        return True, f"Confidence {confidence} is below threshold {ESCALATION_CONFIDENCE_THRESHOLD}"
    return False, ""


def _read_source_lines(file_rel: str, line: int, context: int = 8) -> str:
    full_path = JENKINS_ROOT / file_rel
    if not full_path.exists():
        return ""
    lines = full_path.read_text(errors="ignore").splitlines()
    start = max(0, line - context - 1)
    end = min(len(lines), line + context)
    numbered = [f"{i+1:4d}  {l}" for i, l in enumerate(lines[start:end], start=start)]
    return "\n".join(numbered)


def build_recommendation_agent() -> Agent:
    return Agent(
        role="Recommendation Agent",
        goal=(
            "Synthesize static analysis findings, RAG context, and the Critic's "
            "winning action branch into a final, human-readable cleanup recommendation. "
            "Produce a confidence score, a clear rationale grounded in evidence, "
            "a suggested code diff, and an escalation flag when the recommendation "
            "requires human review before acting."
        ),
        backstory=(
            "You are the final decision layer of a code modernization system. "
            "You communicate clearly to software engineers who need to act on your "
            "recommendations. You never recommend changes you cannot justify with "
            "evidence. When uncertain, you escalate rather than guess. "
            "Your output will be reviewed by a human before any code is touched."
        ),
        llm=_get_llm(),
        verbose=True,
    )


def build_recommendation_task(
    agent: Agent,
    candidate: dict,
    retrieval_verdict: dict,
    winner: dict,
    confidence: float,
    escalate: bool,
    escalation_reason: str,
    source_context: str,
) -> Task:
    return Task(
        description=(
            "Produce the final recommendation for this cleanup candidate.\n\n"
            f"CANDIDATE:\n"
            f"  File: {candidate['file']}\n"
            f"  Line: {candidate['line']}\n"
            f"  Rule: {candidate['rule']}\n"
            f"  Severity: {candidate['severity']}\n"
            f"  Description: {candidate['description']}\n\n"
            f"RAG VERDICT: {retrieval_verdict['verdict']} "
            f"(confidence: {retrieval_verdict['confidence']})\n"
            f"  {retrieval_verdict['reasoning']}\n\n"
            f"CRITIC WINNER:\n"
            f"  Action: {winner.get('action')}\n"
            f"  Description: {winner.get('description')}\n"
            f"  Critic note: {winner.get('critic_note', '')}\n"
            f"  Revised safety: {winner.get('revised_safety')}\n\n"
            f"COMPUTED CONFIDENCE: {confidence}\n"
            f"ESCALATE: {escalate}"
            + (f"\nESCALATION REASON: {escalation_reason}" if escalate else "") + "\n\n"
            f"SOURCE CODE CONTEXT (around line {candidate['line']}):\n"
            f"{source_context}\n\n"
            "Produce a final recommendation with:\n"
            "1. A one-sentence action statement (what to do and where)\n"
            "2. A rationale paragraph (2-3 sentences) citing both the static analysis "
            "finding and the RAG evidence\n"
            "3. A suggested diff showing the specific code change (if action is "
            "REFACTOR or REMOVE); or a clear explanation of what to add/change\n"
            "4. The escalation status and reason (if escalating)\n"
            "Return as structured JSON only."
        ),
        expected_output=(
            "JSON object with fields: action, confidence (float), "
            "action_statement (string), rationale (string), "
            "suggested_diff (string), escalate (bool), escalation_reason (string)"
        ),
        agent=agent,
    )


def run(candidate: dict, retrieval_verdict: dict, winner: dict) -> dict:
    confidence = _compute_confidence(winner, retrieval_verdict)
    escalate, escalation_reason = _should_escalate(confidence, winner, retrieval_verdict)
    source_context = _read_source_lines(candidate["file"], candidate["line"])

    # generate diff programmatically for mechanical actions — don't ask the LLM
    action = winner.get("action", "KEEP")
    programmatic_diff = generate_diff(
        candidate["file"], candidate["line"], action, candidate.get("rule", "")
    )

    agent = build_recommendation_agent()
    task = build_recommendation_task(
        agent, candidate, retrieval_verdict, winner,
        confidence, escalate, escalation_reason, source_context,
    )
    crew = Crew(agents=[agent], tasks=[task], verbose=False)
    raw = str(crew.kickoff())

    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if match:
        result = json.loads(match.group())
    else:
        result = {"action": action, "rationale": raw}

    result["confidence"] = confidence
    result["escalate"] = escalate
    result["escalation_reason"] = escalation_reason
    # override LLM diff with programmatic one when available
    if programmatic_diff and programmatic_diff != "(no change)":
        result["suggested_diff"] = programmatic_diff

    return result
