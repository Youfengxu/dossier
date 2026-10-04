"""claim.py and undefined.py: a section that did not answer is not a section
with nothing in it.

Both tools ask a model about a document one section at a time and keep no
entry for each section. A call that failed came back as `[]`, which is also
what a section with no claim and no term comes back as:

    d: 4 sections
      4/4 sections, 3 nominations

is what undefined.py printed with one of the four unanswered, and claim.py
printed "0 finding(s)" over a document whose one contradiction sat in the
section that failed. `--limit 2` printed "d: 2 sections" of the same four. Each
run exited 0, and the only trace was `'failed': 1` in a dict on the last line.

Neither tool asserts an absence over what it did not read (undefined.py checks
a definition against the whole text, claim.py compares the claims it has), so
what is lost is findings, silently. Nothing false is reported.

Decided with the author on 2026-10-04: the run still exits 0, as inventory.py
does with failed sections; it says how many of how many sections answered,
beside its result, and names the rest; and undefined.py's --out is an object
that carries the same, where it was a bare list of terms. bundle.py and
score-claims.py read those files and now say what the files say.

A review of that change, on the code and not on this account of it, found the
readers short of it: the bundle's sheet without the note its page carried, a
run no section answered bundled as "no inputs found", a file that does not
say labelled as partial, and a score silent on the pairs nobody judged and on
the sections a pass failed on. Those are here too.

No model. The client is a script: it answers each section from the line under
its heading, and fails where a test tells it to.
"""

import contextlib
import hashlib
import html
import importlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

import bundle
import claim
import inventory
import undefined
from llm import LLMError

# By its own name, so that run-tests.py --mutate breaks the object these
# tests are looking at.
SCORE = importlib.import_module("score-claims")

PROSE = "The twin models the drainage and coastal defence network of the area."
DOCUMENT = [
    "## 1. Sensor Fabric",
    "The Sensor Fabric owns the calibration baseline for every gauge.",
    PROSE, PROSE, "",
    "## 2. Hydrology Model",
    "The Hydrology Model owns the calibration baseline for every gauge.",
    PROSE, PROSE, "",
    "## 3. Alerting Service",
    "The Alerting Service reads the calibration baseline before each tick.",
    PROSE, PROSE, "",
    "## 4. Runtime Orchestrator",
    "The Runtime Orchestrator freezes the calibration baseline for a tick.",
    PROSE, PROSE,
]
FABRIC, HYDROLOGY, ALERTING, ORCHESTRATOR = 1, 6, 11, 16   # where each starts
FAILED = {"heading": "2. Hydrology Model", "locator": "d:6-10",
          "why": "extraction failed"}


def scripted(fails=(), only_in_run=None, empty=(), judge="compatible"):
    """A client class for the tool to construct. It fails on the sections
    that start on the lines in `fails` (in the pass `only_in_run`, or in
    every pass), answers the ones in `empty` with nothing, and answers every
    other from the line under its heading. `judge` is its verdict on every
    pair it is asked about, or None for a judging call that fails."""
    class Client:
        def __init__(self, *args, **kwargs):
            self.stats = {"calls": 0, "failed": 0}
            self.prompt_version = kwargs.get("prompt_version", "")

        def ask(self, system, user, validate=None, label=""):
            self.stats["calls"] += 1
            kind, _, start = label.partition(":")
            if kind not in ("claim", "undef"):
                if judge is None:
                    self.stats["failed"] += 1
                    raise LLMError("no schema-valid reply after 2 attempts")
                return {"verdict": judge, "confidence": "high", "reason": "r"}
            start = int(start)
            run = 1 if self.prompt_version.endswith("-r1") else 0
            if start in fails and only_in_run in (None, run):
                self.stats["failed"] += 1
                raise LLMError("no schema-valid reply after 2 attempts")
            line = DOCUMENT[start]
            if kind == "undef":
                return {"terms": [] if start in empty else [
                    {"term": "calibration baseline", "quote": line}]}
            return {"claims": [] if start in empty else [
                {"subject": DOCUMENT[start - 1].split(". ", 1)[1],
                 "object": "calibration baseline",
                 "predicate": "has the calibration baseline",
                 "polarity": "affirm", "kind": "property", "quote": line}]}
    return Client


class Embedder:
    def __init__(self, *args, **kwargs):
        pass

    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


class Run(unittest.TestCase):
    """A tool's main() on the document, frozen in a scratch project."""

    def setUp(self):
        self.project = tempfile.mkdtemp()
        os.mkdir(os.path.join(self.project, "parsed"))
        self.freeze(DOCUMENT)

    def freeze(self, document):
        """Put `document` in the project as its frozen text."""
        raw = ("\n".join(document) + "\n").encode("utf-8") if document else b""
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

    def path(self, name):
        return os.path.join(self.project, name)

    def main(self, module, argv, client=None):
        """What `module.main()` printed, run on `argv`."""
        out, saved = io.StringIO(), sys.argv
        sys.argv = [module.__name__ + ".py", "--project", self.project, *argv]
        try:
            with contextlib.ExitStack() as stack:
                if client is not None:
                    stack.enter_context(
                        mock.patch.object(module, "Client", client))
                if hasattr(module, "Embedder"):
                    stack.enter_context(
                        mock.patch.object(module, "Embedder", Embedder))
                stack.enter_context(contextlib.redirect_stdout(out))
                self.status = module.main()
        finally:
            sys.argv = saved
        return out.getvalue()

    def run_tool(self, module, client, *flags, out="out.json"):
        """(what the run printed, the file it wrote)."""
        said = self.main(module, ["--doc", "d", "--out", out, *flags], client)
        with open(self.path(out), encoding="utf-8") as handle:
            return said, json.load(handle)


