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


class DelimitedFiles(unittest.TestCase):
    """A comment register that arrives as CSV rather than .xlsx.

    Every case here is a way real exports differ from the tidy file you write when
    testing by hand. Before this existed the toolkit met all of them with
    `BadZipFile: File is not a zip file`."""

    def write(self, text, suffix=".csv"):
        path = os.path.join(tempfile.mkdtemp(), "m" + suffix)
        with open(path, "w", newline="", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_columns_are_addressed_by_letter_like_a_sheet(self):
        """The whole point: 13 callers index rows by letter and must not care
        which format the register arrived in."""
        rows = matrix.read_sheet(self.write("id,ref,comment\nC-1,6.3,precedence\n"), "any")
        self.assertEqual(rows[1]["A"], "C-1")
        self.assertEqual(rows[1]["C"], "precedence")

    def test_excel_byte_order_mark_is_stripped(self):
        """Excel writes a BOM on CSV export. Read as plain utf-8 the first header
        cell is '\ufeffid', so every lookup by header name misses and the failure
        looks like a missing column rather than an encoding."""
        rows = matrix.read_sheet(self.write("\ufeffid,ref\nC-1,6.3\n"), "any")
        self.assertEqual(rows[0]["A"], "id")

    def test_semicolon_export_is_not_read_as_one_column(self):
        """The European Excel default. Assumed comma, the entire row lands in A."""
        rows = matrix.read_sheet(self.write("id;ref;comment\nC-1;6.3;precedence\n"), "any")
        self.assertEqual(rows[1]["B"], "6.3")

    def test_a_newline_inside_a_quoted_comment_does_not_shift_the_row(self):
        """Comment text routinely contains line breaks. Counting lines rather than
        records numbers every row after one of them wrongly, and the writeback
        then files answers against the wrong comment."""
        rows = matrix.read_sheet(self.write(
            'id,comment\nC-1,"two\nlines"\nC-2,plain\n'), "any")
        self.assertEqual(rows[1]["comment" and "B"], "two\nlines")
        self.assertEqual((rows[2]["A"], rows[2][matrix.ROW_KEY]), ("C-2", 3))

    def test_blank_rows_are_dropped_reading_and_kept_writing(self):
        """The round trip, which is where an off-by-one becomes silent corruption.
        The reader skips the gap so callers never see an empty dict; the writer
        must keep it so record N in is record N out."""
        import writeback
        src = self.write("id,ref\nC-1,6.3\n,\nC-2,7.1\n")
        rows = matrix.read_sheet(src, "any")
        self.assertEqual([r["A"] for r in rows], ["id", "C-1", "C-2"])
        self.assertEqual(rows[2][matrix.ROW_KEY], 4)          # not 3

        dst = os.path.join(os.path.dirname(src), "out.csv")
        writeback.annotate(src, dst, "any", {"4": "verdict for C-2"}, "C", True)
        back = matrix.read_sheet(dst, "any")
        landed = [r for r in back if r["A"] == "C-2"][0]
        self.assertEqual(landed["C"], "verdict for C-2")
        self.assertEqual(len(open(dst, encoding="utf-8").read().splitlines()), 4)

    def test_writing_past_the_last_column_widens_the_row(self):
        import writeback
        src = self.write("id\nC-1\n")
        dst = os.path.join(os.path.dirname(src), "out.csv")
        writeback.annotate(src, dst, "any", {"2": "late"}, "D", True)
        self.assertEqual(matrix.read_sheet(dst, "any")[1]["D"], "late")

    def test_a_populated_cell_survives_unless_overwrite_is_asked_for(self):
        import writeback
        src = self.write("id,verdict\nC-1,already there\n")
        dst = os.path.join(os.path.dirname(src), "out.csv")
        written, skipped = writeback.annotate(src, dst, "any",
                                              {"2": "new"}, "B", False)
        self.assertEqual((written, skipped), (0, 1))
        self.assertEqual(matrix.read_sheet(dst, "any")[1]["B"], "already there")

    def test_tab_separated_is_read_too(self):
        rows = matrix.read_sheet(self.write("id\tref\nC-1\t6.3\n", ".tsv"), "any")
        self.assertEqual(rows[1]["B"], "6.3")


class ColumnsByName(unittest.TestCase):
    """--id-col takes "id" as well as "A", so nobody counts columns by hand.

    Invariant 4 wants records keyed by name rather than letter. That means
    changing what read_sheet returns and every one of the fifty-six places that
    index a record — in the code path that writes evidence into a client's matrix.
    This is the half that lands safely first: letters stay internal, names reach
    the operator."""

    def rows(self, text="id,section_ref,comment\nC-1,6.3,precedence\n"):
        path = os.path.join(tempfile.mkdtemp(), "m.csv")
        with open(path, "w", newline="") as handle:
            handle.write(text)
        return matrix.read_sheet(path, "any")

    def test_a_header_name_resolves_to_its_letter(self):
        self.assertEqual(matrix.resolve_column(self.rows(), "comment"), "C")

    def test_matching_ignores_case_and_surrounding_space(self):
        self.assertEqual(matrix.resolve_column(self.rows(), "  SECTION_REF "), "B")

    def test_a_letter_still_works(self):
        self.assertEqual(matrix.resolve_column(self.rows(), "b"), "B")

    def test_a_name_beats_a_letter_when_a_spec_could_be_either(self):
        """"ID" is a valid column letter — it is column 238. Someone typing it
        means the column headed ID, so names are matched first and a letter is
        the fallback for a spec that names no header."""
        rows = self.rows("ID,other\nC-1,x\n")
        self.assertEqual(matrix.resolve_column(rows, "ID"), "A")

    def test_a_letter_naming_no_header_passes_through(self):
        self.assertEqual(matrix.resolve_column(self.rows(), "ZZ"), "ZZ")

    def test_an_unknown_name_lists_the_headers(self):
        """The near miss is the common case: "Comments" for a column headed
        "comment". Failing without saying what IS there wastes the operator's
        next five minutes."""
        with self.assertRaises(SystemExit) as caught:
            matrix.resolve_column(self.rows(), "Comments")
        message = str(caught.exception)
        self.assertIn("section_ref", message)
        self.assertIn("comment", message)

    def test_a_duplicated_header_refuses_rather_than_picking_one(self):
        rows = self.rows("id,comment,comment\nC-1,a,b\n")
        with self.assertRaises(SystemExit) as caught:
            matrix.resolve_column(rows, "comment")
        self.assertIn("B, C", str(caught.exception))

    def test_resolve_columns_rewrites_every_col_argument(self):
        import argparse
        args = argparse.Namespace(id_col="id", comment_col="C", sheet="Comments",
                                  out="somewhere.md")
        matrix.resolve_columns(self.rows(), args)
        self.assertEqual((args.id_col, args.comment_col), ("A", "C"))
        self.assertEqual(args.sheet, "Comments")     # untouched: not a *_col
        self.assertEqual(args.out, "somewhere.md")

    def test_the_bookkeeping_key_is_never_a_candidate(self):
        with self.assertRaises(SystemExit):
            matrix.resolve_column(self.rows(), matrix.ROW_KEY)


if __name__ == "__main__":
    unittest.main()
