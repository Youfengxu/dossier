#!/usr/bin/env python3
"""Build `comments.xlsx` and `comments.docx` from `comments.csv`.

Cross-format agreement is the cheapest real check the contract asks for: the same
table, expressed two ways, must produce identical records. It is worth having as
a committed artefact rather than something generated inside the test, because a
generated twin can only ever agree with the generator. This one is a file on disk
that can drift, and `conformance.py` notices when it does.

Half the cells are written as shared strings and half inline, because Excel writes
shared and `writeback.py` writes inline, and a reader that handles one of them
fails on half the workbooks it will meet.

    ./make-twins.py              # rewrites both twins from comments.csv
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

import matrix                                                   # noqa: E402
from tests.support import docx_bytes, xlsx_bytes                # noqa: E402


def escape(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def build_docx(rows):
    """The same table as a Word table — the shape a register arrives in when
    somebody pastes it into a document instead of attaching the spreadsheet."""
    body = ["<w:tbl>"]
    for record in rows:
        body.append("<w:tr>")
        for letter in sorted((k for k in record if k != matrix.ROW_KEY),
                             key=matrix.col_num):
            paragraphs = "".join(
                f"<w:p><w:r><w:t>{escape(line)}</w:t></w:r></w:p>"
                for line in (record[letter] or "").split("\n"))
            body.append(f"<w:tc>{paragraphs or '<w:p/>'}</w:tc>")
        body.append("</w:tr>")
    body.append("</w:tbl>")
    return docx_bytes("".join(body))


def build():
    """The twin's bytes, so a caller can compare without writing anything.

    conformance.py checks the committed file against this rather than against
    `git status`, which reports a staged-but-uncommitted file as changed and would
    make the check depend on where in a workflow it was run rather than on whether
    the fixture is correct.
    """
    rows = matrix.read_csv(os.path.join(HERE, "comments.csv"))
    shared, built = [], []
    for record in rows:
        cells = {}
        for letter, value in record.items():
            if letter == matrix.ROW_KEY:
                continue
            # Alternate the two encodings so both paths are exercised by the
            # same fixture rather than by two that can diverge.
            if (record[matrix.ROW_KEY] + matrix.col_num(letter)) % 2 == 0:
                cells[letter] = value
            else:
                shared.append(value)
                cells[letter] = len(shared) - 1
        built.append((record[matrix.ROW_KEY], cells))

    return xlsx_bytes(built, "Comments", shared), len(built), len(shared)


def main():
    payload, records, strings = build()
    with open(os.path.join(HERE, "comments.xlsx"), "wb") as handle:
        handle.write(payload)
    print(f"  wrote comments.xlsx: {records} rows, {strings} shared strings")

    rows = matrix.read_csv(os.path.join(HERE, "comments.csv"))
    with open(os.path.join(HERE, "comments.docx"), "wb") as handle:
        handle.write(build_docx(rows))
    print(f"  wrote comments.docx: {len(rows)} rows as a Word table")
    return 0


if __name__ == "__main__":
    sys.exit(main())
