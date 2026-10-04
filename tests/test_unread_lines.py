"""inventory.py and synthesize.py: lines of the document that are in no section.

DESIGN 3.21 turns absence into a query over a finished inventory, and the
licence is that the inventory read the whole document. inventory.py printed
"reading 100% of the document" as a constant, over a splitter that left two
kinds of line in no section, where no call is shown them:

    a heading-like line straight    was replaced by it. A numbered list is a
    before another                  run of such lines, so each step replaced
                                    the one before.
    a section of sixty characters   was dropped whole, heading included: a
    or fewer                        stub, a short front matter, a short last
                                    section.

Measured on 2026-10-04 on the committed fixture: 572 of the 574 lines of
deliverable-v1 were in a section. The other two are steps 2 and 3 of the
per-tick sequence, and step 3 is a corroborating anchor of the planted defect
GT-D5-001. The inventory DESIGN 3.24-3.30 quote from was cut that way.

The splitter now keeps both kinds. That is one claim. The other is that the
run CHECKS what its splitter left out, writes it into the inventory, and that
synthesize.py does not assert an absence over lines nothing read. Each is
tested here, in process, on documents small enough to read and on the fixture.

No model: the client in ARun answers every section and finds nothing in it.
"""

import contextlib
import csv
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import inventory
import llm
import synthesize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLOODTWIN = os.path.join(ROOT, "fixtures", "floodtwin")

PROSE = "The twin models the drainage and coastal defence network of the area."
STEPS = ["1. RO freezes the execution order for the tick.",
         "2. SF ingests telemetry and applies quality flags.",
         "3. AS reorders the frozen execution order by severity.",
         "4. RO commits state and advances the tick."]
# The shape of the fixture's Section 12: step 1 arrives with three lines
# buffered and is text; step 2 opens a section; steps 3 and 4 follow at once.
LIST = ["## 12. Algorithms", "", "Per-tick sequence:", ""] + STEPS + ["", PROSE]
STUB = ["## 6. Sensor Fabric", PROSE, PROSE, PROSE, PROSE,
        "## 7. Calibration", "To be written.", "", "", "",
        "## 8. Validation", PROSE, PROSE]


def frozen(slug):
    """The fixture's frozen text, checked against its pin on the way in."""
    return llm.load_doc(FLOODTWIN, slug)[1]


def cut(lines):
    """[(first line, last line, heading)] for each section, in order."""
    return [(s["start"], s["end"], s["heading"])
            for s in inventory.split_sections(lines)]


def shown(lines):
    """Every line a call is handed: each section's heading and its text."""
    out = []
    for section in inventory.split_sections(lines):
        out += [section["heading"]] + section["text"].split("\n")
    return out


def in_no_section(lines):
    """The lines that hold text and fall in no section's start..end. Worked
    out here from the line numbers, apart from inventory.unread_lines, so that
    the splitter is not checked only by the check that ships beside it."""
    held = {number for first, last, _ in cut(lines)
            for number in range(first, last + 1)}
    return [number for number, line in enumerate(lines, 1)
            if line.strip() and number not in held]


class AHeadingStraightAfterAHeading(unittest.TestCase):
    """The first keeps the section. The second is its first line of text."""

    def test_each_step_of_a_numbered_list_is_shown_to_a_call(self):
        self.assertEqual([step for step in STEPS if step not in shown(LIST)],
                         [])

    def test_the_first_of_the_run_heads_the_section_and_the_rest_are_text(self):
        self.assertEqual(cut(LIST), [(1, 5, "12. Algorithms"), (6, 10, STEPS[1])])
        self.assertEqual(inventory.split_sections(LIST)[1]["text"].split("\n"),
                         [STEPS[2], STEPS[3], "", PROSE])

    def test_a_heading_followed_at_once_by_its_sub_heading_keeps_both(self):
        """Text extracted from a .docx has no blank line between them. The
        parent heading was the line that went."""
        lines = ["## 5. Runtime Orchestrator", "### 5.1 Responsibility",
                 PROSE, PROSE]
        self.assertEqual(cut(lines), [(1, 4, "5. Runtime Orchestrator")])
        self.assertEqual(inventory.split_sections(lines)[0]["text"],
                         "\n".join(lines[1:]))

    def test_the_two_steps_the_fixture_lost_are_read(self):
        lines = frozen("deliverable-v1")
        self.assertEqual(lines[283:285], [
            "2. SF ingests telemetry and applies quality flags.",
            "3. AS reorders the frozen execution order by candidate severity "
            "and evaluates."])
        holding = [s for s in inventory.split_sections(lines)
                   if s["start"] <= 285 <= s["end"]]
        self.assertEqual([(s["start"], s["heading"]) for s in holding],
                         [(284, lines[283])])
        self.assertEqual(holding[0]["text"].split("\n")[0], lines[284])

    def test_no_line_of_the_fixture_that_holds_text_is_in_no_section(self):
        left = {slug: in_no_section(frozen(slug))
                for slug in ("deliverable-v1", "deliverable-v2", "requirements")}
        self.assertEqual(left, {"deliverable-v1": [], "deliverable-v2": [],
                                "requirements": []})


