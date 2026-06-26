"""
Generates accurate unified diffs from actual source files.
Used by the Recommendation Agent so diffs are always grounded
in real code, not hallucinated by the LLM.
"""

import difflib
import re
from pathlib import Path

JENKINS_ROOT = Path(__file__).parent.parent / "jenkins"
CONTEXT_LINES = 3


def _read(file_rel: str) -> list[str]:
    path = JENKINS_ROOT / file_rel
    if not path.exists():
        return []
    return path.read_text(errors="ignore").splitlines(keepends=True)


def _unified_diff(original: list[str], modified: list[str], file_rel: str) -> str:
    diff = list(difflib.unified_diff(
        original, modified,
        fromfile=f"a/{file_rel}",
        tofile=f"b/{file_rel}",
        n=CONTEXT_LINES,
    ))
    return "".join(diff) if diff else "(no change)"


def diff_remove_line(file_rel: str, line_number: int) -> str:
    """Generate diff for removing a single line (unused variable/field)."""
    lines = _read(file_rel)
    if not lines or line_number < 1 or line_number > len(lines):
        return f"(could not read {file_rel})"

    idx = line_number - 1
    modified = lines[:idx] + lines[idx + 1:]
    return _unified_diff(lines, modified, file_rel)


def diff_remove_field(file_rel: str, line_number: int) -> str:
    """
    Remove an unused private field — also removes its preceding Javadoc
    comment block if present.
    """
    lines = _read(file_rel)
    if not lines:
        return f"(could not read {file_rel})"

    idx = line_number - 1
    start = idx

    # walk backwards to include any preceding comment block
    i = idx - 1
    while i >= 0:
        stripped = lines[i].strip()
        if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/**") or stripped == "":
            start = i
            i -= 1
        else:
            break

    modified = lines[:start] + lines[idx + 1:]
    return _unified_diff(lines, modified, file_rel)


def diff_deprecate_method(file_rel: str, line_number: int) -> str:
    """
    Add @Deprecated annotation above a method/field at line_number.
    Finds the correct insertion point (before any existing annotations).
    """
    lines = _read(file_rel)
    if not lines:
        return f"(could not read {file_rel})"

    idx = line_number - 1
    insert_at = idx

    # walk backwards past existing annotations to find the right spot
    i = idx - 1
    while i >= 0 and lines[i].strip().startswith("@"):
        insert_at = i
        i -= 1

    indent = re.match(r"(\s*)", lines[insert_at]).group(1)
    deprecated_line = f"{indent}@Deprecated\n"

    modified = lines[:insert_at] + [deprecated_line] + lines[insert_at:]
    return _unified_diff(lines, modified, file_rel)


def diff_remove_method(file_rel: str, line_number: int) -> str:
    """
    Remove an entire method starting at line_number.
    Detects method boundaries by matching braces.
    """
    lines = _read(file_rel)
    if not lines:
        return f"(could not read {file_rel})"

    idx = line_number - 1
    start = idx

    # find opening brace
    brace_count = 0
    end = idx
    found_open = False
    for i in range(idx, min(idx + 200, len(lines))):
        for ch in lines[i]:
            if ch == "{":
                brace_count += 1
                found_open = True
            elif ch == "}":
                brace_count -= 1
        if found_open and brace_count == 0:
            end = i
            break

    modified = lines[:start] + lines[end + 1:]
    return _unified_diff(lines, modified, file_rel)


def generate_diff(file_rel: str, line_number: int, action: str, rule: str) -> str:
    """Main entry point — pick the right diff strategy based on action + rule."""
    if action == "REMOVE":
        if rule == "UnusedPrivateMethod":
            return diff_remove_method(file_rel, line_number)
        elif rule == "UnusedPrivateField":
            return diff_remove_field(file_rel, line_number)
        else:
            return diff_remove_line(file_rel, line_number)
    elif action == "DEPRECATE":
        return diff_deprecate_method(file_rel, line_number)
    else:
        return ""  # REFACTOR and KEEP diffs require LLM judgment
