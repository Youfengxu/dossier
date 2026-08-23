#!/usr/bin/env python3
"""Assign a run to a POLICY, not a rank. The 2x2 that forces the policies apart.

    ./score-cells.py enriched/out-*.jsonl

WHY A RANK IS NOT ENOUGH. A model judging whether an outcome was achieved and a
model checking whether the requirement's words appear in the response produce the
same verdict on most rows, because most of the time the words and the outcome agree.
A score over such rows is not weak evidence for the judgement — it is no evidence.
The two policies are only separable where they disagree.

So cross what the requirement SAYS with what the response DID:

                        addressed            not_addressed
    high overlap    A  both agree        C  words present, outcome absent   <- TRAP
    low  overlap    B  ALTERNATE ROUTE   D  both agree

Overlap is the fraction of the requirement's distinctive terms (4+ letters, minus
stopwords) that occur in the response, split at the fixture's median. It is a
deliberately crude proxy for what a vocabulary matcher sees — crude is fine, because
its only job is to locate the cells where the policies must diverge.

A and D are the agreeing cells: any policy that tracks vocabulary scores well.
B and C are the off-diagonal, and they invert. A vocabulary matcher says
not_addressed on B (the words are missing though the outcome was reached) and
addressed on C (the words are there though nothing was done). An outcome judge is
right on all four.

    policy                A    B    C    D
    vocabulary matcher    Y    n    n    Y
    blanket addressed     Y    Y    n    n
    blanket refuse        n    n    Y    Y
    outcome judge         Y    Y    Y    Y

THE NUMBER THAT MATTERS is the gap between the agreeing cells (A, D) and the
diverging ones (B, C). A model that scores far better where vocabulary and truth
coincide than where they part is matching vocabulary, whatever its total says — and
its total is then partly a measure of how often the two happen to agree in the
fixture, which is a property of the sample rather than of the model.

Runs covering fewer than 80 of the 98 rows are reported and excluded: a partial run
lands one or two rows in a cell, and a cell rate over n=2 is not a signature.
"""

import collections
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MIN_ROWS = 80
STOP = set("""a an the and or of to in for on with by is are be been shall must
should may not that this these those it its as at from any all each every per which
where when who whom whose if then than such other same both either will would can
could have has had do does did we our you your they their""".split())

POLICY = {"vocabulary matcher": {"A": 1, "B": 0, "C": 0, "D": 1},
          "blanket addressed":  {"A": 1, "B": 1, "C": 0, "D": 0},
          "blanket refuse":     {"A": 0, "B": 0, "C": 1, "D": 1},
          "outcome judge":      {"A": 1, "B": 1, "C": 1, "D": 1}}
LABEL = {"A": "high overlap, addressed", "B": "low overlap, addressed (ALT-ROUTE)",
         "C": "high overlap, not_addressed (TRAP)", "D": "low overlap, not_addressed"}


def terms(text):
    return {w for w in re.findall(r"[a-z]{4,}", (text or "").lower()) if w not in STOP}


def overlap(requirement, response):
    want = terms(requirement)
    return len(want & terms(response)) / len(want) if want else 0.0


def cells():
    """Every labelled row assigned to a cell, from the fixture on disk."""
    import csv
    base = os.path.join(HERE, "enriched")
    meta = json.load(open(os.path.join(base, "labels.json")))["labels"]
    req = {r["id"]: r["obligation"]
           for r in csv.DictReader(open(os.path.join(base, "obligations.csv")))}
    responses = {}
    for chunk in open(os.path.join(base, "claims.md")).read().split("\n## ")[1:]:
        head, _, body = chunk.partition("\n")
        responses[head.strip()] = body
    scores = {i: overlap(req.get(i, ""), responses.get(i, "")) for i in meta}
    median = sorted(scores.values())[len(scores) // 2]
    out = {}
    for i, m in meta.items():
        high = scores[i] >= median
        addressed = m["verdict"] == "addressed"
        out[i] = ("A" if high and addressed else "B" if addressed else
                  "C" if high else "D")
    return meta, out, median


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip())
        return 0
    meta, cell, median = cells()
    spread = collections.Counter(cell.values())
    print(f"  {len(meta)} rows, overlap median {median:.2f} — "
          + "  ".join(f"{c}:{spread[c]}" for c in "ABCD"))
    print(f"\n  {'run':<14}" + "".join(f"{c:>7}" for c in "ABCD")
          + f"{'agree':>8}{'diverge':>9}{'gap':>6}   policy")
    print("  " + "-" * 74)
    skipped = []
    for path in sys.argv[1:]:
        got = {json.loads(l)["id"]:
               ("not_addressed" if json.loads(l)["verdict"] == "partial"
                else json.loads(l)["verdict"])
               for l in open(path, encoding="utf-8")}
        name = os.path.basename(path).replace("out-", "").replace(".jsonl", "")
        covered = set(got) & set(meta)
        if len(covered) < MIN_ROWS:
            skipped.append((name, len(covered)))
            continue
        rate = {}
        for c in "ABCD":
            ids = [i for i in covered if cell[i] == c]
            rate[c] = sum(got[i] == meta[i]["verdict"] for i in ids) / len(ids) if ids else 0.0
        agree = (rate["A"] + rate["D"]) / 2
        diverge = (rate["B"] + rate["C"]) / 2
        best = min(POLICY, key=lambda p: sum((rate[c] - POLICY[p][c]) ** 2 for c in "ABCD"))
        print(f"  {name:<14}" + "".join(f"{rate[c]:>6.0%} " for c in "ABCD")
              + f"{agree:>7.0%}{diverge:>8.0%}{agree - diverge:>+6.0%}   {best}")
    for name, n in skipped:
        print(f"  {name:<14} excluded — {n} of {len(meta)} rows, too few per cell")
    print()
    for c in "ABCD":
        print(f"    {c}  {LABEL[c]}")
    print("\n  gap = how much better a run does where vocabulary and truth agree\n"
          "  than where they diverge. Large gap means the verdicts track wording,\n"
          "  and the total then partly measures the fixture rather than the model.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
