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

import locate

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import inventory  # noqa: E402
import matrix as matrix_reader
import vocabulary  # noqa: E402

# Unmet before partial before met: the order a reviewer reads in. "unverifiable"
# sits above "met" because it is a request for evidence, not a pass. Derived from
# the declared vocabulary rather than spelled out, because the words change per
# project and this ordering is a property of the scale, not of these four strings.
RANK = {v: i for i, v in enumerate(vocabulary.load(name="coverage").reading_order)}

HEADERS = ["ID", "Class", "Status", "Statement", "Locator", "Evidence",
           "Why", "Comments"]




BODY_STYLE, HEADER_STYLE = 1, 2

# WHY THIS PART EXISTS AT ALL. The workbook had no styles.xml, and without one
# there is no cellXfs entry to carry alignment, so wrapText is not merely unset —
# it is unsettable. Every cell then takes the default format, where a newline is
# STORED but never shown as a break and the text is clipped at the column
# boundary as soon as the neighbouring cell is non-empty. Six evidence cells in
# the floodtwin fixture hold 194-519 characters over several lines, and column G
# is populated in all six, so a reviewer sees roughly the first fifty characters
# of a quote and nothing indicating there is more. The markdown bug at least put
# every character on the page.
#
# Element order is fixed by the ECMA-376 CT_Stylesheet sequence — fonts, fills,
# borders, cellStyleXfs, cellXfs — and fill 0 = none with fill 1 = gray125 is the
# conventional preamble even though neither is used here.
#
# NOTHING AVAILABLE HERE ENFORCES THAT. LibreOffice opens this workbook, and it
# also opens one with cellXfs moved in front of fonts, so a successful convert
# proves the file is readable and proves nothing about the ordering. Excel is
# stricter about the sequence, but there is no Excel on this machine and that
# was not tested — test_styles_follow_the_schema_sequence asserts the order
# directly, because the assertion is the only enforcement there is.
STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<fonts count="2">'
    '<font><sz val="11"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><name val="Calibri"/></font>'
    '</fonts>'
    '<fills count="2">'
    '<fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill>'
    '</fills>'
    '<borders count="1"><border/></borders>'
    '<cellStyleXfs count="1">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>'
    '</cellStyleXfs>'
    '<cellXfs count="3">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"'
    ' applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0"'
    ' applyFont="1" applyAlignment="1">'
    '<alignment vertical="top" wrapText="1"/></xf>'
    '</cellXfs>'
    '</styleSheet>')


