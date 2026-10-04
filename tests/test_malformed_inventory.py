"""synthesize.py: an inventory that is not as inventory.py writes one.

It took the shape of its input on trust. With one odd value in an otherwise
sound inventory:

    a section that is 5              raised: every finding of every class lost
    "heading": -1                    raised
    "capabilities": null             read as a section that provides nothing:
                                     [D3] the claim that it holds something,
                                     asserted as untrue
    "consumes": {}                   read as a section that consumes nothing:
                                     [D8] an orphan, asserted
    "consumes": [{"name": ...}]      the same orphan, asserted
    a file that is not JSON          a traceback

The last test here puts 14 odd values, one at a time, in each of 147 places
of a sound inventory, and takes each key out in turn: 2,109 inventories that
differ from the sound one. By this file's own statement of the shape, 1,255 of
them are still sound (a text replaced by another text, a key no list is asked
for). The other 854 are not: 27 are no inventory at all, 605 have an entry
that is not a section, and 222 an item that cannot be read. The code as it was
raised on 239 of the 2,109, five of them sound ones whose `runs` was not a
number, and reported on 562 of the 854 with no word of anything unread. Those
are the worse ones: a run that exits 0 over a section it could not read
reports that section's silence as the document's. against(), at the foot of
this file, prints these figures for any synthesize.py it is given.

What it does now is decided at three levels, each in one place.

    the file     synthesize.load_inventory(). A file that is not an inventory
                 is refused: exit status 1, one line, no report.
    an entry     synthesize.unreadable(). What an entry says of itself,
                 inventory.py writes with no model in between. One that is
                 otherwise is a section that was NOT READ, with the reason,
                 and every absence it could answer is a question.
    an item      synthesize.read_items(). What a list holds is a model's
                 reply, and no validator says what most of it must be. An
                 item that cannot be read is set aside: its section is still
                 read, the item is counted and named, and an absence that it
                 could answer is a question.

A run in which no section at all was read says so and exits 1.

No model. main() runs in process on an inventory of three sound sections with
something changed in it, beside the document those sections were split from.
"""

import contextlib
import copy
import csv
import errno
import io
import json
import os
import sys
import types
import unittest
from unittest import mock

import dossier_cli
import inventory
import synthesize
from llm import LLMError
from tests.test_unread_sections import FABRIC, HYDROLOGY, REGISTER, Run

SOUND = [FABRIC, HYDROLOGY, REGISTER]
NO_FILE, GONE = object(), object()
EXTRACTED = "5" * 64            # the hash of a source that is not its text
LISTS = ("capabilities", "evidence_claims", "authority", "defers_to",
         "consumes", "produces")
OWNS = HYDROLOGY["authority"][0]
CLAIM = FABRIC["evidence_claims"][0]
PASSED_ON = FABRIC["defers_to"][0]


def sections(index=None, **changed):
    """The three sound sections, one of them with fields replaced. GONE takes
    a field out."""
    out = copy.deepcopy(SOUND)
    for key, value in changed.items():
        if value is GONE:
            del out[index][key]
        else:
            out[index][key] = value
    return out


class Agreeable:
    """A model that calls every pair it is shown a conflict, in place of the
    client --adjudicate would make."""

    def __init__(self, *args, **kwargs):
        pass

    def ask(self, system, user, validate=None, label=""):
        return {"same_slot": True, "conflict": True, "reason": "r"}

    def summary(self):
        return "calls"


class Odd(Run):
    """main() over an inventory with something odd in it."""

    def freeze(self, sections=None):
        # The document is the one the sound sections were split from, whatever
        # the inventory now says of it.
        return super().freeze(SOUND)

    def run_main(self, inventory, *extra, extracted=False, encoded=False,
                 endpoints=False):
        """`inventory` is what inventory.json holds: text and bytes are
        written as they are, NO_FILE writes nothing, anything else is written
        as JSON. A raise is a failure of the test: it is what this file is
        about.

        `endpoints` runs with the embedder and the model client the
        repository has, under DOSSIER_FAKE_CHAT, its own stand-in for the two
        endpoints: nothing is sent anywhere, and every text is hashed and
        encoded as it would be. Without it there is no embedding, and
        --adjudicate is answered by Agreeable.

        `encoded` prints the report to streams that encode what they are
        given, as a terminal or a pipe does. The default ones here take any
        text at all.

        `extracted` makes the document one whose text is not its source, as
        a .docx is. An inventory with the source's hash and no other is then
        held to its line numbers, entry by entry, before the document is
        consulted."""
        self.freeze()
        if extracted:
            manifest = os.path.join(self.project, "parsed", "MANIFEST.json")
            with open(manifest, encoding="utf-8") as handle:
                held = json.load(handle)
            held["documents"][0].update(source_sha256=EXTRACTED, path="d.docx")
            with open(manifest, "w", encoding="utf-8") as handle:
                json.dump(held, handle)
        path = os.path.join(self.project, "inventory.json")
        if isinstance(inventory, bytes):
            with open(path, "wb") as handle:
                handle.write(inventory)
        elif inventory is not NO_FILE:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(inventory if isinstance(inventory, str)
                             else json.dumps(inventory))
        if encoded:
            out, err = (io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
                        for _ in range(2))
        else:
            out, err = io.StringIO(), io.StringIO()
        argv = sys.argv
        sys.argv = ["synthesize.py", "--project", self.project,
                    *(() if endpoints else ("--no-embed",)), *extra]
        stand_in = mock.patch.dict(os.environ, {"DOSSIER_FAKE_CHAT": "1"}) \
            if endpoints else mock.patch.object(synthesize, "Client", Agreeable)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                    stand_in:
                self.status = synthesize.main()
            if encoded:
                out, err = (io.StringIO(stream.detach().getvalue().decode("utf-8"))
                            for stream in (out, err))
        except Exception as error:      # noqa: BLE001: a raise loses every finding
            self.fail(f"main() raised {type(error).__name__}: {error}")
        finally:
            sys.argv = argv
        self.stderr = err.getvalue()
        return out.getvalue()

    def read(self, entries, *extra, **top):
        """The report on an inventory of these entries. `top` replaces what
        the file says of itself."""
        return self.run_main({"doc": "d", "runs": 3, "sections": entries,
                              "source_sha256": self.freeze(), **top}, *extra)

    def scored(self, entries, defects, *extra):
        """The report, with a score against these planted defects."""
        with open(os.path.join(self.project, "ground-truth.yaml"), "w") as handle:
            handle.write("defects: []\n")
        fake = types.SimpleNamespace(
            safe_load=lambda handle: {"defects": defects})
        with mock.patch.dict(sys.modules, {"yaml": fake}):
            return self.read(entries, "--ground-truth", "ground-truth.yaml",
                             *extra)

    def not_read(self, report, why, of=3):
        """One section fewer was read, and the report says which and why."""
        self.assertEqual(self.status, 0)
        self.assertIn(f"inventory: {of - 1} of {of} sections read", report)
        self.assertIn("NOT READ: 1 section(s)", report)
        self.assertIn(why, report)

    def all_read(self, report):
        """Every section, and every item of each."""
        self.assertEqual(self.status, 0)
        self.assertIn("inventory: 3 of 3 sections read", report)
        self.assertNotIn("NOT READ", report)

    def set_aside(self, report, *whys, items=1, holders=1):
        """Every section was read, and the report says how many items of
        theirs were not, in how many sections, and why."""
        self.assertEqual(self.status, 0)
        self.assertIn("inventory: 3 of 3 sections read", report)
        self.assertNotIn(" NOT READ: ", report.replace("ITEMS NOT READ", ""))
        self.assertIn(f"ITEMS NOT READ: {items}, in {holders} section(s).",
                      report)
        for why in whys:
            self.assertIn(why, report)

    def candidates(self, kind):
        """What the candidate file quotes for each finding of one class."""
        with open(os.path.join(self.project, "candidates.csv"),
                  encoding="utf-8", newline="") as handle:
            return [row[5] for row in csv.reader(handle) if row[1] == kind]

    def again(self):
        self.tearDown()
        self.setUp()


class AFileThatIsNotAnInventory(Odd):
    """Refused. A report with no findings in it, and exit status 0, would say
    that the document has no defects."""

    def refused(self, inventory, *extra):
        report = self.run_main(inventory, *extra)
        self.assertEqual(self.status, 1)
        self.assertEqual(report, "")
        self.assertTrue(self.stderr.startswith("NOT RUN: inventory.json "))
        self.assertEqual(self.stderr.count("\n"), 1)
        return self.stderr

    def test_a_file_that_is_not_there(self):
        self.assertEqual(
            self.refused(NO_FILE), "NOT RUN: inventory.json could not be read: "
            f"{os.strerror(errno.ENOENT)}.\n")

    def test_a_file_the_system_will_not_open_and_gives_no_reason_for(self):
        with mock.patch.object(synthesize, "open", create=True,
                               side_effect=OSError("the disk went away")):
            self.assertEqual(
                self.refused({"doc": "d", "sections": SOUND}),
                "NOT RUN: inventory.json could not be read: the disk went "
                "away.\n")

    def test_text_that_is_not_json(self):
        self.assertIn("NOT RUN: inventory.json is not JSON: ",
                      self.refused('{"doc": "d", "sections": ['))

    def test_bytes_that_are_not_text(self):
        self.assertIn("NOT RUN: inventory.json is not JSON: ",
                      self.refused(b'\xff\xfe{"doc": "d", "sections": []}'))

    def test_the_refusal_is_one_line_whatever_the_parser_says(self):
        with mock.patch.object(synthesize.json, "load",
                               side_effect=json.JSONDecodeError(
                                   "Expecting\n  value", "", 0)):
            self.assertEqual(
                self.refused({"doc": "d", "sections": SOUND}),
                "NOT RUN: inventory.json is not JSON: Expecting value: line 1 "
                "column 1 (char 0).\n")

    def test_json_that_is_not_a_mapping(self):
        """The sections alone, as a list, are what a hand-made file tends to
        be."""
        for held in (SOUND, 5, None, '"sections"'):
            self.again()
            self.assertIn("not a mapping", self.refused(held))

    def test_a_mapping_with_no_sections(self):
        self.assertIn("it has no 'sections'", self.refused({"doc": "d"}))

    def test_sections_that_are_not_a_list(self):
        for held in ({"1. Sensor Fabric": FABRIC}, 5, None, "three"):
            self.again()
            self.assertIn("its 'sections' is", self.refused(
                {"doc": "d", "sections": held}))

    def test_a_list_of_sections_with_nothing_in_it(self):
        """It printed "0 of 0 sections read", no finding, and exited 0."""
        self.assertEqual(
            self.refused({"doc": "d", "runs": 3, "sections": [],
                          "source_sha256": self.freeze()}),
            "NOT RUN: inventory.json is an inventory of no sections: nothing "
            "of the document was read.\n")

    def test_a_number_too_long_for_python_to_read(self):
        """Five thousand digits. From Python 3.11 int() will not turn them
        into a number, and the parser passes its error on. The file is JSON
        all the same, and is not called anything else. An older Python reads
        them."""
        report = self.run_main(json.dumps(
            {"doc": "d", "runs": 3, "sections": SOUND,
             "source_sha256": self.freeze()}).replace(
                 '"runs": 3', '"runs": ' + "9" * 5000))
        if self.status == 1:
            self.assertEqual(report, "")
            self.assertIn("NOT RUN: inventory.json holds what this Python "
                          "will not read: ", self.stderr)
            self.assertNotIn("is not JSON", self.stderr)
        else:
            self.assertEqual(self.status, 0)
            self.assertIn("inventory: 3 of 3 sections read", report)

    def test_a_file_nested_too_deeply_for_python_to_parse(self):
        """It is JSON all the same, and is not called anything else. How
        deep is too deep differs from one Python to the next, so the parser
        is made to give up here."""
        with mock.patch.object(synthesize.json, "load",
                               side_effect=RecursionError("too deep")):
            self.assertEqual(
                self.refused({"doc": "d", "sections": SOUND}),
                "NOT RUN: inventory.json is nested more deeply than this "
                "Python can read.\n")

    def test_a_byte_order_mark_in_front_of_the_file_is_not_part_of_it(self):
        """An editor puts one there, and the parser will not have it."""
        report = self.run_main("\ufeff" + json.dumps(
            {"doc": "d", "runs": 3, "sections": SOUND,
             "source_sha256": self.freeze()}))
        self.all_read(report)

    def test_and_nothing_is_scored_or_written_for_it(self):
        with open(os.path.join(self.project, "ground-truth.yaml"), "w") as handle:
            handle.write("defects: []\n")
        fake = types.SimpleNamespace(safe_load=lambda handle: {"defects": []})
        with mock.patch.dict(sys.modules, {"yaml": fake}):
            self.refused(5, "--ground-truth", "ground-truth.yaml", "--out",
                         "candidates.csv")
        self.assertFalse(os.path.exists(
            os.path.join(self.project, "candidates.csv")))


