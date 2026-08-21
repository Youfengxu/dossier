#!/usr/bin/env python3
"""Two coverage runs over the same document, side by side.

Temperature is zero, so it is tempting to treat a coverage run as a
measurement. It is not: retrieval, concurrency, batching on the server and the
retry path all move, and a failed call degrades one obligation's verdict
without degrading the run. The only way to know how much of a verdict is the
document and how much is the weather is to run it twice and look.

    ./compare-coverage.py --a cov-deliverable-v2.runB.csv --b cov-deliverable-v2.runC.csv

Reports agreement, then every obligation that moved, worst direction first. A
met->unmet flip is a finding appearing out of nowhere; unmet->met is one
vanishing. Both are worse than partial->met, and a reviewer should see them
first.
"""

import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import vocabulary                                          # noqa: E402

# The scale comes from the project's declared vocabulary rather than from a dict
# here. The dict that used to sit in this file ranked "unverifiable" BETWEEN
# partial and met, which made "we could not check" score one step better than
# "partly done" — an artefact of listing the values alphabetically-ish rather
# than a judgement anyone made.
VOCAB = vocabulary.load(name="coverage")


def load(path):
    rows = {}
    with open(path, encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            key = (row.get("obligation") or "").strip()
            if key:
                rows[key] = row
    return rows


def severity(a, b):
    """How alarming is this move? Distance on the scale, with the ends worst."""
    if a not in VOCAB.values or b not in VOCAB.values:
        return 9
    # distance() is None when either side is off-scale — unverifiable and
    # not_applicable are not degrees of coverage, and giving them a position
    # invents a comparison. An unrankable move still sorts to the top, because
    # "this went from partly done to uncheckable" is exactly what a reviewer
    # should see first.
    steps = VOCAB.distance(a, b)
    return 9 if steps is None else steps


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--a", required=True)
    parser.add_argument("--b", required=True)
    parser.add_argument("--label-a", default="run A")
    parser.add_argument("--label-b", default="run B")
    parser.add_argument("--out")
    args = parser.parse_args()

    a, b = load(args.a), load(args.b)
    shared = sorted(set(a) & set(b))
    only_a, only_b = sorted(set(a) - set(b)), sorted(set(b) - set(a))

    moved, same = [], 0
    for key in shared:
        va = (a[key].get("verdict") or "").strip()
        vb = (b[key].get("verdict") or "").strip()
        if va == vb:
            same += 1
        else:
            moved.append((severity(va, vb), key, va, vb))
    moved.sort(key=lambda m: (-m[0], m[1]))

    total = len(shared)
    print(f"{args.label_a}: {args.a}  ({len(a)} obligations)")
    print(f"{args.label_b}: {args.b}  ({len(b)} obligations)")
    if only_a or only_b:
        print(f"\n  only in {args.label_a}: {len(only_a)}   "
              f"only in {args.label_b}: {len(only_b)}")
    print(f"\n{same}/{total} verdicts identical "
          f"({100 * same / total:.0f}%), {len(moved)} moved")
    print("=" * 74)

    # Quote drift matters even where the verdict held: the same verdict resting
    # on a different passage is a different claim, and the quote is what goes
    # in front of the vendor.
    quote_drift = sum(
        1 for key in shared
        if (a[key].get("verdict") == b[key].get("verdict")
            and (a[key].get("quote") or "").strip()
            != (b[key].get("quote") or "").strip()))
    print(f"  of the {same} that agree, {quote_drift} cite a different quote")

    if moved:
        print(f"\n-- obligations whose verdict changed --")
        print(f"  {'id':10} {args.label_a[:14]:<15} {args.label_b[:14]:<15} move")
        for dist, key, va, vb in moved:
            arrow = "!!" if dist >= 3 else ("!" if dist == 2 else " ")
            print(f"  {key:10} {va:<15} {vb:<15} {arrow}")

    # display_order, not a hand-written tuple: the old one omitted
    # "not_applicable" entirely, so a run where obligations were scoped out
    # reported nothing about them. Rows with no count on either side stay hidden
    # by the guard below, so nothing new appears unless it happened.
    for verdict in VOCAB.display_order:
        ca = sum(1 for r in a.values() if r.get("verdict") == verdict)
        cb = sum(1 for r in b.values() if r.get("verdict") == verdict)
        if ca or cb:
            print(f"  {verdict:<14} {ca:>3} -> {cb:>3}"
                  + (f"   ({cb - ca:+d})" if ca != cb else ""))

    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["obligation", args.label_a, args.label_b,
                             "distance", "requirement"])
            for dist, key, va, vb in moved:
                writer.writerow([key, va, vb, dist,
                                 (a[key].get("requirement") or "")[:300]])
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
