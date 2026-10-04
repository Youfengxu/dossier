"""synthesize.py: what it may say about a document it did not read all of.

Three of its four finding classes assert an ABSENCE: nothing owns this, nothing
consumes that, no section holds what this sentence says is recorded there. Its
docstring gives the licence: the inventory was built by reading 100% of the
document. It then opened the inventory with

    sections = [s for s in data["sections"] if s and "error" not in s]

and never looked at what that dropped. On a sound three-section document:

    section 3 failed at extraction    [D3] Section 3 — no such section in the
                                      document
    section 2 failed at extraction    [D6] an ownership gap and [D8] an orphan,
                                      both answered inside section 2
    section 2 read by the fallback    [D8] the same orphan: the fallback schema
                                      never asks what a section consumes

Each finding is false, each run exited 0, and the only trace was a count one
lower than it should have been. An unread section does not merely go
unreported. It is reported, as a defect in the document.

The inventory below is that document. Every test runs main() on it in process,
so run-tests.py --mutate reaches what is tested here.
"""

import contextlib
import copy
import csv
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import synthesize
from llm import LLMError

FABRIC = {
    "heading": "1. Sensor Fabric", "locator": "d:1-20",
    "capabilities": [{"name": "ingest gauge readings", "quote": "q"}],
    "authority": [],
    "defers_to": [{"capability": "threshold adjudication",
                   "to": "Hydrology Model"}],
    "produces": ["calibrated gauge readings"], "consumes": [],
    "evidence_claims": [{"claim": "Calibration drift limits are recorded",
                         "points_to": "Section 3", "quote": "q"}],
    "identifiers": [], "deferred": [], "passes": 3, "full_passes": 3}
HYDROLOGY = {
    "heading": "2. Hydrology Model", "locator": "d:21-40",
    "capabilities": [{"name": "forecast river stage", "quote": "q"}],
    "authority": [{"capability": "threshold adjudication", "action": "decides",
                   "owner": "Hydrology Model", "polarity": "owns",
                   "quote": "q"}],
    "defers_to": [], "produces": [],
    "consumes": ["calibrated gauge readings"],
    "evidence_claims": [], "identifiers": [], "deferred": [],
    "passes": 3, "full_passes": 3}
REGISTER = {
    "heading": "3. Calibration Register", "locator": "d:41-60",
    "capabilities": [{"name": "calibration drift limits", "quote": "q"}],
    "authority": [], "defers_to": [], "produces": [], "consumes": [],
    "evidence_claims": [], "identifiers": [], "deferred": [],
    "passes": 3, "full_passes": 3}


def failed(section):
    """The section as inventory.py records it when no pass answered."""
    return {"error": "no schema-valid reply after 2 attempts",
            "heading": section["heading"], "locator": section["locator"]}


def fallback(section, full_passes=0):
    """The section as the three-field schema leaves it. With a later full pass
    the other five fields are there after all."""
    entry = copy.deepcopy(section)
    entry.update(degraded=True, passes=1 + full_passes, full_passes=full_passes)
    if not full_passes:
        for key in ("identifiers", "deferred", "evidence_claims", "consumes",
                    "produces"):
            entry[key] = []
    return entry