def write_xlsx(path, sheet_name, headers, rows):
    """Minimal OOXML. Inline strings keep it to one part and one pass."""
    def cell(ref, value, style):
        if value is None or value == "":
            return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t/></is></c>'
        text = html.escape(str(value), quote=False)
        # Excel rejects most control characters outright.
        text = "".join(ch for ch in text if ch >= " " or ch in "\t\n")
        return (f'<c r="{ref}" s="{style}" t="inlineStr"><is>'
                f'<t xml:space="preserve">{text}</t></is></c>')

    body = []
    for row_index, row in enumerate([headers] + rows, start=1):
        style = HEADER_STYLE if row_index == 1 else BODY_STYLE
        cells = "".join(cell(f"{matrix_reader.col_letters(i)}{row_index}", v, style)
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
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/styles" Target="styles.xml"/>'
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
        'openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/styles.xml", STYLES)
        z.writestr("xl/worksheets/sheet1.xml", sheet)


QUOTE_ROWS = 12          # a table longer than this is summarised, not pasted


def undefined_terms(data):
    """(the terms undefined.py wrote, what it wrote of the sections behind
    them, or None).

    The file is an object since 2026-10-04: the terms, and how many of the
    document's sections were asked about and answered. Before that it was the
    bare list of terms. Such a file is still read, and says nothing of its
    sections, which is not the same as saying that every one was read.

    Anything else is not a terms file, and is refused. Read as a file with no
    term in it, an object with no "terms" gave a bundle that listed none and
    said nothing of why.
    """
    if isinstance(data, list):
        terms, read = data, None
    elif isinstance(data, dict):
        terms, read = data.get("terms"), data.get("sections")
    else:
        terms, read = None, None
    if not isinstance(terms, list) or \
            not all(isinstance(term, dict) for term in terms):
        raise ValueError('not a terms file: it is neither a list of terms '
                         'nor an object with one under "terms"')
    return terms, read if inventory.is_answers(read) else None


# Said of a terms file written before undefined.py recorded its sections. It
# is not called partial: nothing in it says that a section went unread.
UNSAID = ("NOT KNOWN TO BE THE WHOLE DOCUMENT: the undefined terms come from "
          "a file that does not say how many sections of the document were "
          "asked about")


def terms_note(read):
    """What the bundle says when the terms did not come from every section,
    or "" when they did. A reviewer reads the bundle, not the run that made
    it, and a list of undefined terms reads as the document's.

    Three things are told apart, each under its own words: a file that does
    not say, a run no section answered, and a run some did not.
    """
    if read is None:
        return UNSAID
    if not read["answered"]:
        return (f"NOTHING WAS READ: undefined terms were looked for in 0 of "
                f"the document's {read['of']} sections. That none is listed "
                f"says nothing about the document")
    if read["not_read"]:
        return (f"NOT THE WHOLE DOCUMENT: undefined terms were looked for in "
                f"{read['answered']} of the document's {read['of']} sections; "
                f"a term that only one of the others would have raised is "
                f"not listed")
    return ""


def collect(args, project):
    """(the findings, what the bundle has to say about how they were come
    by)."""
    findings, notes = [], []

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
        with open(path, encoding="utf-8") as handle:
            try:
                terms, read = undefined_terms(json.load(handle))
            except ValueError as error:
                sys.exit(f"{args.undefined}: {error}")
        if terms_note(read):
            notes.append(terms_note(read))
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
    return findings, notes


def note_rows(notes):
    """The notes as rows of the sheet, to stand under its header and above
    every finding.

    They reached the page and the terminal and not the sheet, which is built
    from the findings alone: two outputs of one pass, one of which said the
    terms were looked for in 3 of 4 sections and one of which listed them as
    the document's.
    """
    return [["", "about this bundle", "", note, "", "", "", ""]
            for note in notes]


def nothing_to_bundle(args, project, notes):
    """What a bundle with no finding in it stops with.

    "no inputs found" was said of every such run, and sent its reader to
    check the flags. A terms file from a run no section answered is an input
    that was found, with no term in it and a record saying why: that record
    is the message.
    """
    named = [name for name in (args.coverage, args.undefined, args.candidates)
             if name and os.path.exists(os.path.join(project, name))]
    if not named:
        return "no inputs found — check --coverage/--undefined/--candidates"
    return "\n".join([f"nothing to bundle: no finding in {', '.join(named)}"]
                     + [f"  {note}" for note in notes])


def markdown(findings, counts, title, doc, notes=()):
    """The reviewer-facing report, lifted out of main() so it can be TESTED.

    It was inline, which made the only way to check its output a subprocess —
    and a subprocess is invisible to run-tests.py --mutate, which applies
    mutations in-process and never touches disk. A shelled-out test named in a
    mutation therefore reads as NOT CAUGHT no matter how good it is. Lifting the
    function is what makes the check real.
    """
    out = [f"# {title}", ""]
    out.append(" ".join(
        f"{len(findings)} findings from one pipeline run against {doc}."
        " Ordered so the obligations the deliverable does not meet come first."
        " Every locator is a line number in the frozen text, so it points at"
        " the same place tomorrow.".split()))
    out.append("")
    for key in sorted(counts):
        out.append(f"- {counts[key]} — {key}")
    for note in notes:
        out.append(f"- {note}")
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
            body, is_block = locate.quoted(f["evidence"], limit=QUOTE_ROWS)
            if is_block:
                # A table quote goes on its own lines. Collapsed onto one it is
                # a wall of pipes: present, and impossible to check against.
                out += ["**Evidence.**", "", body, ""]
            else:
                out += [f"**Evidence.** {body}", ""]
        if f["locator"]:
            out.append(f"**Where.** {f['locator']}")
            out.append("")
        out.append("**Your comment:** ______")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


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
    findings, notes = collect(args, project)
    if not findings:
        sys.exit(nothing_to_bundle(args, project, notes))

    # The SAME evidence the markdown shows. It was the raw string here and the
    # capped one there, so two artefacts of one run disagreed about what was
    # cited.
    rows = [[f["id"], f["class"], f["status"], f["statement"], f["locator"],
             locate.quoted(f["evidence"], limit=QUOTE_ROWS)[0], f["why"], ""]
            for f in findings]
    xlsx_path = os.path.abspath(os.path.expanduser(args.out_xlsx))
    write_xlsx(xlsx_path, args.doc[:31], HEADERS, note_rows(notes) + rows)

    title = args.title or f"{args.doc} — pipeline findings"
    counts = {}
    for f in findings:
        key = f["class"] if f["class"] != "RFO coverage" \
            else f"RFO coverage: {f['status']}"
        counts[key] = counts.get(key, 0) + 1

    out = markdown(findings, counts, title, args.doc, notes).splitlines()

    md_path = os.path.abspath(os.path.expanduser(args.out_md))
    os.makedirs(os.path.dirname(md_path) or ".", exist_ok=True)
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(out) + "\n")

    print(f"wrote {xlsx_path}")
    print(f"wrote {md_path}")
    print(f"  {len(findings)} findings: "
          + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    for note in notes:
        print(f"  {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
