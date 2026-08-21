#!/usr/bin/env python3
"""Gather a pipeline run into one reviewable sheet, and the same thing as prose.

The pipeline ends as a scatter of files: a coverage CSV from trace.py, a
candidates CSV from cluster-findings.py, a terms JSON from undefined.py. Each is
shaped for the tool that wrote it. A reviewer wants one list, ordered by what
deserves attention first, with somewhere to write a reply.

    ./bundle.py --project . --doc deliverable-v2 \
        --coverage cov-deliverable-v2.csv --undefined undefined-arch7.json \
        --out-xlsx out/v7-findings.xlsx \
        --out-md   out/v7-findings.md

Two outputs from one pass so they cannot disagree. The xlsx carries an empty
Comments column; the markdown carries a blank line under each finding. Both are
ordered unmet first, because a reviewer who stops halfway should have spent the
half on obligations the deliverable does not meet.

Writes xlsx by hand — inline strings, no sharedStrings table. openpyxl was not
installable on the review machine, which is why every tool here is stdlib only.
"""

import argparse
import csv
import html
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import matrix as matrix_reader
import vocabulary  # noqa: E402

# Unmet before partial before met: the order a reviewer reads in. "unverifiable"
# sits above "met" because it is a request for evidence, not a pass. Derived from
# the declared vocabulary rather than spelled out, because the words change per
# project and this ordering is a property of the scale, not of these four strings.
RANK = {v: i for i, v in enumerate(vocabulary.load(name="coverage").reading_order)}

HEADERS = ["ID", "Class", "Status", "Statement", "Locator", "Evidence",
           "Why", "Comments"]




def write_xlsx(path, sheet_name, headers, rows):
    """Minimal OOXML. Inline strings keep it to one part and one pass."""
    def cell(ref, value):
        if value is None or value == "":
            return f'<c r="{ref}" t="inlineStr"><is><t/></is></c>'
        text = html.escape(str(value), quote=False)
        # Excel rejects most control characters outright.
        text = "".join(ch for ch in text if ch >= " " or ch in "\t\n")
        return (f'<c r="{ref}" t="inlineStr"><is>'
                f'<t xml:space="preserve">{text}</t></is></c>')

    body = []
    for row_index, row in enumerate([headers] + rows, start=1):
        cells = "".join(cell(f"{matrix_reader.col_letters(i)}{row_index}", v)
                        for i, v in enumerate(row))
        body.append(f'<row r="{row_index}">{cells}</row>')

    widths = "".join(
        f'<col min="{i+1}" max="{i+1}" width="{w}" customWidth="1"/>'
        for i, w in enumerate([10, 16, 13, 60, 22, 50, 60, 34]))
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<cols>{widths}</cols>'
        f'<sheetData>{"".join(body)}</sheetData></worksheet>')
    safe = html.escape(sheet_name, quote=True)[:31]
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{safe}" sheetId="1" r:id="rId1"/></sheets></workbook>')
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '</Relationships>')
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>')
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet)