class TheRecord(unittest.TestCase):
    """inventory.answers: what both tools print and write."""

    SECTIONS = [{"heading": "1. Sensor Fabric", "start": 1, "end": 5},
                {"heading": "2. Hydrology Model", "start": 6, "end": 10},
                {"heading": "3. Alerting Service", "start": 11, "end": 15}]
    BEYOND = [{"error": "not read: --limit 3 stopped before this section",
               "skipped": True, "heading": "4. Runtime Orchestrator",
               "locator": "d:16-19"}]

    def record(self, passes=(2, 0, 1), runs=2, beyond=True):
        return inventory.answers(self.SECTIONS, self.BEYOND if beyond else [],
                                 list(passes), runs, "d")

    def test_a_section_no_pass_answered_is_not_read_and_is_named(self):
        read = self.record()
        self.assertEqual((read["of"], read["answered"], read["runs"]), (4, 2, 2))
        self.assertEqual(read["not_read"][0], FAILED)

    def test_so_is_one_the_run_was_told_to_stop_before(self):
        self.assertEqual(self.record()["not_read"][1:], [{
            "heading": "4. Runtime Orchestrator", "locator": "d:16-19",
            "why": "not read: --limit 3 stopped before this section"}])

    def test_one_answered_in_fewer_passes_is_read_and_listed_apart(self):
        read = self.record()
        self.assertEqual(read["short"], [{"heading": "3. Alerting Service",
                                          "locator": "d:11-15", "passes": 1}])
        self.assertNotIn("3. Alerting Service",
                         [entry["heading"] for entry in read["not_read"]])

    def test_with_every_section_answered_in_every_pass_it_lists_nothing(self):
        read = self.record(passes=(2, 2, 2), beyond=False)
        self.assertEqual((read["of"], read["answered"], read["not_read"],
                          read["short"]), (3, 3, [], []))

    def test_what_it_wrote_is_taken_back_as_a_record(self):
        self.assertTrue(inventory.is_answers(self.record()))
        self.assertTrue(inventory.is_answers(
            json.loads(json.dumps(self.record()))))

    def test_nothing_else_is(self):
        """A count missing or not whole, a list that is not one, an entry that
        is not a record, and counts that do not add up to the sections there
        were. Read leniently, each of these is a document read in full."""
        good = self.record()
        wrong = [None, [], "all", {}, dict(good, of=None), dict(good, of=4.0),
                 dict(good, answered=-1), dict(good, not_read=None),
                 dict(good, short="none"), dict(good, not_read=["2."]),
                 {k: v for k, v in good.items() if k != "runs"}]
        self.assertEqual([inventory.is_answers(value) for value in wrong],
                         [False] * len(wrong))

    def test_nor_is_one_whose_counts_do_not_add_up(self):
        """4 sections, 2 answered, 2 not read. With the two not read struck
        out by hand it says 4 sections, 2 answered and none unread."""
        self.assertFalse(inventory.is_answers(dict(self.record(), not_read=[])))

    def test_nor_one_with_a_count_below_nothing(self):
        """-1 answered and five not read add up to the four there were. In
        test_nothing_else_is a count below nothing also broke the sum, so the
        sum caught it and the check on the count was never asked."""
        five = [dict(FAILED) for _ in range(5)]
        self.assertFalse(inventory.is_answers(
            dict(self.record(), answered=-1, not_read=five)))
        self.assertFalse(inventory.is_answers(dict(self.record(), runs=-1)))

    def test_nor_one_that_lists_names_where_it_should_list_entries(self):
        """Two bare names for the two not read: the counts still add up."""
        self.assertFalse(inventory.is_answers(
            dict(self.record(), not_read=["2.", "4."])))
        self.assertFalse(inventory.is_answers(
            dict(self.record(), short=["3."])))

    def test_nor_one_that_counts_in_true_and_false(self):
        """True is 1 to Python, and no count that answers() wrote."""
        self.assertFalse(inventory.is_answers({
            "of": True, "answered": True, "runs": True, "not_read": [],
            "short": []}))


