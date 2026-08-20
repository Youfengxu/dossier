#!/usr/bin/env python3
"""Recover citations that were rejected by too strict a locator grammar.

    ./repair-cites.py --project . --doc deliverable-v2 out/*.jsonl

The first version of assess.py required a citation to look like "120-134" and
rejected a bare "67". A single line is a perfectly ordinary thing to cite, and
10 of the first 11 rejections were that and nothing else — the tool discarding
good evidence and reporting it as the model's fault.

So this re-reads what was rejected, keeps whatever now resolves against the
frozen document, and leaves genuinely bad locators rejected with their reason.
No inference is re-run: the citations were already paid for, and a locator is
checked against the text rather than trusted, so recovering one costs nothing in
rigour. Rerunning is safe — a repaired record is not re-processed.
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from llm import load_doc                                       # noqa: E402

RANGE = re.compile(r"^\s*(\d+)\s*(?:[-:–—]\s*(\d+))?\s*$")
WHY = re.compile(r"\s*\(([^)]*)\)\s*$")
MAX_SPAN = 60


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", required=True)
    p.add_argument("--doc", required=True)
    p.add_argument("files", nargs="+")
    args = p.parse_args()

    _meta, lines = load_doc(os.path.abspath(os.path.expanduser(args.project)),
                            args.doc)
    last = len(lines) - 1

    for path in args.files:
        if not os.path.exists(path):
            print(f"  {path}: missing"); continue
        rows = [json.loads(l) for l in open(path, encoding="utf-8")]
        moved = touched = 0
        for r in rows:
            if r.get("cites_repaired") or not r.get("cites_rejected"):
                continue
            keep, recovered = [], []
            for entry in r["cites_rejected"]:
                raw = WHY.sub("", entry)
                m = RANGE.match(raw)
                if not m:
                    keep.append(entry); continue
                a = int(m.group(1))
                b = int(m.group(2)) if m.group(2) else a
                if a < 0 or b > last or a > b:
                    keep.append(f"{raw} (outside 0-{last})"); continue
                if b - a > MAX_SPAN:
                    keep.append(f"{raw} (too wide ({b-a} lines))"); continue
                recovered.append(f"{args.doc}:{a}-{b}")
            if recovered:
                r["cites"] = r.get("cites", []) + recovered
                moved += len(recovered)
            r["cites_rejected"] = keep
            r["cites_repaired"] = True
            touched += 1
        if touched:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r) + "\n")
            os.replace(tmp, path)
            print(f"  {os.path.basename(path)}: {moved} citation(s) recovered "
                  f"across {touched} row(s)")
        else:
            print(f"  {os.path.basename(path)}: nothing to repair")
    return 0


if __name__ == "__main__":
    sys.exit(main())