class AnEntryThatIsNotASection(Odd):
    """It stands where a section of the document stands, and nothing can be
    read from it: that section was not read."""

    def test_a_number_where_a_section_should_be(self):
        report = self.read([FABRIC, HYDROLOGY, 5])
        self.not_read(report, "  [not as inventory.py writes it: it is 5, not "
                              "a mapping]")
        self.assertIn("section 3 of 3, which the inventory does not name",
                      report)

    def test_anything_else_that_is_not_a_mapping(self):
        """Nought, false, an empty list and an empty text among them. Only
        null and {} hold nothing."""
        for entry in ("d:41-60", [REGISTER], True, 0, False, [], ""):
            self.not_read(self.read([FABRIC, HYDROLOGY, entry]),
                          "not a mapping]")

    def test_an_entry_with_nothing_in_it(self):
        for entry in (None, {}):
            self.not_read(self.read([FABRIC, HYDROLOGY, entry]),
                          "  [the inventory holds nothing for it]")

    def test_an_entry_with_an_error_was_not_read_whatever_the_error_says(self):
        """The reason `"error": ""` gives beside `"skipped": true` is empty,
        and an empty reason is not no reason. A first version of this check
        took it for none: the entry went on to be read as a section, and one
        with no lists in it raised."""
        unsaid = "skipped, and the inventory does not say why"
        for entry, why in (
                ({"error": "", "skipped": True}, unsaid),
                ({"error": "", "skipped": True,
                  "heading": "3. Calibration Register", "locator": "d:41-60"},
                 unsaid),
                ({**REGISTER, "error": "  ", "skipped": 1}, unsaid),
                ({"error": 0, "skipped": [1]}, unsaid),
                ({"error": ["--limit"], "skipped": True}, unsaid),
                ({"error": "not read: --limit 2", "skipped": True},
                 "not read: --limit 2"),
                ({**REGISTER, "error": None}, "extraction failed"),
                ({**REGISTER, "error": "", "skipped": False},
                 "extraction failed")):
            self.not_read(self.read([FABRIC, HYDROLOGY, entry]), f"  [{why}]")

    def test_an_entry_marked_skipped_was_not_read_error_or_no_error(self):
        """inventory.py marks an entry skipped only beside an error. One
        marked so with lists in it and no error was read as a section, and
        what it does not hold was asserted."""
        report = self.read([FABRIC, HYDROLOGY, {**REGISTER, "skipped": True}])
        self.not_read(report, "  [skipped, and the inventory does not say why]")
        self.assertIn("unverifiable", " ".join(self.findings(report)["D3"]))
        self.all_read(self.read([FABRIC, HYDROLOGY,
                                 {**REGISTER, "skipped": False}]))

    def test_why_a_section_was_skipped_is_given_on_one_line(self):
        forged = "-- candidate defects: none"
        report = self.read([FABRIC, HYDROLOGY, {
            "error": f"not read: --limit 2\n{forged}", "skipped": True,
            "heading": "3. Calibration Register", "locator": "d:41-60"}])
        self.not_read(report, f"  [not read: --limit 2 {forged}]")
        self.assertEqual([line for line in report.splitlines()
                          if line.startswith(forged)], [])

    def test_what_it_could_have_answered_is_a_question(self):
        """Section 3 holds what Section 1 says it holds, and Section 2 owns
        and consumes what Section 1 passes on. With either of them a number,
        the three findings that follow are not findings."""
        found = self.findings(self.read([FABRIC, HYDROLOGY, 5]))
        self.assertEqual(sorted(found), ["D3"])
        self.assertIn("unverifiable", " ".join(found["D3"]))
        found = self.findings(self.read([FABRIC, 5, REGISTER]))
        self.assertEqual(sorted(found), ["D6", "D8"])
        for kind in ("D6", "D8"):
            self.assertIn("unverifiable", " ".join(found[kind]))


class NoSectionThatWasRead(Odd):
    """Every entry is one that was not read. The report named them, listed
    no candidate, and exited 0, which is what a document with no defect
    gets. An inventory of no sections is refused for the same reason."""

    def test_it_is_said_and_the_run_is_not_a_result(self):
        failed = {"error": "x", "heading": "1. Sensor Fabric",
                  "locator": "d:1-20"}
        for entries in ([None], [5, "x", [], {}], [failed, failed, failed]):
            self.again()
            report = self.read(entries, "--out", "candidates.csv")
            self.assertEqual(self.status, 1)
            self.assertEqual(
                self.stderr, f"NOTHING WAS READ: 0 of {len(entries)} "
                             f"sections of inventory.json.\n")
            self.assertTrue(report.startswith(
                f"inventory: 0 of {len(entries)} sections read"), report)
            self.assertIn(f"NOT READ: {len(entries)} section(s)", report)
            self.assertNotIn("candidate defects", report)
            self.assertFalse(os.path.exists(
                os.path.join(self.project, "candidates.csv")))

    def test_one_section_read_is_a_report(self):
        report = self.read([FABRIC, None, 5])
        self.assertEqual((self.status, self.stderr), (0, ""))
        self.assertIn("inventory: 1 of 3 sections read", report)
        self.assertIn("-- candidate defects", report)

    def test_and_the_status_reaches_whoever_ran_dossier_inventory(self):
        """`dossier inventory` runs inventory.py and then synthesize.py, and
        returned the first one's status whatever the second did."""
        ran = []

        def run(script, *args, **more):
            ran.append(script)
            return 0 if script == "inventory.py" else 1

        asked = types.SimpleNamespace(project=self.project, doc="d", out=None,
                                      vllm=False)
        with mock.patch.object(dossier_cli, "run", run):
            self.assertEqual(dossier_cli.cmd_inventory(asked), 1)
        self.assertEqual(ran, ["inventory.py", "synthesize.py"])


class AFieldThatIsNotAList(Odd):
    """inventory.py gives every entry its eight lists, whatever the model
    said. An entry without one was not written by it."""

    def test_each_of_the_six_lists_has_to_be_one(self):
        for key in LISTS:
            for held in (None, False, 5, "text", {"x": 1}):
                report = self.read(sections(1, **{key: held}))
                self.not_read(report, f"its '{key}' is ")
                self.assertIn("not a list", report)

    def test_and_has_to_be_there(self):
        """A list that is missing is not an empty list. Nothing says the
        section was asked for it."""
        for key in LISTS:
            self.not_read(self.read(sections(1, **{key: GONE})),
                          f"it has no '{key}'")

    def test_capabilities_of_null_are_not_a_section_that_provides_nothing(self):
        """Section 1 says drift limits are recorded in Section 3. With
        Section 3's capabilities null that was asserted as untrue."""
        found = self.findings(self.read(sections(2, capabilities=None)))
        self.assertEqual(sorted(found), ["D3"])
        self.assertIn("unverifiable", " ".join(found["D3"]))

    def test_consumes_of_an_empty_mapping_are_not_a_section_that_consumes_nothing(self):
        """Iterated, {} yields nothing, and what Section 1 produces was an
        orphan."""
        found = self.findings(self.read(sections(1, consumes={})))
        self.assertIn("unverifiable", " ".join(found["D8"]))

    def test_the_other_two_lists_are_lists_where_an_entry_has_them(self):
        """identifiers and deferred. Nothing is read off them, and one thing
        is asked of them: whether they hold anything. What a list holds is
        not examined, and one that is not there holds nothing."""
        for key in ("identifiers", "deferred"):
            for held in (5, "x", True, {"x": 1}):
                report = self.read(sections(1, **{key: held}))
                self.not_read(report, f"its '{key}' is ")
                self.assertIn("not a list", report)
            self.all_read(self.read(sections(1, **{key: [5, None, {"x": []}]})))
            self.all_read(self.read(sections(1, **{key: GONE})))

    def before_counts(self, **changed):
        """Section 2 as the fallback left it before `full_passes` was kept:
        marked degraded, no counts, and nothing in the five lists the
        fallback does not ask for."""
        entry = {**HYDROLOGY, "degraded": True, "consumes": [], **changed}
        del entry["passes"], entry["full_passes"]
        return [FABRIC, entry, REGISTER]

    def test_a_number_there_is_no_sign_that_a_full_pass_read_the_section(self):
        """What that is for. Such a section shows that a full pass read it
        too by holding something in a list the fallback does not ask for.
        `"identifiers": 5` was something: the section counted as read in
        full, and what Section 1 produces and it does not consume was an
        orphan, asserted."""
        report = self.read(self.before_counts(identifiers=5))
        self.not_read(report, "its 'identifiers' is 5, not a list")
        self.assertIn("unverifiable", " ".join(self.findings(report)["D8"]))

    def test_an_identifier_there_is_one_and_an_empty_place_is_not(self):
        """A null, or text with nothing in it, is no sign of a full pass."""
        for key in ("identifiers", "deferred"):
            report = self.read(self.before_counts(**{key: ["HM-01"]}))
            self.all_read(report)
            self.assertNotIn("READ IN PART", report)
            self.assertNotIn("unverifiable",
                             " ".join(self.findings(report)["D8"]))
            for nothing in (None, "", " "):
                report = self.read(self.before_counts(**{key: [nothing]}))
                self.assertIn("READ IN PART", report, nothing)
                self.assertIn("unverifiable",
                              " ".join(self.findings(report)["D8"]), nothing)


