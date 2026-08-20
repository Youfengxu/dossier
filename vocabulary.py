#!/usr/bin/env python3
"""The scale a review is scored on, declared per project rather than compiled in.

`addressed / partial / not_addressed` is the vocabulary of one kind of review. An
audit says `remediated / in_progress / accepted_risk`; a conformance check says
`compliant / gap / not_applicable`; a safety regulator says `acceptable_action /
unacceptable_response`. The pipeline underneath is identical in every case — only
the words change — so the words belong in a config file and not in eight source
files, which is where they were.

Declare it under `vocabulary:` in the project's corpus.yaml:

    vocabulary:
      scale: [gap, partial, compliant]     # worst to best; ORDER IS MEANINGFUL
      unknown: indeterminate
      labels:
        gap: "Gap"
        partial: "Partial"
        compliant: "Compliant"

ORDER IS MEANINGFUL and is the reason this is a list rather than a set. Two
readers one step apart on the scale disagree about degree; two readers at
opposite ends disagree about fact, and only the second kind is worth a reviewer's
morning. A tool that cannot rank the values cannot tell those apart, and every
report that leads with contested rows depends on being able to.

`unknown` sits outside the scale deliberately. "I could not tell" is not a
midpoint between met and unmet, and averaging it as one silently converts an
absence of evidence into a moderate verdict.
"""

import os

# Two scales already existed in this codebase before either was declared: a
# review is adjudicated against comments, and a deliverable is scored for
# coverage against obligations. They are different questions with different words
# and they were both compiled in. Naming them is what stops a third from being
# invented in a fourth file.
DEFAULTS = {
    "REDACTED-39": {
        "scale": ["not_addressed", "partial", "addressed"],
        "unknown": "unclear",
        "labels": {
            "not_addressed": "Unaddressed",
            "partial": "Partial",
            "addressed": "Addressed",
            "unclear": "Unclear",
        },
    },
    "coverage": {
        "scale": ["unmet", "partial", "met"],
        # TWO values sit off this scale, not one. "unverifiable" means the
        # evidence could not be reached; "not_applicable" means the obligation
        # does not bind. Neither is a degree of coverage, and an earlier version
        # of compare-coverage.py ranked unverifiable BETWEEN partial and met —
        # which made "we could not check" one step better than "partly done".
        "off_scale": ["unverifiable", "not_applicable"],
        "labels": {
            "unmet": "Unmet",
            "partial": "Partial",
            "met": "Met",
            "unverifiable": "Unverifiable",
            "not_applicable": "Not applicable",
        },
    },
}
DEFAULT = DEFAULTS["REDACTED-39"]


class Vocabulary:
    def __init__(self, spec=None, name="REDACTED-39"):
        base = DEFAULTS.get(name, DEFAULT)
        spec = dict(base if not spec else {**base, **spec})
        self.scale = list(spec["scale"])
        # `unknown` is the singular form kept for projects that declare one;
        # `off_scale` is the general case. The first off-scale value is what a
        # coercion falls back to.
        off = spec.get("off_scale")
        if not off:
            off = [spec.get("unknown") or DEFAULT["unknown"]]
        self.off_scale = list(off)
        self.unknown = self.off_scale[0]
        labels = dict(spec.get("labels") or {})
        # A project may declare a scale and no labels; fall back to the value
        # itself rather than printing an empty cell.
        self.labels = {v: labels.get(v, v.replace("_", " ").capitalize())
                       for v in self.scale + self.off_scale}

    # -- membership --------------------------------------------------------
    @property
    def values(self):
        return set(self.scale) | set(self.off_scale)

    def valid(self, verdict):
        return verdict in self.values

    def coerce(self, verdict):
        """Anything unrecognised becomes `unknown`, never a scale value. A model
        inventing a fifth verdict must not be silently rounded onto the scale."""
        v = (verdict or "").strip().lower()
        return v if v in self.values else self.unknown

    def label(self, verdict):
        return self.labels.get(verdict, verdict)

    # -- ordering ----------------------------------------------------------
    def rank(self, verdict):
        """Position on the scale; None for `unknown`, which has no position."""
        return self.scale.index(verdict) if verdict in self.scale else None

    def distance(self, a, b):
        """Steps apart, or None if either is unrankable."""
        ra, rb = self.rank(a), self.rank(b)
        return None if ra is None or rb is None else abs(ra - rb)

    def agreement(self, verdicts):
        """AGREE / ADJACENT / DISAGREE / UNRANKABLE over any number of readers.

        Adjacent means one step — a difference of degree. Anything wider is a
        difference of fact, and that is the distinction the whole report ordering
        rests on."""
        verdicts = [v for v in verdicts if v]
        if not verdicts:
            return "UNRANKABLE"
        if len(set(verdicts)) == 1:
            return "AGREE"
        ranks = [self.rank(v) for v in verdicts]
        if any(r is None for r in ranks):
            return "UNRANKABLE"
        return "ADJACENT" if max(ranks) - min(ranks) <= 1 else "DISAGREE"


def load(project=None, name="REDACTED-39", spec=None):
    """Vocabulary for a project, from corpus.yaml's `vocabulary:` key.

    Falls back to the named default silently, because most projects are ordinary
    reviews and should not have to declare anything to get started. A project may
    declare either a single mapping (taken as the adjudication scale) or a
    mapping of names to scales.
    """
    if spec is not None:
        return Vocabulary(spec, name)
    if not project:
        return Vocabulary(name=name)
    path = os.path.join(os.path.expanduser(project), "corpus.yaml")
    if not os.path.exists(path):
        return Vocabulary(name=name)
    try:
        import yaml
        loaded = yaml.safe_load(open(path, encoding="utf-8")) or {}
    except Exception:
        return Vocabulary(name=name)
    declared = loaded.get("vocabulary") or {}
    if declared and "scale" not in declared:      # a mapping of named scales
        declared = declared.get(name) or {}
    return Vocabulary(declared or None, name)
