#!/usr/bin/env python3
"""Check the comment fixture's ground truth against the documents themselves.

    ./verify-comments.py

A fixture whose answer key is asserted rather than derived is worth very little:
it tests a tool against its author's memory. So every `changed` claim here is
re-derived from deliverable-v1 and deliverable-v2 on each run, and the totals are
recounted from the rows.

This caught its own author. FT-009 was recorded as `changed: none` on the
reasoning that nothing relevant to the comment had moved — but the generator bumps
a version string inside that very section, so the section had in fact changed.
The entry is now the more interesting case it always was: a section that moved for
reasons unrelated to the comment, which is exactly what fools a diff-based reader.
"""

import collections
import csv
import re
import sys

import yaml


def body(text, ref):
    pattern = (rf"^#+ {re.escape(ref)}[.\s].*?$(.*?)(?=^#{{1,3}} |\Z)"
               if ref[0].isdigit()
               else rf"^#+ {re.escape(ref)}.*?$(.*?)(?=^#{{1,2}} |\Z)")
    found = re.search(pattern, text, re.M | re.S)
    return found.group(1).strip() if found else None


def heading(text, ref):
    found = re.search(rf"^#+ {re.escape(ref)}(.*)$", text, re.M)
    return found.group(1).strip() if found else None


def main():
    v1 = open("deliverable-v1.md", encoding="utf-8").read()
    v2 = open("deliverable-v2.md", encoding="utf-8").read()
    rows = {r["id"]: r for r in csv.DictReader(open("comments.csv", encoding="utf-8"))}
    truth = yaml.safe_load(open("comments-truth.yaml", encoding="utf-8"))
    entries, problems = truth["comments"], []

    for key in rows:
        if key not in entries:
            problems.append(f"{key}: in comments.csv, absent from the answer key")
    for key in entries:
        if key not in rows:
            problems.append(f"{key}: in the answer key, absent from comments.csv")

    for key, row in rows.items():
        ref = row["section_ref"]
        if body(v1, ref) is None:
            problems.append(f"{key}: section {ref!r} does not exist in deliverable-v1")
            continue
        before, after = body(v1, ref), body(v2, ref)
        claimed = entries[key]["changed"]
        if claimed == "cosmetic":
            same_body = before == after
            moved_heading = heading(v1, ref) != heading(v2, ref)
            if not (same_body and moved_heading):
                problems.append(f"{key}: claims cosmetic, but body identical="
                                f"{same_body} heading moved={moved_heading}")
            continue
        actual = "none" if after is not None and before == after else "substantive"
        if claimed != actual:
            problems.append(f"{key}: key says changed={claimed}, "
                            f"documents say {actual}")

    seen = collections.Counter(v["changed"] for v in entries.values())
    totals = truth["totals"]
    counted = {
        "comments": len(entries),
        "satisfied_v2_met": sum(1 for v in entries.values()
                                if v["satisfied_v2"] == "met"),
        "changed_none": seen["none"],
        "changed_cosmetic": seen["cosmetic"],
        "changed_substantive": seen["substantive"],
        "claims_unsupported": sum(1 for v in entries.values()
                                  if v.get("claim_supported") is False),
    }
    for label, got in counted.items():
        if totals.get(label) != got:
            problems.append(f"totals.{label}: key says {totals.get(label)}, "
                            f"rows give {got}")

    if problems:
        print("  " + "\n  ".join(problems))
        return 1
    print(f"  ground truth verified against both revisions — no discrepancies\n")
    print(f"  {counted['comments']} comments")
    print(f"  {counted['satisfied_v2_met']} satisfied by standing text "
          f"(invisible to a diff)")
    print(f"  {counted['changed_cosmetic']} changed cosmetically only "
          f"(a heading renamed over an identical body)")
    print(f"  {counted['claims_unsupported']} vendor claims the documents "
          f"do not support")
    return 0


if __name__ == "__main__":
    sys.exit(main())