class WhatAnEntrySaysOfItself(Odd):
    """Its heading and where it is, and how it was read."""

    def test_a_heading_that_is_not_text(self):
        report = self.read(sections(2, heading=-1))
        self.not_read(report, "its 'heading' is -1, not text")
        self.assertIn("the section at d:41-60, which has no heading", report)

    def test_a_locator_that_is_not_text(self):
        report = self.read(sections(2, locator=None))
        self.not_read(report, "its 'locator' is null, not text")
        self.assertIn("    3. Calibration Register  [", report)

    def test_a_heading_or_a_locator_that_is_not_there(self):
        for key in ("heading", "locator"):
            self.not_read(self.read(sections(2, **{key: GONE})),
                          f"it has no '{key}'")

    def test_a_heading_or_a_locator_with_a_line_break_in_it(self):
        """inventory.py takes a heading from one line of the document. One
        that runs over two ended the line of the report it was printed on,
        and began a line that this program did not write."""
        forged = "-- candidate defects: none"
        for key, held in (("heading", f"3. Calibration Register\n{forged}"),
                          ("heading", "3. Calibration Register\n"),
                          ("locator", f"d:41-60\r{forged}"),
                          ("locator", f"d:41-60\u2028{forged}"),
                          ("locator", "d:41-60" + chr(0x2029) + forged)):
            report = self.read(sections(2, **{key: held}))
            self.not_read(report, f"its '{key}' has a line break in it")
            self.assertEqual([line for line in report.splitlines()
                              if line.startswith(forged)], [])

    def test_a_locator_with_more_digits_than_a_line_number_has(self):
        """A finding reaches a planted defect when a line its locator names
        is inside the defect's span, and every run of digits in the locator
        was taken for a line. One of five thousand is more than int() will
        read, and twenty noughts and a 5 are not line 5."""
        orphan = [{"id": "GT-D8-001", "class": "D8", "title": "t",
                   "lines": [1, 60]}]
        for locator, verdict in (("d:1-20", "FOUND"),
                                 ("d:" + "9" * 5000, "miss "),
                                 ("d:" + "0" * 20 + "5", "miss ")):
            report = self.scored(
                [{**FABRIC, "locator": locator}, {**HYDROLOGY, "consumes": []},
                 REGISTER], orphan)
            self.all_read(report)
            self.assertIn(f"{verdict} GT-D8-001", report)

    def test_passes_that_are_not_whole_numbers(self):
        for key, held, shown in (("passes", "three", '"three"'),
                                 ("passes", True, "true"),
                                 ("passes", 2.5, "2.5"),
                                 ("passes", float("nan"), "NaN"),
                                 ("full_passes", [1], "a list")):
            self.not_read(self.read(sections(2, **{key: held})),
                          f"its '{key}' is {shown}, not a whole number")

    def test_passes_as_inventory_py_counts_them(self):
        """One pass or more answered, each with the whole schema, except
        that the first was the fallback's where the entry is marked
        degraded. An entry counted any other way was a section read: with
        `"full_passes": 99` beside `"passes": 1`, read in full."""
        for held in ({"passes": 1, "full_passes": 1},
                     {"passes": 2, "full_passes": 2},
                     {"degraded": False},
                     {"degraded": True, "passes": 1, "full_passes": 0},
                     {"degraded": True, "passes": 3, "full_passes": 2}):
            report = self.read(sections(2, **held))
            self.assertIn("inventory: 3 of 3 sections read", report)
            self.assertNotIn("NOT READ", report)
        for held, shown in (
                ({"full_passes": 2}, "(3), 'full_passes' (2) and 'degraded' "
                                     "(false)"),
                ({"full_passes": 4}, "(3), 'full_passes' (4) and 'degraded' "
                                     "(false)"),
                ({"full_passes": -1}, "(3), 'full_passes' (-1) and "
                                      "'degraded' (false)"),
                ({"degraded": True}, "(3), 'full_passes' (3) and 'degraded' "
                                     "(true)"),
                ({"degraded": True, "passes": 1, "full_passes": 99},
                 "(1), 'full_passes' (99) and 'degraded' (true)"),
                ({"passes": 1, "full_passes": 0}, "(1), 'full_passes' (0) "
                                                  "and 'degraded' (false)")):
            self.not_read(self.read(sections(2, **held)),
                          f"its 'passes' {shown} do not agree")

    def test_a_section_that_no_pass_answered(self):
        """inventory.py writes that one as an error. Given as an entry with
        six empty lists, it was a section that holds nothing."""
        for passes in (0, -1):
            self.not_read(
                self.read(sections(2, passes=passes, full_passes=passes)),
                f"its 'passes' is {passes}, and a section no pass answered is "
                f"written as an error")

    def test_one_count_without_the_other(self):
        for there, gone in (("passes", "full_passes"), ("full_passes", "passes")):
            self.not_read(self.read(sections(2, **{gone: GONE})),
                          f"it has '{there}' and no '{gone}'")

    def test_an_entry_from_before_passes_were_kept_says_nothing_of_them(self):
        """The committed fixture inventory is one: no entry of it has
        `passes`, `full_passes` or `degraded`."""
        entries = sections()
        for entry in entries:
            del entry["passes"], entry["full_passes"]
        report = self.read(entries)
        self.all_read(report)
        # Section 3 has nothing in the five lists the fallback does not ask
        # for. It is not marked degraded, so nothing says the fallback read it.
        self.assertNotIn("READ IN PART", report)

    def test_a_later_full_pass_is_what_the_count_says_it_is(self):
        """The fallback read Section 2 and a second pass read it whole, and
        found nothing in the five lists the fallback does not ask for. The
        count says so, and the lists cannot."""
        entry = {**HYDROLOGY, "degraded": True, "passes": 2, "full_passes": 1,
                 "consumes": []}
        report = self.read([FABRIC, entry, REGISTER])
        self.all_read(report)
        self.assertNotIn("READ IN PART", report)
        self.assertNotIn("unverifiable", " ".join(self.findings(report)["D8"]))

    def test_degraded_that_is_not_true_or_false(self):
        self.not_read(self.read(sections(2, degraded="yes")),
                      "its 'degraded' is \"yes\", not true or false")

    def test_a_value_is_cut_only_when_it_is_longer_than_the_room_for_it(self):
        for held, shown in (("x" * 30, '"' + "x" * 30 + '"'),
                            ("x" * 31, '"' + "x" * 28 + "...")):
            self.not_read(self.read(sections(2, passes=held)),
                          f"its 'passes' is {shown}, not a whole number")

    def test_a_value_too_long_to_show_is_cut(self):
        report = self.read(sections(2, passes="9" * 5000))
        self.not_read(report, "its 'passes' is \"" + "9" * 28
                      + "..., not a whole number")
        self.assertLess(max(map(len, report.splitlines())), 200)

    def test_a_section_with_neither_a_heading_nor_a_place(self):
        """Both are text, and empty. It was read, and is named as what it
        is where the report lists it."""
        report = self.read(sections(1, heading="", locator="", passes=2,
                                    full_passes=2))
        self.assertIn("READ IN FEWER than the 3 passes", report)
        self.assertIn("    a section the inventory does not name  [2 of 3]",
                      report)


class AnItemThatCannotBeRead(Odd):
    """What a list holds is a model's reply. An item that is not what its
    list is asked for is set aside: counted and named, and not used. The
    section it is in is read all the same."""

    def test_an_input_or_an_output_that_is_not_text(self):
        for key in ("consumes", "produces"):
            for item, why in ((5, "is 5, not text"), (["x"], "is a list, not text"),
                              (True, "is true, not text"),
                              ({"label": "gauge readings"}, "has no 'name'"),
                              ({"name": 5}, "has 5 for its 'name', not text"),
                              ({"name": " "}, "has nothing in its 'name'")):
                self.set_aside(
                    self.read(sections(1, **{key: [item]})),
                    f"  [entry 1 of its '{key}' {why}]")

    def test_an_input_or_an_output_given_by_its_name_is_read_by_it(self):
        """`{"name": ...}`, as a capability is given. It was read that way
        where --authority-as-dataflow folds data flow into ownership, and
        nowhere else: without the flag it was no consumer, and what it names
        was an orphan, asserted."""
        named = {"name": "calibrated gauge readings", "quote": "q"}
        for flags in ((), ("--authority-as-dataflow",)):
            for index, key in ((1, "consumes"), (0, "produces")):
                report = self.read(sections(index, **{key: [named]}), *flags)
                self.all_read(report)
                self.assertEqual(report, self.read(SOUND, *flags), (key, flags))

    def test_it_is_named_by_its_section_and_where_it_stands_in_the_list(self):
        report = self.read(sections(
            1, consumes=["calibrated gauge readings", None, 5]))
        self.set_aside(report, "    2. Hydrology Model (d:21-40)  [entry 3 of "
                               "its 'consumes' is 5, not text]")

    def test_a_null_is_an_empty_place_in_a_list_and_not_an_item(self):
        """Nor is text with nothing in it. Each holds nothing that could be
        anything: `[null]` and `[""]` say what `[]` says, in every list."""
        for key in LISTS:
            for nothing in (None, "", "  "):
                report = self.read(
                    sections(1, **{key: HYDROLOGY[key] + [nothing]}))
                self.all_read(report)
                self.assertEqual(report, self.read(SOUND), (key, nothing))

    def test_the_rest_of_its_list_is_read(self):
        """Section 2 consumes what Section 1 produces, and says so beside
        the item that could not be read: no orphan."""
        report = self.read(sections(
            1, consumes=[5, "calibrated gauge readings"]))
        self.set_aside(report)
        self.assertEqual(self.findings(report), {})

    def test_several_are_counted_and_the_first_in_each_section_is_given(self):
        report = self.read([{**FABRIC, "consumes": [5, 6], "produces": [[]]},
                            {**HYDROLOGY, "defers_to": [7]},
                            {**REGISTER, "produces": [8, 9]}])
        self.set_aside(
            report,
            "    1. Sensor Fabric (d:1-20)  [entry 1 of its 'consumes' is 5, "
            "not text; and 2 more]",
            "    2. Hydrology Model (d:21-40)  [entry 1 of its 'defers_to' is "
            "7, not a mapping]",
            "    3. Calibration Register (d:41-60)  [entry 1 of its 'produces' "
            "is 8, not text; and 1 more]", items=6, holders=3)

    def test_an_authority_entry(self):
        for item, why in (
                ("Hydrology Model decides", 'is "Hydrology Model decides", '
                                            'not a mapping'),
                ({}, "has no 'capability'"),
                ({"capability": "threshold adjudication"}, "has no 'owner'"),
                ({**OWNS, "owner": ["Hydrology Model"]},
                 "has a list for its 'owner', not text"),
                ({**OWNS, "owner": None}, "has null for its 'owner', not text"),
                ({**OWNS, "capability": 5},
                 "has 5 for its 'capability', not text"),
                ({**OWNS, "capability": ""}, "has nothing in its 'capability'"),
                ({**OWNS, "owner": "  "}, "has nothing in its 'owner'"),
                ({**OWNS, "action": 5}, "has 5 for its 'action', not text"),
                ({**OWNS, "polarity": "disputed"},
                 'has "disputed" for its \'polarity\', not "owns" or '
                 '"excludes"')):
            self.set_aside(self.read(sections(1, authority=[item])),
                           f"  [entry 1 of its 'authority' {why}]")

    def test_an_evidence_claim(self):
        for item, why in (
                ("Drift limits are in Section 3",
                 'is "Drift limits are in Section 3", not a mapping'),
                ({"points_to": "Section 3"}, "has no 'claim'"),
                ({**CLAIM, "claim": ["Calibration", "drift"]},
                 "has a list for its 'claim', not text"),
                ({**CLAIM, "claim": None},
                 "has null for its 'claim', not text"),
                ({**CLAIM, "claim": ""}, "has nothing in its 'claim'"),
                ({**CLAIM, "points_to": 3}, "has 3 for its 'points_to', not text"),
                ({**CLAIM, "points_to": {"place": "Section 3"}},
                 "has a mapping for its 'points_to', not text"),
                ({**CLAIM, "points_to": ["Section 3", 5]},
                 "has a list for its 'points_to', not text"),
                ({"claim": 5, "points_to": ["Section 3", "Appendix B"]},
                 "has 5 for its 'claim', not text")):
            self.set_aside(self.read(sections(0, evidence_claims=[item])),
                           f"  [entry 1 of its 'evidence_claims' {why}]")

    def test_a_capability(self):
        for item, why in (({"name": 5}, "has 5 for its 'name', not text"),
                          ({"name": None}, "has null for its 'name', not text"),
                          ({"name": "", "quote": "Calibration drift limits"},
                           "has nothing in its 'name'"),
                          ({"quote": "q"}, "has no 'name'"),
                          (5, "is 5, not text")):
            self.set_aside(self.read(sections(2, capabilities=[item])),
                           f"  [entry 1 of its 'capabilities' {why}]")

    def test_a_deferral(self):
        for item, why in (({}, "has no 'capability'"),
                          ({"capability": None, "to": "Hydrology Model"},
                           "has null for its 'capability', not text"),
                          ({"capability": "", "to": "Hydrology Model"},
                           "has nothing in its 'capability'"),
                          ({**PASSED_ON, "to": 5}, "has 5 for its 'to', not text"),
                          ("Hydrology Model",
                           'is "Hydrology Model", not a mapping')):
            self.set_aside(self.read(sections(0, defers_to=[item])),
                           f"  [entry 1 of its 'defers_to' {why}]")


