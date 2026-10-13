"""Capture et restitution d'un état de lecture.

L'API Pear n'a pas d'endpoint « jouer ce videoId » : on insère le titre juste
après le morceau courant (POST /queue), on attend qu'il apparaisse (l'insertion
est asynchrone côté renderer), on bascule l'index (PATCH /queue), puis on
recale la position (POST /seek-to).

Ce mode « file » ne transporte pas le contexte (mix, playlist) : quand un
`context_loader` est fourni (instance locale, voir launcher.py), il est utilisé
en priorité pour rouvrir le titre DANS son mix.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from .pear_api import PearApiError, PearClient, parse_queue, queue_index_of

log = logging.getLogger("pearsync.handoff")

_PLAYLIST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{2,128}$")


class HandoffError(Exception):
    pass


class ContextLoadError(Exception):
    """Le contexte (mix…) n'a pas pu être rechargé ; le transfert continue en mode file."""


@dataclass
class PlaybackState:
    video_id: str
    position: float
    paused: bool
    duration: float = 0.0
    title: str = ""
    artist: str = ""
    upcoming: list[str] = field(default_factory=list)  # videoId des titres suivants
    playlist_id: str = ""  # contexte de lecture : mix (RD…), playlist (PL…), album (OLAK…)
    ts: float = field(default_factory=time.time)  # horodatage de la capture (epoch)
    queue: list[dict[str, str]] = field(default_factory=list)  # titres à venir avec titre/artiste (affichage)
    volume: int | None = None  # 0-100

    def position_now(self, now: float | None = None) -> float:
        """Position extrapolée : un état « en lecture » vieillit avec le temps."""
        pos = self.position
        if not self.paused:
            pos += max(0.0, (now or time.time()) - self.ts)
        if self.duration > 0:
            pos = min(pos, max(0.0, self.duration - 1))
        return pos

    def music_url(self, now: float | None = None) -> str:
        url = f"https://music.youtube.com/watch?v={self.video_id}"
        if self.playlist_id:
            url += f"&list={self.playlist_id}"
        return f"{url}&t={int(self.position_now(now))}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PlaybackState:
        if not isinstance(d, dict) or not isinstance(d.get("video_id"), str) or not d["video_id"]:
            raise HandoffError("état de lecture invalide")
        return cls(
            video_id=d["video_id"],
            position=float(d.get("position") or 0),
            paused=bool(d.get("paused")),
            duration=float(d.get("duration") or 0),
            title=str(d.get("title") or ""),
            artist=str(d.get("artist") or ""),
            # le serveur peut ne donner que le nombre de titres à venir (vue résumée) : on l'ignore alors
            upcoming=[v for v in up if isinstance(v, str)][:200] if isinstance(up := d.get("upcoming"), list) else [],
            playlist_id=_clean_playlist_id(d.get("playlist_id")),
            ts=float(d.get("ts") or time.time()),
            queue=_clean_queue(d.get("queue")),
            volume=int(v) if isinstance(v := d.get("volume"), int | float) and not isinstance(v, bool) else None,
        )


def _clean_queue(value: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in value if isinstance(value, list) else []:
        if isinstance(row, dict) and isinstance(row.get("video_id"), str) and row["video_id"]:
            out.append(
                {
                    "video_id": row["video_id"][:64],
                    "title": str(row.get("title") or "")[:200],
                    "artist": str(row.get("artist") or "")[:200],
                }
            )
    return out[:200]


def _clean_playlist_id(value: Any) -> str:
    return value if isinstance(value, str) and _PLAYLIST_ID_RE.match(value) else ""


def capture_state(client: PearClient, queue_limit: int = 25, with_volume: bool = False) -> PlaybackState | None:
    """Lit l'état courant ; None si rien n'est chargé."""
    song = client.song()
    if not song or not song.get("videoId"):
        return None
    upcoming: list[str] = []
    queue: list[dict[str, str]] = []
    if queue_limit > 0:
        entries = parse_queue(client.queue())
        sel = next((e.index for e in entries if e.selected), None)
        if sel is not None:
            after = [e for e in entries if e.index > sel][:queue_limit]
            upcoming = [e.video_id for e in after]
            queue = [{"video_id": e.video_id, "title": e.title, "artist": e.artist} for e in after]
    volume = None
    if with_volume:
        try:
            volume = client.volume()
        except PearApiError:
            volume = None  # ancienne version de l'API
    return PlaybackState(
        queue=queue,
        volume=volume,
        video_id=song["videoId"],
        position=float(song.get("elapsedSeconds") or 0),
        paused=bool(song.get("isPaused")),
        duration=float(song.get("songDuration") or 0),
        title=song.get("title") or "",
        artist=song.get("artist") or "",
        upcoming=upcoming,
        playlist_id=_clean_playlist_id(song.get("playlistId")),
    )


def _wait(pred: Callable[[], Any], timeout: float, interval: float) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        val = pred()
        if val is not None and val is not False:
            return val
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)


