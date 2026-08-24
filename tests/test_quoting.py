"""locate.quoted — reproducing a cited passage so a reviewer can judge it.

Both report renderers collapsed every quote with `" ".join(text.split())`. For
prose that is correct: a passage arrives wrapped at the source's width and
reflowing is what makes it readable. For a table it destroys the evidence while
appearing to include it — every character present, nothing checkable, because a
table's meaning lives in the alignment of cell to column.

That failure is invisible to the obvious test. A check for "does the quote appear
in the report" passes on the flattened form if it normalises whitespace, and fails
on the CORRECT form if it does not. The first version of this check reported five
missing quotes that were all present, then reported zero problems once fixed —
while the real defect sat in the output the whole time. So these assert the SHAPE
of what is emitted, not merely its presence.
"""

import unittest

import locate

TABLE = ("| ID | Component | Responsibility |\n"
         "|---|---|---|\n"
         "| RO | Runtime Orchestrator | Execution scheduling |\n"
         "| SF | Sensor Fabric | Telemetry ingest |")


class Quoted(unittest.TestCase):

    def test_a_table_keeps_one_row_per_line(self):
        text, is_block = locate.quoted(TABLE)
        self.assertTrue(is_block)
        self.assertEqual(len(text.splitlines()), 4)
        self.assertTrue(all(ln.startswith("|") for ln in text.splitlines()))

    def test_prose_is_reflowed(self):
        text, is_block = locate.quoted("some prose\nwrapped across\nthree lines")
        self.assertFalse(is_block)
        self.assertEqual(text, "some prose wrapped across three lines")
        self.assertNotIn("\n", text)

    def test_prose_containing_a_pipe_is_not_a_table(self):
        # One pipe-bearing line is prose. Treating it as a table would put a
        # sentence on its own lines and claim structure that is not there.
        text, is_block = locate.quoted("throughput | latency is measured hourly")
        self.assertFalse(is_block)

    def test_a_single_table_row_is_not_a_table(self):
        # Nothing to align against, so nothing is lost by reflowing.
        _, is_block = locate.quoted("| ID | Component |")
        self.assertFalse(is_block)

    def test_a_long_table_is_truncated_by_rows_not_characters(self):
        rows = "\n".join(f"| R{n} | value {n} |" for n in range(40))
        text, is_block = locate.quoted(rows, limit=5)
        self.assertTrue(is_block)
        lines = text.splitlines()
        self.assertEqual(len(lines), 6)                  # 5 rows + the count
        self.assertIn("further row", lines[-1])
        self.assertTrue(lines[-1].startswith("|"))       # still a table row
        for line in lines[:5]:                           # never mid-cell
            self.assertTrue(line.endswith("|"))

    def test_blank_is_empty_and_not_a_block(self):
        self.assertEqual(locate.quoted("   \n  "), ("", False))
        self.assertEqual(locate.quoted(None), ("", False))

    def test_flattening_a_table_is_what_this_exists_to_prevent(self):
        # The regression stated as the defect: if quoted() ever reverts to
        # " ".join(split()), the table arrives as a wall of pipes on one line.
        text, _ = locate.quoted(TABLE)
        self.assertNotEqual(text, " ".join(TABLE.split()))
        self.assertNotIn("| |", text)


class RenderersAreWired(unittest.TestCase):
    """A correct helper proves nothing about the two places that must call it.

    Both renderers had the same flattening, and this repo's recurring defect is a
    guard that lives in one file and not its sibling. So assert the OUTPUT of the
    tool, not the helper it is supposed to use.
    """

    def render(self, evidence):
        """bundle.markdown IN-PROCESS. Not a subprocess, deliberately.

        run-tests.py --mutate applies a mutation by re-executing source into the
        already-imported module and never touches disk, so a subprocess sees the
        UNMUTATED file. A shelled-out test named in a mutation therefore reads as
        NOT CAUGHT however good it is — which is what happened here, and is why
        markdown() was lifted out of main().
        """
        import bundle
        finding = {"sort": (0, ""), "id": "O-007", "class": "RFO coverage",
                   "status": "met", "statement": "The design shall name owners.",
                   "locator": "deliverable-v2:120", "evidence": evidence,
                   "why": "the component table names an owner per component"}
        return bundle.markdown([finding], {"RFO coverage: met": 1},
                               "Review bundle", "deliverable-v2")

    def test_markdown_emits_a_table_quote_as_a_table(self):
        body = self.render(TABLE)
        rows = [ln for ln in body.splitlines()
                if ln.startswith("|") and ln.count("|") >= 3]
        self.assertGreaterEqual(len(rows), 3, "the table did not survive")
        for line in body.splitlines():
            if line.startswith("**Evidence.**"):
                self.assertNotIn("|---", line, f"table flattened: {line[:90]}")
                self.assertNotIn("| |", line, f"rows run together: {line[:90]}")

    def test_markdown_still_reflows_prose_evidence(self):
        body = self.render("prose evidence\nwrapped over\nthree lines")
        self.assertIn("**Evidence.** prose evidence wrapped over three lines",
                      body)

    def test_bundle_cli_emits_a_table_quote_as_a_table(self):
        # The CLI path, end to end. NOT named in any mutation — see render().
        import os
        import subprocess
        import sys
        import tempfile
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        project = os.path.join(here, "fixtures", "floodtwin")
        if not os.path.exists(os.path.join(project, "cov-embed-12.csv")):
            self.skipTest("floodtwin coverage fixture not present")
        with tempfile.TemporaryDirectory() as out:
            md = os.path.join(out, "b.md")
            done = subprocess.run(
                [sys.executable, os.path.join(here, "bundle.py"),
                 "--project", project, "--doc", "deliverable-v2",
                 "--coverage", "cov-embed-12.csv",
                 "--out-xlsx", os.path.join(out, "b.xlsx"), "--out-md", md],
                capture_output=True, text=True, cwd=here)
            self.assertEqual(done.returncode, 0, done.stderr[-400:])
            body = open(md, encoding="utf-8").read()

        rows = [ln for ln in body.splitlines()
                if ln.startswith("|") and ln.count("|") >= 3]
        self.assertGreaterEqual(len(rows), 4, "no table survived into the report")
        # The specific corruption, stated precisely. Counting pipes is too
        # blunt: a lone table row is a legitimate inline quote — it has no second
        # row to align against, which is what test_a_single_table_row asserts.
        # What cannot survive a reflow is a table of two or more rows, and its
        # signature is unmistakable: the |---| separator, or the | | seam where
        # one row's end was pushed against the next row's start.
        for line in body.splitlines():
            if line.startswith("**Evidence.**"):
                self.assertNotIn("|---", line, f"table flattened: {line[:90]}")
                self.assertNotIn("| |", line, f"rows run together: {line[:90]}")
