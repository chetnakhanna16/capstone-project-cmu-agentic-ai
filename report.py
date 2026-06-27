"""
Generate self-contained HTML reports.

Usage:
    # Evaluation report (ground-truth accuracy metrics):
    .venv/bin/python3 report.py
    .venv/bin/python3 report.py --mode eval       # → output/report.html

    # Multi-module pipeline report (all pipeline_results_*.json files):
    .venv/bin/python3 report.py --mode pipeline   # → output/pipeline_report.html
"""

import argparse
import json
import html
from pathlib import Path
from datetime import datetime

OUTPUT_DIR = Path(__file__).parent / "output"
RESULTS_FILE = OUTPUT_DIR / "evaluation_results.json"
REPORT_FILE  = OUTPUT_DIR / "report.html"

VERDICT_COLOR = {"SAFE": "#22c55e", "RISKY": "#ef4444", "UNCERTAIN": "#f59e0b"}
ACTION_COLOR  = {
    "REMOVE":    "#ef4444",
    "REFACTOR":  "#3b82f6",
    "DEPRECATE": "#f59e0b",
    "KEEP":      "#22c55e",
}


def _conf_color(conf: float) -> str:
    if conf >= 0.7:
        return "#22c55e"
    if conf >= 0.6:
        return "#f59e0b"
    return "#ef4444"


def _badge(text: str, color: str, text_color: str = "#fff") -> str:
    return (
        f'<span style="background:{color};color:{text_color};'
        f'padding:2px 8px;border-radius:9999px;font-size:0.75rem;'
        f'font-weight:600;white-space:nowrap">{html.escape(str(text))}</span>'
    )


_NO_DIFF_PHRASES = {"(no change)", "no code changes", "no change to the code", "no changes are recommended"}


def _is_unified_diff(text: str) -> bool:
    return text.startswith("---") or text.startswith("@@") or text.startswith("diff ")


def _is_error_diff(text: str) -> bool:
    return text.startswith("(could not read")


def _diff_block(diff: str, action: str = "") -> str:
    if not diff or any(p in diff.lower() for p in _NO_DIFF_PHRASES):
        if action == "KEEP":
            return '<em style="color:#6b7280">No code change — recommendation is KEEP.</em>'
        return '<em style="color:#6b7280">No diff available.</em>'

    if _is_error_diff(diff):
        return f'<em style="color:#f59e0b">{html.escape(diff)}</em>'

    if not _is_unified_diff(diff):
        # prose explanation from LLM — render as a note block
        return (
            '<span style="color:#94a3b8;font-style:italic;white-space:pre-wrap">'
            + html.escape(diff)
            + "</span>"
        )

    lines = []
    for line in diff.splitlines():
        escaped = html.escape(line)
        if line.startswith("---") or line.startswith("+++"):
            lines.append(f'<span style="color:#94a3b8">{escaped}</span>')
        elif line.startswith("-"):
            lines.append(f'<span style="color:#fca5a5;background:#450a0a">{escaped}</span>')
        elif line.startswith("+"):
            lines.append(f'<span style="color:#86efac;background:#052e16">{escaped}</span>')
        elif line.startswith("@@"):
            lines.append(f'<span style="color:#7dd3fc">{escaped}</span>')
        else:
            lines.append(f'<span style="color:#d1d5db">{escaped}</span>')
    return "\n".join(lines)


def compute_metrics(results: list[dict]) -> dict:
    valid = [r for r in results if r.get("predicted") not in ("ERROR", "SKIP", None)]
    total = len(valid)
    if not total:
        return {}

    correct      = [r for r in valid if r.get("correct")]
    false_pos    = [r for r in valid if not r.get("is_safe", True)
                    and str(r.get("predicted", "")).upper().split()[0] in {"REMOVE", "REFACTOR"}]
    escalated    = [r for r in valid if r.get("escalated")]
    confs        = [r["confidence"] for r in valid]
    conf_correct = [r["confidence"] for r in valid if r.get("correct")]
    conf_wrong   = [r["confidence"] for r in valid if not r.get("correct")]

    return {
        "total":          total,
        "correct":        len(correct),
        "accuracy":       round(100 * len(correct) / total),
        "false_positives": len(false_pos),
        "fp_rate":        round(100 * len(false_pos) / total),
        "escalated":      len(escalated),
        "esc_rate":       round(100 * len(escalated) / total),
        "avg_conf":       round(sum(confs) / len(confs), 2),
        "avg_conf_correct": round(sum(conf_correct) / len(conf_correct), 2) if conf_correct else 0,
        "avg_conf_wrong":   round(sum(conf_wrong)   / len(conf_wrong),   2) if conf_wrong   else 0,
        "action_dist":    _count(valid, "predicted"),
        "verdict_dist":   _count(valid, "verdict"),
    }


