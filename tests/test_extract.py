"""extract.py — OOXML to plain text, on a .docx this test builds itself.

Every locator this toolkit prints is a line number in extract.py's output, and
every hash it pins is the hash of that output. A change here moves every
citation in every register at once, which is the failure freeze.py exists to
make impossible — so the shape of the text is worth pinning even where it looks
arbitrary.

The fixture is assembled with zipfile and string XML rather than with a
document library. extract.py exists BECAUSE no such library was installable on
the review machine; a fixture written with one would test the library, and would
also quietly stop testing the case that matters most, which is a file some other
tool wrote in a way we did not choose.
"""

import io
import os
import tempfile
import unittest
import zipfile

import extract
from tests.support import docx_bytes, para


class Docx(unittest.TestCase):

    def build(self, body_xml):
        """Write a real .docx to a temp dir and open it the way the tool does."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = os.path.join(directory.name, "deliverable.docx")
        with open(path, "wb") as handle:
            handle.write(docx_bytes(body_xml))
        return extract.docx(zipfile.ZipFile(path))

    def test_paragraphs_come_out_one_per_line_in_document_order(self):
        text = self.build(para("The twin models the drainage network.")
                          + para("Six components are described."))
        self.assertEqual(text.splitlines(),
                         ["The twin models the drainage network.",
                          "Six components are described."])

    def test_runs_inside_a_paragraph_are_joined_with_no_separator(self):
        # Word splits a sentence into runs at every formatting change, mid-word
        # included. Joining runs with a space would insert one into the middle
        # of "Calibration" and break every anchor that quotes it.
        text = self.build(para("Cali", "bration ", "Pipeline"))
        self.assertEqual(text, "Calibration Pipeline")

    def test_leading_and_trailing_spaces_inside_a_run_survive(self):
        # xml:space="preserve" is how Word writes a run that ends in a space.
        # Losing it welds the last word of one run to the first of the next.
        text = self.build(para("Section 9 ", "is deployment topology"))
        self.assertEqual(text, "Section 9 is deployment topology")

    def test_empty_and_whitespace_only_paragraphs_are_dropped(self):
        # A .docx is full of empty paragraphs used as spacing. Emitting them
        # would put hundreds of blank lines between the passages a locator
        # points at, for no gain to anyone reading the output.
        text = self.build(para("Alpha") + "<w:p/>" + para("   ") + para("Beta"))
        self.assertEqual(text.splitlines(), ["Alpha", "Beta"])

    def test_table_cells_come_out_as_sequential_paragraphs(self):
        # Cell structure is lost and content is preserved — the documented
        # trade. The component table is where the identifiers live, so losing
        # the CONTENT would take the whole ID register with it.
        table = ("<w:tbl><w:tr>"
                 "<w:tc>" + para("RO") + "</w:tc>"
                 "<w:tc>" + para("Runtime Orchestrator") + "</w:tc>"
                 "</w:tr><w:tr>"
                 "<w:tc>" + para("SF") + "</w:tc>"
                 "<w:tc>" + para("Sensor Fabric") + "</w:tc>"
                 "</w:tr></w:tbl>")
        self.assertEqual(self.build(table).splitlines(),
                         ["RO", "Runtime Orchestrator", "SF", "Sensor Fabric"])

    def test_a_zip_that_is_not_a_word_document_yields_empty_text(self):
        # Not an exception. A .docx that is really a renamed .pptx turns up in
        # every document set, and the caller prints whatever comes back.
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("ppt/presentation.xml", "<p/>")
        self.assertEqual(extract.docx(zipfile.ZipFile(buffer)), "")

    def test_bug_a_paragraph_nested_in_a_text_box_is_emitted_twice(self):
        # BUG (reported, not fixed): the walk visits every element whose local
        # name is "p" and, for each, concatenates every <w:t> BELOW it. A text
        # box carries its own paragraph inside a run of the outer one, so the
        # boxed text is emitted once as part of the outer paragraph and once
        # again on its own line.
        #
        # It is not cosmetic. Duplicated text inflates the line counts that
        # closure.py compares and that --too-broad thresholds on, and it shifts
        # every locator after it. Vendor deliverables use text boxes for callout
        # notes, which is exactly the emphasised prose a reviewer quotes.
        body = ("<w:p><w:r><w:t>Callout: </w:t>"
                "<w:pict><w:txbxContent>"
                + para("selections remain open") +
                "</w:txbxContent></w:pict></w:r></w:p>")
        self.assertEqual(self.build(body).splitlines(),
                         ["Callout: selections remain open",
                          "selections remain open"])


if __name__ == "__main__":
    unittest.main()
