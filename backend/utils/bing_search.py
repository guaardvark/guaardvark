"""Ask Bing's public result page for a query and read the answer.

A search here is two requests to www.bing.com and nothing else: the home page
(it sets the session cookies a browser would carry), then /search with the
query. Read by backend/api/web_search_api.py.

Bing answers a client that runs no scripts unevenly. Measured from one machine
on 2026-11-16, with queries it had not been asked before:

* /search with only ``q``: 7 of 20 first pages were about the query. The rest
  were results for one word of it ("history" for a question about a railroad's
  history), with nothing on the page saying so.
* Home page first, then /search with the parameters the search box sends
  (``_BROWSER_PARAMS``): 12 of 20 on the first ask, 16 of 20 on the second.

So a page is checked against the query before it is believed
(``answers_query``), and asked again when it fails the check.
"""

import base64
import random
import re
import secrets
import unicodedata
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlsplit

from lxml import html as lxml_html

HOME_URL = "https://www.bing.com/"
SEARCH_URL = "https://www.bing.com/search"
REQUEST_TIMEOUT_SECONDS = 10
RESULTS_PER_PAGE = 10
# Result pages read for one search. A later page repeats some of the page
# before it, so twenty results can take three.
_MAX_PAGES = 3

# Statuses Bing uses to turn an automated client away.
_REFUSAL_STATUSES = (202, 400, 403, 418, 429)

# A page counts as an answer to the query when at least this share of its
# results mention two or more of the query's words. On 64 saved first pages
# of five or more results (2026-11-16), the 29 about another search scored
# 0.10 or less and the 35 about the query 0.70 or more.
_ON_QUERY_SHARE = 0.25

# Desktop browsers only: Bing sends phone browsers a short page of three to
# five results, and some of them a redirect carrying ad-campaign parameters.
_DESKTOP_SYSTEMS = ("windows", "macos", "linux")


class BingError(Exception):
    """The search could not be completed; str() is the reason, without the query."""


class BingRefused(BingError):
    """Bing turned the request away (rate limit or block)."""

    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")
        self.status = status


class BingTimeout(BingError):
    """Bing did not answer in time."""


class BingUnreachable(BingError):
    """No connection to Bing could be made."""


class BingNoResultList(BingError):
    """Bing answered with a page that is not a result page (a challenge, a
    consent wall, an error page)."""


class BingOffQuery(BingError):
    """Bing answered with results for a different search."""


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def _query_words(query: str) -> List[str]:
    """The words of ``query`` a result is expected to mention, shortened so an
    inflected form still matches ("cleaning" finds "clean", "prune" "pruning")."""
    words = list(dict.fromkeys(re.findall(r"\w+", _plain(query))))
    telling = [w for w in words if len(w) >= 4 or (len(w) >= 2 and not w.isascii())]
    if len(telling) >= 2:
        words = telling
    return [w[:max(4, len(w) - 3)] if w.isascii() and w.isalpha() else w for w in words]


def _mentions(text: str, said: set, word: str) -> bool:
    """Latin-script words are matched from the start of a word ("erie" is not
    in "experience"); other scripts anywhere, since many write no spaces."""
    if word.isascii():
        return any(token.startswith(word) for token in said)
    return word in text


def answers_query(query: str, rows: List[Dict[str, str]]) -> bool:
    """False when ``rows`` are results for some other search than ``query``.

    A query of one word cannot be judged this way and always passes.
    """
    words = _query_words(query)
    if len(words) < 2 or not rows:
        return True
    on_query = 0
    for row in rows:
        text = _plain(" ".join((row.get("title", ""), row.get("body", ""), row.get("href", ""))))
        said = set(re.findall(r"\w+", text))
        if sum(_mentions(text, said, word) for word in words) >= 2:
            on_query += 1
    return on_query / len(rows) >= _ON_QUERY_SHARE


def _unwrap(href: str) -> str:
    """The address behind one of Bing's click-tracking links."""
    if not href.startswith("https://www.bing.com/ck/a?"):
        return href
    wrapped = parse_qs(urlsplit(href).query).get("u", [""])[0]
    if len(wrapped) <= 2:
        return href
    try:
        encoded = wrapped[2:]
        return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        return href


def _text(nodes) -> str:
    return " ".join("".join(nodes).split())


