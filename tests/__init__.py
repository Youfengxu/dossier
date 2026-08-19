"""Tests for the deterministic half of the toolkit — no model, no network.

Everything in here runs against the functions that decide what a finding is:
which passages a term reaches, what state a comparison is in, what counts as a
heading, what a scorer accepts as a hit. Those are the parts where a defect is
invisible — a wrong answer looks exactly like a right one, in the same format,
with the same confidence.

The repo root goes on sys.path here so a single file can be run on its own:

    python3 -m unittest tests.test_closure -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
