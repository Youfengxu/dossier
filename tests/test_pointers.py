"""synthesize.py, class D3: where a pointer points, and what may be said when it
points nowhere.

"Calibration drift limits are recorded in Section 3.1." D3 checks a sentence
like that two ways: is there such a place, and does it hold what the sentence
says. For the first it asked the inventory, and the inventory cannot answer. It
keeps the FIRST heading of each chunk, and a chunk also holds whatever headings
arrived too soon to start one of their own (tests/test_chunking.py records it
for the fixture: 88 headings, 74 chunks, 15 headings that head none). So, with
every section read:

    "Section 3.1"          3.1 sits inside the chunk headed "3. ..."
                           -> "no such section in the document"
    "Section 3"            the heading is spelled "Section 3: ..."
                           -> "no such section in the document"
    "Section 3.2"          a stub, too short for the splitter to keep
                           -> "no such section in the document"
    "Table 4"              the number is a table's, not a section's
                           -> "no such section", or section 4 if there is one
    "Sections 6.4, 7.4"    the test for a place knew "section", not "sections"
                           -> skipped as pointing outside the document
    a long section         cut into chunks that share its heading: only the
                           last chunk was looked in

Which places exist is a question about the document, and the document is frozen
beside the inventory. D3 now reads its headings from there, checks a claim
against the chunks that cover the place's own lines, and says how many claims
it checked and why it did not check the rest.

WHAT IT STANDS BEHIND is the other half of this file. D3 asserts a finding only
in a document that marks its headings: a Markdown source, with "#" on them.
Text extracted from a .docx marks nothing, and two reviews of earlier versions
of this change each found a dozen ways in which a rule for telling a heading
from a cell of a table, a numbered step or a line of the contents asserted
something false. There D3 takes every line that opens with a number as the
start of something, to decide where to look; it clears a claim it finds, and
lists the rest as unverifiable.

A third review found as many on the Markdown side, so there too a finding is
stood behind only when the pointer is its places and nothing else, each place
is under a heading that is one of the document's own, and no other line could
be the place instead. The rest are listed as unverifiable, with the reason.

No model. Two documents: one in Markdown, and the same one as a .docx put
through the repository's own extract.py. Their chunks are the ones
inventory.split_sections makes, and main() is run in process, so run-tests.py
--mutate reaches what is tested.
"""

import contextlib
import functools
import types
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock
from xml.sax.saxutils import escape

import inventory
import llm
import synthesize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DOCUMENT = """\
# Flood twin design

Status: draft for review.
Owner: the twin team.

## 1. Sensor Fabric
The fabric ingests gauge readings.
It checks each reading against its range.
It forwards them to the Hydrology Model.

## 2. Hydrology Model
The model forecasts river stage.
It runs once per tick.
It publishes a projection.

## 3. Calibration Register
The registers kept for every gauge.

### 3.1 Drift limits
Drift limits are held per gauge.
They are reviewed each quarter.

## 4. Algorithms
The tick runs in this order.
Each step finishes before the next begins.
A step that fails stops the tick.

1. The fabric ingests readings, in tick order
   A late reading waits for the next tick.
2. The model forecasts
3. The service raises alerts

## Appendix A — Requirement mapping
R-010 is addressed in Section 1.
R-011 is addressed in Section 2.
""".splitlines()

# What each part of the document provides, by the line that says so. An
# inventory records it against whichever chunk the splitter put that line in.
HOLDS = {7: "ingest gauge readings", 12: "forecast river stage",
         20: "calibration drift limits", 24: "tick order",
         28: "ingest readings", 34: "requirement mapping"}
CLAIM = "Calibration drift limits are recorded"
SOURCE = "5" * 64               # the hash of a source nobody needs here

# The same design as its author would have it in Word: one paragraph each, the
# headings typed. A tuple is one paragraph with a tab between its parts, which
# is how "3.1<TAB>Drift limits" is stored.
WORD = (
    "Flood twin design",
    "Status: draft for review.",
    "Owner: the twin team.",
    "1. Sensor Fabric",
    "The fabric ingests gauge readings.",
    "It checks each reading against its range.",
    "It forwards them to the Hydrology Model.",
    "2. Hydrology Model",
    "The model forecasts river stage.",
    "It runs once per tick.",
    "It publishes a projection.",
    "3. Calibration Register",
    "The registers kept for every gauge.",
    ("3.1", "Drift limits"),
    "Drift limits are held per gauge.",
    "They are reviewed each quarter.",
    "4. Algorithms",
    "The tick runs in this order.",
    "Each step finishes before the next begins.",
    "A step that fails stops the tick.",
    "1. The fabric ingests readings, in tick order.",
    "A late reading waits for the next tick.",
    "2. The model forecasts.",
    "3. The service raises alerts.",
    "Appendix A \N{EM DASH} Requirement mapping",
    "R-010 is addressed in Section 1.",
    "R-011 is addressed in Section 2.",
)
# By line of the text extract.py makes of it, which opens with an empty line
# and the extractor's banner: paragraph n is line n + 2.
WORD_HOLDS = {7: "ingest gauge readings", 11: "forecast river stage",
              17: "calibration drift limits", 20: "tick order",
              28: "requirement mapping"}


@functools.lru_cache(maxsize=None)
def extracted(paragraphs):
    """The frozen text of a .docx holding these paragraphs: what extract.py
    prints for it, which is what freeze.py stores."""
    def runs(paragraph):
        parts = paragraph if isinstance(paragraph, tuple) else (paragraph,)
        return "<w:r><w:tab/></w:r>".join(
            f'<w:r><w:t xml:space="preserve">{escape(part)}</w:t></w:r>'
            for part in parts)
    body = "".join(f"<w:p>{runs(paragraph)}</w:p>" for paragraph in paragraphs)
    folder = tempfile.mkdtemp()
    try:
        path = os.path.join(folder, "design.docx")
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "word/document.xml",
                '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w='
                '"http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f"<w:body>{body}</w:body></w:document>")
        done = subprocess.run([sys.executable, os.path.join(ROOT, "extract.py"),
                               path], capture_output=True, check=True)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return done.stdout.decode("utf-8").splitlines()


def word(before=None, *added):
    """The Word document's text, with `added` paragraphs put in before the
    paragraph that reads `before`."""
    paragraphs = list(WORD)
    if before is not None:
        at = paragraphs.index(before)
        paragraphs[at:at] = added
    return extracted(tuple(paragraphs))


def sections_of(document, *claims, holds=HOLDS, older=False, **split):
    """The inventory's sections for `document`: the chunks inventory.py splits
    it into, each holding what `holds` gives for a line of it. The claims are
    made by the first chunk.

    `older` is the inventory as it was cut before 2026-10-04, when a chunk of
    sixty characters or fewer was dropped, heading and all. Inventories cut
    that way are still read, and what D3 says of the lines they left out is
    tested on them."""
    parts = inventory.split_sections(document, **split)
    if older:
        parts = [part for part in parts
                 if sum(len(line.strip())
                        for line in part["text"].split("\n")) > 60]
    sections = []
    for part in parts:
        sections.append({
            "heading": part["heading"],
            "locator": f"d:{part['start']}-{part['end']}",
            "capabilities": [{"name": name, "quote": "q"}
                             for line, name in sorted(holds.items())
                             if part["start"] <= line <= part["end"]],
            "authority": [], "defers_to": [], "produces": [], "consumes": [],
            "evidence_claims": [], "identifiers": [], "deferred": [],
            "passes": 3, "full_passes": 3})
    sections[0]["evidence_claims"] = [
        {"claim": text, "points_to": place, "quote": "q"}
        for text, place in claims]
    return sections


def where(sections):
    return [(section["heading"], section["locator"]) for section in sections]


class TheChunksAreTheSplittersOwn(unittest.TestCase):
    """Every test below rests on these. They are not written out by hand: a
    first version of this file did that, and the chunks it described were not
    the ones inventory.py makes."""

    def test_what_inventory_py_makes_of_the_document(self):
        """3.1 has no chunk: its heading came two lines after "3." and was
        taken as body text. A numbered step heads a chunk of its own. The five
        lines before section 1 are a chunk under the title: they were too
        short to be kept at all until 2026-10-04."""
        self.assertEqual(where(sections_of(DOCUMENT)), [
            ("Flood twin design", "d:1-5"),
            ("1. Sensor Fabric", "d:6-10"),
            ("2. Hydrology Model", "d:11-15"),
            ("3. Calibration Register", "d:16-22"),
            ("4. Algorithms", "d:23-27"),
            ("1. The fabric ingests readings, in tick order", "d:28-32"),
            ("Appendix A — Requirement mapping", "d:33-35")])

    def test_as_it_was_cut_before_a_short_chunk_was_kept(self):
        self.assertEqual(where(sections_of(DOCUMENT, older=True)), [
            ("1. Sensor Fabric", "d:6-10"),
            ("2. Hydrology Model", "d:11-15"),
            ("3. Calibration Register", "d:16-22"),
            ("4. Algorithms", "d:23-27"),
            ("1. The fabric ingests readings, in tick order", "d:28-32"),
            ("Appendix A — Requirement mapping", "d:33-35")])

    def test_and_of_the_same_document_as_a_docx(self):
        """extract.py writes a paragraph a line and no line between them, so a
        heading comes sooner after the last one. Section 2 heads no chunk and
        the tab in "3.1<TAB>Drift limits" is gone. The second numbered step
        heads a chunk, and the third and Appendix A, which follow it at once,
        are text of that chunk. Until 2026-10-04 each of those lines replaced
        the one before as the heading: two steps were in no chunk at all, and
        Appendix A headed one."""
        text = word()
        self.assertEqual(text[:3], ["", "########## design.docx ##########",
                                    "Flood twin design"])
        self.assertEqual(text[15], "3.1Drift limits")
        self.assertEqual(where(sections_of(text)), [
            ("(front matter)", "d:1-5"),
            ("1. Sensor Fabric", "d:6-13"),
            ("3. Calibration Register", "d:14-18"),
            ("4. Algorithms", "d:19-24"),
            ("2. The model forecasts.", "d:25-29")])


class Frozen(unittest.TestCase):
    """main() over an inventory and the frozen document it was built from."""

    def setUp(self):
        self.project = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.project, ignore_errors=True)

    def again(self):
        """An empty project, for a test that runs main() more than once."""
        self.tearDown()
        self.setUp()

    def freeze(self, lines, **pinned):
        """Write the document the way freeze.py leaves it; return the hash of
        its text. `pinned` replaces what the manifest says of it: the source
        is a Markdown file unless it says otherwise."""
        text = ("\n".join(lines) + "\n").encode("utf-8")
        os.makedirs(os.path.join(self.project, "parsed"), exist_ok=True)
        with open(os.path.join(self.project, "parsed", "d.txt"), "wb") as handle:
            handle.write(text)
        digest = hashlib.sha256(text).hexdigest()
        with open(os.path.join(self.project, "parsed", "MANIFEST.json"),
                  "w") as handle:
            json.dump({"documents": [{"slug": "d", "role": "draft",
                                      "path": "design.md",
                                      "parsed": "parsed/d.txt",
                                      "source_sha256": SOURCE,
                                      "text_sha256": digest, **pinned}]}, handle)
        return digest

    def run_on(self, sections, *extra, document=DOCUMENT, path="design.md",
               **recorded):
        """`document` is what to freeze beside the inventory, as the text of
        the source `path`, or None to freeze nothing here. `recorded` is
        anything else the inventory says about itself. Without it the
        inventory is one written before the hash of the text was kept: it has
        the hash of the source and no other."""
        if document is not None:
            self.freeze(document, path=path)
        with open(os.path.join(self.project, "inventory.json"), "w") as handle:
            json.dump({"doc": "d", "runs": 3, "sections": sections,
                       "source_sha256": SOURCE, **recorded}, handle)
        out, err, argv = io.StringIO(), io.StringIO(), sys.argv
        sys.argv = ["synthesize.py", "--project", self.project, "--no-embed",
                    *extra]
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.status = synthesize.main()
        except Exception as error:      # noqa: BLE001: a crash is a failure
            self.fail(f"main() raised {type(error).__name__}: {error}")
        finally:
            sys.argv = argv
        return out.getvalue()

    def d3(self, report):
        """The D3 findings as printed, one string each."""
        found, current = [], None
        for line in report.splitlines():
            if line.startswith("["):
                current = [] if line.startswith("[D3]") else None
                if current is not None:
                    found.append(current)
            if current is not None and line.strip():
                current.append(line.strip())
        return [" ".join(lines) for lines in found]

    def one(self, report):
        """The one D3 finding a report lists. None, or two, is a failure."""
        found = self.d3(report)
        self.assertEqual(len(found), 1, f"D3 findings: {found}")
        return found[0]

    def asserted(self, report):
        """The one D3 finding, which has to be one the tool stands behind."""
        finding = self.one(report)
        self.assertNotIn("unverifiable", finding)
        return finding

    def in_doubt(self, report):
        """The one D3 finding, which has to be marked as not established, and
        must not say the section is missing."""
        finding = self.one(report)
        self.assertIn("unverifiable", finding)
        self.assertNotIn("no such section in the document", finding)
        return finding


