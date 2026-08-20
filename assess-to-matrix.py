#!/usr/bin/env python3
"""Write assess.py results into the matrix, one column per evaluator.

    ./assess-to-matrix.py --xlsx matrix.xlsx --sheet "Comments" \
        --eval "Coder-Next=out/coder.jsonl" --eval "qwen3.6=out/qwen.jsonl" \
        --first-col J --out matrix-assessed.xlsx

Each cell reads as a person would write it: a one-word conclusion, a full stop,
the reasoning, then the locators in brackets. The locators stay because this is
the working copy — a reviewer disputing a verdict needs to reach the lines it
rests on, and `deliverable-v2:120-134` is a stable address into hash-pinned text.

Columns sit side by side rather than merged. Two evaluators disagreeing on a row
is the most useful thing the sheet can tell you, and a consensus column would
throw it away.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import vocabulary                                          # noqa: E402
import writeback                                              # noqa: E402
import matrix as matrix_reader                                # noqa: E402

VOCAB = vocabulary.load()


def col_num(s):
    n = 0
    for ch in s.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def col_name(n):
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--xlsx", required=True)
    p.add_argument("--sheet", required=True)
    p.add_argument("--eval", action="append", required=True, metavar="NAME=FILE")
    p.add_argument("--first-col", required=True)
    p.add_argument("--id-col", default="A")
    p.add_argument("--no-locators", action="store_true")
    p.add_argument("--out", required=True)
    args = p.parse_args()

    src, out = args.xlsx, os.path.abspath(os.path.expanduser(args.out))
    start, current = col_num(args.first_col), args.xlsx

    for offset, spec in enumerate(args.eval):
        name, path = spec.split("=", 1)
        by_id = {}
        for line in open(os.path.expanduser(path), encoding="utf-8"):
            try:
                r = json.loads(line)
                by_id[r["id"]] = r
            except Exception:
                continue
        column = col_name(start + offset)
        rows = matrix_reader.read_sheet(current, args.sheet)
        values = {"1": name}
        for pos, row in enumerate(rows[1:], start=2):
            rid = (row.get(args.id_col, "") or "").strip()
            r = by_id.get(rid)
            if not r:
                continue
            head = VOCAB.label(r["verdict"])
            text = (r.get("rationale") or "").strip()
            cell = f"{head}. {text}" if text else head
            if r.get("cites") and not args.no_locators:
                cell += "  [" + ", ".join(r["cites"]) + "]"
            values[str(pos)] = cell
        target = out if offset == len(args.eval) - 1 else out + f".step{offset}"
        written, _ = writeback.annotate(current, target, args.sheet, values,
                                        column, True)
        if current != src and ".step" in current:
            os.remove(current)
        current = target
        print(f"  {column}: {name:<16} {written} row(s)")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