class WhatIsSaidBesideTheResult(unittest.TestCase):
    """inventory.unanswered and inventory.asking."""

    GAUGES = [{"heading": f"{n}. Gauge", "start": n * 10, "end": n * 10 + 9}
              for n in range(1, 21)]

    def said(self, read, most=12):
        return inventory.unanswered(read, "It may hold what is missing",
                                    "The result is of nothing", most)

    def test_nothing_is_said_when_every_section_answered(self):
        read = inventory.answers(TheRecord.SECTIONS, [], [1, 1, 1], 1, "d")
        self.assertEqual(self.said(read), [])

    def test_the_sections_not_read_are_counted_and_named_with_why(self):
        read = inventory.answers(TheRecord.SECTIONS, TheRecord.BEYOND,
                                 [1, 0, 1], 1, "d")
        self.assertEqual(self.said(read), [
            "2 of 4 sections were NOT read.",
            "It may hold what is missing:",
            "    2. Hydrology Model (d:6-10)  [extraction failed]",
            "    4. Runtime Orchestrator (d:16-19)  [not read: --limit 3 "
            "stopped before this section]"])

    def test_one_section_is_one_section(self):
        read = inventory.answers(TheRecord.SECTIONS, [], [1, 0, 1], 1, "d")
        self.assertEqual(self.said(read)[0], "1 of 3 sections was NOT read.")

    def test_a_run_no_section_answered_is_said_apart(self):
        read = inventory.answers(TheRecord.SECTIONS, [], [0, 0, 0], 1, "d")
        self.assertEqual(self.said(read)[:2], [
            "NOTHING WAS READ: 0 of 3 sections answered.",
            "The result is of nothing:"])
        self.assertEqual(len(self.said(read)), 5)

    def test_a_text_with_no_section_in_it_is_said_apart_too(self):
        """It was said on the run's first line and nowhere near its result,
        and the file it wrote read as a document answered in full."""
        read = inventory.answers([], [], [], 2, "d")
        self.assertEqual(self.said(read), [
            "NOTHING WAS READ: no line of the document holds text.",
            "The result is of nothing."])

    def test_a_long_list_is_cut_and_its_count_is_not(self):
        read = inventory.answers(self.GAUGES, [], [0] * 15 + [1] * 5, 1, "d")
        said = self.said(read, most=12)
        self.assertEqual(said[0], "15 of 20 sections were NOT read.")
        self.assertIn("12. Gauge (d:120-129)", said[13])
        self.assertEqual(said[14], "    and 3 more")

    def test_a_list_of_exactly_that_many_is_given_whole(self):
        read = inventory.answers(self.GAUGES, [], [0] * 12 + [1] * 8, 1, "d")
        said = self.said(read, most=12)
        self.assertEqual(len(said), 14)
        self.assertIn("12. Gauge (d:120-129)", said[-1])

    def test_the_list_of_those_answered_in_fewer_passes_is_cut_the_same(self):
        read = inventory.answers(self.GAUGES, [], [1] * 15 + [2] * 5, 2, "d")
        said = self.said(read, most=12)
        self.assertEqual(said[0], "15 section(s) answered in fewer than the "
                                  "2 passes asked for:")
        self.assertEqual(len(said), 14)
        self.assertIn("12. Gauge (d:120-129)  [1 of 2]", said[12])
        self.assertEqual(said[13], "    and 3 more")

    def test_sections_answered_in_fewer_passes_are_listed_after(self):
        read = inventory.answers(TheRecord.SECTIONS, [], [2, 1, 2], 2, "d")
        self.assertEqual(self.said(read), [
            "1 section(s) answered in fewer than the 2 passes asked for:",
            "    2. Hydrology Model (d:6-10)  [1 of 2]"])

    def test_a_section_with_no_heading_is_named_by_its_lines(self):
        read = inventory.answers([{"heading": "", "start": 7, "end": 9}], [],
                                 [0], 1, "d")
        self.assertIn("the section at d:7-9, which has no heading",
                      self.said(read)[2])

    def test_before_it_starts_a_run_says_how_many_sections_it_will_ask(self):
        every, two = TheRecord.SECTIONS, TheRecord.SECTIONS[:2]
        self.assertEqual(inventory.asking("d", every, every, []),
                         "d: 3 sections")
        self.assertEqual(
            inventory.asking("d", two, every, [TheRecord.BEYOND[0]]),
            "d: asking about the first 2 of 3 sections (--limit): NOT the "
            "whole document")
        self.assertEqual(inventory.asking("d", [], [], []),
                         "d: nothing to read: no line of the document holds "
                         "text")


