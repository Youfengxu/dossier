#!/usr/bin/env python3
"""Score an assess.py run against NTSB's own adjudications.

    ./score-ntsb.py out/qwen36.jsonl [more.jsonl ...]

THE PARTIAL PROBLEM, decided before looking at any score. NTSB's labels are
binary — the response was acceptable or it was not — and the review scale has
three values. `partial` has to land somewhere, and where it lands moves the
number, so both readings are printed every time rather than the flattering one:

    strict    partial counts as not_addressed. NTSB closes a recommendation as
              acceptable or it does not; a partial response is not an acceptance.
              This is the primary number.
    lenient   partial counts as addressed. Printed because a reader should see
              how much of the result rests on that single choice.

THE BASELINE IS PRINTED WITH THE RESULT, always. The slice is 11 not_addressed
against 13 addressed, so answering "addressed" to everything scores 54%. A tool
that does not beat that comfortably has demonstrated nothing, and an accuracy
figure quoted without it is not a measurement — it is a decoration.

`Closed — Reconsidered` is excluded upstream: NTSB withdrew the recommendation
rather than judging the response, so scoring against it would be scoring a
question nobody asked.
"""

import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MAPPINGS = {"strict": {"partial": "not_addressed"},
            "lenient": {"partial": "addressed"}}


def score(records, truth, policy):
    hits, confusion, seen = 0, collections.Counter(), 0
    for rid, actual in truth.items():
        got = records.get(rid)
        if got is None:
            continue
        seen += 1
        mapped = MAPPINGS[policy].get(got, got)
        confusion[(actual, mapped)] += 1
        hits += mapped == actual
    return hits, seen, confusion


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip())
        return 0
    labels = json.load(open(os.path.join(HERE, "labels.json"), encoding="utf-8"))
    truth = {k: v["verdict"] for k, v in labels["labels"].items()}
    counts = collections.Counter(truth.values())
    majority, majority_n = counts.most_common(1)[0]
    baseline = majority_n / len(truth)

    print(f"  {len(truth)} labelled, {len(labels['excluded'])} excluded "
          f"({', '.join(f'{v} {k}' for k, v in counts.items())})")
    print(f"  majority-class baseline: answer {majority!r} to everything "
          f"-> {majority_n}/{len(truth)} = {baseline:.1%}")

    for path in sys.argv[1:]:
        records, unverified = {}, 0
        for line in open(path, encoding="utf-8"):
            row = json.loads(line)
            records[row["id"]] = row["verdict"]
            if not row.get("cites"):
                unverified += 1
        print(f"\n  == {os.path.basename(path)} ==")
        print(f"     {len(records)} verdicts, {unverified} resting on no "
              f"verified citation")
        for policy in ("strict", "lenient"):
            hits, seen, confusion = score(records, truth, policy)
            delta = hits / seen - baseline if seen else 0
            print(f"     {policy:<8} {hits}/{seen} = {hits / seen:.1%}"
                  f"   ({delta:+.1%} vs baseline)")
            if policy == "strict":
                for actual in sorted({a for a, _ in confusion}):
                    row = "  ".join(
                        f"{got}:{confusion[(actual, got)]}"
                        for got in sorted({g for _, g in confusion})
                        if confusion[(actual, got)])
                    print(f"       truth {actual:<14} -> {row}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
