#!/usr/bin/env python3
"""Turn a dual-judge workbook into prose a person can read on an e-reader.

A spreadsheet is the wrong shape for reading. Thirteen columns do not fit an
e-ink page, the rationale is the part that matters and it is the part a cell
truncates, and a reviewer working through a hundred rows needs to move down a
page, not across one. So: no tables, one section per row, the two judgements as
sentences, and the rows that need a human first.

    ./render.py --xlsx out/D2-dual-judge.xlsx --sheet "Comments" \
                --title "Deliverable v2" --out out/dual-judge.md

Ordering is deliberate. Contradictions lead, then near-misses, then agreement,
because a reviewer who stops halfway should have spent that half on the rows
where two models could not agree what the vendor did.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import matrix as matrix_reader                              # noqa: E402

ORDER = ["DISAGREE", "ADJACENT", "ONE JUDGE ONLY", "AGREE"]

BLURB = {
    "DISAGREE": (
        "Two models read the same evidence and reached opposite conclusions. "
        "Read every one of these before the response goes out."),
    "ADJACENT": (
        "The two judgements differ by one step — partial against not "
        "addressed. That is usually two readers drawing the same line in "
        "slightly different places rather than a real conflict, but the "
        "wording you send should reflect whichever you settle on."),
    "ONE JUDGE ONLY": (
        "Only one model returned a verdict here, so there is no second "
        "opinion behind it."),
    "AGREE": (
        "Both models reached the same verdict independently. These need "
        "confirmation rather than adjudication."),
}

VERDICT_PHRASE = {
    "addressed": "considers it addressed",
    "partial": "considers it partly addressed",
    "not_addressed": "considers it not addressed",
    "unclear": "could not tell",
    "": "returned no verdict",
}


def wrap(text, width=0):
    """Left alone on purpose. Hard-wrapping fights an e-reader's own reflow and
    its font-size control; soft paragraphs let the device lay them out."""
    return " ".join(text.split())


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--xlsx", "--matrix", required=True)
    parser.add_argument("--sheet", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--id-col", default="A")
    parser.add_argument("--section-col", default="C")
    parser.add_argument("--comment-col", default="D")
    parser.add_argument("--a-verdict", default="M")
    parser.add_argument("--a-rationale", default="N")
    parser.add_argument("--b-verdict", default="O")
    parser.add_argument("--b-rationale", default="P")
    parser.add_argument("--flag-col", default="Q")
    parser.add_argument("--label-a", default="gpt-oss-120b")
    parser.add_argument("--label-b", default="glimmer-30b")
    args = parser.parse_args()

    rows = matrix_reader.read_sheet(args.xlsx, args.sheet)
    buckets = {k: [] for k in ORDER}
    for row in rows[1:]:
        rid = row.get(args.id_col, "").strip()
        flag = row.get(args.flag_col, "").strip()
        if not rid or flag not in buckets:
            continue
        buckets[flag].append(row)

    total = sum(len(v) for v in buckets.values())
    out = [f"# {args.title} — adjudication review", ""]
    out.append(wrap(
        f"{total} comments were judged twice, once by {args.label_a} and once "
        f"by {args.label_b}, each without sight of the other's answer. "
        f"{len(buckets['AGREE'])} agree, {len(buckets['ADJACENT'])} differ by "
        f"one step, {len(buckets['DISAGREE'])} contradict outright."))
    out.append("")
    out.append(wrap(
        "Where a comment named a section, the judgement is based on the diff "
        "between that section in the old and new drafts. Where it named none "
        "— \"All\", \"Title\", or nothing — it was judged against the changed "
        "sections closest to the comment instead, and the rationale says so. "
        "Those are the weaker verdicts of the two kinds."))
    out.append("")

    for flag in ORDER:
        items = buckets[flag]
        if not items:
            continue
        out.append(f"## {flag.title()} — {len(items)} "
                   f"{'comment' if len(items) == 1 else 'comments'}")
        out.append("")
        out.append(wrap(BLURB[flag]))
        out.append("")
        for row in items:
            rid = row.get(args.id_col, "").strip()
            section = row.get(args.section_col, "").strip()
            where = f" (section {section})" if section else ""
            out.append(f"### {rid}{where}")
            out.append("")
            comment = wrap(row.get(args.comment_col, "").strip())
            if comment:
                out.append(f"**The comment.** {comment}")
                out.append("")
            av = row.get(args.a_verdict, "").strip()
            bv = row.get(args.b_verdict, "").strip()
            ar = wrap(row.get(args.a_rationale, "").strip())
            br = wrap(row.get(args.b_rationale, "").strip())
            out.append(f"**{args.label_a}** {VERDICT_PHRASE.get(av, av)}. {ar}")
            out.append("")
            if bv or br:
                out.append(f"**{args.label_b}** {VERDICT_PHRASE.get(bv, bv)}. {br}")
                out.append("")
            # A place to write, because this file is the thing being reviewed.
            out.append("**Your call:** ______")
            out.append("")

    path = os.path.abspath(os.path.expanduser(args.out))
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(out).rstrip() + "\n")
    print(f"wrote {path}  ({total} comments, "
          + ", ".join(f"{len(buckets[f])} {f.lower()}" for f in ORDER
                      if buckets[f]) + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