class Run(unittest.TestCase):
    """main() over an inventory written to a scratch project, beside the frozen
    document it was built from."""

    def setUp(self):
        self.project = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.project, ignore_errors=True)

    def freeze(self, sections):
        """Write the document these sections were split from, the way freeze.py
        leaves one: each heading on the first line its section's locator
        names. Returns its hash. Which sections a document has is the
        document's to say (tests/test_pointers.py), so D3 calls a section
        missing only with this beside the inventory."""
        lines = []
        for entry in sections:
            entry = entry or {}
            found = re.search(r":(\d+)-(\d+)$", str(entry.get("locator", "")))
            if found and entry.get("heading") is not None:
                lines += ["Body text."] * (int(found.group(2)) - len(lines))
                lines[int(found.group(1)) - 1] = "## " + entry["heading"]
        text = ("\n".join(lines) + "\n").encode("utf-8")
        digest = hashlib.sha256(text).hexdigest()
        os.makedirs(os.path.join(self.project, "parsed"), exist_ok=True)
        with open(os.path.join(self.project, "parsed", "d.txt"), "wb") as handle:
            handle.write(text)
        with open(os.path.join(self.project, "parsed", "MANIFEST.json"),
                  "w") as handle:
            json.dump({"documents": [{"slug": "d", "role": "draft",
                                      "path": "d.md",
                                      "parsed": "parsed/d.txt",
                                      "source_sha256": digest,
                                      "text_sha256": digest}]}, handle)
        return digest

    def run_on(self, sections, *extra, runs=3):
        with open(os.path.join(self.project, "inventory.json"), "w") as handle:
            json.dump({"doc": "d", "runs": runs, "sections": sections,
                       "source_sha256": self.freeze(sections)}, handle)
        out, err, argv = io.StringIO(), io.StringIO(), sys.argv
        sys.argv = ["synthesize.py", "--project", self.project, "--no-embed",
                    *extra]
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.status = synthesize.main()
        finally:
            sys.argv = argv
        self.stderr = err.getvalue()
        return out.getvalue()

    def findings(self, report):
        """{class: the lines printed under it}, for each finding listed."""
        found, current = {}, None
        for line in report.splitlines():
            if line.startswith("[D"):
                current = found.setdefault(line[1:3], [])
            if current is not None and line.strip():
                current.append(line.strip())
        return found


class TheWholeDocument(Run):
    """Read in full, this document has no defect of any class."""

    def test_nothing_is_found_and_nothing_is_in_doubt(self):
        report = self.run_on([FABRIC, HYDROLOGY, REGISTER])
        self.assertEqual(self.findings(report), {})
        self.assertNotIn("unverifiable", report.lower())

    def test_the_inventory_line_says_all_of_it_was_read(self):
        report = self.run_on([FABRIC, HYDROLOGY, REGISTER])
        self.assertIn("inventory: 3 of 3 sections read", report)
        self.assertNotIn("NOT READ", report)


