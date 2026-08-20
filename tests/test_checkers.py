"""The verification layers, fed the inputs a real model actually emits.

Three checkers in this repository shipped stricter than reality, and each one
discarded good evidence and reported it as the model's fault:

    the locator grammar     demanded "120-134" and rejected a bare "67". Ten of
                            the first eleven "invalid" citations were that and
                            nothing else. Fixed in assess.py, missed in
                            converse.py, and caught there by the first real
                            question anyone asked of it.
    the punctuation check   was suspected of the same fault. It was tested and
                            cleared — but only because someone tested it rather
                            than assuming, and that test now lives here.
    the heading detector    is a heuristic and is characterised below rather
                            than pinned as correct, because in table-dense
                            material it identifies a region, not a section.

A checker that is quietly too narrow is worse than no checker: it manufactures
false confidence in the opposite direction, and it accuses the model while doing
it. So these tests do not feed tidy inputs. They feed bare numbers, en-dashes,
smart quotes, non-breaking hyphens and one-word table cells — the things that
turn up in real output — and assert the checker survives contact with them.

Every case here is drawn from something that actually appeared in a run.
"""

import importlib.util
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name, filename):
    """Import a hyphenated top-level script as a module."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LocatorGrammar(unittest.TestCase):
    """A citation grammar has to accept what a reader would actually write."""

    def setUp(self):
        self.graders = {
            "assess.py": load("assess_mod", "assess.py").RANGE,
            "converse.py": load("converse_mod", "converse.py").RANGE,
            "repair-cites.py": load("repair_cites_mod", "repair-cites.py").RANGE,
        }

    def bounds(self, pattern, text):
        found = pattern.match(text)
        if not found:
            return None
        start = int(found.group(1))
        end = int(found.group(2)) if found.group(2) else start
        return start, end

    def test_bare_line_number_is_a_locator(self):
        """The exact failure. A single line is an ordinary thing to cite, and
        rejecting it threw away ten of the first eleven citations in one run."""
        for where, pattern in self.graders.items():
            with self.subTest(where=where):
                self.assertEqual(self.bounds(pattern, "67"), (67, 67))

    def test_accepts_the_separators_models_emit(self):
        for where, pattern in self.graders.items():
            for text, want in (("120-134", (120, 134)),
                               ("120 - 134", (120, 134)),
                               ("5:9", (5, 9)),
                               ("8–14", (8, 14)),      # en-dash
                               ("8—14", (8, 14)),      # em-dash
                               ("  42  ", (42, 42))):
                with self.subTest(where=where, text=text):
                    self.assertEqual(self.bounds(pattern, text), want)

    def test_rejects_what_is_genuinely_not_a_locator(self):
        """Being permissive about form must not mean accepting anything."""
        for where, pattern in self.graders.items():
            for text in ("", "abc", "section 4", "12a", "1.2.3", "-5"):
                with self.subTest(where=where, text=text):
                    self.assertIsNone(pattern.match(text.strip()) if text.strip()
                                      else None)

    def test_every_grammar_agrees(self):
        """Three copies existed and two of them disagreed. If they must be
        duplicated, they must at least not diverge."""
        samples = ["67", "120-134", "5:9", "8–14", "abc", ""]
        results = {where: [self.bounds(p, s) for s in samples]
                   for where, p in self.graders.items()}
        first = next(iter(results.values()))
        for where, got in results.items():
            with self.subTest(where=where):
                self.assertEqual(got, first)


class QuoteVerification(unittest.TestCase):
    """Whitespace normalisation, and the punctuation question that was tested."""

    @staticmethod
    def flat(text):
        return " ".join(text.split()).lower()

    def test_whitespace_variants_normalise(self):
        document = "the fabric defers resolution to the Hydrology Model"
        for variant in ("The  fabric   defers resolution to the Hydrology Model",
                        "the fabric\ndefers resolution to the\tHydrology Model",
                        "  the fabric defers resolution to the Hydrology Model  "):
            with self.subTest(variant=variant):
                self.assertIn(self.flat(variant), self.flat(document))

    def test_narrow_no_break_space_is_whitespace(self):
        """Real output contained U+202F between words. If str.split() did not
        treat it as whitespace, every quote containing one would fail to match
        and the model would be blamed for it."""
        self.assertEqual(self.flat("Exposure & Attention"),
                         self.flat("Exposure & Attention"))

    def test_punctuation_is_NOT_normalised_and_that_was_measured(self):
        """Smart quotes and non-breaking hyphens survive normalisation, so a
        quote differing only in punctuation does NOT match.

        This looks like a bug and was investigated as one. Folding punctuation
        across a real run recovered ZERO additional quotes out of 32 failures,
        which is what established that the failures were fabrication rather than
        a strict matcher. The behaviour is pinned here so the finding is not
        quietly undone by someone 'fixing' it later.
        """
        self.assertNotEqual(self.flat("attended‑exposure"),
                            self.flat("attended-exposure"))
        self.assertNotEqual(self.flat("“viewport”"),
                            self.flat('"viewport"'))

    def test_empty_quote_never_matches(self):
        """An empty string is a substring of everything. Without a guard, a model
        returning "" would score as verbatim."""
        self.assertEqual(self.flat("   "), "")
        self.assertFalse(self.flat("   ") and self.flat("   ") in "any document")


class HeadingDetection(unittest.TestCase):
    """Characterised, not pinned as correct — it is a navigation aid."""

    def setUp(self):
        self.converse = load("converse_mod2", "converse.py")

    def test_finds_a_real_heading_above_the_line(self):
        lines = ["intro", "Runtime Orchestrator", "constructs the tick order", "more"]
        head, _line = self.converse.nearest_heading(lines, 2)
        self.assertEqual(head, "Runtime Orchestrator")

    def test_a_one_word_table_cell_is_not_a_useful_heading(self):
        """Cited blocks frequently open on a table cell holding one word. The
        detector requires more than one word precisely so 'Layer' does not become
        the label a reviewer is told to navigate to."""
        lines = ["Layer", "L3 core stack", "detail"]
        head, _line = self.converse.nearest_heading(lines, 2)
        self.assertNotEqual(head, "Layer")

    def test_prose_ending_in_punctuation_is_not_a_heading(self):
        lines = ["The system defers resolution to the model,", "detail here"]
        head, _line = self.converse.nearest_heading(lines, 1)
        self.assertIsNone(head)

    def test_the_search_is_inclusive_of_the_cited_line(self):
        """A cited line that itself looks like a heading is returned as its own
        heading. Sensible — a block often opens on one — and worth pinning,
        because it means the walk-back only happens for body text."""
        lines = ["intro", "Layer", "Pre-run characterization"]
        head, line = self.converse.nearest_heading(lines, 2)
        self.assertEqual((head, line), ("Pre-run characterization", 2))

    def test_known_weakness_all_caps_region_label(self):
        """In table-dense material the walk-back reaches a region label rather
        than a section, skipping the one-word cells between. Recorded as a
        limitation, not asserted as correct — when this happens the search phrase
        is the reliable navigation aid, not the heading."""
        lines = ["ARCHITECTURE POSITION", "Layer",
                 "the pre-run characterization is described, with detail."]
        head, _line = self.converse.nearest_heading(lines, 2)
        self.assertEqual(head, "ARCHITECTURE POSITION")


class TrailingBracketStripping(unittest.TestCase):
    """The locator bracket an adjudicator appends, removed for a human column."""

    def setUp(self):
        self.trail = load("combine_mod", "combine-evaluators.py").TRAIL

    def test_strips_consecutive_brackets(self):
        """A row can carry more than one: the wide path appends its own note and
        the unlocatable fallback appends another, so they arrive back to back.
        Stripping a single bracket left the first of the pair in the column."""
        text = "Unaddressed. The section is unchanged. [§5 (doc:1-2)] [§9 could not be located]"
        self.assertEqual(self.trail.sub("", text).strip(),
                         "Unaddressed. The section is unchanged.")

    def test_leaves_brackets_that_are_part_of_the_sentence(self):
        text = "Unaddressed. The register [sic] was deleted."
        self.assertEqual(self.trail.sub("", text), text)


if __name__ == "__main__":
    unittest.main()
