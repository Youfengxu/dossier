#!/usr/bin/env python3
"""Build `comments.xlsx` from `comments.csv` — the same fourteen rows, twice.

Cross-format agreement is the cheapest real check the contract asks for: the same
table, expressed two ways, must produce identical records. It is worth having as
a committed artefact rather than something generated inside the test, because a
generated twin can only ever agree with the generator. This one is a file on disk
that can drift, and `conformance.py` notices when it does.

Half the cells are written as shared strings and half inline, because Excel writes
shared and `writeback.py` writes inline, and a reader that handles one of them
fails on half the workbooks it will meet.

    ./make-xlsx-twin.py          # rewrites comments.xlsx from comments.csv
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

import matrix                                                   # noqa: E402
from tests.support import xlsx_bytes                            # noqa: E402


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
    out = os.path.join(HERE, "comments.xlsx")
    with open(out, "wb") as handle:
        handle.write(payload)
    print(f"  wrote {os.path.basename(out)}: {records} rows, "
          f"{strings} shared strings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
