"""Shared machinery for the test suite. Stdlib only, like everything else here.

Two jobs, both of which exist because of how the code under test is shaped
rather than because a test suite normally needs them:

  lift()        reaches inside a function to test a nested one. Two of the most
                consequential predicates in this toolkit — undefined.py's
                `same` and sweep.py's `headings_of` — are closures defined
                inside main() and inside an `if args.compare:` branch. Both
                decide what a scorer counts as a hit. Copying them into the test
                would be the one thing a test must never do, because the copy
                stops tracking the original the moment someone edits it.

  docx/xlsx     build the smallest OOXML file the parser under test will accept.
                extract.py and matrix.py were written because no OOXML library
                was installable on the review machine; testing them against a
                library-generated file would test the library.
"""

import io
import types
import zipfile

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def lift(module, outer, inner):
    """Rebuild a nested function from its code object, bound to its module.

    Only works for a nested function with no free variables — one that reads
    module globals and its own arguments and nothing from the enclosing frame.
    That is checked rather than assumed: a closure that later starts capturing a
    variable would otherwise be rebuilt with that name unbound and fail as a
    NameError deep inside an assertion, which reads as a broken test rather than
    as "this function can no longer be lifted".
    """
    code = _find_code(getattr(module, outer).__code__, inner)
    if code is None:
        raise AssertionError(
            f"{module.__name__}.{outer} no longer defines a nested {inner!r}. "
            f"If it was renamed or promoted to module level, point the test at "
            f"the new name — do not copy the body in here.")
    if code.co_freevars:
        raise AssertionError(
            f"{module.__name__}.{outer}.{inner} now closes over "
            f"{code.co_freevars}; it cannot be lifted out of its frame. Promote "
            f"it to module level or pass those values as arguments.")
    return types.FunctionType(code, module.__dict__, inner)


def _find_code(code, name):
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            if const.co_name == name:
                return const
            found = _find_code(const, name)
            if found is not None:
                return found
    return None


def docx_bytes(body_xml):
    """A .docx carrying exactly the <w:body> content given.

    Only word/document.xml is written. extract.py reads nothing else, and a
    fixture that carries parts the parser never opens invites the reader to
    believe those parts are under test.
    """
    document = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<w:document xmlns:w="{W}"><w:body>{body_xml}</w:body></w:document>')
    return _zip({"word/document.xml": document})


def para(*runs):
    """One <w:p>, one <w:r> per fragment. Word splits a sentence across runs at
    every formatting change and at every spell-check boundary, so a paragraph
    arriving as one run is the unusual case, not the normal one."""
    inner = "".join(
        f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>' for text in runs)
    return f"<w:p>{inner}</w:p>"


def xlsx_bytes(rows, sheet_name="Comments", shared=None):
    """A .xlsx with one sheet. `rows` is [(excel_row_number, {col: value})].

    The row number is explicit rather than derived from list position because
    the behaviour under test IS what happens when sheet row numbers and list
    positions disagree — a fixture that could not express a gap at row 4 could
    not reach the case at all.

    A str value is written as an inline string; an int is written as a reference
    into `shared`. Both paths exist in real workbooks — Excel itself writes
    shared strings, and the annotated copy writeback.py produces writes inline
    ones — so a reader that handles only one of them fails on half the inputs.
    """
    shared = shared or []
    cells_xml = []
    for number, columns in rows:
        cells = "".join(
            (f'<c r="{col}{number}" t="s"><v>{value}</v></c>'
             if isinstance(value, int) else
             f'<c r="{col}{number}" t="inlineStr"><is>'
             f'<t xml:space="preserve">{value}</t></is></c>')
            for col, value in sorted(columns.items()))
        cells_xml.append(f'<row r="{number}">{cells}</row>')
    sheet = (f'<?xml version="1.0" encoding="UTF-8"?>'
             f'<worksheet xmlns="{S}"><sheetData>'
             + "".join(cells_xml) + "</sheetData></worksheet>")
    workbook = (f'<?xml version="1.0" encoding="UTF-8"?>'
                f'<workbook xmlns="{S}" xmlns:r="{R}"><sheets>'
                f'<sheet name="{sheet_name}" sheetId="1" r:id="rId1"/>'
                f"</sheets></workbook>")
    rels = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/'
            'package/2006/relationships">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
            "</Relationships>")
    parts = {
        "xl/workbook.xml": workbook,
        "xl/_rels/workbook.xml.rels": rels,
        "xl/worksheets/sheet1.xml": sheet,
    }
    if shared:
        items = "".join(f"<si><t>{t}</t></si>" for t in shared)
        parts["xl/sharedStrings.xml"] = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<sst xmlns="{S}" count="{len(shared)}">{items}</sst>')
    return _zip(parts)


def _zip(parts):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in parts.items():
            archive.writestr(name, text)
    return buffer.getvalue()
