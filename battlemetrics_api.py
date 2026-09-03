"""Small helper around the BattleMetrics API, used to retrieve
server status and statistics.

Runs entirely on the local machine (no SSH involved) - only needs a
BattleMetrics API key and server ID.

The BattleMetrics API uses JSON:API responses and Bearer-token
authentication.
"""

import json
import urllib.error
import urllib.request


BATTLEMETRICS_API_BASE = "https://api.battlemetrics.com"


def _get(url, api_key, timeout=15):
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
        ) as resp:
            return json.loads(
                resp.read().decode("utf-8")
            )

    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode(
                "utf-8",
                errors="replace",
            )
        except Exception:
            body = ""

        if exc.code == 401:
            raise RuntimeError(
                "BattleMetrics API authentication failed. "
                "Check the API key in Settings."
            ) from exc

        if exc.code == 403:
            raise RuntimeError(
                "BattleMetrics API access was denied. "
                "Check the API key permissions."
            ) from exc

        if exc.code == 404:
            raise RuntimeError(
                "BattleMetrics server was not found. "
                "Check the server ID in Settings."
            ) from exc

        raise RuntimeError(
            f"BattleMetrics API returned HTTP {exc.code}."
            + (
                f" Response: {body[:300]}"
                if body
                else ""
            )
        ) from exc

    except urllib.error.URLError as exc:
        raise RuntimeError(
            "Could not connect to the BattleMetrics API: "
            f"{exc.reason}"
        ) from exc

    except TimeoutError as exc:
        raise RuntimeError(
            "BattleMetrics API request timed out."
        ) from exc

    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "BattleMetrics API returned invalid JSON."
        ) from exc


def get_server_stats(api_key, server_id):
    """Return current BattleMetrics information for a server.

    The returned dictionary contains the commonly useful server
    attributes for display in the Server Status panel.

    Returns None when the server resource is missing or malformed.
    """

    if not api_key:
        raise RuntimeError(
            "Set a BattleMetrics API key in the Settings tab first."
        )

    if not server_id:
        raise RuntimeError(
            "Set a BattleMetrics server ID in the Settings tab first."
        )

    server_id = str(server_id).strip()

    if not server_id:
        raise RuntimeError(
            "Set a BattleMetrics server ID in the Settings tab first."
        )

    data = _get(
        f"{BATTLEMETRICS_API_BASE}/servers/{server_id}",
        api_key,
    )

    resource = data.get("data")

    if not isinstance(resource, dict):
        return None

    attributes = resource.get("attributes")

    if not isinstance(attributes, dict):
        return None

    # BattleMetrics returns player count and maximum player
    # count directly in the server attributes.
    players = attributes.get("players")
    max_players = attributes.get("maxPlayers")

    try:
        if players is not None:
            players = int(players)
    except (TypeError, ValueError):
        players = None

    try:
        if max_players is not None:
            max_players = int(max_players)
    except (TypeError, ValueError):
        max_players = None

    # Server details contain game-specific information such as
    # the current map.
    details = attributes.get("details")

    if not isinstance(details, dict):
        details = {}

    return {
        "id": str(
            resource.get("id")
            or server_id
        ),

        "name": (
            attributes.get("name")
            or "(unknown server)"
        ).strip(),

        "status": (
            attributes.get("status")
            or "unknown"
        ).strip().lower(),

        "players": players,

        "max_players": max_players,

        "map": (
            details.get("map")
            or ""
        ).strip(),

        "ip": (
            attributes.get("ip")
            or ""
        ).strip(),

        "port": attributes.get("port"),

        "country": (
            attributes.get("country")
            or ""
        ).strip(),

        "rank": attributes.get("rank"),

        "updated_at": (
            attributes.get("updatedAt")
            or ""
        ),

        "created_at": (
            attributes.get("createdAt")
            or ""
        ),
    }
