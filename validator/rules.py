import re
import unicodedata
from datetime import date, timedelta
from difflib import SequenceMatcher

import settings

# Below this, an extraction is too shaky to count as a confirming source.
MIN_CONFIDENCE_FOR_VERIFY = 0.5
SAME_THRESHOLD = 0.75
AMBIGUOUS_THRESHOLD = 0.35

_GENERIC_CARD_WORDS = {
    "card", "cards", "credit", "karte", "kreditkarte", "carte", "bancaire", "tarjeta", "credito",
    "de", "la", "le", "the", "und", "y", "et",
}
_NUMBER = re.compile(r"\d{1,3}(?:[.,  ]\d{3})+(?![\d])|\d+(?:[.,]\d+)?")

_MECHANIC_PATTERNS = {
    "balance_transfer": re.compile(r"\bbalance\s+transfers?\b", re.I),
    "purchase": re.compile(r"\bpurchases?\b", re.I),
    "referral": re.compile(r"\b(referral|recommend|empfehl|werben)\b", re.I),
    "bank_switch": re.compile(r"\b(bank\s+switch|mobilit[eé]\s+bancaire)\b", re.I),
}


def _fold(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def _tokens(s: str | None) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", _fold(s)))


def card_tokens(card_name: str | None, bank_name: str | None) -> set[str]:
    return _tokens(card_name) - _GENERIC_CARD_WORDS - _tokens(bank_name)


def card_relation(a: dict, b: dict) -> str:
    """same | unknown | similar | different"""
    ta = card_tokens(a.get("card_name"), a.get("bank_name"))
    tb = card_tokens(b.get("card_name"), b.get("bank_name"))
    if not ta or not tb:
        return "unknown"
    if ta == tb:
        return "same"
    if ta <= tb or tb <= ta:
        return "similar"
    return "different"


def reward_numbers(value: str | None) -> set[float]:
    nums = set()
    for m in _NUMBER.findall(value or ""):
        if re.fullmatch(r"\d{1,3}(?:[.,  ]\d{3})+", m):
            n = float(re.sub(r"[.,  ]", "", m))
        else:
            n = float(m.replace(",", "."))
        if n:
            nums.add(n)
    return nums


def number_relation(a: str | None, b: str | None) -> str:
    na, nb = reward_numbers(a), reward_numbers(b)
    if not na or not nb:
        return "unknown"
    return "match" if na & nb else "mismatch"


def mechanic_tags(row: dict) -> set[str]:
    text = " ".join(str(row.get(k) or "") for k in ("campaign_title", "offer_summary", "conditions"))
    return {name for name, pattern in _MECHANIC_PATTERNS.items() if pattern.search(text)}


def mechanic_relation(a: dict, b: dict) -> str:
    ta, tb = mechanic_tags(a), mechanic_tags(b)
    if not ta or not tb:
        return "unknown"
    return "match" if ta & tb else "mismatch"


def _title(s: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9%]+", _fold(s)))


def similarity(a: dict, b: dict) -> float:
    """0..1 score that two extraction/campaign rows describe the same promotion."""
    if a["bank_id"] != b["bank_id"] or a["campaign_type"] != b["campaign_type"]:
        return 0.0
    card = card_relation(a, b)
    if card == "different":
        return 0.0
    score = 0.4 * SequenceMatcher(None, _title(a["campaign_title"]), _title(b["campaign_title"])).ratio()
    score += {"same": 0.3, "unknown": 0.1, "similar": 0.1}[card]
    score += {"match": 0.3, "unknown": 0.1, "mismatch": -0.3}[number_relation(a["reward_value"], b["reward_value"])]
    score += {"match": 0.15, "unknown": 0.0, "mismatch": -0.15}[mechanic_relation(a, b)]
    if a["end_date"] and b["end_date"]:
        score += 0.1 if a["end_date"] == b["end_date"] else -0.1
    return max(0.0, min(1.0, score))


def lifecycle(start: str | None, end: str | None, today: date | None = None) -> str:
    today = today or date.today()
    if start and date.fromisoformat(start) > today:
        return "upcoming"
    if end:
        end_d = date.fromisoformat(end)
        if end_d < today:
            return "expired"
        if end_d - today <= timedelta(days=settings.EXPIRING_WINDOW_DAYS):
            return "expiring"
    return "active"


def verify(supporters: list[dict]) -> tuple[str, str]:
    """Decide verification from the current supporting extractions (each carries domain/source_type)."""
    solid = [s for s in supporters if (s["confidence"] or 0) >= MIN_CONFIDENCE_FOR_VERIFY]
    domains = {s["domain"] for s in solid}
    end_dates = {s["end_date"] for s in solid if s["end_date"]}
    reward_sets = [(s["domain"], reward_numbers(s["reward_value"])) for s in solid]
    reward_conflicts = sorted({
        f"{da}{sorted(na)} vs {db}{sorted(nb)}"
        for i, (da, na) in enumerate(reward_sets) for db, nb in reward_sets[i + 1:]
        if na and nb and not na & nb
    })

    notes = [f"{len(domains)} independent domain(s): {', '.join(sorted(domains)) or '-'}"]
    if len(end_dates) > 1:
        notes.append(f"end_date disagreement: {sorted(end_dates)}")
    if reward_conflicts:
        notes.append("reward disagreement: " + "; ".join(reward_conflicts))
    if len(end_dates) > 1 or reward_conflicts:
        return "conflict", "; ".join(notes)
    if len(domains) >= 2:
        return "verified", "; ".join(notes)
    return "unverified", "; ".join(notes)


def pick_primary(supporters: list[dict]) -> dict:
    """Primary-role source wins; then official, confidence and recency."""
    solid = [s for s in supporters if (s.get("confidence") or 0) >= MIN_CONFIDENCE_FOR_VERIFY]
    candidates = solid or supporters
    return max(
        candidates,
        key=lambda s: (
            s.get("source_role", "primary") == "primary",
            s["source_type"] == "official",
            s["confidence"] or 0,
            s["extracted_at"],
        ),
    )
