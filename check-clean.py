#!/usr/bin/env python3
"""Fail if anything in this tree names the engagement it was built on.

    ./check-clean.py            # exits non-zero on any hit

This toolkit exists because a client forbade sending their documents to a hosted
model. Publishing it with that client's vocabulary embedded would be the worst
possible advertisement for the judgement it is meant to demonstrate — so the
check runs in CI, not once by hand. A one-time scrub is undone by the next
commit that pastes in a real example; this is the thing that stops it.

Two categories, and the second is the one a manual review misses:

  IDENTIFIER   names, acronyms, document slugs. Obvious once you look.
  VOCABULARY   the engagement's coined terms, quoted in comments AS EVIDENCE
               for why a detector exists. These read as good technical writing,
               which is exactly why they survive a read-through.

Adding a term here is cheap. Removing one needs a reason, because every entry is
here as a result of a real audit finding.
"""

import os
import re
import sys

# Whole words only: short acronyms appear inside ordinary words. "REDACTED-04" lives
# inside "encoding", which is how a first audit of this tree produced 28 false
# positives and nearly buried the 2 real ones.
IDENTIFIERS = [
    "REDACTED-01", "REDACTED-02", "REDACTED-03", "REDACTED-04", "REDACTED-05", "REDACTED-06", "REDACTED-07",
]

# Substring, case-insensitive. Phrases coined by the engagement; none of them
# appears in the synthetic fixture, which is what makes them identifying.
VOCABULARY = [
    "REDACTED-08", "REDACTED-09", "REDACTED-10",
    "REDACTED-11", "REDACTED-12", "REDACTED-13", "REDACTED-14",
    "REDACTED-15", "REDACTED-16", "REDACTED-17",
    "REDACTED-18", "REDACTED-19", "REDACTED-20",
]

# Document slugs and artefact names, including the ones that are argument
# DEFAULTS rather than comments — a client workbook's sheet name baked into the
# public entry point is a leak that no amount of comment-reading finds.
ARTEFACTS = [
    "REDACTED-21" "REDACTED-22", "REDACTED-21" "REDACTED-23", "REDACTED-21" "REDACTED-24", "REDACTED-21" "REDACTED-25", "REDACTED-21" "REDACTED-26",
    "REDACTED-27" "REDACTED-23", "REDACTED-27" "REDACTED-24", "REDACTED-28" "REDACTED-29", "REDACTED-28" "REDACTED-30", "REDACTED-31" "REDACTED-32",
    "REDACTED-33" "REDACTED-34", "REDACTED-35" "REDACTED-36", "REDACTED-37" "REDACTED-38",
    "REDACTED-39" "REDACTED-40", "REDACTED-41" "REDACTED-42",
    "REDACTED-43" "REDACTED-44",
]

# Real people. A default signature on client-facing output is not a comment and
# will not be found by reading prose.
PEOPLE = ["REDACTED-45"]

SKIP_DIRS = {".git", ".private", "__pycache__", "source", "parsed",
             ".dossier-cache"}
SCAN_EXT = {".py", ".md", ".yaml", ".yml", ".txt", ".toml", ".cfg", ".json"}


def files(root):
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            path = os.path.join(base, name)
            if name == "check-clean.py":
                continue
            if os.path.splitext(name)[1] in SCAN_EXT or name == "dossier":
                yield path


def main():
    root = os.path.dirname(os.path.realpath(__file__))
    word = re.compile(r"\b(" + "|".join(map(re.escape, IDENTIFIERS)) + r")\b")
    people = re.compile(r"\b(" + "|".join(map(re.escape, PEOPLE)) + r")\b")
    phrases = [(t, re.compile(re.escape(t), re.I))
               for t in VOCABULARY + ARTEFACTS]

    hits = []
    for path in files(root):
        try:
            lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
        except OSError:
            continue
        rel = os.path.relpath(path, root)
        for number, line in enumerate(lines, 1):
            for match in word.finditer(line):
                hits.append((rel, number, "IDENTIFIER", match.group(1), line))
            for match in people.finditer(line):
                hits.append((rel, number, "PERSON", match.group(1), line))
            for term, pattern in phrases:
                if pattern.search(line):
                    kind = "ARTEFACT" if term in ARTEFACTS else "VOCABULARY"
                    hits.append((rel, number, kind, term, line))

    if not hits:
        print("clean — no engagement identifiers, vocabulary or artefacts found")
        return 0

    by_kind = {}
    for hit in hits:
        by_kind.setdefault(hit[2], []).append(hit)
    print(f"{len(hits)} occurrence(s) that must not be published:\n")
    for kind in ("PERSON", "IDENTIFIER", "ARTEFACT", "VOCABULARY"):
        found = by_kind.get(kind, [])
        if not found:
            continue
        print(f"-- {kind} ({len(found)}) --")
        for rel, number, _, term, line in found:
            print(f"  {rel}:{number}  {term!r}")
            print(f"      {line.strip()[:96]}")
        print()
    print(f"{len(hits)} hit(s). Re-ground every example in fixtures/floodtwin, "
          f"which has its own\ninvented vocabulary for exactly this purpose.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
