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
import vocabulary                                          # noqa: E402
import matrix as matrix_reader                               # noqa: E402
from llm import load_doc                                     # noqa: E402

VOCAB = vocabulary.load()          # replaced per-project in main()
HEADING = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?[A-Z][^.!?]{2,78}$")

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


def nearest_heading(lines, n):
    # No clamping. min(n, len(lines)-1) meant a locator up to 400 lines PAST the
    # end of the document returned the document's last heading and an empty
    # search phrase, and the report read: search for "". No error, no warning,
    # exit 0 — a stale locator presenting as a finding.
    if not 0 <= n < len(lines):
        return None
    for i in range(n, max(-1, n - 400), -1):
        line = lines[i].strip()
        if not line or len(line) > 80:
            continue
        if HEADING.match(line) and not line.endswith((",", ";", ":")):
            if 1 < len(line.split()) < 14:
                return line
    return None


def searchable(text, words=9):
    return " ".join(" ".join(text.split()).split(" ")[:words]).strip(" ,;:.")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", required=True)
    p.add_argument("--doc", required=True)
    p.add_argument("--xlsx", required=True)
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
    for r in matrix_reader.read_sheet(xlsx, args.sheet)[1:]:
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
                for c in r.get("cites", []):
                    try:
                        a = int(c.rsplit(":", 1)[1].split("-")[0])
                    except (IndexError, ValueError):
                        continue
                    h = nearest_heading(lines, a)
                    if h and h not in heads:
                        heads.append(h)
                        finds.append(searchable("\n".join(lines[a:a + 10])))
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