class WhatTheInventoryLineSays(Run):

    def test_a_section_that_failed_is_counted_and_named(self):
        report = self.run_on([FABRIC, HYDROLOGY, failed(REGISTER)])
        self.assertIn("inventory: 2 of 3 sections read", report)
        self.assertIn("NOT READ", report)
        self.assertIn("3. Calibration Register (d:41-60)", report)

    def test_one_the_inventory_cannot_name_is_given_by_its_position(self):
        """An inventory written before a failed section kept its heading stores
        {"error": ...} and nothing else. That is still a section, and it still
        was not read."""
        report = self.run_on([FABRIC, HYDROLOGY, {"error": "x"}])
        self.assertIn("inventory: 2 of 3 sections read", report)
        self.assertIn("section 3 of 3", report)

    def test_an_entry_that_is_empty_was_not_read_either(self):
        report = self.run_on([FABRIC, HYDROLOGY, None])
        self.assertIn("inventory: 2 of 3 sections read", report)

    def test_a_section_read_by_the_fallback_alone_is_named_as_read_in_part(self):
        report = self.run_on([FABRIC, fallback(HYDROLOGY), REGISTER])
        self.assertIn("inventory: 3 of 3 sections read", report)
        self.assertIn("READ IN PART", report)
        self.assertIn("2. Hydrology Model (d:21-40)", report)

    def test_one_the_fallback_started_and_a_full_pass_finished_is_not(self):
        report = self.run_on([FABRIC, fallback(HYDROLOGY, full_passes=1),
                              REGISTER])
        self.assertNotIn("READ IN PART", report)

    def test_sections_read_fewer_times_than_asked_are_counted_and_named(self):
        once = dict(HYDROLOGY, passes=1, full_passes=1)
        report = self.run_on([FABRIC, once, REGISTER])
        head = report.split("-- candidate defects")[0]
        self.assertIn("READ IN FEWER than the 3 passes", head)
        self.assertIn("2. Hydrology Model (d:21-40)", head)

    def test_a_section_the_run_was_told_to_stop_before_was_not_read(self):
        """`inventory.py --limit 2` wrote two entries and nothing to say there
        had been a third. "2 of 2 sections read", then "Section 3 — no such
        section in the document"."""
        beyond = {"error": "not read: --limit 2 stopped before this section",
                  "skipped": True, "heading": "3. Calibration Register",
                  "locator": "d:41-60"}
        report = self.run_on([FABRIC, HYDROLOGY, beyond])
        self.assertIn("inventory: 2 of 3 sections read", report)
        self.assertIn("--limit 2", report)
        self.assertNotIn("no such section in the document", report)

    def test_a_long_list_is_cut_and_its_count_is_not(self):
        """A quarter of a real document has failed at extraction before. The
        count has to be exact; seventy names would bury what they qualify."""
        unread = [{"error": "x", "heading": f"{n}. Section", "locator": f"d:{n}"}
                  for n in range(4, 19)]
        head = self.run_on([FABRIC, HYDROLOGY, REGISTER] + unread).split(
            "-- candidate defects")[0]
        self.assertIn("inventory: 3 of 18 sections read", head)
        self.assertIn("NOT READ: 15 section(s)", head)
        self.assertIn("15. Section", head)
        self.assertNotIn("16. Section", head)
        self.assertIn("and 3 more", head)

    def test_an_old_inventory_is_read_in_part_only_where_it_shows_no_full_pass(self):
        """An entry written before `full_passes` was recorded does not say how
        many full passes it had. One the fallback started is in doubt if the
        five fields the fallback never asks for are all empty, and not if any
        of them holds something: a full pass put it there."""
        def old(section, **changes):
            entry = {k: v for k, v in section.items()
                     if k not in ("passes", "full_passes")}
            return dict(entry, degraded=True, **changes)
        merged = self.run_on([FABRIC, old(HYDROLOGY), REGISTER])
        self.assertNotIn("READ IN PART", merged)
        alone = self.run_on([FABRIC, old(HYDROLOGY, consumes=[]), REGISTER])
        self.assertIn("READ IN PART", alone)

    def test_a_section_with_no_heading_is_named_by_its_lines(self):
        """split_sections gives a bare "#" line the heading "". Naming such a
        section, read in part, raised KeyError."""
        report = self.run_on([FABRIC, dict(fallback(HYDROLOGY), heading=""),
                              REGISTER])
        self.assertIn("READ IN PART", report)
        self.assertIn("d:21-40", report.split("-- candidate defects")[0])


