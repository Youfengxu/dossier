"""undefined.py — the corpus half: is_defined(), _loose(), and same().

Two matchers with opposite risk profiles.

is_defined() clears a nominated term. Being generous is deliberate — a false
"undefined" wastes a reviewer's time on a term the document does explain — so
the tests here are mostly about the ONE place it was deliberately tightened: a
definitional copula needs an article after it. The loose version cleared 70 of
112 nominations and the detector reported almost nothing.

same() decides whether a reported term counts as a register finding. It replaced
a version that joined all output into one string and counted a hit if any word
longer than five characters appeared anywhere in it, so emitting more candidates
raised the score by itself. Its whole job is the containment rule, and the rule
has a floor: "execution order" is the same finding as "frozen execution order",
bare "order" is not.
"""

import unittest

import undefined
from tests.support import lift


class Loose(unittest.TestCase):
    """_loose() — hyphens and case are not meaning."""

    def test_hyphenated_and_spaced_spellings_converge(self):
        # The document writes a component one way in its heading and another in
        # prose. Matching literally left 181 uses at the top of the output as
        # undefined, against a section that defines it.
        self.assertEqual(undefined._loose("Platform-Adapter"),
                         undefined._loose("Platform Adapter"))

    def test_punctuation_runs_collapse_to_one_space_and_edges_strip(self):
        self.assertEqual(undefined._loose("  (Sensor/Fabric).  "),
                         "sensor fabric")


class IsDefined(unittest.TestCase):
    """Any one shape of definition clears the term."""

    LINES = [
        "## 4. Calibration baseline",
        "A calibration baseline is a recorded parameter set for one gauge.",
        "The drift budget is stored in the calibration register.",
        "tick advancement: the unit by which the orchestrator moves state",
    ]

    def setUp(self):
        self.lowered = "\n".join(self.LINES).lower()
        self.headings = "## 4. calibration baseline"

    def check(self, term, glossary=""):
        return undefined.is_defined(term, self.lowered, self.LINES,
                                    self.headings, glossary)

    def test_a_heading_of_its_own_clears_the_term(self):
        self.assertEqual(self.check("calibration baseline"),
                         "has its own heading")

    def test_a_heading_match_survives_a_spelling_difference(self):
        self.assertEqual(self.check("Calibration-Baseline"),
                         "has its own heading")

    def test_a_definitional_copula_clears_the_term(self):
        self.assertEqual(self.check("recorded parameter set"), None)
        self.assertEqual(
            undefined.is_defined("calibration baseline", self.lowered,
                                 self.LINES, "", ""),
            "definition sentence")

    def test_a_definition_list_entry_clears_the_term(self):
        self.assertEqual(self.check("tick advancement"),
                         "definition list entry")

    def test_a_glossary_entry_clears_the_term(self):
        self.assertEqual(self.check("gauge record", "gauge record — a reading"),
                         "glossary entry")

    def test_an_undefined_term_is_not_cleared(self):
        # The negative case is the one that matters: if everything clears, the
        # detector reports nothing and looks like a clean document.
        self.assertIsNone(self.check("decision surface"))

    def test_is_stored_in_is_not_a_definition(self):
        # The tightening the module records: a first version accepted a copula
        # with no article and cleared 70 of 112 nominations. "The drift budget
        # is stored in the calibration register" says where it lives, not what
        # it is.
        self.assertIsNone(self.check("drift budget"))


class Same(unittest.TestCase):
    """same() — one reported term against one register term."""

    def setUp(self):
        self.same = lift(undefined, "main", "same")

    def test_identical_terms_match(self):
        self.assertTrue(self.same("escalation contract", "escalation contract"))

    def test_match_ignores_case_and_hyphenation(self):
        self.assertTrue(self.same("Execution Order", "execution-order"))

    def test_a_shorter_term_inside_a_longer_one_matches(self):
        # The register may record "frozen execution order" where the tool
        # reports "execution order". Same finding, different phrasing.
        self.assertTrue(self.same("execution order", "frozen execution order"))

    def test_containment_works_in_either_direction(self):
        self.assertTrue(self.same("frozen execution order", "execution order"))

    def test_a_bare_word_inside_a_phrase_does_not_match(self):
        # The load-bearing rejection. "order" sits inside "frozen execution
        # order" and is not the same finding; accepting it is how a scorer
        # starts rewarding vague output, which is what the previous version did
        # by construction.
        self.assertFalse(self.same("order", "frozen execution order"))

    def test_the_contained_side_must_be_most_of_the_container(self):
        # Pinned as behaviour rather than as the 0.6 constant: a term carrying
        # most of the register's phrase is the same finding, one carrying a
        # third of it is a different, vaguer claim.
        self.assertTrue(self.same("calibration baseline",
                                  "the calibration baseline"))
        self.assertFalse(self.same("baseline", "calibration baseline set"))

    def test_a_word_boundary_is_required(self):
        # Substring containment without boundaries would match "order" inside
        # "reordering" and "cal" inside "calibration".
        self.assertFalse(self.same("cal", "calibration"))

    def test_empty_terms_never_match(self):
        # An empty reported term against an empty register term is not a hit;
        # it is two blanks. Without this guard the containment test would fire
        # on every empty string in the register.
        self.assertFalse(self.same("", "execution order"))
        self.assertFalse(self.same("execution order", ""))
        self.assertFalse(self.same("", ""))
        self.assertFalse(self.same("---", "execution order"))

    def test_bug_a_plural_does_not_match_its_singular(self):
        # BUG (reported, not fixed): containment is tested with surrounding
        # spaces, so "escalation contract" is not found inside "escalation
        # contracts" — the trailing "s" breaks the boundary. The register
        # records the plural and the tool reports the singular routinely, and
        # each such pair is scored as a miss AND as a precision failure. The
        # error direction is pessimistic, which is the safe one, but a scorer
        # that under-counts by a fixed rule is still not measuring what it says.
        self.assertFalse(self.same("escalation contract",
                                   "escalation contracts"))


if __name__ == "__main__":
    unittest.main()
