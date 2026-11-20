import hashlib
import hmac
import re
import secrets


def words(text):
    return " ".join(re.findall(r"[\w]+", text.casefold()))


def digest(phrase, salt):
    return hashlib.pbkdf2_hmac("sha256", words(phrase).encode(), salt, 600_000).hex()


def enroll(phrase):
    if not re.fullmatch(r"[a-zA-Z]+(?:\s+[a-zA-Z]+){1,11}", phrase.strip()):
        raise ValueError("Choose a shutdown password of 2 to 12 English words.")
    salt = secrets.token_bytes(16)
    return f"{salt.hex()}:{digest(phrase, salt)}"


def matches(phrase, verifier):
    if not phrase or not verifier:
        return False
    salt, expected = verifier.split(":")
    return hmac.compare_digest(digest(phrase, bytes.fromhex(salt)), expected)
