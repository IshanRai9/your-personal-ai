import hashlib
import unittest

from personal_ai.passphrase import enroll, matches


class PassphraseTests(unittest.TestCase):
    def test_typed_and_spoken_forms_use_the_same_words(self):
        for typed, spoken in [
            ("Sunflower", "Sunflower."),
            ("  “It’s   lights‑out!”  ", "It's lights out."),
            ("Café, please!", "Cafe\u0301 please."),
        ]:
            with self.subTest(typed=typed):
                verifier = enroll(typed)
                self.assertTrue(matches(spoken, verifier))
                self.assertFalse(matches("almost " + spoken, verifier))
        self.assertFalse(matches("well rest", enroll("we’ll rest")))

    def test_limits_and_unspoken_characters(self):
        verifier = enroll(" ".join(["moon"] * 12))
        for phrase, warning in [
            ("…", "at least one"),
            ("moon " * 13, "at most 12"),
            ("moon 7", "Spell out"),
            ("moon + stars", "Spell out"),
        ]:
            with self.subTest(phrase=phrase):
                with self.assertRaisesRegex(ValueError, warning):
                    enroll(phrase)
                self.assertFalse(matches(phrase, verifier))

    def test_verifiers_from_previous_versions_still_match(self):
        salt = bytes(range(16))
        old_hash = hashlib.pbkdf2_hmac("sha256", b"autumn orchard", salt, 600_000).hex()
        self.assertTrue(matches("Autumn orchard.", f"{salt.hex()}:{old_hash}"))