class WhatAnItemSetAsideCouldHaveAnswered(Odd):
    """Three of the six lists are asked for an absence: whether any section
    consumes an output, whether any owns what another defers, whether the
    place a claim points to holds what it says. An item of one of those that
    was not read could be the answer, and the finding is a question."""

    def doubt(self, report, kind):
        """The line under a finding of this class that says why it is not
        asserted, or None."""
        return next((line for line in self.findings(report)[kind]
                     if line.startswith("unverifiable: ")), None)

    def test_an_input_could_be_what_consumes_an_output(self):
        """Section 1 produces calibrated gauge readings. Section 2 has one
        input, and it is `["calibrated gauge readings"]`: a list where text
        is asked, so no consumer."""
        report = self.read(sections(
            1, consumes=[["calibrated gauge readings"]]))
        self.set_aside(report)
        self.assertEqual(
            self.doubt(report, "D8"),
            "unverifiable: a section holds an input that was not read, "
            "which could be this: 2. Hydrology Model (d:21-40)")
        # An input is not a claim, and the claims D3 answers for are as they
        # were.
        self.assertIn("claims:    D3 checked 1 of 1 evidence claims", report)
        self.assertNotIn("not checked", report)
        # With no input at all it is an orphan, and said to be.
        report = self.read(sections(1, consumes=[]))
        self.all_read(report)
        self.assertIsNone(self.doubt(report, "D8"))

    def test_an_authority_entry_could_be_the_owner(self):
        """Section 1 passes threshold adjudication to the Hydrology Model.
        Section 2's one authority entry is a sentence, not a mapping."""
        report = self.read(sections(1, authority=["Hydrology Model decides"]))
        self.set_aside(report)
        self.assertEqual(
            self.doubt(report, "D6"),
            "unverifiable: a section holds an item that was not read, which "
            "could be its owner: 2. Hydrology Model (d:21-40)")
        report = self.read(sections(1, authority=[]))
        self.all_read(report)
        self.assertIsNone(self.doubt(report, "D6"))

    def test_a_capability_could_be_what_a_claim_points_to(self):
        """Section 1 says drift limits are recorded in Section 3, and
        Section 3's one capability has a number for its name."""
        report = self.read(sections(2, capabilities=[{"name": 5}]))
        self.set_aside(report)
        self.assertEqual(
            self.doubt(report, "D3"),
            "unverifiable: the place it points to holds a capability that "
            "was not read: 3. Calibration Register (d:41-60)")
        report = self.read(sections(2, capabilities=[]))
        self.all_read(report)
        self.assertIsNone(self.doubt(report, "D3"))

    def test_a_capability_named_with_nothing_is_not_one_that_was_read(self):
        """The fallback's validator lets it through, and the model had put
        what the section holds in the quote. The name was text, so the item
        was read: a section with a capability that matches nothing, and the
        claim that it holds the limits asserted as untrue."""
        report = self.read(sections(2, capabilities=[
            {"name": "", "quote": "Calibration drift limits, by gauge"}]))
        self.set_aside(report, "has nothing in its 'name'")
        self.assertIsNotNone(self.doubt(report, "D3"))

    def test_but_not_one_in_a_section_the_claim_does_not_point_to(self):
        report = self.read([FABRIC, {**HYDROLOGY, "capabilities": [5]},
                            {**REGISTER, "capabilities": []}])
        self.set_aside(report)
        self.assertIsNone(self.doubt(report, "D3"))

    def test_nor_an_item_of_another_list_in_the_section_it_does_point_to(self):
        """Section 3 holds no capability, and an input of its own that was
        not read. What it consumes is not what it holds."""
        for key in ("consumes", "produces", "authority", "defers_to",
                    "evidence_claims"):
            report = self.read([FABRIC, HYDROLOGY,
                                {**REGISTER, "capabilities": [], key: [5]}])
            self.set_aside(report)
            self.assertIsNone(self.doubt(report, "D3"), key)

    def test_a_section_that_was_not_read_is_the_reason_given_first(self):
        """Both stand between the claim and saying it is untrue. One is
        given: the section, which could hold the place itself."""
        report = self.read([{**FABRIC, "evidence_claims": [
            {**CLAIM, "points_to": "Sections 2 to 3"}]},
            {**HYDROLOGY, "capabilities": [5]},
            {"error": "x", "heading": "3. Calibration Register",
             "locator": "d:41-60"}])
        self.assertTrue(self.doubt(report, "D3").startswith(
            "unverifiable: a section that was not read could"),
            self.doubt(report, "D3"))

    def test_and_the_capability_before_a_doubt_about_the_pointer_itself(self):
        """A pointer that says more than the place it names is a doubt of
        its own, and the one given where the place was read whole. Where a
        capability of the place was not read, that is the reason given: it
        is what the place could hold."""
        claim = {**CLAIM, "points_to": "Section 3, in the second table"}
        report = self.read([{**FABRIC, "evidence_claims": [claim]}, HYDROLOGY,
                            {**REGISTER, "capabilities": [{"name": 5}]}])
        self.assertEqual(
            self.doubt(report, "D3"),
            "unverifiable: the place it points to holds a capability that "
            "was not read: 3. Calibration Register (d:41-60)")
        report = self.read([{**FABRIC, "evidence_claims": [claim]}, HYDROLOGY,
                            {**REGISTER, "capabilities": []}])
        self.assertEqual(
            self.doubt(report, "D3"),
            "unverifiable: the pointer has more in it than the places it "
            "names, and only those were read")

    def test_each_list_answers_for_its_own_absence_and_no_other(self):
        """With Section 2 owning nothing and consuming nothing, Section 1's
        deferral has no owner and its output no consumer. An item set aside
        in another list leaves both findings as they are."""
        for key, item in (("capabilities", 5), ("evidence_claims", 5),
                          ("defers_to", 5), ("produces", 5)):
            report = self.read(
                [FABRIC, {**HYDROLOGY, "authority": [], "consumes": [],
                          key: [item]}, REGISTER])
            self.set_aside(report)
            self.assertIsNone(self.doubt(report, "D6"), key)
            self.assertIsNone(self.doubt(report, "D8"), key)
        report = self.read([FABRIC, {**HYDROLOGY, "authority": [],
                                     "consumes": [5]}, REGISTER])
        self.assertIsNone(self.doubt(report, "D6"))
        self.assertIsNotNone(self.doubt(report, "D8"))
        report = self.read([FABRIC, {**HYDROLOGY, "authority": [5],
                                     "consumes": []}, REGISTER])
        self.assertIsNotNone(self.doubt(report, "D6"))
        self.assertIsNone(self.doubt(report, "D8"))

    def test_under_authority_as_dataflow_an_input_or_an_output_could_own(self):
        """That flag takes ownership from what a section produces and
        consumes as well."""
        for key in ("produces", "consumes"):
            entries = [FABRIC, {**HYDROLOGY, "authority": []},
                       {**REGISTER, key: [5]}]
            report = self.read(entries, "--authority-as-dataflow")
            self.assertEqual(
                self.doubt(report, "D6"),
                "unverifiable: a section holds an item that was not read, "
                "which could be its owner: 3. Calibration Register (d:41-60)")
            self.assertIsNone(self.doubt(self.read(entries), "D6"))

    def test_both_doubts_are_given_where_there_are_two(self):
        report = self.read([FABRIC, {**HYDROLOGY, "consumes": [5]}, 5])
        self.assertEqual(
            self.doubt(report, "D8"),
            "unverifiable: a section that was not read, or was read without "
            "its consumes, could consume it: section 3 of 3, which the "
            "inventory does not name; a section holds an input that was not "
            "read, which could be this: 2. Hydrology Model (d:21-40)")

    def test_the_reasons_listed_under_the_count_take_an_item_in(self):
        """The lines under the count of candidates say why a finding is
        marked unverifiable. An item that was set aside is one more reason,
        and is given there when there is one."""
        line = ("   An item of a section's lists that could not be read is "
                "such a reason as well.\n")
        self.assertIn(line, self.read(sections(1, consumes=[5])))
        # A section that was not read, and no item set aside.
        report = self.read([FABRIC, 5, REGISTER])
        self.assertIn("unverifiable: ", report)
        self.assertNotIn(line, report)

    def test_a_claim_is_one_that_d3_did_not_check(self):
        """It is one of the section's claims all the same, and is counted
        among those D3 did not check, with the reason."""
        report = self.read(sections(
            0, evidence_claims=[CLAIM, "Limits are in Section 3", 5]))
        self.set_aside(report, items=2)
        self.assertIn("claims:    D3 checked 1 of 3 evidence claims", report)
        self.assertIn("           not checked: 2\n"
                      "               an entry that could not be read as a "
                      "claim: 2\n", report)
        self.assertNotIn("no place to check against", report)

    def test_and_a_score_says_how_many_items_it_was_made_without(self):
        """Under the line that says what the inventory left unread, which is
        printed for this too."""
        planted = [{"id": "GT-D8-001", "class": "D8", "title": "t",
                    "lines": [1, 60]}]
        report = self.scored(sections(1, consumes=[5, True]), planted)
        self.assertIn(
            "  scored over an inventory that left 0 section(s) unread, 0 read "
            "in part and 0 read in\n  fewer passes than asked, and has 0 "
            "line(s) in no section: a miss may be", report)
        self.assertIn("\n  The inventory also holds 2 item(s) that could not "
                      "be read.\n", report)
        report = self.scored(sections(1, consumes=[]), planted)
        self.assertNotIn("scored over an inventory", report)

    def test_and_says_nothing_of_items_where_none_was_set_aside(self):
        """The line is as it was for a section not read, one read in part
        and one read in fewer passes, each of them alone."""
        planted = [{"id": "GT-D8-001", "class": "D8", "title": "t",
                    "lines": [1, 60]}]
        for entry, left in (
                ({"error": "x", "heading": "2. Hydrology Model",
                  "locator": "d:21-40"},
                 "1 section(s) unread, 0 read in part and 0 read in"),
                # As the fallback left one before its passes were counted.
                ({key: held for key, held in
                  {**HYDROLOGY, "degraded": True, "consumes": []}.items()
                  if key not in ("passes", "full_passes")},
                 "0 section(s) unread, 1 read in part and 0 read in"),
                ({**HYDROLOGY, "passes": 2, "full_passes": 2},
                 "0 section(s) unread, 0 read in part and 1 read in")):
            report = self.scored([FABRIC, entry, REGISTER], planted)
            self.assertIn(f"  scored over an inventory that left {left}\n  "
                          f"fewer passes than asked, and has 0 line(s) in no "
                          f"section: a miss may be", report)
            self.assertNotIn("also holds", report)


