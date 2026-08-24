"""bundle.write_xlsx — the styles part, and why the evidence column needed one.

The workbook had no styles.xml. Without one there is no cellXfs entry to carry
alignment, so wrapText was not merely unset but unsettable, and every cell took
the default format: a newline STORED and never shown as a break, text clipped at
the column boundary as soon as the neighbour is non-empty. Six evidence cells in
the floodtwin fixture hold 194-519 characters over several lines with column G
populated in all six.

WHAT ENFORCES THIS. Nothing on this machine validates OOXML. LibreOffice opens
the workbook — and also opens one with cellXfs moved in front of fonts, so a
successful convert says the file is readable and says nothing about the schema.
Excel is stricter and was not available to test. These assertions are therefore
the only enforcement, which is the reason they check the sequence explicitly
rather than trusting a parser to complain.
"""

import re
import unittest
import xml.dom.minidom
import zipfile

import bundle


class StylesPart(unittest.TestCase):

    def setUp(self):
        import os
        import tempfile
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "w.xlsx")
        bundle.write_xlsx(self.path, "Sheet1", bundle.HEADERS,
                          [["O-1", "RFO coverage", "met", "a requirement",
                            "doc:12", "| ID | X |\n|---|---|\n| A | b |",
                            "why", ""]])
        self.zip = zipfile.ZipFile(self.path)

    def tearDown(self):
        import shutil
        self.zip.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_the_styles_part_is_present_and_declared_in_all_three_places(self):
        # A part that is written but not declared is invisible; a part that is
        # declared but not written makes the workbook unopenable. Three places
        # have to agree, so assert all three rather than the file listing alone.
        self.assertIn("xl/styles.xml", self.zip.namelist())
        self.assertIn(b"spreadsheetml.styles+xml",
                      self.zip.read("[Content_Types].xml"))
        self.assertIn(b"styles.xml", self.zip.read("xl/_rels/workbook.xml.rels"))

    def test_styles_follow_the_schema_sequence(self):
        body = self.zip.read("xl/styles.xml").decode()
        order = [m for m in re.findall(
            r"<(fonts|fills|borders|cellStyleXfs|cellXfs)[ >]", body)]
        self.assertEqual(order,
                         ["fonts", "fills", "borders", "cellStyleXfs", "cellXfs"])

    def test_every_part_is_well_formed_xml(self):
        for name in self.zip.namelist():
            xml.dom.minidom.parseString(self.zip.read(name))

    def xfs(self):
        body = self.zip.read("xl/styles.xml").decode()
        block = re.search(r"<cellXfs.*?</cellXfs>", body, re.S).group(0)
        return re.findall(r"<xf\b.*?(?:/>|</xf>)", block, re.S)

    def style_of(self, row_number):
        """The style index the OUTPUT actually uses for a row.

        Read from the sheet, never from bundle.BODY_STYLE. The first version of
        this test built its expectation out of that constant, so setting both
        constants to 0 left the test agreeing with the broken output — it could
        not fail, which the mutation run reported as NOT CAUGHT. A test that
        sources its expected value from the thing under test is not a test.
        """
        sheet = self.zip.read("xl/worksheets/sheet1.xml").decode()
        row = dict(re.findall(r'<row r="(\d+)">(.*?)</row>', sheet, re.S))[row_number]
        used = set(re.findall(r's="(\d+)"', row))
        self.assertEqual(len(used), 1, f"row {row_number} mixes styles: {used}")
        self.assertEqual(len(re.findall(r'<c ', row)), len(bundle.HEADERS))
        return int(used.pop())

    def test_body_cells_reference_a_style_that_wraps(self):
        # The whole point: a style index that does not wrap is the original bug
        # with extra steps. Resolve the index from the sheet, then look up what
        # that index actually means.
        entries = self.xfs()
        index = self.style_of("2")
        self.assertLess(index, len(entries))
        self.assertIn('wrapText="1"', entries[index])

    def test_header_cells_reference_a_style_that_wraps_and_is_bold(self):
        entries = self.xfs()
        index = self.style_of("1")
        self.assertLess(index, len(entries))
        self.assertIn('wrapText="1"', entries[index])
        self.assertIn('applyFont="1"', entries[index])

    def test_a_multi_line_cell_keeps_its_newlines(self):
        sheet = self.zip.read("xl/worksheets/sheet1.xml").decode()
        cells = re.findall(r'<t xml:space="preserve">(.*?)</t>', sheet, re.S)
        self.assertTrue(any("\n" in c for c in cells),
                        "the table quote lost its line breaks")

    def test_the_workbook_reads_back_through_dossiers_own_reader(self):
        import matrix
        rows = matrix.read_sheet(self.path, "Sheet1")
        self.assertEqual(rows[0]["F"], "Evidence")
        self.assertIn("\n", rows[1]["Evidence"])       # by name, and intact
