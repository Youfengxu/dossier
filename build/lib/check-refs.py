#!/usr/bin/env python3
"""Fail if anything cites a DESIGN section that does not exist.

    ./check-refs.py             # exits non-zero on any dangling reference

`DESIGN.md` §1 names cross-reference rot as one of three failures worth building
a toolkit around. This repository had four of its own: a `§A` that never
existed, a `design 7.6, layer L3` from a numbering scheme that predates the
current document, a `DESIGN 5.5` pointing at a seven-row table, and a README
sending newcomers to a section title that had been renamed. Each was written
true and went stale when the document moved underneath it.

That is exactly the failure mode `sweep.py xref` was built to catch in a
client's deliverable, and nothing was pointing it at this repository. So:

NOT every `§n` is a DESIGN reference. Comments legitimately quote the section
numbering of a document under review — "a comment on §8.10 has no anchor" is
about a reviewed deliverable, not about DESIGN.md. Only citations that name
DESIGN explicitly are checked, which is the distinction a naive grep misses and
the reason this is a script rather than a one-line CI step.
"""

import os
import pathlib
import re
import sys

# A reference counts only when DESIGN is named: "DESIGN §3.4", "DESIGN.md §4a",
# "design 3.11". A bare "§13" is a document section, not a claim about this repo.
# The backtick is why ARCHITECTURE.md was invisible. It writes every citation as
# `DESIGN` §3.4 — markdown code style — and a pattern that ran DESIGN straight
# into the section number matched none of its eighteen references, including a
# deliberately planted dangling one. The document with no automated checking was
# the document the checker could not see, which is the worst way to not have a
# check: it looks like you have one.
CITATION = re.compile(r"\bDESIGN(?:\.md)?[`\s]*(?:§\s*)?([0-9]+(?:\.[0-9]+)*[a-z]?)\b"
                      r"|\bDESIGN(?:\.md)?[`\s]*§\s*([A-Z])\b", re.I)
HEADING = re.compile(r"^#{2,4}\s+(§?\s*)?([0-9]+(?:\.[0-9]+)*[a-z]?|[A-Z])\b", re.M)


def main():
    root = pathlib.Path(os.path.dirname(os.path.realpath(__file__)))
    design = (root / "DESIGN.md").read_text(encoding="utf-8")
    sections = {m.group(2) for m in HEADING.finditer(design)}

    # Inside DESIGN itself a bare "§3.4" IS a self-reference, so check those too.
    self_refs = re.compile(r"§\s*([0-9]+(?:\.[0-9]+)*[a-z]?|[A-Z])\b")

    dangling = []
    for path in sorted(root.glob("*.py")) + [root / "DESIGN.md", root / "README.md",
                                             root / "ARCHITECTURE.md",
                                             root / "dossier"]:
        # This file quotes the dangling references it was written to catch, so
        # scanning itself reports them forever. The scrub checker needed the
        # same exemption for the same reason: a tool that names bad examples
        # will always contain bad examples.
        if not path.exists() or path.name == "check-refs.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8",
                                                     errors="replace").splitlines(), 1):
            found = {m.group(1) or m.group(2) for m in CITATION.finditer(line)}
            if path.name == "DESIGN.md":
                found |= {m.group(1) for m in self_refs.finditer(line)}
            for ref in found:
                if ref and ref not in sections:
                    dangling.append((path.name, number, ref, line.strip()[:88]))

    print(f"DESIGN.md has {len(sections)} numbered sections")
    if not dangling:
        print("clean — every DESIGN reference resolves")
        return 0
    print(f"\n{len(dangling)} dangling reference(s):\n")
    for name, number, ref, line in dangling:
        print(f"  {name}:{number}  cites §{ref}, which does not exist")
        print(f"      {line}")
    return 1


if __name__ == "__main__":
    if '-h' in sys.argv[1:] or '--help' in sys.argv[1:]:
        print(__doc__.strip())
        raise SystemExit(0)
    sys.exit(main())
