#!/usr/bin/env python3
"""Fail if code decides a tie by iteration order, or tallies votes after dropping
the ones that failed.

    ./check-aggregation.py [path ...]      # exits non-zero on any hit

DESIGN §0.1 says a failed check is not a passed check: wherever a guard can fail,
the failure must land on the cautious side of the decision it guards. That
principle was written down, and then violated twice more by someone who had read
it. Prose does not enforce; this does.

The two shapes, both from real bugs in this repository's own tooling:

  TIE BY INSERTION ORDER
      counts.most_common(1)[0]
    Counter.most_common breaks a tie by first-seen order. A panel of two models
    that disagreed on nine rows resolved every one of them to whichever model
    happened to be listed first in the array, and reported "23 not_addressed" —
    a true count containing nine coin flips.

  TALLY AFTER DROPPING FAILURES
      [v for v in votes if v.get("verdict") in VALID]
    Filtering errors out and then asking whether the survivors agree is how "panel
    split on 0 rows" got printed when two of three models had errored and the
    third agreed with itself. An error is not a vote; dropping it silently
    converts thin evidence into unanimity.

WHAT THIS CANNOT DO. It is a text scan, not a type checker. It cannot see that a
tie was handled three lines below, so it accepts an explicit acknowledgement on
the same line or the line above:

    # aggregation-ok: tie handled below
    top, _ = counts.most_common(1)[0]

That escape hatch is deliberate and is the point: the goal is to force the
question to be ASKED, not to ban a function. A reviewer seeing the marker knows
someone considered it. A reviewer seeing bare most_common knows nobody did.
"""

import os
import re
import sys

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "fixtures"}
MARKER = re.compile(r"#\s*aggregation-ok\b")

PATTERNS = [
    (re.compile(r"\.most_common\(\s*1\s*\)|\.most_common\(\s*\)\s*\[\s*0\s*\]"),
     "tie decided by insertion order",
     "most_common breaks ties by first-seen order. Decide what a tie MEANS — "
     "contested, escalate, or a documented precedence — and say so."),
    (re.compile(r"""(?x)
        (?:for\s+[\w,\s]+?\s+in\s+|=\s*[\[{])   # list OR dict comprehension,
                                                # single var OR tuple unpacking:
                                                # the first version matched only
                                                # `= [` and `for x in`, so it
                                                # reported clean on the real
                                                # instance, a dict comprehension
                                                # over `for m, v in`.
        [^\n]*\bif\b[^\n]*
        (?:\bin\s+(?:VALID|ORDER|SCALE|ALLOWED)\b
           |\bnot\s+None\b
           |!=\s*["']error["']
           |\.get\(["']verdict["']\)\s*(?:in|is\s+not)\b)
     """),
     "votes filtered before tallying",
     "dropping failed answers and then asking whether the rest agree turns thin "
     "evidence into unanimity. Count how many actually answered and report it."),
]


def sources(paths):
    for path in paths:
        if os.path.isfile(path):
            yield path
            continue
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in sorted(files):
                if name.endswith(".py") and name != os.path.basename(__file__):
                    yield os.path.join(root, name)


def main():
    paths = sys.argv[1:] or [os.path.dirname(os.path.abspath(__file__))]
    hits = []
    for path in sources(paths):
        try:
            lines = open(path, encoding="utf-8").read().splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        # LOGICAL lines, not a fixed window. Three attempts here, each failing
        # a different way, which is the argument for testing a checker against a
        # known answer rather than reading it:
        #   line-by-line   missed a comprehension whose filter sat on line two
        #   3-line window  joined UNRELATED neighbours, so `terms = [t.lower()
        #                  for t in ...]` matched because an `if` sat nearby
        #   this           accumulates until brackets balance, so a statement is
        #                  a statement and nothing else is glued to it
        statements = []
        buf, start, depth = "", 1, 0
        for n, line in enumerate(lines, 1):
            code = line.split("#")[0] if not line.lstrip().startswith("#") else ""
            if not buf:
                start = n
            buf += " " + code.strip()
            depth += code.count("(") + code.count("[") + code.count("{")
            depth -= code.count(")") + code.count("]") + code.count("}")
            if depth <= 0:
                if buf.strip():
                    statements.append((start, buf.strip()))
                buf, depth = "", 0
        for n, text in statements:
            near = " ".join(lines[max(0, n - 2):n + 1])
            if MARKER.search(near):
                continue
            for pattern, what, why in PATTERNS:
                if pattern.search(text):
                    hits.append((path, n, what, why, text[:76]))

    if not hits:
        print("  clean — no unguarded tie-breaks or filtered tallies")
        return 0

    print(f"  {len(hits)} site(s) where a failure may not land on the cautious "
          f"side (DESIGN §0.1)\n")
    for path, n, what, why, text in hits:
        rel = os.path.relpath(path)
        print(f"  {rel}:{n}  {what}")
        print(f"      {text}")
        print(f"      {why}")
        print(f"      If it is already handled, mark it: # aggregation-ok: <how>\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
