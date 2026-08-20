"""matrix.py — read_sheet(), the spreadsheet reader everything downstream trusts.

The client's matrix is the only ID space that matters: it is what the vendor
writes against and what the status column tracks. read_sheet is where that
spreadsheet becomes Python, and its one surprising behaviour — it drops blank
rows — is the root of a live row-alignment defect in writeback.py, which writes
evidence into cells a client reads.

These tests pin the behaviour AS IT IS. Dropping blank rows is defensible on its
own; the returned rows carry no row identity, which is what makes it dangerous.
Both facts are asserted here so that a fix to either one fails loudly.
"""

import os
import tempfile
import unittest

import matrix
from tests.support import xlsx_bytes


class ReadSheet(unittest.TestCase):

    def setUp(self):
        # A temp dir per test: read_sheet takes a path, not bytes, and nothing
        # in this suite may write inside the repository.
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def write(self, rows, sheet_name="Comments", shared=None):
        path = os.path.join(self.dir.name, "matrix.xlsx")
        with open(path, "wb") as handle:
            handle.write(xlsx_bytes(rows, sheet_name, shared))
        return path

    def test_cells_come_back_keyed_by_column_letter(self):
        path = self.write([(1, {"A": "ID", "D": "Comment"}),
                           (2, {"A": "C-001", "D": "Section 9 is not assessed"})])
        rows = matrix.read_sheet(path, "Comments")
        self.assertEqual({k: v for k, v in rows[0].items() if k != matrix.ROW_KEY},
                         {"A": "ID", "D": "Comment"})
        self.assertEqual(rows[1]["A"], "C-001")

    def test_multi_letter_columns_survive(self):
        # The digits are stripped from the cell reference with a regex, so "AA7"
        # has to become "AA" and not "A". Matrices reach column AA routinely —
        # the adjudication column defaults to I and the client inputs column
        # sits further right.
        path = self.write([(7, {"AA": "far right"})])
        row = matrix.read_sheet(path, "Comments")[0]
        self.assertEqual({k: v for k, v in row.items() if k != matrix.ROW_KEY},
                         {"AA": "far right"})

    def test_shared_strings_are_resolved(self):
        path = self.write([(1, {"A": 0}), (2, {"A": 1})],
                          shared=["ID", "C-001"])
        rows = matrix.read_sheet(path, "Comments")
        self.assertEqual([r["A"] for r in rows], ["ID", "C-001"])

    def test_an_unknown_sheet_exits_and_names_the_alternatives(self):
        # "no sheet 'Comments'" with no list of what IS there sends the operator
        # to open the workbook by hand. The sheet name is a command-line
        # argument and is misspelled constantly.
        path = self.write([(1, {"A": "ID"})], sheet_name="Feedback")
        with self.assertRaises(SystemExit) as caught:
            matrix.read_sheet(path, "Comments")
        self.assertIn("Feedback", str(caught.exception))

    def test_a_workbook_with_no_shared_string_table_still_reads(self):
        # xl/sharedStrings.xml is absent from any workbook written entirely with
        # inline strings, including the annotated copy this toolkit emits.
        path = self.write([(1, {"A": "ID"})])
        self.assertEqual(matrix.read_sheet(path, "Comments")[0]["A"], "ID")


