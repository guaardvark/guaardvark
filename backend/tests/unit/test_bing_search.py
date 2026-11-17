"""Reading Bing's result page: the result list is parsed from pages saved from
www.bing.com (backend/tests/fixtures/web_search, cut down to the list), a page
about another search is told from one about the query, and a search asks again
or says why it has no answer. The HTTP client is replaced; nothing is sent."""

from pathlib import Path
from urllib.parse import urlsplit

import pytest

from backend.api import web_search_api
from backend.utils import bing_search
from backend.utils.web_search_sources import SEARCH_ENGINE

primp = pytest.importorskip("primp")

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "web_search"


def page(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class _Page:
    def __init__(self, url, text="", status=200):
        self.url, self.text, self.status_code = url, text, status


@pytest.fixture
def bing(monkeypatch):
    """Replace the HTTP client. ``pages`` are served to /search in order, the
    last one again once they run out; the home page is always an empty 200."""
    state = {"pages": [], "asked": []}

    class FakeClient:
        def __init__(self, **kwargs):
            state["client"] = kwargs

        def get(self, url, params=None, **kwargs):
            state["asked"].append((url, dict(params or {})))
            if url == bing_search.HOME_URL:
                return _Page(url)
            searches = sum(1 for asked_url, _ in state["asked"] if asked_url == bing_search.SEARCH_URL)
            return _Page(url, state["pages"][min(searches, len(state["pages"])) - 1])

    monkeypatch.setattr(primp, "Client", FakeClient)
    return state


def test_a_result_page_is_read_though_it_carries_the_no_results_string():
    """The page holds ten results and, in a script block, the chat panel's
    "There are no results for this question". An earlier client took that
    string for the answer and returned nothing for every such page."""
    text = page("bing_results_desktop.html")
    assert "There are no results for" in text
    rows = bing_search.parse_result_page(text)
    assert len(rows) == 10
    assert rows[0]["title"] == "Python's asyncio: A Hands-On Walkthrough \u2013 Real Python"
    assert rows[0]["href"] == "https://realpython.com/async-io-python/"
    assert all(row["title"] and row["body"] for row in rows)


def test_click_tracking_links_are_unwrapped():
    text = page("bing_results_desktop.html")
    assert text.count("https://www.bing.com/ck/a?") >= 10
    hosts = [urlsplit(row["href"]).hostname for row in bing_search.parse_result_page(text)]
    assert "www.bing.com" not in hosts
    assert hosts[:3] == ["realpython.com", "www.geeksforgeeks.org", "docs.python.org"]


def test_the_short_page_with_the_heading_inside_the_link_is_read():
    """Phone browsers get this layout: <a><h2> where the full page has <h2><a>."""
    text = page("bing_results_short.html")
    assert "<h2><a" not in text
    rows = bing_search.parse_result_page(text)
    assert len(rows) == 8
    assert rows[0]["title"] == "Kayak Hull Damage: When To Repair vs. Replace"
    assert rows[0]["href"].startswith("https://kakukayaks.com/")


def test_a_page_without_a_result_list_is_not_an_empty_answer():
    assert bing_search.parse_result_page(page("bing_empty_list.html")) is None
    assert bing_search.parse_result_page("<html><body><form id='challenge'></form></body></html>") is None
    assert bing_search.parse_result_page("") is None


def test_a_page_that_says_nothing_was_found_is_an_empty_answer():
    """Written by hand: Bing did not send this page for any query tried on
    2026-11-16, it sent results for other searches instead. b_no is the class
    its no-results item has carried."""
    said = ("<html><body><ol id='b_results'><li class='b_no'><h1>There are no results for "
            "<strong>zqxjkv</strong></h1></li></ol></body></html>")
    assert bing_search.parse_result_page(said) == []


def test_advertisements_and_links_that_are_not_web_addresses_are_left_out():
    listed = ("<html><body><ol id='b_results'>"
              "<li class='b_algo'><h2><a href='https://www.bing.com/aclick?ld=x'>An advertisement</a></h2></li>"
              "<li class='b_algo'><h2><a href='javascript:void(0)'>A control</a></h2></li>"
              "<li class='b_algo'><h2><a href='https://example.org/a'>A result</a></h2><p>Its text.</p></li>"
              "<li class='b_algo'><h2><a href='https://example.org/a'>The same result again</a></h2></li>"
              "<li class='b_algo_group'><h2><a href='https://example.org/b'>Not a result item</a></h2></li>"
              "</ol></body></html>")
    assert bing_search.parse_result_page(listed) == [
        {"title": "A result", "href": "https://example.org/a", "body": "Its text."}]


@pytest.mark.parametrize("fixture,query,expected", [
    ("bing_results_desktop.html", "python asyncio tutorial", True),
    ("bing_results_short.html", "is it worth replacing a worn kayak hull", True),
    # Bing answered "history of the Erie Lackawanna railroad" with pages about the word "history".
    ("bing_off_query.html", "history of the Erie Lackawanna railroad", False),
    ("bing_results_desktop.html", "history of the Erie Lackawanna railroad", False),
])
def test_results_for_another_search_are_told_from_results_for_the_query(fixture, query, expected):
    rows = bing_search.parse_result_page(page(fixture))
    assert bing_search.answers_query(query, rows) is expected


@pytest.mark.parametrize("query,title,expected", [
    ("asyncio", "Anything at all", True),                                   # one word cannot be judged
    ("how to prune hydrangea bushes", "Pruning Hydrangeas In Fall", True),  # inflected forms
    ("mejores recetas de paella valenciana", "Receta de Paella Valenciana tradicional", True),
    ("wie funktioniert eine W\u00e4rmepumpe im Winter", "Warmepumpe im Winter: so funktioniert sie", True),
    ("wie funktioniert eine W\u00e4rmepumpe im Winter", "IEEE Women In Engineering | IEEE WIE", False),
    ("\u043a\u0442\u043e \u043d\u0430\u043f\u0438\u0441\u0430\u043b \u043c\u0430\u0441\u0442\u0435\u0440 \u0438 "
     "\u043c\u0430\u0440\u0433\u0430\u0440\u0438\u0442\u0430",
     "\u041c\u0430\u0441\u0442\u0435\u0440 \u0438 \u041c\u0430\u0440\u0433\u0430\u0440\u0438\u0442\u0430", True),
    ("best mechanical keyboard under 100 dollars", "Best Buy | Official Online Store", False),
    ("history of the Erie Lackawanna railroad", "Your browsing history: a better experience", False),
    ("\u6771\u4eac \u304a\u3059\u3059\u3081 \u30e9\u30fc\u30e1\u30f3 \u5e97",
     "\u6771\u4eac\u306e\u304a\u3059\u3059\u3081\u30e9\u30fc\u30e1\u30f3\u5e9710\u9078", True),   # no spaces between words
])
def test_the_on_query_check_across_kinds_of_query(query, title, expected):
    rows = [{"title": title, "href": "https://example.org/", "body": ""}] * 5
    assert bing_search.answers_query(query, rows) is expected


def test_a_search_visits_the_home_page_then_asks_as_a_desktop_browser(bing):
    bing["pages"] = [page("bing_results_desktop.html")]
    rows = bing_search.search("python asyncio tutorial", max_results=5)
    assert len(rows) == 5 and rows[0]["href"] == "https://realpython.com/async-io-python/"
    assert [url for url, _ in bing["asked"]] == [bing_search.HOME_URL, bing_search.SEARCH_URL]
    params = bing["asked"][1][1]
    assert params["q"] == params["pq"] == "python asyncio tutorial" and params["form"] == "QBLH"
    assert bing["client"]["impersonate_os"] in ("windows", "macos", "linux")
    assert bing["client"]["cookie_store"] is True


def test_a_second_page_is_asked_for_only_when_more_results_are_wanted(bing):
    second = page("bing_results_desktop.html").replace("https://www.bing.com/ck/a?u=", "https://example.org/asyncio?u=")
    bing["pages"] = [page("bing_results_desktop.html"), second]
    rows = bing_search.search("python asyncio tutorial", max_results=15)
    assert len(rows) == 15 and len({row["href"] for row in rows}) == 15
    searches = [params for url, params in bing["asked"] if url == bing_search.SEARCH_URL]
    assert len(searches) == 2 and searches[1]["first"] == "11"


def test_results_for_another_search_are_refused_and_the_search_asked_again(bing):
    """The first answer is about the word "history"; the second is about the query."""
    bing["pages"] = [page("bing_off_query.html"), page("bing_results_short.html")]
    with pytest.raises(bing_search.BingOffQuery):
        bing_search.search("is it worth replacing a worn kayak hull")
    bing["asked"].clear()
    bing["pages"] = [page("bing_off_query.html"), page("bing_results_short.html")]
    found = web_search_api.perform_duckduckgo_search("is it worth replacing a worn kayak hull")
    assert found["success"] and found["source"] == SEARCH_ENGINE
    assert found["results"][0]["title"] == "Kayak Hull Damage: When To Repair vs. Replace"
    assert "My Activity" not in str(found)
    assert sum(1 for url, _ in bing["asked"] if url == bing_search.SEARCH_URL) == 2


def test_only_off_query_answers_end_as_a_failure_not_as_no_results(bing):
    bing["pages"] = [page("bing_off_query.html")]
    result = web_search_api.perform_duckduckgo_search("is it worth replacing a worn kayak hull")
    assert not result["success"] and "no_results" not in result and result["results"] == []
    assert result["error"].startswith(f"{SEARCH_ENGINE} did not answer this search")
    assert sum(1 for url, _ in bing["asked"] if url == bing_search.SEARCH_URL) == web_search_api._SEARCH_ATTEMPTS
    api = web_search_api.enhanced_web_search("is it worth replacing a worn kayak hull")
    assert api["data"]["type"] == "search_failed"


def test_an_empty_list_page_ends_as_a_failure_not_as_no_results(bing):
    bing["pages"] = [page("bing_empty_list.html")]
    result = web_search_api.perform_duckduckgo_search("is it worth replacing a worn air fryer basket")
    assert not result["success"] and "no_results" not in result
    assert "no result list" in result["error"]


def test_a_redirect_off_the_engine_is_not_read_as_an_answer(monkeypatch):
    class Elsewhere:
        def __init__(self, **kwargs):
            pass

        def get(self, url, params=None, **kwargs):
            return _Page("https://login.example.net/consent", page("bing_results_desktop.html"))

    monkeypatch.setattr(primp, "Client", Elsewhere)
    with pytest.raises(bing_search.BingNoResultList):
        bing_search.search("python asyncio tutorial")
