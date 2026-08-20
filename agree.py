#!/usr/bin/env python3
"""Where two independent judges disagree about the same row.

A single model's verdict on "did the vendor address this comment?" is one
opinion. Thirty of those are heading to a vendor, and the ones worth a human's
attention first are not the confident ones — they are the ones where a second
model, run independently on the same evidence, reached a different answer.

    ./agree.py --a out/D2-adjudicated-v7.xlsx --b out/D2-adjudicated-v7-glimmer.xlsx \
               --sheet "Comments" --out out/D2-dual-judge.xlsx

Column M/N keep judge A's verdict and rationale (the file's existing layout).
O/P take judge B's. Q is the flag a reviewer sorts on:

    AGREE           both said the same thing
    DISAGREE        both judged, different verdicts
    ADJACENT        addressed vs partial, or partial vs not_addressed — a
                    difference of degree, not of direction
    ONE JUDGE ONLY  only one model returned a verdict for this row
    UNRANKABLE      a verdict sits off the scale, so how far apart the two
                    readings are cannot be stated — grouped with the
                    disagreements, because "we cannot tell" belongs in front of
                    a person rather than filed under agreement

ADJACENT exists because the ordinal scale is not flat. addressed/not_addressed
is a contradiction; partial/not_addressed is two readers drawing the same line
in slightly different places. Ranking them alike would bury the contradictions
in a list of near-misses.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import vocabulary                                          # noqa: E402
import matrix as matrix_reader                              # noqa: E402
import writeback                                            # noqa: E402

# Ordered weakest to strongest. Neighbours differ by degree; distance 2+ is a
# real conflict. "unclear" is off the scale — it is not a weaker "addressed",
# it is a refusal to answer, so any pairing with it is flagged outright.
VOCAB = vocabulary.load()


def classify(a, b):
    """AGREE / ADJACENT / DISAGREE, or a note that only one judge answered.

    The ordering comes from the project's declared scale rather than from a list
    compiled in here, so an audit or a conformance review gets the same
    adjacency logic under its own words.
    """
    if not a or not b:
        return "ONE JUDGE ONLY"
    return VOCAB.agreement([a, b])


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--a", required=True, help="judge A workbook")
    parser.add_argument("--b", required=True, help="judge B workbook")
    parser.add_argument("--sheet", required=True)
    parser.add_argument("--id-col", default="A")
    parser.add_argument("--verdict-col", default="M")
    parser.add_argument("--rationale-col", default="N")
    parser.add_argument("--b-verdict-col", default="O")
    parser.add_argument("--b-rationale-col", default="P")
    parser.add_argument("--flag-col", default="Q")
    parser.add_argument("--label-a", default="judge A")
    parser.add_argument("--label-b", default="judge B")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    rows_a = matrix_reader.read_sheet(args.a, args.sheet)
    rows_b = matrix_reader.read_sheet(args.b, args.sheet)

    # Key by row id, not by position: the two runs skip the same rows for the
    # same reasons today, but a comparison that silently depends on that would
    # misalign every verdict the first time one of them doesn't.
    def by_id(rows):
        out = {}
        for row in rows[1:]:
            rid = row.get(args.id_col, "").strip()
            if rid:
                out[rid] = row
        return out

    index_b = by_id(rows_b)
    verdicts, notes, flags = {}, {}, {}
    counts, conflicts = {}, []

    for position, row in enumerate(rows_a[1:], start=2):
        rid = row.get(args.id_col, "").strip()
        if not rid:
            continue
        a_verdict = row.get(args.verdict_col, "").strip()
        other = index_b.get(rid, {})
        b_verdict = other.get(args.verdict_col, "").strip()
        if not a_verdict and not b_verdict:
            continue
        flag = classify(a_verdict, b_verdict)
        counts[flag] = counts.get(flag, 0) + 1
        verdicts[str(position)] = b_verdict
        notes[str(position)] = other.get(args.rationale_col, "").strip()
        flags[str(position)] = flag
        if flag != "AGREE":
            conflicts.append((rid, a_verdict or "-", b_verdict or "-", flag))

    print(f"{args.label_a}: {args.a}")
    print(f"{args.label_b}: {args.b}")
    print(f"\n{sum(counts.values())} row(s) with at least one verdict\n")
    print("=" * 74)
    for flag in ("DISAGREE", "UNRANKABLE", "ADJACENT", "ONE JUDGE ONLY", "AGREE"):
        if counts.get(flag):
            print(f"  {flag:<16} {counts[flag]}")

    if conflicts:
        print(f"\n-- rows a human should read first --")
        print(f"  {'row':<9} {args.label_a[:16]:<17} {args.label_b[:16]:<17} flag")
        order = {"DISAGREE": 0, "UNRANKABLE": 1, "ONE JUDGE ONLY": 2,
                 "ADJACENT": 3}
        for rid, a, b, flag in sorted(conflicts, key=lambda c: (order[c[3]], c[0])):
            print(f"  {rid:<9} {a:<17} {b:<17} {flag}")

    out_path = os.path.abspath(os.path.expanduser(args.out))
    written, _ = writeback.annotate(args.a, out_path, args.sheet, verdicts,
                                    args.b_verdict_col, True)
    writeback.annotate(out_path, out_path + ".tmp2", args.sheet, notes,
                       args.b_rationale_col, True)
    os.replace(out_path + ".tmp2", out_path)
    writeback.annotate(out_path, out_path + ".tmp3", args.sheet, flags,
                       args.flag_col, True)
    os.replace(out_path + ".tmp3", out_path)
    print(f"\nwrote {out_path}")
    print(f"  {args.verdict_col}/{args.rationale_col} = {args.label_a}, "
          f"{args.b_verdict_col}/{args.b_rationale_col} = {args.label_b}, "
          f"{args.flag_col} = agreement ({written} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