class AnAbsenceAnUnreadSectionCouldAnswer(Run):
    """The finding is still listed. It says that it rests on sections that were
    not read, and which."""

    def test_a_pointer_to_an_unread_section_is_not_called_a_missing_section(self):
        report = self.run_on([FABRIC, HYDROLOGY, failed(REGISTER)])
        self.assertNotIn("no such section in the document", report)
        (finding,) = self.findings(report).values()
        self.assertIn("unverifiable", " ".join(finding))
        self.assertIn("3. Calibration Register (d:41-60)", " ".join(finding))

    def test_nor_when_the_inventory_cannot_say_which_section_went_unread(self):
        report = self.run_on([FABRIC, HYDROLOGY, {"error": "x"}])
        self.assertNotIn("no such section in the document", report)
        self.assertIn("unverifiable", " ".join(self.findings(report)["D3"]))

    def test_no_pointer_is_cleared_by_what_an_unread_heading_says(self):
        """The first version of this marking compared the pointer with the
        heading of each unread section and let the finding stand when they did
        not match. A review broke it seven ways, and they have one cause: the
        inventory keeps the FIRST heading of each chunk, and a chunk also holds
        whatever headings followed too soon to start a chunk of their own
        (tests/test_chunking.py pins that). "3. Calibration Register" may hold
        3.1, a table can sit anywhere, and a heading may be spelled "Section 3:
        ...". With a section unread, no D3 absence is asserted at all."""
        pointers = ("Section 3.1",                    # inside the unread section
                    "Section 9",                      # nothing like the heading
                    "Section 9 and Appendix F")
        asserted = {}
        for pointer in pointers:
            claim = dict(FABRIC, evidence_claims=[
                {"claim": "Calibration drift limits are recorded",
                 "points_to": pointer, "quote": "q"}])
            report = self.run_on([claim, HYDROLOGY, failed(REGISTER)])
            d3 = " ".join(self.findings(report).get("D3", ["(no D3)"]))
            if "no such section in the document" in d3 or "unverifiable" not in d3:
                asserted[pointer] = d3
        self.assertEqual(asserted, {})

    def test_and_a_pointer_that_names_no_section_is_not_a_finding_at_all(self):
        """A table can sit in any section and a section may be named by its
        title alone. Neither is looked up, read or unread: it is counted among
        the claims D3 did not check (tests/test_pointers.py)."""
        listed = {}
        for pointer in ("Table 4", "the calibration register section",
                        "the Calibration Register section, page 12"):
            claim = dict(FABRIC, evidence_claims=[
                {"claim": "Calibration drift limits are recorded",
                 "points_to": pointer, "quote": "q"}])
            report = self.run_on([claim, HYDROLOGY, failed(REGISTER)])
            if "D3" in self.findings(report) or "0 of 1 evidence claims" not in report:
                listed[pointer] = report
        self.assertEqual(listed, {})

    def test_nor_by_how_the_unread_heading_spells_its_number(self):
        asserted = {}
        for heading in ("Section 3: Calibration Register",
                        "Annex 3 Calibration Register", "(front matter)"):
            unread = dict(failed(REGISTER), heading=heading)
            report = self.run_on([FABRIC, HYDROLOGY, unread])
            d3 = " ".join(self.findings(report).get("D3", ["(no D3)"]))
            if "no such section in the document" in d3 or "unverifiable" not in d3:
                asserted[heading] = d3
        self.assertEqual(asserted, {})

    def test_nor_is_a_claim_checked_against_only_the_part_that_was_read(self):
        """The section the pointer names was read and does not hold the claim.
        A section can run over several chunks, and one of them went unread."""
        claim = dict(FABRIC, evidence_claims=[
            {"claim": "Calibration drift limits are recorded",
             "points_to": "Section 2", "quote": "q"}])
        report = self.run_on([claim, HYDROLOGY, failed(REGISTER)])
        d3 = " ".join(self.findings(report)["D3"])
        self.assertIn("-> Section 2", d3)
        self.assertIn("unverifiable", d3)

    def test_with_every_section_read_a_missing_section_is_a_finding(self):
        """The other half, and the reason the marking is worth reading: when
        nothing went unread the absence is the inventory's to assert."""
        claim = dict(FABRIC, evidence_claims=[
            {"claim": "Calibration drift limits are recorded",
             "points_to": "Section 9", "quote": "q"}])
        report = self.run_on([claim, HYDROLOGY, REGISTER])
        d3 = " ".join(self.findings(report)["D3"])
        self.assertIn("no such section in the document", d3)
        self.assertNotIn("unverifiable", report)

    def test_and_so_is_a_claim_the_section_it_names_does_not_hold(self):
        claim = dict(FABRIC, evidence_claims=[
            {"claim": "Calibration drift limits are recorded",
             "points_to": "Section 2", "quote": "q"}])
        report = self.run_on([claim, HYDROLOGY, REGISTER])
        d3 = " ".join(self.findings(report)["D3"])
        self.assertIn("-> Section 2", d3)
        self.assertNotIn("unverifiable", report)

    def test_a_section_read_in_part_does_not_put_a_pointer_in_doubt(self):
        """The fallback keeps the heading and asks for capabilities, which is
        all a pointer is checked against."""
        claim = dict(FABRIC, evidence_claims=[
            {"claim": "Calibration drift limits are recorded",
             "points_to": "Section 9", "quote": "q"}])
        report = self.run_on([claim, HYDROLOGY, fallback(REGISTER)])
        d3 = " ".join(self.findings(report)["D3"])
        self.assertIn("no such section in the document", d3)
        self.assertNotIn("unverifiable", d3)

    def test_an_ownership_gap_the_unread_section_may_close(self):
        report = self.run_on([FABRIC, failed(HYDROLOGY), REGISTER])
        d6 = " ".join(self.findings(report)["D6"])
        self.assertIn("unverifiable", d6)
        self.assertIn("2. Hydrology Model (d:21-40)", d6)

    def test_an_orphan_the_unread_section_may_consume(self):
        report = self.run_on([FABRIC, failed(HYDROLOGY), REGISTER])
        self.assertIn("unverifiable", " ".join(self.findings(report)["D8"]))

    def test_an_orphan_when_the_consumer_was_read_without_its_consumes(self):
        report = self.run_on([FABRIC, fallback(HYDROLOGY), REGISTER])
        d8 = " ".join(self.findings(report)["D8"])
        self.assertIn("unverifiable", d8)
        self.assertIn("2. Hydrology Model (d:21-40)", d8)

    def test_the_fallback_does_not_put_an_ownership_finding_in_doubt(self):
        """It asks for authority, so what it says about ownership is a reading.
        Only the fields it never asks for are in doubt."""
        gap = dict(HYDROLOGY, authority=[])
        report = self.run_on([FABRIC, fallback(gap), REGISTER])
        self.assertNotIn("unverifiable", " ".join(self.findings(report)["D6"]))

    def test_the_summary_counts_them_apart(self):
        report = self.run_on([FABRIC, failed(HYDROLOGY), REGISTER])
        self.assertIn("unverifiable: 2 of these 2.", report)

    def test_the_candidate_file_carries_the_verdict(self):
        """`unverifiable` is the coverage scale's own word for evidence that
        could not be reached, and score-register.py counts it as flagged. A
        dual binding is two owners FOUND, so the unread section leaves it as
        it was."""
        def owning(section, owner):
            return dict(section, authority=[
                {"capability": "gauge polling", "action": "owns",
                 "owner": owner, "polarity": "owns", "quote": "q"}])
        self.run_on([owning(FABRIC, "Sensor Fabric"), failed(HYDROLOGY),
                     owning(REGISTER, "Calibration Register")],
                    "--out", "candidates.csv")
        with open(os.path.join(self.project, "candidates.csv")) as handle:
            rows = {row["source_ref"]: row for row in csv.DictReader(handle)}
        self.assertEqual({k: v["verdict"] for k, v in rows.items()},
                         {"D5": "unmet", "D6": "unverifiable",
                          "D8": "unverifiable"})
        self.assertIn("2. Hydrology Model", rows["D8"]["reason"])

    def test_under_the_dataflow_flag_a_section_read_in_part_may_be_the_owner(self):
        """--authority-as-dataflow takes ownership from produces and consumes,
        which the fallback never asks for. Under it a section read in part can
        be hiding the owner as well as the consumer."""
        owner = dict(HYDROLOGY, authority=[],
                     produces=["threshold adjudication"])
        report = self.run_on([FABRIC, fallback(owner), REGISTER],
                             "--authority-as-dataflow")
        d6 = " ".join(self.findings(report)["D6"])
        self.assertIn("unverifiable", d6)
        self.assertIn("2. Hydrology Model (d:21-40)", d6)


