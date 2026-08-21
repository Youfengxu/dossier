#!/usr/bin/env python3
"""Test a vendor's claim of what they changed against what the documents show.

A feedback matrix usually carries a column in which the responding party states
what they did — "Adjusted", "Updated", "Added in the new document". That column
is an assertion about a diff, and a diff is a fact. Nothing here needs a model:
either the section the row points at moved between two frozen revisions, or it
did not.

    ./action-check.py --project . --xlsx matrix.xlsx --sheet "Comments" \\
        --from draft-a --to draft-b --out checked.xlsx

WHY THIS EXISTS. On one review a revision arrived claiming changes against
seventy-seven open comments. The two documents were 99.93% identical: four
changed lines in 5,959, no insertions, no deletions, and every embedded image
byte-for-byte the same. Running a retrieval-and-judge pipeline over those rows
would have cost hours and produced seventy-seven "not addressed" verdicts that
this check produces in a second, with a stronger warrant — a model's opinion
that a gap remains is weaker evidence than the text being unchanged.

The verdicts are about the CLAIM, not about the comment:

  DISPUTED         the row claims a change; the section it names is byte-identical
  SUPPORTED        the row claims a change; that section did move
  MIXED            several sections named, some moved and some did not
  NO SECTION       a change is claimed but no section is named or resolvable —
                   reported with how much the document moved overall, since on a
                   near-static revision that is itself the answer
  SECTION MISSING  the named section is not in the new revision at all
  NO CLAIM         the cell does not assert that anything was done

A DISPUTED row is not proof of bad faith. The claim may refer to a different
deliverable, or to a change made and then lost. It means the document in hand
does not support the sentence in the matrix, which is the thing a reviewer has
to raise.
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import adjudicate                                            # noqa: E402
import matrix as matrix_reader                               # noqa: E402
import writeback                                             # noqa: E402
from llm import content_lines, load_doc                                     # noqa: E402

# Verbs that assert work was done to the document. Deliberately narrow: the
# point is to separate "we changed it" from "we will", "we disagree" and "we
# think it is already fine", because only the first is a claim a diff can test.
CLAIMED = re.compile(
    r"\b(adjust|updat|amend|revis|add|added|includ|insert|remov|delet|"
    r"rewrit|rewrote|clarif|defin|correct|expand|renam|reorganis|reorganiz|"
    r"restructur|incorporat|address|resolv|chang|modif)\w*", re.I)

# Words that withdraw the claim even when a verb above is present: "will update",
# "intended to update", "to be updated" are all statements about the future.
DEFERRED = re.compile(r"\b(will|shall|intend|plan|to be|going to|next|future|"
                      r"pending|plann)\w*\b", re.I)

# Section numbers written any of the ways a person writes them in a cell.
# More candidate headings than this for one number means the number is being
# used for something other than sections — almost always numbered list items.
AMBIGUOUS_AT = 2

SECTION = re.compile(r"(?:§|\bsection\s+|\bsec\.?\s+)(\d+(?:\.\d+)*)", re.I)
BARE = re.compile(r"\b(\d+\.\d+(?:\.\d+)*)\b")


def sections_named(*cells):
    """Every section number these cells point at, in order, deduplicated."""
    found = []
    for cell in cells:
        text = cell or ""
        for match in list(SECTION.finditer(text)) + list(BARE.finditer(text)):
            number = match.group(1)
            if number not in found:
                found.append(number)
    return found


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True)
    parser.add_argument("--xlsx", "--matrix", required=True)
    parser.add_argument("--sheet", required=True)
    parser.add_argument("--from", dest="old", required=True)
    parser.add_argument("--to", dest="new", required=True)
    parser.add_argument("--id-col", default="A")
    parser.add_argument("--section-col", default="C")
    parser.add_argument("--action-col", default="F",
                        help="the column stating what the vendor did")
    parser.add_argument("--filter-col",
                        help="only check rows whose value here matches "
                             "--filter-values (e.g. a prior verdict column)")
    parser.add_argument("--filter-values", default="Partial,Unaddressed")
    parser.add_argument("--verdict-col", default="M")
    parser.add_argument("--evidence-col", default="N")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    project = os.path.abspath(os.path.expanduser(args.project))
    _o, old_lines = load_doc(project, args.old)
    _n, new_lines = load_doc(project, args.new)
    old_index = adjudicate.index_headings(old_lines)
    new_index = adjudicate.index_headings(new_lines)

    # How much the revision moved overall. A row that names no section still
    # deserves an answer, and on a near-static revision this IS the answer.
    import difflib
    old_content, new_content = content_lines(old_lines), content_lines(new_lines)
    opcodes = difflib.SequenceMatcher(None, old_content,
                                      new_content).get_opcodes()
    moved = sum(max(i2 - i1, j2 - j1) for t, i1, i2, j1, j2 in opcodes
                if t != "equal")
    print(f"{args.old} -> {args.new}: {len(old_content)} content lines to "
          f"{len(new_content)}, {moved} changed\n")

    xlsx = args.xlsx if os.path.isabs(args.xlsx) \
        else os.path.join(project, args.xlsx)
    rows = matrix_reader.read_sheet(xlsx, args.sheet)
    # --*-col accepts a header name as well as a letter; resolved here so
    # nothing below has to care which one the operator typed.
    matrix_reader.resolve_columns(rows, args)
    wanted = {v.strip().lower() for v in args.filter_values.split(",")}

    verdicts, evidence, counts = {}, {}, {}
    for position, row in matrix_reader.numbered(rows):
        rid = row.get(args.id_col, "").strip()
        if not rid:
            continue
        if args.filter_col:
            value = row.get(args.filter_col, "").strip().split(".")[0].lower()
            if value not in wanted:
                continue

        action = row.get(args.action_col, "").strip()
        if not action:
            state, note = "NO CLAIM", "the action column is empty"
        elif not CLAIMED.search(action) or DEFERRED.search(action):
            state = "NO CLAIM"
            note = ("states an intention or a position rather than a completed "
                    f"change: {action[:70]!r}")
        else:
            refs = sections_named(row.get(args.section_col, ""), action)
            if not refs:
                state = "NO SECTION"
                note = (f"claims a change but names no section; across the whole "
                        f"document {moved} of {len(new_content)} lines differ "
                        f"between {args.old} and {args.new}")
            else:
                changed, same, missing, ambiguous = [], [], [], []
                for ref in refs:
                    # A bare top-level number is not only a section number. These
                    # documents number their procedure steps too, so "10." opens
                    # nine different list items and index_headings collects all
                    # of them; pick() then takes whichever has the most body,
                    # which is arbitrary. Resolving §1 to lines 5919-5960 of a
                    # 5960-line document is how this surfaced. Dotted references
                    # never collide — not once in this corpus — so the rule is
                    # narrow: refuse where the number is genuinely contested and
                    # say so, rather than compare the wrong text and report a
                    # verdict about it.
                    if len(new_index.get(ref, [])) > AMBIGUOUS_AT:
                        ambiguous.append(ref)
                        continue
                    new_hit = adjudicate.pick(new_index, ref)
                    old_hit = adjudicate.pick(old_index, ref)
                    if not new_hit:
                        missing.append(ref)
                        continue
                    new_body = "\n".join(new_lines[new_hit[1]:new_hit[2]])
                    old_body = "\n".join(old_lines[old_hit[1]:old_hit[2]]) \
                        if old_hit else ""
                    (same if old_body == new_body else changed).append(
                        (ref, new_hit[1], new_hit[2]))
                where = lambda items: ", ".join(
                    f"§{r} ({args.new}:{s}-{e})" for r, s, e in items)
                if ambiguous and not changed and not same:
                    state = "UNRESOLVED"
                    note = (f"§{', §'.join(ambiguous)} matches "
                            f"{len(new_index.get(ambiguous[0], []))} places in "
                            f"{args.new} — the document numbers list items the "
                            f"same way it numbers sections, so this reference "
                            f"cannot be resolved mechanically. Read it by hand.")
                elif missing and not changed and not same:
                    state = "SECTION MISSING"
                    note = (f"§{', §'.join(missing)} named by the row does not "
                            f"exist in {args.new}")
                elif changed and same:
                    state = "MIXED"
                    note = (f"changed: {where(changed)}; byte-identical: "
                            f"{where(same)}")
                elif changed:
                    state = "SUPPORTED"
                    note = f"changed between {args.old} and {args.new}: {where(changed)}"
                else:
                    state = "DISPUTED"
                    note = (f"the row states {action[:40]!r}, but "
                            f"{where(same)} is byte-identical to {args.old}")

        verdicts[str(position)] = state
        evidence[str(position)] = note
        counts[state] = counts.get(state, 0) + 1

    print("=" * 74)
    for state in ("DISPUTED", "MIXED", "SUPPORTED", "UNRESOLVED", "NO SECTION",
                  "SECTION MISSING", "NO CLAIM"):
        if counts.get(state):
            print(f"  {state:<16} {counts[state]}")
    print("=" * 74)
    print("A DISPUTED row is not proof of bad faith — the claim may point at\n"
          "another deliverable, or at a change made and then lost. It means the\n"
          "document in hand does not support the sentence in the matrix.")

    out_path = args.out if os.path.isabs(args.out) \
        else os.path.join(project, args.out)
    written, _ = writeback.annotate(xlsx, out_path, args.sheet, verdicts,
                                    args.verdict_col, True)
    writeback.annotate(out_path, out_path + ".tmp", args.sheet, evidence,
                       args.evidence_col, True)
    os.replace(out_path + ".tmp", out_path)
    print(f"\nwrote {out_path}  ({written} rows in {args.verdict_col}, "
          f"evidence in {args.evidence_col})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
