"""Where a web search goes and how many results it may return.

Read by the search code (backend/api/web_search_api.py) and by the web_search
tool's description (backend/tools/web_tools.py), so what the tool says and what
it does come from one place.
"""

# A search asks www.bing.com's public result page (backend/utils/bing_search.py)
# and reads the result list from it; no key, no account.
# tests/unit/test_web_search_sources.py watches which host is called, so a
# change that searches elsewhere fails there instead of leaving this label wrong.
#
# The engine is the only place a search query goes. When it finds nothing,
# cannot be asked, or answers a different search, the search says so; the
# query is not sent anywhere else.
SEARCH_ENGINE = "Bing"

# A question about the weather in a named place is answered by this service.
WEATHER_SOURCE = "wttr.in"

# Results asked for by default, and the most a caller may ask for. A result
# page holds about ten, so 20 keeps one search to two or three result pages.
DEFAULT_SEARCH_RESULTS = 5
MAX_SEARCH_RESULTS = 20

# The longest query chat sends: enhanced chat holds the person's message to it,
# the web_search tool the query a model or MCP client writes. The whole query
# goes to the engine, so a longer one is a paste or a brief and is refused unsent.
MAX_SEARCH_QUERY_CHARS = 300
