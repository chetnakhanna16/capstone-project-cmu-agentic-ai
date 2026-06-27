# Self-Study Capstone Checkpoint Activity 7.1: Final Capstone Report
**CMU Agentic AI Program — Building Autonomous Systems for Real-World Applications**
**Student:** Chetna Khanna

---

## 1. Project Title

**Enterprise Code Cleanup and Modernization Agent: An Autonomous Multi-Agent System for Safe Technical Debt Reduction in Large Java Codebases**

---

## 2. Problem and User

Large enterprise Java codebases accumulate technical debt over years — unused private fields, dead local variables, unreachable methods, unnecessary imports, and risky code patterns that bloat the codebase, slow builds, and increase the cognitive load on every developer who reads the code. Static analysis tools like PMD and SpotBugs can identify hundreds of potential issues in a single module, but the raw output is a flat list with no context: engineers must manually research each finding, judge whether it is truly safe to remove, and write the cleanup diff themselves. On a codebase with 500,000+ lines of Java this is impractical to do well.

The problem is especially acute in dynamic plugin ecosystems. In Jenkins, for example, 605 classes implement extension points that are loaded at runtime through reflection. A class that *looks* unused — no direct call sites in the source — may be instantiated dynamically by any of hundreds of third-party plugins. A naive cleanup tool that recommends removing such a class would silently break the entire plugin ecosystem. False positives are not just annoying; they are dangerous.

**Intended users:**
- Senior engineers and tech leads responsible for codebase health in large Java projects
- Platform teams maintaining open-source infrastructure (Jenkins, Spring, etc.)
- Engineering managers who want safe, evidence-backed cleanup recommendations before scheduling refactoring sprints

**Why this matters:** Technical debt compounds — each ignored warning makes the next cleanup harder. But unsafe cleanups in plugin ecosystems cause regressions that take weeks to diagnose. Neither extreme (ignore everything, or remove everything flagged) is acceptable. The gap between raw static analysis output and an actionable, safe recommendation is exactly where an autonomous agent adds value.

---

## 3. System Goal and Scope

**Goal:** Autonomously analyze a large enterprise Java codebase, identify cleanup candidates from static analysis, retrieve documentary evidence about each candidate, reason through multiple cleanup strategies, and produce a final recommendation with a mathematical confidence score and a ready-to-apply unified diff — without ever modifying code directly.

**What successful performance looks like:**
- Accuracy ≥ 90% on ground-truth labeled candidates
- False-positive rate of 0% on plugin-adjacent code (never recommend removing dynamically loaded classes)
- Calibrated confidence: correct predictions score higher confidence than wrong ones
- Every recommendation backed by both static analysis evidence and retrieved documentation
- Escalation to human review whenever evidence is insufficient or stakes are high

**Boundaries and constraints:**
- The system **never modifies code** — it produces recommendations and diffs only; all actual changes require a human engineer to review and apply
- Scope is Java source code analyzed through PMD (dead code) and SpotBugs (bug patterns)
- Test codebase: Jenkins CI/CD platform (`jenkinsci/jenkins`), chosen specifically because its 605 extension point classes create maximum false-positive pressure
- The system escalates to human review for any REMOVE action, any RISKY RAG verdict, or any candidate where confidence falls below 0.60

---

## 4. Final System Architecture

The system implements a six-phase sequential multi-agent pipeline built on CrewAI's ReAct (Reason → Act → Observe) loop.

**Phase 1 — Environment Setup**
Java 21 + Maven 3.9 to build Jenkins locally (required for SpotBugs bytecode analysis). Python 3.12 virtual environment with all agent and tool dependencies. One-time RAG index construction: 1,715 Jenkins documentation files chunked into 7,862 segments and embedded into ChromaDB using local `all-MiniLM-L6-v2` embeddings.

**Phase 2 — Repository Analysis Agent (ReAct)**
Runs PMD 7.25 (six dead code rules) and SpotBugs (STYLE/BAD_PRACTICE categories) on the Jenkins source. Scores each finding by severity and tool type. Returns a ranked list of candidates as structured JSON. Also runs `jdeps` for a dependency summary. Implemented with GPT-4o.

**Phase 3 — Context Retrieval Agent (RAG)**
Key optimization: pure dead code rules (`UnusedPrivateField`, `UnusedLocalVariable`, `UnusedPrivateMethod`, `UnusedFormalParameter`, `UnnecessaryImport`) bypass the LLM entirely and immediately return `SAFE/HIGH` — these are private or local by definition and cannot be referenced externally. For all other rules, the agent queries ChromaDB with 2–3 targeted queries against indexed Javadoc, CONTRIBUTING.md, package-info.java files, and pom.xml dependency sections. Returns a verdict of `SAFE`, `RISKY`, or `UNCERTAIN` with a confidence level. Implemented with GPT-4o.