class ASectionOfAFewWords(unittest.TestCase):
    """Sixty characters was the floor, to save a call that "cannot produce
    anything". "To be written." is a deferral, and "Component maturity is
    assessed in Section 9." is forty-four characters and the anchor of a
    planted defect."""

    def test_a_stub_is_a_section_of_its_own(self):
        self.assertEqual(cut(STUB), [(1, 5, "6. Sensor Fabric"),
                                     (6, 10, "7. Calibration"),
                                     (11, 13, "8. Validation")])
        self.assertEqual(inventory.split_sections(STUB)[1]["text"],
                         "To be written.\n\n\n")

    def test_a_short_front_matter_is_kept(self):
        """Document control lives there: the version, and what it supersedes."""
        lines = ["FloodTwin", "Version 1.4", "", "", "## 1. Scope", PROSE, PROSE]
        self.assertEqual(cut(lines), [(1, 4, "(front matter)"), (5, 7, "1. Scope")])

    def test_a_short_last_section_is_kept(self):
        """Nothing follows it, so nothing showed that it had gone: an older
        inventory's line numbers simply stop one section early."""
        lines = ["## 8. Validation", PROSE, PROSE, PROSE, PROSE,
                 "## 9. Open items", "None recorded."]
        self.assertEqual(cut(lines), [(1, 5, "8. Validation"),
                                      (6, 7, "9. Open items")])

    def test_a_heading_with_nothing_under_it_is_kept(self):
        """A heading is text. One with no line under it is a section that
        holds nothing, which is not the same as no section."""
        last = ["## 8. Validation", PROSE, PROSE, PROSE, PROSE, "## 9. Open items"]
        self.assertEqual(cut(last)[-1], (6, 6, "9. Open items"))
        self.assertEqual(inventory.split_sections(last)[-1]["text"], "")
        blank = ["## 3. Glossary", "", "", "", "", "## 4. Scope", PROSE]
        self.assertEqual(cut(blank), [(1, 5, "3. Glossary"), (6, 7, "4. Scope")])

    def test_blank_lines_under_no_heading_are_all_that_is_left_out(self):
        """After a section is cut for size, the blank lines before the next
        heading have no heading of their own and no text. They are in no
        section, and nothing is lost with them."""
        lines = ["## 1. Terms"] + ["x" * 400] * 3 + ["", "", "", "",
                                                     "## 2. Scope", PROSE]
        self.assertEqual(cut(lines), [(1, 4, "1. Terms"), (9, 10, "2. Scope")])
        self.assertEqual(in_no_section(lines), [])


