"""Synthetic accounts-payable world for Payee Sentinel.

One invented buyer (Hallberg Components GmbH) pays about 60 invented vendors in a payment run every Thursday at
08:00. Over six weeks the world produces events on five channels: the vendor portal, the AP inbox, the support line,
the vendor master (bank-change requests and the clerk's callbacks) and invoices.

A few vendors are attacked: someone takes over their portal or mailbox, or talks the support line into changing a
phone number, and then asks for payments to go to a new bank account. Other vendors do legitimate things that look
similar (a real bank change, a trip abroad, a new AP clerk with a new laptop). Every event carries hidden ground
truth (`attack_id`, `benign_id`) that only the evaluation reads.

The world only holds what happens outside the buyer's control. Whether a bank change gets applied and whether a
payment is released depends on the agents, so `evaluate.py` decides those.

Everything is invented. IBANs have valid check digits around random account numbers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import random
import string

BUYER = "Hallberg Components GmbH"
START = datetime(2026, 10, 5)            # a Monday
WEEKS = 6
STORY_VENDOR = "Nordwind Logistik GmbH"  # the vendor in the demo story

COUNTRIES = {  # country: (BBAN pattern, phone prefix, domain suffix, legal suffix, name parts)
    "DE": ("n18", "+49", ".de", "GmbH", ["Nordwind", "Bergmann", "Kessler", "Falkenrot", "Steinhof", "Weber",
                                         "Lindauer", "Hartwig"]),
    "AT": ("n16", "+43", ".at", "GmbH", ["Alpenkraft", "Huber", "Donautal"]),
    "NL": ("a4n10", "+31", ".nl", "B.V.", ["Van Dijk", "Bakker", "Zeeland"]),
    "FR": ("n23", "+33", ".fr", "SAS", ["Dupont", "Lefevre", "Rhodanie"]),
    "IT": ("a1n22", "+39", ".it", "S.r.l.", ["Bertoli", "Ferraro", "Lombarda"]),
    "ES": ("n20", "+34", ".es", "S.L.", ["Vasquez", "Iberforja", "Montoya"]),
    "PL": ("n24", "+48", ".pl", "Sp. z o.o.", ["Kowalski", "Wislany"]),
}
TRADES = {"DE": ["Logistik", "Metallbau", "Elektrotechnik", "Verpackung", "Kunststoff", "Antriebe", "Werkzeuge"],
          "AT": ["Stahl", "Hydraulik", "Elektro"], "NL": ["Elektro", "Logistiek", "Kunststoffen"],
          "FR": ["Emballages", "Mecanique", "Transports"], "IT": ["Imballaggi", "Meccanica", "Cuscinetti"],
          "ES": ["Metales", "Transportes", "Componentes"], "PL": ["Narzedzia", "Odlewnia"]}
COUNTRY_WEIGHTS = {"DE": 28, "AT": 6, "NL": 6, "FR": 6, "IT": 6, "ES": 5, "PL": 3}
FAR_COUNTRIES = ["NG", "RU", "BR", "VN", "TR", "UA", "RO", "BG"]  # where an attacker may log in from
MULE_IBAN_COUNTRIES = ["LT", "PL", "BG", "RO", "GB", "CY"]
EXTRA_BBAN = {"LT": "n16", "BG": "a4n14", "RO": "a4n16", "GB": "a4n14", "CY": "n24"}

URGENCY = ["before Friday's payment run", "urgently, as our old account is being closed this week",
           "as soon as possible, our auditors require it", "today if possible"]


@dataclass
class Vendor:
    id: str
    name: str
    country: str
    domain: str
    email: str
    phone: str
    iban: str
    holder: str
    usual_countries: list[str]
    devices: list[str]
    cadence_days: int
    mean_amount: float
    terms_days: int


@dataclass
class Event:
    id: str
    ts: datetime
    channel: str          # portal | inbox | support | vendor_master | invoices
    kind: str
    vendor_id: str
    data: dict
    attack_id: str | None = None   # hidden ground truth
    benign_id: str | None = None   # hidden ground truth: a legitimate look-alike case


@dataclass
class Case:
    """An attack or a benign look-alike, with its events in order (hidden ground truth)."""
    id: str
    kind: str
    vendor_id: str
    event_ids: list[str] = field(default_factory=list)
    new_iban: str | None = None
    controlled_phone: str | None = None   # attacks: a phone the attacker answers


@dataclass
class World:
    seed: int
    vendors: dict[str, Vendor]
    events: list[Event]
    attacks: list[Case]
    benign: list[Case]

    @property
    def payment_runs(self) -> list[datetime]:
        first = START + timedelta(days=3, hours=8)  # Thursday 08:00
        return [first + timedelta(weeks=w) for w in range(WEEKS)]

    def events_of(self, vendor_id: str) -> list[Event]:
        return [e for e in self.events if e.vendor_id == vendor_id]


# ---------- small generators ----------

def _bban(rng: random.Random, pattern: str) -> str:
    out, i = "", 0
    while i < len(pattern):
        kind, j = pattern[i], i + 1
        while j < len(pattern) and pattern[j].isdigit():
            j += 1
        n = int(pattern[i + 1:j])
        pool = string.digits if kind == "n" else string.ascii_uppercase
        out += "".join(rng.choice(pool) for _ in range(n))
        i = j
    return out


def make_iban(rng: random.Random, country: str) -> str:
    pattern = COUNTRIES[country][0] if country in COUNTRIES else EXTRA_BBAN[country]
    bban = _bban(rng, pattern)
    digits = "".join(str(int(c, 36)) for c in bban + country + "00")
    check = 98 - int(digits) % 97
    return f"{country}{check:02d}{bban}"


def iban_is_valid(iban: str) -> bool:
    moved = iban[4:] + iban[:4]
    return int("".join(str(int(c, 36)) for c in moved)) % 97 == 1


def _phone(rng: random.Random, prefix: str) -> str:
    return f"{prefix} {rng.randint(100, 999)} {rng.randint(1000000, 9999999)}"


def _lookalike(rng: random.Random, domain: str) -> str:
    name, dot, tld = domain.rpartition(".")
    tricks = [lambda s: s.replace("l", "1", 1) if "l" in s else s + "s",
              lambda s: s.replace("i", "l", 1) if "i" in s else s + "-pay",
              lambda s: s.replace("m", "rn", 1) if "m" in s else s.replace("o", "0", 1),
              lambda s: s + "-group",
              lambda s: s[:-1] + s[-1] * 2]
    fake = rng.choice(tricks)(name)
    if fake == name:
        fake = name + "-billing"
    return fake + dot + (tld if rng.random() < 0.6 else "com")


def _device(rng: random.Random) -> str:
    return "dev-" + "".join(rng.choice("0123456789abcdef") for _ in range(6))


def _slug(name: str) -> str:
    keep = "".join(c for c in name.lower().split(" gmbh")[0].split(" b.v.")[0] if c.isalnum() or c == " ")
    return "-".join(keep.split()[:2])


def _make_vendors(rng: random.Random, n: int) -> dict[str, Vendor]:
    vendors, names = {}, set()
    countries = list(COUNTRY_WEIGHTS)
    weights = list(COUNTRY_WEIGHTS.values())
    while len(vendors) < n:
        c = "DE" if not vendors else rng.choices(countries, weights)[0]
        _, prefix, tld, legal, parts = COUNTRIES[c]
        name = STORY_VENDOR if not vendors else f"{rng.choice(parts)} {rng.choice(TRADES[c])} {legal}"
        if name in names:
            continue
        names.add(name)
        vid = f"V{len(vendors) + 1:03d}"
        domain = _slug(name) + tld
        vendors[vid] = Vendor(
            id=vid, name=name, country=c, domain=domain, email=f"accounts@{domain}", phone=_phone(rng, prefix),
            iban=make_iban(rng, c), holder=name, usual_countries=[c] + (["DE"] if c != "DE" and rng.random() < 0.3
                                                                        else []),
            devices=[_device(rng) for _ in range(rng.randint(1, 2))], cadence_days=rng.choice([7, 14, 14, 28, 28]),
            mean_amount=round(rng.lognormvariate(9.3, 0.8), -1), terms_days=rng.choice([14, 30, 30]))
    return vendors


class _Builder:
    def __init__(self, rng: random.Random):
        self.rng, self.events, self._n = rng, [], 0

    def add(self, ts, channel, kind, v: Vendor, case: Case | None = None, benign: bool = False, **data) -> Event:
        self._n += 1
        e = Event(f"E{self._n:05d}", ts, channel, kind, v.id, data,
                  attack_id=None if benign or case is None else case.id,
                  benign_id=case.id if benign and case is not None else None)
        self.events.append(e)
        if case is not None:
            case.event_ids.append(e.id)
        return e


def _work_time(rng: random.Random, day: datetime) -> datetime:
    return day + timedelta(hours=rng.randint(8, 17), minutes=rng.randint(0, 59))


# ---------- background activity ----------

def _background(b: _Builder, vendors: dict[str, Vendor]) -> None:
    rng = b.rng
    end = START + timedelta(weeks=WEEKS)
    for v in vendors.values():
        # invoices on the vendor's cadence (they arrive by email to the AP inbox)
        t = START - timedelta(days=rng.randint(0, v.cadence_days))
        k = 0
        while t < end:
            if t >= START - timedelta(days=v.terms_days):
                k += 1
                amount = round(max(300.0, rng.gauss(v.mean_amount, v.mean_amount * 0.25)), 2)
                b.add(max(t, START - timedelta(days=v.terms_days)), "invoices", "invoice", v,
                      invoice_no=f"{v.id}-{2026}{k:03d}", amount=amount, currency="EUR",
                      due=(t + timedelta(days=v.terms_days)).date().isoformat(), sender=v.email)
            t += timedelta(days=v.cadence_days)
        # portal logins from usual devices and countries, with an occasional typo
        day = START
        while day < end:
            if rng.random() < 0.35:
                ts = _work_time(rng, day)
                if rng.random() < 0.08:
                    for _ in range(rng.randint(1, 2)):
                        b.add(ts, "portal", "login", v, device=rng.choice(v.devices), country=v.country,
                              success=False)
                        ts += timedelta(seconds=rng.randint(5, 40))
                b.add(ts, "portal", "login", v, device=rng.choice(v.devices), country=v.country, success=True)
            day += timedelta(days=1)
        # payment-status questions and calls
        for _ in range(rng.randint(0, 2)):
            ts = _work_time(rng, START + timedelta(days=rng.randint(0, 7 * WEEKS - 1)))
            if rng.random() < 0.6:
                b.add(ts, "inbox", "email", v, sender=v.email, reply_to=None,
                      subject="Payment status", body=f"Hello, could you tell us when invoice {v.id}-2026001 "
                                                     f"will be paid? Kind regards, {v.name}")
            else:
                b.add(ts, "support", "call", v, caller=v.phone, request="invoice_status", verified_by="invoice_no")
        if rng.random() < 0.1:  # forgot password, from a usual device
            ts = _work_time(rng, START + timedelta(days=rng.randint(0, 7 * WEEKS - 1)))
            b.add(ts, "portal", "password_reset", v, device=rng.choice(v.devices), country=v.country)


# ---------- attacks ----------

ATTACK_KINDS = ["lookalike_bec", "mailbox_takeover", "portal_takeover", "phone_social", "slow_burn"]


def _bank_change_email(rng, v: Vendor, sender: str, reply_to: str | None, new_iban: str, holder: str,
                       urgent: bool, phone_in_body: str | None) -> dict:
    body = (f"Dear Accounts Payable team,\n\nplease note that our bank details have changed. "
            f"From now on, please pay all open invoices to:\nAccount holder: {holder}\nIBAN: {new_iban}\n")
    if urgent:
        body += f"Please update this {rng.choice(URGENCY)}.\n"
    if phone_in_body:
        body += f"For any questions, call our finance office on {phone_in_body}.\n"
    body += f"\nBest regards,\nFinance Department\n{v.name}"
    return dict(sender=sender, reply_to=reply_to, subject="Updated bank details", body=body)


def _attack(b: _Builder, v: Vendor, kind: str, case: Case, day0: datetime, next_run: datetime) -> None:
    """One attack. The bank-change request lands 1-5 days before `next_run` (the payment run the attacker aims at)."""
    rng = b.rng
    mule_country = rng.choice(MULE_IBAN_COUNTRIES) if rng.random() < 0.6 else v.country
    case.new_iban = make_iban(rng, mule_country if mule_country in COUNTRIES or mule_country in EXTRA_BBAN
                              else v.country)
    holder = v.name if rng.random() < 0.6 else v.name.split()[0] + " Trading Ltd"
    attacker_phone = _phone(rng, COUNTRIES[v.country][1])
    case.controlled_phone = attacker_phone
    request_at = next_run - timedelta(days=rng.randint(1, 5), hours=rng.randint(0, 6))
    far = rng.choice(FAR_COUNTRIES)
    vpn_home = rng.random() < (0.6 if kind == "slow_burn" else 0.3)   # attacker hides behind a VPN in the home country
    login_country = v.country if vpn_home else far
    new_dev = _device(rng)

    def portal_takeover(t0: datetime) -> datetime:
        t = t0
        if rng.random() < 0.6:
            for _ in range(rng.randint(5, 12)):
                b.add(t, "portal", "login", v, case, device=new_dev, country=login_country, success=False)
                t += timedelta(seconds=rng.randint(3, 20))
        t += timedelta(minutes=rng.randint(1, 30))
        b.add(t, "portal", "password_reset", v, case, device=new_dev, country=login_country)
        t += timedelta(minutes=rng.randint(2, 20))
        b.add(t, "portal", "login", v, case, device=new_dev, country=login_country, success=True)
        t += timedelta(minutes=rng.randint(10, 300))
        b.add(t, "portal", "contact_change", v, case, device=new_dev, country=login_country, field="email",
              old=v.email, new=f"finance.{v.domain.split('.')[0]}@{rng.choice(['gmail.com', 'outlook.com'])}")
        if rng.random() < 0.6:
            t += timedelta(minutes=rng.randint(1, 15))
            b.add(t, "portal", "contact_change", v, case, device=new_dev, country=login_country, field="phone",
                  old=v.phone, new=attacker_phone)
        return t

    if kind in ("portal_takeover", "slow_burn"):
        start = request_at - (timedelta(days=rng.randint(14, 21)) if kind == "slow_burn"
                              else timedelta(hours=rng.randint(6, 60)))
        t = portal_takeover(max(start, START))
        phone_changed = any(e.kind == "contact_change" and e.data["field"] == "phone"
                            for e in b.events if e.attack_id == case.id)
        b.add(max(request_at, t + timedelta(minutes=5)), "vendor_master", "bank_change_request", v, case,
              via="portal", new_iban=case.new_iban, holder=holder, device=new_dev, country=login_country)
        source = "master" if phone_changed else ("portal" if rng.random() < 0.5 else "master")
    elif kind == "phone_social":
        t = max(START, request_at - timedelta(days=rng.randint(2, 6)))
        b.add(t, "support", "call", v, case, caller=attacker_phone, request="update_phone", new=attacker_phone,
              verified_by="invoice_no")
        sender = v.email if rng.random() < 0.5 else f"{v.domain.split('.')[0]}.finance@gmail.com"
        b.add(request_at, "inbox", "email", v, case, **_bank_change_email(
            rng, v, sender, None, case.new_iban, holder, rng.random() < 0.5, None))
        b.add(request_at + timedelta(minutes=2), "vendor_master", "bank_change_request", v, case, via="email",
              new_iban=case.new_iban, holder=holder)
        source = "master"   # the clerk calls the master phone, which the attacker already changed
    else:  # lookalike_bec, mailbox_takeover
        if kind == "lookalike_bec":
            sender, reply_to = f"accounts@{_lookalike(rng, v.domain)}", None
        else:
            sender = v.email
            reply_to = f"accounts@{_lookalike(rng, v.domain)}" if rng.random() < 0.5 else None
            if rng.random() < 0.7:  # reconnaissance from the stolen mailbox
                b.add(request_at - timedelta(days=rng.randint(1, 4)), "inbox", "email", v, case, sender=sender,
                      reply_to=reply_to, subject="Open items",
                      body="Hello, for our month-end reconciliation could you send us the list of all open "
                           f"invoices and the next payment date? Thank you, {v.name}")
        put_phone = rng.random() < 0.6
        b.add(request_at, "inbox", "email", v, case, **_bank_change_email(
            rng, v, sender, reply_to, case.new_iban, holder, rng.random() < 0.7,
            attacker_phone if put_phone else None))
        b.add(request_at + timedelta(minutes=2), "vendor_master", "bank_change_request", v, case, via="email",
              new_iban=case.new_iban, holder=holder)
        source = "email" if put_phone and rng.random() < 0.6 else "master"
    # the AP clerk's callback (the control most companies already have)
    phone = {"email": attacker_phone, "portal": attacker_phone}.get(source)
    if source == "master":
        changed = [e for e in b.events if e.attack_id == case.id and (
            (e.kind == "contact_change" and e.data.get("field") == "phone")
            or (e.kind == "call" and e.data.get("request") == "update_phone"))]
        phone = attacker_phone if changed else v.phone
    result = "confirmed" if phone == attacker_phone else "denied"
    b.add(request_at + timedelta(hours=rng.randint(2, 20)), "vendor_master", "callback", v, case,
          phone=phone, phone_source=source, result=result)
    # make sure money is due at the run the attacker aims at: a fake "updated invoice" or the real one
    if rng.random() < 0.5 and kind in ("lookalike_bec", "mailbox_takeover"):
        sender = [e for e in b.events if e.attack_id == case.id and e.kind == "email"][-1].data["sender"]
        b.add(request_at + timedelta(hours=1), "invoices", "invoice", v, case,
              invoice_no=f"{v.id}-2026-U{rng.randint(10, 99)}", amount=round(v.mean_amount * rng.uniform(1.5, 4), 2),
              currency="EUR", due=(next_run + timedelta(days=rng.randint(0, 3))).date().isoformat(), sender=sender)
    else:
        b.add(request_at - timedelta(days=rng.randint(10, 20)), "invoices", "invoice", v, None,
              invoice_no=f"{v.id}-2026-R{rng.randint(100, 999)}", amount=round(v.mean_amount * rng.uniform(1, 2.5), 2),
              currency="EUR", due=(next_run + timedelta(days=rng.randint(0, 3))).date().isoformat(), sender=v.email)


# ---------- benign look-alikes ----------

BENIGN_KINDS = ["legit_bank_change", "legit_bank_change_foreign", "travel_login", "new_clerk", "urgent_legit",
                "merger"]


def _benign(b: _Builder, v: Vendor, kind: str, case: Case, day: datetime) -> None:
    rng = b.rng
    t = _work_time(rng, day)
    if kind in ("legit_bank_change", "legit_bank_change_foreign", "merger"):
        country = v.country if kind != "legit_bank_change_foreign" else rng.choice(
            [c for c in COUNTRIES if c != v.country])
        case.new_iban = make_iban(rng, country)
        holder = v.name if kind != "merger" else f"{v.name.split()[0]} Holding {COUNTRIES[v.country][3]}"
        b.add(t, "inbox", "email", v, case, True, **_bank_change_email(
            rng, v, v.email, None, case.new_iban, holder, rng.random() < 0.3, None))
        b.add(t + timedelta(minutes=2), "vendor_master", "bank_change_request", v, case, True, via="email",
              new_iban=case.new_iban, holder=holder)
        b.add(t + timedelta(hours=rng.randint(2, 20)), "vendor_master", "callback", v, case, True,
              phone=v.phone, phone_source="master", result="confirmed")
    elif kind == "travel_login":
        b.add(t, "portal", "login", v, case, True, device=_device(rng), country=rng.choice(
            ["US", "CN", "AE", "IN", "MX"]), success=True)
    elif kind == "new_clerk":  # new AP clerk: new laptop, password reset, contact email updated
        dev = _device(rng)
        b.add(t, "portal", "password_reset", v, case, True, device=dev, country=v.country)
        b.add(t + timedelta(minutes=5), "portal", "login", v, case, True, device=dev, country=v.country, success=True)
        b.add(t + timedelta(minutes=rng.randint(10, 120)), "portal", "contact_change", v, case, True, device=dev,
              country=v.country, field="email", old=v.email, new=f"ap.team@{v.domain}")
    elif kind == "urgent_legit":
        b.add(t, "inbox", "email", v, case, True, sender=v.email, reply_to=None, subject="Overdue payment",
              body=f"Hello, invoice {v.id}-2026001 is overdue. Please pay it {rng.choice(URGENCY)}. "
                   f"Regards, {v.name}")


def generate(seed: int = 7, n_vendors: int = 60, n_attacks: int = 8, n_benign: int = 14) -> World:
    rng = random.Random(seed)
    vendors = _make_vendors(rng, n_vendors)
    b = _Builder(rng)
    _background(b, vendors)
    runs = [START + timedelta(days=3, hours=8) + timedelta(weeks=w) for w in range(WEEKS)]
    pool = list(vendors)[1:]
    rng.shuffle(pool)
    attacks, benign = [], []
    # attacks aim at runs 2..6 (the first week is history), spread over time
    for i in range(n_attacks):
        kind = ATTACK_KINDS[i % len(ATTACK_KINDS)] if i < len(ATTACK_KINDS) else rng.choice(ATTACK_KINDS)
        run = runs[1 + (i * (WEEKS - 1)) // n_attacks]
        case = Case(f"ATK-{i + 1:02d}", kind, pool.pop())
        _attack(b, vendors[case.vendor_id], kind, case, START, run)
        attacks.append(case)
    for i in range(n_benign):
        kind = BENIGN_KINDS[i % len(BENIGN_KINDS)]
        day = START + timedelta(days=rng.randint(3, 7 * WEEKS - 4))
        case = Case(f"BEN-{i + 1:02d}", kind, pool.pop())
        _benign(b, vendors[case.vendor_id], kind, case, day)
        benign.append(case)
    events = sorted(b.events, key=lambda e: (e.ts, e.id))
    return World(seed, vendors, events, attacks, benign)


if __name__ == "__main__":
    from collections import Counter
    w = generate()
    print(len(w.vendors), "vendors,", len(w.events), "events,", len(w.attacks), "attacks,", len(w.benign), "benign")
    print(Counter((e.channel, e.kind) for e in w.events).most_common())
    for a in w.attacks:
        print(a.id, a.kind, w.vendors[a.vendor_id].name, len(a.event_ids), "events", a.new_iban)
