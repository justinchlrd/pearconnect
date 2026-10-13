"""Une instance Pear vue soit en direct (API HTTP joignable), soit via le relais.

Les deux exposent la même interface, ce qui permet de transférer dans les
quatre combinaisons : direct→direct (LAN/VPN), direct→relais, relais→direct,
relais→relais.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Protocol

from .config import Config, TokenStore
from .handoff import HandoffError, PlaybackState, apply_state, capture_state
from .pear_api import PearClient
from .relay_client import RelayClient


class Endpoint(Protocol):
    name: str

    def capture(self) -> PlaybackState | None: ...
    def pause(self) -> None: ...
    def apply(self, state: PlaybackState, with_queue: bool) -> None: ...


ContextLoader = Callable[[PlaybackState], None]


class DirectEndpoint:
    def __init__(
        self, name: str, client: PearClient, queue_limit: int, context_loader: ContextLoader | None = None
    ) -> None:
        self.name = name
        self.client = client
        self.queue_limit = queue_limit
        self.context_loader = context_loader  # seulement pour l'instance de CE poste

    def capture(self) -> PlaybackState | None:
        return capture_state(self.client, self.queue_limit)

    def pause(self) -> None:
        self.client.pause()

    def apply(self, state: PlaybackState, with_queue: bool) -> None:
        apply_state(self.client, state, with_queue=with_queue, context_loader=self.context_loader)


class RelayEndpoint:
    """Nœud joint uniquement via le relais : son agent exécute les commandes."""

    def __init__(self, name: str, relay: RelayClient) -> None:
        self.name = name
        self.relay = relay

    def capture(self) -> PlaybackState | None:
        return self.relay.get_state(self.name)  # None si l'appareil est hors ligne

    def pause(self) -> None:
        self.relay.send_command(self.name, {"type": "pause"})

    def apply(self, state: PlaybackState, with_queue: bool) -> None:
        self.relay.send_command(self.name, {"type": "resume", "state": state.to_dict(), "with_queue": with_queue})


def relay_client(cfg: Config) -> RelayClient:
    if not cfg.relay:
        raise HandoffError("aucun serveur Connect configuré ([relay])")
    return RelayClient(cfg.relay.url, cfg.relay.token, cafile=cfg.relay.ca)


def direct_client(cfg: Config, name: str, tokens: TokenStore | None = None) -> PearClient:
    url = cfg.instances[name]
    return PearClient(url, (tokens or TokenStore()).get(url))


def local_context_loader(cfg: Config) -> ContextLoader | None:
    """Rechargement de mix par redémarrage de Pear : possible uniquement sur l'instance locale."""
    if not cfg.pear_app.reload_mix or sys.platform != "win32":
        return None
    from .launcher import PearLauncher

    return PearLauncher(cfg.pear_app.config, cfg.pear_app.exe).load_context


def resolve(cfg: Config, name: str, tokens: TokenStore | None = None) -> Endpoint:
    if name in cfg.instances:
        loader = local_context_loader(cfg) if name == cfg.local else None
        return DirectEndpoint(name, direct_client(cfg, name, tokens), cfg.queue_limit, loader)
    if cfg.relay:
        return RelayEndpoint(name, relay_client(cfg))
    raise HandoffError(f"instance inconnue « {name} » (ni dans [instances], ni relais configuré)")


def transfer(src: Endpoint, dst: Endpoint, *, with_queue: bool = True, keep_source: bool = False) -> PlaybackState:
    if isinstance(src, RelayEndpoint) and isinstance(dst, RelayEndpoint) and not keep_source:
        # les deux côtés sont sur le serveur : c'est lui qui orchestre (et fait autorité)
        return dst.relay.transfer(dst.name, src.name, with_queue)
    state = src.capture()
    if state is None:
        raise HandoffError(f"rien en lecture sur « {src.name} »")

    if not keep_source and not state.paused:
        src.pause()
        if isinstance(src, DirectEndpoint):
            # relecture après la pause : position exacte, mais on garde l'intention « en lecture »
            exact = src.capture()
            if exact and exact.video_id == state.video_id:
                exact.paused = False
                exact.upcoming = state.upcoming
                state = exact

    dst.apply(state, with_queue)
    return state
