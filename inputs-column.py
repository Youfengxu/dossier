#!/usr/bin/env python3
"""Turn two models' adjudication into one column a human can sign their name to.

The dual-judge sheet is working material: two verdicts, two rationales, a flag,
and a column where the reviewer says which one they believe. What goes back to
the client is one sentence per comment, in a voice that does not announce it was
machine-written.

    ./inputs-column.py --xlsx D2-edited.xlsx --sheet "D2 - Architecture" \
        --out D2-cleaned.xlsx --by Paul

Selection follows the reviewer's column: named model wins, "addressed" means
they judged it addressed themselves, blank means the default model. A verdict of
"addressed" collapses to the single word — there is nothing to explain when the
answer is yes, and a paragraph justifying a pass reads like padding.

The rewrite is a local model. Every rationale is a sentence about the client's
document, and this toolkit keeps that text on hardware the reviewer owns.
"""

import argparse
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from llm import Client, LLMError                             # noqa: E402
import matrix as matrix_reader                               # noqa: E402
import writeback                                             # noqa: E402

PROMPT_VERSION = "inputs-1"

WORD = {"not_addressed": "Unaddressed", "partial": "Partial",
        "addressed": "Addressed", "unclear": "Unclear"}

SYSTEM = """You rewrite one review note so it reads as though a person wrote it
while working through a spreadsheet.

Reply with JSON only: {"text":"..."}

Rules:
  - Drop every phrase that narrates the review itself. No "the reviewer asked",
    "the comment asks", "the document states that", "this response does not",
    "the diff shows", "based on the provided text". Say what is missing or
    present, directly.
  - Write in the third person about the deliverable. "The section does not
    define X." Not "I found that..." and not "You did not..."
  - Keep every specific: section numbers, defined terms, named artefacts,
    quantities. Those are the substance. Do not invent any.
  - One or two sentences. Plain professional English. No bullet points, no
    headings, no closing pleasantries, no hedging like "it appears that".
  - Do not restate the verdict word (Addressed / Partial / Unaddressed); it is
    printed separately in front of your sentence.

If the note is already clean, return it with only the banned phrasing removed."""


def validate(obj):
    if not isinstance(obj.get("text"), str) or not obj["text"].strip():
        return "'text' must be a non-empty string"
    return None


# Tool provenance the reviewer never wrote and would not: a trailing bracket
# naming the sections the judgement was based on. Useful in the working sheet,
# noise in the column that goes out.
PROVENANCE = re.compile(r"\s*\[(judged against|§)[^\]]*\]\s*$")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--xlsx", required=True)
    parser.add_argument("--sheet", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--id-col", default="A")
    parser.add_argument("--a-verdict", default="M")
    parser.add_argument("--a-rationale", default="N")
    parser.add_argument("--b-verdict", default="O")
    parser.add_argument("--b-rationale", default="P")
    parser.add_argument("--pick-col", default="R")
    parser.add_argument("--target-col", default="K")
    parser.add_argument("--by-col", default="L")
    parser.add_argument("--by", default="REDACTED-45")
    parser.add_argument("--default-model", default="coder",
                        help="which model an empty pick means")
    parser.add_argument("--model")
    parser.add_argument("--url")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--max-tokens", type=int, default=700)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rows = matrix_reader.read_sheet(args.xlsx, args.sheet)
    project = os.path.dirname(os.path.abspath(args.xlsx))

    work, plan = [], []
    for position, row in enumerate(rows[1:], start=2):
        rid = row.get(args.id_col, "").strip()
        if not rid:
            continue
        coder_v = row.get(args.a_verdict, "").strip()
        coder_r = row.get(args.a_rationale, "").strip()
        glim_v = row.get(args.b_verdict, "").strip()
        glim_r = row.get(args.b_rationale, "").strip()
        if not coder_v and not glim_v:
            continue
        pick = row.get(args.pick_col, "").strip().lower()

        if pick in ("addressed", "address", "addresed"):
            source, verdict, rationale = "reviewer", "addressed", ""
        elif pick.startswith("glim"):
            source, verdict, rationale = "glimmer", glim_v, glim_r
        else:
            source, verdict, rationale = (
                ("coder" if pick else f"{args.default_model} (default)"),
                coder_v, coder_r)

        if verdict == "addressed":
            plan.append((position, rid, source, verdict, None))
            continue
        plan.append((position, rid, source, verdict, len(work)))
        work.append(PROVENANCE.sub("", rationale).strip())

    print(f"{len(plan)} row(s): {sum(1 for p in plan if p[4] is None)} collapse "
          f"to 'Addressed', {len(work)} rewritten")
    for position, rid, source, verdict, slot in plan:
        if source.endswith("(default)") or source == "reviewer":
            print(f"  {rid:8} {WORD.get(verdict, verdict):<12} <- {source}")

    if args.dry_run:
        return 0

    kwargs = {}
    if args.model:
        kwargs["model"] = args.model
    if args.url:
        kwargs["url"] = args.url
    client = Client(project, prompt_version=PROMPT_VERSION,
                    max_tokens=args.max_tokens, **kwargs)

    def rewrite(text):
        if not text:
            return ""
        try:
            return client.ask(SYSTEM, text, validate=validate,
                              label="rw")["text"].strip()
        except LLMError:
            # Better the original sentence than a blank cell: the reviewer can
            # edit prose, but cannot recover a note that was silently dropped.
            return text

    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        rewritten = list(pool.map(rewrite, work))

    values, by = {}, {}
    for position, rid, source, verdict, slot in plan:
        word = WORD.get(verdict, verdict)
        if slot is None:
            values[str(position)] = "Addressed"
        else:
            note = rewritten[slot]
            values[str(position)] = f"{word}. {note}" if note else word
        by[str(position)] = args.by

    out_path = os.path.abspath(os.path.expanduser(args.out))
    written, _ = writeback.annotate(args.xlsx, out_path, args.sheet, values,
                                    args.target_col, True)
    writeback.annotate(out_path, out_path + ".tmp", args.sheet, by,
                       args.by_col, True)
    os.replace(out_path + ".tmp", out_path)
    print(f"\nwrote {out_path}  ({written} rows in {args.target_col}, "
          f"'{args.by}' in {args.by_col})")
    print(f"model calls: {client.stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
