"""claim.py: a pair nobody judged is not a pair judged consistent.

The claim index asks a model, pair by pair, whether two statements can both be
true. The call can fail, and when it did the pair was dropped by `if result:`
while the progress line went on counting it:

    judged 200/200, 0 contradictions

is what a run printed when every one of its calls had been refused. The only
trace was `'failed': 200` inside a dict on the last line of the output.

No model here: `judge` is a function, and the tests hand it one.
"""

import contextlib
import io
import unittest

import claim

PAIRS = [("pump capacity", {"start": 10}, {"start": 40}),
         ("gauge interval", {"start": 12}, {"start": 55}),
         ("alert threshold", {"start": 20}, {"start": 70})]


def judging(*verdicts):
    """Run claim.judged over PAIRS with a judge that gives these verdicts, in
    order. None is a call that failed."""
    answers = dict(zip((subject for subject, _a, _b in PAIRS), verdicts))

    def judge(candidate):
        subject, a, b = candidate
        if answers[subject] is None:
            return None
        return {"subject": subject, "verdict": answers[subject],
                "confidence": "high", "reason": "r", "a": a, "b": b}

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        findings, unjudged = claim.judged(PAIRS, judge, concurrency=1)
    return findings, unjudged, out.getvalue()


class APairNobodyJudged(unittest.TestCase):

    def test_it_is_kept_and_counted_apart(self):
        findings, unjudged, _ = judging("consistent", None, "contradiction")
        self.assertEqual([f["subject"] for f in findings],
                         ["pump capacity", "alert threshold"])
        self.assertEqual([subject for subject, _a, _b in unjudged],
                         ["gauge interval"])

    def test_the_progress_line_counts_what_was_judged_not_what_was_tried(self):
        _findings, _unjudged, report = judging("consistent", None,
                                               "contradiction")
        self.assertIn("judged 2 of 3", report)
        self.assertIn("1 could not be judged", report)
        self.assertIn("1 contradictions", report)

    def test_a_run_whose_calls_all_failed_judged_nothing(self):
        findings, unjudged, report = judging(None, None, None)
        self.assertEqual((findings, len(unjudged)), ([], 3))
        self.assertIn("judged 0 of 3", report)

    def test_a_run_with_no_failures_says_nothing_about_any(self):
        _findings, unjudged, report = judging("consistent", "consistent",
                                              "tension")
        self.assertEqual(unjudged, [])
        self.assertIn("judged 3 of 3", report)
        self.assertNotIn("could not be judged", report)


if __name__ == "__main__":
    unittest.main()