class ASectionThatExists(Frozen):
    """Sections that are in the document, and claims they hold. Some were
    "no such section in the document" in the code as committed, some were
    reported by a version of this change, and the rest are here so that what
    is found stays found."""

    def test_a_sub_section_folded_into_its_parents_chunk_is_found_there(self):
        report = self.run_on(sections_of(DOCUMENT, (CLAIM, "Section 3.1")))
        self.assertEqual(self.d3(report), [])
        self.assertIn("1 of 1 evidence claims", report)

    def test_and_a_claim_it_does_not_hold_is_reported_against_it(self):
        """Found, so the finding is about what the section holds. Not that the
        section is missing."""
        finding = self.asserted(self.run_on(sections_of(
            DOCUMENT, (CLAIM, "Section 3.1"),
            holds={**HOLDS, 20: "gauge inventory"})))
        self.assertIn("-> Section 3.1", finding)
        self.assertNotIn("no such section", finding)

    # A sub-heading on the line straight after its parent's. Until 2026-10-04
    # it replaced the parent and headed the chunk; it is now the first line of
    # the parent's chunk. Matched against the headings of chunks, as D3 once
    # did, a pointer to it would have read "no such section in the document":
    # a review of the splitter found that against the D3 of the day.
    UNDER = DOCUMENT[:32] + [
        "## 5. Runtime Orchestrator", "### 5.1 Responsibility",
        "The orchestrator schedules each tick and commits its state.",
        "It freezes the order of a tick before any reading is ingested.",
        ""] + DOCUMENT[32:]

    def under(self, claim, holds):
        sections = sections_of(self.UNDER, (claim, "Section 5.1"), holds=holds)
        self.assertIn(("5. Runtime Orchestrator", "d:33-37"), where(sections))
        self.assertNotIn("5.1 Responsibility",
                         [heading for heading, _ in where(sections)])
        return self.run_on(sections, document=self.UNDER)

    def test_a_sub_heading_straight_under_its_parent_is_found_in_its_chunk(self):
        report = self.under("Tick scheduling is described",
                            {35: "tick scheduling"})
        self.assertEqual(self.d3(report), [])
        self.assertIn("1 of 1 evidence claims", report)

    def test_and_what_it_does_not_hold_is_reported_against_it(self):
        finding = self.asserted(self.under(CLAIM, {35: "tick scheduling"}))
        self.assertIn("-> Section 5.1", finding)
        self.assertNotIn("no such section", finding)

    def test_a_heading_that_spells_out_the_word_section(self):
        document = [line.replace("## 2. Hydrology Model",
                                 "## Section 2: Hydrology Model")
                    for line in DOCUMENT]
        sections = sections_of(document, ("River stage is forecast", "Section 2"))
        self.assertIn(("Section 2: Hydrology Model", "d:11-15"), where(sections))
        self.assertEqual(self.d3(self.run_on(sections, document=document)), [])

    def test_a_heading_behind_an_anchor(self):
        document = [line.replace("## 2. Hydrology Model",
                                 '## <a name="model"></a>2. Hydrology Model')
                    for line in DOCUMENT]
        sections = sections_of(document, ("River stage is forecast", "Section 2"))
        self.assertEqual(self.d3(self.run_on(sections, document=document)), [])

    # What comes after a heading at its own depth and is not headed as another
    # place. Each of these ended the place there, and a claim held under it
    # was reported as one the place does not hold.
    KEPT = "The register of gauges is kept by reach"

    def after_appendix_a(self, first, second, claim=KEPT):
        """The document with two more headings after Appendix A, the second
        over the register of gauges, and a claim that points to Appendix A."""
        document = DOCUMENT + [
            "", first, "Each requirement is mapped by the engineer who owns it.",
            "The mapping is reviewed with the design.", "", "", second,
            "The register of gauges lists every gauge by reach.",
            "It is kept by the survey team."]
        sections = sections_of(
            document, (claim, "Appendix A"),
            holds={**HOLDS, 43: "register of gauges by reach"})
        self.assertEqual(where(sections)[-2:], [
            ("Appendix A — Requirement mapping", "d:33-41"),
            (second.lstrip("# "), "d:42-44")])
        return sections, document

    def test_a_part_of_an_appendix_marked_at_the_appendixs_own_depth(self):
        """"## A.2 Register of gauges" after "## Appendix A"."""
        sections, document = self.after_appendix_a(
            "## A.1 Method", "## A.2 Register of gauges")
        self.assertEqual(self.d3(self.run_on(sections, document=document)), [])

    def test_numbering_that_starts_again_under_an_appendix(self):
        """"## 2. Register of gauges" after "## Appendix A" is the second
        part of the appendix. As the document's Section 2 over again it
        ended the appendix."""
        sections, document = self.after_appendix_a(
            "## 1. Method", "## 2. Register of gauges")
        self.assertEqual(self.d3(self.run_on(sections, document=document)), [])

    def test_and_what_none_of_it_holds_is_still_reported_against_it(self):
        sections, document = self.after_appendix_a(
            "## A.1 Method", "## A.2 Register of gauges", claim=CLAIM)
        self.assertIn("-> Appendix A", self.asserted(
            self.run_on(sections, document=document)))

    def test_a_heading_with_no_number_at_the_sections_own_depth(self):
        """"## Detailed design of the register" after "## 3. Calibration
        Register": nothing says that it is another place."""
        document = DOCUMENT[:22] + [
            "## Detailed design of the register",
            "The register of gauges lists every gauge by reach.",
            "It is kept by the survey team, who review it each quarter.",
            "", ""] + DOCUMENT[22:]
        sections = sections_of(document, (self.KEPT, "Section 3"),
                               holds={24: "register of gauges by reach"})
        self.assertIn(("Detailed design of the register", "d:23-27"),
                      where(sections))
        self.assertEqual(self.d3(self.run_on(sections, document=document)), [])

    def test_a_section_numbered_with_a_capital_after_the_number(self):
        """"## 4A. Overfitting audit", which a pointer calls Section 4a. Read
        as a title, like "3D flood twin design", it was a section that is
        missing."""
        document = DOCUMENT[:32] + [
            "## 4A. Overfitting audit",
            "The audit compares the fit per reach, each quarter.",
            "A reach that overfits is refit by its owner.", "",
            ""] + DOCUMENT[32:]
        sections = sections_of(
            document, ("The overfitting audit is run per reach", "Section 4a"),
            holds={34: "overfitting audit per reach"})
        self.assertIn(("4A. Overfitting audit", "d:33-37"), where(sections))
        self.assertEqual(self.d3(self.run_on(sections, document=document)), [])

    def test_an_annex_and_an_appendix_with_a_number(self):
        document = DOCUMENT[:32] + [
            "## Annex B — Drift limits",
            "Drift limits per gauge are set here, and reviewed each quarter "
            "by the owner.",
            "", "", "",
            "## Appendix 2 - Drift history",
            "Drift history per gauge is kept here for one year, then archived."]
        sections = sections_of(
            document, (CLAIM, "Annex B"), ("Drift history is recorded", "Appendix 2"),
            holds={**HOLDS, 34: "calibration drift limits", 39: "drift history"})
        self.assertEqual(where(sections)[-2:], [
            ("Annex B — Drift limits", "d:33-37"),
            ("Appendix 2 - Drift history", "d:38-39")])
        report = self.run_on(sections, document=document)
        self.assertEqual(self.d3(report), [])
        self.assertIn("2 of 2 evidence claims", report)

    def test_an_appendix_numbered_in_roman_and_pointed_to_by_its_number(self):
        document = [line.replace("## Appendix A", "## Appendix II")
                    for line in DOCUMENT]
        report = self.run_on(sections_of(
            document, ("Requirement mapping tables are kept", "Appendix 2")),
            document=document)
        self.assertEqual(self.d3(report), [])
        self.assertIn("1 of 1 evidence claims", report)

    # A section longer than the size cap, which the splitter cuts into pieces
    # that share its heading.
    LONG = DOCUMENT[:17] + [
        f"Gauge {n:02d} has its drift limit recorded against its last survey "
        f"date." for n in range(1, 21)] + DOCUMENT[17:]

    def pieces(self, first, last):
        sections = sections_of(self.LONG, (CLAIM, "Section 3"),
                               holds={18: first, 40: last})
        self.assertEqual(
            [[c["name"] for c in section["capabilities"]] for section in sections
             if section["heading"] == "3. Calibration Register"],
            [[first], [last]])
        return sections

    def test_a_section_cut_into_chunks_is_looked_for_in_the_first_of_them(self):
        """The pieces were kept in a dict keyed by heading, so only the last
        was looked in."""
        report = self.run_on(
            self.pieces("calibration drift limits", "gauge inventory"),
            document=self.LONG)
        self.assertNotIn("NOT CONSULTED", report)
        self.assertEqual(self.d3(report), [])

    def test_and_in_the_last_of_them(self):
        report = self.run_on(
            self.pieces("gauge inventory", "calibration drift limits"),
            document=self.LONG)
        self.assertEqual(self.d3(report), [])

    def test_a_section_with_no_line_of_its_own_is_its_sub_sections(self):
        """A document can number 5.1 and 5.2 and never write a heading for 5."""
        document = DOCUMENT[:32] + [
            "## 5.1 Regions", "The twin runs in one region, close to the gauges.",
            "A second region is planned for the year after.", "", "",
            "## 5.2 Failover", "Failover is manual and takes an hour to do.",
            "The steps are in the runbook, which the operator keeps."]
        sections = sections_of(document, ("Failover is manual", "Section 5"),
                               holds={39: "manual failover"})
        self.assertEqual(where(sections)[-2:], [("5.1 Regions", "d:33-37"),
                                                ("5.2 Failover", "d:38-40")])
        self.assertEqual(self.d3(self.run_on(sections, document=document)), [])

    def test_several_sections_named_at_once(self):
        """`section` was looked for as a whole word, so "Sections 2, 3.1" was
        skipped as pointing outside the document."""
        report = self.run_on(sections_of(DOCUMENT, (CLAIM, "Sections 2, 3.1")))
        self.assertEqual(self.d3(report), [])
        self.assertIn("1 of 1 evidence claims", report)

    def test_the_last_of_a_list_written_with_a_comma_before_and(self):
        """"Sections 1, 2, and 3" read as 1 and 2 reports a claim that Section
        3 holds."""
        report = self.run_on(sections_of(DOCUMENT,
                                         (CLAIM, "Sections 1, 2, and 3")))
        self.assertEqual(self.d3(report), [])

    def test_a_section_named_after_a_figure_in_the_same_list(self):
        """"Section 2, Figure 1, and 3": the 1 is the figure's and the 3 is
        not."""
        report = self.run_on(sections_of(
            DOCUMENT, (CLAIM, "Section 2, Figure 1, and 3")))
        self.assertEqual(self.d3(report), [])

    def test_a_section_named_after_a_title_with_a_counting_word_in_it(self):
        """"Section 1 (Line protection) and 3": "line" owns no number here."""
        report = self.run_on(sections_of(
            DOCUMENT, (CLAIM, "Section 1 (Line protection) and 3")))
        self.assertEqual(self.d3(report), [])

    def test_a_range_of_sections_names_every_section_in_it(self):
        report = self.run_on(sections_of(DOCUMENT, (CLAIM, "Sections 2 to 4")))
        self.assertEqual(self.d3(report), [])
        self.again()
        finding = self.asserted(self.run_on(
            sections_of(DOCUMENT, (CLAIM, "Sections 1 to 2"))))
        self.assertIn("-> Sections 1 to 2", finding)

    def test_a_section_is_all_of_its_lines_not_its_first_chunk(self):
        """Section 4 runs on through its numbered steps, which the splitter
        put in a chunk of their own under the first step's words."""
        sections = sections_of(
            DOCUMENT, ("Late readings wait for the next tick", "Section 4"),
            holds={24: "step sequencing",
                   29: "late readings wait for the next tick"})
        self.assertEqual(
            [[c["name"] for c in section["capabilities"]]
             for section in sections[4:6]],
            [["step sequencing"], ["late readings wait for the next tick"]])
        self.assertEqual(self.d3(self.run_on(sections)), [])

    def test_a_section_under_a_first_chunk_that_is_not_a_heading(self):
        """A document that opens with text has a first chunk the splitter
        calls "(front matter)". That is not a line of the document, and the
        inventory is still the document's."""
        document = ["This is the third issue of the design, written for the "
                    "review board.", "It replaces the second issue in full.",
                    "", ""] + DOCUMENT[5:]
        sections = sections_of(document, (CLAIM, "Section 3.1"),
                               holds={19: "calibration drift limits"})
        self.assertEqual(where(sections)[0], ("(front matter)", "d:1-4"))
        report = self.run_on(sections, document=document)
        self.assertNotIn("NOT CONSULTED", report)
        self.assertEqual(self.d3(report), [])

    def test_the_passage_a_claim_is_in_is_one_of_the_places_it_names(self):
        """"This section and Appendix A": Appendix A does not hold it, and the
        section the claim was made in does. Section 1 is the second chunk: the
        first is the lines under the title."""
        sections = sections_of(DOCUMENT)
        self.assertEqual(where(sections)[1], ("1. Sensor Fabric", "d:6-10"))
        sections[1]["evidence_claims"] = [
            {"claim": "Gauge readings are ingested",
             "points_to": "this section and Appendix A", "quote": "q"}]
        self.assertEqual(self.d3(self.run_on(sections)), [])


    def test_this_section_is_the_whole_of_the_section_the_claim_is_made_in(self):
        """Said in the first lines of Section 3 and held in 3.1, which has a
        chunk of its own. Said in 3.1, "this section" is 3.1 and no more."""
        document = DOCUMENT[:17] + [
            "They are listed by reach.", "Each has an owner.",
            "Each is kept for ten years.", ""] + DOCUMENT[17:]
        sections = sections_of(document, holds={24: "calibration drift limits",
                                                18: "registers by reach"})
        self.assertEqual(where(sections)[3:5], [
            ("3. Calibration Register", "d:16-22"),
            ("3.1 Drift limits", "d:23-31")])
        sections[3]["evidence_claims"] = [
            {"claim": CLAIM, "points_to": "this section and Appendix A",
             "quote": "q"}]
        sections[4]["evidence_claims"] = [
            {"claim": "Registers are listed by reach",
             "points_to": "this section and Appendix A", "quote": "q"}]
        finding = self.in_doubt(self.run_on(sections, document=document))
        self.assertIn("Registers are listed by reach -> this section", finding)


    def test_and_said_before_the_first_section_it_is_not_the_whole_document(self):
        """The lines under the title are no numbered section. "This section"
        said there is those lines, not everything the title stands over."""
        document = DOCUMENT[:4] + [
            "This design is the third issue, written for the review board.",
            ""] + DOCUMENT[5:]
        sections = sections_of(document, (CLAIM, "this section and Appendix A"),
                               holds={21: "calibration drift limits"})
        self.assertEqual(where(sections)[0], ("Flood twin design", "d:1-6"))
        finding = self.in_doubt(self.run_on(sections, document=document))
        self.assertIn("-> this section and Appendix A", finding)


