"""Agent : publie l'état local sur le relais et exécute les commandes reçues.

Tourne sur chaque PC. Ne fait que des requêtes sortantes (vers 127.0.0.1 pour
Pear, vers le relais en HTTPS) : rien à ouvrir côté pare-feu/box.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from .handoff import ContextLoadError, HandoffError, PlaybackState, apply_state, capture_state
from .launcher import PearLauncher, context_url
from .pear_api import PearApiError, PearClient, parse_queue, queue_index_of
from .relay_client import RelayClient, RelayError

log = logging.getLogger("pearsync.agent")

HEARTBEAT = 15.0  # s : republication même sans changement (le relais date l'état)
QUEUE_EVERY = 10.0  # s : la lecture de file passe par l'IPC du renderer, on l'espace
PRIME_EVERY = 30.0  # s : fréquence de mise à jour de l'URL de reprise quand Pear est fermé
DRIFT = 3.0  # s : écart de position au-delà duquel on republie


def _changed(old: PlaybackState | None, new: PlaybackState | None) -> bool:
    if (old is None) != (new is None):
        return True
    if old is None or new is None:
        return False
    if (old.video_id, old.paused, old.upcoming, old.volume) != (new.video_id, new.paused, new.upcoming, new.volume):
        return True
    return abs(old.position_now(new.ts) - new.position) > DRIFT


class Agent:
    """Rend l'instance Pear locale visible et pilotable depuis le serveur Connect."""

    def __init__(
        self,
        local: PearClient,
        relay: RelayClient,
        node: str,
        queue_limit: int = 25,
        context_loader: Callable[[PlaybackState], None] | None = None,
        launcher: PearLauncher | None = None,
    ) -> None:
        self.local = local
        self.relay = relay
        self.node = node
        self.queue_limit = queue_limit
        self.context_loader = context_loader
        self.launcher = launcher  # pour tenir l'URL de reprise à jour quand Pear est fermé
        self._published: PlaybackState | None = None
        self._published_at = 0.0
        self._queue_at = 0.0
        self._primed_at = 0.0
        self._primed_url = ""

    def handle(self, cmd: dict[str, Any]) -> None:
        kind = cmd.get("type")
        if kind == "pause":
            self.local.pause()
        elif kind == "play":
            self.local.play()
        elif kind == "next":
            self.local.next()
        elif kind == "previous":
            self.local.previous()
        elif kind == "seek":
            self.local.seek(float(cmd.get("seconds") or 0))
        elif kind == "volume":
            self.local.set_volume(int(cmd.get("level") or 0))
        elif kind == "jump":
            self._jump(str(cmd.get("video_id") or ""))
        elif kind == "enqueue":
            self.local.add_to_queue(str(cmd.get("video_id") or ""), after_current=bool(cmd.get("next")))
            self._queue_at = 0.0  # relire la file au prochain tour
        elif kind in ("remove", "move"):
            self._edit_queue(kind, str(cmd.get("video_id") or ""), cmd.get("index"), cmd.get("to"))
        elif kind == "resume":
            state = PlaybackState.from_dict(cmd.get("state") or {})
            log.info("reprise : %s — %s à %.0f s", state.artist, state.title, state.position_now())
            apply_state(
                self.local,
                state,
                with_queue=bool(cmd.get("with_queue", True)),
                context_loader=self.context_loader,
            )
        else:
            log.warning("commande ignorée : %r", kind)
        self._published_at = 0.0  # republier tout de suite l'état qui vient de changer

    def _jump(self, video_id: str) -> None:
        """Joue un titre déjà présent dans la file (vue « Lecture sur… » du téléphone)."""
        q = self.local.queue()
        sel = next((e.index for e in parse_queue(q) if e.selected), -1)
        idx = queue_index_of(q, video_id, after=sel)
        if idx is None:
            idx = queue_index_of(q, video_id)
        if idx is None:
            raise HandoffError("ce titre n'est plus dans la file")
        self.local.set_queue_index(idx)
        self._queue_at = 0.0

    def _edit_queue(self, kind: str, video_id: str, index: Any, to: Any) -> None:
        """Supprime ou déplace un titre « à suivre » (rangs comptés après le titre en cours)."""
        q = self.local.queue()
        entries = parse_queue(q)
        sel = next((e.index for e in entries if e.selected), -1)
        idx: int | None = None
        if isinstance(index, int):  # rang indiqué par l'appelant : on le prend s'il désigne bien ce titre
            cand = sel + 1 + index
            if any(e.index == cand and e.video_id == video_id for e in entries):
                idx = cand
        if idx is None:
            idx = queue_index_of(q, video_id, after=sel)
        if idx is None:
            raise HandoffError("ce titre n'est plus dans la file")
        if kind == "remove":
            self.local.remove_from_queue(idx)
        else:
            self.local.move_in_queue(idx, sel + 1 + max(0, int(to or 0)))
        self._queue_at = 0.0

    def _capture(self) -> PlaybackState | None:
        now = time.monotonic()
        refresh_queue = now - self._queue_at >= QUEUE_EVERY
        state = capture_state(self.local, self.queue_limit if refresh_queue else 0, with_volume=True)
        if refresh_queue:
            self._queue_at = now
        elif state and self._published and state.video_id == self._published.video_id:
            state.upcoming = self._published.upcoming  # file non relue ce tour-ci
            state.queue = self._published.queue
        return state

    def _prime_resume_url(self) -> None:
        """Pear fermé : sa reprise au démarrage pointera sur la session du serveur (titre + mix)."""
        if not self.launcher or time.monotonic() - self._primed_at < PRIME_EVERY:
            return
        self._primed_at = time.monotonic()
        session = (self.relay.snapshot().get("session") or {}).get("state")
        if not session:
            return
        try:
            state = PlaybackState.from_dict(session)
            url = context_url(state)
            if url != self._primed_url and self.launcher.prime_resume(state):
                self._primed_url = url
                log.info("reprise de Pear pointée sur %s", url)
        except (HandoffError, ContextLoadError) as e:
            log.debug("URL de reprise non mise à jour : %s", e)

    def tick(self, wait: float = 0.0) -> None:
        for cmd in self.relay.take_commands(self.node, wait):
            try:
                self.handle(cmd)
            except (HandoffError, PearApiError) as e:
                log.error("commande %s en échec : %s", cmd.get("type"), e)

        try:
            state = self._capture()
            pear_up = True
        except PearApiError:
            state, pear_up = None, False  # Pear fermé : l'appareil reste joignable (« disponible »)

        now = time.monotonic()
        if _changed(self._published, state) or now - self._published_at >= HEARTBEAT:
            self.relay.put_state(self.node, state)
            self._published, self._published_at = state, now
        if not pear_up:
            self._prime_resume_url()

    def run(self, interval: float = 3.0) -> None:
        log.info("agent « %s » démarré", self.node)
        backoff = interval
        while True:
            try:
                self.tick(wait=interval)  # long-poll : une commande est traitée dès son arrivée
                backoff = interval
            except RelayError as e:
                log.warning("%s", e)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
            except Exception:  # noqa: BLE001 — un agent d'arrière-plan ne doit jamais mourir en silence
                log.exception("erreur inattendue, l'agent continue")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
