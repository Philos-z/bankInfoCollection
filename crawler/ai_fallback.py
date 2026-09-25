from bs4 import BeautifulSoup

import ai_client
from crawler.fetch import dom_outline

SYSTEM = (
    "You locate credit card promotion content on bank web pages. Given a DOM outline, "
    "return the single CSS selector that best captures the main credit card offers / campaigns / "
    "card product blocks (welcome bonus, cashback, fee waivers, promo terms, validity dates). "
    'Output {"selector": "<css>", "reason": "<short>"}. Use only classes/ids present in the outline.'
)


def heal_selector(html: str, url: str, broken_selector: str | None) -> str | None:
    """Ask the AI for a new selector; returns it only if it actually matches content."""
    outline = dom_outline(html)
    prompt = f"URL: {url}\nPrevious selector (no longer matches): {broken_selector}\n\nDOM outline:\n{outline}"
    try:
        result = ai_client.chat_json(SYSTEM, prompt)
    except Exception:
        return None
    selector = (result or {}).get("selector") if isinstance(result, dict) else None
    if not selector:
        return None
    try:
        nodes = BeautifulSoup(html, "html.parser").select(selector)
    except Exception:
        return None
    if not nodes or sum(len(n.get_text(strip=True)) for n in nodes) < 200:
        return None
    return selector