class WhatTheFallbackMayHaveWritten(Run):
    """The fallback's validator accepts shapes the full one does not, and a
    section read that way reaches synthesize.py like any other."""

    def test_capabilities_given_as_plain_strings(self):
        """minimal_validate asks only that `capabilities` be a list. A string in
        it raised AttributeError where a pointer is checked against it."""
        plain = dict(fallback(REGISTER),
                     capabilities=["calibration drift limits"])
        report = self.run_on([FABRIC, HYDROLOGY, plain])
        self.assertEqual(self.findings(report), {})

    def test_a_pointer_given_as_a_list_of_places(self):
        """validate does not say `points_to` must be a string, and `.strip()`
        on a list raised AttributeError. Each place is still a place."""
        claim = dict(FABRIC, evidence_claims=[
            {"claim": "Calibration drift limits are recorded",
             "points_to": ["Section 3", "Appendix B"], "quote": "q"}])
        self.assertEqual(self.findings(self.run_on([claim, HYDROLOGY,
                                                    REGISTER])), {})
        nowhere = dict(FABRIC, evidence_claims=[
            {"claim": "Calibration drift limits are recorded",
             "points_to": ["Section 9", "Appendix Q"], "quote": "q"}])
        mapping = dict(REGISTER, heading="Appendix A — Requirement mapping",
                       locator="d:61-80")
        report = self.run_on([nowhere, HYDROLOGY, REGISTER, mapping])
        self.assertIn("[D3] Section 9 and Appendix Q — no such section",
                      report)