class ASectionThatDoesNot(Frozen):
    """The finding that is worth having: the document's own headings say so."""

    def test_a_section_nobody_wrote_is_called_missing(self):
        finding = self.asserted(self.run_on(sections_of(DOCUMENT,
                                                        (CLAIM, "Section 9"))))
        self.assertIn("Section 9 — no such section in the document", finding)

    def test_so_is_a_sub_section_of_one_that_is_there(self):
        finding = self.asserted(self.run_on(sections_of(DOCUMENT,
                                                        (CLAIM, "Section 2.4"))))
        self.assertIn("Section 2.4 — no such section in the document", finding)

    def test_so_is_an_appendix_past_the_last_one(self):
        finding = self.asserted(self.run_on(sections_of(DOCUMENT,
                                                        (CLAIM, "Appendix F"))))
        self.assertIn("Appendix F — no such section in the document", finding)

    def test_a_numbered_step_in_a_list_is_not_a_section(self):
        """The splitter takes "1. The fabric ingests readings" for a heading
        and gives it a chunk. A pointer to Section 1 means the section, and
        the step's chunk used to be searched as well."""
        sections = sections_of(
            DOCUMENT, ("The fabric ingests readings in tick order", "Section 1"),
            holds={**HOLDS, 7: "gauge inventory",
                   28: "fabric ingests readings in tick order"})
        self.assertIn("-> Section 1", self.asserted(self.run_on(sections)))

    def test_a_gap_in_the_numbering_is_no_doubt_where_headings_are_marked(self):
        """Section 4 is followed by 5.1, and there is no heading for 5. The
        marks say where section 4 ends, whatever the numbers do."""
        document = DOCUMENT[:32] + [
            "## 5.1 Regions", "The twin runs in one region, close to the gauges.",
            "A second region is planned for the year after.", ""]
        finding = self.asserted(self.run_on(
            sections_of(document, (CLAIM, "Section 4")), document=document))
        self.assertIn("-> Section 4", finding)

    def test_one_place_that_is_missing_beside_one_that_holds_nothing(self):
        """Appendix F is not in the document and Section 2 does not hold the
        claim. Nothing is in doubt: neither place it names holds it."""
        finding = self.asserted(self.run_on(sections_of(
            DOCUMENT, (CLAIM, "Section 2 and Appendix F"))))
        self.assertIn("-> Section 2 and Appendix F", finding)

    def test_a_title_that_opens_with_a_number_is_not_that_section(self):
        """"# 3D flood twin design" at the top is not section 3, and Section 3
        is not the whole document."""
        document = ["# 3D flood twin design"] + DOCUMENT[1:]
        sections = sections_of(document, ("Gauge readings are ingested",
                                          "Section 3"))
        self.assertIn("-> Section 3", self.asserted(
            self.run_on(sections, document=document)))

    def test_a_heading_the_author_commented_out_is_not_one(self):
        """As a heading, "## 5. Deployment (old)" inside a comment would end
        Section 4 there and be a Section 5 of its own."""
        document = DOCUMENT[:27] + ["<!--", "## 5. Deployment (old)",
                                    "One region.", "-->"] + DOCUMENT[27:]
        self.assertEqual(
            {head["key"]: head["end"] for head in
             synthesize.outline(document, True) if head["key"]}[("section", "4")],
            36)
        self.assertIn("unverifiable", self.one(self.run_on(
            sections_of(document, (CLAIM, "Section 5")), document=document)))

    def test_a_section_that_is_its_sub_sections_and_holds_no_such_thing(self):
        """5.1 and 5.2 with no heading for 5, and no line that could be one.
        What neither holds, Section 5 does not hold."""
        document = DOCUMENT[:32] + [
            "## 5.1 Regions", "The twin runs in one region, close to the gauges.",
            "A second region is planned for the year after.", "", "",
            "## 5.2 Failover", "Failover is manual and takes an hour to do.",
            "The steps are in the runbook, which the operator keeps."]
        self.assertIn("-> Section 5", self.asserted(self.run_on(
            sections_of(document, (CLAIM, "Section 5")), document=document)))


class APlaceThatCannotBeLookedUp(Frozen):
    """Not found among the headings, and not shown to be missing either."""

    def test_a_kind_of_place_the_document_has_none_of(self):
        """The document has no annex at all. That is no evidence about what an
        annex would be called here, or whether it is a separate file, so the
        pointer is in doubt, not refuted."""
        finding = self.in_doubt(self.run_on(sections_of(DOCUMENT,
                                                        (CLAIM, "Annex B"))))
        self.assertIn("is an annex", finding)

    def test_nor_one_place_of_two_when_the_other_cannot_be_looked_up(self):
        self.in_doubt(self.run_on(sections_of(
            DOCUMENT, (CLAIM, "Section 9 and Annex B"))))

    def test_a_line_that_opens_with_the_number_and_is_not_marked_a_heading(self):
        """Deep headings are often set in bold, not marked. The document has a
        line that opens "3.2", so nothing says there is no 3.2."""
        document = DOCUMENT[:22] + ["**3.2 Drift history**",
                                    "Drift history is kept for a year.",
                                    ""] + DOCUMENT[22:]
        finding = self.in_doubt(self.run_on(
            sections_of(document, ("Every audit is logged", "Section 3.2")),
            document=document))
        self.assertIn("line 23 of the document opens with 3.2", finding)

    def test_and_it_is_looked_for_in_the_section_it_is_part_of(self):
        """Whatever a "3.2" without a mark is, it is inside Section 3. A
        claim that Section 3 holds is not reported for pointing to it."""
        document = DOCUMENT[:22] + ["**3.2 Drift history**",
                                    "Drift history is kept for a year.",
                                    ""] + DOCUMENT[22:]
        sections = sections_of(document, ("Drift history is kept", "Section 3.2"),
                               holds={24: "drift history kept for a year"})
        report = self.run_on(sections, document=document)
        self.assertEqual(self.d3(report), [])
        self.assertIn("1 of 1 evidence claims", report)

    def test_nor_one_that_opens_with_it_behind_a_list_or_a_table_mark(self):
        for mark in ("- 4.2 The twin shall keep readings", "| 4.2 | readings |",
                     "[4.2] The twin shall keep readings",
                     "> 4.2 The twin shall keep readings",
                     "##4.2 Readings"):
            document = DOCUMENT[:27] + [mark, ""] + DOCUMENT[27:]
            self.again()
            finding = self.in_doubt(self.run_on(
                sections_of(document, (CLAIM, "clause 4.2")), document=document))
            self.assertIn("line 28 of the document opens with 4.2", finding)

    def test_nor_a_section_when_a_line_opens_with_one_of_its_sub_sections(self):
        """No line opens with 3. One opens with 3.2, in bold, and where there
        is a 3.2 there is a Section 3."""
        document = DOCUMENT[:15] + [
            "**3.2 Drift history**",
            "Drift history is kept for a year, per gauge.", "",
            "## 4. Algorithms", "The tick runs in this order.",
            "Each step finishes before the next begins.",
            "A step that fails stops the tick.", ""] + DOCUMENT[32:]
        finding = self.in_doubt(self.run_on(
            sections_of(document, (CLAIM, "Section 3"), holds={}),
            document=document))
        self.assertIn("line 16 of the document opens with 3.2", finding)

    def test_nor_an_appendix_whose_heading_is_set_in_bold(self):
        document = DOCUMENT + [
            "", "**Appendix C \N{EM DASH} Glossary**",
            "Terms used in this design, in the order of the alphabet."]
        finding = self.in_doubt(self.run_on(
            sections_of(document, (CLAIM, "Appendix C")), document=document))
        self.assertIn("line 37 of the document opens with Appendix C", finding)

    def test_nor_one_inside_a_fenced_block(self):
        """A fenced block is not read for headings. It is still a place a
        number can stand, and enough to withhold "missing"."""
        document = DOCUMENT[:27] + ["```", "4.2 keep readings", "```", ""] \
            + DOCUMENT[27:]
        self.in_doubt(self.run_on(
            sections_of(document, (CLAIM, "Section 4.2")), document=document))

    def test_nor_one_with_a_rule_under_it(self):
        """Markdown's other way of marking a heading: "-" or "=" under the
        line. A rule under the last item of a list looks the same, so it is
        not taken for a mark, and Section 3 is not looked in. The line still
        opens with 3."""
        document = DOCUMENT[:15] + [
            "3. Calibration Register", "-----------------------",
            "The registers kept for every gauge are listed with their owners.",
            "They are reviewed each quarter by the survey team.", "", "",
            ""] + DOCUMENT[22:]
        finding = self.in_doubt(self.run_on(
            sections_of(document, (CLAIM, "Section 3"), holds={}),
            document=document))
        self.assertIn("line 16 of the document opens with 3", finding)

    PART = [line.replace("## 4. Algorithms", "## Part 4: Algorithms")
            for line in DOCUMENT]

    def test_a_heading_that_has_the_number_in_it_and_does_not_open_with_it(self):
        """"## Part 4: Algorithms" is not headed 4, and no line opens with 4.
        It may be what the pointer calls Section 4."""
        finding = self.in_doubt(self.run_on(
            sections_of(self.PART, (CLAIM, "Section 4")), document=self.PART))
        self.assertIn("the heading on line 23 has 4 in it", finding)

    def test_nor_an_appendix_headed_by_its_letter_alone(self):
        """"### C. Register of gauges" under "## Appendices", in a document
        whose first appendix is headed "Appendix A"."""
        document = DOCUMENT + [
            "", "## Appendices", "", "### C. Register of gauges",
            "The register of gauges lists every gauge by reach.",
            "It is kept by the survey team."]
        finding = self.in_doubt(self.run_on(
            sections_of(document, (CLAIM, "Appendix C")), document=document))
        self.assertIn("the heading on line 39 has C in it", finding)

    def test_a_section_an_older_inventory_has_no_chunk_for(self):
        """A stub: a heading and "To be written." Until 2026-10-04 the
        splitter dropped a chunk of sixty characters or fewer, heading and
        all, so an inventory cut before then never saw it. It is in the
        document all the same."""
        document = DOCUMENT[:22] + ["### 3.2 Drift history", "",
                                    "To be written.", "", ""] + DOCUMENT[22:]
        sections = sections_of(document, (CLAIM, "Section 3.2"), older=True)
        self.assertNotIn("3.2 Drift history",
                         [heading for heading, _ in where(sections)])
        finding = self.in_doubt(self.run_on(sections, document=document))
        self.assertIn("lines 23-27", finding)

    def test_and_cut_now_it_is_a_chunk_the_claim_is_checked_against(self):
        """The stub is read, it holds no such thing, and that is a finding
        about what Section 3.2 holds: the reason to keep a short chunk."""
        document = DOCUMENT[:22] + ["### 3.2 Drift history", "",
                                    "To be written.", "", ""] + DOCUMENT[22:]
        sections = sections_of(document, (CLAIM, "Section 3.2"))
        self.assertIn(("3.2 Drift history", "d:23-27"), where(sections))
        finding = self.asserted(self.run_on(sections, document=document))
        self.assertIn("-> Section 3.2", finding)
        self.assertNotIn("no such section", finding)

    UNNUMBERED = ["# Flood twin design", "", "## Sensor Fabric",
                  "The fabric ingests gauge readings, and checks each one.",
                  "It forwards them to the Hydrology Model every tick.", ""]

    def test_a_document_that_numbers_none_of_its_sections(self):
        """One heading that opens with a number shows nothing about how the
        rest are numbered. "## 3 options considered" is not Section 3, and
        does not make Section 9 a section that is missing."""
        document = self.UNNUMBERED + [
            "## 3 options considered",
            "Three ways of ingesting readings were weighed against cost.",
            "The second was chosen, for the reasons the fabric gives.", ""]
        for pointer in ("Section 9", "Section 3", "Section 5.2"):
            self.again()
            self.in_doubt(self.run_on(
                sections_of(document, (CLAIM, pointer), holds={}),
                document=document))

    def test_nor_does_a_heading_that_opens_with_a_year(self):
        document = self.UNNUMBERED + [
            "## 2024 roadmap",
            "Three more reaches are to be covered before the winter.",
            "The fabric is to be extended to them in the spring.", ""]
        finding = self.in_doubt(self.run_on(
            sections_of(document, (CLAIM, "Section 3"), holds={}),
            document=document))
        self.assertIn("none of the document's headings is a numbered section",
                      finding)

    def test_a_sub_section_missing_from_the_one_section_that_is_numbered(self):
        """No run of numbered sections here either, but 3 and 3.1 show how
        the parts of section 3 are numbered, and there is no 3.7 among them."""
        document = self.UNNUMBERED + [
            "## 3. Calibration Register",
            "The registers kept for every gauge on the river, by reach.",
            "", "### 3.1 Drift limits",
            "Drift limits are held per gauge, and reviewed each quarter.",
            "They are set by the owner of the gauge.", ""]
        finding = self.asserted(self.run_on(
            sections_of(document, (CLAIM, "Section 3.7"), holds={}),
            document=document))
        self.assertIn("Section 3.7 — no such section in the document", finding)

    def test_a_pointer_that_names_more_places_than_it_lists(self):
        """"Section 2 onwards": 3.1 comes after 2, and holds it. What a
        pointer like that does not hold cannot be told from 2 alone."""
        finding = self.in_doubt(self.run_on(
            sections_of(DOCUMENT, (CLAIM, "Section 2 onwards"))))
        self.assertIn("names more places than it lists", finding)