**Phase 4a — Thought Generator Agent (Tree-of-Thoughts + Beam Search)**
Given the candidate and RAG verdict, generates 3–5 distinct action branches: `REMOVE`, `REFACTOR`, `DEPRECATE`, or `KEEP`. Each branch includes a concrete description, a list of assumptions, and scores for feasibility, safety, and impact (0.0–1.0). Applies Beam Search, retaining the top 3 branches by overall score. Implemented with GPT-4o.

**Phase 4b — Critic Agent (Adversarial Review)**
An independent agent that challenges all Thought Generator branches before any action is selected. Uses rule-aware pruning thresholds: 0.3 for dead code rules with SAFE/HIGH verdict (permissive, since private code is provably safe), 0.5 for all other rules (stricter). Selects the winning action; defaults to KEEP if all branches are pruned. Implemented with **o3-mini** specifically — chosen over GPT-4o because its stronger conditional reasoning eliminated a persistent false positive on `Computer.java`.

**Phase 5 — Recommendation Agent**
Computes confidence mathematically (not LLM-generated):
```
score = winner.overall_score
      + verdict_adjustment  (SAFE: +0.1, UNCERTAIN: -0.2, RISKY: -0.25)
      + confidence_adjustment (HIGH: +0.05, MEDIUM: 0.0, LOW: -0.1)
```
Capped by action type: `REMOVE ≤ 0.65`, `REFACTOR ≤ 0.75`, `KEEP ≤ 0.60`. Escalates automatically on: RISKY verdict, REMOVE action, UNCERTAIN+LOW, or confidence < 0.60. Generates unified diffs programmatically via Python `difflib` (not LLM-generated, which were inconsistent). Implemented with GPT-4o.

**Phase 6 — MCP Memory Layer**
A Model Context Protocol server persisting decisions, developer overrides, and rule-level patterns to a local JSON store. Before each candidate: checks for developer overrides (skip if blocked) and surfaces historical rule patterns (e.g., "this rule is historically RISKY 4/5 times"). After each candidate: saves the decision for cross-session learning. The system learns from its own history without retraining.

**Cross-cutting components:**
- `pipeline.py` — CLI orchestrator chaining all phases; `--mode diverse` or `--mode dead-code`; `--src-subpath` for multi-module support
- `evaluate.py` — 20-candidate ground-truth evaluation framework with accuracy, false-positive rate, escalation rate, and calibration metrics
- `report.py` — self-contained HTML report generator; `--mode eval` for evaluation report, `--mode pipeline` for multi-module pipeline report
- `smoke_test.py` + `.github/workflows/smoke_test.yml` — CI smoke test that verifies all components on every push

---

## 5. Design Evolution Across the Program

**Module 1 — Problem Definition and Initial Design**
Defined the problem, chose Jenkins as the test codebase, and planned a single-agent pipeline. The critical early decision: select Jenkins *specifically because* of its 605 extension point classes, making it a worst-case stress test for false-positive prevention. A system that passes on Jenkins is much more likely to be safe on simpler codebases.

**Module 2 — Repository Analysis (Tool Use + ReAct)**
Built the first working agent with PMD and SpotBugs tool wrappers. Key technical discovery: PMD 7 changed its XML report schema — the filename moved from `<violation>` attributes to the `<file>` parent element. Required a full parser rewrite. Introduced a scoring formula (`severity × tool_multiplier`) to rank candidates. Established that SpotBugs requires compiled bytecode while PMD works on source, which shaped the setup requirements.

