"""freeze.py — slugify(), the only pure function in the corpus builder.

A slug is the identity a document keeps for the rest of the engagement: it is
what --doc, --from and --to take, what parsed/ is named after, and what every
locator in every register is written against. Renaming one invalidates a
register; two documents sharing one silently merges them. The rest of freeze.py
is hashing and file I/O and is exercised by the CI's `--check` step; this is the
part where a quiet defect would be a wrong ANSWER rather than a failure.
"""

import unittest

import freeze


class Slugify(unittest.TestCase):

    def test_a_vendor_filename_becomes_a_typeable_slug(self):
        self.assertEqual(
            freeze.slugify("source/System Design Description v2.docx"),
            "system-design-description-v2")

    def test_case_and_extension_are_dropped(self):
        # The same document arrives as .DOCX from one sender and .docx from
        # another. Two slugs for one document is two documents to the tools.
        self.assertEqual(freeze.slugify("DELIVERABLE_V1.DOCX"),
                         freeze.slugify("deliverable-v1.docx"))

    def test_the_directory_is_not_part_of_the_slug(self):
        self.assertEqual(freeze.slugify("source/exports/v1.docx"),
                         freeze.slugify("v1.docx"))

    def test_punctuation_runs_collapse_and_edges_are_trimmed(self):
        self.assertEqual(freeze.slugify("  (a) --- b! .md"), "a-b")

    def test_a_filename_with_nothing_usable_still_gets_a_slug(self):
        # An empty slug would produce parsed/.txt and a manifest entry nothing
        # can address. Better a useless name than an unaddressable document.
        self.assertEqual(freeze.slugify("---.docx"), "doc")

    def test_slugs_are_truncated_at_forty_characters(self):
        # Characterised, not a defect: freeze --init de-duplicates collisions
        # with a numeric suffix. But the truncation is silent, and two documents
        # whose names differ only past character 40 — the ordinary shape of
        # "... Revision Two" and "... Revision Three" — end up distinguished
        # only by that suffix, which tells a reader nothing about which is
        # which.
        long_two = ("Flood Response Digital Twin System Design Description "
                    "Revision Two.docx")
        long_three = long_two.replace("Two", "Three")
        self.assertEqual(len(freeze.slugify(long_two)), 40)
        self.assertEqual(freeze.slugify(long_two), freeze.slugify(long_three))


if __name__ == "__main__":
    unittest.main()