class APairThatCouldNotBeJudged(unittest.TestCase):
    """adjudicate_pairs asks a model about each pair. A call that fails has not
    said "no conflict", and it used to be returned as exactly that."""

    ENTRIES = [
        {"capability": "run scheduling", "owner": "Orchestrator", "_locator": "d:1"},
        {"capability": "run scheduling", "owner": "Runtime", "_locator": "d:2"},
        {"capability": "run scheduling", "owner": "Platform", "_locator": "d:3"},
    ]

    class Client:
        def __init__(self, *replies):
            self.replies = list(replies)

        def ask(self, system, user, validate=None, label=""):
            reply = self.replies.pop(0)
            if isinstance(reply, dict):
                return reply
            raise LLMError(reply)

    CLEAR = {"same_slot": True, "conflict": False, "reason": "r"}
    CONFLICT = {"same_slot": True, "conflict": True, "reason": "r"}

    def test_a_failed_call_is_counted_as_failed(self):
        client = self.Client(self.CLEAR, "endpoint call failed", self.CONFLICT)
        attempted, unjudged, conflicts = synthesize.adjudicate_pairs(
            client, self.ENTRIES, "capability")
        self.assertEqual((attempted, unjudged, len(conflicts)), (3, 1, 1))

    def test_and_a_run_where_every_call_failed_has_judged_nothing(self):
        client = self.Client("x", "x", "x")
        attempted, unjudged, conflicts = synthesize.adjudicate_pairs(
            client, self.ENTRIES, "capability")
        self.assertEqual((attempted, unjudged, conflicts), (3, 3, []))


class TheAdjudicationLine(Run):

    def test_it_says_how_many_pairs_were_judged_not_how_many_were_tried(self):
        owners = [dict(HYDROLOGY, authority=[
                      {"capability": "threshold adjudication",
                       "action": "decides", "owner": "Hydrology Model",
                       "polarity": "owns", "quote": "q"}]),
                  dict(REGISTER, authority=[
                      {"capability": "threshold adjudication",
                       "action": "decides", "owner": "Alerting Service",
                       "polarity": "owns", "quote": "q"}])]

        class Refusing:
            def __init__(self, *args, **kwargs):
                pass

            def ask(self, *args, **kwargs):
                raise LLMError("endpoint call failed")

            def summary(self):
                return "1 calls, 0 cached, 0 retries, 1 failed"

        with mock.patch.object(synthesize, "Client", Refusing):
            report = self.run_on([FABRIC] + owners, "--adjudicate")
        self.assertIn("adjudicated 0 of 1 authority pairs", report)
        self.assertIn("1 could not be judged", report)


class WithoutPyYAML(Run):
    """PyYAML is a declared dependency, and a bare clone may not have it."""

    def test_a_score_that_cannot_be_computed_is_not_a_clean_exit(self):
        """`--ground-truth` returned 0 without printing a score."""
        with open(os.path.join(self.project, "ground-truth.yaml"), "w") as handle:
            handle.write("defects: []\n")
        with mock.patch.dict(sys.modules, {"yaml": None}):
            self.run_on([FABRIC, HYDROLOGY, REGISTER],
                        "--ground-truth", "ground-truth.yaml")
        self.assertNotEqual(self.status, 0)
        self.assertIn("PyYAML", self.stderr)

    def test_a_components_file_that_was_not_read_is_said_not_to_have_been(self):
        with open(os.path.join(self.project, "components.yaml"), "w") as handle:
            handle.write("components: {Hydrology Model: [HM]}\n")
        with mock.patch.dict(sys.modules, {"yaml": None}):
            self.run_on([FABRIC, HYDROLOGY, REGISTER])
        self.assertIn("components.yaml", self.stderr)
        self.assertIn("PyYAML", self.stderr)

    def test_a_project_with_no_components_file_is_told_nothing(self):
        with mock.patch.dict(sys.modules, {"yaml": None}):
            self.run_on([FABRIC, HYDROLOGY, REGISTER])
        self.assertEqual(self.stderr, "")


if __name__ == "__main__":
    unittest.main()
