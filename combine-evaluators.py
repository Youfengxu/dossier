#!/usr/bin/env python3
"""Put several evaluators' verdicts side by side, one column each.

    ./combine-evaluators.py --xlsx matrix.xlsx --sheet "Comments" \
        --eval "gpt-oss=out/a.xlsx" --eval "qwen=out/b.xlsx" \
        --eval "glimmer=out/c.xlsx" --after I --out combined.xlsx

Each evaluator gets its own column, and each cell reads as a person would write
it: a one-word conclusion, a full stop, then the reasoning. The trailing locator
the tools append is stripped, because it is provenance for the run rather than
something a reviewer wants mid-sentence.

WHY SIDE BY SIDE RATHER THAN A CONSENSUS COLUMN. A majority hides the thing worth
seeing. On one tab the three judges agreed on 68 of 89 rows; the value is in
knowing which 21 they did not, and reading those first. A single merged verdict
buys tidiness at the price of the only signal that tells a reviewer where to
spend attention. Rank the disagreements, do not average them away.
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import vocabulary                                          # noqa: E402
import matrix as matrix_reader                               # noqa: E402
import writeback                                             # noqa: E402

VOCAB = vocabulary.load()

# The bracket the adjudicator appends: section locators, or a note that the
# reference could not be resolved. Useful in the working file, noise in a column
# somebody reads.
# Any trailing bracket the adjudicator appends: section locators, a note that a
# reference could not be resolved, a record of renumbering. The first version
# matched only the two openings it knew about and left five rows carrying
# "[§9, §11 could not be located in …]" mid-column — a pattern that had not been
# invented when the regex was written.
# A row can carry more than one: the wide path appends its own note and the
# unlocatable fallback appends another, so they arrive back to back. Stripping a
# single bracket left the first of the pair sitting in the column.
TRAIL = re.compile(r"(?:\s*\[[^\]]{0,400}\])+\s*$")


def col_num(letters):
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def col_name(number):
    out = ""
    while number:
        number, rem = divmod(number - 1, 26)
        out = chr(65 + rem) + out
    return out


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--xlsx", "--matrix", required=True, help="matrix to write into")
    parser.add_argument("--sheet", required=True)
    parser.add_argument("--eval", action="append", required=True,
                        metavar="NAME=FILE",
                        help="repeatable; column order follows argument order")
    parser.add_argument("--id-col", default="A")
    parser.add_argument("--verdict-col", default="M",
                        help="column the evaluator wrote its verdict into")
    parser.add_argument("--rationale-col", default="N")
    parser.add_argument("--first-col", required=True,
                        help="letter of the first output column")
    parser.add_argument("--keep-locator", action="store_true")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    evaluators = []
    for spec in args.eval:
        if "=" not in spec:
            sys.exit(f"--eval wants NAME=FILE, got {spec!r}")
        name, path = spec.split("=", 1)
        rows = matrix_reader.read_sheet(path, args.sheet)
        evaluators.append((name.strip(), {
            r[args.id_col].strip(): (r.get(args.verdict_col, "").strip(),
                                     (r.get(args.rationale_col, "") or "").strip())
            for r in rows[1:] if r.get(args.id_col, "").strip()}))

    src = args.xlsx
    out = os.path.abspath(os.path.expanduser(args.out))
    start = col_num(args.first_col)

    current = src
    for offset, (name, data) in enumerate(evaluators):
        column = col_name(start + offset)
        rows = matrix_reader.read_sheet(current, args.sheet)
        values = {}
        for position, row in enumerate(rows[1:], start=2):
            rid = row.get(args.id_col, "").strip()
            if not rid or rid not in data:
                continue
            verdict, rationale = data[rid]
            if not verdict:
                continue
            text = rationale if args.keep_locator else TRAIL.sub("", rationale)
            head = VOCAB.label(verdict)
            values[str(position)] = f"{head}. {text.strip()}" if text.strip() else head
        # Header goes in row 1, alongside the verdicts.
        values["1"] = name
        target = out if offset == len(evaluators) - 1 else out + f".step{offset}"
        written, _ = writeback.annotate(current, target, args.sheet, values,
                                        column, True)
        if current != src and current.endswith(tuple(f".step{i}" for i in range(9))):
            os.remove(current)
        current = target
        print(f"  {column}: {name:<14} {written} rows")

    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
