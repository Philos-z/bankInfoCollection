import io
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

import settings


@dataclass
class FetchResult:
    url: str
    status: int | None
    content_type: str
    body: bytes
    html: str | None


def fetch_requests(url: str) -> FetchResult:
    resp = requests.get(
        url,
        headers={"User-Agent": settings.USER_AGENT, "Accept-Language": "en,de,fr,es;q=0.8"},
        timeout=30,
    )
    ctype = resp.headers.get("Content-Type", "")
    html = resp.text if "html" in ctype else None
    return FetchResult(resp.url, resp.status_code, ctype, resp.content, html)


def fetch_playwright(url: str) -> FetchResult:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=settings.USER_AGENT, locale="en-GB")
            resp = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(2500)
            _dismiss_cookie_banner(page)
            html = page.content()
            status = resp.status if resp else None
            return FetchResult(page.url, status, "text/html", html.encode(), html)
        finally:
            browser.close()


def _dismiss_cookie_banner(page) -> None:
    for label in ("Accept all", "Accept All", "Alle akzeptieren", "Tout accepter",
                  "Aceptar todas", "Aceptar", "Accepter", "Akzeptieren", "I agree"):
        btn = page.get_by_role("button", name=label)
        if btn.count():
            try:
                btn.first.click(timeout=2000)
                page.wait_for_timeout(800)
                return
            except Exception:
                continue


def fetch(url: str, fetcher: str) -> FetchResult:
    return fetch_playwright(url) if fetcher == "playwright" else fetch_requests(url)


_NOISE_TAGS = ("script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe")


def html_to_text(html: str, selector: str | None) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_NOISE_TAGS):
        tag.decompose()
    if selector:
        nodes = soup.select(selector)
        if not nodes:
            return ""
        return "\n\n".join(n.get_text("\n", strip=True) for n in nodes)
    return soup.get_text("\n", strip=True)


def pdf_to_text(body: bytes) -> str:
    reader = PdfReader(io.BytesIO(body))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def extract_links(html: str, base_url: str) -> list[tuple[str, str]]:
    from urllib.parse import urljoin, urldefrag

    soup = BeautifulSoup(html, "html.parser")
    seen, links = set(), []
    for a in soup.find_all("a", href=True):
        href = urldefrag(urljoin(base_url, a["href"]))[0]
        if not href.startswith("http") or href in seen:
            continue
        seen.add(href)
        links.append((href, a.get_text(" ", strip=True)[:120]))
    return links


def dom_outline(html: str, max_chars: int = 12000) -> str:
    """Compact tag/class/id outline so the AI can pick a selector without the full page."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_NOISE_TAGS):
        tag.decompose()
    lines = []
    body = soup.body or soup
    for el in body.find_all(True):
        text = el.find(string=True, recursive=False)
        text = (text or "").strip()
        if not text and not el.get("id") and not el.get("class"):
            continue
        depth = len(list(el.parents)) - 2
        attrs = ""
        if el.get("id"):
            attrs += f"#{el['id']}"
        if el.get("class"):
            attrs += "." + ".".join(el["class"][:3])
        lines.append(f"{'  ' * min(depth, 12)}<{el.name}{attrs}> {text[:80]}")
        if sum(len(l) for l in lines) > max_chars:
            break
    return "\n".join(lines)