class WhatIsTakenOfAnItem(Odd):
    """The keys its list is asked for, and its quote where that is text."""

    def test_a_key_its_list_is_not_asked_for_is_not_carried(self):
        """`points_to` on an authority entry, `owner` on a claim or on a
        deferral. None is what its list is asked for, so none is checked, and
        the item is read. None is taken either: every key of an item used to
        be carried, and a number under one of these, read as text where a
        finding is printed, raises."""
        self.all_read(self.read(sections(1, authority=[
            {**OWNS, "points_to": [1, 2], "to": 5}])))
        report = self.read(sections(
            0, evidence_claims=[{**CLAIM, "points_to": "Section 9",
                                 "owner": 5, "to": ["x"]}],
            defers_to=[{**PASSED_ON, "owner": 5, "points_to": [None]}]))
        self.all_read(report)
        self.assertIn("Section 9", " ".join(self.findings(report)["D3"]))

    def test_and_does_not_stand_in_for_one_that_is(self):
        """A finding is printed with who each entry names: the place a claim
        points to, the party a deferral passes to. `owner` was looked at
        first on every entry, so text under it on a claim or a deferral was
        printed in that place."""
        report = self.read(
            [{**FABRIC,
              "evidence_claims": [{**CLAIM, "points_to": "Section 9",
                                   "owner": "Somebody Else"}],
              "defers_to": [{**PASSED_ON, "owner": "Somebody Else"}]},
             {**HYDROLOGY, "authority": []}, REGISTER])
        self.all_read(report)
        found = self.findings(report)
        self.assertTrue(found["D3"][1].endswith("Section 9"), found["D3"])
        self.assertTrue(found["D6"][1].endswith("Hydrology Model"), found["D6"])
        self.assertNotIn("Somebody Else", report)

    def test_nor_where_two_owners_are_put_to_a_model(self):
        """--adjudicate asks who each of two entries names: the `owner` of
        an authority entry, the `to` of a deferral. It took whichever of the
        two keys held something."""
        passed_on = {"capability": "threshold adjudication", "owner": 5}
        report = self.read(
            [{**FABRIC, "defers_to": [{**passed_on, "to": "Hydrology Model"}]},
             {**HYDROLOGY, "authority": [{**OWNS, "to": 5}],
              "defers_to": [{**passed_on, "to": "Alerting Service"}]},
             {**REGISTER, "authority": [{**OWNS, "owner": "Alerting Service",
                                         "to": 5}]}],
            "--adjudicate")
        self.all_read(report)
        self.assertIn("adjudicated 1 of 1 authority pairs", report)
        self.assertIn("adjudicated 1 of 1 deferral pairs", report)

    def shown_to_a_model(self, entries, *extra):
        """What --adjudicate puts to the model for each pair, and the
        report."""
        shown = []

        def ask(client, system, user, validate=None, label=""):
            shown.append(user)
            return {"same_slot": True, "conflict": True, "reason": "r"}

        with mock.patch.object(Agreeable, "ask", ask):
            report = self.read(entries, "--adjudicate", *extra)
        return shown, report

    def test_the_quote_of_each_entry_is_what_a_model_and_a_reader_are_shown(self):
        """--adjudicate puts the quotes of both entries of a pair to the
        model, each cut to 220 characters, and the candidate file gives the
        quote of the first entry a finding rests on."""
        decides = "the model decides " + "a" * 230
        serves = "the service decides " + "b" * 230
        shown, report = self.shown_to_a_model(
            [{**FABRIC, "defers_to": [{**PASSED_ON,
                                      "quote": "the model adjudicates"}]},
             {**HYDROLOGY, "authority": [{**OWNS, "quote": decides}],
              "defers_to": [{**PASSED_ON, "to": "Alerting Service",
                             "quote": "alerting adjudicates"}]},
             {**REGISTER, "authority": [{**OWNS, "owner": "Alerting Service",
                                         "quote": serves}]}],
            "--out", "candidates.csv")
        self.all_read(report)
        self.assertEqual(shown, [
            "A. [2. Hydrology Model] threshold adjudication \u2014 owner: "
            f"Hydrology Model\n   {decides[:220]}\n\n"
            "B. [3. Calibration Register] threshold adjudication \u2014 owner: "
            f"Alerting Service\n   {serves[:220]}\n\n"
            "Same slot? Conflict?",
            "A. [1. Sensor Fabric] threshold adjudication \u2014 owner: "
            "Hydrology Model\n   the model adjudicates\n\n"
            "B. [2. Hydrology Model] threshold adjudication \u2014 owner: "
            "Alerting Service\n   alerting adjudicates\n\n"
            "Same slot? Conflict?"])
        quoted = self.candidates("D5")
        self.assertTrue(quoted)
        self.assertEqual(set(quoted), {decides})

    def test_a_quote_that_is_not_text_is_not_shown(self):
        """Nothing is decided on a quote. A list of two, a number or null is
        no reason to set aside what the item says, and it is not cut to
        length or written out as if it were text either."""
        for quote in (["q one", "q two"], 7, None, {"text": "q"}):
            self.again()
            shown, report = self.shown_to_a_model(
                [{**FABRIC, "evidence_claims": [
                    {**CLAIM, "points_to": "Section 9", "quote": quote}]},
                 {**HYDROLOGY, "authority": [{**OWNS, "quote": quote}]},
                 {**REGISTER, "authority": [{**OWNS, "owner": "Alerting Service",
                                             "quote": quote}],
                  "capabilities": [{"name": "calibration drift limits",
                                    "quote": quote}]}],
                "--out", "candidates.csv")
            self.all_read(report)
            self.assertEqual(shown, [
                "A. [2. Hydrology Model] threshold adjudication \u2014 owner: "
                "Hydrology Model\n   \n\n"
                "B. [3. Calibration Register] threshold adjudication \u2014 "
                "owner: Alerting Service\n   \n\n"
                "Same slot? Conflict?"])
            self.assertEqual(set(self.candidates("D5")), {""})
            # A claim with no quote is quoted by what it says.
            self.assertEqual(self.candidates("D3"),
                             ["Calibration drift limits are recorded"])

    def test_nothing_under_a_key_that_may_be_absent(self):
        """Null, an empty list, an empty mapping and text with nothing in it
        alike. A deferral to {} still says what is deferred, and the finding
        that nobody owns it is made as it is for one that names nobody."""
        for nothing in (None, [], {}, "", "  ", GONE):
            deferral = {"capability": "threshold adjudication", "to": nothing}
            claim = {**CLAIM, "points_to": nothing}
            owns = {**OWNS, "action": nothing}
            for item in (deferral, claim, owns):
                if nothing is GONE:
                    del item[[k for k, v in item.items() if v is GONE][0]]
            report = self.read(
                [{**FABRIC, "defers_to": [deferral], "evidence_claims": [claim]},
                 {**HYDROLOGY, "authority": []},
                 {**REGISTER, "authority": [{**owns, "capability": "archive"}]}])
            self.all_read(report)
            self.assertEqual(self.findings(report)["D6"][0],
                             "[D6] threshold adjudication")
            self.assertIn("               no pointer: 1\n", report)

    def test_the_quote_of_a_claim_is_given_where_it_has_one(self):
        self.all_read(self.read(
            sections(0, evidence_claims=[{**CLAIM, "points_to": "Section 9",
                                          "quote": "limits: see Section 9"}]),
            "--out", "candidates.csv"))
        self.assertEqual(self.candidates("D3"), ["limits: see Section 9"])

    def test_an_entry_that_disclaims_a_capability_does_not_own_it(self):
        """Polarity is one of the keys that are carried. Two owners of one
        capability are a finding. An owner, and a section that says who does
        not own it, are not."""
        for polarity, found in (("owns", True), ("excludes", False)):
            report = self.read([FABRIC, HYDROLOGY, {**REGISTER, "authority": [
                {**OWNS, "owner": "Alerting Service", "polarity": polarity}]}])
            self.all_read(report)
            self.assertEqual("D5" in self.findings(report), found, report)
            if found:
                self.assertEqual(
                    [line.split("  ")[-1].strip()
                     for line in self.findings(report)["D5"][1:]],
                    ["Hydrology Model", "Alerting Service"])

    def test_an_authority_entry_from_before_action_and_polarity_were_kept(self):
        self.all_read(self.read(sections(1, authority=[
            {"capability": "threshold adjudication",
             "owner": "Hydrology Model"}])))

    def test_a_pointer_that_is_a_list_of_places_or_nothing(self):
        for place in (["Section 3", "Appendix B"], None, GONE):
            claim = dict(CLAIM)
            if place is GONE:
                del claim["points_to"]
            else:
                claim["points_to"] = place
            self.all_read(self.read(sections(0, evidence_claims=[claim])))

    def test_the_places_of_a_list_are_read_as_one_pointer(self):
        """Section 9 and Appendix B: neither is in the document."""
        report = self.read(sections(0, evidence_claims=[
            {**CLAIM, "points_to": ["Section 9", "Appendix B"]}]))
        self.all_read(report)
        self.assertTrue(self.findings(report)["D3"][1].endswith(
            "Section 9 and Appendix B"))

    def test_a_capability_that_is_its_name_and_nothing_else(self):
        """What the fallback's validator lets through. It is read, and holds
        what Section 1 says it holds."""
        report = self.read(sections(2, capabilities=["calibration drift limits"]))
        self.all_read(report)
        self.assertEqual(self.findings(report), {})

    def test_a_line_break_in_a_claim_does_not_begin_a_line_of_the_report(self):
        """A model copies a sentence that runs over two lines, and the
        second line of it was printed as a line of the report."""
        forged = "[D9] every section is in order"
        report = self.read(sections(0, evidence_claims=[
            {"claim": f"Alert thresholds\n{forged} and tabulated in full",
             "points_to": "Section 3"},
            {"claim": "Flood maps are drawn for every gauge in the basin",
             "points_to": f"Section 9\n{forged}"}]))
        self.all_read(report)
        lines = report.splitlines()
        self.assertEqual([line for line in lines if line.startswith("[D9]")],
                         [])
        self.assertEqual(sum(line.startswith("[D3] ") for line in lines), 2)