class APlaceInDoubtBesideOneThatWasFound(Frozen):
    """One place of two was found and does not hold the claim. The other may:
    the finding is not one to stand behind."""

    BOLD = DOCUMENT[:22] + ["**3.2 Drift history**",
                            "Drift history is kept for a year.", ""] \
        + DOCUMENT[22:]
    STUB = DOCUMENT[:22] + ["### 3.2 Drift history", "", "To be written.", "",
                            ""] + DOCUMENT[22:]

    def test_the_other_is_a_line_the_document_does_not_mark_as_a_heading(self):
        finding = self.in_doubt(self.run_on(
            sections_of(self.BOLD, ("Drift history is kept", "Sections 2 and 3.2")),
            document=self.BOLD))
        self.assertIn("-> Sections 2 and 3.2", finding)

    def test_the_other_is_of_a_kind_the_document_has_none_of(self):
        finding = self.in_doubt(self.run_on(sections_of(
            DOCUMENT, (CLAIM, "Section 2 and Annex B"))))
        self.assertIn("-> Section 2 and Annex B", finding)

    def test_part_of_the_place_is_in_no_chunk(self):
        """Section 3 runs over the stub 3.2, which an inventory cut before
        2026-10-04 has no chunk for. What the rest of Section 3 does not
        hold, those lines may."""
        sections = sections_of(self.STUB, ("Every audit is logged", "Section 3"),
                               older=True)
        finding = self.in_doubt(self.run_on(sections, document=self.STUB))
        self.assertIn("-> Section 3", finding)
        self.assertIn("line 25 of it is in no section", finding)

    def test_and_a_claim_the_rest_of_it_does_hold_is_no_finding(self):
        sections = sections_of(self.STUB, (CLAIM, "Section 3"), older=True)
        self.assertEqual(self.d3(self.run_on(sections, document=self.STUB)), [])

    def test_with_the_stub_read_the_claim_is_one_the_section_does_not_hold(self):
        """The same document cut now: the stub is a chunk, every line of
        Section 3 was read, and the finding is one to stand behind."""
        sections = sections_of(self.STUB, ("Every audit is logged", "Section 3"))
        self.assertIn(("3.2 Drift history", "d:23-27"), where(sections))
        finding = self.asserted(self.run_on(sections, document=self.STUB))
        self.assertIn("-> Section 3", finding)

    def test_the_section_is_found_by_its_sub_sections_and_has_a_line_of_its_own(self):
        """A heading set in bold above sub-sections that are marked. What is
        between it and 3.1 belongs to Section 3, and only 3.1 was looked in."""
        document = DOCUMENT[:15] + [
            "**3. Calibration Register**",
            "The registers kept for every gauge are listed with their owners.",
            "", "### 3.1 Drift limits", "Drift limits are held per gauge.",
            "They are reviewed each quarter.", ""] + DOCUMENT[22:]
        finding = self.in_doubt(self.run_on(
            sections_of(document, ("Registers are listed with their owners",
                                   "Section 3"), holds={}),
            document=document))
        self.assertIn("only its sub-sections were looked in", finding)

    def test_or_is_under_a_heading_that_has_its_number_in_it(self):
        """"## Part 4: Algorithms" over a "### 4.1" that is marked. The three
        lines between them were looked at by nobody."""
        document = [line.replace("## 4. Algorithms", "## Part 4: Algorithms")
                    for line in DOCUMENT[:27]] + ["### 4.1 Steps"] + DOCUMENT[27:]
        finding = self.in_doubt(self.run_on(
            sections_of(document, (CLAIM, "Section 4"), holds={}),
            document=document))
        self.assertIn("the heading on line 23 has 4 in it", finding)
        self.assertIn("only its sub-sections were looked in", finding)


class APointerThatSaysMoreThanItsPlaces(Frozen):
    """Whose a number is, in a pointer with other words in it, is a guess: a
    table's, a count of something, a section of another document. Each guess
    asserted something false. Such a pointer is read as far as it names
    sections, to know where to look, and what is not found there is listed
    and not asserted."""

    def test_a_section_of_another_document(self):
        finding = self.in_doubt(self.run_on(sections_of(
            DOCUMENT, (CLAIM, "Section 9 of the interface control document"))))
        self.assertIn("more in it than the places it names", finding)

    def test_tables_named_before_the_section_they_are_in(self):
        """"Tables 4 and 5 of the calibration section": the 5 was read as
        Section 5, which the document does not have."""
        self.in_doubt(self.run_on(sections_of(
            DOCUMENT, (CLAIM, "Tables 4 and 5 of the calibration section"))))

    def test_a_number_that_counts_something(self):
        """Two tables, twelve gauges: read as Section 2 and Section 12."""
        for pointer in ("the calibration section (2 tables)",
                        "the calibration section, 12 gauges"):
            self.again()
            self.in_doubt(self.run_on(sections_of(DOCUMENT, (CLAIM, pointer))))

    def test_a_range_written_in_words_this_does_not_know(self):
        """Read as its two ends, 2 and 4, it reports a claim that Section 3
        holds."""
        finding = self.in_doubt(self.run_on(sections_of(
            DOCUMENT, (CLAIM, "Sections 2 up to and including 4"))))
        self.assertIn("-> Sections 2 up to and including 4", finding)

    def test_a_pointer_that_is_its_places_and_nothing_else_is_stood_behind(self):
        for pointer in ("Section 2 and Appendix A", "Sections 1, 2",
                        "\N{SECTION SIGN} 2", "Sections 1 to 2."):
            self.again()
            self.assertIn(f"-> {pointer}", self.asserted(self.run_on(
                sections_of(DOCUMENT, (CLAIM, pointer)))))


class NumbersThatAreNotTheDocumentsSections(Frozen):
    """A numbered heading under a heading that does not hold it by number."""

    # A document whose sections are numbered when it is rendered (pandoc
    # --number-sections, Sphinx, a stylesheet): the source's headings carry no
    # numbers, and its prose says "Section 2".
    RENDERED = [
        "# Flood twin design", "", "Status: draft for review.", "",
        "## Sensor Fabric", "The fabric ingests gauge readings.",
        "It checks each reading against its range.",
        "It forwards them to the Hydrology Model.", "",
        "## Hydrology Model", "The model forecasts river stage.",
        "It runs once per tick.", "It publishes a projection.", "",
        "## Operations", "To move the twin to a new release:", "",
        "### 1. Stop the service", "The operator stops the service.",
        "Readings queue at the gauges meanwhile.",
        "The queue holds one hour of readings.", "",
        "### 2. Apply the migration",
        "The operator applies the migration to the state log.",
        "It is checked against the last snapshot.",
        "A failed check stops the release.", "",
        "### 3. Start the service", "The operator starts the service.",
        "The queue drains within a minute.", "Alerts resume after that.", ""]

    def test_a_step_numbered_under_a_heading_is_not_the_documents_section(self):
        """"### 2. Apply the migration" under "## Operations" was taken for
        Section 2, which is the Hydrology Model and holds the claim."""
        finding = self.in_doubt(self.run_on(
            sections_of(self.RENDERED, ("River stage is forecast", "Section 2"),
                        holds={11: "forecast river stage"}),
            document=self.RENDERED))
        self.assertIn("2 is found only under the heading on line 15", finding)

    def test_nor_do_the_steps_show_how_the_document_numbers_its_sections(self):
        """Steps 1 to 3 made Section 5 a section that is missing."""
        finding = self.in_doubt(self.run_on(
            sections_of(self.RENDERED, (CLAIM, "Section 5"), holds={}),
            document=self.RENDERED))
        self.assertIn("none of the document's headings is a numbered section",
                      finding)

    def test_nor_do_steps_under_a_heading_that_opens_with_a_number(self):
        document = APlaceThatCannotBeLookedUp.UNNUMBERED + [
            "## 3 options considered",
            "Three ways of ingesting readings were weighed against cost.", "",
            "### 1. Do nothing", "The readings stay on the gauges for a day.",
            "They are fetched by hand each morning.", "",
            "### 2. Buy a fabric", "A fabric is bought and run by the vendor.",
            "The vendor keeps the readings for a year.", ""]
        finding = self.in_doubt(self.run_on(
            sections_of(document, (CLAIM, "Section 9"), holds={}),
            document=document))
        self.assertIn("do not show it numbering its sections this way", finding)

    def test_sub_sections_under_a_heading_that_has_no_number(self):
        """5.1 under "## Deployment". What stands between that heading and
        5.1 may be Section 5's, and it was not looked in."""
        document = DOCUMENT[:32] + [
            "## Deployment", "Deployment is automated by the release pipeline.",
            "It takes ten minutes, and a failed one is rolled back.", "", "",
            "### 5.1 Regions", "The twin runs in one region, close to the gauges.",
            "A second region is planned for the year after.", "", ""]
        finding = self.in_doubt(self.run_on(
            sections_of(document, ("Deployment is automated by the release "
                                   "pipeline", "Section 5"),
                        holds={34: "automated deployment by the release pipeline"}),
            document=document))
        self.assertIn("5 is found only under the heading on line 33", finding)

    def test_a_section_whose_number_an_appendix_uses_again_is_the_documents(self):
        """"### 2. Register of gauges" under Appendix A, beside the
        document's own Section 2. Both are looked in, and what neither holds
        is asserted."""
        document = DOCUMENT + [
            "", "### 1. Method",
            "Each requirement is mapped by the engineer who owns it.",
            "The mapping is reviewed with the design.", "", "",
            "### 2. Register of gauges",
            "The register of gauges lists every gauge by reach.",
            "It is kept by the survey team."]
        holds = {**HOLDS, 43: "register of gauges by reach"}
        self.assertIn("-> Section 2", self.asserted(self.run_on(
            sections_of(document, (CLAIM, "Section 2"), holds=holds),
            document=document)))
        self.again()
        self.assertEqual(self.d3(self.run_on(
            sections_of(document, ("The register of gauges is kept by reach",
                                   "Section 2"), holds=holds),
            document=document)), [])

    # Sections written as plain or bold numbered lines, and two steps of a
    # procedure marked with "#".
    PLAIN = [
        "# Flood twin design", "", "Status: draft for review.", "",
        "**1. Sensor Fabric**", "", "The fabric ingests gauge readings.",
        "It checks each reading against its range.",
        "It forwards them to the Hydrology Model.", "",
        "2. Hydrology Model", "The model forecasts river stage.",
        "It runs once per tick.", "It publishes a projection.", "",
        "**3. Operations**", "", "To move the twin to a new release:", "",
        "### 1. Stop the service", "The operator stops the service.",
        "Readings queue at the gauges meanwhile.",
        "The queue holds one hour of readings.", "It is drained on start.", "",
        "### 2. Apply the migration",
        "The operator applies the migration to the state log.",
        "It is checked against the last snapshot.",
        "A failed check stops the release.", "The snapshot is restored.", ""]

    def test_a_step_marked_as_a_heading_beside_a_section_that_is_not(self):
        """"2. Hydrology Model" has its own paragraph under it and no "#".
        The pointer was looked for under "### 2. Apply the migration"."""
        for pointer, line in (("Section 2", 11), ("Section 1", 5)):
            self.again()
            finding = self.in_doubt(self.run_on(
                sections_of(self.PLAIN, ("River stage is forecast", pointer),
                            holds={12: "forecast river stage"}),
                document=self.PLAIN))
            self.assertIn(f"line {line} of the document opens with "
                          f"{pointer[-1]}, is not in a list", finding)

    def test_an_appendix_under_a_heading_is_the_documents_all_the_same(self):
        """"### Appendix A" under "## Appendices". A letter counts nothing."""
        document = DOCUMENT[:32] + [
            "## Appendices", "", "### Appendix A \N{EM DASH} Requirement mapping",
            "R-010 is addressed in Section 1.", "R-011 is addressed in Section 2.",
            "The mapping is kept by the twin team.", ""]
        self.assertIn("-> Appendix A", self.asserted(self.run_on(
            sections_of(document, (CLAIM, "Appendix A")), document=document)))
        self.again()
        self.assertIn("Appendix F \N{EM DASH} no such section", self.asserted(
            self.run_on(sections_of(document, (CLAIM, "Appendix F")),
                        document=document)))


class APlaceThatIsNotASection(Frozen):
    """Counted, not listed, and never looked up as a section."""

    def test_a_table_is_not_section_four(self):
        report = self.run_on(sections_of(DOCUMENT, (CLAIM, "Table 4")))
        self.assertEqual(self.d3(report), [])
        self.assertIn("a table, figure, page or paragraph: 1", report)

    def test_a_page_number_is_not_a_section_number(self):
        report = self.run_on(sections_of(
            DOCUMENT, (CLAIM, "the register section, page 2")))
        self.assertEqual(self.d3(report), [])
        self.assertIn("a table, figure, page or paragraph: 1", report)

    def test_a_table_inside_a_section_is_looked_for_in_the_section(self):
        """And what the section does not hold is listed, not asserted: the
        pointer names more than a section."""
        finding = self.in_doubt(self.run_on(sections_of(
            DOCUMENT, (CLAIM, "Section 2, Table 4"))))
        self.assertIn("-> Section 2, Table 4", finding)
        self.assertIn("more in it than the places it names", finding)
        self.again()
        self.assertEqual(self.d3(self.run_on(sections_of(
            DOCUMENT, ("River stage is forecast", "Section 2, Table 4")))), [])

    def test_a_place_with_no_number_or_letter_is_counted(self):
        report = self.run_on(sections_of(DOCUMENT,
                                         (CLAIM, "the calibration section")))
        self.assertEqual(self.d3(report), [])
        self.assertIn("a place without a number or a letter: 1", report)

    def test_the_passage_a_claim_sits_in_is_not_somewhere_else(self):
        report = self.run_on(sections_of(DOCUMENT, (CLAIM, "this section")))
        self.assertEqual(self.d3(report), [])
        self.assertIn("the passage it is in or the one beside it: 1", report)

    def test_a_cross_section_of_the_river_is_not_a_section(self):
        report = self.run_on(sections_of(DOCUMENT, (CLAIM, "cross-section 12")))
        self.assertEqual(self.d3(report), [])
        self.assertIn("names no place in the document: 1", report)