class WhatALocatorSays(unittest.TestCase):

    def test_a_sections_text_is_the_lines_its_locator_names(self):
        """The heading line and then the text, or the text alone where a
        section starts on no heading. And no line is in two sections."""
        wrong = []
        for slug in ("deliverable-v1", "deliverable-v2", "requirements"):
            lines, held = frozen(slug), []
            for section in inventory.split_sections(lines):
                span = lines[section["start"] - 1:section["end"]]
                opens = span[0].strip().lstrip("#").strip() == section["heading"]
                if "\n".join(span[1:] if opens else span) != section["text"]:
                    wrong.append((slug, section["start"]))
                held += range(section["start"], section["end"] + 1)
            if len(held) != len(set(held)):
                wrong.append((slug, "a line in two sections"))
        self.assertEqual(wrong, [])

    def test_the_committed_inventory_differs_from_a_fresh_cut_in_one_section(self):
        """fixtures/floodtwin/inv-ablation.json was cut before the splitter
        kept every line, and cannot be rebuilt without the model that made
        it. A fresh cut of the same text has the same 74 sections but one:
        the section that started on step 4 now starts on step 2."""
        with open(os.path.join(FLOODTWIN, "inv-ablation.json"),
                  encoding="utf-8") as handle:
            then = [(entry["locator"], entry["heading"])
                    for entry in json.load(handle)["sections"]]
        now = [(f"deliverable-v1:{first}-{last}", heading)
               for first, last, heading in cut(frozen("deliverable-v1"))]
        self.assertEqual((len(then), len(now)), (74, 74))
        self.assertEqual(
            [pair for pair in then if pair not in now],
            [("deliverable-v1:286-293", "4. HM estimates state and publishes "
                                        "projections, damped by the stability")])
        self.assertEqual(
            [pair for pair in now if pair not in then],
            [("deliverable-v1:284-293",
              "2. SF ingests telemetry and applies quality flags.")])


def section(heading, start, end, lines, headed=True):
    """A section as the splitter writes one, over these lines."""
    body = lines[start:end] if headed else lines[start - 1:end]
    return {"heading": heading, "start": start, "end": end,
            "text": "\n".join(body)}


class WhatTheRunChecks(unittest.TestCase):
    """inventory.unread_lines: the lines that hold text and that no section
    shows a call. The splitter is not taken at its word."""

    LINES = ["## 1. Sensor Fabric", PROSE, "",
             "A line of text that no section holds.",
             "## 2. Hydrology Model", PROSE]

    def test_a_line_in_no_section_is_reported(self):
        sections = [section("1. Sensor Fabric", 1, 3, self.LINES),
                    section("2. Hydrology Model", 5, 6, self.LINES)]
        self.assertEqual(inventory.unread_lines(self.LINES, sections), [4])

    def test_a_blank_line_in_no_section_is_not(self):
        sections = [section("1. Sensor Fabric", 1, 2, self.LINES),
                    section("(front matter)", 4, 4, self.LINES, headed=False),
                    section("2. Hydrology Model", 5, 6, self.LINES)]
        self.assertEqual(inventory.unread_lines(self.LINES, sections), [])

    def test_with_every_line_in_a_section_nothing_is(self):
        sections = [section("1. Sensor Fabric", 1, 4, self.LINES),
                    section("2. Hydrology Model", 5, 6, self.LINES)]
        self.assertEqual(inventory.unread_lines(self.LINES, sections), [])

    def test_a_section_whose_text_is_not_its_lines_shows_none_of_them(self):
        """Its locator says lines 1-4 and its text stops at line 2. The claim
        is about what a call is shown, and that is the text."""
        short = dict(section("1. Sensor Fabric", 1, 4, self.LINES), text=PROSE)
        sections = [short, section("2. Hydrology Model", 5, 6, self.LINES)]
        self.assertEqual(inventory.unread_lines(self.LINES, sections), [1, 2, 4])

    def test_nor_does_one_whose_heading_is_not_the_line_it_starts_on(self):
        """A section that kept its heading and moved its start back over the
        line before would show that line a call under the wrong account of
        where it is."""
        moved = {"heading": "2. Hydrology Model", "start": 4, "end": 6,
                 "text": "\n".join([self.LINES[3], self.LINES[5]])}
        sections = [section("1. Sensor Fabric", 1, 3, self.LINES), moved]
        self.assertEqual(inventory.unread_lines(self.LINES, sections), [4, 5, 6])

    def test_it_finds_the_two_lines_the_committed_inventory_left_out(self):
        """The check on the case it was written for: the sections of
        inv-ablation.json, as the splitter of its day cut them."""
        lines = frozen("deliverable-v1")
        with open(os.path.join(FLOODTWIN, "inv-ablation.json"),
                  encoding="utf-8") as handle:
            entries = json.load(handle)["sections"]
        sections = []
        for entry in entries:
            first, last = (int(n) for n in
                           entry["locator"].split(":")[1].split("-"))
            headed = lines[first - 1].strip().lstrip("#").strip() \
                == entry["heading"]
            sections.append(section(entry["heading"], first, last, lines,
                                    headed=headed))
        self.assertEqual(inventory.unread_lines(lines, sections), [284, 285])


