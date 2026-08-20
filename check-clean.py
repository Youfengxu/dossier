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

import hashlib
import os
import re
import sys

# THE TERMS ARE STORED AS DIGESTS, NOT WORDS.
#
# This file screens a public repository for a client's vocabulary. Listing that
# vocabulary in the file that screens for it publishes exactly what it protects —
# a lock whose key is taped to the door. So denylist.txt holds sha256 of each
# normalised term with its word count, and the plaintext lives in .private/,
# which is gitignored.
#
# The cost is that a hit cannot name the term it matched. It names the file, the
# line, the category and the matched span, which is enough to fix and not enough
# to leak. Regenerate with ./make-denylist.py after editing the private source.
DENYLIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "denylist.txt")


def normalise(text):
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def load_denylist(path=DENYLIST):
    """{word_count: {digest: category}} — grouped by length so the scanner knows
    which window sizes to hash."""
    by_length = {}
    if not os.path.exists(path):
        sys.exit(f"{path} is missing. Regenerate it with ./make-denylist.py")
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        category, count, digest = line.split("\t")
        by_length.setdefault(int(count), {})[digest] = category
    return by_length


def hits_in(text, by_length):
    """Every denied term in `text`, as (category, matched span).

    Windows of normalised words are hashed and compared. A phrase of N words is
    only ever compared against N-word windows, so the cost is one pass per
    distinct term length rather than one per term.
    """
    found = []
    for line_no, line in enumerate(text.splitlines(), 1):
        words = normalise(line).split()
        if not words:
            continue
        for size, digests in by_length.items():
            for i in range(len(words) - size + 1):
                window = " ".join(words[i:i + size])
                digest = hashlib.sha256(window.encode()).hexdigest()
                category = digests.get(digest)
                if category:
                    found.append((line_no, category, window))
    return found


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
    by_length = load_denylist()
    label = {"IDENTIFIERS": "IDENTIFIER", "PEOPLE": "PERSON",
             "ARTEFACTS": "ARTEFACT", "VOCABULARY": "VOCABULARY"}

    hits = []
    for path in files(root):
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        rel = os.path.relpath(path, root)
        for number, category, span in hits_in(text, by_length):
            line = text.splitlines()[number - 1]
            hits.append((rel, number, label.get(category, category), span, line))

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
    if '-h' in sys.argv[1:] or '--help' in sys.argv[1:]:
        print(__doc__.strip())
        raise SystemExit(0)
    sys.exit(main())