class HowManyClaimsWereChecked(Frozen):
    """D3 skipped most claims and said nothing: on the committed fixture it
    checked 9 of 122."""

    def test_the_line_counts_what_was_checked_and_why_the_rest_was_not(self):
        report = self.run_on(sections_of(
            DOCUMENT,
            (CLAIM, "Section 3.1"),
            (CLAIM + " again", "the delivery plan"),
            (CLAIM + " in full", "Table 4"),
            ("Requirement R-010 is addressed", "Section 1"),
            (CLAIM + " once more", "")))
        self.assertIn("1 of 5 evidence claims", report)
        self.assertIn("not checked: 4", report)
        self.assertIn("no pointer: 1", report)
        self.assertIn("names no place in the document: 1", report)
        self.assertIn("a table, figure, page or paragraph: 1", report)
        self.assertIn("too little to check: 1", report)
        self.assertNotIn("no place to check against", report)

    def test_a_claim_with_no_place_to_check_it_against_is_not_one_checked(self):
        """A missing section is a finding, and nothing was compared with
        anything to reach it."""
        report = self.run_on(sections_of(DOCUMENT, (CLAIM, "Section 3.1"),
                                         (CLAIM + " again", "Section 9")))
        self.assertIn("1 of 2 evidence claims", report)
        self.assertIn("no place to check against: 1, each a D3 finding", report)

    def test_one_sentence_pointing_to_two_places_is_two_claims(self):
        report = self.run_on(sections_of(
            DOCUMENT, (CLAIM, "Section 2"), (CLAIM, "Appendix A")))
        self.assertEqual(len(self.d3(report)), 2)
        self.assertNotIn("a repeat of an earlier claim", report)

    def test_this_section_said_in_two_sections_is_two_claims(self):
        """True where Section 1 says it and untrue where Section 2 does. As
        a repeat of the first, the second was never looked at."""
        sections = sections_of(DOCUMENT)
        self.assertEqual([heading for heading, _ in where(sections)[1:3]],
                         ["1. Sensor Fabric", "2. Hydrology Model"])
        for section in sections[1:3]:
            section["evidence_claims"] = [
                {"claim": "Gauge readings are ingested",
                 "points_to": "this section and Appendix A", "quote": "q"}]
        report = self.run_on(sections)
        self.assertIn("d:11-15", self.in_doubt(report))
        self.assertNotIn("a repeat of an earlier claim", report)

    def test_a_claim_made_twice_is_looked_at_once_and_counted_as_a_repeat(self):
        report = self.run_on(sections_of(DOCUMENT, (CLAIM, "Section 9"),
                                         (CLAIM, "Section 9")))
        self.assertEqual(len(self.d3(report)), 1)
        self.assertIn("0 of 2 evidence claims", report)
        self.assertIn("a repeat of an earlier claim: 1", report)

    def test_with_nothing_left_out_nothing_is_said_about_it(self):
        report = self.run_on(sections_of(DOCUMENT, (CLAIM, "Section 3.1")))
        self.assertIn("1 of 1 evidence claims", report)
        self.assertNotIn("not checked", report)
        self.assertNotIn("no place to check against", report)

    def test_a_sentence_about_a_range_of_requirements_is_boilerplate_too(self):
        """"Requirement R-050 is addressed in Section 21" has nothing in it to
        check. Nor has the same sentence about forty requirements and twelve
        sections: "through" and "across" are not what it claims."""
        report = self.run_on(sections_of(DOCUMENT, (
            "Requirements R-001 through R-042 are addressed across",
            "Sections 1 to 2")))
        self.assertEqual(self.d3(report), [])
        self.assertIn("too little to check: 1", report)

    def test_one_word_of_content_is_too_little(self):
        """"A detailed mapping is provided": `mapping`, and nothing to tell it
        from any other mapping."""
        report = self.run_on(sections_of(
            DOCUMENT, ("A detailed mapping is provided", "Section 2")))
        self.assertEqual(self.d3(report), [])
        self.assertIn("too little to check: 1", report)


class WhichDocument(Frozen):
    """D3 looks a section up by its lines. The document it consults has to be
    the text the inventory's line numbers are in."""

    def test_the_report_says_which_document_and_what_it_found_there(self):
        report = self.run_on(sections_of(DOCUMENT, (CLAIM, "Section 3.1")))
        self.assertNotIn("NOT CONSULTED", report)
        self.assertIn("document:  d, 7 headings (numbered sections: 5, "
                      "appendices: 1)", report)
        self.assertNotIn("DOES NOT MARK", report)

    def test_which_sources_are_markdown(self):
        """By the name the manifest gives it, whatever its case. Not a .txt,
        and not a name with ".md" somewhere inside it."""
        for path, marks in (("design.md", True), ("DESIGN.MD", True),
                            ("design.txt", False), ("design.md.docx", False),
                            ("notes.mdx", False)):
            self.again()
            report = self.run_on(sections_of(DOCUMENT, (CLAIM, "Section 9")),
                                 path=path)
            self.assertEqual("DOES NOT MARK ITS HEADINGS" not in report, marks,
                             path)

    def test_an_old_inventory_of_a_text_that_is_its_own_source(self):
        """freeze.py stores a .md as it is, so the hash of the source, which
        is all an old inventory has, is the hash of the text. Nothing more
        has to tie the inventory to it: here no chunk starts on a heading,
        and the text is consulted all the same."""
        document = [
            "Flood twin design", "", "Section 1: Sensor Fabric",
            "The fabric ingests gauge readings and checks each of them.",
            "It forwards them to the Hydrology Model on every tick.", "",
            "Section 2: Hydrology Model",
            "The model forecasts river stage, once per tick, for each reach.",
            "It publishes a projection that the alerting service reads.", ""]
        sections = sections_of(document, (CLAIM, "Section 9"), holds={},
                               max_chars=100)
        digest = self.freeze(document)
        self.freeze(document, source_sha256=digest)
        report = self.run_on(sections, document=None, source_sha256=digest)
        self.assertNotIn("NOT CONSULTED", report)
        self.assertIn("DOES NOT MARK ITS HEADINGS", report)

    def test_a_document_whose_source_changed_is_not_consulted(self):
        report = self.run_on(sections_of(DOCUMENT, (CLAIM, "Section 9")),
                             source_sha256="0" * 64)
        self.assertIn("NOT CONSULTED", report)
        self.in_doubt(report)

    def test_a_re_freeze_that_moved_the_lines_is_noticed(self):
        """freeze.py --refreeze leaves the hash of the source as it was and
        moves every line. An inventory from before the text's hash was kept
        has only the source's, so each chunk's heading is looked for at the
        line its locator gives. Here the text gained a line at the top, and
        the first chunk, under the title, is the first to show it."""
        report = self.run_on(sections_of(DOCUMENT, (CLAIM, "Section 9")),
                             document=[""] + DOCUMENT)
        self.assertIn("NOT CONSULTED", report)
        self.assertIn("line 1 is not the heading the inventory has there",
                      report)
        self.in_doubt(report)

    def test_a_heading_with_nothing_in_it_is_not_found_on_a_blank_line(self):
        """A bare "#" heads a chunk whose heading is "". A blank line is ""
        as well once its hashes are taken off, so a line added just above
        that chunk put a blank line where its heading had been and nothing
        differed. Only a line of hashes is that heading."""
        lines = ["# Flood twin design", "", "The twin is in three parts.",
                 "Each is described below.", "Each has an owner.", "",
                 "#", "Gap", "Owner", "G1", "Provider not selected", "IA lead"]
        sections = [{"heading": part["heading"],
                     "locator": f"d:{part['start']}-{part['end']}"}
                    for part in inventory.split_sections(lines)]
        self.assertEqual(where(sections), [("Flood twin design", "d:1-6"),
                                           ("", "d:7-12")])
        self.assertEqual(synthesize.moved(sections, lines), "")
        shifted = lines[:6] + [""] + lines[6:]
        self.assertIn("line 7 is not the heading the inventory has there",
                      synthesize.moved(sections, shifted))

    def test_nor_after_an_entry_that_says_nothing(self):
        """An entry with no heading puts the chunk after it in doubt, and a
        chunk in doubt is not refused for its first line. It is not tied by
        a blank one either: with no other chunk to tie the text, nothing
        does, and that is said."""
        lines = ["", "Gap", "Owner", "G1", "Provider not selected", "IA lead"]
        sections = [{"error": "extraction failed"},
                    {"heading": "", "locator": "d:1-6"}]
        self.assertIn("no chunk of the inventory starts on a heading",
                      synthesize.moved(sections, lines))

    def test_a_line_added_between_two_sections_of_one_heading_is_not_seen(self):
        """A limit, pinned so that it is not taken for something checked.
        Two tables in a row each open on a bare "#". The second chunk has
        the heading of the one before it, which is also how a later piece of
        one section looks, so it is not looked at: a line added between the
        two moves the second and moved() returns "". Nothing in an entry
        tells the two cases apart."""
        table = ["#", "Gap", "Owner", "G1", "Provider not selected",
                 "IA lead", "G2", "Datum not agreed with the port authority",
                 "Hydrology lead"]
        lines = ["# Flood twin design", "The twin is in three parts.",
                 "Each is described below.", "Each has an owner.",
                 "Open items follow.", "They are reviewed monthly."] \
            + table + table
        sections = [{"heading": part["heading"],
                     "locator": f"d:{part['start']}-{part['end']}"}
                    for part in inventory.split_sections(lines)]
        self.assertEqual(where(sections), [("Flood twin design", "d:1-6"),
                                           ("", "d:7-15"), ("", "d:16-24")])
        between = lines[:15] + ["One more gap was closed."] + lines[15:]
        self.assertEqual(synthesize.moved(sections, between), "")
        above = lines[:6] + ["One more gap was closed."] + lines[6:]
        self.assertIn("line 7 is not the heading the inventory has there",
                      synthesize.moved(sections, above))

    def test_and_in_an_inventory_cut_before_the_title_had_a_chunk(self):
        report = self.run_on(sections_of(DOCUMENT, (CLAIM, "Section 9"),
                                         older=True),
                             document=[""] + DOCUMENT)
        self.assertIn("NOT CONSULTED", report)
        self.assertIn("line 6 is not the heading the inventory has there",
                      report)
        self.in_doubt(report)

    def test_and_so_is_one_that_cut_the_text_short(self):
        """Only the end of the last chunk is past the end of the text: its
        heading is still where the inventory has it."""
        report = self.run_on(sections_of(DOCUMENT, (CLAIM, "Section 9")),
                             document=DOCUMENT[:34])
        self.assertIn("NOT CONSULTED", report)
        self.assertIn("the text has 34 lines", report)
        self.in_doubt(report)

    def test_an_inventory_no_chunk_of_which_starts_on_a_heading(self):
        """Nothing ties its line numbers to any text. Here the headings are
        spelled "Section 3: ...", which the splitter does not take for
        headings, so every chunk is front matter cut for size."""
        document = [
            "Flood twin design", "", "Section 1: Sensor Fabric",
            "The fabric ingests gauge readings and checks each of them.",
            "It forwards them to the Hydrology Model on every tick.", "",
            "Section 2: Hydrology Model",
            "The model forecasts river stage, once per tick, for each reach.",
            "It publishes a projection that the alerting service reads.", ""]
        sections = sections_of(document, (CLAIM, "Section 9"), holds={},
                               max_chars=100)
        self.assertEqual([heading for heading, _ in where(sections)],
                         ["(front matter)"] * 2)
        report = self.run_on(sections, document=document)
        self.assertIn("NOT CONSULTED", report)
        self.assertIn("no chunk of the inventory starts on a heading", report)
        self.in_doubt(report)

    def test_a_failed_section_with_no_heading_does_not_refuse_the_text(self):
        """A failed section was once recorded as {"error": ...} and nothing
        else. The piece after it, of a section cut for size, carries a heading
        that is on no line of its own: that is not a text that moved."""
        long = ASectionThatExists.LONG
        sections = sections_of(long, (CLAIM, "Section 2"))
        first = [section["heading"] for section in sections].index(
            "3. Calibration Register")
        self.assertEqual(sections[first + 1]["heading"],
                         "3. Calibration Register")
        sections[first] = {"error": "no schema-valid reply after 2 attempts"}
        report = self.run_on(sections, document=long)
        self.assertNotIn("NOT CONSULTED", report)
        self.assertIn("unverifiable", self.one(report))

    def test_and_it_excuses_the_one_chunk_after_it_and_no_other(self):
        """A line added further down, before section 4, is still a text that
        moved."""
        long = ASectionThatExists.LONG
        sections = sections_of(long, (CLAIM, "Section 2"))
        first = [section["heading"] for section in sections].index(
            "3. Calibration Register")
        sections[first] = {"error": "no schema-valid reply after 2 attempts"}
        at = long.index("## 4. Algorithms")
        report = self.run_on(sections, document=long[:at] + [""] + long[at:])
        self.assertIn("NOT CONSULTED", report)
        self.assertIn("'4. Algorithms'", report)

    def test_an_inventory_that_records_the_hash_of_the_text_is_held_to_it(self):
        """What inventory.py writes now. The source's hash is wrong here, and
        is not what is asked."""
        self.freeze(DOCUMENT)
        doc, lines = llm.load_doc(self.project, "d")
        written = inventory.record(
            "d", doc, 3, sections_of(lines, (CLAIM, "Section 9")), [])
        report = self.run_on(written["sections"], source_sha256="0" * 64,
                             text_sha256=written.get("text_sha256"))
        self.assertNotIn("NOT CONSULTED", report)
        self.assertIn("no such section in the document", report)

    def test_and_a_text_with_another_hash_is_not_consulted(self):
        report = self.run_on(sections_of(DOCUMENT, (CLAIM, "Section 9")),
                             text_sha256="0" * 64)
        self.assertIn("NOT CONSULTED", report)
        self.assertNotIn("no such section in the document", report)

    def test_an_inventory_with_no_hash_at_all_is_not_taken_on_trust(self):
        """Nor is a manifest with none: two hashes that are both missing are
        not two hashes that agree."""
        self.freeze(DOCUMENT, source_sha256=None, text_sha256=None)
        report = self.run_on(sections_of(DOCUMENT, (CLAIM, "Section 9")),
                             document=None, source_sha256=None)
        self.assertIn("NOT CONSULTED", report)
        self.assertIn("records no hash", report)
        self.assertNotIn("no such section in the document", report)

    def test_a_manifest_that_cannot_be_read_is_not_a_crash(self):
        os.makedirs(os.path.join(self.project, "parsed"))
        with open(os.path.join(self.project, "parsed", "MANIFEST.json"),
                  "w") as handle:
            handle.write('{"documents": [["d"]]}')
        report = self.run_on(sections_of(DOCUMENT, (CLAIM, "Section 9")),
                             document=None)
        self.assertEqual(self.status, 0)
        self.assertIn("NOT CONSULTED: d could not be read", report)


