# Enterprise Code Cleanup and Modernization Agent

**CMU Agentic AI Program — Capstone Project**
**Student:** Chetna Khanna

---

## What This Does

An autonomous multi-agent system that analyzes large enterprise Java codebases for technical debt, then produces safe, evidence-backed cleanup recommendations — without ever modifying code directly.

Tested against the Jenkins CI/CD platform (`jenkinsci/jenkins`) — 500k+ lines of Java, 605 extension point classes, active plugin ecosystem.

---

## Architecture

```
[Phase 1] Environment Setup
  → Java 21 + Maven 3.9 (build Jenkins locally)
  → Python 3.12 venv (CrewAI, LangChain, ChromaDB)
  → Jenkins test suite verified (13/13 passing)
      ↓
[Phase 2] Repository Analysis Agent
  → PMD (dead code) + SpotBugs (bugs) + jdeps (dependencies)
  → 208 ranked candidates
      ↓
[Phase 3] Context Retrieval Agent  (RAG)
  → 1,715 docs / 7,862 chunks indexed in ChromaDB
  → Sources: Javadoc, CONTRIBUTING.md, package-info, pom.xml
  → Verdict: SAFE / RISKY / UNCERTAIN
      ↓
[Phase 4a] Thought Generator Agent  (Tree-of-Thoughts + Beam Search)
  → 3–5 branches: REMOVE / REFACTOR / DEPRECATE / KEEP
      ↓
[Phase 4b] Critic Agent  (o3-mini)
  → Challenges assumptions, prunes unsafe branches
  → Selects winning action
      ↓
[Phase 5] Recommendation Agent
  → Confidence score + rationale + unified diff
  → Escalation flag for human review
      ↓
[Phase 6] MCP Memory Layer
  → Saves decisions, developer overrides, rule patterns
  → System learns across sessions
```

---

## Evaluation Results

### Core Module (20 candidates, ground-truth labeled)

| Metric | Score |
|--------|-------|
| Accuracy | 95% |
| False-positive rate | 0% |
| Escalation rate | 100% |
| Avg confidence (correct) | 0.56 |
| Avg confidence (wrong) | 0.47 |

Zero false positives — the system never recommended removing plugin-adjacent code.
Calibrated: correct predictions consistently score higher confidence than wrong ones.

### Test Module (169 candidates available)

PMD found 169 dead code violations in `test/src/test/java` across 5 rule types.
Pipeline processes them identically to production code via `--src-subpath src/test/java`.

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Agent framework | CrewAI 1.14 |
| LLM (most agents) | GPT-4o (OpenAI) |
| LLM (Critic agent) | o3-mini (OpenAI) |
| Vector database | ChromaDB 1.1 |
| Embeddings | all-MiniLM-L6-v2 (local, free) |
| Static analysis | PMD 7.25 + SpotBugs |
| Dependency analysis | jdeps (JDK built-in) |
| Memory layer | MCP (Model Context Protocol) |
| Test codebase | Jenkins core (`jenkinsci/jenkins`) |

---

## Project Structure

```
.
├── agents/
│   ├── repository_analysis_agent.py   # Phase 2 — scans codebase
│   ├── context_retrieval_agent.py     # Phase 3 — RAG over docs
│   ├── thought_generator_agent.py     # Phase 4a — ToT branches
│   ├── critic_agent.py                # Phase 4b — prunes branches (o3-mini)
│   └── recommendation_agent.py       # Phase 5 — final output + diff
├── tools/
│   ├── static_analysis.py            # PMD + SpotBugs + jdeps wrappers
│   ├── doc_indexer.py                # ChromaDB indexing + retrieval
│   ├── diff_generator.py             # Programmatic unified diff generation
│   └── memory_client.py             # MCP memory read/write
├── mcp_server/
│   └── server.py                     # MCP server (save/recall/override/patterns)
├── pipeline.py                       # End-to-end orchestration
├── evaluate.py                       # 20-candidate evaluation framework
├── requirements.txt
└── output/                           # Generated results (gitignored)
```

---

## Setup

**Prerequisites:** Java 21, Maven 3.9, Python 3.12, Homebrew

```bash
# 1. Clone this repo and the Jenkins test codebase
git clone https://github.com/chetnakhanna16/jenkins

# 2. Install Python dependencies
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. Set your API key
echo "OPENAI_API_KEY=your-key-here" > .env

# 4. Build the Jenkins codebase (Phase 1)
cd jenkins && mvn clean install -DskipTests -q && cd ..

# 5. Build the RAG index — one-time setup, ~5 minutes (Phase 3)
.venv/bin/python3 tools/doc_indexer.py

# 6. Run the pipeline
.venv/bin/python3 pipeline.py --module core --candidates 5

# Run dead-code focused mode
.venv/bin/python3 pipeline.py --module core --candidates 5 --mode dead-code

# Run on the test module (scans test sources)
.venv/bin/python3 pipeline.py --module test --src-subpath src/test/java --candidates 5 --mode dead-code

# 7. Run evaluation (20 candidates with ground truth)
.venv/bin/python3 evaluate.py
```

---

## Key Design Decisions

| Decision | Why |
|----------|-----|
| Jenkins as test codebase | Real enterprise Java codebase with 605 dynamic extension points — maximum stress test for false-positive prevention |
| ReAct loop (Phases 2–5) | Grounds LLM reasoning in real tool outputs at each step |
| Tree-of-Thoughts + Beam Search | Explores multiple strategies before committing; beam width=3 balances coverage and cost |
| Critic as separate agent (o3-mini) | Adversarial review avoids self-confirmation bias; o3-mini's stronger reasoning eliminates false positives |
| RAG for docs, static analysis for code | Each tool suited to its data type — avoids bloating vector DB with source code |
| No direct code modification | Human always reviews before any change is made |
| MCP memory layer | System learns from past decisions across sessions without retraining |

---

## Safety Guardrails

1. Every recommendation backed by both static analysis AND retrieved documentation
2. Agent never modifies code — produces recommendations and diffs only
3. Critic agent challenges all assumptions before any action is selected
4. Automatic escalation on: RISKY RAG verdict, REMOVE actions, confidence < 0.6
5. Developer overrides permanently block re-analysis of sensitive files
6. Conflicting or insufficient evidence → confidence drops, human review required
