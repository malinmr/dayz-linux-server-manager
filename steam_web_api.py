"""Small helper around the Steam Web API's IPublishedFileService, used to
search and inspect DayZ Workshop items.

Runs entirely on the local machine (no SSH involved) - only needs a Steam
Web API key from:
https://steamcommunity.com/dev/apikey
"""

import json
import urllib.parse
import urllib.request


STEAM_API_BASE = "https://api.steampowered.com"
DAYZ_APP_ID = 221100

# EPublishedFileQueryType.RankedByTextSearch, per Steamworks' clientenums.h
QUERY_TYPE_TEXT_SEARCH = 12


def _get(url, params, timeout=15):
    query = urllib.parse.urlencode(params)
    full_url = f"{url}?{query}"

    with urllib.request.urlopen(full_url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def search_workshop(api_key, query, page=1, per_page=20):
    if not api_key:
        raise RuntimeError("Set a Steam Web API key in the Settings tab first.")

    data = _get(
        f"{STEAM_API_BASE}/IPublishedFileService/QueryFiles/v1/",
        {
            "key": api_key,
            "query_type": QUERY_TYPE_TEXT_SEARCH,
            "search_text": query,
            "appid": DAYZ_APP_ID,
            "numperpage": per_page,
            "page": page,
            "return_short_description": True,
        },
    )

    items = (
        (data.get("response") or {})
        .get("publishedfiledetails")
        or []
    )

    return [
        {
            "id": str(it.get("publishedfileid")),
            "title": it.get("title") or "(untitled)",
            "subscriptions": it.get("subscriptions", 0),
            "description": (
                it.get("short_description") or ""
            ).strip()[:200],
        }
        for it in items
    ]


def get_details(api_key, workshop_id):
    """Return detailed information about a Workshop item.

    Important:
    time_updated is returned so the Mods panel can compare the current
    Steam Workshop update timestamp with the locally installed Workshop
    metadata.
    """

    if not api_key:
        raise RuntimeError(
            "Set a Steam Web API key in the Settings tab first."
        )

    data = _get(
        f"{STEAM_API_BASE}/IPublishedFileService/GetDetails/v1/",
        {
            "key": api_key,
            "publishedfileids[0]": str(workshop_id),
        },
    )

    items = (
        (data.get("response") or {})
        .get("publishedfiledetails")
        or []
    )

    if not items:
        return None

    it = items[0]

    # Steam normally returns result=1 for a successful item lookup.
    if it.get("result") not in (1, "1", None):
        return None

    # Steam returns time_updated as a Unix timestamp.
    time_updated = it.get("time_updated")

    try:
        if time_updated is not None:
            time_updated = int(time_updated)
    except (TypeError, ValueError):
        time_updated = None

    return {
        "id": str(it.get("publishedfileid")),
        "title": it.get("title") or "(untitled)",
        "subscriptions": it.get("subscriptions", 0),

        # IMPORTANT:
        # This was missing before, which caused the Mods panel to show
        # UNKNOWN because it had nothing to compare against.
        "time_updated": time_updated,
    }
