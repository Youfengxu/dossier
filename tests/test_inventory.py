"""inventory.py: what an entry has to say about how it was read.

synthesize.py asserts absences, and its docstring gives the licence for it: the
inventory was built by reading 100% of the document. inventory.py is what makes
that true or does not, and it recorded less than it knew:

    a section that failed   was stored as {"error": "..."} and nothing else. No
                            heading, no lines: the inventory could say that
                            some section had gone unread and not which one.
    the fallback schema     asks for three of the eight fields and stores the
                            other five as empty lists. A section read that way
                            says "consumes nothing" where the truth is that
                            nothing asked it.
    the extra passes        are the reason --runs defaults to 3: at one pass the
                            fixture's deferral cycle is invisible. A second or
                            third pass that failed was swallowed, and a section
                            read once looked the same as one read three times.

No model: the client is a script of replies. A dict is an answer; anything else
is a call that failed.
"""

import copy
import unittest

import inventory
from llm import LLMError

SECTION = {"heading": "3. Calibration Register", "start": 41, "end": 60,
           "text": "Drift limits are held per gauge."}
FULL = {"capabilities": [{"name": "drift limits per gauge", "quote": "q"}],
        "identifiers": [], "deferred": [], "evidence_claims": [],
        "authority": [], "defers_to": [],
        "consumes": ["calibrated gauge readings"], "produces": []}
MINIMAL = {"capabilities": [{"name": "drift limits per gauge", "quote": "q"}],
           "authority": [], "defers_to": []}
FAILS = "no schema-valid reply after 2 attempts"


class Scripted:
    """A client that answers from a script, in order."""

    def __init__(self, *replies):
        self.replies = list(replies)

    def ask(self, system, user, validate=None, label=""):
        reply = self.replies.pop(0)
        if isinstance(reply, dict):
            return copy.deepcopy(reply)
        raise LLMError(f"{label}: {reply}")


class ASectionThatFailed(unittest.TestCase):

    def test_it_is_still_named(self):
        """Without this the inventory can only say that a section is missing,
        and synthesize.py reports a pointer to it as "no such section"."""
        entry = inventory.catalogue(SECTION, "d", [Scripted(FAILS, FAILS)])
        self.assertIn("error", entry)
        self.assertEqual((entry.get("heading"), entry.get("locator")),
                         ("3. Calibration Register", "d:41-60"))

    def test_and_holds_nothing_that_reads_as_content(self):
        entry = inventory.catalogue(SECTION, "d", [Scripted(FAILS, FAILS)])
        self.assertEqual(sorted(entry), ["error", "heading", "locator"])


class ThePassesBehindAnEntry(unittest.TestCase):

    def test_every_pass_that_answered_is_counted(self):
        clients = [Scripted(FULL), Scripted(FULL), Scripted(FULL)]
        self.assertEqual(inventory.catalogue(SECTION, "d", clients)["passes"], 3)

    def test_a_pass_that_failed_is_not(self):
        """It used to be swallowed: `except LLMError: pass`."""
        clients = [Scripted(FULL), Scripted(FAILS), Scripted(FULL)]
        self.assertEqual(inventory.catalogue(SECTION, "d", clients)["passes"], 2)

    def test_the_fallback_is_one_pass_and_says_which_schema_it_used(self):
        clients = [Scripted(FAILS, MINIMAL), Scripted(FAILS), Scripted(FAILS)]
        entry = inventory.catalogue(SECTION, "d", clients)
        self.assertEqual((entry["passes"], entry["full_passes"]), (1, 0))
        self.assertTrue(entry["degraded"])
        self.assertEqual(entry["consumes"], [])

    def test_a_later_full_pass_is_recorded_as_one(self):
        """The first pass fell back; the second read the section whole and its
        consumes are in the entry. That is not a section with no consumes
        extracted, and only this count tells the two apart."""
        clients = [Scripted(FAILS, MINIMAL), Scripted(FULL)]
        entry = inventory.catalogue(SECTION, "d", clients)
        self.assertEqual((entry["passes"], entry["full_passes"]), (2, 1))
        self.assertEqual(entry["consumes"], ["calibrated gauge readings"])


class WhatAReplyMayNotSay(unittest.TestCase):
    """The keys an entry uses to say how it was read belong to the inventory.
    A reply is a model's, and may hold anything."""

    def test_a_reply_carrying_an_error_key_is_not_a_failed_section(self):
        entry = inventory.catalogue(SECTION, "d",
                                    [Scripted(dict(FULL, error="none"))])
        self.assertNotIn("error", entry)
        self.assertEqual(entry["consumes"], ["calibrated gauge readings"])

    def test_nor_can_it_say_how_it_was_read_or_where(self):
        reply = dict(FULL, degraded=True, passes=9, full_passes=9,
                     heading="X", locator="Y")
        entry = inventory.catalogue(SECTION, "d", [Scripted(reply)])
        self.assertEqual(
            (entry.get("degraded"), entry["passes"], entry["full_passes"],
             entry["heading"], entry["locator"]),
            (None, 1, 1, "3. Calibration Register", "d:41-60"))


class ARunToldToStopEarly(unittest.TestCase):
    """`--limit 2` wrote the first two sections and nothing else, and the line
    above them said "reading 100% of the document". synthesize.py then called
    a pointer to the third a pointer to a section that does not exist."""

    def test_the_sections_it_stopped_before_are_recorded_as_not_read(self):
        sections = [dict(SECTION, heading=f"{n}. S", start=n * 10, end=n * 10 + 9)
                    for n in (1, 2, 3)]
        kept, beyond = inventory.limited(sections, "d", 2)
        self.assertEqual([s["heading"] for s in kept], ["1. S", "2. S"])
        self.assertEqual(
            [(e.get("heading"), e.get("locator"), e.get("skipped")) for e in beyond],
            [("3. S", "d:30-39", True)])
        self.assertIn("--limit 2", beyond[0].get("error", ""))

    def test_no_limit_leaves_nothing_out(self):
        self.assertEqual(inventory.limited([SECTION], "d", 0), ([SECTION], []))


class WhatTheRunReports(unittest.TestCase):

    def test_the_shortfall_is_counted_beside_the_failures(self):
        results = [
            {"error": "x", "heading": "1. A", "locator": "d:1-9"},
            dict(FULL, heading="2. B", locator="d:10-19", passes=3, full_passes=3),
            dict(FULL, heading="3. C", locator="d:20-29", passes=1, full_passes=1),
            dict(MINIMAL, heading="4. D", locator="d:30-39", passes=1,
                 full_passes=0, degraded=True),
            {"error": "not read: --limit 4 stopped before this section",
             "skipped": True, "heading": "5. E", "locator": "d:40-49"},
        ]
        self.assertEqual(inventory.shortfall(results, runs=3),
                         {"failed": 1, "degraded": 1, "short": 2, "skipped": 1})


if __name__ == "__main__":
    unittest.main()
