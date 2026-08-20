#!/usr/bin/env python3
"""Assess every matrix row against the whole current document.

    ./assess.py --project . --doc deliverable-v2 \
        --xlsx matrix.xlsx --sheet "Comments" \
        --model Qwen3-Coder-Next-UD-Q4_K_M \
        --url http://192.168.100.148:8085/v1/chat/completions \
        --out out/coder.jsonl

DIFFERENT QUESTION FROM adjudicate.py, AND THAT IS THE POINT. That tool asks
whether a REVISION addressed a comment, and answers from the diff between two
frozen drafts. When the revision changed one line in 422, the diff has already
answered every row and a model adds nothing.

This asks the other question: does the document IN HAND satisfy the comment at
all? A deliverable can meet a request in text that was there before the comment
was written, and the diff cannot see that — it reports "unchanged" either way.
Run both: the diff establishes what moved, this establishes what is true now.

WHOLE DOCUMENT, ALWAYS. These deliverables are 8,800 and 14,690 tokens. There is
no retrieval, no section resolution, and therefore none of the failures that come
with them — no reference resolving to a table of contents, no heading renumbered
out from under a citation, no detail lost because it lived in a table. The
document is the system message and never varies, so the model pays one prefill
and every row after it reuses the prefix.

CITATION BY LINE RANGE. The document goes in numbered and the model returns
ranges, which are rendered from the frozen text rather than reproduced by the
model. Asked to reproduce quotations against a corpus like this, four model
families were verbatim on 33 of 65 attempts, and normalising punctuation
recovered none of the rest. A range can be wrong; it cannot be invented.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import vocabulary                                          # noqa: E402
import llm
from llm import load_doc                                       # noqa: E402

SYSTEM = """You are reviewing one engineering deliverable against a client's
review comment. The whole document is below, with every line numbered.

Reply with JSON only:
{"verdict": "addressed|partial|not_addressed|unclear",
 "rationale": "...", "cites": ["120-134", "355-360"]}

  - "verdict": does the document AS IT STANDS satisfy the comment?
      addressed     the document does what the comment asks
      partial       some of it, with a specific remainder missing
      not_addressed the document does not do it
      unclear       the comment cannot be assessed against this document
    Do not consider whether anything was recently changed. You are judging the
    text in front of you, not a revision history.
  - "rationale": two or three sentences. Name the sections and defined terms the
    document actually uses. If something is missing, say what and where you
    looked. Do not restate the comment.
  - "cites": line ranges from the margin numbers that support your rationale.
    Two to five, each under 25 lines. Do not quote text — the ranges are
    rendered from the document itself.

Judge only against this document. Absence is a real answer; say so plainly rather
than filling the space.