class HowAFindingIsPrinted(Odd):
    """Under its class and its label, the entries it rests on: where each is,
    the heading it is under, and who or what it names. Each is cut to fit a
    line, and three entries are shown."""

    def test_each_part_is_cut_to_fit_and_three_entries_are_shown(self):
        heading = "1. Sensor Fabric and everything attached to it"
        place = "Section 9 of the accompanying volume on calibration"
        claim = {"claim": "Calibration drift limits and their history, gauge "
                          "by gauge and year by year, are recorded",
                 "points_to": place}
        report = self.read(
            [{**FABRIC, "heading": heading, "evidence_claims": [claim],
              "defers_to": [dict(PASSED_ON) for _ in range(4)]},
             {**HYDROLOGY, "authority": []}, REGISTER])
        lines = report.splitlines()
        self.assertEqual([len(line) for line in lines
                          if line.startswith("[D3] ")], [len("[D3] ") + 76])
        # Both cuts fall inside a word, so one a character later shows.
        self.assertIn(f"      {'d:1-20':22} {heading[:30]:32} {place[:26]}",
                      lines)
        self.assertEqual((heading[30], place[26]), ("g", "i"))
        first = lines.index("[D6] threshold adjudication") + 1
        self.assertEqual(
            lines[first:first + 3],
            [f"      {'d:1-20':22} {heading[:30]:32} Hydrology Model"] * 3)
        self.assertTrue(lines[first + 3].startswith("[D3] "))

    def test_a_control_character_is_a_space_where_a_line_is_printed(self):
        """An escape sequence in what a claim points to took back the line a
        terminal had shown above it. A tab in a heading is a heading
        inventory.py can write, and its section is read."""
        erase = "\x1b[1A\x1b[2K"
        report = self.read(
            [{**FABRIC, "heading": "1.\tSensor Fabric", "evidence_claims": [
                {"claim": f"Alert thresholds {erase} are tabulated in full",
                 "points_to": f"Section 8 {erase} of the atlas"}]},
             HYDROLOGY, REGISTER])
        self.all_read(report)
        self.assertNotIn("\x1b", report)
        self.assertNotIn("\t", report)
        self.assertIn("1. Sensor Fabric", report)
        # And in where a section says it is, which is printed beside each
        # finding and wherever the section is listed.
        report = self.read(
            [{**FABRIC, "locator": "d:1-20" + erase, "passes": 2,
              "full_passes": 2}, {**HYDROLOGY, "consumes": []}, REGISTER])
        self.assertIn("READ IN FEWER than the 3 passes", report)
        self.assertIn("[D8] calibrated gauge readings", report)
        self.assertNotIn("\x1b", report)

    def test_an_entry_that_names_nobody_is_printed_with_nothing_there(self):
        """An output has no owner and points nowhere."""
        report = self.read(sections(1, consumes=[]))
        self.assertIn(f"      {'d:1-20':22} {'1. Sensor Fabric':32} \n", report)


class WhichDocumentItWasBuiltFrom(Odd):
    """`doc`, which the file says of itself. D3 looks the document up by it."""

    def test_a_name_that_is_not_text_names_no_document(self):
        for doc, shown in ((["d"], "a list"), (5, "5"), (True, "true"),
                           ({"slug": "d"}, "a mapping")):
            report = self.read(sections(), doc=doc)
            self.all_read(report)
            self.assertIn(
                f"NOT CONSULTED: the inventory gives {shown} as the document "
                f"it was built from, which is not a name.", report)

    def test_nor_does_one_with_a_character_that_cannot_be_printed(self):
        """It was looked up, and the line that says no document goes by
        that name printed it: what came after a line break in it began a
        line of the report."""
        forged = "-- candidate defects: none"
        for doc in (f"d\n{forged}", "d\x1b[2K", "d\t"):
            report = self.read(sections(), doc=doc)
            self.all_read(report)
            self.assertIn("as the document it was built from, which is not a "
                          "name.", report)
            self.assertEqual([line for line in report.splitlines()
                              if line.startswith(forged)], [])
            self.assertNotIn("\x1b", report)

    def test_an_inventory_that_names_none(self):
        for top in ({"doc": None}, {"doc": ""}, {"doc": 0}):
            report = self.read(sections(), **top)
            self.all_read(report)
            self.assertIn("NOT CONSULTED: the inventory does not name the "
                          "document it was built from.", report)


class WhereAnEntryThatWasNotReadSaysItIs(Odd):
    """Beside a document whose text is not its source, an inventory that
    records the source's hash alone is held to its line numbers: each entry's
    heading is looked for at the line its locator gives. Every entry is
    walked, read or not."""

    def beside_a_docx(self, entries):
        return self.run_main({"doc": "d", "sections": entries,
                              "source_sha256": EXTRACTED}, extracted=True)

    def test_one_that_does_not_say_where_in_text_says_nothing(self):
        """A heading given as a list is not the line its locator names.
        Compared with it all the same, the document is "not the text this
        inventory was built from"."""
        for changed in ({"heading": ["3. Calibration Register"]},
                        {"heading": 5}, {"locator": ["d:41-60"]},
                        {"locator": 41}):
            report = self.beside_a_docx(sections(2, **changed))
            self.not_read(report, "not text]")
            self.assertNotIn("NOT CONSULTED", report)

    def test_nor_does_one_that_is_not_a_mapping(self):
        for entry in (5, None, ["3. Calibration Register"]):
            report = self.beside_a_docx([FABRIC, HYDROLOGY, entry])
            self.assertIn("inventory: 2 of 3 sections read", report)
            self.assertNotIn("NOT CONSULTED", report)

    def test_one_that_says_it_in_text_is_still_held_to_it(self):
        report = self.beside_a_docx(sections(2, locator="d:45-60",
                                             capabilities=None))
        self.not_read(report, "its 'capabilities' is null, not a list")
        self.assertIn("NOT CONSULTED: the frozen text of d is not the text "
                      "this inventory was built from: line 45 is not the "
                      "heading the inventory has there", report)


class AValueNestedDeeperThanPythonWillPrint(Odd):
    """A hundred thousand lists, each inside the last, in places where
    nothing says what a value has to be. Python 3.14 parses that and cannot
    then turn it into text, which is what each of these places did with it.
    An older Python cannot parse it and the file is refused. Neither is a
    traceback."""

    DEEP = "[" * 100000 + "]" * 100000

    def outcome(self, held, *extra, **how):
        """The report on an inventory with DEEP where `held` says "DEEP", or
        None where this Python refused the file."""
        report = self.run_main(json.dumps(
            {"doc": "d", "runs": 3, "source_sha256": self.freeze(), **held}
        ).replace('"DEEP"', self.DEEP), *extra, **how)
        if self.status == 1:
            self.assertEqual(
                self.stderr, "NOT RUN: inventory.json is nested more deeply "
                             "than this Python can read.\n")
            return None
        self.assertEqual(self.status, 0)
        return report

    def test_as_the_name_of_the_document(self):
        report = self.outcome({"doc": "DEEP", "sections": SOUND})
        if report is not None:
            self.assertIn("gives a list as the document it was built from",
                          report)

    def test_as_why_a_section_was_skipped(self):
        report = self.outcome({"sections": [
            FABRIC, HYDROLOGY, {"error": "DEEP", "skipped": True}]})
        if report is not None:
            self.not_read(report, "skipped, and the inventory does not say why")

    def test_as_the_quote_of_a_claim(self):
        report = self.outcome(
            {"sections": sections(0, evidence_claims=[
                {**CLAIM, "points_to": "Section 9", "quote": "DEEP"}])},
            "--out", "candidates.csv")
        if report is not None:
            self.all_read(report)

    def test_as_the_heading_or_the_locator_of_an_entry_beside_a_docx(self):
        for key in ("heading", "locator"):
            report = self.outcome(
                {"sections": sections(2, **{key: "DEEP"}),
                 "source_sha256": EXTRACTED}, extracted=True)
            if report is not None:
                self.not_read(report, f"its '{key}' is a list, not text")


class TextThatNothingCanEncode(Odd):
    """Half of a surrogate pair. JSON spells it "\\ud83d", nothing stops a
    model's reply from holding one, and it is text of the right kind in every
    place. Printed to a terminal or a pipe it raised, and so did writing the
    candidate file. It raises as well where a text is hashed for the
    embedding cache and where a prompt is hashed for a model call, which is
    every run that is not given --no-embed. So it is written as its escape
    where the text is read, before anything is done with it."""

    LONE = "\ud83d"

    def test_in_a_claim_and_in_what_it_points_to(self):
        """And in where its section says it is, which is printed beside the
        finding."""
        report = self.run_main(
            {"doc": "d", "sections": sections(
                0, locator=f"d:1-20{self.LONE}", evidence_claims=[
                    {"claim": f"Calibration drift {self.LONE} limits are recorded",
                     "points_to": f"Section 9{self.LONE}", "quote": "q"}]),
             "source_sha256": self.freeze()}, encoded=True)
        self.assertEqual(self.status, 0)
        self.assertIn("Section 9\\ud83d", report)
        self.assertIn("      d:1-20\\ud83d ", report)

    def test_in_the_heading_of_a_section_that_was_not_read(self):
        report = self.run_main(
            {"doc": "d", "sections": [FABRIC, HYDROLOGY, {
                "error": "x", "heading": f"3. Calibration {self.LONE} Register",
                "locator": "d:41-60"}],
             "source_sha256": self.freeze()}, encoded=True)
        self.assertEqual(self.status, 0)
        self.assertIn("3. Calibration \\ud83d Register (d:41-60)", report)

    def test_in_why_a_section_was_skipped(self):
        report = self.run_main(
            {"doc": "d", "sections": [FABRIC, HYDROLOGY, {
                "error": f"not read: {self.LONE} --limit 2", "skipped": True,
                "heading": "3. Calibration Register", "locator": "d:41-60"}],
             "source_sha256": self.freeze()}, encoded=True)
        self.assertEqual(self.status, 0)
        self.assertIn("  [not read: \\ud83d --limit 2]", report)

    def test_in_what_is_embedded_and_in_what_is_put_to_a_model(self):
        """Every capability, output and deferral is embedded, and
        --adjudicate puts each pair to a model with its headings, parties
        and quotes. Under --authority-as-dataflow what a section consumes is
        embedded as well."""
        lone = self.LONE
        entries = [
            {**FABRIC, "heading": f"1. Sensor {lone} Fabric",
             "produces": [f"calibrated {lone} gauge readings"],
             "capabilities": [f"ingest {lone} gauge readings"],
             "defers_to": [{**PASSED_ON, "to": f"Hydrology {lone} Model",
                            "quote": f"deferred {lone}"}]},
            {**HYDROLOGY,
             "consumes": [{"name": f"calibrated {lone} gauge readings"}],
             "authority": [{**OWNS, "capability": f"threshold {lone} adjudication",
                            "action": f"decides {lone}", "quote": f"q {lone}"}],
             "defers_to": [{**PASSED_ON, "to": "Alerting Service"}]},
            {**REGISTER, "locator": f"d:41-60{lone}",
             "authority": [{**OWNS, "owner": f"Alerting {lone} Service"}]}]
        report = self.run_main(
            {"doc": "d", "runs": 3, "sections": entries,
             "source_sha256": self.freeze()}, "--adjudicate",
            endpoints=True, encoded=True)
        self.assertEqual(self.status, 0)
        self.assertIn("inventory: 3 of 3 sections read", report)
        # Both kinds of pair were put to the client, which hashed each prompt.
        self.assertIn("1 authority pairs", report)
        self.assertIn("1 deferral pairs", report)
        self.again()
        report = self.run_main(
            {"doc": "d", "runs": 3, "sections": entries,
             "source_sha256": self.freeze()}, "--authority-as-dataflow",
            endpoints=True, encoded=True)
        self.assertEqual(self.status, 0)
        self.assertIn("2 produces/consumes entries re-admitted", report)

    def test_in_the_name_of_the_document(self):
        """It is not a name, and is not looked up."""
        report = self.run_main(
            {"doc": f"d{self.LONE}", "sections": SOUND,
             "source_sha256": self.freeze()}, encoded=True)
        self.assertEqual(self.status, 0)
        self.assertIn("as the document it was built from, which is not a name.",
                      report)

    def test_and_in_the_file_of_candidates(self):
        self.run_main(
            {"doc": "d", "sections": sections(0, evidence_claims=[
                {"claim": "Calibration drift limits are recorded",
                 "points_to": f"Section 9{self.LONE}", "quote": "q"}]),
             "source_sha256": self.freeze()}, "--out", "candidates.csv")
        self.assertEqual(self.status, 0)
        with open(os.path.join(self.project, "candidates.csv"),
                  encoding="utf-8") as handle:
            self.assertIn("Section 9\\ud83d", handle.read())


