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

# Steam GetPlayerSummaries accepts multiple SteamID64 values in one request.
STEAM_IDS_PER_REQUEST = 100


def _get(url, params, timeout=15):
    query = urllib.parse.urlencode(params)
    full_url = f"{url}?{query}"

    request = urllib.request.Request(
        full_url,
        headers={
            "User-Agent": "DayZServerManager/1.0",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=timeout,
    ) as resp:
        return json.loads(
            resp.read().decode("utf-8")
        )


def _get_steam_display_names(
    api_key,
    steam_ids,
):
    """Resolve multiple SteamID64 values to Steam display names.

    Steam's GetPlayerSummaries endpoint supports multiple SteamIDs in one
    request. This avoids making one HTTP request per Workshop result.

    Returns a dictionary:
        {
            "7656119...": "SteamName",
            ...
        }
    """

    if not api_key:
        return {}

    cleaned_ids = []

    for steam_id in steam_ids:
        steam_id = str(
            steam_id or ""
        ).strip()

        if steam_id and steam_id not in cleaned_ids:
            cleaned_ids.append(steam_id)

    if not cleaned_ids:
        return {}

    display_names = {}

    # Keep requests comfortably within Steam's supported URL/request size.
    for start in range(
        0,
        len(cleaned_ids),
        STEAM_IDS_PER_REQUEST,
    ):
        batch = cleaned_ids[
            start:start + STEAM_IDS_PER_REQUEST
        ]

        try:
            data = _get(
                f"{STEAM_API_BASE}/ISteamUser/GetPlayerSummaries/v2/",
                {
                    "key": api_key,
                    "steamids": ",".join(batch),
                },
            )
        except Exception:
            # Profile lookup should never break Workshop searching.
            continue

        players = (
            (data.get("response") or {})
            .get("players")
            or []
        )

        for player in players:
            steam_id = str(
                player.get("steamid")
                or ""
            ).strip()

            display_name = str(
                player.get("personaname")
                or ""
            ).strip()

            if steam_id and display_name:
                display_names[
                    steam_id
                ] = display_name

    return display_names


def _get_uploader_steam_id(item):
    """Return the Workshop item's uploader SteamID64."""

    return str(
        item.get("creator")
        or item.get("steamid")
        or ""
    ).strip()


def _get_uploader_name(
    steam_id,
    display_names,
):
    """Return a display name, falling back to SteamID64."""

    steam_id = str(
        steam_id or ""
    ).strip()

    if not steam_id:
        return "Unknown"

    return (
        display_names.get(
            steam_id,
            "",
        ).strip()
        or steam_id
    )


def search_workshop(
    api_key,
    query,
    page=1,
    per_page=20,
):
    if not api_key:
        raise RuntimeError(
            "Set a Steam Web API key in the Settings tab first."
        )

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

    # Collect all uploader SteamIDs first.
    steam_ids = []

    for item in items:
        steam_id = _get_uploader_steam_id(
            item
        )

        if steam_id:
            steam_ids.append(steam_id)

    # Resolve all uploader names in one batched API call instead of
    # making one request for every Workshop result.
    display_names = _get_steam_display_names(
        api_key,
        steam_ids,
    )

    results = []

    for item in items:
        steam_id = _get_uploader_steam_id(
            item
        )

        uploader = _get_uploader_name(
            steam_id,
            display_names,
        )

        results.append(
            {
                "id": str(
                    item.get("publishedfileid")
                ),
                "title": (
                    item.get("title")
                    or "(untitled)"
                ),
                "subscriptions": item.get(
                    "subscriptions",
                    0,
                ),
                "uploader": uploader,
                "steam_id": steam_id,
                "preview_url": str(
                    item.get("preview_url")
                    or ""
                ).strip(),
                "description": (
                    item.get(
                        "short_description"
                    )
                    or ""
                ).strip()[:200],
            }
        )

    return results


def get_details(
    api_key,
    workshop_id,
):
    """Return detailed information about a Workshop item.

    Includes the Steam Workshop preview URL, creator/uploader,
    SteamID64, and time_updated so the Mods panel can display
    the preview and compare Workshop update timestamps.
    """

    if not api_key:
        raise RuntimeError(
            "Set a Steam Web API key in the Settings tab first."
        )

    data = _get(
        f"{STEAM_API_BASE}/IPublishedFileService/GetDetails/v1/",
        {
            "key": api_key,
            "publishedfileids[0]": str(
                workshop_id
            ),
        },
    )

    items = (
        (data.get("response") or {})
        .get("publishedfiledetails")
        or []
    )

    if not items:
        return None

    item = items[0]

    if item.get("result") not in (
        1,
        "1",
        None,
    ):
        return None

    time_updated = item.get(
        "time_updated"
    )

    try:
        if time_updated is not None:
            time_updated = int(
                time_updated
            )
    except (
        TypeError,
        ValueError,
    ):
        time_updated = None

    steam_id = _get_uploader_steam_id(
        item
    )

    # get_details() is normally used for one selected Workshop item,
    # so only one profile lookup is needed here.
    display_names = _get_steam_display_names(
        api_key,
        [steam_id],
    )

    uploader = _get_uploader_name(
        steam_id,
        display_names,
    )

    return {
        "id": str(
            item.get("publishedfileid")
        ),
        "title": (
            item.get("title")
            or "(untitled)"
        ),
        "subscriptions": item.get(
            "subscriptions",
            0,
        ),
        "uploader": uploader,
        "steam_id": steam_id,
        "preview_url": str(
            item.get("preview_url")
            or ""
        ).strip(),
        "time_updated": time_updated,
    }