**Module 3 — Context Retrieval (RAG)**
Built ChromaDB index from 1,715 Jenkins documentation files. Most important refinement: discovered that querying RAG for dead code rules (`UnusedPrivateField` etc.) is unnecessary and counterproductive — the RAG system would sometimes return UNCERTAIN for things that are provably safe (private fields can't be accessed externally). Added a pre-classification bypass that returns `SAFE/HIGH` deterministically for these rules, improving speed, cost, and reliability simultaneously.

**Module 4 — Tree-of-Thoughts Reasoning**
Introduced the Thought Generator + Critic two-agent design. Initially both used GPT-4o. Problem: the Critic had a "blanket skeptic" backstory that caused it to prune all branches and default to KEEP, resulting in 100% escalation and useless recommendations. Fix: changed Critic backstory to "calibrated" and added rule-aware pruning thresholds so dead code candidates are evaluated more permissively than ambiguous SpotBugs findings.

**Module 5 — Recommendation and Safety**
Two major fixes here: (1) switched from LLM-generated confidence scores (which were arbitrary and uncalibrated) to a mathematical formula with explicit adjustments and action-type caps; (2) switched from LLM-generated diffs (which were inconsistent and sometimes invented context) to programmatic diffs via Python `difflib`. These two changes made the system's outputs deterministic and auditable.

**Module 6 — Memory (MCP)**
Added cross-session learning. Key insight: MCP enables the system to get smarter over time without retraining — developer overrides, historical rule patterns, and prior decisions all inform future recommendations.

**Post-build refinements (evaluation-driven):**
- Switched Critic from GPT-4o to **o3-mini** after GPT-4o produced a false positive on `Computer.java` (a core Jenkins infrastructure class). o3-mini's stronger reasoning eliminated it.
- Fixed **confidence calibration inversion**: wrong predictions were scoring *higher* confidence than correct ones. Root cause: KEEP had no confidence cap, so the Critic's high ToT branch score could reach 0.95 for a wrong KEEP prediction. Fix: added `KEEP ≤ 0.60` cap and increased `UNCERTAIN` penalty from -0.1 to -0.2.
- Fixed evaluate.py to **bypass developer overrides** during ground-truth evaluation — overrides are a production feature but must not reduce the evaluation sample.
- Fixed `DependencyRunner.java` path in the FILE_MAP (wrong package: `jenkins/model/lazy/` → `hudson/`).

---

## 6. Implementation Overview

| Component | Technology | Why |
|-----------|-----------|-----|
| Agent framework | CrewAI 1.14 | Provides ReAct loops, tool integration, and multi-agent orchestration out of the box |
| LLM (most agents) | GPT-4o (OpenAI) | Best balance of reasoning quality and cost for the analysis/generation tasks |
| LLM (Critic) | o3-mini (OpenAI) | Stronger conditional and adversarial reasoning; eliminated false positives that GPT-4o missed |
| Vector database | ChromaDB 1.1 | Local, no cloud dependency, fast similarity search, simple Python API |
| Embeddings | all-MiniLM-L6-v2 | Local (free), 384-dimensional, high-quality semantic similarity for code and documentation |
| Dead code analysis | PMD 7.25 | Source-only (no build needed), six targeted dead code rules, XML output |
| Bug pattern analysis | SpotBugs (Maven) | Bytecode-level analysis catches patterns PMD misses; finds null pointer and comparator bugs |
| Dependency analysis | jdeps (JDK built-in) | No installation required, summarizes module-level dependency graph |
| Diff generation | Python `difflib` | Programmatic, deterministic, auditable — replaces inconsistent LLM-generated diffs |
| Memory / persistence | MCP SDK 1.26.0 | Model Context Protocol enables structured cross-session memory with four tool endpoints |
| API integration | openai 2.43.0, python-dotenv | OpenAI API access; dotenv for clean credential management |
| Reporting | Pure Python HTML | Self-contained single-file HTML reports with no external JS/CSS dependencies |
| CI | GitHub Actions | Smoke test on every push; validates all components without requiring API key for first 4 checks |

**Key architectural choices:**
- **RAG over documentation, static analysis over source code**: Each tool is matched to its appropriate data type. Indexing source code into ChromaDB would create noise and miss the real signal — Javadoc and architecture docs are where extension point relationships are documented.
- **Local embeddings**: Using `all-MiniLM-L6-v2` locally means zero embedding API cost regardless of index size. With 7,862 chunks, this would otherwise be significant.
- **Mathematical confidence, not LLM confidence**: LLMs calibrate numbers poorly when asked to self-assess. The mathematical formula is transparent, auditable, and consistently calibrated against observed ground truth.
- **Two separate Python libraries (CrewAI + LangChain)**: CrewAI handles agent orchestration and ReAct loops; LangChain is used specifically for the ChromaDB integration layer, which CrewAI does not natively support.

---

## 7. Evaluation and Results

**Methodology**

A hand-labeled ground truth set of 20 candidates was drawn from Jenkins core, split to reflect two fundamentally different failure modes:

- **11 safe dead code candidates** (correct action: REMOVE or REFACTOR) — private fields, local variables, private methods with no callers, unnecessary imports, and a null-pointer style issue in a CLI handler class
- **9 plugin-adjacent candidates** (correct action: KEEP or DEPRECATE, never REMOVE) — classes implementing Extensible, `compareTo` methods in plugin lifecycle classes, and null-pointer candidates in core plugin infrastructure

Accuracy was measured liberally for safe candidates (REMOVE and REFACTOR both accepted as correct), and strictly for plugin-adjacent candidates (any REMOVE or REFACTOR = false positive regardless of confidence).

**Results**

| Metric | Result |
|--------|--------|
| Total candidates evaluated | 20 |
| Accuracy | 95% (19/20) |
| False-positive rate | 0% |
| Escalation rate | 100% |
| Avg confidence (correct) | 0.56 |
| Avg confidence (wrong) | 0.47 |
| Calibrated (correct > wrong) | Yes |

**Key findings:**

- **Zero false positives** across all 9 plugin-adjacent candidates, including the highest-risk classes: `ExtensionComponent.java`, `PluginWrapper.java`, `Computer.java`, and `Functions.java`. The combination of RAG retrieval, adversarial Critic review with o3-mini, and RISKY verdict escalation collectively prevented every case of incorrectly recommending cleanup on plugin infrastructure.

- **Calibrated confidence**: After fixing the KEEP cap, correct predictions reliably score higher average confidence (0.56) than wrong predictions (0.47). This means the confidence score is meaningful — a reviewer can prioritize lower-confidence recommendations for closer scrutiny.

- **One remaining miss**: `BuildCommand.java:204` — a null-pointer path in a CLI handler. The system alternates between KEEP and REFACTOR across independent runs, suggesting genuine ambiguity. The ground truth label is REFACTOR, but the system's KEEP reasoning (insufficient evidence of where the null originates) is defensible.

- **Escalation rate 100%**: Every candidate was flagged for human review. This is conservative by design — in production, it means no change is ever applied without a human approving it. The escalation rate could be reduced by relaxing the always-escalate-REMOVE rule for high-confidence dead code candidates.

---

## 8. Safety and Reliability Considerations

**Guardrails**

1. **No code modification**: The system produces recommendations and diffs only. No file is ever written to by the pipeline. A human engineer must copy, review, and apply any diff.

2. **Dead code pre-classification**: Private/local code rules bypass the LLM entirely and return a deterministic `SAFE/HIGH` verdict. This eliminates a class of LLM hallucination risk for the most common rule types.

3. **Independent Critic agent**: The Critic (o3-mini) is architecturally separate from the Thought Generator and has no knowledge of which branches were proposed first. Its sole job is to challenge and prune. This guards against self-confirmation bias.

4. **Programmatic diffs**: Unified diffs are generated by Python `difflib` against the actual source file, not by an LLM. The diff is always grounded in the real file content and correct line numbers.

5. **Confidence caps by action type**: `REMOVE ≤ 0.65`, `REFACTOR ≤ 0.75`, `KEEP ≤ 0.60`. Even if an LLM-generated branch score is inflated to 1.0, the final reported confidence is bounded. This prevents the system from being "overconfident" about any recommendation.

6. **Action normalization**: LLM outputs like `DEPRECATE_METHOD` or `REMOVE_FIELD` are normalized to canonical actions (`DEPRECATE`, `REMOVE`) before any downstream use, preventing unexpected behavior from LLM variation.

**Escalation triggers (automatic human review)**

- RAG verdict is `RISKY` — candidate touches known plugin infrastructure
- Winning action is `REMOVE` — irreversible; always requires human sign-off
- Verdict is `UNCERTAIN` and confidence level is `LOW` — insufficient evidence
- Computed confidence < 0.60 — below the reliability threshold

**Fallback logic**

- Critic defaults to `KEEP` if all branches are pruned — the safe choice when uncertain
- Recommendation agent falls back to the Critic winner action if LLM JSON parsing fails
- Pipeline catches per-candidate exceptions and continues processing remaining candidates
- `retrieve_context` returns a pre-computed `SAFE/HIGH` for dead code rules even if ChromaDB is unavailable

**Human oversight mechanisms**

- **Developer overrides**: Engineers can permanently block re-analysis of a sensitive file via the MCP memory layer. The override persists across sessions.
- **Historical pattern surfacing**: MCP memory surfaces rule-level patterns before each analysis ("this rule is historically RISKY 4/5 times") so the system enters each analysis with calibrated priors.
- **HTML evaluation report**: Every recommendation, diff, rationale, confidence score, and escalation reason is viewable in the interactive HTML report before any action is taken. Rows are expandable to show the full diff.
- **CI smoke test**: GitHub Actions verifies all components on every push, catching regressions before they affect results.

---

## 9. Limitations and Next Steps

**Current Limitations**

1. **Evaluation breadth**: The 20-candidate ground truth set covers only Jenkins core. Performance on other large Java codebases (Spring, Hadoop, Cassandra) is unknown. The zero false-positive result may be specific to the Jenkins documentation density in the RAG index.

2. **SpotBugs requires compiled bytecode**: Unlike PMD, SpotBugs needs the project to be built with Maven before analysis can run. This adds setup friction for new codebases and makes CI integration slower.

3. **Heuristic confidence formula**: The mathematical confidence formula was derived by observing failure modes and adjusting constants. It works well on the current evaluation set but is not learned from data. A Platt-scaled or isotonic regression calibrator trained on more labeled examples would be more principled.

4. **LLM non-determinism in edge cases**: `BuildCommand.java:204` produces different predictions (KEEP vs. REFACTOR) across independent runs. There is currently no mechanism to detect this instability and flag it. A candidate that flips between runs should automatically get a higher escalation priority.

5. **Escalation rate 100%**: In production this would create an unmanageable review queue. The always-escalate-REMOVE rule is appropriate for safety at this stage but would need to be relaxed — for example, to "escalate REMOVE only when confidence < 0.70 OR verdict is not SAFE" — before the system could be used at scale.

6. **MCP server not deployed as a service**: The `mcp_server/server.py` exists and is spec-compliant, but the pipeline currently uses `memory_client.py` (direct file access) rather than the MCP server. True multi-user team-wide memory sharing would require deploying the MCP server as a persistent HTTP service.

**Realistic Next Steps**

1. **Broader evaluation**: Run on 3–5 non-Jenkins Java codebases with fresh ground truth labels to validate that the 0% false-positive rate is generalizable.

2. **LLM instability detection**: Run each candidate twice; if the predicted action differs, flag the candidate with a new `UNSTABLE` escalation reason and report both predictions. This turns a hidden failure mode into an explicit, visible signal.

3. **Relaxed escalation rules**: After accumulating 50+ confirmed correct REMOVE decisions in memory, allow high-confidence (`≥ 0.65`) dead code candidates with `SAFE/HIGH` verdict to be auto-approved rather than escalated.

4. **Confidence calibration via training**: Collect 100+ labeled results and fit a logistic regression calibrator on the raw confidence scores. Replace the hand-tuned formula with the calibrated model.

5. **Deploy MCP server**: Run `mcp_server/server.py` as a persistent service so that multiple engineers' pipeline runs share the same memory store, enabling team-wide learning from past decisions.

---

## 10. Public GitHub Repository

**Repository:** https://github.com/chetnakhanna16/capstone-project-cmu-agentic-ai

**The repository includes:**

1. **README.md** — Full project documentation: problem statement, 6-phase architecture diagram (ASCII), tech stack table, project structure, step-by-step setup instructions, usage examples (diverse mode, dead-code mode, multi-module mode), evaluation results table, key design decisions with rationale, and safety guardrails

2. **Main system code:**
   - `pipeline.py` — End-to-end CLI orchestrator
   - `agents/` — Five agent implementations (repository analysis, context retrieval, thought generator, critic, recommendation)
   - `tools/` — Static analysis wrappers, ChromaDB indexer/retriever, diff generator, MCP memory client
   - `mcp_server/server.py` — MCP-compliant memory server
   - `evaluate.py` — 20-candidate ground-truth evaluation framework
   - `report.py` — HTML report generator (eval + pipeline modes)
   - `smoke_test.py` — CI smoke test

3. **Evaluation artifacts:**
   - `output/report.html` — Interactive evaluation report: per-candidate diffs, confidence bars, calibration chart, action and verdict distribution charts
   - `output/pipeline_report.html` — Multi-module pipeline report with module filter

4. **CI:**
   - `.github/workflows/smoke_test.yml` — GitHub Actions workflow that validates all components on every push to main

**Running the project:**
```bash
# Clone repo and Jenkins codebase
git clone https://github.com/chetnakhanna16/capstone-project-cmu-agentic-ai
git clone --depth=1 https://github.com/chetnakhanna16/jenkins

# Install dependencies
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
echo "OPENAI_API_KEY=your-key" > .env

# Build Jenkins (needed for SpotBugs)
cd jenkins && mvn clean install -DskipTests -q && cd ..

# Index documentation (one-time, ~5 min)
.venv/bin/python3 tools/doc_indexer.py

# Run pipeline
.venv/bin/python3 pipeline.py --module core --candidates 5

# Run evaluation
.venv/bin/python3 evaluate.py

# Generate HTML report
.venv/bin/python3 report.py
```
