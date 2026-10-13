"""Client minimal du plugin « API Server » de Pear Desktop (v3.12, API v1).

Référence : src/plugins/api-server/ du dépôt pear-devs/pear-desktop.
Uniquement la bibliothèque standard, pour rester trivial à installer sous Windows.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

API = "/api/v1"


class PearApiError(Exception):
    """Erreur de communication avec une instance Pear Desktop."""


@dataclass(frozen=True)
class QueueEntry:
    index: int
    video_id: str
    selected: bool
    title: str = ""
    artist: str = ""


def _runs_text(node: Any) -> str:
    """Texte d'un champ YouTube {"runs": [{"text": …}]} ou {"simpleText": …}."""
    if not isinstance(node, dict):
        return ""
    if isinstance(node.get("simpleText"), str):
        return node["simpleText"]
    return "".join(r.get("text", "") for r in node.get("runs") or [] if isinstance(r, dict))


def _renderer(item: dict[str, Any]) -> dict[str, Any] | None:
    """Extrait le playlistPanelVideoRenderer d'un élément de file.

    Un élément est soit un renderer direct, soit un « wrapper » (piste audio +
    clip vidéo associé) dont le renderer principal est dans primaryRenderer.
    """
    direct = item.get("playlistPanelVideoRenderer")
    if direct:
        return direct
    wrapper = item.get("playlistPanelVideoWrapperRenderer") or {}
    return (wrapper.get("primaryRenderer") or {}).get("playlistPanelVideoRenderer")


def _alt_video_ids(item: dict[str, Any]) -> set[str]:
    """videoId des contreparties (version clip/audio) d'un élément wrapper."""
    wrapper = item.get("playlistPanelVideoWrapperRenderer") or {}
    ids = set()
    for cp in wrapper.get("counterpart") or []:
        vid = ((cp.get("counterpartRenderer") or {}).get("playlistPanelVideoRenderer") or {}).get("videoId")
        if vid:
            ids.add(vid)
    return ids


def parse_queue(payload: dict[str, Any] | None) -> list[QueueEntry]:
    entries = []
    for i, item in enumerate((payload or {}).get("items") or []):
        r = _renderer(item)
        if r and r.get("videoId"):
            # shortBylineText : « Artiste » ; longBylineText : « Artiste • Album • Année »
            artist = _runs_text(r.get("shortBylineText")) or _runs_text(r.get("longBylineText")).split(" • ")[0]
            entries.append(QueueEntry(i, r["videoId"], bool(r.get("selected")), _runs_text(r.get("title")), artist))
    return entries


def queue_index_of(payload: dict[str, Any] | None, video_id: str, after: int = -1) -> int | None:
    """Premier index > after dont l'élément correspond à video_id (contreparties incluses)."""
    for i, item in enumerate((payload or {}).get("items") or []):
        if i <= after:
            continue
        r = _renderer(item) or {}
        if r.get("videoId") == video_id or video_id in _alt_video_ids(item):
            return i
    return None


class PearClient:
    """Accès à une instance via son API HTTP.

    token=None convient si l'instance est en authStrategy NONE ; sinon obtenir
    un jeton avec request_token() (déclenche une boîte de dialogue « Autoriser »
    sur la machine cible la première fois).
    """

    def __init__(self, base_url: str, token: str | None = None, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    # -- transport ---------------------------------------------------------
    def _request(self, method: str, path: str, body: Any = None, timeout: float | None = None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read()
                if resp.status == 204 or not raw:
                    return None
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise PearApiError(
                    f"{self.base_url} : 401 non autorisé (jeton absent ou révoqué, relancer « auth »)"
                ) from e
            raise PearApiError(f"{self.base_url}{path} : HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise PearApiError(f"{self.base_url} injoignable : {getattr(e, 'reason', e)}") from e

    # -- authentification -------------------------------------------------
    def request_token(self, client_id: str, timeout: float = 120.0) -> str:
        """POST /auth/{id}. Long délai : l'utilisateur doit cliquer « Autoriser » côté cible."""
        res = self._request("POST", f"/auth/{urllib.parse.quote(client_id, safe='')}", timeout=timeout)
        if not res or "accessToken" not in res:
            raise PearApiError(f"{self.base_url} : demande d'autorisation refusée")
        self.token = res["accessToken"]
        return self.token

    # -- lecture -----------------------------------------------------------
    def song(self) -> dict[str, Any] | None:
        return self._request("GET", f"{API}/song")

    def queue(self) -> dict[str, Any] | None:
        return self._request("GET", f"{API}/queue")

    # -- contrôle ----------------------------------------------------------
    def play(self) -> None:
        self._request("POST", f"{API}/play")

    def pause(self) -> None:
        self._request("POST", f"{API}/pause")

    def next(self) -> None:
        self._request("POST", f"{API}/next")

    def previous(self) -> None:
        self._request("POST", f"{API}/previous")

    def volume(self) -> int | None:
        """Volume du lecteur (0-100), None si l'API ne le donne pas."""
        res = self._request("GET", f"{API}/volume")
        if not isinstance(res, dict) or not isinstance(res.get("state"), int | float):
            return None
        return 0 if res.get("isMuted") else int(res["state"])

    def set_volume(self, level: int) -> None:
        self._request("POST", f"{API}/volume", {"volume": int(min(100, max(0, level)))})

    def seek(self, seconds: float) -> None:
        self._request("POST", f"{API}/seek-to", {"seconds": max(0.0, float(seconds))})

    def add_to_queue(self, video_id: str, after_current: bool = True) -> None:
        pos = "INSERT_AFTER_CURRENT_VIDEO" if after_current else "INSERT_AT_END"
        self._request("POST", f"{API}/queue", {"videoId": video_id, "insertPosition": pos})

    def set_queue_index(self, index: int) -> None:
        self._request("PATCH", f"{API}/queue", {"index": int(index)})

    def remove_from_queue(self, index: int) -> None:
        self._request("DELETE", f"{API}/queue/{int(index)}")
