#!/usr/bin/env python3
"""Derive deliverable-v2.md from v1 by applying declared regressions.

v2 is generated rather than hand-written so the D7 ground truth is exact: every
difference between the two revisions is one of the transformations below, and
nothing else. Run after editing v1.

    ./make-v2.py

The regressions mirror the shape of a real disclosure regression — gap registers
deleted, a risk register removed, and a heading renamed while its body stays
byte-identical, so a reader diffing prose sees almost nothing.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# Sections removed wholesale, by heading text.
DROP_SECTIONS = [
    "## 14. Not-defined register",
    "## Appendix B — Risk register",
    "## Appendix D — Evidence locations",
]

# Gap tables removed but their heading kept, so the section still looks present.
# (component, heading) -> the register rows vanish, the heading does not.
DROP_TABLES_UNDER = [
    "### 6.5 Implementation constraints and dependencies",
    "### 7.5 Implementation constraints and dependencies",
]

# Applied before section/table removal.
RENAMES = [
    ("Risks and open decisions", "Implementation constraints and dependencies"),
]


def drop_section(text, heading):
    """Remove from `heading` up to the next heading of the same or higher level."""
    level = len(heading) - len(heading.lstrip("#"))
    pattern = re.compile(
        r"^" + re.escape(heading) + r"\s*$.*?(?=^#{1," + str(level) + r"} |\Z)",
        re.M | re.S,
    )
    if not pattern.search(text):
        sys.exit(f"make-v2: heading not found, cannot drop: {heading!r}")
    return pattern.sub("", text)


def drop_table_under(text, heading):
    """Keep the heading, delete the markdown table that follows it."""
    pattern = re.compile(
        r"(^" + re.escape(heading) + r"\s*$\n\n)(\|.*?\n)+",
        re.M,
    )
    if not pattern.search(text):
        sys.exit(f"make-v2: table not found under: {heading!r}")
    return pattern.sub(
        r"\1Constraints for this component are tracked in the delivery plan.\n",
        text,
    )


def main():
    source = os.path.join(HERE, "deliverable-v1.md")
    target = os.path.join(HERE, "deliverable-v2.md")
    text = open(source, encoding="utf-8").read()

    text = text.replace("Document version 1.4 · Response to RFO",
                        "Document version 2.0 · Response to RFO")
    text = text.replace("Document version 1.4. Supersedes 1.3.",
                        "Document version 2.0. Supersedes 1.4.")

    for old, new in RENAMES:
        text = text.replace(old, new)
    for heading in DROP_TABLES_UNDER:
        text = drop_table_under(text, heading)
    for heading in DROP_SECTIONS:
        text = drop_section(text, heading)

    # Section 14 is gone, so this pointer now dangles as well — an emergent
    # consequence of the deletion rather than a separately planted defect.
    text = text.replace(
        "Full\nstatus definitions are in Section 14 and Appendix F.",
        "Full\nstatus definitions are in Section 14 and Appendix F.",
    )

    header = ("*Generated from deliverable-v1.md by make-v2.py — do not edit "
              "directly.*\n\n")
    text = re.sub(r"(^# .*?\n)", r"\1\n" + header, text, count=1, flags=re.M)

    with open(target, "w", encoding="utf-8") as handle:
        handle.write(text)

    v1_lines = open(source, encoding="utf-8").read().count("\n")
    v2_lines = text.count("\n")
    print(f"wrote {os.path.relpath(target)}  ({v1_lines} -> {v2_lines} lines)")
    for label, pattern in (("ND-", r"\bND-\d+"), ("SF-G", r"\bSF-G\d+"),
                           ("HM-G", r"\bHM-G\d+"), ("RSK-", r"\bRSK-\d+"),
                           ("'risk'", r"(?i)risk")):
        before = len(re.findall(pattern, open(source, encoding="utf-8").read()))
        after = len(re.findall(pattern, text))
        print(f"  {label:8} {before:4} -> {after:4}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
