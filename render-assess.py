#!/usr/bin/env python3
"""Turn assess.py results into prose for an e-reader.

    ./render-assess.py --project . --doc deliverable-v2 \
        --xlsx matrix.xlsx --sheet "Comments" --title "D9 Widget" \
        --reader "Coder=out/coder.jsonl" --reader "Qwen=out/qwen.jsonl" \
        --out out/review.md

Same argument as render.py and a different input shape: three readers rather than
two, and structured JSONL rather than spreadsheet cells. A spreadsheet is the
wrong thing to read on an e-ink page — the rationale is what matters and the cell
is what truncates it, and a reviewer needs to move down a page rather than across
one.

ORDERING IS THE WHOLE POINT. Rows where the readers split lead, then rows where
they differ by one step, then the unanimous ones. A reviewer who stops halfway
should have spent that half where the machines could not agree, because those are
the only rows where their opinion is actually required.

NAVIGATION, NOT LOCATORS. Each row carries the heading its evidence sits under
and a phrase to search for. A line number into parsed text appears nowhere in the
document a reviewer has open, and a .docx has no page numbers at all — Word
computes pagination when it renders. The heading and the search string are the
only two things that actually find a passage.
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from locate import HEADING, searchable  # noqa: E402,F401
from locate import heading_only as nearest_heading  # noqa: E402
import vocabulary                                          # noqa: E402
import matrix as matrix_reader                               # noqa: E402
from llm import load_doc                                     # noqa: E402

VOCAB = vocabulary.load()          # replaced per-project in main()
BLURB = {
    "SPLIT": ("The readers reached different conclusions here. These are the "
              "rows where your judgement is actually needed — nothing below "
              "this section is contested in the same way."),
    "DIFFER": ("The readers differ by one step, usually partial against "
               "unaddressed. That is often two readers drawing the same line in "
               "slightly different places, but the wording you send should "
               "reflect whichever you settle on."),
    "AGREE": ("Every reader reached the same verdict independently. Skim these "
              "unless one looks wrong."),
}


# How much cited text to reproduce. Enough to judge the claim, not so much that
# the deliverable becomes a second copy of the document.
MAX_QUOTED = 2
QUOTE_LINES = 6
QUOTE_CHARS = 400






def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", required=True)
    p.add_argument("--doc", required=True)
    p.add_argument("--xlsx", "--matrix", required=True)
    p.add_argument("--sheet", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--reader", action="append", required=True, metavar="NAME=FILE")
    p.add_argument("--id-col", default="A")
    p.add_argument("--section-col", default="C")
    p.add_argument("--comment-col", default="D")
    p.add_argument("--action-col", default="E")
    p.add_argument("--preamble", default="")
    p.add_argument("--out", required=True)
    args = p.parse_args()

    project = os.path.abspath(os.path.expanduser(args.project))
    global VOCAB
    VOCAB = vocabulary.load(project)
    _meta, lines = load_doc(project, args.doc)
    xlsx = args.xlsx if os.path.isabs(args.xlsx) else os.path.join(project, args.xlsx)
    rows = {}
    sheet_rows = matrix_reader.read_sheet(xlsx, args.sheet)
    # Before the slice below discards the header, which is what
    # a name resolves against.
    matrix_reader.resolve_columns(sheet_rows, args)
    for r in sheet_rows[1:]:
        rid = (r.get(args.id_col, "") or "").strip()
        if rid:
            rows[rid] = r

    readers = []
    for spec in args.reader:
        name, path = spec.split("=", 1)
        readers.append((name.strip(),
                        {json.loads(l)["id"]: json.loads(l)
                         for l in open(os.path.expanduser(path), encoding="utf-8")}))

    ids = sorted(set.intersection(*(set(d) for _n, d in readers)))
    buckets = {"SPLIT": [], "DIFFER": [], "AGREE": []}
    for rid in ids:
        vs = [d[rid]["verdict"] for _n, d in readers]
        # UNRANKABLE lands with the splits on purpose: if a reader returned a
        # verdict off the scale, nobody can say how far apart these readings are,
        # and "we cannot tell" belongs in front of a person, not filed under
        # agreement.
        buckets[{"AGREE": "AGREE", "ADJACENT": "DIFFER",
                 "DISAGREE": "SPLIT", "UNRANKABLE": "SPLIT"}[
                     VOCAB.agreement(vs)]].append(rid)

    out = [f"# {args.title}", ""]
    if args.preamble:
        out += [args.preamble, ""]
    out += [f"{len(ids)} comments, {len(readers)} readers "
            f"({', '.join(n for n, _ in readers)}), each reading the whole of "
            f"`{args.doc}` independently.", "",
            f"**{len(buckets['SPLIT'])} split** · "
            f"{len(buckets['DIFFER'])} differ by one step · "
            f"{len(buckets['AGREE'])} unanimous", "", "---", ""]

    for bucket in ("SPLIT", "DIFFER", "AGREE"):
        if not buckets[bucket]:
            continue
        title = {"SPLIT": "Where the readers disagree",
                 "DIFFER": "Where they differ by one step",
                 "AGREE": "Where they agree"}[bucket]
        out += [f"## {title}  ({len(buckets[bucket])})", "", BLURB[bucket], ""]
        for rid in buckets[bucket]:
            row = rows.get(rid, {})
            out += [f"### {rid}", ""]
            ref = (row.get(args.section_col, "") or "").strip()
            if ref:
                out += [f"*Reviewer pointed at: {ref}*", ""]
            comment = (row.get(args.comment_col, "") or "").strip()
            if comment:
                out += ["**The comment.** " + comment, ""]
            claim = (row.get(args.action_col, "") or "").strip()
            if claim:
                out += ["**What the vendor says they did.** " + claim, ""]
            heads, finds = [], []
            for name, data in readers:
                r = data[rid]
                out += [f"**{name} — {VOCAB.label(r['verdict'])}.** "
                        + (r.get("rationale") or "*no reasoning given*"), ""]
                shown = 0
                for c in r.get("cites", []):
                    try:
                        a = int(c.rsplit(":", 1)[1].split("-")[0])
                        b = int(c.rsplit(":", 1)[1].split("-")[-1])
                    except (IndexError, ValueError):
                        continue
                    # Reproduce the cited text, not merely a route to it.
                    # Navigation and verification are different jobs: "search for
                    # X under heading Y" lets a reviewer FIND the passage, but
                    # leaves them unable to judge whether it says what the reader
                    # claimed without opening the document and reading it. The
                    # rationale and its evidence belong on the same page, or the
                    # evidence is decorative.
                    if shown < MAX_QUOTED:
                        body = [ln.strip() for ln in lines[a:min(b, a + QUOTE_LINES) + 1]
                                if ln.strip()]
                        if body:
                            quoted = " ".join(body)
                            if len(quoted) > QUOTE_CHARS:
                                quoted = quoted[:QUOTE_CHARS].rstrip() + " …"
                            out += [f"> **{args.doc}:{a}"
                                    + (f"-{b}" if b != a else "") + "** "
                                    + quoted, ""]
                            shown += 1
                    h = nearest_heading(lines, a)
                    if h and h not in heads:
                        heads.append(h)
                        finds.append(searchable("\n".join(lines[a:a + 10])))
                # A reader whose locators all failed to resolve reads exactly like
                # one whose locators held: same verdict, same confident prose, no
                # evidence either way. That asymmetry was visible only in the
                # JSONL, which is the one artifact a reviewer never opens.
                rejected = r.get("cites_rejected") or []
                if rejected and not r.get("cites"):
                    out += [f"> *Cited {len(rejected)} passage(s), none of which "
                            f"resolved against the document — this verdict rests "
                            f"on no checkable evidence.*", ""]
                elif rejected:
                    out += [f"> *{len(rejected)} further citation(s) did not "
                            f"resolve.*", ""]
            if heads:
                out += ["**Where to look.**", ""]
                for h, f in list(zip(heads, finds))[:4]:
                    out += [f"- Under *{h}* — search for “{f}”"]
                out += [""]
            out += ["**Your call:** ______", "", "---", ""]

    path = os.path.expanduser(args.out)
    open(path, "w", encoding="utf-8").write("\n".join(out))
    print(f"  {args.title}: {len(ids)} rows "
          f"({len(buckets['SPLIT'])} split, {len(buckets['DIFFER'])} differ, "
          f"{len(buckets['AGREE'])} agree) -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
