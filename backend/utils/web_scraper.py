import logging
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from slugify import slugify

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LLMXScraper/1.0)"}
DEFAULT_TIMEOUT = 10


def _fetch(session: requests.Session, url: str) -> str:
    resp = session.get(url, headers=DEFAULT_HEADERS, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    # polite delay
    time.sleep(1)
    return resp.text


def _discover_sitemaps(base_url: str, session: requests.Session) -> list:
    """Attempt to discover sitemap URLs for the given base domain."""
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    discovered = []

    # check robots.txt for Sitemap entries
    robots_url = urljoin(root, "robots.txt")
    try:
        text = _fetch(session, robots_url)
        for line in text.splitlines():
            if line.lower().startswith("sitemap:"):
                sitemap_url = line.split(":", 1)[1].strip()
                discovered.append(sitemap_url)
    except Exception:
        logger.info("robots.txt not accessible or no sitemap entry")

    candidates = [
        "sitemap.xml",
        "sitemap_index.xml",
        "sitemap1.xml",
        "sitemap.xml.gz",
        "sitemap_index.xml.gz",
    ]

    for cand in candidates:
        url = urljoin(root + "/", cand)
        if url in discovered:
            continue
        try:
            resp = session.get(url, headers=DEFAULT_HEADERS, timeout=DEFAULT_TIMEOUT)
            if resp.status_code == 200:
                discovered.append(url)
        except Exception:
            continue
        finally:
            time.sleep(1)
    return discovered


def scrape_website(url: str) -> dict:
    """Scrape basic information from the given URL."""
    session = requests.Session()

    html = _fetch(session, url)
    soup = BeautifulSoup(html, "html.parser")

    parsed = urlparse(url)
    slug = slugify(parsed.path.strip("/")) or "index"

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title["content"].strip()

    keywords = ""
    meta_keywords = soup.find("meta", attrs={"name": "keywords"})
    if meta_keywords and meta_keywords.get("content"):
        keywords = meta_keywords["content"].strip()

    category = ""
    cat = soup.find("meta", attrs={"property": "article:section"}) or soup.find(
        "meta", attrs={"name": "category"}
    )
    if cat and cat.get("content"):
        category = cat["content"].strip()

    featured_image = ""
    img_meta = soup.find("meta", property="og:image")
    if img_meta and img_meta.get("content"):
        featured_image = img_meta["content"].split("/")[-1]

    body = soup.body
    content = body.get_text(separator="\n", strip=True) if body else ""

    metadata = {}
    for tag in soup.find_all("meta"):
        name = tag.get("name") or tag.get("property")
        if name and tag.get("content"):
            metadata[name.lower()] = tag["content"].strip()

    sitemaps = _discover_sitemaps(url, session)

    return {
        "url": url,
        "slug": slug,
        "content": content,
        "keywords": keywords,
        "title": title,
        "featured_image": featured_image,
        "metadata": metadata,
        "category": category,
        "sitemaps": sitemaps,
    }