class HowLinesAreNamed(unittest.TestCase):

    def test_neighbours_are_one_run(self):
        self.assertEqual(inventory.runs_of([285, 300, 284, 285]),
                         [(284, 285), (300, 300)])
        self.assertEqual(inventory.line_runs([(284, 285), (300, 300)]),
                         "lines 284-285, 300")

    def test_one_line_is_a_line(self):
        self.assertEqual(inventory.line_runs(inventory.runs_of([7])), "line 7")


class WhatTheRunSays(unittest.TestCase):
    """inventory.reading: "100% of the document" is a result, not a constant."""

    def test_one_hundred_percent_is_said_when_no_line_is_left_out(self):
        said = inventory.reading(["a", "b"], ["a", "b"], [], [], 4)
        self.assertIn("reading 100% of the document at concurrency 4", said)
        self.assertNotIn("NOT", said)

    def test_and_not_when_one_is(self):
        said = inventory.reading(["a", "b"], ["a", "b"], [], [284, 285], 1)
        self.assertNotIn("100%", said)
        self.assertIn("NOT the whole document", said)
        self.assertIn("2 line(s) that hold text are in no section", said)
        self.assertIn("lines 284-285", said)

    def test_a_run_told_to_stop_early_still_says_so(self):
        said = inventory.reading(["a", "b"], ["a", "b", "c"], ["c"], [], 1)
        self.assertIn("reading the first 2 of 3 sections (--limit)", said)
        self.assertIn("NOT the whole document", said)
        self.assertIn("The other 1 go into the inventory as not read", said)
        self.assertNotIn("in no section", said)

    def test_and_says_both_when_both_are_so(self):
        said = inventory.reading(["a", "b"], ["a", "b", "c"], ["c"], [7], 1)
        self.assertIn("the first 2 of 3 sections (--limit)", said)
        self.assertIn("1 line(s) that hold text are in no section", said)
        self.assertIn("line 7", said)


class Answering:
    """A client that reads every section and finds nothing in it."""

    def __init__(self, *args, **kwargs):
        pass

    def ask(self, system, user, validate=None, label=""):
        return {key: [] for key in inventory.FIELDS}

    def summary(self):
        return "every call answered by the test"


class ARun(unittest.TestCase):
    """inventory.main() on a frozen scratch document."""

    def setUp(self):
        self.project = tempfile.mkdtemp()
        os.mkdir(os.path.join(self.project, "parsed"))
        raw = ("\n".join(STUB) + "\n").encode("utf-8")
        with open(os.path.join(self.project, "parsed", "d.txt"), "wb") as handle:
            handle.write(raw)
        with open(os.path.join(self.project, "parsed", "MANIFEST.json"),
                  "w") as handle:
            json.dump({"documents": [{
                "slug": "d", "parsed": "parsed/d.txt",
                "source_sha256": "5" * 64,
                "text_sha256": hashlib.sha256(raw).hexdigest()}]}, handle)

    def tearDown(self):
        shutil.rmtree(self.project, ignore_errors=True)

    def run_main(self, *extra):
        """(what the run printed, the inventory it wrote)."""
        out, argv = io.StringIO(), sys.argv
        sys.argv = ["inventory.py", "--project", self.project, "--doc", "d",
                    *extra]
        try:
            with mock.patch.object(inventory, "Client", Answering), \
                    contextlib.redirect_stdout(out):
                self.status = inventory.main()
        finally:
            sys.argv = argv
        with open(os.path.join(self.project, "inventory.json"),
                  encoding="utf-8") as handle:
            return out.getvalue(), json.load(handle)

    def leaky(self):
        """The splitter, less the stub: what it did until 2026-10-04."""
        whole = inventory.split_sections

        def split(lines, **limits):
            return [s for s in whole(lines, **limits)
                    if s["heading"] != "7. Calibration"]
        return mock.patch.object(inventory, "split_sections", split)

    def test_it_reads_the_whole_document_and_the_inventory_says_so(self):
        said, written = self.run_main()
        self.assertIn("reading 100% of the document", said)
        self.assertEqual(written.get("unread_lines", "not recorded"), [])
        self.assertEqual([entry["locator"] for entry in written["sections"]],
                         ["d:1-5", "d:6-10", "d:11-13"])

    def test_a_splitter_that_leaves_lines_out_is_not_taken_at_its_word(self):
        with self.leaky():
            said, written = self.run_main()
        self.assertNotIn("100%", said)
        self.assertIn("NOT the whole document", said)
        self.assertIn("2 line(s) that hold text are in no section", said)
        self.assertIn("lines 6-7", said)

    def test_and_the_inventory_records_which_lines(self):
        with self.leaky():
            _, written = self.run_main()
        self.assertEqual(written.get("unread_lines"), [6, 7])

    def test_and_the_last_line_of_the_run_counts_them(self):
        with self.leaky():
            said, _ = self.run_main()
        self.assertIn("2 line(s) in no section", said.split("wall clock")[1])


