# CMU Capstone Project — Context

**Student:** Chetna Khanna  
**Course:** Agentic AI Program: Building Autonomous Systems for Real-World Applications (CMU)  
**Project:** Enterprise Code Cleanup and Modernization Agent  
**Category:** Research Agent  

---

## What the Agent Does

Helps software engineers and platform teams analyze large-scale enterprise codebases to identify technical debt and safely recommend cleanup or modernization actions. Technical debt types targeted: dead code, stale dependencies, duplicate logic, unreachable code, outdated configurations.

The agent does **not** directly modify code — it only produces recommendations and diffs for human review.

---

## Problem Statement

Enterprise systems accumulate technical debt over time, making them harder to maintain. A simple LLM/prompt-only approach is insufficient because:
- It cannot understand repository-wide dependencies
- It can hallucinate or misclassify active code as unused
- It cannot verify runtime behavior from static analysis alone
- It cannot handle multi-step validation across tools

---

## Architecture Overview (from Checkpoint 3.1)

```
Codebase
    ↓
Static Analysis Tool
    ↓
Potential Dead Code Candidates
    ↓
Retrieve Supporting Context (RAG)
    ├─ Architecture Docs
    ├─ Design Docs
    ├─ API Specs
    └─ Historical Cleanup Decisions
    ↓
Reasoning Agent (ReAct + ToT)
    ↓
Validation (Tests + Build)
    ↓
Recommendation (with confidence score)
```

---

## Reasoning Framework

**ReAct loop (Checkpoint 2.1):**
```
Reason → Act → Observe → Update Reasoning → Repeat
```

**Tree-of-Thoughts (Checkpoint 4.1)** applied at the decision-making stage only (not scanning/retrieval which are deterministic):
- Branches: remove / refactor / deprecate / leave unchanged
- Typically 3–5 branches per cleanup candidate
- **Search strategy: Beam Search** (not BFS — too many paths; not DFS — risks going deep on wrong path)
- Branches scored on: dependency impact, safety risk, validation results, confidence level
- Weak/risky branches pruned by Critic Agent

---

## Multi-Agent Architecture (5 Agents — Checkpoint 5.1)

| Agent | Role |
|-------|------|
| **Repository Analysis Agent** | Scans codebase, runs static analysis, identifies cleanup candidates |
| **Context Retrieval Agent** | RAG over engineering docs (arch docs, API specs, historical decisions) |
| **Thought Generator Agent** | ToT — generates possible actions (remove/refactor/deprecate/keep) |
| **Critic Agent** | Reviews each branch, challenges assumptions, eliminates risky options |
| **Recommendation Agent** | Selects strongest option, produces final recommendation with rationale |

**Coordination:** Hybrid
- Sequential: Analysis → Retrieval (each step depends on previous)
- Iterative loop: Thought Generator ↔ Critic Agent (refine before committing)
- Communication: mostly one-way; exception is the Thought Generator ↔ Critic feedback loop

---

## Memory Design (Checkpoint 2.1)

**Short-term (within a session):**
- Files currently being analyzed
- Intermediate reasoning steps
- Dependency relationships discovered
- Tool outputs (static analysis, code search)
- Validation results (test failures, build errors)

**Long-term (across sessions):**
- Historical cleanup decisions
- Common technical debt patterns per repo
- Developer override feedback (e.g., "don't touch dynamically loaded plugins")
- Previously flagged risky modules

---

## RAG / Retrieval Design (Checkpoint 3.1)

**Sources:** Architecture docs, API specs, dependency manifests, engineering documentation, historical cleanup reports

**Approach:**
- Semantic search via vector database (embeddings)
- Source code is NOT stored in vector DB — static analysis tools handle that directly
- Documents chunked by logical sections (headings/topics) before embedding
- Top 5–10 results retrieved per query

**Key insight:** Retrieval provides organizational context that code alone can't reveal. Example: a module with no static references might be dynamically loaded via config — retrieval finds the doc explaining this and prevents an unsafe deletion recommendation.

**Failure mode mitigation:** Retrieved context is combined with static analysis + code search. Conflicting or incomplete evidence → agent lowers confidence score and escalates for human review.

---

## Safety Guardrails (Checkpoint 6.1)

1. **Evidence requirement:** Every recommendation must be backed by both code analysis AND retrieved documentation — not LLM reasoning alone
2. **No direct code modification:** Agent only produces recommendations/diffs
3. **Critic Agent review:** Challenges assumptions before any final recommendation
4. **Auto-escalation:** Conflicting evidence or insufficient support → human review required

**Human intervention triggers:**
- Low confidence score
- Conflicting evidence between code analysis and docs
- Business-critical services
- Shared libraries (high blast radius)

**Trade-off:** Automate low-risk recommendations; escalate uncertain/high-impact ones. Full automation would risk costly mistakes; requiring approval for everything would kill productivity.

---

## Evaluation Metrics (Checkpoint 6.1)

- Recommendation accuracy
- Groundedness (is the recommendation backed by evidence?)
- Calibration (do confidence scores match actual reliability?)
- False-positive rate for code removal recommendations
- Escalation rate

---

## Technology Stack

- **CrewAI** — define specialized agent roles (Thought Generator, Critic, etc.)
- **LangChain** — orchestrate workflow, tool calls, branch evaluation
- **MCP (Model Context Protocol)** — shared memory/state layer across agents (stores branch states, retrieved context, evaluation results)
- **Vector Database** — semantic search over engineering docs
- **Static Analysis Tools** — AST inspection, dependency graphs, symbol references, import relationships
- **Test Execution Frameworks** — runtime validation after proposed cleanup

---

## Key Design Decisions & Rationale

| Decision | Why |
|----------|-----|
| ReAct over simple chain-of-thought | Allows iterative grounding in tool outputs rather than committing to one reasoning path |
| ToT only at decision stage | Scanning/retrieval are deterministic; ToT overhead only justified where multiple valid strategies exist |
| Beam Search over BFS/DFS | BFS too expensive; DFS risks deep exploration of wrong path; Beam keeps top-N promising branches |
| 5 specialized agents vs 1 monolithic | Different tasks require different reasoning modes; adds validation layer |
| RAG for docs, static analysis for code | Each tool suited to its data type; avoids bloating vector DB with source code |
| No direct code modification | Highest-risk guardrail; human always in the loop for actual changes |

---

## Checkpoint Summary

| Checkpoint | Focus |
|-----------|-------|
| 1.1 | Project scoping — problem, users, actions, feedback loop |
| 2.1 | Agent design — ReAct framework, memory types, external tools, failure modes |
| 3.1 | RAG integration — retrieval sources, chunking, vector DB, failure modes |
| 4.1 | Tree-of-Thoughts — decision stage only, Beam Search, Critic Agent, scoring |
| 5.1 | Multi-agent architecture — 5 agents, hybrid coordination, MCP memory layer |
| 6.1 | Safety guardrails — evidence requirements, human oversight, evaluation metrics |
