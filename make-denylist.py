#!/usr/bin/env python3
"""Regenerate the hashed denylist from the private plaintext source.

    ./make-denylist.py

`check-clean.py` screens this tree for a client's vocabulary. Listing that
vocabulary inside the file that screens for it publishes exactly what it
protects — a lock with the key taped to the door, and the reason this repository
could not be made public even after every other trace was removed.

So the words live in `.private/denylist-source.txt`, which is gitignored, and
only their digests are committed. Adding a term means editing the private file
and running this.

The normalisation is deliberately lossy: lowercase, and every run of non-alphanumeric
characters collapsed to a single space. That makes `Sensor - Fabric`,
`Sensor Fabric` and `sensor_fabric` one term, which matters because the previous
exact-substring matcher required the punctuation to line up and three real leaks
survived in this tree because of it.
"""

import hashlib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(HERE, ".private", "denylist-source.txt")
TARGET = os.path.join(HERE, "denylist.txt")

HEADER = [
    "# Hashed denylist. The terms this screens for are the very thing it exists",
    "# to keep out of a public repository, so they are stored as digests: the",
    "# gate still works and the vocabulary is not published.",
    "# Regenerate from .private/denylist-source.txt with ./make-denylist.py",
    "# format: category<TAB>word_count<TAB>sha256(normalised term)",
]


def normalise(text):
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def main():
    if not os.path.exists(SOURCE):
        sys.exit(f"{SOURCE} is missing.\n\n"
                 "It is gitignored on purpose — a fresh clone has the digests but\n"
                 "not the words. To add a term you need the private source; to\n"
                 "merely run the check you do not.")
    rows, seen = [], set()
    for line in open(SOURCE, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        category, _, term = line.partition("\t")
        key = normalise(term)
        if not key or key in seen:
            continue
        seen.add(key)
        rows.append((category.strip(), len(key.split()),
                     hashlib.sha256(key.encode()).hexdigest()))

    with open(TARGET, "w", encoding="utf-8") as handle:
        handle.write("\n".join(HEADER) + "\n")
        for category, count, digest in rows:
            handle.write(f"{category}\t{count}\t{digest}\n")

    lengths = sorted({c for _, c, _ in rows})
    print(f"  {len(rows)} terms hashed into {os.path.basename(TARGET)}")
    print(f"  word lengths present: {lengths}")
    print(f"  the plaintext stays in {os.path.relpath(SOURCE, HERE)}, which is gitignored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