class HowManyPassesWereAskedFor(Odd):
    """`runs`, which the file says of itself. It decides one list in the
    report and no finding."""

    NOTE = "as the passes it asked for, which is not a whole"

    def test_runs_that_are_not_a_whole_number_of_one_or_more(self):
        """"runs": "three" raised where a section's passes were compared with
        it."""
        for runs, shown in (("three", '"three"'), (True, "true"), ([3], "a list"),
                            (0, "0"), (-3, "-3"), (2.5, "2.5")):
            report = self.read(sections(), runs=runs)
            self.all_read(report)
            self.assertIn(f"The inventory gives {shown} {self.NOTE}", report)
            self.assertNotIn("READ IN FEWER", report)

    def test_runs_that_are_not_given_are_not_remarked_on(self):
        for top in ({"runs": None}, {}):
            entries = sections()
            report = self.run_main({"doc": "d", "sections": entries,
                                    "source_sha256": self.freeze(), **top})
            self.all_read(report)
            self.assertNotIn(self.NOTE, report)

    def test_a_number_of_passes_is_still_what_a_section_s_are_compared_with(self):
        report = self.read(sections(1, passes=2, full_passes=2))
        self.assertIn("READ IN FEWER than the 3 passes", report)
        self.assertIn("[2 of 3]", report)
        self.assertNotIn(self.NOTE, report)

    def test_one_pass_is_a_number_of_passes(self):
        report = self.read(sections(), runs=1)
        self.all_read(report)
        self.assertNotIn(self.NOTE, report)
        self.assertNotIn("READ IN FEWER", report)


# -- what inventory.py writes ------------------------------------------------
#
# The shape is said twice: by inventory.catalogue(), which writes an entry,
# and by synthesize.unreadable(), which decides whether an entry is as
# catalogue() writes one. Nothing held the second to the first. An earlier
# version of unreadable() called "not as inventory.py writes it" 33 shapes of
# entry that catalogue() does write, from replies its own validators accept.

class Model:
    """In place of the model, with the validator applied as llm.Client applies
    it: a reply the validator refuses is a call that failed."""

    def __init__(self, full=None, minimal=None):
        self.full, self.minimal = full, minimal

    def ask(self, system, user, validate=None, label=""):
        reply = self.minimal if system == inventory.MINIMAL_SYSTEM else self.full
        error = "no reply" if reply is None else validate(reply)
        if error:
            raise LLMError(error)
        return copy.deepcopy(reply)


def reply(index, **lists):
    """What a model says of sound section `index`, with lists replaced."""
    return {**{key: copy.deepcopy(SOUND[index][key]) for key in inventory.FIELDS},
            **copy.deepcopy(lists)}


def brief(index, **lists):
    """The same for the fallback, which asks for three of the lists."""
    return {**{key: copy.deepcopy(SOUND[index][key])
               for key in ("capabilities", "authority", "defers_to")},
            **copy.deepcopy(lists)}


def catalogued(index, *clients):
    """The entry inventory.py writes for sound section `index`."""
    first, last = SOUND[index]["locator"].split(":")[1].split("-")
    return inventory.catalogue(
        {"heading": SOUND[index]["heading"], "start": int(first),
         "end": int(last), "text": "Body text."}, "d", list(clients))


# (what the model said, the section, its reply to the full schema, its reply
# to the fallback, how many items of the entry cannot be read)
SAID = (
    ("a capability named with a number", 2,
     reply(2, capabilities=[{"name": 5}]), None, 1),
    ("a capability named with a list", 2,
     reply(2, capabilities=[{"name": ["calibration", "drift limits"]}]), None, 1),
    ("a claim that is a sentence", 0,
     reply(0, evidence_claims=["Drift limits are recorded in Section 3"]),
     None, 1),
    ("a claim under another key", 0,
     reply(0, evidence_claims=[{"statement": "Drift limits are recorded",
                                "points_to": "Section 3"}]), None, 1),
    ("a claim of null", 0,
     reply(0, evidence_claims=[{"claim": None, "points_to": "Section 3"}]),
     None, 1),
    ("a pointer that is a number", 0,
     reply(0, evidence_claims=[{**CLAIM, "points_to": 3}]), None, 1),
    ("a pointer that is a mapping", 0,
     reply(0, evidence_claims=[{**CLAIM, "points_to": {"section": "3"}}]),
     None, 1),
    ("a pointer that is a list with a number in it", 0,
     reply(0, evidence_claims=[{**CLAIM, "points_to": ["Section 3", 4]}]),
     None, 1),
    ("a claim that is a list of two texts", 0,
     reply(0, evidence_claims=[{**CLAIM, "claim": ["Drift limits",
                                                   "are recorded"]}]), None, 1),
    ("null among the claims", 0, reply(0, evidence_claims=[None]), None, 0),
    ("an owner that is a list of two", 1,
     reply(1, authority=[{**OWNS, "owner": ["Hydrology Model",
                                            "Alerting Service"]}]), None, 1),
    ("an owned capability that is a list", 1,
     reply(1, authority=[{**OWNS, "capability": ["threshold adjudication"]}]),
     None, 1),
    ("an action that is a list of two verbs", 1,
     reply(1, authority=[{**OWNS, "action": ["decides", "owns"]}]), None, 1),
    ("an authority quote that is a list of two", 1,
     reply(1, authority=[{**OWNS, "quote": ["q one", "q two"]}]), None, 0),
    ("an authority quote that is a number", 1,
     reply(1, authority=[{**OWNS, "quote": 7}]), None, 0),
    ("a deferral that is a sentence", 0,
     reply(0, defers_to=["Hydrology Model"]), None, 1),
    ("a deferral that does not say what", 0,
     reply(0, defers_to=[{"to": "Hydrology Model"}]), None, 1),
    ("a deferral of null", 0,
     reply(0, defers_to=[{"capability": None, "to": "Hydrology Model"}]),
     None, 1),
    ("a deferral to a list of two", 0,
     reply(0, defers_to=[{**PASSED_ON, "to": ["Hydrology Model",
                                              "Alerting Service"]}]), None, 1),
    ("a deferral quote that is a list", 0,
     reply(0, defers_to=[{**PASSED_ON, "quote": ["q"]}]), None, 0),
    ("an input given by its name", 1,
     reply(1, consumes=[{"name": "calibrated gauge readings"}]), None, 0),
    ("null among the inputs", 1,
     reply(1, consumes=["calibrated gauge readings", None]), None, 0),
    ("a number among the outputs", 0,
     reply(0, produces=["calibrated gauge readings", 5]), None, 1),
    ("an output given by its name", 0,
     reply(0, produces=[{"name": "calibrated gauge readings"}]), None, 0),
    ("to the fallback, a capability with no name", 2, None,
     brief(2, capabilities=[{"capability": "calibration drift limits"}]), 1),
    ("to the fallback, a capability that is a number", 2, None,
     brief(2, capabilities=[5]), 1),
    ("to the fallback, a capability named null", 2, None,
     brief(2, capabilities=[{"name": None, "quote": "q"}]), 1),
    ("to the fallback, an authority entry that is a sentence", 1, None,
     brief(1, authority=["Hydrology Model decides threshold adjudication"]), 1),
    ("to the fallback, an authority entry with no owner", 1, None,
     brief(1, authority=[{"capability": "threshold adjudication",
                          "polarity": "owns"}]), 1),
    ("to the fallback, an owner of null", 1, None,
     brief(1, authority=[{**OWNS, "owner": None}]), 1),
    ("to the fallback, an authority entry that does not say what", 1, None,
     brief(1, authority=[{"owner": "Hydrology Model", "polarity": "owns"}]), 1),
    ("to the fallback, a deferral that is a sentence", 0, None,
     brief(0, defers_to=["Hydrology Model"]), 1),
    ("to the fallback, inputs it was not asked for", 1, None,
     {**brief(1), "consumes": [5]}, 1),
    ("to the fallback, a capability that is its name alone", 2, None,
     brief(2, capabilities=["calibration drift limits"]), 0),
)

# And four with nothing where something is asked, which the validators
# accept as well: text that is blank, a mapping that is empty.
SAID_NOTHING = (
    ("an input that is blank", 1,
     reply(1, consumes=["calibrated gauge readings", ""]), None, 0),
    ("a deferral to an empty mapping", 0,
     reply(0, defers_to=[{**PASSED_ON, "to": {}}]), None, 0),
    ("to the fallback, a capability named with nothing", 2, None,
     brief(2, capabilities=[{"name": "", "quote": "Calibration drift limits"}]),
     1),
    ("to the fallback, an owner that is blank", 1, None,
     brief(1, authority=[{**OWNS, "owner": " "}]), 1),
)