FABRIC = {
    "heading": "1. Sensor Fabric", "locator": "d:1-20",
    "capabilities": [{"name": "ingest gauge readings", "quote": "q"}],
    "authority": [],
    "defers_to": [{"capability": "threshold adjudication",
                   "to": "Hydrology Model"}],
    "produces": ["calibrated gauge readings"], "consumes": [],
    "evidence_claims": [], "identifiers": [], "deferred": [],
    "passes": 3, "full_passes": 3}
# Nothing here owns the adjudication the Sensor Fabric defers, and nothing
# consumes what it produces: an ownership gap and an orphan, in what was read.
HYDROLOGY = {
    "heading": "2. Hydrology Model", "locator": "d:23-40",
    "capabilities": [{"name": "forecast river stage", "quote": "q"}],
    "authority": [], "defers_to": [], "produces": [], "consumes": [],
    "evidence_claims": [], "identifiers": [], "deferred": [],
    "passes": 3, "full_passes": 3}
REGISTER = {
    "heading": "3. Calibration Register", "locator": "d:41-60",
    "capabilities": [{"name": "calibration drift limits", "quote": "q"}],
    "authority": [], "defers_to": [], "produces": [], "consumes": [],
    "evidence_claims": [], "identifiers": [], "deferred": [],
    "passes": 3, "full_passes": 3}
SECTIONS = [FABRIC, HYDROLOGY, REGISTER]        # lines 21-22 are in none


class Synthesized(unittest.TestCase):
    """synthesize.main() over an inventory written to a scratch project."""

    def setUp(self):
        self.project = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.project, ignore_errors=True)

    def run_on(self, sections, *extra, **recorded):
        """`recorded` is what the inventory says beside its sections. With no
        `unread_lines` it is an inventory written before that was recorded."""
        with open(os.path.join(self.project, "inventory.json"), "w") as handle:
            json.dump(dict({"doc": "d", "runs": 3, "sections": sections},
                           **recorded), handle)
        out, argv = io.StringIO(), sys.argv
        sys.argv = ["synthesize.py", "--project", self.project, "--no-embed",
                    *extra]
        try:
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.status = synthesize.main()
        finally:
            sys.argv = argv
        return out.getvalue()

    def head(self, report):
        return report.split("-- candidate defects")[0]

    def findings(self, report):
        """{class: the lines printed under it}, for each finding listed."""
        found, current = {}, None
        for line in report.splitlines():
            if line.startswith("[D"):
                current = found.setdefault(line[1:3], [])
            if current is not None and line.strip():
                current.append(line.strip())
        return found


class TheInventoryLine(Synthesized):

    def test_an_inventory_that_left_no_line_out_says_so(self):
        head = self.head(self.run_on(SECTIONS, unread_lines=[]))
        self.assertIn("no line that holds text is outside those 3 sections",
                      head)
        self.assertNotIn("NOT IN ANY SECTION", head)

    def test_lines_the_inventory_left_out_are_counted_and_named(self):
        head = self.head(self.run_on(SECTIONS, unread_lines=[21, 22]))
        self.assertIn("NOT IN ANY SECTION: 2 line(s) that hold text", head)
        self.assertIn("lines 21-22", head)

    def test_an_older_inventory_shows_them_as_a_gap_between_its_sections(self):
        head = self.head(self.run_on(SECTIONS))
        self.assertIn("NOT IN ANY SECTION: 2 line(s), going by the sections' "
                      "own line", head)
        self.assertIn("lines 21-22", head)
        self.assertIn("does not record the lines it left out", head)

    def test_an_older_inventory_with_no_gap_is_not_said_to_be_whole(self):
        """Its last section may not be the document's last: a short one after
        it was dropped the same way, and left nothing to show for it."""
        whole = [FABRIC, dict(HYDROLOGY, locator="d:21-40"), REGISTER]
        head = self.head(self.run_on(whole))
        self.assertIn("does not record the lines it left out", head)
        self.assertIn("is not known here", head)
        self.assertNotIn("NOT IN ANY SECTION", head)
        self.assertNotIn("no line that holds text is outside", head)

    def test_a_long_list_is_cut_and_its_count_is_not(self):
        odd = list(range(101, 131, 2))                  # fifteen runs of one
        head = self.head(self.run_on(SECTIONS, unread_lines=odd))
        self.assertIn("NOT IN ANY SECTION: 15 line(s)", head)
        self.assertIn("123", head)
        self.assertNotIn("125", head)
        self.assertIn("and 3 more", head)


