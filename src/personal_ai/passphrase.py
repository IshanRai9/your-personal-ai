import hashlib
import hmac
import re
import secrets
import unicodedata


def canonical_phrase(text):
    text = unicodedata.normalize("NFKC", text).casefold().replace("’", "'")
    # Keep apostrophes inside words, where they can change meaning (e.g. we'll / well).
    text = re.sub(r"(?<!\w)'|'(?!\w)", " ", text)
    text = re.sub(r'["“”‘.,!?;:()\-‐‑–—\u200b\ufeff]', " ", text)
    tokens = text.split()
    if not tokens:
        raise ValueError("Enter at least one spoken word for the shutdown password.")
    if len(tokens) > 12:
        raise ValueError("Use at most 12 words for the shutdown password.")
    if not all(part.isalpha() for token in tokens for part in token.split("'")):
        raise ValueError(
            "Write the shutdown password as spoken words. Spell out numbers and symbols."
        )
    return " ".join(tokens)


def digest(canonical, salt):
    return hashlib.pbkdf2_hmac("sha256", canonical.encode(), salt, 600_000).hex()


def enroll(phrase):
    canonical = canonical_phrase(phrase)
    salt = secrets.token_bytes(16)
    return f"{salt.hex()}:{digest(canonical, salt)}"


def matches(phrase, verifier):
    if not phrase or not verifier:
        return False
    try:
        canonical = canonical_phrase(phrase)
    except ValueError:
        return False
    salt, expected = verifier.split(":")
    return hmac.compare_digest(digest(canonical, bytes.fromhex(salt)), expected)
