"""fixtures/ntsb/score-ntsb.py: the baseline every score is printed beside.

The scorer prints one line before any result: answer this class to everything
and you score this much. A result that does not beat it has shown nothing, so
the line has to be right, and it named its class with

    majority, majority_n = counts.most_common(1)[0]

which breaks a tie by whichever class labels.json happens to list first. The
count is the same either way, so the baseline was never wrong. The class named
beside it was decided by the order of a file.

It was found by check-aggregation.py on the day that gate first read fixtures/,
which it had skipped since its first commit.

The labels are not in the repository, so main() is run here against invented
ones. Nothing below says anything about NTSB's.
"""

import collections
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

from tests.support import script

score = script("fixtures/ntsb/score-ntsb.py")


class TheMajorityClass(unittest.TestCase):

    def test_a_tie_names_every_class_in_it(self):
        counts = collections.Counter(
            ["not_addressed"] * 3 + ["addressed"] * 3 + ["partial"])
        self.assertEqual(score.majority(counts),
                         (["addressed", "not_addressed"], 3))

    def test_and_does_not_depend_on_which_was_counted_first(self):
        one = collections.Counter(["addressed", "not_addressed"])
        other = collections.Counter(["not_addressed", "addressed"])
        self.assertEqual(score.majority(one), score.majority(other))

    def test_a_clear_majority_is_named_alone(self):
        """The slice the scorer's own docstring describes: 13 against 11."""
        counts = collections.Counter(
            ["addressed"] * 13 + ["not_addressed"] * 11)
        self.assertEqual(score.majority(counts), (["addressed"], 13))


class TheLineItPrints(unittest.TestCase):
    """main(), with its folder pointed at invented labels."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def baseline(self, verdicts):
        labels = {f"R-{n}": {"verdict": v} for n, v in enumerate(verdicts)}
        with open(os.path.join(self.tmp, "labels.json"), "w") as handle:
            json.dump({"labels": labels, "excluded": []}, handle)
        run = os.path.join(self.tmp, "run.jsonl")
        with open(run, "w") as handle:
            handle.write(json.dumps({"id": "R-0", "verdict": verdicts[0],
                                     "cites": ["d:1-1"]}) + "\n")
        out, argv, here = io.StringIO(), sys.argv, score.HERE
        sys.argv, score.HERE = ["score-ntsb.py", run], self.tmp
        try:
            with contextlib.redirect_stdout(out):
                score.main()
        finally:
            sys.argv, score.HERE = argv, here
        return next(line.strip() for line in out.getvalue().splitlines()
                    if "majority-class baseline" in line)

    def test_a_clear_majority_reads_as_it_always_did(self):
        self.assertEqual(
            self.baseline(["addressed"] * 13 + ["not_addressed"] * 11),
            "majority-class baseline: answer 'addressed' to everything "
            "-> 13/24 = 54.2%")

    def test_a_tie_is_said_to_be_one(self):
        self.assertEqual(
            self.baseline(["not_addressed"] * 2 + ["addressed"] * 2),
            "majority-class baseline: answer 'addressed' or 'not_addressed' "
            "to everything -> 2/4 = 50.0%")


if __name__ == "__main__":
    unittest.main()