def collect(args, project):
    findings = []

    path = os.path.join(project, args.coverage) if args.coverage else None
    if path and os.path.exists(path):
        for row in csv.DictReader(open(path, encoding="utf-8")):
            verdict = (row.get("verdict") or "").strip()
            findings.append({
                "sort": (RANK.get(verdict, 9), row.get("obligation", "")),
                "id": row.get("obligation", ""),
                "class": "RFO coverage",
                "status": verdict,
                "statement": (row.get("requirement") or "").strip(),
                "locator": (row.get("locator") or "").strip()
                           or (row.get("requirement_locator") or "").strip(),
                "evidence": (row.get("quote") or "").strip(),
                "why": (row.get("reason") or "").strip(),
            })

    path = os.path.join(project, args.undefined) if args.undefined else None
    if path and os.path.exists(path):
        terms = json.load(open(path, encoding="utf-8"))
        # Frequency-ordered and capped. The detector's own scoring is not
        # trustworthy enough to paginate a reviewer through 1,187 rows; the
        # tail is noise and saying so is better than shipping it as findings.
        for item in terms[:args.max_terms]:
            findings.append({
                "sort": (4, -item.get("uses", 0)),
                "id": "",
                "class": "undefined term",
                "status": f"used {item.get('uses', 0)}x",
                "statement": item.get("term", ""),
                "locator": item.get("locator", ""),
                "evidence": item.get("quote", ""),
                "why": f"under heading: {item.get('heading','')}",
            })

    path = os.path.join(project, args.candidates) if args.candidates else None
    if path and os.path.exists(path):
        for row in csv.DictReader(open(path, encoding="utf-8")):
            # cluster-findings writes rank/obligations/strength/verdicts/
            # lead_requirement/lead_reason. A cluster is several obligations
            # failing for one underlying reason, so it belongs near the top of
            # a reviewer's list: fixing the lead may close all of them.
            members = row.get("obligations", "")
            count = len([m for m in members.split(",") if m.strip()])
            findings.append({
                "sort": (-1, -float(row.get("strength") or 0)),
                "id": f"cluster-{row.get('rank','')}",
                "class": "convergent cluster",
                "status": f"{count} obligations, strength "
                          f"{row.get('strength','')}",
                "statement": (row.get("lead_requirement") or "").strip(),
                "locator": members,
                "evidence": "",
                "why": (row.get("lead_reason") or "").strip(),
            })

    findings.sort(key=lambda f: f["sort"])
    return findings


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True)
    parser.add_argument("--doc", required=True)
    parser.add_argument("--coverage")
    parser.add_argument("--undefined")
    parser.add_argument("--candidates")
    parser.add_argument("--max-terms", type=int, default=40)
    parser.add_argument("--title")
    parser.add_argument("--out-xlsx", required=True)
    parser.add_argument("--out-md", required=True)
    args = parser.parse_args()

    project = os.path.abspath(os.path.expanduser(args.project))
    findings = collect(args, project)
    if not findings:
        sys.exit("no inputs found — check --coverage/--undefined/--candidates")

    rows = [[f["id"], f["class"], f["status"], f["statement"], f["locator"],
             f["evidence"], f["why"], ""] for f in findings]
    xlsx_path = os.path.abspath(os.path.expanduser(args.out_xlsx))
    write_xlsx(xlsx_path, args.doc[:31], HEADERS, rows)

    title = args.title or f"{args.doc} — pipeline findings"
    counts = {}
    for f in findings:
        key = f["class"] if f["class"] != "RFO coverage" \
            else f"RFO coverage: {f['status']}"
        counts[key] = counts.get(key, 0) + 1

    out = [f"# {title}", ""]
    out.append(" ".join(
        f"{len(findings)} findings from one pipeline run against {args.doc}."
        " Ordered so the obligations the deliverable does not meet come first."
        " Every locator is a line number in the frozen text, so it points at"
        " the same place tomorrow.".split()))
    out.append("")
    for key in sorted(counts):
        out.append(f"- {counts[key]} — {key}")
    out.append("")

    current = None
    for f in findings:
        group = f["class"] if f["class"] != "RFO coverage" \
            else f"RFO coverage — {f['status']}"
        if group != current:
            current = group
            out.append(f"## {group}")
            out.append("")
        head = f"### {f['id']} " if f["id"] else "### "
        out.append((head + (f["statement"][:90] if not f["id"] else "")).strip())
        out.append("")
        if f["id"] and f["statement"]:
            out.append(f"**Requirement.** {' '.join(f['statement'].split())}")
            out.append("")
        if f["why"]:
            out.append(f"**Finding.** {' '.join(f['why'].split())}")
            out.append("")
        if f["evidence"]:
            out.append(f"**Evidence.** {' '.join(f['evidence'].split())}")
            out.append("")
        if f["locator"]:
            out.append(f"**Where.** {f['locator']}")
            out.append("")
        out.append("**Your comment:** ______")
        out.append("")

    md_path = os.path.abspath(os.path.expanduser(args.out_md))
    os.makedirs(os.path.dirname(md_path) or ".", exist_ok=True)
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(out).rstrip() + "\n")

    print(f"wrote {xlsx_path}")
    print(f"wrote {md_path}")
    print(f"  {len(findings)} findings: "
          + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
