"""Second-stage validation for discovered source candidates.

Stage 1 (ai_discovery) is intentionally recall-oriented and only ranks links.
This module inspects the fetched page content and decides whether a lead is a
useful, stable source for concrete card promotions.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import ai_client


PAGE_TYPES = {
    "campaign",
    "campaign_terms",
    "product_with_current_offer",
    "generic_product",
    "educational",
    "application",
    "legal",
    "navigation",
    "personalized_offer_hub",
    "other",
}

OFFER_TYPES = {
    "welcome_bonus", "cashback", "points_multiplier", "fee_waiver", "interest_free",
    "referral", "merchant_offer", "other", "none",
}

SYSTEM = """You validate candidate web pages as sources for a European bank card-promotion database.
Be strict: mentioning rewards, cashback, 0%, points, fees, or card benefits does NOT by itself make a page
a promotion source.

Classify the page as exactly one page_type:
- campaign: a concrete public promotion/campaign page.
- campaign_terms: binding terms/summary box for a concrete current/introductory promotion.
- product_with_current_offer: a card/account product page that states a concrete current acquisition or introductory offer.
- generic_product: standing product features/benefits without a concrete current promotion.
- educational: explainer, guide, comparison advice, redemption/how-to content.
- application: application/login/personal-data flow rather than an evidence page.
- legal: generic legal terms/privacy/AGB not specific to a current promotion.
- navigation: listing/navigation/hub without enough concrete offer evidence itself.
- personalized_offer_hub: offers exist but are personalized/not publicly enumerated as stable concrete campaigns.
- other: none of the above.

A concrete offer has specific mechanics such as a welcome/referral reward, cashback amount/rate, fee waiver,
0% introductory period, points multiplier, merchant credit/discount, or account-opening reward tied to a card/bundle.
Standing features are not promotions unless the page states a current/introductory offer.
Mark is_standing_feature=true for permanent/recurring card benefits or optional paid program features that are
part of the normal product proposition rather than a temporary/acquisition/referral/merchant campaign. Examples:
an annually recurring restaurant credit included with the card, a permanent lounge entitlement, or a permanent
paid points accelerator. A calendar-year redemption window alone does NOT make a standing feature a promotion.

Return JSON only with:
{
  "page_type": "...",
  "has_concrete_offer": true|false,
  "offer_type": "welcome_bonus|cashback|points_multiplier|fee_waiver|interest_free|referral|merchant_offer|other|none",
  "offer_summary": "short concrete offer or null",
  "has_eligibility": true|false,
  "has_time_or_intro_period": true|false,
  "is_stable_evidence_page": true|false,
  "is_standing_feature": true|false,
  "recommended_role": "primary|supporting|none",
  "evidence_quote": "verbatim substring <=240 chars proving the concrete offer, or null",
  "confidence": 0.0-1.0,
  "reason": "short explanation"
}