class WithoutTheDocument(Frozen):
    """The inventory alone shows neither that a section is missing nor where
    one ends, so D3 says neither."""

    def test_a_pointer_no_chunk_heading_matches_is_in_doubt_not_missing(self):
        report = self.run_on(sections_of(DOCUMENT, (CLAIM, "Section 3.1")),
                             document=None)
        self.assertIn("not among the headings the inventory records",
                      self.in_doubt(report))
        self.assertIn("0 of 1 evidence claims", report)
        self.assertIn("no place to check against: 1", report)

    def test_the_report_says_the_document_was_not_consulted(self):
        report = self.run_on(sections_of(DOCUMENT, (CLAIM, "Section 3.1")),
                             document=None)
        self.assertIn("NOT CONSULTED", report)
        self.assertIn("asserts nothing found that way", report)

    def test_a_pointer_a_chunk_heading_does_match_is_still_looked_for(self):
        report = self.run_on(sections_of(DOCUMENT, (CLAIM, "Section 3")),
                             document=None)
        self.assertEqual(self.d3(report), [])
        self.assertIn("1 of 1 evidence claims", report)

    def test_and_what_is_not_found_there_is_listed_and_not_asserted(self):
        """Only the chunks headed "3..." were looked in, and a section can
        run on under any heading the splitter chose."""
        finding = self.in_doubt(self.run_on(
            sections_of(DOCUMENT, (CLAIM, "Section 3"),
                        holds={**HOLDS, 20: "gauge inventory"}),
            document=None))
        self.assertIn("-> Section 3", finding)
        self.assertIn("the document was not consulted", finding)

    def test_and_so_is_one_whose_heading_spells_out_the_word_section(self):
        document = [line.replace("## 3. Calibration Register",
                                 "## Section 3: Calibration Register")
                    for line in DOCUMENT]
        report = self.run_on(sections_of(document, (CLAIM, "Section 3")),
                             document=None)
        self.assertEqual(self.d3(report), [])

    def test_a_section_is_looked_for_under_its_sub_sections_headings_too(self):
        """Here 3.1 comes late enough to head a chunk of its own, and what
        Section 3 is said to hold is in it."""
        document = DOCUMENT[:17] + ["Each register has an owner.",
                                    "Each is kept for ten years."] + DOCUMENT[17:]
        sections = sections_of(document, (CLAIM, "Section 3"), holds={
            17: "gauge registers", 22: "calibration drift limits"})
        self.assertEqual(where(sections)[3:5], [
            ("3. Calibration Register", "d:16-20"),
            ("3.1 Drift limits", "d:21-29")])
        self.assertEqual(self.d3(self.run_on(sections, document=None)), [])


class TextFromADocx(Frozen):
    """Text extract.py makes of a .docx has no "#". Every line that opens with
    a number is taken as the start of something, D3 looks there, and it stands
    behind nothing it does not find."""

    def run_word(self, text, *claims, holds=WORD_HOLDS, **recorded):
        return self.run_on(sections_of(text, *claims, holds=holds),
                           document=text, path="design.docx", **recorded)

    def test_the_report_says_the_headings_are_not_marked(self):
        report = self.run_word(word(), (CLAIM, "Section 3.1"))
        self.assertNotIn("NOT CONSULTED", report)
        self.assertIn("document:  d DOES NOT MARK ITS HEADINGS", report)
        self.assertIn("asserts nothing found that way", report)

    def test_a_sub_section_whose_tab_the_extractor_dropped_is_found(self):
        """The line is "3.1Drift limits". It opens with 3.1, not with 3: read
        as a second line of Section 3, it left 3.1 to be looked for in the
        whole of that section."""
        self.assertIn((("section", "3.1"), 16), [
            (head["key"], head["line"])
            for head in synthesize.outline(word(), False)])
        report = self.run_word(word(), (CLAIM, "Section 3.1"))
        self.assertEqual(self.d3(report), [])
        self.assertIn("1 of 1 evidence claims", report)

    def test_a_section_whose_heading_heads_no_chunk_is_found(self):
        """With no blank lines a heading comes three lines after the last,
        and "2. Hydrology Model" is taken into section 1's chunk."""
        report = self.run_word(word(), ("River stage is forecast", "Section 2"))
        self.assertEqual(self.d3(report), [])
        self.assertIn("1 of 1 evidence claims", report)

    def test_a_claim_a_section_does_not_hold_is_listed_and_not_asserted(self):
        finding = self.in_doubt(self.run_word(word(), (CLAIM, "Section 2")))
        self.assertIn("-> Section 2", finding)
        self.assertIn("does not mark its headings", finding)

    def test_a_section_no_line_opens_with_is_listed_and_not_called_missing(self):
        """Word keeps the numbers it gives headings itself out of the text,
        and a text with no line that opens "9" may be one of those."""
        finding = self.in_doubt(self.run_word(word(), (CLAIM, "Section 9")))
        self.assertIn("does not mark its headings", finding)

    def test_two_lines_of_shell_do_not_make_it_a_document_that_marks(self):
        """"# 1. Stop the service" and "# 2. Apply the migration", in a
        listing. Taken for marks they were sections 1 and 2, and no real
        heading was a heading any more: two true claims were reported."""
        text = word("Appendix A \N{EM DASH} Requirement mapping",
                    "# 1. Stop the service",
                    "# 2. Apply the migration and start it again")
        self.assertEqual(sum(line.startswith("# ") for line in text), 2)
        report = self.run_word(text, ("Gauge readings are ingested", "Section 1"),
                               ("River stage is forecast", "Section 2"))
        self.assertIn("DOES NOT MARK ITS HEADINGS", report)
        self.assertEqual(self.d3(report), [])

    def test_a_table_of_numbered_rows_is_not_a_numbering_of_sections(self):
        """Headings Word numbers itself carry no number in the text. A table
        whose rows read "1 - Low", "2 - Medium", "3 - High" then looked like
        three sections: Section 5 was called missing, and a true claim about
        Section 2 was reported against "2 - Medium"."""
        text = extracted((
            "Flood twin design", "Sensor Fabric",
            "The fabric ingests gauge readings, each tick.",
            "It forwards them to the Hydrology Model at once.",
            "Hydrology Model", "The model forecasts river stage for each reach.",
            "It runs once per tick and publishes a projection.",
            "Severity", "1 - Low", "2 - Medium", "3 - High",
            "The scale above is used by the alerting service only."))
        for claim, pointer in (("River stage is forecast", "Section 2"),
                               (CLAIM, "Section 5"), (CLAIM, "Section 3.1")):
            self.again()
            report = self.run_on(
                sections_of(text, (claim, pointer),
                            holds={8: "forecast river stage"}),
                document=text, path="design.docx",
                text_sha256=self.freeze(text))
            self.assertNotIn("NOT CONSULTED", report)
            self.in_doubt(report)

    def test_markdown_frozen_from_a_docx_is_not_a_markdown_source(self):
        """What the document was is the manifest's to say, not the text's."""
        report = self.run_on(sections_of(DOCUMENT, (CLAIM, "Section 9")),
                             path="design.docx")
        self.assertIn("DOES NOT MARK ITS HEADINGS", report)
        self.in_doubt(report)


class WhatTheScoreRestsOn(Frozen):
    """--ground-truth counts a finding marked unverifiable as a hit, and said
    so only when a section had gone unread. D3 has other reasons now: in text
    from a .docx every one of its findings is a question."""

    def scored(self, document, path):
        answers = {"defects": [{"id": "GT-D3-001", "class": "D3",
                                "title": "drift limits are not in Section 2",
                                "anchor": CLAIM}]}
        with open(os.path.join(self.project, "ground-truth.yaml"), "w") as handle:
            handle.write("defects: []\n")
        fake = types.SimpleNamespace(safe_load=lambda handle: answers)
        with mock.patch.dict(sys.modules, {"yaml": fake}):
            return self.run_on(
                sections_of(document, (CLAIM, "Section 2"), holds={}),
                "--ground-truth", "ground-truth.yaml", document=document,
                path=path)

    def test_a_hit_that_rests_on_a_question_is_said_to(self):
        report = self.scored(word(), "design.docx")
        self.assertIn("recall     1/1", report)
        self.assertIn("1 of the hits rest on a finding marked unverifiable",
                      report)

    def test_and_one_that_rests_on_a_finding_is_not(self):
        report = self.scored(DOCUMENT, "design.md")
        self.assertIn("recall     1/1", report)
        self.assertNotIn("rest on", report)


class WhereAChunkSaysItIs(Frozen):
    """A locator is text, and nothing promises what the text says. What an
    entry may hold besides, and what happens to one that holds something
    else, is tests/test_malformed_inventory.py."""

    def sections(self, pointer="Section 3.1"):
        return sections_of(DOCUMENT, (CLAIM, pointer),
                           holds={**HOLDS, 20: "gauge inventory"})

    def test_a_locator_with_a_line_number_no_document_has(self):
        """int() refuses a number of five thousand digits."""
        sections = self.sections()
        sections[1]["locator"] = "d:" + "9" * 5000 + "-" + "9" * 5000
        self.assertIn("-> Section 3.1", self.one(self.run_on(sections)))

    def test_a_locator_that_runs_far_past_the_end_of_the_text(self):
        """An inventory that records the text's hash is not held to its line
        numbers one by one, and a chunk "on lines 11 to 999999999" was counted
        out line by line."""
        sections = self.sections()
        sections[1]["locator"] = "d:11-999999999"
        report = self.run_on(sections, text_sha256=self.freeze(DOCUMENT))
        self.assertNotIn("NOT CONSULTED", report)
        self.assertIn("-> Section 3.1", self.one(report))


class WhatAPointerNames(unittest.TestCase):
    """places(): the sections and appendices in a pointer's text."""

    def keys(self, target):
        return synthesize.places(target)[0]

    def sections(self, target):
        return [name for kind, name in self.keys(target) if kind == "section"]

    def test_one_section_and_one_appendix(self):
        self.assertEqual(self.keys("Section 14 and Appendix F"),
                         [("section", "14"), ("appendix", "f")])

    def test_a_list_of_sections(self):
        self.assertEqual(self.sections("Sections 6.4, 7.4 and 11.2"),
                         ["6.4", "7.4", "11.2"])

    def test_every_member_of_a_list_however_it_is_joined(self):
        """Read short by one, a list reports a claim its last section holds."""
        self.assertEqual([self.sections(target) for target in (
            "Sections 5, 6, and 7", "Sections 5, 6, or 7", "Sections 5 and/or 7",
            "Sections 5/7", "Section 5 (runtime) and 7 (failover)",
            "5 and Section 7")],
            [["5", "6", "7"], ["5", "6", "7"], ["5", "7"], ["5", "7"],
             ["5", "7"], ["5", "7"]])
        self.assertEqual(self.keys("Appendices A, B, and C"),
                         [("appendix", "a"), ("appendix", "b"),
                          ("appendix", "c")])

    def test_a_number_is_given_up_only_to_the_word_that_owns_it(self):
        """A table, a page or a step takes the number straight after it and
        no other. A title that only has such a word in it takes none."""
        self.assertEqual([self.sections(target) for target in (
            "Sections 2 (Table 4) and 5", "Sections 2 (p. 4) and 5 (p. 9)",
            "Section 2, Figure 1, and 5", "Sections 4 (step 2) and 5",
            "Section 4 (Version history) and 5",
            "Section 1 (Line protection) and 3",
            "Section 1, second paragraph, and 3.1",
            "Section 2, Tables 4 to 6, and 7")],
            [["2", "5"], ["2", "5"], ["2", "5"], ["4", "5"], ["4", "5"],
             ["1", "3"], ["1", "3.1"], ["2", "7"]])

    def test_a_range_names_everything_between_its_ends(self):
        self.assertEqual(self.sections("Sections 5 to 8"), list("5678"))
        self.assertEqual(self.sections("Sections 5.2\N{EN DASH}5.4"),
                         ["5.2", "5.3", "5.4"])
        self.assertEqual(self.sections("Sections 5 to Section 8"), list("5678"))
        self.assertEqual(self.keys("Appendices A to C"),
                         [("appendix", "a"), ("appendix", "b"),
                          ("appendix", "c")])

    def test_a_range_however_it_is_written(self):
        self.assertEqual([self.sections(target) for target in (
            "Sections 2 through to 4", "Sections 2 thru 4", "Sections 2 up to 4",
            "between Sections 2 and 4", "Sections 2\N{NON-BREAKING HYPHEN}4",
            "Sections 2\N{MINUS SIGN}4", "Sections 2 \N{EM DASH} 4")],
            [["2", "3", "4"]] * 7)

    def test_a_range_written_with_two_marks_or_with_dots(self):
        """"2--4" lost its start at the second hyphen and was read as 2 and
        4, which reports a claim that Section 3 holds."""
        self.assertEqual([self.sections(target) for target in (
            "Sections 2--4", "Sections 2 thru to 4", "Sections 2..4",
            "Sections 2\N{HORIZONTAL ELLIPSIS}4", "Sections 2 ... 4")],
            [["2", "3", "4"]] * 5)

    def test_a_range_from_one_parent_into_the_next_is_both_parents(self):
        """"3.1 to 4.2" runs to the end of 3 and into 4. Which sub-sections
        that takes in is the document's to say; the whole of 3 and of 4 holds
        them all."""
        self.assertEqual(self.sections("Sections 3.1 to 4.2"), ["3", "4"])

    def test_a_range_that_runs_backwards_or_too_far_is_its_two_ends(self):
        self.assertEqual(self.sections("Sections 7 to 3"), ["7", "3"])
        self.assertEqual(self.sections("Sections 1 to 500"), ["1", "500"])

    def test_and_names_more_than_them(self):
        """What lies between the ends of a range that cannot be filled in is
        not known, and the pointer says so. One that can be is its list."""
        for target in ("Sections 7 to 3", "Sections 1 to 500",
                       "Sections 4a to 7", "Appendices C to A"):
            self.assertTrue(synthesize.places(target)[2], target)
        for target in ("Sections 2 to 4", "Sections 4a to 4c",
                       "Appendices A to C", "Sections 3.1 to 4.2"):
            self.assertFalse(synthesize.places(target)[2], target)

    def test_a_range_of_sections_numbered_with_a_letter(self):
        self.assertEqual(self.sections("Sections 4a to 4c"), ["4a", "4b", "4c"])
        self.assertEqual(self.sections("Sections 3.1a\N{EN DASH}3.1c"),
                         ["3.1a", "3.1b", "3.1c"])
        self.assertEqual(self.sections("Sections 4a to 5b"), ["4a", "5b"])

    def test_a_range_does_not_run_across_a_word_or_into_another_kind(self):
        self.assertEqual(self.sections("Section 3 - see also 5"), ["3", "5"])
        self.assertEqual(self.keys("Section 3 to Appendix C"),
                         [("section", "3"), ("appendix", "c")])

    def test_a_pointer_that_names_more_than_it_lists(self):
        self.assertEqual([synthesize.places(target) for target in (
            "Section 2 onwards", "Section 2 et seq.", "Section 2 ff.",
            "Section 2 and following", "Sections 2, 3 etc.")],
            [([("section", "2")], False, True)] * 4
            + [([("section", "2"), ("section", "3")], False, True)])
        self.assertEqual(synthesize.places("Section 2"),
                         ([("section", "2")], False, False))

    def test_appendices_numbered_in_roman(self):
        self.assertEqual(self.keys("Appendices I and II"),
                         [("appendix", "i"), ("appendix", "ii")])
        self.assertEqual(self.keys("Appendices II to IV"),
                         [("appendix", "ii"), ("appendix", "iii"),
                          ("appendix", "iv")])

    def test_a_range_of_single_letters_that_may_be_roman_is_both(self):
        """"I to V" is five appendices, or the letters from I to V."""
        names = {name for _, name in self.keys("Appendices I to V")}
        self.assertLessEqual({"i", "ii", "iii", "iv", "v", "j", "u"}, names)

    def test_a_part_of_an_appendix_is_in_the_appendix(self):
        """"Section A.1" is not Section 1."""
        self.assertEqual([self.keys(target) for target in (
            "Section A.1", "Clause A.1", "\N{SECTION SIGN}A.1", "Appendix A.1")],
            [[("appendix", "a")]] * 4)

    def test_the_paragraph_sign_and_a_clause(self):
        self.assertEqual(self.keys("\N{SECTION SIGN}4.2"), [("section", "4.2")])
        self.assertEqual(self.keys("clause 7"), [("section", "7")])

    def test_the_other_ways_of_writing_section(self):
        self.assertEqual([self.keys(target) for target in
                          ("Subsection 3.1", "sub-section 3.1", "Sec. 3.1",
                           "Sect. 3.1", "SECTION 3.1", "Sec 3.1", "sect 3.1")],
                         [[("section", "3.1")]] * 7)
        self.assertEqual(self.keys("Ch. 3"), [("section", "3")])

    def test_a_number_written_with_a_leading_zero_or_a_trailing_one(self):
        self.assertEqual(self.sections("Section 03.1"), ["3.1"])
        self.assertEqual(self.sections("Section 3.0"), ["3"])

    def test_a_section_numbered_with_a_letter_after_it(self):
        self.assertEqual(self.sections("Sections 4a and 3.16a"), ["4a", "3.16a"])

    def test_and_with_a_capital_after_it(self):
        """"Section 3A" was a place without a number, and not checked."""
        self.assertEqual(self.sections("Sections 4A and 4b"), ["4a", "4b"])

    def test_a_year_straight_after_the_word_is_a_section_number(self):
        """"The 2024 roadmap section" names no Section 2024. "Section 2024"
        does, and was counted as a place without a number."""
        self.assertEqual(self.sections("Section 2024"), ["2024"])
        self.assertEqual(self.sections("the 2024 roadmap, Section 3"), ["3"])

    def test_a_small_letter_straight_after_appendix(self):
        self.assertEqual(self.keys("appendix b"), [("appendix", "b")])
        self.assertEqual(self.keys("Annex c and Appendix D"),
                         [("annex", "c"), ("appendix", "d")])

    def test_a_number_no_section_has_is_left_as_it_is(self):
        """Not filled in as a range, and not handed to int(), which refuses a
        number of five thousand digits."""
        long = "9" * 5000
        try:
            keys = self.keys(f"Sections 1 to {long}")
        except ValueError as error:
            self.fail(f"places() raised {type(error).__name__}")
        self.assertEqual(keys, [("section", "1"), ("section", long)])

    def test_several_appendices_and_an_annex(self):
        self.assertEqual(self.keys("Appendices A and C"),
                         [("appendix", "a"), ("appendix", "c")])
        self.assertEqual(self.keys("Annex 3"), [("annex", "3")])

    def test_a_table_is_not_a_section(self):
        self.assertEqual(synthesize.places("Table 4"), ([], True, False))

    def test_nor_is_a_figure_a_page_or_a_paragraph(self):
        self.assertEqual([synthesize.places(target) for target in
                          ("Figure 2", "page 12", "pp. 12", "paragraph 3",
                           "para 4", "Fig. 2")],
                         [([], True, False)] * 6)

    def test_a_section_with_a_table_in_it_is_still_the_section(self):
        self.assertEqual(synthesize.places("Section 3, Table 4"),
                         ([("section", "3")], True, False))
        self.assertEqual(synthesize.places("Table 4 of Section 3"),
                         ([("section", "3")], True, False))

    def test_numbers_that_count_something_else(self):
        self.assertEqual([self.sections(target) for target in (
            "Section 5, requirement R-012", "Section 5 of the 2024 plan",
            "Section 5, step 3", "Section 5, version 2", "Section 5, 2nd item")],
            [["5"]] * 5)

    def test_the_article_after_and_is_not_an_appendix(self):
        self.assertEqual(self.keys("Appendix B and a later note"),
                         [("appendix", "b")])

    def test_words_that_only_look_like_a_place(self):
        self.assertEqual([synthesize.places(target) for target in
                          ("the delivery plan", "Load test report", "above",
                           "cross-section 12", "the intersection of 4 reaches")],
                         [([], False, False)] * 5)