class AnAbsenceALineInNoSectionCouldAnswer(Synthesized):
    """"Owned nowhere" and "consumed nowhere" are statements about the whole
    document. With lines of it shown to no call they are questions."""

    def test_with_no_line_left_out_the_gap_and_the_orphan_are_findings(self):
        report = self.run_on(SECTIONS, unread_lines=[])
        self.assertEqual(sorted(self.findings(report)), ["D6", "D8"])
        self.assertNotIn("unverifiable", report)

    def test_an_orphan_a_line_in_no_section_could_consume(self):
        report = self.run_on(SECTIONS, unread_lines=[21, 22])
        d8 = " ".join(self.findings(report)["D8"])
        self.assertIn("unverifiable: a line that is in no section could "
                      "consume it: lines 21-22", d8)

    def test_an_ownership_gap_a_line_in_no_section_could_close(self):
        report = self.run_on(SECTIONS, unread_lines=[21, 22])
        d6 = " ".join(self.findings(report)["D6"])
        self.assertIn("unverifiable: a line that is in no section could own "
                      "it: lines 21-22", d6)

    def test_an_older_inventorys_gap_does_the_same(self):
        report = self.run_on(SECTIONS)
        found = self.findings(report)
        self.assertIn("could consume it: lines 21-22", " ".join(found["D8"]))
        self.assertIn("could own it: lines 21-22", " ".join(found["D6"]))

    def test_an_older_inventory_with_no_gap_keeps_its_findings(self):
        whole = [FABRIC, dict(HYDROLOGY, locator="d:21-40"), REGISTER]
        report = self.run_on(whole)
        self.assertEqual(sorted(self.findings(report)), ["D6", "D8"])
        self.assertNotIn("unverifiable", report)

    def test_two_owners_found_is_a_finding_whatever_was_left_out(self):
        def owning(entry, owner):
            return dict(entry, authority=[
                {"capability": "gauge polling", "action": "owns",
                 "owner": owner, "polarity": "owns", "quote": "q"}])
        report = self.run_on([owning(FABRIC, "Sensor Fabric"), HYDROLOGY,
                              owning(REGISTER, "Calibration Register")],
                             unread_lines=[21, 22])
        self.assertNotIn("unverifiable", " ".join(self.findings(report)["D5"]))

    def test_a_section_not_read_and_a_line_in_none_are_both_named(self):
        unread = {"error": "no schema-valid reply after 2 attempts",
                  "heading": REGISTER["heading"], "locator": REGISTER["locator"]}
        report = self.run_on([FABRIC, HYDROLOGY, unread], unread_lines=[21, 22])
        d8 = " ".join(self.findings(report)["D8"])
        self.assertIn("3. Calibration Register (d:41-60)", d8)
        self.assertIn("lines 21-22", d8)

    def test_the_summary_counts_them(self):
        report = self.run_on(SECTIONS, unread_lines=[21, 22])
        self.assertIn("unverifiable: 2 of these 2.", report)

    def test_the_candidate_file_carries_the_verdict_and_the_lines(self):
        self.run_on(SECTIONS, "--out", "candidates.csv", unread_lines=[21, 22])
        with open(os.path.join(self.project, "candidates.csv")) as handle:
            rows = {row["source_ref"]: row for row in csv.DictReader(handle)}
        self.assertEqual({k: v["verdict"] for k, v in rows.items()},
                         {"D6": "unverifiable", "D8": "unverifiable"})
        self.assertIn("lines 21-22", rows["D8"]["reason"])

    def test_limit_a_pointer_is_not_put_in_doubt_and_the_header_says_so(self):
        """LIMIT (stated, not fixed). D3 asks whether the sections a pointer
        names hold what a claim says they do, and an inventory cannot say
        which section a line in none belongs to: that takes the document's
        own outline. So a pointer to a section an older inventory dropped is
        still called a pointer to no section. The header says that D3 is not
        marked, so that the silence under the finding is not read as a check."""
        claim = dict(FABRIC, evidence_claims=[
            {"claim": "Calibration drift limits are recorded",
             "points_to": "Section 9", "quote": "q"}])
        report = self.run_on([claim, HYDROLOGY, REGISTER], unread_lines=[21, 22])
        d3 = " ".join(self.findings(report)["D3"])
        self.assertIn("no such section in the document", d3)
        self.assertNotIn("unverifiable", d3)
        self.assertIn("A pointer (D3)", self.head(report))
        self.assertIn("NOT marked", self.head(report))

    def test_a_score_says_what_the_inventory_did_not_read(self):
        with open(os.path.join(self.project, "ground-truth.yaml"), "w") as handle:
            handle.write("defects: []\n")
        report = self.run_on(SECTIONS, "--ground-truth", "ground-truth.yaml",
                             unread_lines=[21, 22])
        self.assertIn("has 2 line(s) in no section", report)
        self.assertIn("a miss may be a defect it", report)