class UndefinedTerms(Run):
    """undefined.py"""

    def test_a_section_whose_call_failed_is_counted_and_named(self):
        said, _ = self.run_tool(undefined, scripted(fails={HYDROLOGY}))
        self.assertIn("4/4 sections asked, 3 answered", said)
        self.assertIn("1 of 4 sections was NOT read.", said)
        self.assertIn("2. Hydrology Model (d:6-10)  [extraction failed]", said)

    def test_and_what_that_costs_the_list_is_said_with_it(self):
        """Terms are missed and none is wrongly listed. That is this tool's
        consequence and not the line a run that read nothing gets."""
        said, _ = self.run_tool(undefined, scripted(fails={HYDROLOGY}))
        self.assertIn("A term that only one of them would have nominated is "
                      "not in the list below:", said)
        self.assertNotIn("says nothing about the document", said)

    def test_a_text_with_no_section_in_it_is_not_one_with_no_such_term(self):
        self.freeze([])
        said, written = self.run_tool(undefined, scripted())
        self.assertIn("d: nothing to read", said)
        self.assertIn("NOTHING WAS READ: no line of the document holds text.",
                      said)
        self.assertEqual((written["sections"]["of"], written["terms"]),
                         (0, []))

    def test_and_it_is_said_above_the_list_it_qualifies(self):
        said, _ = self.run_tool(undefined, scripted(fails={HYDROLOGY}))
        self.assertLess(said.index("NOT read"), said.index("=" * 74))

    def test_the_file_says_how_many_sections_its_terms_came_from(self):
        """It was a bare list of terms, with nowhere to say."""
        _, written = self.run_tool(undefined, scripted(fails={HYDROLOGY}))
        self.assertIsInstance(written, dict)
        self.assertEqual(written.get("sections"), {
            "of": 4, "answered": 3, "runs": 1, "not_read": [FAILED],
            "short": []})
        self.assertEqual([term["term"] for term in written.get("terms", [])],
                         ["calibration baseline"])

    def test_a_run_told_to_stop_early_says_so_and_names_what_it_left(self):
        """`--limit 2` printed "d: 2 sections" of a document that has four."""
        said, written = self.run_tool(undefined, scripted(), "--limit", "2")
        self.assertIn("d: asking about the first 2 of 4 sections (--limit): "
                      "NOT the whole document", said)
        self.assertIn("2 of 4 sections were NOT read.", said)
        self.assertEqual(
            [(entry["heading"], entry["why"])
             for entry in written["sections"]["not_read"]],
            [("3. Alerting Service",
              "not read: --limit 2 stopped before this section"),
             ("4. Runtime Orchestrator",
              "not read: --limit 2 stopped before this section")])

    def test_with_every_section_answered_nothing_is_said_about_any(self):
        said, written = self.run_tool(undefined, scripted())
        self.assertIn("4/4 sections asked, 4 answered", said)
        self.assertNotIn("NOT read", said)
        self.assertEqual((written["sections"]["answered"],
                          written["sections"]["not_read"]), (4, []))

    def test_a_section_that_names_no_term_has_answered(self):
        """`[]` is an answer. It is what a failed call used to come back as,
        which is how the two became one."""
        said, written = self.run_tool(undefined, scripted(empty={HYDROLOGY}))
        self.assertIn("4/4 sections asked, 4 answered", said)
        self.assertEqual(written["sections"]["not_read"], [])

    def test_a_run_no_section_answered_is_not_a_document_with_no_such_term(self):
        said, written = self.run_tool(undefined, scripted(
            fails={FABRIC, HYDROLOGY, ALERTING, ORCHESTRATOR}))
        self.assertIn("NOTHING WAS READ: 0 of 4 sections answered.", said)
        self.assertIn('"0 undefined" below says nothing about the document',
                      said)
        self.assertEqual((written["sections"]["answered"], written["terms"]),
                         (0, []))

    def test_and_it_still_exits_clean(self):
        """Decided with the author: said, loudly, and exit 0, as inventory.py
        does when sections fail. A reader of the status alone learns nothing
        from it; the count is beside the result and in the file."""
        self.run_tool(undefined, scripted(
            fails={FABRIC, HYDROLOGY, ALERTING, ORCHESTRATOR}))
        self.assertEqual(self.status, 0)

    def test_a_score_says_how_many_sections_it_is_a_score_of(self):
        with open(self.path("map.yaml"), "w") as handle:
            handle.write("findings:\n"
                         "  - {id: F-1, class: framing, "
                         "terms: [calibration baseline]}\n")
        said, _ = self.run_tool(undefined, scripted(fails={HYDROLOGY}),
                                "--score", "map.yaml")
        self.assertIn("scored over 3 of 4 sections", said)
        self.again()
        with open(self.path("map.yaml"), "w") as handle:
            handle.write("findings: []\n")
        said, _ = self.run_tool(undefined, scripted(), "--score", "map.yaml")
        self.assertNotIn("scored over", said)

    def again(self):
        self.tearDown()
        self.setUp()