def parse_result_page(page: str) -> Optional[List[Dict[str, str]]]:
    """The results on a Bing page as ``{"title", "href", "body"}`` rows.

    ``[]`` when the page says Bing found nothing; ``None`` when the page is
    not a result page at all.

    Only the result list is read. The page also carries the interface strings
    of Bing's chat panel in a script block, and one of them is "There are no
    results for this question": text found anywhere in the page says nothing
    about the search.
    """
    if not page or not page.strip():
        return None
    tree = lxml_html.fromstring(page)
    rows: List[Dict[str, str]] = []
    seen = set()
    for item in tree.xpath("//li[contains(concat(' ', normalize-space(@class), ' '), ' b_algo ')]"):
        # The title link is <h2><a> on some pages and <a><h2> on others.
        link = item.xpath(".//h2/a/@href | .//a[h2]/@href")
        href = _unwrap(str(link[0]).strip()) if link else ""
        if not href.startswith(("http://", "https://")) or href.startswith("https://www.bing.com/aclick?"):
            continue
        title = _text(item.xpath(".//h2//text()"))
        if not title or href in seen:
            continue
        seen.add(href)
        body = _text(item.xpath(".//p//text()")).replace("\xa0", " ")
        rows.append({"title": title, "href": href, "body": body})
    if rows:
        return rows
    if tree.xpath("//ol[@id='b_results']/li[contains(concat(' ', normalize-space(@class), ' '), ' b_no ')]"):
        return []
    return None


def _browser_params(query: str, first: int = 0) -> Dict[str, str]:
    """What Bing's own search box sends with a query."""
    params = {
        "q": query, "form": "QBLH", "sp": "-1", "ghc": "1", "lq": "0", "pq": query,
        "sc": f"0-{len(query)}", "qs": "n", "sk": "", "cvid": secrets.token_hex(16).upper(),
    }
    if first:
        params["first"] = str(first)
        params["FORM"] = "PERE"
    return params


def _get(client, url: str, **kwargs):
    import primp

    try:
        response = client.get(url, **kwargs)
    except primp.TimeoutError as exc:
        raise BingTimeout("timed out") from exc
    except (primp.ConnectError, primp.DNSError) as exc:
        raise BingUnreachable(type(exc).__name__) from exc
    except primp.PrimpError as exc:
        raise BingError(type(exc).__name__) from exc
    host = urlsplit(str(response.url)).hostname or ""
    if host != "bing.com" and not host.endswith(".bing.com"):
        raise BingNoResultList(f"redirected to {host or 'another site'}")
    if response.status_code in _REFUSAL_STATUSES:
        raise BingRefused(response.status_code)
    if response.status_code != 200:
        raise BingError(f"HTTP {response.status_code}")
    return response


def search(query: str, max_results: int = RESULTS_PER_PAGE) -> List[Dict[str, str]]:
    """One search at Bing, in a new browser session: up to ``max_results`` rows
    of ``{"title", "href", "body"}``; ``[]`` when Bing says it found nothing.

    Raises a ``BingError`` when Bing could not be asked or did not answer the
    query; ``BingOffQuery`` when the page was about a different search.
    """
    import primp

    client = primp.Client(
        timeout=REQUEST_TIMEOUT_SECONDS, cookie_store=True, referer=True, follow_redirects=True,
        impersonate="random", impersonate_os=random.choice(_DESKTOP_SYSTEMS),
    )
    _get(client, HOME_URL)
    rows = parse_result_page(_get(client, SEARCH_URL, params=_browser_params(query)).text)
    if rows is None:
        raise BingNoResultList("no result list on the page")
    if not answers_query(query, rows):
        raise BingOffQuery("results for a different search")

    first = RESULTS_PER_PAGE + 1
    for _page in range(_MAX_PAGES - 1):
        if not rows or len(rows) >= max_results:
            break
        try:
            more = parse_result_page(_get(client, SEARCH_URL, params=_browser_params(query, first)).text)
        except BingError:
            break
        known = {row["href"] for row in rows}
        fresh = [row for row in more or [] if row["href"] not in known]
        if not fresh or not answers_query(query, fresh):
            break
        rows.extend(fresh)
        first += RESULTS_PER_PAGE
    return rows[:max_results]
