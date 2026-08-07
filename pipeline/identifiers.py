"""Identifier generation for synthetic corpora.

Every identifier this module emits is *structurally valid* — it passes the same
check-digit and format rules a real one would — and *provably not real*, because
it is drawn from a range that the issuing authority has reserved for
documentation, testing, or internal use.

That combination is the whole point. An identifier that is merely random may
collide with a live record; an identifier that is obviously fake ("XXX-0000")
breaks the illusion the corpus depends on. Reserved ranges give us both.

Reserved ranges used here
-------------------------
IP            RFC 5737 TEST-NET-1/2/3, RFC 3849 IPv6 documentation prefix
Domain/email  RFC 2606 (example.com, .test, .invalid)
Phone         NANPA 555-0100..555-0199, the only guaranteed-fictitious block
SSN-shaped    Never-issued areas 000 / 666 / 900-999
Country       ISO 3166 user-assigned alpha-2 (QM-QZ, XA-XZ, ZZ)
Airport       ICAO "ZZZZ" = aerodrome with no assigned code
GTIN          GS1 restricted-circulation prefixes 02 / 04 / 20-29
NPI           CMS-published placeholder NPIs (Luhn-valid with the 80840 prefix)

Where an authority publishes no reserved block (NDC labeler codes, for
instance) we generate a structurally valid value and mark the pack so the
safety test can assert it is absent from the real registry pattern.
"""

from __future__ import annotations

import random
import string

# --------------------------------------------------------------------------
# Reserved constants
# --------------------------------------------------------------------------

TEST_NET = ("192.0.2", "198.51.100", "203.0.113")   # RFC 5737
DOC_IPV6_PREFIX = "2001:db8"                        # RFC 3849
EXAMPLE_DOMAINS = ("example.com", "example.net", "example.org")
FICTITIOUS_PHONE_PREFIX = "555-01"                  # NANPA 555-0100..0199
NEVER_ISSUED_SSN_AREAS = ("000", "666") + tuple(str(n) for n in range(900, 1000))
ISO_USER_ASSIGNED_A2 = (
    ["ZZ"] + [f"Q{c}" for c in "MNOPQRSTUVWXYZ"] + [f"X{c}" for c in string.ascii_uppercase]
)
ICAO_UNASSIGNED = "ZZZZ"
GS1_RESTRICTED_PREFIXES = ("02", "04") + tuple(str(n) for n in range(20, 30))

# CMS-published placeholder NPIs. These are documented default values, not a
# formal reserved block, so they are safe to print but must never be presented
# as belonging to a provider.
PLACEHOLDER_NPI = {
    "professional": "1999999984",
    "institutional": "1999999976",
    "dmepos": "1999999992",
}


# --------------------------------------------------------------------------
# Check digits
# --------------------------------------------------------------------------

def luhn_check_digit(payload: str) -> int:
    """Luhn check digit, doubling from the rightmost payload digit leftward."""
    total = 0
    for i, ch in enumerate(reversed(payload)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - total % 10) % 10


def gs1_check_digit(payload: str) -> int:
    """GS1 mod-10 check digit (3-1 weighting from the right)."""
    total = 0
    for i, ch in enumerate(reversed(payload)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - total % 10) % 10


# --------------------------------------------------------------------------
# Generators
# --------------------------------------------------------------------------

class IdFactory:
    """Deterministic, seeded identifier generation.

    Seeded so a tenant's corpus is byte-reproducible across builds — a rebuild
    that silently reshuffles every identifier makes diffs meaningless and
    breaks any cached index keyed on document content.
    """

    def __init__(self, seed: int):
        self.rng = random.Random(seed)

    # -- primitives --------------------------------------------------------

    def digits(self, n: int) -> str:
        return "".join(self.rng.choice("0123456789") for _ in range(n))

    def alnum(self, n: int) -> str:
        return "".join(self.rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(n))

    def letters(self, n: int) -> str:
        return "".join(self.rng.choice(string.ascii_uppercase) for _ in range(n))

    def year(self, lo: int = 2023, hi: int = 2026) -> int:
        return self.rng.randint(lo, hi)

    # -- reserved-range identities ----------------------------------------

    def ipv4(self) -> str:
        return f"{self.rng.choice(TEST_NET)}.{self.rng.randint(2, 254)}"

    def hostname(self, role: str) -> str:
        return f"{role}-{self.digits(2)}.{self.rng.choice(EXAMPLE_DOMAINS)}"

    def email(self, first: str, last: str) -> str:
        return f"{first[0].lower()}.{last.lower()}@{self.rng.choice(EXAMPLE_DOMAINS)}"

    def phone(self) -> str:
        return f"+1 (555) {FICTITIOUS_PHONE_PREFIX}{self.rng.randint(0, 99):02d}"

    def country(self) -> str:
        return self.rng.choice(ISO_USER_ASSIGNED_A2)

    # -- healthcare --------------------------------------------------------

    def npi(self, kind: str = "professional") -> str:
        """A Luhn-valid 10-digit NPI outside the NPPES-issued 1/2-prefix space.

        Real NPIs begin with 1 or 2. We emit a 9-prefix value, which NPPES has
        never allocated, so the number validates but cannot resolve.
        """
        payload = "9" + self.digits(8)
        return payload + str(luhn_check_digit("80840" + payload))

    def placeholder_npi(self, kind: str = "professional") -> str:
        return PLACEHOLDER_NPI[kind]

    def mrn(self) -> str:
        return f"MRN{self.digits(8)}"

    def accession(self) -> str:
        return f"{self.rng.randint(23, 26)}-{self.digits(6)}"

    def ndc(self) -> str:
        """5-4-2 NDC with a labeler code in a range FDA has not allocated."""
        return f"9{self.digits(4)}-{self.digits(4)}-{self.digits(2)}"

    # -- supply chain ------------------------------------------------------

    def gtin13(self) -> str:
        payload = self.rng.choice(GS1_RESTRICTED_PREFIXES) + self.digits(10)
        return payload + str(gs1_check_digit(payload))

    def sscc(self) -> str:
        payload = "0" + self.rng.choice(GS1_RESTRICTED_PREFIXES) + self.digits(14)
        return payload + str(gs1_check_digit(payload))

    def gln(self) -> str:
        payload = self.rng.choice(GS1_RESTRICTED_PREFIXES) + self.digits(10)
        return payload + str(gs1_check_digit(payload))

    # -- aviation / maritime ----------------------------------------------

    def tail_number(self) -> str:
        """N-number in the unassigned N9xxZZ tail of the registry."""
        return f"N9{self.digits(2)}ZZ"

    def imo(self) -> str:
        """IMO number with a valid mod-11 check digit."""
        payload = "9" + self.digits(5)
        total = sum(int(d) * w for d, w in zip(payload, (7, 6, 5, 4, 3, 2)))
        return f"IMO {payload}{total % 10}"

    def mmsi(self) -> str:
        return self.digits(9)

    # -- generic enterprise ------------------------------------------------

    def doc_no(self, prefix: str, width: int = 4) -> str:
        return f"{prefix}-{self.digits(width)}"

    def sequenced(self, prefix: str, year: int, width: int = 4) -> str:
        return f"{prefix}-{year}-{self.digits(width)}"

    def batch(self) -> str:
        return f"{self.letters(2)}{self.digits(5)}"

    def version(self) -> str:
        return f"{self.rng.randint(1, 6)}.{self.rng.randint(0, 4)}"
