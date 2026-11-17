"""The repo's text files are UTF-8 with LF line ends, so «€» and «£» reach GitHub and the app as themselves and not as
the mojibake a wrong encoding leaves (UTF-8 bytes read as Windows-1252)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = ("*.py", "*.md", "*.toml", "*.txt", "*.json", "*.yml", "*.example", ".gitattributes", ".gitignore")
SKIP = {".git", "runs", "__pycache__", ".pytest_cache", ".venv"}
MOJIBAKE = [c.encode("utf-8").decode("cp1252") for c in "€£é’–"] + [chr(0xFFFD)]


def _text_files():
    for pattern in PATTERNS:
        for p in ROOT.rglob(pattern):
            if p.is_file() and not SKIP & set(p.relative_to(ROOT).parts):
                yield p


def test_text_files_are_utf8_with_lf_and_no_mojibake():
    files = list(_text_files())
    assert len(files) > 20
    for p in files:
        raw = p.read_bytes()
        text = raw.decode("utf-8")                          # fails on any byte sequence that is not UTF-8
        assert not raw.startswith(b"\xef\xbb\xbf"), f"{p.name} starts with a byte-order mark"
        assert b"\r\n" not in raw, f"{p.name} has CRLF line ends"
        assert not [m for m in MOJIBAKE if m in text], f"{p.name} holds mojibake"


def test_the_currency_signs_survive():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "€85,250" in readme and "£450.7 million" in readme and "$2.77 billion" in readme
    assert "€85,250" in (ROOT / "app.py").read_text(encoding="utf-8")


def test_invented_addresses_cannot_belong_to_anyone():
    """The repo is public: every e-mail address in it uses a name reserved by RFC 2606, never a real provider."""
    found = {}
    for p in _text_files():
        for m in re.finditer(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)", p.read_text(encoding="utf-8")):
            host = m.group(1).lower()
            if not (host.endswith((".example", ".test")) or host in ("example.com", "example.org", "example.net")):
                found.setdefault(str(p.relative_to(ROOT)), set()).add(m.group(0))
    assert not found, found


# The Bundesnetzagentur's «Rufnummern für Medienproduktionen» (Drama Numbers): never given to anyone.
DRAMA_LANDLINE = ("+493023125", "+496990009", "+494066969", "+492214710", "+498999998")    # each + 3 digits
DRAMA_MOBILE = {"+4915228817386", "+4915228895456", "+4915254599371", "+491729925904", "+491729968532",
                "+491729973185", "+491729973186", "+491729980752", "+491749091317", "+491749464308"}
DRAMA_MOBILE_BLOCKS = ("+4917139200", "+49176040690")                                      # each + 2 digits


def _drama_number(phone: str) -> bool:
    p = phone.replace(" ", "")
    return (p in DRAMA_MOBILE
            or any(p.startswith(b) and len(p) == len(b) + 3 and p[len(b):].isdigit() for b in DRAMA_LANDLINE)
            or any(p.startswith(b) and len(p) == len(b) + 2 and p[len(b):].isdigit() for b in DRAMA_MOBILE_BLOCKS))


def test_the_story_phones_and_ibans_cannot_reach_anyone():
    """Every phone in the story is a Drama Number; every IBAN has valid check digits around a bank code no bank has."""
    from payee_sentinel.scenario import NO_BANK, iban_is_valid
    from payee_sentinel.story import CREW, VENDORS, VENDORS2
    vendors = [*VENDORS.values(), *VENDORS2.values()]
    phones = [v.phone for v in vendors] + [c["phone"] for c in CREW.values()]
    assert [p for p in phones if not _drama_number(p)] == []
    for iban in [v.iban for v in vendors] + [c["iban"] for c in CREW.values()]:
        a, b, fill = NO_BANK[iban[:2]]
        assert iban_is_valid(iban) and iban[4 + a:4 + b] == fill * (b - a), iban