def _song_or_none(client: PearClient) -> dict[str, Any]:
    """/song, en tolérant que l'API soit momentanément absente (redémarrage de Pear)."""
    try:
        return client.song() or {}
    except PearApiError:
        return {}


def _selected_index(q: dict[str, Any] | None) -> int:
    return next((e.index for e in parse_queue(q) if e.selected), -1)


def _load_track(client: PearClient, video_id: str, timeout: float, interval: float) -> None:
    current = client.song() or {}
    if current.get("videoId") == video_id:
        return

    # Déjà dans la suite de la file (transfert répété, aller-retour) : on y saute plutôt que
    # de l'insérer une nouvelle fois, sinon le titre s'empile entre les morceaux du mix.
    q = client.queue()
    existing = queue_index_of(q, video_id, after=_selected_index(q))
    if existing is not None:
        client.set_queue_index(existing)
        if _wait(lambda: (client.song() or {}).get("videoId") == video_id, timeout, interval) is None:
            raise HandoffError("la cible n'a pas basculé sur le titre demandé")
        return

    client.add_to_queue(video_id, after_current=True)

    def inserted_index() -> int | None:
        q = client.queue()
        return queue_index_of(q, video_id, after=_selected_index(q))

    idx = _wait(inserted_index, timeout, interval)
    if idx is None:
        raise HandoffError(
            "le titre n'est pas apparu dans la file de la cible. Si la cible n'a encore rien "
            "joué depuis son démarrage, lance n'importe quel titre dessus puis réessaie."
        )
    client.set_queue_index(idx)
    if _wait(lambda: (client.song() or {}).get("videoId") == video_id, timeout, interval) is None:
        raise HandoffError("la cible n'a pas basculé sur le titre demandé")


def _replace_upcoming(client: PearClient, upcoming: list[str], timeout: float, interval: float) -> None:
    """Remplace ce qui suit le titre courant par la file de la source, dans l'ordre."""
    q = client.queue()
    sel = _selected_index(q)
    total = len((q or {}).get("items") or [])
    for i in range(total - 1, sel, -1):  # de la fin vers le courant : les index restent valides
        client.remove_from_queue(i)

    def queue_len() -> int:
        return len((client.queue() or {}).get("items") or [])

    for vid in upcoming:
        before = queue_len()
        client.add_to_queue(vid, after_current=False)

        def grown(n: int = before) -> bool:
            return queue_len() > n

        # ajout asynchrone : on attend chaque insertion pour garder l'ordre
        _wait(grown, timeout, interval)


def apply_state(
    client: PearClient,
    state: PlaybackState,
    *,
    with_queue: bool = True,
    timeout: float = 15.0,
    interval: float = 0.3,
    seek_tolerance: float = 2.5,
    context_loader: Callable[[PlaybackState], None] | None = None,
    restart_timeout: float = 90.0,
) -> None:
    """Rejoue `state` sur `client`.

    `context_loader(state)` doit faire repartir la cible sur le titre dans son
    contexte (mix…) ; s'il lève une exception, on retombe sur le mode « file ».
    """
    context_loaded = False
    if context_loader:
        current = _song_or_none(client)
        if state.playlist_id and current.get("playlistId") == state.playlist_id:
            context_loaded = True  # la cible est déjà dans ce mix : la file suffit pour changer de titre
        elif state.playlist_id or not current:
            # mix à recharger, ou Pear fermé / sans file (le mode file échouerait) : (re)lancement
            try:
                context_loader(state)
            except ContextLoadError as e:
                log.warning("(re)lancement de Pear impossible (%s) : transfert en mode file", e)
            else:
                ok = _wait(lambda: _song_or_none(client).get("videoId") == state.video_id, restart_timeout, 1.0)
                if not ok:
                    raise HandoffError("Pear n'a pas rouvert le titre après (re)lancement")
                context_loaded = bool(state.playlist_id)
    elif state.playlist_id:
        log.warning("mix %s non rechargeable sur cette cible : seule la file est recopiée", state.playlist_id)

    _load_track(client, state.video_id, timeout, interval)
    # Dans le mix rechargé, YouTube Music gère lui-même la suite (et sa génération
    # infinie) : on n'y touche pas pour ne pas risquer de casser sa continuation.
    if with_queue and state.upcoming and not context_loaded:
        _replace_upcoming(client, state.upcoming, timeout, interval)

    # Juste après un changement de titre le lecteur peut ignorer le seek : on vérifie.
    for _ in range(4):
        target = state.position_now()
        client.seek(target)

        def landed(t: float = target) -> bool:
            elapsed = float((client.song() or {}).get("elapsedSeconds") or 0)
            # la lecture continue pendant la vérification : on tolère quelques secondes d'avance
            return t - seek_tolerance <= elapsed <= t + seek_tolerance + 2.0

        if _wait(landed, 2.0, interval):
            break
    else:
        raise HandoffError("la position n'a pas pu être appliquée sur la cible")

    if state.paused:
        client.pause()
    else:
        client.play()
