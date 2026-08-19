#!/usr/bin/env python3
"""Score a coverage run against CUAD's lawyer-written annotations.

Two questions, both of which nothing else here can answer:

  ABSENCE   79% of these clause/contract pairs are marked absent by lawyers.
            Every other corpus is one where most obligations are MET, so the
            tools have only been measured against a single bias. Does the run
            say "unmet" when the clause genuinely is not there, and — harder —
            does it avoid saying "unmet" when it is?

  SPAN      Where a clause IS present, a lawyer marked the exact text. Does the
            quoted evidence overlap that text? floodtwin only ever checked that
            a quote was verbatim from the passages supplied; it never checked
            the quote was the RIGHT one. A verdict can be correct for entirely
            the wrong reason, and until now that was invisible.

    ./score-spans.py --contract <slug> --coverage coverage-<slug>.csv
"""

import argparse
import csv
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))


def normalise(text):
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def tokens(text):
    return set(re.findall(r"[a-z0-9]{3,}", normalise(text)))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default=HERE)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--coverage", required=True)
    parser.add_argument("--overlap", type=float, default=0.30,
                        help="token-overlap fraction counted as hitting the span")
    args = parser.parse_args()

    try:
        import yaml
    except ImportError:
        sys.exit("needs PyYAML")

    project = os.path.abspath(args.project)
    answers = yaml.safe_load(open(os.path.join(project, "answers.yaml")))["labels"]
    gold = {a["clause"]: a for a in answers if a["contract"] == args.contract}
    if not gold:
        sys.exit(f"no labels for contract {args.contract!r}; have: "
                 + ", ".join(sorted({a['contract'] for a in answers})))

    rows = list(csv.DictReader(open(os.path.join(project, args.coverage),
                                    encoding="utf-8")))
    print(f"=== CUAD / {args.contract} ===")
    print(f"{len(rows)} verdicts, {len(gold)} lawyer-labelled clauses, "
          f"{sum(1 for g in gold.values() if g['absent'])} of them absent\n")

    matrix = Counter()
    span_hit = span_total = 0
    wrong_absent, wrong_present, blind_hits = [], [], []

    for row in rows:
        clause = row.get("source_ref", "").strip()
        label = gold.get(clause)
        if label is None:
            continue
        expected_absent = bool(label["absent"])
        # Only a flat "unmet" is a claim of absence. partial/unverifiable are
        # hedges and are scored as "present" — the tool did not commit.
        predicted_absent = row["verdict"] == "unmet"
        matrix[(expected_absent, predicted_absent)] += 1

        if expected_absent and not predicted_absent:
            wrong_present.append((clause, row["verdict"]))
        if not expected_absent and predicted_absent:
            wrong_absent.append((clause, row["reason"][:70]))

        if not expected_absent and row.get("quote"):
            span_total += 1
            quoted = tokens(row["quote"])
            best = 0.0
            for span in label.get("spans") or []:
                want = tokens(span)
                if want:
                    best = max(best, len(quoted & want) / len(want))
            if best >= args.overlap:
                span_hit += 1
            elif row["verdict"] in ("met", "partial"):
                blind_hits.append((clause, best, row["quote"][:60]))

    tp = matrix[(True, True)]      # absent, called unmet
    fn = matrix[(True, False)]     # absent, not called unmet
    fp = matrix[(False, True)]     # present, wrongly called unmet
    tn = matrix[(False, False)]
    total = tp + fn + fp + tn

    def pct(a, b):
        return f"{a}/{b}" + (f"  {100*a/b:5.1f}%" if b else "     —")

    print("ABSENCE DETECTION  (does 'unmet' mean the clause is really missing?)")
    print(f"  accuracy                {pct(tp + tn, total)}")
    print(f"  recall on absent        {pct(tp, tp + fn)}   "
          f"(lawyer says absent, tool says unmet)")
    print(f"  precision of 'unmet'    {pct(tp, tp + fp)}   "
          f"(tool says unmet, lawyer agrees)")
    print(f"  confusion  tp={tp} fn={fn} fp={fp} tn={tn}")

    if wrong_absent:
        print(f"\n  FALSE 'unmet' — the clause IS in the contract ({len(wrong_absent)}):")
        for clause, reason in wrong_absent[:8]:
            print(f"    {clause[:38]:40} {reason}")
    if wrong_present:
        print(f"\n  MISSED absences — lawyer says absent, tool did not commit "
              f"({len(wrong_present)}):")
        for clause, verdict in wrong_present[:8]:
            print(f"    {clause[:38]:40} said '{verdict}'")

    print(f"\nSPAN FIDELITY  (is the quote the RIGHT text, not merely verbatim?)")
    print(f"  quotes checked          {span_total}")
    print(f"  overlap the gold span   {pct(span_hit, span_total)} "
          f"(>= {args.overlap:.0%} of gold tokens)")
    if blind_hits:
        print(f"\n  RIGHT VERDICT, WRONG EVIDENCE ({len(blind_hits)}) — these are the"
              f"\n  dangerous ones: a finding that looks sound and cites the wrong text.")
        for clause, best, quote in blind_hits[:6]:
            print(f"    {clause[:34]:36} overlap {best:4.0%}  {quote!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