class WhatAPointerIsMadeOf(unittest.TestCase):
    """bare(): whether a pointer is its places and nothing else."""

    def test_places_and_what_joins_them(self):
        for target in ("Section 14 and Appendix F", "Sections 5.2, 8.4",
                       "Sections 2 to 13", "Appendix A", "\N{SECTION SIGN} 3.1",
                       "Sections 6.4, 7.4, 8.5, 11.2", "between Sections 2 and 4",
                       "Section 3 (and 3.1).", "Sections 2\N{EN DASH}4 or Annex B",
                       "A.1", "Sec. 3; Appendices A, B & C", "[Section 3]",
                       "Sections 3/4"):
            self.assertTrue(synthesize.bare(target), target)

    def test_anything_else_in_it(self):
        for target in ("Section 2, Table 4", "Section 4.2 of the ICD",
                       "Tables 4 and 5 of the calibration section",
                       "IEC 61508 Clause 7", "Sections 2 up to and including 4",
                       "the calibration section (2 tables)", "Section 2 onwards",
                       "Section 1 (Line protection) and 3", "Section 3, step 2",
                       "R-012 in Section 3", "this section and Appendix A",
                       "Section 3 \N{RIGHTWARDS ARROW} 4", "Section 3 + 4",
                       "Section 3 (?)", "Section 3, R-012",
                       "{'place': 'Section 9'}"):
            self.assertFalse(synthesize.bare(target), target)


class WhatALineOpensWith(unittest.TestCase):
    """leading() and heading_key(): the number or letter a line opens with."""

    def test_the_number_is_taken_whole(self):
        """"3.1Drift limits" is what a tab becomes on extraction. A reading
        that needs something after the number falls back to 3."""
        self.assertEqual([synthesize.heading_key(text) for text in (
            "3.1Drift limits", "2Hydrology Model", "3.1 Drift limits",
            "3.1. Drift limits", "Section 3: Calibration Register",
            "03.1 Drift limits", "1.0 Introduction", "2i18n and locale",
            "3.2e-mail gateway")],
            [("section", "3.1"), ("section", "2"), ("section", "3.1"),
             ("section", "3.1"), ("section", "3"), ("section", "3.1"),
             ("section", "1"), ("section", "2"), ("section", "3.2")])

    def test_a_letter_after_the_number_is_part_of_it(self):
        """DESIGN.md has a 4a and a 3.16a. As section 4 and section 3 they
        were looked for in the wrong place."""
        self.assertEqual([synthesize.heading_key(text) for text in (
            "4a. Overfitting audit", "3.16a What a reviewer is owed")],
            [("section", "4a"), ("section", "3.16a")])

    def test_and_so_is_a_capital_with_a_stop_after_it(self):
        """"## 4A. Overfitting audit" is the section a pointer calls 4a. The
        3D of "3D flood twin design" is not a 3d."""
        self.assertEqual([synthesize.heading_key(text, strict=True) for text in (
            "4A. Overfitting audit", "4A) Overfitting audit", "4A",
            "4A Overfitting audit", "3D flood twin design")],
            [("section", "4a"), ("section", "4a"), ("section", "4a"), None, None])

    def test_what_stands_in_front_of_the_number_is_not_part_of_it(self):
        self.assertEqual([synthesize.heading_key(text) for text in (
            "**3.2 Drift history**", "- 4.2 The twin shall", "| 4.2 | x |",
            "[4.2] The twin shall", '<a name="x"></a>3. Register',
            "##4. Operations", "\N{SECTION SIGN} 4.2 Scope")],
            [("section", "3.2"), ("section", "4.2"), ("section", "4.2"),
             ("section", "4.2"), ("section", "3"), ("section", "4"),
             ("section", "4.2")])

    def test_a_year_is_not_a_section_number(self):
        self.assertIsNone(synthesize.heading_key("2024 roadmap"))

    def test_an_appendix_by_letter_number_or_roman_numeral(self):
        self.assertEqual([synthesize.heading_key(text) for text in (
            "Appendix A \N{EM DASH} Requirement mapping", "Appendix 2 - History",
            "Annex B", "Appendix IV: Terms", "APPENDIX C", "Appendix About us")],
            [("appendix", "a"), ("appendix", "2"), ("annex", "b"),
             ("appendix", "iv"), ("appendix", "c"), None])

    def test_a_marked_heading_has_to_end_its_number_where_a_number_ends(self):
        """`strict`, for a line the document marks as a heading. There are no
        lost tabs in Markdown: "3D flood twin design" is a title."""
        self.assertEqual([synthesize.heading_key(text, strict=True) for text in (
            "3D flood twin design", "3. Calibration Register", "3.1 Drift limits",
            "Section 3: Calibration Register", "3", "4a. Overfitting audit",
            "Appendix A \N{EM DASH} Requirement mapping", "Appendix A1")],
            [None, ("section", "3"), ("section", "3.1"), ("section", "3"),
             ("section", "3"), ("section", "4a"), ("appendix", "a"), None])