class WhatInventoryPyWrites(Odd):
    """Every entry here is made by inventory.catalogue() itself, from a
    reply its own validator accepts."""

    def test_every_way_it_reads_a_section_is_an_entry_this_reads(self):
        """One full pass, three, one of them failing, the fallback alone,
        the fallback and a later full pass or two."""
        full, short = reply(1), brief(1)
        for clients in ([Model(full)], [Model(full), Model(full), Model(full)],
                        [Model(full), Model(), Model(full)],
                        [Model(None, short), Model(), Model()],
                        [Model(None, short), Model(full)],
                        [Model(None, short), Model(full), Model(full)]):
            entry = catalogued(1, *clients)
            self.assertEqual(synthesize.unreadable(entry), "", entry)
            report = self.read([FABRIC, entry, REGISTER])
            self.assertIn("inventory: 3 of 3 sections read", report)
            self.assertNotIn("NOT READ", report)

    def test_whatever_a_validator_lets_a_model_say_the_section_is_read(self):
        """And the item that cannot be read is set aside, where there is
        one."""
        self.assertEqual(len(SAID), 34)
        for said, index, full, short, aside in SAID + SAID_NOTHING:
            entry = catalogued(index, Model(full, short))
            self.assertNotIn("error", entry, said)
            self.assertEqual(synthesize.unreadable(entry), "", said)
            for extra in ((), ("--adjudicate",)):
                report = self.read(
                    SOUND[:index] + [entry] + SOUND[index + 1:], *extra)
                self.assertEqual(self.status, 0, said)
                self.assertIn("inventory: 3 of 3 sections read", report, said)
                self.assertEqual(
                    f"ITEMS NOT READ: {aside}, in 1 section(s)." in report,
                    bool(aside), said)
                self.assertEqual("ITEMS NOT READ" in report, bool(aside), said)

    def test_the_file_it_writes_is_one_this_reads(self):
        """With the hash of the text and the lines that are in no section,
        which inventory.py has written since 2026-10-04."""
        digest = self.freeze()
        written = inventory.record(
            "d", {"source_sha256": digest, "text_sha256": digest}, 3,
            [catalogued(index, *(Model(reply(index)) for _ in range(3)))
             for index in range(3)], [])
        report = self.run_main(written)
        self.all_read(report)
        self.assertIn("no line that holds text is outside those 3 sections",
                      report)
        self.assertEqual(self.findings(report), {})

    def test_a_section_it_could_not_read_is_one_this_did_not_read(self):
        entry = catalogued(2, Model(), Model())
        self.not_read(self.read([FABRIC, HYDROLOGY, entry]),
                      "    3. Calibration Register (d:41-60)  [extraction "
                      "failed]")

    def test_nor_one_that_a_limit_stopped_it_before(self):
        _, beyond = inventory.limited(
            [{"heading": entry["heading"], "start": 1, "end": 20, "text": "t"}
             for entry in SOUND], "d", 2)
        report = self.read([FABRIC, HYDROLOGY] + beyond)
        self.not_read(report, "--limit 2")


# -- every place in the file, with every odd value ---------------------------
#
# The shape, said a second time and another way: as_written() and set_aside()
# are each one expression, where synthesize.unreadable() and its item checks
# are sequences of reasons. They were written apart, and the test below holds
# main() to these.

VALUES = (None, True, 0, 5, -1, 2.5, "", "text", "two\nlines", [], [None],
          ["a"], {}, {"x": 1})


def text(held):
    return isinstance(held, str)


def whole(held):
    return isinstance(held, int) and not isinstance(held, bool)


def nothing_or(check, held):
    return held is None or held == [] or held == {} or check(held)


def said(held):
    """Text with something in it."""
    return text(held) and bool(held.strip())


def by_name(item):
    """A capability, an input or an output: its name, alone or under `name`."""
    return text(item) or (isinstance(item, dict) and said(item.get("name")))


def counted(entry):
    """Whether an entry counts its passes as inventory.py does, or not at
    all."""
    there = [key in entry for key in ("passes", "full_passes")]
    if not any(there):
        return True
    return all(there) and whole(entry["passes"]) and whole(entry["full_passes"]) \
        and entry["passes"] >= 1 \
        and entry["full_passes"] == entry["passes"] - (
            1 if entry.get("degraded") is True else 0)


def as_written(entry):
    """Whether an entry is one inventory.py could have written for a section
    it read."""
    return isinstance(entry, dict) and "error" not in entry \
        and not entry.get("skipped") \
        and all(text(entry.get(key)) and "\n" not in entry[key]
                for key in ("heading", "locator")) \
        and isinstance(entry.get("degraded", False), bool) and counted(entry) \
        and all(isinstance(entry.get(key), list) for key in LISTS) \
        and all(isinstance(entry[key], list)
                for key in ("identifiers", "deferred") if key in entry)


ITEM = {
    "capabilities": by_name,
    "evidence_claims": lambda item: isinstance(item, dict)
    and said(item.get("claim")) and (
        nothing_or(text, item.get("points_to"))
        or (isinstance(item["points_to"], list)
            and all(map(text, item["points_to"])))),
    "authority": lambda item: isinstance(item, dict)
    and said(item.get("capability")) and said(item.get("owner"))
    and nothing_or(text, item.get("action"))
    and item.get("polarity") in (None, "owns", "excludes"),
    "defers_to": lambda item: isinstance(item, dict)
    and said(item.get("capability")) and nothing_or(text, item.get("to")),
    "consumes": by_name, "produces": by_name}


def set_aside(entry):
    """How many items of a section that was read cannot be. A null is not an
    item, and nor is text with nothing in it."""
    return sum(item is not None and not (text(item) and not item.strip())
               and not ITEM[key](item)
               for key in LISTS for item in entry[key])


# The keys an item of one list or another goes by, and the marks this program
# puts on an item itself. Each is tried on every item, where it belongs and
# where it does not: a key that belongs to another list is not checked on this
# one, and must not be read off it either.
KEYS = ("name", "claim", "points_to", "capability", "owner", "action",
        "polarity", "to", "quote", "value", "label", "_heading", "_locator",
        "_component")

# A planted defect of each class, so that a score reads every finding too.
PLANTED = {"defects": [
    {"id": "GT-D3-001", "class": "D3", "title": "t",
     "anchor": "Calibration drift limits are recorded"},
    {"id": "GT-D5-001", "class": "D5", "title": "t", "lines": [1, 60]},
    {"id": "GT-D6-001", "class": "D6", "title": "t",
     "anchor": "threshold adjudication"},
    {"id": "GT-D8-001", "class": "D8", "title": "t", "lines": [1, 60]}]}


def places(data):
    """Every path at which one value of the file can be replaced or taken
    out, or a key added to an item."""
    yield ()
    for key in ("doc", "runs", "sections", "source_sha256", "text_sha256",
                "unread_lines"):
        yield (key,)
    for index, entry in enumerate(data["sections"]):
        yield ("sections", index)
        for key in list(entry) + ["error", "skipped", "degraded"]:
            yield ("sections", index, key)
            held = entry.get(key)
            if isinstance(held, list) and held:
                yield ("sections", index, key, 0)
                if isinstance(held[0], dict):
                    for inner in dict.fromkeys(list(held[0]) + list(KEYS)):
                        yield ("sections", index, key, 0, inner)


def put(data, path, value):
    """`data` with `value` at `path`, or with what is there taken out."""
    if not path:
        return value
    target = data
    for step in path[:-1]:
        target = target[step]
    if value is not GONE:
        target[path[-1]] = value
    elif isinstance(target, list) or path[-1] in target:
        del target[path[-1]]
    return data


def changes(sound):
    """(where, the inventory) for every one change to a sound inventory that
    leaves a different one."""
    for path in places(sound):
        for value in VALUES + (GONE,):
            if path or value is not GONE:
                data = put(copy.deepcopy(sound), path,
                           value if value is GONE else copy.deepcopy(value))
                if data != sound:
                    yield (f"{'.'.join(map(str, path)) or 'the file'} = "
                           f"{'(taken out)' if value is GONE else repr(value)}",
                           data)


def shape(data):
    """What this file's statement of the shape says of an inventory: None
    where it is not one, or (the entries that are sections as inventory.py
    writes them, how many of their items cannot be read)."""
    if not isinstance(data, dict) or not isinstance(data.get("sections"), list) \
            or not data["sections"]:
        return None
    read = [entry for entry in data["sections"] if as_written(entry)]
    return read, sum(map(set_aside, read))


NOTHING_READ = "NOTHING WAS READ: 0 of "


class EveryPlaceInTheFile(Odd):

    def test_no_odd_value_anywhere_raises_or_is_read_as_a_sound_section(self):
        """One value changed at a time, at every place of a sound inventory.
        The run is refused when the file is not an inventory. Otherwise it
        says how many entries it read, and how many of their items it did
        not, and those are the numbers as_written() and set_aside() give.
        It never raises: run_main() fails the test if it does."""
        sound = {"doc": "d", "runs": 3, "sections": copy.deepcopy(SOUND),
                 "source_sha256": self.freeze()}
        with open(os.path.join(self.project, "ground-truth.yaml"), "w") as handle:
            handle.write("defects: []\n")
        fake = types.SimpleNamespace(safe_load=lambda handle: PLANTED)
        tried = 0
        for where, data in changes(sound):
            # Once with every pair of owners put to a model, which reads the
            # entries again; once beside a document whose text is not its
            # source, where each entry is looked for at its line; once with
            # the candidates written to a file and scored, which reads every
            # finding twice more; and once as it is run by default.
            self.run_main(data, "--adjudicate")
            if isinstance(data, dict):
                self.run_main({**data, "source_sha256": EXTRACTED},
                              extracted=True)
            with mock.patch.dict(sys.modules, {"yaml": fake}):
                self.run_main(data, "--out", "candidates.csv",
                              "--ground-truth", "ground-truth.yaml")
            report = self.run_main(data)
            tried += 1
            if shape(data) is None:
                self.assertEqual((self.status, report), (1, ""), where)
                continue
            read, aside = shape(data)
            self.assertIn(f"inventory: {len(read)} of {len(data['sections'])} "
                          f"sections read", report, where)
            # With no section read there is no result, and the run says so.
            self.assertEqual(
                (self.status, self.stderr.startswith(NOTHING_READ)),
                (0, False) if read else (1, True), where)
            self.assertEqual("ITEMS NOT READ" in report, bool(aside), where)
            if aside:
                self.assertIn(f"ITEMS NOT READ: {aside}, in ", report, where)
        self.assertGreater(tried, 1500)


def against(path):
    """Print what the synthesize.py at `path` does with each inventory the
    test above makes: the figures at the top of this file. For the code as
    it was before the shape was checked, from the root of the repository:

        git show a7ee99c:synthesize.py > /tmp/as-it-was.py
        python -m tests.test_malformed_inventory --against /tmp/as-it-was.py
    """
    import collections
    import importlib.util
    spec = importlib.util.spec_from_file_location("as_it_was", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    case = EveryPlaceInTheFile(
        "test_no_odd_value_anywhere_raises_or_is_read_as_a_sound_section")
    case.setUp()
    try:
        sound = {"doc": "d", "runs": 3, "sections": copy.deepcopy(SOUND),
                 "source_sha256": case.freeze()}
        tally = collections.Counter()
        for _, data in changes(sound):
            said = shape(data)
            kind = "not an inventory" if said is None \
                else "an entry that is not a section" \
                if len(said[0]) < len(data["sections"]) \
                else "an item that cannot be read" if said[1] else "sound"
            with open(os.path.join(case.project, "inventory.json"), "w",
                      encoding="utf-8") as handle:
                json.dump(data, handle)
            out, argv = io.StringIO(), sys.argv
            sys.argv = ["synthesize.py", "--project", case.project, "--no-embed"]
            try:
                with contextlib.redirect_stdout(out), \
                        contextlib.redirect_stderr(io.StringIO()):
                    status = module.main()
                did = "refused" if status and not out.getvalue() else \
                    "said that something was not read" \
                    if "NOT READ" in out.getvalue() \
                    else "reported with no word of anything unread"
            except Exception:       # noqa: BLE001: the raise is what is counted
                did = "raised"
            finally:
                sys.argv = argv
            tally[kind, did] += 1
    finally:
        case.tearDown()
    print(f"{len(list(places(sound)))} places, {len(VALUES)} values put at "
          f"each and each key taken out: {sum(tally.values())} inventories "
          f"that differ from the sound one")
    for (kind, did), count in sorted(tally.items()):
        print(f"{count:7}  {kind}: {did}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--against"]:
        against(sys.argv[2])
    else:
        unittest.main()
