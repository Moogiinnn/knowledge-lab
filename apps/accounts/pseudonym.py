"""Research pseudonyms.

The only place a real user identifier is turned into a research ID. HMAC with a key
kept outside the database means the mapping cannot be rebuilt by hashing every
possible login ID (which a plain SHA-256 of "S001" allows).
"""

import hmac
from hashlib import sha256

from django.conf import settings


def pseudonymous_id(user_id: int | str) -> str:
    key = settings.PSEUDONYM_KEY
    if not key or len(key) < 32:
        raise RuntimeError("PSEUDONYM_KEY must be set to at least 32 characters")
    digest = hmac.new(key.encode(), str(user_id).encode(), sha256).hexdigest()
    return f"p_{digest[:24]}"
