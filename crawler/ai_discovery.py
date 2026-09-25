from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import ai_client
from crawler.fetch import extract_links

SYSTEM = (
    "You triage links from a bank website to find pages likely to describe credit card promotions: "
    "welcome/sign-up bonuses, cashback, points multipliers, fee waivers, 0% offers, referral bonuses, "
    "limited-time campaigns, or their terms & conditions (including PDFs). "
    'Return {"leads": [{"url": "...", "score": 0.0-1.0, "reason": "..."}]} with at most 15 items, '
    "score >= 0.5 only. Use only URLs from the provided list."
)

_TRACKING_PARAMS = {"inav", "linknav", "intlink", "gclid", "fbclid"}


def canonicalize_url(url: str) -> str:
    """Remove known navigation/tracking parameters without dropping offer-selection parameters."""
    p = urlparse(url)
    query = []
    for key, value in parse_qsl(p.query, keep_blank_values=True):
        lower = key.lower()
        if lower in _TRACKING_PARAMS or lower.startswith("utm_"):
            continue
        # AEM authoring/display switch; it does not identify a different offer.
        if lower == "wcmmode":
            continue
        query.append((key, value))
    return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path, p.params, urlencode(query, doseq=True), ""))


def discover_leads(html: str, page_url: str, allowed_domains: set[str]) -> list[dict]:
    """Grounded discovery: the AI only ranks links that actually exist on the page."""
    links = [
        (u, t) for u, t in extract_links(html, page_url)
        if _registrable(urlparse(u).netloc) in allowed_domains
    ]
    if not links:
        return []
    listing = "\n".join(f"{u} | {t}" for u, t in links[:400])
    result = ai_client.chat_json(SYSTEM, f"Page: {page_url}\n\nLinks (url | anchor text):\n{listing}")
    known = {u for u, _ in links}
    leads = result.get("leads", []) if isinstance(result, dict) else []
    return [
        l for l in leads
        if isinstance(l, dict) and l.get("url") in known and float(l.get("score", 0)) >= 0.5
    ]


def _registrable(netloc: str) -> str:
    parts = netloc.lower().split(":")[0].split(".")
    return ".".join(parts[-3:]) if len(parts) > 2 and parts[-2] in {"co", "com"} else ".".join(parts[-2:])


def registrable_domain(url: str) -> str:
    return _registrable(urlparse(url).netloc)
