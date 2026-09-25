import re
from datetime import date

CAMPAIGN_TYPES = (
    "welcome_bonus",      # sign-up points/cash after minimum spend
    "cashback",
    "points_multiplier",
    "fee_waiver",         # annual fee waived / first year free
    "interest_free",      # 0% purchase / balance transfer periods
    "referral",
    "merchant_offer",     # partner discounts, travel/insurance perks with promo terms
    "other",
)

# card_bundle = account-opening / bank-switch / package offers that come with a debit or charge card.
PRODUCT_SCOPES = ("credit_card", "card_bundle")

OUTPUT_SPEC = """{
  "campaigns": [
    {
      "product_scope": one of %s,
      "card_name": "exact card product name or null",
      "campaign_title": "short title in the page's language",
      "campaign_type": one of %s,
      "offer_summary": "one-sentence summary in English",
      "reward_value": "e.g. '30,000 Membership Rewards points', '5%% cashback', '0%% for 20 months' or null",
      "conditions": "eligibility / minimum spend / time window, in English, or null",
      "start_date": "YYYY-MM-DD or null",
      "end_date": "YYYY-MM-DD or null",
      "language": "ISO 639-1 code of the source text",
      "evidence_quote": "verbatim substring (<=200 chars) from the text supporting the offer and dates",
      "confidence": 0.0-1.0
    }
  ]
}""" % (list(PRODUCT_SCOPES), list(CAMPAIGN_TYPES))

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalize_date(value) -> str | None:
    if not value or not isinstance(value, str) or not _ISO_DATE.match(value.strip()):
        return None
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        return None


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _compact(s: str) -> str:
    return "".join(re.findall(r"[a-z0-9]+", s.lower()))


def _strip_inline_markers(s: str) -> str:
    s = re.sub(r"\[@[^\]]+\]", "", s)
    s = re.sub(r"(?m)^\s*\d{1,2}\s*$", "", s)
    return s


def evidence_quote_valid(quote: str | None, source_text: str) -> bool:
    """Ground an extraction quote without allowing generic fuzzy matching."""
    if not quote:
        return False
    quote = _strip_inline_markers(quote)
    source_text = _strip_inline_markers(source_text)
    if _squash(quote) in _squash(source_text):
        return True
    compact_quote, compact_source = _compact(quote), _compact(source_text)
    if len(compact_quote) >= 24 and compact_quote in compact_source:
        return True
    if re.search(r"(?:\.\.\.|\[\.\.\.\]|\[…\])", quote):
        parts = [
            _compact(p)
            for p in re.split(r"(?:\.\.\.|\[\.\.\.\]|\[…\])", quote)
            if len(_compact(p)) >= 12
        ]
        if len(parts) >= 2 and sum(map(len, parts)) >= 32:
            pos = 0
            for part in parts:
                found = compact_source.find(part, pos)
                if found < 0:
                    return False
                pos = found + len(part)
            return True
    return False


def clean_campaign(raw: dict, source_text: str) -> dict | None:
    title = (raw.get("campaign_title") or "").strip()
    if not title:
        return None
    ctype = raw.get("campaign_type") if raw.get("campaign_type") in CAMPAIGN_TYPES else "other"
    start, end = normalize_date(raw.get("start_date")), normalize_date(raw.get("end_date"))
    if start and end and start > end:
        start, end = end, start
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5

    quote = (raw.get("evidence_quote") or "").strip()
    # A quote that cannot be grounded in the source means the model may have invented details.
    if not evidence_quote_valid(quote, source_text):
        confidence = min(confidence, 0.3)

    scope = raw.get("product_scope") if raw.get("product_scope") in PRODUCT_SCOPES else "credit_card"

    return {
        "product_scope": scope,
        "card_name": (raw.get("card_name") or "").strip() or None,
        "campaign_title": title[:300],
        "campaign_type": ctype,
        "offer_summary": raw.get("offer_summary"),
        "reward_value": raw.get("reward_value"),
        "conditions": raw.get("conditions"),
        "start_date": start,
        "end_date": end,
        "language": raw.get("language"),
        "evidence_quote": quote[:500] or None,
        "confidence": confidence,
    }
