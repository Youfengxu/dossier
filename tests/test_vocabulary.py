"""The derived views on a scale, which eight tools now depend on.

Each of `shortfall`, `flagged` and `reading_order` replaced a tuple that had been
written out by hand in several files. Every one of those said
`("unmet", "partial", "unverifiable")` — correct for the coverage scale and
silently wrong for any project that declares its own words, which is the entire
reason the vocabulary is declared rather than compiled in.

So these tests do two things: pin the derived values against the literals they
replaced, so the migration is provably behaviour-preserving, and prove the
derivation actually follows a different scale rather than happening to agree.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import vocabulary


class DerivedViews(unittest.TestCase):

    def setUp(self):
        self.coverage = vocabulary.load(name="coverage")

    def test_the_literals_eight_files_used_are_reproduced_exactly(self):
        """The migration must not have changed a single verdict's treatment."""
        self.assertEqual(self.coverage.flagged, ("unmet", "partial", "unverifiable"))
        self.assertEqual(self.coverage.shortfall, ("unmet", "partial"))
        self.assertEqual(self.coverage.reading_order[:4],
                         ("unmet", "partial", "unverifiable", "met"))

    def test_flagged_and_shortfall_are_not_the_same_question(self):
        """"Could not verify" needs a reviewer — it is a request for evidence.
        "Does not apply" does not — someone already scoped it out. Collapsing the
        two is what `off_scale` being a list rather than a single `unknown`
        exists to prevent."""
        self.assertIn("unverifiable", self.coverage.flagged)
        self.assertNotIn("unverifiable", self.coverage.shortfall)
        self.assertNotIn("not_applicable", self.coverage.flagged)

    def test_the_top_of_the_scale_is_never_flagged(self):
        self.assertNotIn(self.coverage.scale[-1], self.coverage.flagged)
        self.assertNotIn(self.coverage.scale[-1], self.coverage.shortfall)

    def test_unknown_is_read_before_the_top_value_not_after(self):
        """A request for evidence belongs in the part of the report that still
        needs decisions, not filed behind the passes."""
        order = self.coverage.reading_order
        self.assertLess(order.index("unverifiable"), order.index("met"))

    def test_everything_appears_exactly_once_in_display_order(self):
        order = self.coverage.display_order
        self.assertEqual(sorted(order), sorted(self.coverage.values))
        self.assertEqual(len(order), len(set(order)))

    def test_a_project_scale_changes_all_of_them(self):
        """The point of the migration. A regulator's words, not this repo's."""
        safety = vocabulary.load(spec={
            "scale": ["unacceptable", "acceptable_with_action", "acceptable"],
            "off_scale": ["not_assessed", "out_of_scope"],
            "labels": {},
        })
        self.assertEqual(safety.shortfall,
                         ("unacceptable", "acceptable_with_action"))
        self.assertEqual(safety.flagged,
                         ("unacceptable", "acceptable_with_action", "not_assessed"))
        self.assertEqual(safety.reading_order,
                         ("unacceptable", "acceptable_with_action", "not_assessed",
                          "acceptable", "out_of_scope"))
        for view in (safety.shortfall, safety.flagged, safety.reading_order):
            self.assertNotIn("unmet", view)

    def test_a_two_value_scale_has_an_empty_shortfall_not_a_crash(self):
        """A pass/fail regulator. scale[:-1] is one element, and the degenerate
        one-value case must not index off the end."""
        binary = vocabulary.load(spec={"scale": ["fail", "pass"],
                                       "off_scale": ["not_assessed"], "labels": {}})
        self.assertEqual(binary.shortfall, ("fail",))
        self.assertEqual(binary.flagged, ("fail", "not_assessed"))


if __name__ == "__main__":
    unittest.main()
