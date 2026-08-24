#!/usr/bin/env python3
"""Turning an address into a place in a document, in one file.

`ADAPTERS.md` calls this half of the contract `locate`. It existed already, four
times over, and the copies had form:

    RANGE            three copies, in assess.py, converse.py and repair-cites.py.
                     Two of them accepted a bare "67" and one did not, and the one
                     that did not threw away ten of the first eleven citations in a
                     run and reported it as the model's fault. It was fixed in
                     assess.py, missed in converse.py, and found there by the first
                     real question anyone asked of it.

    nearest_heading  two copies, line for line the same walk, differing only in
                     whether they returned the line number beside the heading. Both
                     used to clamp, so an address past the end of a document
                     returned the LAST heading and an empty search phrase, and the
                     delivered report read: search for "". Both were fixed.

    searchable       two copies, identical behaviour, one written as a one-liner.

The pattern is the argument for this module. A checker duplicated across tools is
not a checker; it is several checkers that happen to agree today, and the way you
find out they have stopped agreeing is a reviewer reading a citation that points
at the wrong text.
"""

import re

# A citation as a reader would actually write one. A single line is an ordinary
# thing to cite, so a bare "67" is a locator, and en- and em-dashes are what
# models emit for a range because that is what documents use.
RANGE = re.compile(r"^\s*(\d+)\s*(?:[-:–—]\s*(\d+))?\s*$")

# A heading as it appears in extracted prose: optionally numbered, initial
# capital, no sentence punctuation, and short. A heuristic, and characterised as
# one in tests/test_checkers.py rather than asserted to be correct — in
# table-dense material it finds a region label, not a section.
HEADING = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?[A-Z][^.!?]{2,78}$")

WALK_BACK = 400


def bounds(text):
    """(start, end) for a citation, or None. A bare number is a one-line range."""
    found = RANGE.match(text or "")
    if not found:
        return None
    start = int(found.group(1))
    return start, int(found.group(2)) if found.group(2) else start


def nearest_heading(lines, n):
    """The closest plausible heading at or above line n, with its line number.

    NO CLAMPING. `min(n, len(lines) - 1)` meant an address up to 400 lines past
    the end of the document returned the document's last heading beside an empty
    search phrase, and the delivered report read: search for "". No error, no
    warning, exit 0 — a stale locator presenting as a finding.
    """
    if not 0 <= n < len(lines):
        return None, None
    for i in range(n, max(-1, n - WALK_BACK), -1):
        line = lines[i].strip()
        if not line or len(line) > 80:
            continue
        if HEADING.match(line) and not line.endswith((",", ";", ":")):
            if 1 < len(line.split()) < 14:
                return line, i
    return None, None


def heading_only(lines, n):
    """`nearest_heading` for callers that want the text and not the line."""
    return nearest_heading(lines, n)[0]


def searchable(text, words=9):
    """A phrase distinctive enough to Ctrl-F in the source document."""
    return " ".join(" ".join(text.split()).split(" ")[:words]).strip(" ,;:.")


if __name__ == "__main__":
    import sys
    print(__doc__.strip())
    sys.exit(0)


def quoted(text, limit=None):
    """Render a cited passage so a reviewer can still JUDGE it.

    Both renderers used to write `" ".join(text.split())`. For prose that is
    right — a quote pulled from a document arrives wrapped at whatever width the
    source used, and reflowing it is what makes it readable in a report.

    Applied to a table it destroys the evidence while appearing to include it:

        | ID | Component | Responsibility | |---|---|---| | RO | Runtime
        Orchestrator | Execution scheduling, tick advancement, state commit | ...

    Every character is present and nothing can be checked, because a table's
    meaning is in the alignment of cell to column and that is exactly what the
    collapse discards. Five of the seventeen quotes in the floodtwin fixture are
    tables, and all five reached the report in that state — evidence that is
    decorative in a subtler way than evidence that is missing, and harder to
    notice precisely because the text is right there.

    So: reflow prose, keep tables as tables. Returns (rendered, is_block) — a
    block has to be emitted on its own lines rather than after a bold label.
    """
    if not (text or "").strip():
        return "", False
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    # A table is two or more pipe-delimited rows. One pipe is prose containing a
    # pipe; a single row has no alignment to preserve.
    rows = [ln for ln in lines if ln.count("|") >= 2]
    if len(rows) >= 2:
        if limit and len(rows) > limit:
            rows = rows[:limit] + [f"| … {len(lines) - limit} further row(s) |"]
        return "\n".join(rows), True
    flat = " ".join(text.split())
    return flat, False