class ClaimIndex(Run):
    """claim.py"""

    def test_a_section_whose_call_failed_is_counted_under_the_findings(self):
        """The Sensor Fabric and the Hydrology Model each own the calibration
        baseline. With the second unanswered there is no such pair to judge,
        and "0 finding(s)" was all that was said."""
        said, _ = self.run_tool(claim, scripted(fails={HYDROLOGY}))
        self.assertIn("4/4 sections asked, 3 answered", said)
        result = said.split("finding(s)")[1]
        self.assertIn("1 of 4 sections was NOT read.", result)
        self.assertIn("A contradiction with a claim made there cannot be "
                      "among the findings", result)
        self.assertIn("2. Hydrology Model (d:6-10)  [extraction failed]",
                      result)

    def test_and_it_is_said_between_the_count_and_the_findings(self):
        """Under "N finding(s)" and above the rule that closes the count, as
        the pairs that could not be judged are. Printed after the findings
        it would be the last thing on a long report."""
        said, _ = self.run_tool(claim, scripted(fails={HYDROLOGY}))
        parts = said.split("=" * 74)
        self.assertEqual(len(parts), 3)
        self.assertIn("0 finding(s)", parts[1])
        self.assertIn("1 of 4 sections was NOT read.", parts[1])

    def test_the_count_of_answers_starts_again_with_each_pass(self):
        """A section that failed in the first pass and answered in the second
        was still counted out of the second pass's line."""
        said, _ = self.run_tool(claim, scripted(fails={HYDROLOGY},
                                                only_in_run=0))
        self.assertIn("run 1/2: 4/4 sections asked, 3 answered", said)
        self.assertIn("run 2/2: 4/4 sections asked, 4 answered", said)

    def test_a_run_of_no_pass_is_refused(self):
        """`--runs 0` asked about nothing and printed "0 finding(s)". With
        sections counted it named all four as "extraction failed", of calls
        that were never made."""
        said = io.StringIO()
        with self.assertRaises(SystemExit) as stopped, \
                contextlib.redirect_stderr(said):
            self.main(claim, ["--doc", "d", "--runs", "0"], scripted())
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn("--runs must be at least 1", said.getvalue())

    def test_and_a_run_of_one_pass_is_not(self):
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                said, written = self.run_tool(claim, scripted(), "--runs", "1")
        except SystemExit as stopped:
            self.fail(f"--runs 1 was refused, with exit {stopped.code}")
        self.assertIn("run 1/1: 4/4 sections asked, 4 answered", said)
        self.assertEqual((written["sections"]["runs"],
                          written["sections"]["short"]), (1, []))

    def test_a_text_with_no_section_in_it_has_not_been_found_consistent(self):
        self.freeze([])
        said, written = self.run_tool(claim, scripted())
        result = said.split("=" * 74)[1]
        self.assertIn("NOTHING WAS READ: no line of the document holds text.",
                      result)
        self.assertIn('"0 finding(s)" says nothing about the document.',
                      result)
        self.assertEqual(written["sections"]["of"], 0)

    def test_the_index_says_how_many_sections_its_claims_came_from(self):
        _, written = self.run_tool(claim, scripted(fails={HYDROLOGY}))
        self.assertEqual(written.get("sections"), {
            "of": 4, "answered": 3, "runs": 2, "not_read": [FAILED],
            "short": []})

    def test_a_run_told_to_stop_early_says_so_and_names_what_it_left(self):
        said, written = self.run_tool(claim, scripted(), "--limit", "2")
        self.assertIn("d: asking about the first 2 of 4 sections (--limit): "
                      "NOT the whole document", said)
        self.assertIn("2 of 4 sections were NOT read.", said)
        self.assertEqual([entry["heading"]
                          for entry in written["sections"]["not_read"]],
                         ["3. Alerting Service", "4. Runtime Orchestrator"])

    def test_a_section_one_pass_answered_is_read_and_listed_apart(self):
        said, written = self.run_tool(claim, scripted(fails={HYDROLOGY},
                                                      only_in_run=1))
        self.assertNotIn("NOT read", said)
        self.assertIn("1 section(s) answered in fewer than the 2 passes asked "
                      "for:", said)
        self.assertEqual(written["sections"]["short"], [{
            "heading": "2. Hydrology Model", "locator": "d:6-10", "passes": 1}])

    def test_with_every_section_answered_nothing_is_said_about_any(self):
        said, written = self.run_tool(claim, scripted())
        self.assertNotIn("NOT read", said)
        self.assertNotIn("fewer than", said)
        self.assertEqual((written["sections"]["answered"],
                          written["sections"]["not_read"]), (4, []))

    def test_a_section_that_asserts_nothing_has_answered(self):
        said, written = self.run_tool(claim, scripted(empty={HYDROLOGY}))
        self.assertIn("4/4 sections asked, 4 answered", said)
        self.assertEqual(written["sections"]["not_read"], [])

    def test_a_run_no_section_answered_has_not_found_a_consistent_document(self):
        said, _ = self.run_tool(claim, scripted(
            fails={FABRIC, HYDROLOGY, ALERTING, ORCHESTRATOR}))
        self.assertIn("NOTHING WAS READ: 0 of 4 sections answered.", said)
        self.assertIn('"0 finding(s)" says nothing about the document', said)
        self.assertEqual(self.status, 0)

    def test_extract_returns_how_many_passes_answered_each_section(self):
        sections = inventory.split_sections(DOCUMENT)
        client = scripted(fails={HYDROLOGY, ALERTING}, only_in_run=1)()
        with contextlib.redirect_stdout(io.StringIO()):
            claims, passes = claim.extract(client, sections, 1, runs=2)
        self.assertEqual(passes, [2, 1, 1, 2])
        self.assertEqual(len(claims), 4)

    def test_an_index_reused_brings_its_record_with_it(self):
        self.run_tool(claim, scripted(fails={HYDROLOGY}), out="index.json")
        said, written = self.run_tool(claim, scripted(), "--reuse",
                                      "index.json", out="again.json")
        self.assertIn("1 of 4 sections was NOT read.", said)
        self.assertEqual(written["sections"]["not_read"], [FAILED])

    def test_one_that_does_not_say_is_not_taken_to_be_of_every_section(self):
        """An index written before this was recorded. Its claims may be all
        of the document's or half of them."""
        _, index = self.run_tool(claim, scripted(), out="index.json")
        del index["sections"]
        with open(self.path("index.json"), "w") as handle:
            json.dump(index, handle)
        said, written = self.run_tool(claim, scripted(), "--reuse",
                                      "index.json", out="again.json")
        self.assertIn("index.json does not say how many sections its claims "
                      "were taken from", said)
        self.assertIsNone(written["sections"])

    def test_nor_is_one_whose_record_does_not_add_up(self):
        """4 sections, 3 answered, and the one not read struck out. Believed,
        it prints nothing: three of four and no section missing."""
        _, index = self.run_tool(claim, scripted(fails={HYDROLOGY}),
                                 out="index.json")
        index["sections"]["not_read"] = []
        with open(self.path("index.json"), "w") as handle:
            json.dump(index, handle)
        said, written = self.run_tool(claim, scripted(), "--reuse",
                                      "index.json", out="again.json")
        self.assertIn("index.json does not say how many sections its claims "
                      "were taken from", said)
        self.assertIsNone(written["sections"])