class WhatAnInventorySaysOfItsLines(unittest.TestCase):
    """synthesize.lines_left_out -> (runs of lines, whether it was recorded)."""

    def test_the_inventorys_own_list_is_taken_first(self):
        data = {"unread_lines": [22, 21, 70], "sections": SECTIONS}
        self.assertEqual(synthesize.lines_left_out(data),
                         ([(21, 22), (70, 70)], True))

    def test_and_an_empty_list_is_an_answer(self):
        self.assertEqual(synthesize.lines_left_out(
            {"unread_lines": [], "sections": SECTIONS}), ([], True))

    def test_only_line_numbers_are_taken_from_it(self):
        data = {"unread_lines": [22, "x", True, -3, None, 21, 21],
                "sections": SECTIONS}
        self.assertEqual(synthesize.lines_left_out(data), ([(21, 22)], True))

    def test_a_list_that_is_not_one_is_no_record(self):
        data = {"unread_lines": "none", "sections": SECTIONS}
        self.assertEqual(synthesize.lines_left_out(data), ([(21, 22)], False))

    def test_without_one_the_gaps_between_the_sections_are_all_there_is(self):
        self.assertEqual(synthesize.lines_left_out({"sections": SECTIONS}),
                         ([(21, 22)], False))

    def test_lines_before_the_first_section_are_a_gap(self):
        late = [dict(FABRIC, locator="d:5-20"), HYDROLOGY, REGISTER]
        self.assertEqual(synthesize.lines_left_out({"sections": late}),
                         ([(1, 4), (21, 22)], False))

    def test_sections_out_of_order_or_overlapping_are_no_gap(self):
        mixed = [REGISTER, dict(HYDROLOGY, locator="d:15-40"), FABRIC]
        self.assertEqual(synthesize.lines_left_out({"sections": mixed}),
                         ([], False))

    def test_an_entry_with_no_locator_leaves_its_lines_as_a_gap(self):
        """A failed section, as it was once recorded: {"error": ...} and
        nothing else. Nothing places it, so its lines are in no section that
        the inventory can show."""
        data = {"sections": [FABRIC, {"error": "x"}, None, REGISTER]}
        self.assertEqual(synthesize.lines_left_out(data), ([(21, 40)], False))

    def test_a_line_number_no_document_has_costs_nothing_to_read(self):
        far = [FABRIC, dict(HYDROLOGY, locator="d:23-40"),
               dict(REGISTER, locator="d:900000000-999999999")]
        self.assertEqual(synthesize.lines_left_out({"sections": far}),
                         ([(21, 22), (41, 899999999)], False))

    def test_the_committed_inventory_has_two(self):
        with open(os.path.join(FLOODTWIN, "inv-ablation.json"),
                  encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(synthesize.lines_left_out(data), ([(284, 285)], False))


if __name__ == "__main__":
    unittest.main()