=== DOCUMENT (line-numbered) ===
%s
=== END DOCUMENT ==="""

VOCAB = vocabulary.load()          # replaced per-project in main()
RANGE = re.compile(r"^\s*(\d+)\s*(?:[-:\u2013\u2014]\s*(\d+))?\s*$")
ESCALATION = 3


def ask(url, model, system, user, max_tokens, timeout):
    """Delegates to the shared client; the guards live there, not here."""
    return llm.chat(url, model, system, user,
                    max_tokens=max_tokens, timeout=timeout)

def parse(raw):
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        try:
            o = json.loads(m.group(0))
            v = (o.get("verdict") or "").strip().lower()
            return {"verdict": VOCAB.coerce(v),
                    "rationale": (o.get("rationale") or "").strip(),
                    "cites": [c for c in (o.get("cites") or []) if isinstance(c, str)],
                    "unparsed": False}
        except json.JSONDecodeError:
            pass
    hit = re.search(r'"rationale"\s*:\s*"((?:[^"\\]|\\.)*)"', raw, re.S)
    v = re.search(r'"verdict"\s*:\s*"(\w+)"', raw)
    return {"verdict": VOCAB.coerce(v.group(1) if v else ""),
            "rationale": (json.loads('"' + hit.group(1) + '"') if hit else raw)[:1500],
            "cites": re.findall(r'"(\d+\s*-\s*\d+)"', raw), "unparsed": True}


def check(lines, cites):
    """Ranges that actually resolve, and why the others do not."""
    good, bad = [], []
    for c in cites:
        m = RANGE.match(c)
        if not m:
            bad.append((c, "not a line range")); continue
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if a < 0 or b >= len(lines) or a > b:
            bad.append((c, f"outside 0-{len(lines)-1}")); continue
        if b - a > 60:
            bad.append((c, f"too wide ({b-a} lines)")); continue
        good.append((a, b))
    return good, bad


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", required=True)
    p.add_argument("--doc", required=True)
    p.add_argument("--xlsx", "--matrix", required=True)
    p.add_argument("--sheet", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--url", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--id-col", default="A")
    p.add_argument("--section-col", default="C")
    p.add_argument("--comment-col", default="D")
    p.add_argument("--action-col", default="")
    p.add_argument("--label", default="")
    p.add_argument("--max-tokens", type=int, default=2000)
    p.add_argument("--max-tokens-cap", type=int, default=8000)
    p.add_argument("--timeout", type=int, default=1200)
    args = p.parse_args()

    import matrix as matrix_reader
    project = os.path.abspath(os.path.expanduser(args.project))
    global VOCAB
    VOCAB = vocabulary.load(project)
    meta, lines = load_doc(project, args.doc)
    system = SYSTEM % "\n".join(f"{i:>5} | {l}" for i, l in enumerate(lines))
    label = args.label or args.model

    xlsx = args.xlsx if os.path.isabs(args.xlsx) else os.path.join(project, args.xlsx)
    rows = matrix_reader.read_sheet(xlsx, args.sheet)
    work = []
    for pos, r in enumerate(rows[1:], start=2):
        rid = (r.get(args.id_col, "") or "").strip()
        comment = (r.get(args.comment_col, "") or "").strip()
        if rid and comment:
            work.append((pos, rid, r))
    print(f"  {args.doc}: {len(lines):,} lines, {len(system):,} chars of prefix")
    print(f"  {args.sheet}: {len(work)} row(s) carrying a comment\n")

    out = os.path.abspath(os.path.expanduser(args.out))
    done = set()
    if os.path.exists(out):
        for line in open(out, encoding="utf-8"):
            try:
                done.add(json.loads(line)["id"])
            except Exception:
                continue

    for pos, rid, r in work:
        if rid in done:
            print(f"  {rid}: done, skipping"); continue
        parts = [f"COMMENT ID: {rid}"]
        if (r.get(args.section_col, "") or "").strip():
            parts.append(f"SECTION REFERENCE GIVEN BY THE REVIEWER: "
                         f"{r[args.section_col].strip()}  (written against an "
                         f"earlier revision; find the matching section here)")
        parts += ["", "THE COMMENT:", r[args.comment_col].strip()]
        if args.action_col and (r.get(args.action_col, "") or "").strip():
            parts += ["", "WHAT THE RESPONDING PARTY SAYS THEY DID (a claim, not "
                      "evidence — judge the document, not this):",
                      r[args.action_col].strip()]
        budget, started = args.max_tokens, time.time()
        for attempt in range(1, 4):
            try:
                content, reasoning, why = ask(args.url, args.model, system,
                                              "\n".join(parts), budget, args.timeout)
            except Exception as e:
                print(f"  {rid}: {type(e).__name__}: {e}", file=sys.stderr)
                if attempt == 3:
                    sys.exit(3)
                time.sleep(15 * attempt); continue
            if content and why != "length":
                break
            raised = min(budget * ESCALATION, args.max_tokens_cap)
            if raised == budget:
                break
            print(f"  {rid}: {'empty' if not content else 'truncated'}; "
                  f"{budget} -> {raised}", file=sys.stderr)
            budget = raised
        else:
            sys.exit(4)

        obj = parse(content)
        good, bad = check(lines, obj["cites"])
        # Stamp the exact text this answer was produced against. Locators are
        # line numbers, so they mean nothing without the version they index into:
        # after a refreeze the document still verifies against its own manifest,
        # the citations still fall inside it, and every one of them now points at
        # different text. Nothing downstream could detect that, because nothing
        # upstream recorded it.
        rec = {"id": rid, "row": pos, "model": label, "verdict": obj["verdict"],
               "doc": args.doc, "doc_sha256": meta.get("text_sha256"),
               "rationale": obj["rationale"],
               "cites": [f"{args.doc}:{a}-{b}" for a, b in good],
               "cites_rejected": [f"{c} ({why})" for c, why in bad],
               "unparsed": obj["unparsed"],
               "seconds": round(time.time() - started, 1)}
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n"); fh.flush(); os.fsync(fh.fileno())
        flag = "  NOT JSON" if obj["unparsed"] else ""
        print(f"  {rid}: {rec['verdict']:<14} cites {len(good)} ok"
              f"{f', {len(bad)} rejected' if bad else ''}  "
              f"{rec['seconds']}s{flag}")

    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
