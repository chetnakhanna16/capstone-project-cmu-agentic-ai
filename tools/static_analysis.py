import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from dataclasses import dataclass, field
from typing import Literal

JENKINS_ROOT = Path(__file__).parent.parent / "jenkins"
OUTPUT_DIR = Path(__file__).parent.parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

PMD_NAMESPACE = "{http://pmd.sourceforge.net/report/2.0.0}"


@dataclass
class Candidate:
    file: str
    line: int
    rule: str
    category: str
    description: str
    severity: Literal["HIGH", "MEDIUM", "LOW"]
    source: Literal["pmd", "spotbugs"]
    score: float = 0.0  # higher = higher priority for cleanup


def _severity_from_priority(priority: int) -> Literal["HIGH", "MEDIUM", "LOW"]:
    if priority <= 2:
        return "HIGH"
    if priority <= 3:
        return "MEDIUM"
    return "LOW"


def _score(severity: str, source: str) -> float:
    base = {"HIGH": 3.0, "MEDIUM": 2.0, "LOW": 1.0}[severity]
    # dead code from PMD is directly actionable; SpotBugs needs more context
    multiplier = 1.2 if source == "pmd" else 1.0
    return round(base * multiplier, 2)


def run_pmd(module: str = "core") -> list[Candidate]:
    src = JENKINS_ROOT / module / "src/main/java"
    out = OUTPUT_DIR / f"pmd-{module}.xml"

    rules = ",".join([
        "category/java/bestpractices.xml/UnusedPrivateField",
        "category/java/bestpractices.xml/UnusedLocalVariable",
        "category/java/design.xml/UselessOverridingMethod",
        "category/java/codestyle.xml/UnnecessaryImport",
        "category/java/bestpractices.xml/UnusedPrivateMethod",
        "category/java/bestpractices.xml/UnusedFormalParameter",
    ])

    subprocess.run(
        ["pmd", "check", "-d", str(src), "-R", rules, "-f", "xml", "-r", str(out), "--no-fail-on-violation"],
        capture_output=True,
    )

    candidates = []
    if not out.exists():
        return candidates

    tree = ET.parse(out)
    # PMD 7: filename is on the <file> parent element, not on each <violation>
    for file_el in tree.getroot().findall(f".//{PMD_NAMESPACE}file"):
        raw_path = file_el.get("name", "")
        src_path = raw_path.replace(str(JENKINS_ROOT) + "/", "") if raw_path else ""
        if not src_path:
            continue
        for v in file_el.findall(f"{PMD_NAMESPACE}violation"):
            sev = _severity_from_priority(int(v.get("priority", "3")))
            c = Candidate(
                file=src_path,
                line=int(v.get("beginline", 0)),
                rule=v.get("rule", ""),
                category="DEAD_CODE",
                description=(v.text or "").strip(),
                severity=sev,
                source="pmd",
            )
            c.score = _score(c.severity, "pmd")
            candidates.append(c)

    return candidates


def run_spotbugs(module: str = "core") -> list[Candidate]:
    module_dir = JENKINS_ROOT / module
    out = module_dir / "target/spotbugsXml.xml"

    if not out.exists():
        subprocess.run(
            ["mvn", "spotbugs:spotbugs", "-q", "-Dspotbugs.effort=Max", "-Dspotbugs.threshold=Medium"],
            cwd=module_dir,
            capture_output=True,
        )

    candidates = []
    if not out.exists():
        return candidates

    tree = ET.parse(out)
    for bug in tree.getroot().findall(".//BugInstance"):
        category = bug.get("category", "")
        # focus on style/bad-practice which maps to cleanup candidates
        if category not in ("STYLE", "BAD_PRACTICE"):
            continue

        src_line = bug.find(".//SourceLine[@primary='true']") or bug.find(".//SourceLine")
        file_path = ""
        line_num = 0
        if src_line is not None:
            file_path = src_line.get("sourcepath", "")
            line_num = int(src_line.get("start", 0))

        long_msg = bug.find("LongMessage")
        description = long_msg.text.strip() if long_msg is not None and long_msg.text else bug.get("type", "")

        priority = int(bug.get("priority", "3"))
        sev = _severity_from_priority(priority)
        c = Candidate(
            file=file_path,
            line=line_num,
            rule=bug.get("type", ""),
            category=category,
            description=description,
            severity=sev,
            source="spotbugs",
        )
        c.score = _score(c.severity, "spotbugs")
        candidates.append(c)

    return candidates


def run_jdeps(module: str = "core") -> dict:
    classes_dir = JENKINS_ROOT / module / "target/classes"
    result = subprocess.run(
        ["jdeps", "--multi-release", "21", "-summary", str(classes_dir)],
        capture_output=True,
        text=True,
    )
    deps = {}
    for line in result.stdout.splitlines():
        if "->" in line:
            parts = line.split("->")
            src = parts[0].strip()
            tgt = parts[1].strip()
            deps.setdefault(src, []).append(tgt)
    return deps


def get_ranked_candidates(module: str = "core") -> list[Candidate]:
    pmd = run_pmd(module)
    sb = run_spotbugs(module)
    all_candidates = pmd + sb
    return sorted(all_candidates, key=lambda c: c.score, reverse=True)
