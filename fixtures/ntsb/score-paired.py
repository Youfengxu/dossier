#!/usr/bin/env python3
"""Score the enriched slice on the pair of numbers that actually decides a model.

    ./score-paired.py enriched/out-*.jsonl

WHY A PAIR. The question is "does this model recognise compliance reached by a
route other than the one asked for". Measured alone, that question rewards a model
that answers `addressed` to everything: it scores 34/34 and is worthless, because
it has also stopped catching failure — the thing a review tool exists to do.

So two numbers, and a model is better only if it moves the first without giving up
the second:

    alternate-route recall   of the 34 "Closed — Acceptable Alternate Action"
                             rows, how many were called addressed
    failure recall           of the 54 not_addressed rows, how many were caught

Overall accuracy is printed beside the majority baseline, and is the least
interesting of the three: on a 44/54 split a model can sit on the baseline by
having no opinion at all.

`partial` counts as not_addressed, as in score-ntsb.py — NTSB closes a
recommendation as acceptable or it does not, and a partial response is not an
acceptance.
"""

import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = "Closed - Acceptable Alternate Action"


def strict(verdict):
    return "not_addressed" if verdict == "partial" else verdict


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip())
        return 0
    meta = json.load(open(os.path.join(HERE, "enriched", "labels.json")))["labels"]
    truth = {k: v["verdict"] for k, v in meta.items()}
    alt = {k for k, v in meta.items() if v["ntsb_status"] == TARGET}
    fails = {k for k, v in truth.items() if v == "not_addressed"}
    counts = collections.Counter(truth.values())
    baseline = max(counts.values()) / len(truth)

    print(f"  {len(truth)} rows: {counts['addressed']} addressed, "
          f"{counts['not_addressed']} not_addressed "
          f"({len(alt)} of the addressed are alternate-route)")
    print(f"  majority baseline {baseline:.1%}\n")
    print(f"  {'run':<22} {'alt-route':>11}  {'failures':>11}  {'overall':>9}")
    print("  " + "-" * 58)

    for path in sys.argv[1:]:
        got = {}
        for line in open(path, encoding="utf-8"):
            row = json.loads(line)
            got[row["id"]] = strict(row["verdict"])
        covered = set(got) & set(truth)
        a_hit = sum(1 for k in alt & covered if got[k] == "addressed")
        f_hit = sum(1 for k in fails & covered if got[k] == "not_addressed")
        overall = sum(1 for k in covered if got[k] == truth[k])
        a_n, f_n = len(alt & covered), len(fails & covered)
        name = os.path.basename(path).replace("out-", "").replace(".jsonl", "")
        note = "" if len(covered) == len(truth) else f"  ({len(covered)} of {len(truth)})"
        print(f"  {name:<22} {a_hit:>3}/{a_n:<3} {a_hit/a_n:>5.0%}  "
              f"{f_hit:>3}/{f_n:<3} {f_hit/f_n:>5.0%}  "
              f"{overall/len(covered):>8.1%}{note}")

    print("\n  A model is better only if alt-route rises AND failures do not fall.\n"
          "  Either number alone can be bought by having no opinion.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