class WhatTheDocumentIsDividedInto(unittest.TestCase):
    """outline(): the places a document is divided into and the lines each
    runs over."""

    def spans(self, lines, marked=True):
        return {heading["key"]: (heading["line"], heading["end"])
                for heading in synthesize.outline(lines, marked)
                if heading["key"]}

    def test_a_section_runs_to_the_next_one_that_is_not_inside_it(self):
        spans = self.spans(DOCUMENT)
        self.assertEqual(spans[("section", "3")], (16, 22))
        self.assertEqual(spans[("section", "3.1")], (19, 22))
        self.assertEqual(spans[("appendix", "a")], (33, 35))

    def test_where_headings_are_marked_a_numbered_step_is_not_one(self):
        spans = self.spans(DOCUMENT)
        self.assertEqual(spans[("section", "1")], (6, 10))
        self.assertEqual(spans[("section", "4")], (23, 32))

    def test_a_sub_section_at_its_parents_depth_still_belongs_to_it(self):
        """Some documents mark "5." and "5.1" with the same number of "#"."""
        lines = ["## 5. Runtime", "Body.", "## 5.1 Responsibility", "Body.",
                 "## 6. Fabric", "Body."]
        self.assertEqual(self.spans(lines)[("section", "5")], (1, 4))

    def test_section_one_does_not_take_in_section_ten(self):
        lines = ["## 1. Scope", "Body.", "## 10. Fabric", "Body.",
                 "## 11. Model", "Body."]
        self.assertEqual(self.spans(lines)[("section", "1")], (1, 2))
        self.assertEqual(
            [head["key"] for head in synthesize.located(
                ("section", "1"), synthesize.outline(lines, True))],
            [("section", "1")])

    def test_a_heading_without_a_number_under_a_section_belongs_to_it(self):
        """Marked deeper, or at the section's own depth. Nothing says that
        "## References" is another place, and a claim is looked for there too
        before it is reported."""
        lines = ["## 5. Runtime", "Body.", "### Open decisions", "Body.",
                 "## References", "Body.", "## 6. Fabric", "Body."]
        self.assertEqual(self.spans(lines)[("section", "5")], (1, 6))

    def test_nor_does_one_marked_shallower_end_it(self):
        """"# restart the fabric" inside <pre> is taken for a heading by
        anything that does not read HTML. Only a numbered heading says that
        another place has begun."""
        lines = ["## 5. Runtime", "Body.", "# Part two", "Body.",
                 "## 6. Fabric", "Body.", "# 7. Model", "Body."]
        self.assertEqual(self.spans(lines),
                         {("section", "5"): (1, 4), ("section", "6"): (5, 6),
                          ("section", "7"): (7, 8)})

    def test_an_appendix_runs_to_the_next_appendix(self):
        """Through "A.1 Method", which is not headed as a place of any kind,
        and through numbering that starts again under it. A section does not
        run on into an appendix, and a numbered section marked shallower than
        the appendix is not the appendix's."""
        lines = ["## 9. Results", "Body.", "## Appendix A", "Body.",
                 "## A.1 Method", "Body.", "## 1. Scope", "Body.",
                 "## Annex B", "Body.", "## Appendix C", "Body.",
                 "# 10. Late addition", "Body."]
        self.assertEqual(self.spans(lines),
                         {("section", "9"): (1, 2), ("appendix", "a"): (3, 8),
                          ("section", "1"): (7, 8), ("annex", "b"): (9, 10),
                          ("appendix", "c"): (11, 12),
                          ("section", "10"): (13, 14)})

    def test_a_numbered_heading_marked_deeper_is_inside_whatever_its_number(self):
        """"### 1. First step" under "## 4. Algorithms" counts the steps of
        section 4."""
        lines = ["## 4. Algorithms", "Body.", "### 1. First step", "Body.",
                 "## 5. Results", "Body."]
        self.assertEqual(self.spans(lines),
                         {("section", "4"): (1, 4), ("section", "1"): (3, 4),
                          ("section", "5"): (5, 6)})

    def test_a_heading_with_no_number_runs_to_the_next_not_marked_deeper(self):
        lines = ["## Notes", "Body.", "### Detail", "Body.", "## More", "Body."]
        self.assertEqual(
            [(head["text"], head["line"], head["end"])
             for head in synthesize.outline(lines, True)],
            [("Notes", 1, 4), ("Detail", 3, 4), ("More", 5, 6)])

    def test_a_hash_with_no_space_after_it_is_not_a_heading(self):
        """Markdown's rule. "#2 priority" is somebody's note, and as a heading
        it would be a section 2 that ends section 1. Nor are seven hashes."""
        lines = ["## 1. Scope", "The scope.", "#2 priority: the limits",
                 "####### 2. seven", "## 3. Method", "The method."]
        self.assertEqual(self.spans(lines),
                         {("section", "1"): (1, 4), ("section", "3"): (5, 6)})

    def test_closing_hashes_are_not_part_of_the_title(self):
        self.assertEqual(
            [head["text"] for head in synthesize.outline(
                ["## 1. Scope ##", "The scope.", "## 2. Method", "Text."], True)],
            ["1. Scope", "2. Method"])

    def test_a_comment_in_a_fenced_block_is_not_a_heading(self):
        """"# 2. start the service" in a shell listing would end section 1
        there and start a section 2 the document does not have."""
        lines = ["## 1. Scope", "Run it:", "```", "# 2. start the service",
                 "twin up", "```", "Then wait.", "## 3. Method", "The method."]
        self.assertEqual(self.spans(lines),
                         {("section", "1"): (1, 7), ("section", "3"): (8, 9)})

    def test_nor_in_one_fenced_with_tildes(self):
        lines = ["## 1. Scope", "Run it:", "~~~", "# 2. start the service",
                 "twin up", "~~~", "Then wait.", "## 3. Method", "The method."]
        self.assertEqual(self.spans(lines),
                         {("section", "1"): (1, 7), ("section", "3"): (8, 9)})

    def test_nor_in_one_set_in_by_four_spaces(self):
        lines = ["## 1. Scope", "Run it:", "", "    # 2. start the service",
                 "    twin up", "", "Then wait.", "## 3. Method", "The method."]
        self.assertEqual(self.spans(lines),
                         {("section", "1"): (1, 7), ("section", "3"): (8, 9)})

    def test_nor_between_pre_and_its_close(self):
        """Raw HTML that Markdown does not read into. Nothing fences it."""
        lines = ["## 1. Scope", "Run it:", "<pre>", "twin stop",
                 "# 2. start the service", "</pre>", "Then wait.",
                 "## 3. Method", "The method."]
        self.assertEqual(self.spans(lines),
                         {("section", "1"): (1, 7), ("section", "3"): (8, 9)})

    def test_and_pre_closed_on_its_own_line_opens_nothing(self):
        lines = ["## 1. Scope", "<pre>twin up</pre>", "## 2. Method",
                 "The method."]
        self.assertEqual(self.spans(lines),
                         {("section", "1"): (1, 2), ("section", "2"): (3, 4)})

    def test_a_fence_is_closed_only_by_one_as_long(self):
        """A template in four backticks with a listing in three inside it."""
        lines = ["## 1. Scope", "The template:", "````markdown", "```sh",
                 "# 2. the command that was run", "```", "````", "Then wait.",
                 "## 3. Method", "The method."]
        self.assertEqual(self.spans(lines),
                         {("section", "1"): (1, 8), ("section", "3"): (9, 10)})

    def test_and_only_by_one_of_its_own_mark_with_nothing_after_it(self):
        for inner in ("~~~", "```sh"):
            lines = ["## 1. Scope", "```", inner, "# 2. start the service",
                     "```", "## 3. Method", "The method."]
            self.assertEqual(self.spans(lines),
                             {("section", "1"): (1, 5), ("section", "3"): (6, 7)},
                             inner)

    def test_a_code_span_that_opens_a_line_opens_no_fence(self):
        """"```twin status``` shows the state." As a fence it hid every
        heading after it."""
        lines = ["## 1. Scope", "```twin status``` shows the state.",
                 "## 2. Method", "The method."]
        self.assertEqual(self.spans(lines),
                         {("section", "1"): (1, 2), ("section", "2"): (3, 4)})

    def test_which_numbered_sections_are_the_documents_own(self):
        """Each heading, with the line of the heading it stands under and is
        numbered apart from: a step under "Operations", a section that starts
        the numbering again under an appendix. Not one under the title, nor a
        sub-section under its section."""
        lines = ["# Design", "## Sensor Fabric", "Body.", "## Operations",
                 "### 1. Stop", "### 2. Apply", "## 3. Calibration",
                 "### 3.1 Drift", "## Appendix A", "### 1. Method"]
        self.assertEqual(
            {head["text"]: head["over"]["line"]
             for head in synthesize.outline(lines, True) if head["over"]},
            {"1. Stop": 4, "2. Apply": 4, "1. Method": 9})

    def test_a_comment_mark_in_running_text_opens_no_comment(self):
        """Markdown's rule again: a comment that runs over lines opens a
        line. Taken for one, "`<!--`" in a sentence hid every heading after
        it."""
        lines = ["## 1. Scope", "Notes for editors open with `<!--` in the "
                 "source.", "## 2. Method", "The method."]
        self.assertEqual(self.spans(lines),
                         {("section", "1"): (1, 2), ("section", "2"): (3, 4)})

    def test_a_fence_set_in_under_an_item_of_a_list(self):
        lines = ["## 1. Scope", "1. Run it:", "", "   ```sh",
                 "   # 2. start the service", "   ```", "## 3. Method", "x"]
        self.assertEqual(self.spans(lines),
                         {("section", "1"): (1, 6), ("section", "3"): (7, 8)})

    def test_a_fence_is_closed_by_one_set_in_no_further_than_it(self):
        """Four spaces in from a fence at the margin is a line of the
        listing. Four spaces in under an item of a list is where the fence
        itself stands."""
        lines = ["## 1. Scope", "```", "    ```", "# 2. still the listing",
                 "```", "## 3. Method", "x"]
        self.assertEqual(self.spans(lines),
                         {("section", "1"): (1, 5), ("section", "3"): (6, 7)})
        lines = ["## 1. Scope", "1. Run it:", "", "    ```sh", "    twin up",
                 "    ```", "## 2. Method", "x"]
        self.assertEqual(self.spans(lines),
                         {("section", "1"): (1, 6), ("section", "2"): (7, 8)})

    def test_a_heading_set_in_by_three_spaces_is_one(self):
        lines = ["## 1. Scope", "The scope.", "   ## 2. Method", "The method."]
        self.assertEqual(self.spans(lines),
                         {("section", "1"): (1, 2), ("section", "2"): (3, 4)})

    def test_a_comment_closed_on_the_line_it_opens_hides_nothing(self):
        lines = ["## 1. Scope", "<!-- a note for the editors -->",
                 "## 2. Method", "The method."]
        self.assertEqual(self.spans(lines),
                         {("section", "1"): (1, 2), ("section", "2"): (3, 4)})

    def test_a_comment_in_the_front_matter_is_not_a_heading(self):
        """"# template version 3" between the two rules a file opens with is
        a comment in YAML. As a heading it was the first of the document,
        the title was the second, and every section under the title was
        taken for one numbered apart from the document's own."""
        for close in ("---", "..."):
            lines = ["---", "title: design", "# template version 3", close,
                     "# Flood twin design", "## 1. Scope", "The scope.",
                     "## 2. Method", "The method."]
            self.assertEqual(
                [(head["text"], head["over"])
                 for head in synthesize.outline(lines, True)],
                [("Flood twin design", None), ("1. Scope", None),
                 ("2. Method", None)], close)

    def test_a_rule_further_down_or_never_closed_is_no_front_matter(self):
        lines = ["## 1. Scope", "The scope.", "---", "## 2. Method",
                 "The method."]
        self.assertEqual(self.spans(lines),
                         {("section", "1"): (1, 3), ("section", "2"): (4, 5)})
        lines = ["---", "## 1. Scope", "The scope.", "## 2. Method",
                 "The method."]
        self.assertEqual(self.spans(lines),
                         {("section", "1"): (2, 3), ("section", "2"): (4, 5)})

    def test_a_line_with_a_rule_under_it_is_not_taken_for_a_heading(self):
        """Markdown's other way of marking one. "2. It is not the sea" over a
        rule is the last item of a list, "title: 1. design" between two rules
        is the front matter of a file, and "2. Method" over a rule of "=" is
        a heading by the same sign. Nothing here tells them apart, so none is
        a heading, and section 1 runs on to the next "#"."""
        lines = ["---", "title: 1. design", "---", "", "## 1. Scope",
                 "The scope is the river.", "", "2. It is not the sea", "---",
                 "", "2. Method", "=========", "The method.", "## 3. Results",
                 "The results."]
        self.assertEqual(self.spans(lines),
                         {("section", "1"): (5, 13), ("section", "3"): (14, 15)})

    def test_text_that_marks_nothing_is_cut_at_each_line_that_opens_like_a_heading(self):
        """A number and a word with a capital, or an appendix letter. A step
        written that way starts something too, and what each starts runs to
        the next: no rule here tells a step from a heading, so nothing is
        asserted from it. It says where to look."""
        bare = ["4. Algorithms", "The steps", "1. Freeze the order", "It is fixed",
                "10 km", "3) Open the bypass", "7", "Section 4 describes them",
                "Appendix A", "The mapping", "3.1Drift limits", "Per gauge"]
        self.assertEqual(
            [(head["key"], head["line"], head["end"])
             for head in synthesize.outline(bare, False)],
            [(("section", "4"), 1, 2), (("section", "1"), 3, 8),
             (("appendix", "a"), 9, 10), (("section", "3.1"), 11, 12)])


class WhatAHeadingHasInIt(unittest.TestCase):
    """renamed(): a heading that is not headed with a place's number or
    letter, and has it standing in it."""

    def said(self, key, *titles):
        return synthesize.renamed(key, [
            {"key": synthesize.heading_key(title, strict=True), "text": title,
             "line": number} for number, title in enumerate(titles, 1)])

    def test_a_number_standing_anywhere_in_a_heading(self):
        for title in ("Part 3: Design", "Design (3)", "Stage 3.2 rollout",
                      "Work package 03"):
            self.assertIn("the heading on line 1 has",
                          self.said(("section", "3"), title), title)
        self.assertIn("has 4a in it",
                      self.said(("section", "4a"), "Part 4a: Audit"))
        self.assertIn("has 4A in it",
                      self.said(("section", "4a"), "4A Overfitting audit"))

    def test_a_letter_or_a_roman_numeral(self):
        self.assertIn("has C in it",
                      self.said(("appendix", "c"), "C. Register of gauges"))
        self.assertIn("has IV in it",
                      self.said(("appendix", "iv"), "Part IV: Terms"))
        self.assertIn("has IV in it", self.said(("annex", "4"), "Part IV: Terms"))

    def test_not_a_number_or_a_letter_that_is_part_of_something_else(self):
        for key, title in ((("section", "3"), "3D flood twin design"),
                           (("section", "2.3"), "Changes in v2.3"),
                           (("section", "3"), "Release 13"),
                           (("section", "3.1"), "Limits (3.10)"),
                           (("appendix", "a"), "About the twin")):
            self.assertEqual(self.said(key, title), "", title)

    def test_not_a_heading_that_is_headed_with_it(self):
        """A sub-section's own heading is how the place was found, not
        another name for it."""
        self.assertEqual(self.said(("section", "3"), "3.1 Drift limits",
                                   "3. Register"), "")
        self.assertIn("line 2", self.said(("section", "3"), "3.1 Drift limits",
                                          "Part 3"))


class WhichLinesHeadTextWithoutAMark(unittest.TestCase):
    """unlisted(): a line that opens with a section's number, is not a
    heading and is not an item of a list."""

    LINES = ["# Flood twin design", "", "**1. Sensor Fabric**", "",
             "The fabric ingests gauge readings.", "", "2. Hydrology Model",
             "The model forecasts river stage.", "", "The steps:", "",
             "1. Stop the service", "   The operator stops it.",
             "2. Apply the migration", "", "3. Start the service", "",
             "## 4. Algorithms", "The tick runs in this order."]

    def test_a_numbered_line_with_text_of_its_own_and_no_list_around_it(self):
        """In bold or plain. Not the items of a list, tight or loose, with a
        line set in under one of them; and not a heading."""
        self.assertEqual(
            synthesize.unlisted(self.LINES, synthesize.outline(self.LINES, True)),
            {("section", "1"): 3, ("section", "2"): 7})

    def test_the_steps_of_the_document_are_a_list(self):
        self.assertEqual(
            synthesize.unlisted(DOCUMENT, synthesize.outline(DOCUMENT, True)), {})


class WhichLinesAreMarked(unittest.TestCase):
    """marks(): the headings of a Markdown document."""

    def test_the_marked_headings_of_the_document(self):
        self.assertEqual(synthesize.marks(DOCUMENT)[:2],
                         [(1, 1, "Flood twin design"), (6, 2, "1. Sensor Fabric")])

    def marked(self, document):
        case = Frozen("run_on")
        case.setUp()
        try:
            report = case.run_on(sections_of(document, (CLAIM, "Section 9"),
                                             holds={}), document=document)
        finally:
            case.tearDown()
        return "DOES NOT MARK ITS HEADINGS" not in report

    BODY = ["The fabric ingests gauge readings, and checks each one.",
            "It forwards them to the Hydrology Model every tick.", ""]

    def test_two_of_them_do(self):
        self.assertTrue(self.marked(["# Flood twin design", "",
                                     "## 1. Sensor Fabric"] + self.BODY))

    def test_a_hash_with_nothing_after_it_is_not_one_of_the_two(self):
        self.assertFalse(self.marked(["# Flood twin design", "", "#", "",
                                      "1. Sensor Fabric"] + self.BODY))

    def test_one_of_them_does_not_make_a_document_that_marks_its_headings(self):
        """A title with "#" over sections that are plain numbered lines.
        consult() asks for two, and here every finding is in doubt."""
        document = ["# Flood twin design", ""] + [
            line.lstrip("# ") for line in DOCUMENT[2:]]
        case = Frozen("run_on")
        case.setUp()
        try:
            report = case.run_on(sections_of(document, (CLAIM, "Section 9")),
                                 document=document)
        finally:
            case.tearDown()
        self.assertIn("DOES NOT MARK ITS HEADINGS", report)


if __name__ == "__main__":
    unittest.main()