class WhatReadsTheFiles(Run):
    """bundle.py reads undefined.py's terms, score-claims.py reads claim.py's
    index. Each says what the file says of the sections behind it."""

    def test_the_bundle_reads_the_terms_and_what_was_not_read(self):
        _, written = self.run_tool(undefined, scripted(fails={HYDROLOGY}))
        terms, read = bundle.undefined_terms(written)
        self.assertEqual([term["term"] for term in terms],
                         ["calibration baseline"])
        self.assertEqual(read["not_read"], [FAILED])

    def test_and_a_file_from_before_it_was_an_object(self):
        """A bare list of terms is still read. It says nothing of its
        sections, and that is not taken for a document read in full."""
        older = [{"term": "calibration baseline", "uses": 4, "quote": "q",
                  "locator": "d:1", "heading": "1. Sensor Fabric"}]
        self.assertEqual(bundle.undefined_terms(older), (older, None))
        self.assertEqual(bundle.terms_note(None), bundle.UNSAID)

    def test_a_record_that_cannot_be_read_is_no_record(self):
        terms, read = bundle.undefined_terms({"terms": [], "sections": {
            "of": 4, "answered": 4}})
        self.assertEqual((terms, read), ([], None))

    def test_the_note_counts_the_sections_and_is_empty_when_all_answered(self):
        _, partly = self.run_tool(undefined, scripted(fails={HYDROLOGY}))
        self.assertIn("in 3 of the document's 4 sections",
                      bundle.terms_note(partly["sections"]))
        _, wholly = self.run_tool(undefined, scripted())
        self.assertEqual(bundle.terms_note(wholly["sections"]), "")

    def test_a_run_no_section_answered_gets_words_of_its_own(self):
        _, nothing = self.run_tool(undefined, scripted(
            fails={FABRIC, HYDROLOGY, ALERTING, ORCHESTRATOR}))
        self.assertIn("NOTHING WAS READ: undefined terms were looked for in "
                      "0 of the document's 4 sections",
                      bundle.terms_note(nothing["sections"]))

    def test_a_file_that_does_not_say_is_not_called_partial(self):
        """"NOT THE WHOLE DOCUMENT" stood over every older list of terms,
        the ones from a complete run among them. Nothing in such a file says
        a section went unread: it is not known."""
        self.assertTrue(bundle.terms_note(None).startswith(
            "NOT KNOWN TO BE THE WHOLE DOCUMENT: "))

    def test_a_file_with_no_list_of_terms_is_not_a_terms_file(self):
        """Read as a file with no term in it, an object with no "terms" gave
        a bundle that listed none, beside a record saying every section had
        answered."""
        whole = {"of": 4, "answered": 4, "runs": 1, "not_read": [],
                 "short": []}
        refused = 0
        for data in ({"doc": "d", "sections": whole}, {"terms": None},
                     {"terms": {"a": 1}}, {"terms": ["calibration baseline"]},
                     ["calibration baseline"], None, "terms", 3):
            try:
                bundle.undefined_terms(data)
            except ValueError:
                refused += 1
        self.assertEqual(refused, 8)

    def test_the_reviewers_page_carries_the_note(self):
        page = bundle.markdown([], {"undefined term": 1}, "T", "d", [
            "NOT THE WHOLE DOCUMENT: undefined terms were looked for in 3 "
            "of 4"])
        self.assertIn("- NOT THE WHOLE DOCUMENT: undefined terms were looked "
                      "for in 3 of 4", page)

    def test_and_the_sheet_carries_it_as_a_row_above_the_findings(self):
        rows = bundle.note_rows(["NOT THE WHOLE DOCUMENT: 3 of 4",
                                 "NOT KNOWN TO BE THE WHOLE DOCUMENT: a file"])
        self.assertEqual(rows, [
            ["", "about this bundle", "", "NOT THE WHOLE DOCUMENT: 3 of 4",
             "", "", "", ""],
            ["", "about this bundle", "",
             "NOT KNOWN TO BE THE WHOLE DOCUMENT: a file", "", "", "", ""]])
        self.assertEqual(len(rows[0]), len(bundle.HEADERS))

    def bundled(self, name, *flags):
        """(what bundle.py printed, its page, the text of its sheet)."""
        said = self.main(bundle, ["--doc", "d", "--undefined", name,
                                  "--out-xlsx", self.path("b.xlsx"),
                                  "--out-md", self.path("b.md"), *flags])
        with open(self.path("b.md"), encoding="utf-8") as handle:
            page = handle.read()
        with zipfile.ZipFile(self.path("b.xlsx")) as book:
            sheet = html.unescape(
                book.read("xl/worksheets/sheet1.xml").decode("utf-8"))
        return said, page, sheet

    def refused(self, name, *flags):
        """What bundle.py stopped with."""
        with self.assertRaises(SystemExit) as stopped:
            self.bundled(name, *flags)
        return str(stopped.exception.code)

    def coverage(self):
        with open(self.path("cov.csv"), "w", encoding="utf-8") as handle:
            handle.write("obligation,verdict,requirement,locator,quote,reason\n"
                         "O-1,unmet,The twin shall ingest gauges,d:3,quoted,"
                         "why not\n")
        return "--coverage", "cov.csv"

    PARTIAL = ("NOT THE WHOLE DOCUMENT: undefined terms were looked for in 3 "
               "of the document's 4 sections")
    NOTHING = ("NOTHING WAS READ: undefined terms were looked for in 0 of "
               "the document's 4 sections")

    def test_a_bundle_made_from_a_partial_run_says_so_on_the_page(self):
        self.run_tool(undefined, scripted(fails={HYDROLOGY}))
        said, page, _ = self.bundled("out.json")
        self.assertIn("calibration baseline", page)
        self.assertIn(self.PARTIAL, page)
        self.assertIn(self.PARTIAL, said)

    def test_and_in_the_sheet_above_the_terms(self):
        """Two outputs of one pass. The page said 3 of 4 sections and the
        sheet, built from the findings alone, listed the terms as the
        document's."""
        self.run_tool(undefined, scripted(fails={HYDROLOGY}))
        _, _, sheet = self.bundled("out.json")
        self.assertIn(self.PARTIAL, sheet)
        self.assertLess(sheet.find(self.PARTIAL),
                        sheet.find("calibration baseline"))

    def test_one_made_from_an_older_list_keeps_its_terms_and_says_so(self):
        with open(self.path("older.json"), "w") as handle:
            json.dump([{"term": "calibration baseline", "uses": 4,
                        "quote": "q", "locator": "d:1",
                        "heading": "1. Sensor Fabric"}], handle)
        _, page, sheet = self.bundled("older.json")
        self.assertIn("calibration baseline", page)
        self.assertIn("does not say how many sections", page)
        self.assertIn("does not say how many sections", sheet)
        self.assertNotIn("- NOT THE WHOLE DOCUMENT", page)

    def test_one_made_from_a_whole_run_says_nothing_of_it(self):
        self.run_tool(undefined, scripted())
        said, page, sheet = self.bundled("out.json")
        self.assertIn("calibration baseline", page)
        for text in (said, page, sheet):
            self.assertNotIn("WHOLE DOCUMENT", text)
            self.assertNotIn("NOTHING WAS READ", text)
        self.assertNotIn("about this bundle", sheet)

    def test_a_run_no_section_answered_is_not_a_bundle_with_no_input(self):
        """Its file holds no term, and "no inputs found" sent the reader to
        check the flags. The file was found. It says nothing was read."""
        self.run_tool(undefined, scripted(
            fails={FABRIC, HYDROLOGY, ALERTING, ORCHESTRATOR}))
        said = self.refused("out.json")
        self.assertNotIn("no inputs found", said)
        self.assertIn("nothing to bundle: no finding in out.json", said)
        self.assertIn(self.NOTHING, said)

    def test_and_beside_other_findings_the_bundle_says_so_everywhere(self):
        self.run_tool(undefined, scripted(
            fails={FABRIC, HYDROLOGY, ALERTING, ORCHESTRATOR}))
        said, page, sheet = self.bundled("out.json", *self.coverage())
        for text in (said, page, sheet):
            self.assertIn(self.NOTHING, text)
        self.assertIn("The twin shall ingest gauges", sheet)

    def test_a_bundle_given_no_file_still_says_no_input_was_found(self):
        self.assertIn("no inputs found", self.refused("nope.json"))

    def test_a_file_that_is_not_a_terms_file_stops_the_bundle(self):
        with open(self.path("index.json"), "w") as handle:
            json.dump({"doc": "d", "claims": [], "findings": [], "sections": {
                "of": 4, "answered": 4, "runs": 2, "not_read": [],
                "short": []}}, handle)
        said = self.refused("index.json", *self.coverage())
        self.assertIn("index.json: not a terms file", said)
        self.assertFalse(os.path.exists(self.path("b.md")))

    def test_nor_does_one_cut_off_mid_write(self):
        """undefined.py writes its file in place. One cut off mid-write is
        not JSON, and the bundle stops on it by name."""
        with open(self.path("cut.json"), "w") as handle:
            handle.write('{"doc": "d", "sections": {"of": 4,')
        said = self.refused("cut.json", *self.coverage())
        self.assertTrue(said.startswith("cut.json: "), said)
        self.assertFalse(os.path.exists(self.path("b.md")))

    TRUTH = ("defects:\n"
             "  - id: GT-D5-001\n"
             "    class: D5\n"
             "    expect: defect\n"
             "    title: Two owners of the calibration baseline\n"
             "    lines: [1, 10]\n"
             "  - id: GT-D5-002\n"
             "    class: D5\n"
             "    expect: defect\n"
             "    title: Two freezes of the calibration baseline\n"
             "    lines: [16, 19]\n"
             "decoys: []\n")

    def scored(self, name, truth="defects: []\ndecoys: []\n"):
        with open(self.path("ground-truth.yaml"), "w") as handle:
            handle.write(truth)
        return self.main(SCORE, ["--claims", name])

    def rewritten(self, change, name="changed.json"):
        """An index from a run in which every section answered and every
        pair was judged, with `change` made to it."""
        _, index = self.run_tool(claim, scripted())
        change(index)
        with open(self.path(name), "w") as handle:
            json.dump(index, handle)
        return name

    def test_a_score_of_an_index_says_how_many_sections_the_index_is_of(self):
        self.run_tool(claim, scripted(fails={HYDROLOGY}))
        self.assertIn("the index was answered on 3 of 4 sections",
                      self.scored("out.json"))

    def test_and_says_so_of_an_index_that_does_not_record_it(self):
        name = self.rewritten(lambda index: index.pop("sections"))
        self.assertIn("the index does not say how many sections its claims "
                      "were taken", self.scored(name))

    def test_and_of_one_whose_record_does_not_add_up(self):
        def strike(index):
            index["sections"]["answered"] = 3
        self.assertIn("the index does not say how many sections its claims "
                      "were taken", self.scored(self.rewritten(strike)))

    def test_and_of_one_no_section_answered(self):
        self.run_tool(claim, scripted(
            fails={FABRIC, HYDROLOGY, ALERTING, ORCHESTRATOR}))
        self.assertIn("NOTHING WAS READ: the index was answered on 0 of 4 "
                      "sections", self.scored("out.json"))

    def test_and_of_a_section_a_pass_failed_on(self):
        """Every section answered, one of them in one pass of two. A claim
        only the other pass would have returned is not in the index."""
        self.run_tool(claim, scripted(fails={HYDROLOGY}, only_in_run=1))
        said = self.scored("out.json")
        self.assertIn("1 section(s) of the index answered in fewer than the "
                      "2 passes asked for", said)
        self.assertNotIn("the index was answered on", said)

    def test_and_of_the_pairs_nobody_judged(self):
        """Six candidate pairs and every judging call failed. The index said
        so and the score did not: "recall 0/1" and MISSED, over the pair
        that is the defect and was never judged."""
        self.run_tool(claim, scripted(judge=None))
        said = self.scored("out.json", self.TRUTH)
        self.assertIn("6 candidate pair(s) could not be judged", said)
        self.assertLess(said.find("could not be judged"), said.find("recall"))

    def test_a_missed_defect_that_an_unjudged_pair_matches_says_so(self):
        """The Fabric and the Model, lines 1 and 6, are a pair nobody
        judged. No pair lies inside lines 16-19, so that defect is a miss
        with nothing to add."""
        self.run_tool(claim, scripted(judge=None))
        said = self.scored("out.json", self.TRUTH)
        self.assertIn("MISSED   GT-D5-001  Two owners of the calibration "
                      "baseline\n         a candidate pair that matches it "
                      "was never judged", said)
        self.assertIn("MISSED   GT-D5-002", said)
        self.assertEqual(said.count("was never judged"), 1)

    def test_an_index_that_does_not_say_which_pairs_were_judged(self):
        name = self.rewritten(lambda index: index.pop("unjudged"))
        self.assertIn("the index does not say whether every candidate pair "
                      "was judged", self.scored(name))

    def test_nor_one_whose_record_of_them_cannot_be_read(self):
        """A pair is two claims, each with the words it quotes and the line
        it starts on: what a pair is matched to a defect by. Anything short
        of that is no record, each way of falling short on its own."""
        whole = {"quote": "q", "start": 1}
        unsaid = 0
        for garbled in ("6", ["a pair"], [{"a": whole}],
                        [{"a": whole, "b": None}],
                        [{"a": whole, "b": {"start": 6}}],
                        [{"a": {"quote": "q"}, "b": whole}],
                        [{"a": whole, "b": {"quote": "q", "start": "6"}}],
                        [{"a": whole, "b": {"quote": 6, "start": 6}}]):
            name = self.rewritten(
                lambda index: index.update(unjudged=garbled))
            unsaid += "the index does not say whether every candidate " \
                "pair was judged" in self.scored(name)
        self.assertEqual(unsaid, 8)

    def test_and_nothing_of_one_answered_on_every_section(self):
        self.run_tool(claim, scripted())
        said = self.scored("out.json", self.TRUTH)
        self.assertNotIn("a MISSED below", said)
        self.assertNotIn("does not say", said)
        self.assertNotIn("never judged", said)
        self.assertIn("recall", said)


if __name__ == "__main__":
    unittest.main()