Use primary for pages suitable for discovering/extracting campaigns directly. Use supporting for concrete
terms/summary-box evidence. Use none for pages that should not become a crawler source.
"""

_APP_HOSTS = ("dco-cc.",)
_APP_PATHS = (
    "/application", "/apply-in-app", "/personal-details", "/login", "/sign-in", "/signin",
)
_LEGAL_PATHS = ("/privacy", "/cookie", "/impressum", "/datenschutz", "/agb")

_KEYWORDS = re.compile(
    r"(?i)(welcome|willkommen|bonus|cashback|0\s*%|interest[- ]free|balance transfer|"
    r"punkte|points|guthaben|aktion|angebot|referral|empfehl|werben|offert|promo|"
    r"remise|réduction|introductory|fee waiver|annual fee|startguthaben)"
)


def _squash(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _compact(value: str) -> str:
    return "".join(re.findall(r"[a-z0-9]+", value.lower()))


def _strip_inline_markers(value: str) -> str:
    """Remove machine-inserted footnote markers that interrupt otherwise verbatim page text."""
    value = re.sub(r"\[@[^\]]+\]", "", value)
    # Some bank HTML renders footnote references as standalone text nodes/lines ("1", "2").
    # Remove only tiny numeric-only lines, leaving real offer amounts such as 30,000 or 3.09% intact.
    value = re.sub(r"(?m)^\s*\d{1,2}\s*$", "", value)
    return value


def evidence_quote_valid(quote: str | None, source_text: str) -> bool:
    if not quote:
        return False
    source_text = _strip_inline_markers(source_text)
    quote = _strip_inline_markers(quote)
    if _squash(quote) in _squash(source_text):
        return True
    # PDF extraction often inserts spaces inside words or around punctuation. For a sufficiently
    # long quote, allow a compact alphanumeric comparison while still requiring verbatim order.
    compact_quote = _compact(quote)
    compact_source = _compact(source_text)
    if len(compact_quote) >= 24 and compact_quote in compact_source:
        return True

    # The model sometimes uses an explicit ellipsis to omit a middle clause from a long
    # otherwise-verbatim citation. Accept only when every substantial fragment occurs in
    # source order; this is still source-grounded and avoids generic fuzzy matching.
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


def _bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def prefilter_url(url: str) -> dict | None:
    """Cheap high-confidence rejects. Return a finished result or None to inspect content."""
    p = urlparse(url)
    host, path = p.netloc.lower(), p.path.lower()
    if any(host.startswith(prefix) for prefix in _APP_HOSTS) or any(token in path for token in _APP_PATHS):
        return rejected_result("application", "URL is an application/login flow, not a stable evidence page")
    if any(token in path for token in _LEGAL_PATHS):
        return rejected_result("legal", "URL is a generic legal/privacy/AGB page")
    return None


def validation_excerpt(text: str, max_chars: int = 24000) -> str:
    """Keep enough context around promotion language without sending huge pages to the model."""
    if len(text) <= max_chars:
        return text
    windows = [text[:4000]]
    seen = set()
    for match in _KEYWORDS.finditer(text):
        start = max(0, match.start() - 900)
        end = min(len(text), match.end() + 2200)
        bucket = start // 1500
        if bucket in seen:
            continue
        seen.add(bucket)
        windows.append(text[start:end])
        if sum(len(w) for w in windows) >= max_chars:
            break
    return "\n\n--- relevant section ---\n\n".join(windows)[:max_chars]


def rejected_result(page_type: str, reason: str) -> dict:
    return {
        "validation_status": "reject",
        "page_type": page_type,
        "content_score": 0.0,
        "has_concrete_offer": False,
        "has_eligibility": False,
        "has_time_or_intro_period": False,
        "is_stable_evidence_page": False,
        "is_standing_feature": False,
        "recommended_role": None,
        "validation_reason": reason,
        "evidence_quote": None,
        "validation_confidence": 1.0,
    }


def clean_ai_result(raw: dict, source_text: str) -> dict:
    page_type = raw.get("page_type") if raw.get("page_type") in PAGE_TYPES else "other"
    offer_type = raw.get("offer_type") if raw.get("offer_type") in OFFER_TYPES else "other"
    quote = (raw.get("evidence_quote") or "").strip()[:500] or None
    quote_valid = evidence_quote_valid(quote, source_text)
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5
    if not quote_valid:
        confidence = min(confidence, 0.3)

    role = raw.get("recommended_role")
    if role not in {"primary", "supporting", "none"}:
        role = "none"
    return {
        "page_type": page_type,
        "has_concrete_offer": _bool(raw.get("has_concrete_offer")),
        "offer_type": offer_type,
        "offer_summary": raw.get("offer_summary"),
        "has_eligibility": _bool(raw.get("has_eligibility")),
        "has_time_or_intro_period": _bool(raw.get("has_time_or_intro_period")),
        "is_stable_evidence_page": _bool(raw.get("is_stable_evidence_page")),
        "is_standing_feature": _bool(raw.get("is_standing_feature")),
        "recommended_role": None if role == "none" else role,
        "evidence_quote": quote,
        "quote_valid": quote_valid,
        "validation_confidence": confidence,
        "validation_reason": (raw.get("reason") or "").strip()[:1000],
    }


def recommendation_score(result: dict) -> float:
    base = {
        "campaign": 0.25,
        "campaign_terms": 0.20,
        "product_with_current_offer": 0.20,
        "personalized_offer_hub": 0.02,
        "generic_product": -0.15,
        "educational": -0.35,
        "application": -0.60,
        "legal": -0.40,
        "navigation": -0.45,
        "other": -0.10,
    }.get(result.get("page_type"), -0.10)
    score = base
    score += 0.30 if result.get("has_concrete_offer") else 0.0
    score += 0.10 if result.get("has_eligibility") else 0.0
    score += 0.10 if result.get("has_time_or_intro_period") else 0.0
    score += 0.10 if result.get("is_stable_evidence_page") else 0.0
    score += 0.10 if result.get("quote_valid") else 0.0
    score += 0.05 if (result.get("validation_confidence") or 0) >= 0.8 else 0.0
    score -= 0.50 if result.get("is_standing_feature") else 0.0
    return round(max(0.0, min(1.0, score)), 3)


def apply_policy(result: dict) -> dict:
    result = dict(result)
    # Acquisition/referral/introductory mechanics are in scope even when they are continuously available
    # and the model describes them as a standing feature. "Standing feature" is meant to remove normal
    # card entitlements (lounge access, annual credits, permanent paid accelerators), not welcome offers.
    if result.get("has_concrete_offer") and result.get("offer_type") in {
        "welcome_bonus", "referral", "interest_free"
    }:
        result["is_standing_feature"] = False
    score = recommendation_score(result)
    page_type = result["page_type"]
    bad = {"application", "educational", "legal", "navigation"}
    if result.get("is_standing_feature"):
        status, role = "reject", None
    elif page_type in bad:
        status, role = "reject", None
    elif page_type == "personalized_offer_hub":
        status, role = "review", None
    elif not result.get("quote_valid") or (result.get("validation_confidence") or 0) < 0.5:
        # Recommendation is an evidence-bearing decision. If the model cannot point back to
        # source text (or its confidence was capped by a failed quote check), keep the lead
        # for human review instead of promoting it automatically.
        if result.get("has_concrete_offer") and page_type in {
            "campaign", "campaign_terms", "product_with_current_offer"
        }:
            status, role = "review", None
        else:
            status, role = "reject", None
    elif page_type == "campaign_terms" and result["has_concrete_offer"] and score >= 0.65:
        status, role = "recommended", "supporting"
    elif page_type in {"campaign", "product_with_current_offer"} and result["has_concrete_offer"] and score >= 0.70:
        status = "recommended"
        role = result.get("recommended_role") or "primary"
    elif score >= 0.40:
        status, role = "review", result.get("recommended_role")
    else:
        status, role = "reject", None
    result.update(validation_status=status, content_score=score, recommended_role=role)
    return result


def validate_page(text: str, url: str, bank_name: str, discovery_reason: str = "") -> dict:
    excerpt = validation_excerpt(text)
    raw = ai_client.chat_json(
        SYSTEM,
        f"Bank: {bank_name}\nURL: {url}\nDiscovery reason: {discovery_reason}\n\nPage text:\n{excerpt}",
    )
    if not isinstance(raw, dict):
        raw = {}
    return apply_policy(clean_ai_result(raw, text))