def _count(items: list[dict], key: str) -> dict:
    out: dict[str, int] = {}
    for r in items:
        v = str(r.get(key, "UNKNOWN")).upper().split()[0]
        out[v] = out.get(v, 0) + 1
    return out


def build_html(results: list[dict], m: dict) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows = _build_rows(results)
    calibration = _calibration_bars(m)
    action_chart = _dist_chart(m.get("action_dist", {}), ACTION_COLOR, "Action Distribution")
    verdict_chart = _dist_chart(m.get("verdict_dist", {}), VERDICT_COLOR, "RAG Verdict Distribution")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Code Cleanup Agent — Evaluation Report</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
         background:#0f172a; color:#e2e8f0; line-height:1.5; padding:2rem; }}
  h1 {{ font-size:1.5rem; font-weight:700; color:#f1f5f9; }}
  h2 {{ font-size:1rem; font-weight:600; color:#94a3b8; text-transform:uppercase;
        letter-spacing:.05em; margin-bottom:.75rem; }}
  .subtitle {{ color:#64748b; font-size:.85rem; margin-top:.25rem; }}
  .header {{ margin-bottom:2rem; border-bottom:1px solid #1e293b; padding-bottom:1.25rem; }}
  .grid {{ display:grid; gap:1rem; grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
           margin-bottom:2rem; }}
  .card {{ background:#1e293b; border:1px solid #334155; border-radius:.75rem; padding:1.25rem; }}
  .card .label {{ font-size:.75rem; color:#64748b; text-transform:uppercase;
                  letter-spacing:.05em; margin-bottom:.25rem; }}
  .card .value {{ font-size:2rem; font-weight:700; color:#f1f5f9; }}
  .card .sub {{ font-size:.8rem; color:#94a3b8; margin-top:.25rem; }}
  .section {{ margin-bottom:2rem; }}
  .charts {{ display:grid; gap:1rem; grid-template-columns:1fr 1fr; margin-bottom:2rem; }}
  .bar-row {{ display:flex; align-items:center; gap:.5rem; margin-bottom:.5rem; font-size:.85rem; }}
  .bar-label {{ width:90px; text-align:right; color:#94a3b8; flex-shrink:0; }}
  .bar-track {{ flex:1; background:#334155; border-radius:4px; height:10px; overflow:hidden; }}
  .bar-fill  {{ height:100%; border-radius:4px; }}
  .bar-val   {{ width:40px; color:#cbd5e1; font-size:.8rem; }}
  table {{ width:100%; border-collapse:collapse; font-size:.85rem; }}
  thead th {{ background:#1e293b; color:#94a3b8; font-weight:600; padding:.6rem .75rem;
              text-align:left; position:sticky; top:0; border-bottom:1px solid #334155; }}
  tbody tr {{ border-bottom:1px solid #1e293b; cursor:pointer; }}
  tbody tr:hover {{ background:#1e293b; }}
  tbody tr.incorrect {{ background:#1c0a0a; }}
  tbody tr.incorrect:hover {{ background:#2d0f0f; }}
  td {{ padding:.55rem .75rem; vertical-align:middle; }}
  .status-correct  {{ color:#22c55e; font-weight:700; font-size:1rem; }}
  .status-incorrect {{ color:#ef4444; font-weight:700; font-size:1rem; }}
  .conf-wrap {{ display:flex; align-items:center; gap:.5rem; }}
  .conf-bar {{ width:60px; height:6px; border-radius:3px; background:#334155; flex-shrink:0; }}
  .conf-fill {{ height:100%; border-radius:3px; }}
  .diff-row td {{ padding:0; }}
  .diff-panel {{ display:none; background:#020617; border-top:1px solid #1e293b;
                 padding:1rem 1.25rem; }}
  .diff-panel.open {{ display:block; }}
  pre.diff {{ font-family:'Fira Code',Menlo,monospace; font-size:.78rem; line-height:1.6;
              overflow-x:auto; white-space:pre; }}
  .rationale {{ color:#94a3b8; font-size:.8rem; margin-top:.75rem; font-style:italic; }}
  .expand-hint {{ color:#475569; font-size:.7rem; }}
  @media(max-width:768px) {{ .charts {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>

<div class="header">
  <h1>Enterprise Code Cleanup Agent — Evaluation Report</h1>
  <p class="subtitle">CMU Agentic AI Program · Capstone Project · Generated {ts}</p>
</div>

<!-- Metric cards -->
<div class="grid">
  <div class="card">
    <div class="label">Accuracy</div>
    <div class="value" style="color:{'#22c55e' if m['accuracy']>=80 else '#f59e0b'}">{m['accuracy']}%</div>
    <div class="sub">{m['correct']} of {m['total']} correct</div>
  </div>
  <div class="card">
    <div class="label">False-Positive Rate</div>
    <div class="value" style="color:{'#22c55e' if m['fp_rate']==0 else '#ef4444'}">{m['fp_rate']}%</div>
    <div class="sub">{m['false_positives']} plugin-adjacent misses</div>
  </div>
  <div class="card">
    <div class="label">Escalation Rate</div>
    <div class="value" style="color:#f59e0b">{m['esc_rate']}%</div>
    <div class="sub">{m['escalated']} flagged for human review</div>
  </div>
  <div class="card">
    <div class="label">Avg Confidence</div>
    <div class="value" style="color:#3b82f6">{m['avg_conf']}</div>
    <div class="sub">Correct: {m['avg_conf_correct']} · Wrong: {m['avg_conf_wrong']}</div>
  </div>
</div>

<!-- Charts -->
<div class="charts">
  <div class="card">
    {action_chart}
  </div>
  <div class="card">
    {verdict_chart}
  </div>
</div>

<!-- Calibration -->
<div class="card section">
  <h2>Confidence Calibration</h2>
  {calibration}
</div>

<!-- Per-candidate table -->
<div class="card section">
  <h2>Per-Candidate Results</h2>
  <p class="expand-hint" style="margin-bottom:.75rem;color:#475569;font-size:.8rem">
    Click any row to expand the diff and rationale.
  </p>
  <div style="overflow-x:auto">
  <table>
    <thead>
      <tr>
        <th></th>
        <th>File</th>
        <th>Rule</th>
        <th>Ground Truth</th>
        <th>Predicted</th>
        <th>Confidence</th>
        <th>RAG Verdict</th>
        <th>Escalated</th>
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
  </div>
</div>

<script>
  document.querySelectorAll('tr[data-id]').forEach(row => {{
    row.addEventListener('click', () => {{
      const panel = document.getElementById('diff-' + row.dataset.id);
      if (panel) panel.classList.toggle('open');
    }});
  }});
</script>
</body>
</html>"""


def _build_rows(results: list[dict]) -> str:
    out = []
    valid = [r for r in results if r.get("predicted") not in ("ERROR", "SKIP", None)]
    for i, r in enumerate(valid):
        correct   = r.get("correct", False)
        fname     = r.get("fname", "?")
        line      = r.get("line", "?")
        rule      = r.get("rule", "?")
        gt        = r.get("gt", "?")
        predicted = str(r.get("predicted", "?")).upper().split()[0]
        conf      = float(r.get("confidence", 0))
        verdict   = str(r.get("verdict", "UNCERTAIN")).upper()
        escalated = r.get("escalated", False)
        diff             = r.get("diff", "")
        rationale        = r.get("rationale", "")
        action_statement = r.get("action_statement", "")
        escalation_reason = r.get("escalation_reason", "")

        row_class = "" if correct else " class='incorrect'"
        status_cls = "status-correct" if correct else "status-incorrect"
        status_icon = "✓" if correct else "✗"

        pred_color = ACTION_COLOR.get(predicted, "#94a3b8")
        vrd_color  = VERDICT_COLOR.get(verdict, "#6b7280")
        cc = _conf_color(conf)
        conf_pct = int(conf * 100)
        esc_badge = (_badge("Escalated", "#854d0e", "#fef08a") if escalated
                     else _badge("Auto", "#1e3a5f", "#93c5fd"))

        diff_html = _diff_block(diff, predicted)

        meta_parts = []
        if action_statement:
            meta_parts.append(
                f'<div style="color:#e2e8f0;font-size:.82rem;margin-bottom:.5rem">'
                f'{html.escape(action_statement)}</div>'
            )
        if rationale:
            meta_parts.append(
                f'<div class="rationale">{html.escape(str(rationale)[:400])}</div>'
            )
        if escalated and escalation_reason:
            meta_parts.append(
                f'<div style="color:#fbbf24;font-size:.78rem;margin-top:.4rem">'
                f'Escalated: {html.escape(escalation_reason)}</div>'
            )
        rat_html = "\n".join(meta_parts)

        out.append(f"""
<tr data-id="{i}"{row_class}>
  <td><span class="{status_cls}">{status_icon}</span></td>
  <td style="font-family:monospace;font-size:.8rem">{html.escape(fname)}:{line}</td>
  <td style="color:#94a3b8;font-size:.78rem">{html.escape(rule)}</td>
  <td>{_badge(gt, "#334155", "#e2e8f0")}</td>
  <td>{_badge(predicted, pred_color)}</td>
  <td>
    <div class="conf-wrap">
      <div class="conf-bar"><div class="conf-fill" style="width:{conf_pct}%;background:{cc}"></div></div>
      <span style="font-size:.8rem;color:{cc}">{conf:.2f}</span>
    </div>
  </td>
  <td>{_badge(verdict, vrd_color)}</td>
  <td>{esc_badge}</td>
</tr>
<tr class="diff-row">
  <td colspan="8">
    <div class="diff-panel" id="diff-{i}">
      <pre class="diff">{diff_html}</pre>
      {rat_html}
    </div>
  </td>
</tr>""")
    return "\n".join(out)


def _calibration_bars(m: dict) -> str:
    items = [
        ("Correct predictions", m.get("avg_conf_correct", 0), "#22c55e"),
        ("Wrong predictions",   m.get("avg_conf_wrong",   0), "#ef4444"),
        ("All predictions",     m.get("avg_conf", 0),         "#3b82f6"),
    ]
    rows = []
    for label, val, color in items:
        pct = int(val * 100)
        rows.append(f"""
<div class="bar-row">
  <div class="bar-label">{html.escape(label)}</div>
  <div class="bar-track">
    <div class="bar-fill" style="width:{pct}%;background:{color}"></div>
  </div>
  <div class="bar-val">{val:.2f}</div>
</div>""")
    note = (
        '<p style="font-size:.78rem;color:#64748b;margin-top:.5rem">'
        "Well-calibrated: correct predictions should score higher than wrong ones."
        "</p>"
    )
    return "\n".join(rows) + note


def _dist_chart(dist: dict, color_map: dict, title: str) -> str:
    total = sum(dist.values()) or 1
    rows = []
    for key, count in sorted(dist.items(), key=lambda x: -x[1]):
        pct = int(100 * count / total)
        color = color_map.get(key, "#6b7280")
        rows.append(f"""
<div class="bar-row">
  <div class="bar-label">{html.escape(key)}</div>
  <div class="bar-track">
    <div class="bar-fill" style="width:{pct}%;background:{color}"></div>
  </div>
  <div class="bar-val">{count}</div>
</div>""")
    return f"<h2>{html.escape(title)}</h2>" + "\n".join(rows)


def _load_pipeline_results() -> list[dict]:
    """Load all pipeline_results_*.json files and tag each record with its module."""
    records = []
    for f in sorted(OUTPUT_DIR.glob("pipeline_results_*.json")):
        module = f.stem.replace("pipeline_results_", "")
        with open(f) as fh:
            data = json.load(fh)
        for item in data:
            if "error" in item or "skipped" in item:
                continue
            cand = item.get("candidate", {})
            rec  = item.get("recommendation", {})
            rv   = item.get("retrieval_verdict", {})
            action = str(rec.get("action", "")).upper().split()[0] or "UNKNOWN"
            records.append({
                "module":    module,
                "fname":     Path(cand.get("file", "?")).name,
                "file":      cand.get("file", ""),
                "line":      cand.get("line", 0),
                "rule":      cand.get("rule", ""),
                "severity":  cand.get("severity", ""),
                "action":    action,
                "confidence": float(rec.get("confidence", 0)),
                "verdict":   str(rv.get("verdict", "UNCERTAIN")).upper(),
                "escalated": bool(rec.get("escalate", False)),
                "diff":      rec.get("suggested_diff", ""),
                "rationale": rec.get("rationale", ""),
                "action_statement": rec.get("action_statement", ""),
                "escalation_reason": rec.get("escalation_reason", ""),
            })
    return records


def _module_filter_buttons(modules: list[str]) -> str:
    parts = []
    for m in modules:
        parts.append(
            f'<button class="filter-btn" '
            f'onclick="filterModule(\'{html.escape(m)}\', this)">'
            f'{html.escape(m)}</button>'
        )
    return "".join(parts)


def build_pipeline_html(records: list[dict]) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    modules = sorted(set(r["module"] for r in records))
    total = len(records)
    escalated = sum(1 for r in records if r["escalated"])
    confs = [r["confidence"] for r in records]
    avg_conf = round(sum(confs) / len(confs), 2) if confs else 0
    action_dist = _count(records, "action")
    verdict_dist = _count(records, "verdict")

    action_chart  = _dist_chart(action_dist,  ACTION_COLOR,  "Action Distribution")
    verdict_chart = _dist_chart(verdict_dist, VERDICT_COLOR, "RAG Verdict Distribution")

    module_cards = ""
    for mod in modules:
        mod_recs = [r for r in records if r["module"] == mod]
        mod_esc  = sum(1 for r in mod_recs if r["escalated"])
        mod_conf = [r["confidence"] for r in mod_recs]
        avg = round(sum(mod_conf) / len(mod_conf), 2) if mod_conf else 0
        actions = _count(mod_recs, "action")
        action_str = " · ".join(f"{k}: {v}" for k, v in sorted(actions.items()))
        module_cards += f"""
<div class="card" style="flex:1;min-width:200px">
  <div class="label">{html.escape(mod)} module</div>
  <div class="value" style="font-size:1.5rem;color:#f1f5f9">{len(mod_recs)}</div>
  <div class="sub">Avg conf: {avg} · Escalated: {mod_esc}</div>
  <div class="sub" style="margin-top:.25rem;font-size:.72rem">{html.escape(action_str)}</div>
</div>"""

    rows = _build_pipeline_rows(records)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Code Cleanup Agent — Pipeline Report</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
         background:#0f172a; color:#e2e8f0; line-height:1.5; padding:2rem; }}
  h1 {{ font-size:1.5rem; font-weight:700; color:#f1f5f9; }}
  h2 {{ font-size:1rem; font-weight:600; color:#94a3b8; text-transform:uppercase;
        letter-spacing:.05em; margin-bottom:.75rem; }}
  .subtitle {{ color:#64748b; font-size:.85rem; margin-top:.25rem; }}
  .header {{ margin-bottom:2rem; border-bottom:1px solid #1e293b; padding-bottom:1.25rem; }}
  .grid  {{ display:grid; gap:1rem; grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
            margin-bottom:2rem; }}
  .mflex {{ display:flex; gap:1rem; flex-wrap:wrap; margin-bottom:2rem; }}
  .card  {{ background:#1e293b; border:1px solid #334155; border-radius:.75rem; padding:1.25rem; }}
  .card .label {{ font-size:.75rem; color:#64748b; text-transform:uppercase;
                  letter-spacing:.05em; margin-bottom:.25rem; }}
  .card .value {{ font-size:2rem; font-weight:700; color:#f1f5f9; }}
  .card .sub   {{ font-size:.8rem; color:#94a3b8; margin-top:.25rem; }}
  .charts {{ display:grid; gap:1rem; grid-template-columns:1fr 1fr; margin-bottom:2rem; }}
  .bar-row {{ display:flex; align-items:center; gap:.5rem; margin-bottom:.5rem; font-size:.85rem; }}
  .bar-label {{ width:90px; text-align:right; color:#94a3b8; flex-shrink:0; }}
  .bar-track {{ flex:1; background:#334155; border-radius:4px; height:10px; overflow:hidden; }}
  .bar-fill  {{ height:100%; border-radius:4px; }}
  .bar-val   {{ width:40px; color:#cbd5e1; font-size:.8rem; }}
  .filter-bar {{ display:flex; gap:.5rem; margin-bottom:1rem; flex-wrap:wrap; }}
  .filter-btn {{ padding:.3rem .8rem; border-radius:9999px; border:1px solid #334155;
                 background:#1e293b; color:#94a3b8; cursor:pointer; font-size:.8rem; }}
  .filter-btn.active {{ background:#3b82f6; border-color:#3b82f6; color:#fff; }}
  table {{ width:100%; border-collapse:collapse; font-size:.85rem; }}
  thead th {{ background:#1e293b; color:#94a3b8; font-weight:600; padding:.6rem .75rem;
              text-align:left; position:sticky; top:0; border-bottom:1px solid #334155; }}
  tbody tr {{ border-bottom:1px solid #1e293b; cursor:pointer; }}
  tbody tr:hover {{ background:#1e293b; }}
  tbody tr[data-module].hidden {{ display:none; }}
  td {{ padding:.55rem .75rem; vertical-align:middle; }}
  .conf-wrap {{ display:flex; align-items:center; gap:.5rem; }}
  .conf-bar  {{ width:60px; height:6px; border-radius:3px; background:#334155; flex-shrink:0; }}
  .conf-fill {{ height:100%; border-radius:3px; }}
  .diff-row td  {{ padding:0; }}
  .diff-panel   {{ display:none; background:#020617; border-top:1px solid #1e293b;
                   padding:1rem 1.25rem; }}
  .diff-panel.open {{ display:block; }}
  pre.diff {{ font-family:'Fira Code',Menlo,monospace; font-size:.78rem; line-height:1.6;
              overflow-x:auto; white-space:pre; }}
  .rationale {{ color:#94a3b8; font-size:.8rem; margin-top:.75rem; font-style:italic; }}
  .section {{ margin-bottom:2rem; }}
</style>
</head>
<body>

<div class="header">
  <h1>Enterprise Code Cleanup Agent — Pipeline Report</h1>
  <p class="subtitle">CMU Agentic AI Program · Multi-module run · Generated {ts}</p>
</div>

<div class="grid">
  <div class="card">
    <div class="label">Total Candidates</div>
    <div class="value">{total}</div>
    <div class="sub">{len(modules)} module(s): {", ".join(modules)}</div>
  </div>
  <div class="card">
    <div class="label">Escalated</div>
    <div class="value" style="color:#f59e0b">{escalated}</div>
    <div class="sub">{round(100*escalated/total) if total else 0}% flagged for human review</div>
  </div>
  <div class="card">
    <div class="label">Avg Confidence</div>
    <div class="value" style="color:#3b82f6">{avg_conf}</div>
    <div class="sub">Across all modules</div>
  </div>
</div>

<div class="mflex">
  {module_cards}
</div>

<div class="charts">
  <div class="card">{action_chart}</div>
  <div class="card">{verdict_chart}</div>
</div>

<div class="card section">
  <h2>All Candidates</h2>
  <div class="filter-bar">
    <button class="filter-btn active" onclick="filterModule('all', this)">All</button>
    {_module_filter_buttons(modules)}
  </div>
  <div style="overflow-x:auto">
  <table>
    <thead>
      <tr>
        <th>Module</th>
        <th>File</th>
        <th>Rule</th>
        <th>Action</th>
        <th>Confidence</th>
        <th>RAG Verdict</th>
        <th>Escalated</th>
      </tr>
    </thead>
    <tbody id="pipeline-table">
      {rows}
    </tbody>
  </table>
  </div>
</div>

<script>
  document.querySelectorAll('tr[data-id]').forEach(row => {{
    row.addEventListener('click', () => {{
      const panel = document.getElementById('diff-' + row.dataset.id);
      if (panel) panel.classList.toggle('open');
    }});
  }});

  function filterModule(mod, btn) {{
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    document.querySelectorAll('tr[data-module]').forEach(row => {{
      const show = mod === 'all' || row.dataset.module === mod;
      row.classList.toggle('hidden', !show);
      const diffRow = document.getElementById('diff-' + row.dataset.id)?.closest('tr');
      if (diffRow) diffRow.classList.toggle('hidden', !show);
    }});
  }}
</script>
</body>
</html>"""


def _build_pipeline_rows(records: list[dict]) -> str:
    out = []
    for i, r in enumerate(records):
        module   = r["module"]
        fname    = r["fname"]
        line     = r["line"]
        rule     = r["rule"]
        action   = r["action"]
        conf     = r["confidence"]
        verdict  = r["verdict"]
        escalated = r["escalated"]
        diff     = r["diff"]
        rationale = r["rationale"]
        action_statement = r["action_statement"]
        escalation_reason = r["escalation_reason"]

        pred_color = ACTION_COLOR.get(action,  "#94a3b8")
        vrd_color  = VERDICT_COLOR.get(verdict, "#6b7280")
        cc = _conf_color(conf)
        conf_pct = int(conf * 100)
        esc_badge = (_badge("Escalated", "#854d0e", "#fef08a") if escalated
                     else _badge("Auto", "#1e3a5f", "#93c5fd"))

        diff_html = _diff_block(diff, action)

        meta_parts = []
        if action_statement:
            meta_parts.append(
                f'<div style="color:#e2e8f0;font-size:.82rem;margin-bottom:.5rem">'
                f'{html.escape(action_statement)}</div>'
            )
        if rationale:
            meta_parts.append(
                f'<div class="rationale">{html.escape(str(rationale)[:400])}</div>'
            )
        if escalated and escalation_reason:
            meta_parts.append(
                f'<div style="color:#fbbf24;font-size:.78rem;margin-top:.4rem">'
                f'Escalated: {html.escape(escalation_reason)}</div>'
            )
        rat_html = "\n".join(meta_parts)

        mod_color = "#3b82f6" if module == "core" else "#a855f7"

        out.append(f"""
<tr data-id="{i}" data-module="{html.escape(module)}">
  <td>{_badge(module, mod_color)}</td>
  <td style="font-family:monospace;font-size:.8rem">{html.escape(fname)}:{line}</td>
  <td style="color:#94a3b8;font-size:.78rem">{html.escape(rule)}</td>
  <td>{_badge(action, pred_color)}</td>
  <td>
    <div class="conf-wrap">
      <div class="conf-bar"><div class="conf-fill" style="width:{conf_pct}%;background:{cc}"></div></div>
      <span style="font-size:.8rem;color:{cc}">{conf:.2f}</span>
    </div>
  </td>
  <td>{_badge(verdict, vrd_color)}</td>
  <td>{esc_badge}</td>
</tr>
<tr data-module="{html.escape(module)}">
  <td colspan="7" style="padding:0">
    <div class="diff-panel" id="diff-{i}">
      <pre class="diff">{diff_html}</pre>
      {rat_html}
    </div>
  </td>
</tr>""")
    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser(description="Generate HTML report")
    parser.add_argument("--mode", choices=["eval", "pipeline"], default="eval",
                        help="eval: accuracy report from evaluate.py; pipeline: multi-module run report")
    args = parser.parse_args()

    if args.mode == "pipeline":
        records = _load_pipeline_results()
        if not records:
            print("ERROR: No pipeline_results_*.json files found in output/. Run pipeline.py first.")
            return
        html_content = build_pipeline_html(records)
        out_file = OUTPUT_DIR / "pipeline_report.html"
        out_file.write_text(html_content, encoding="utf-8")
        modules = sorted(set(r["module"] for r in records))
        print(f"Pipeline report written to: {out_file}")
        print(f"  {len(records)} candidates across {len(modules)} module(s): {', '.join(modules)}")
        return

    # default: eval mode
    if not RESULTS_FILE.exists():
        print(f"ERROR: {RESULTS_FILE} not found. Run evaluate.py first.")
        return

    with open(RESULTS_FILE) as f:
        results = json.load(f)

    m = compute_metrics(results)
    html_content = build_html(results, m)

    REPORT_FILE.write_text(html_content, encoding="utf-8")
    print(f"Report written to: {REPORT_FILE}")
    print(f"  {m['total']} candidates · {m['accuracy']}% accuracy · {m['fp_rate']}% FP rate")


if __name__ == "__main__":
    main()