class BlankRows(unittest.TestCase):
    """The dropping behaviour, and the alignment hazard it creates.

    Characterisation, not endorsement. Reported, not fixed: writeback.py's own
    comment says "Excel row numbers, not list positions — read_sheet drops blank
    rows, so the two diverge and writing to the wrong row is silent and
    catastrophic", and the line immediately below it derives the row number from
    the list position anyway. read_sheet is where the information is lost, so it
    is where the loss is pinned.
    """

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        path = os.path.join(self.dir.name, "gapped.xlsx")
        with open(path, "wb") as handle:
            # A spacer row between two comment rows: a formatted-but-empty row,
            # which is what a human editing a matrix leaves behind.
            handle.write(xlsx_bytes([
                (1, {"A": "ID", "D": "Comment"}),
                (2, {"A": "C-001", "D": "first"}),
                (3, {"A": "", "D": "   "}),
                (4, {"A": "C-002", "D": "second"}),
            ]))
        self.path = path

    def test_a_row_of_empty_cells_is_dropped(self):
        rows = matrix.read_sheet(self.path, "Comments")
        self.assertEqual(len(rows), 3)
        self.assertEqual([r.get("A") for r in rows], ["ID", "C-001", "C-002"])

    def test_whitespace_only_cells_count_as_empty(self):
        # The dropped row is not empty in the XML — it has two cells, one of
        # them three spaces. `any(v.strip() ...)` is what makes it vanish.
        rows = matrix.read_sheet(self.path, "Comments")
        self.assertNotIn("   ", [r.get("D") for r in rows])

    def test_every_row_carries_its_physical_sheet_row(self):
        # This assertion used to say the opposite, and called itself load-bearing
        # for it: the row number was gone and a caller could not recover it. That
        # was a defect characterised rather than fixed, and it stayed a defect
        # until an adapter-contract review traced what it does to a client
        # workbook. It is now carried under a dunder-flanked key that cannot
        # collide with a column letter.
        for row in matrix.read_sheet(self.path, "Comments"):
            self.assertIn(matrix.ROW_KEY, row)
            self.assertIsInstance(row[matrix.ROW_KEY], int)
            self.assertTrue(all(isinstance(v, str)
                                for k, v in row.items() if k != matrix.ROW_KEY))

    def test_list_position_still_diverges_but_the_row_key_does_not(self):
        # Both halves matter. Position-derived numbering is STILL wrong after a
        # blank — that arithmetic has not become safe, and any caller doing it is
        # still broken. What changed is that the correct answer is now available,
        # so writeback no longer has to derive it.
        rows = matrix.read_sheet(self.path, "Comments")
        derived = {row["A"]: number
                   for number, row in enumerate(rows[1:], start=2)}
        self.assertEqual(derived["C-002"], 3)          # wrong: C-002 is row 4
        carried = {row["A"]: row[matrix.ROW_KEY] for row in rows[1:]}
        self.assertEqual(carried["C-001"], 2)
        self.assertEqual(carried["C-002"], 4)          # right, and not derived

    def test_a_row_absent_from_the_xml_shifts_everything_the_same_way(self):
        # Excel omits never-touched rows entirely rather than writing an empty
        # <row>, so the same divergence appears with no blank row present at
        # all. Two routes to one defect; a fix has to close both.
        path = os.path.join(self.dir.name, "sparse.xlsx")
        with open(path, "wb") as handle:
            handle.write(xlsx_bytes([(1, {"A": "ID"}),
                                     (2, {"A": "C-001"}),
                                     (9, {"A": "C-002"})]))
        rows = matrix.read_sheet(path, "Comments")
        self.assertEqual(len(rows), 3)
        derived = {row["A"]: number
                   for number, row in enumerate(rows[1:], start=2)}
        self.assertEqual(derived["C-002"], 3)          # wrong: C-002 is row 9

    def test_the_header_is_whatever_row_survives_first(self):
        # matrix.py takes rows[0] as the header. If the sheet opens with a title
        # row above the header — common in a jointly-agreed workbook — the title
        # becomes the header and the real header becomes a data row, which then
        # reaches the model as a comment to extract terms from.
        path = os.path.join(self.dir.name, "titled.xlsx")
        with open(path, "wb") as handle:
            handle.write(xlsx_bytes([(1, {"A": "Feedback matrix, rev 3"}),
                                     (2, {"A": "ID", "D": "Comment"}),
                                     (3, {"A": "C-001", "D": "first"})]))
        rows = matrix.read_sheet(path, "Comments")
        self.assertEqual(rows[0]["A"], "Feedback matrix, rev 3")
        self.assertEqual(rows[1]["A"], "ID")


if __name__ == "__main__":
    unittest.main()
