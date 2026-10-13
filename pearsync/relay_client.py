"""Client du serveur Connect (voir server/connect_server.py pour le protocole)."""

from __future__ import annotations

import contextlib
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import Any

from .handoff import PlaybackState


class RelayError(Exception):
    pass


class RelayClient:
    def __init__(self, url: str, token: str, timeout: float = 10.0, cafile: str | None = None) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout
        # HTTPS avec l'autorité de certification du homelab (sinon : magasin du système)
        self.context = ssl.create_default_context(cafile=cafile) if cafile else None

    def _open(self, req: urllib.request.Request, timeout: float) -> Any:
        return urllib.request.urlopen(req, timeout=timeout, context=self.context)

    def _req(self, method: str, path: str, body: Any = None, timeout: float | None = None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with self._open(req, timeout or self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            detail = ""
            with contextlib.suppress(json.JSONDecodeError, AttributeError):
                detail = json.loads(e.read() or b"{}").get("error", "")
            raise RelayError(f"serveur : HTTP {e.code} sur {path}" + (f" ({detail})" if detail else "")) from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise RelayError(f"serveur injoignable : {getattr(e, 'reason', e)}") from e

    @staticmethod
    def _node(node: str) -> str:
        return urllib.parse.quote(node, safe="")

    # -- appareil ---------------------------------------------------------------
    def put_state(self, node: str, state: PlaybackState | None, kind: str = "pear") -> None:
        body = {"kind": kind, "state": state.to_dict() if state else None}
        self._req("PUT", f"/v1/devices/{self._node(node)}/state", body)

    def take_commands(self, node: str, wait: float = 0.0) -> list[dict[str, Any]]:
        path = f"/v1/devices/{self._node(node)}/commands/take?wait={wait:g}"
        res = self._req("POST", path, timeout=wait + self.timeout)
        return (res or {}).get("commands", [])

    # -- télécommande --------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        return self._req("GET", "/v1/snapshot") or {}

    def send_command(self, node: str, command: dict[str, Any]) -> None:
        self._req("POST", f"/v1/devices/{self._node(node)}/commands", command)

    def control(self, action: str, seconds: float | None = None, **params: Any) -> str:
        body: dict[str, Any] = {"action": action, **params}
        if seconds is not None:
            body["seconds"] = seconds
        return str((self._req("POST", "/v1/control", body) or {}).get("device", ""))

    def events(self, timeout: float = 60.0) -> Iterator[dict[str, Any]]:
        """Flux temps réel : un snapshot complet à chaque changement côté serveur."""
        req = urllib.request.Request(self.url + "/v1/events")
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "text/event-stream")
        try:
            with self._open(req, timeout) as resp:
                data: list[str] = []
                for raw in resp:
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if line.startswith("data:"):
                        data.append(line[5:].lstrip())
                    elif not line and data:
                        with contextlib.suppress(json.JSONDecodeError):
                            yield json.loads("\n".join(data))
                        data = []
        except urllib.error.HTTPError as e:
            raise RelayError(f"serveur : HTTP {e.code} sur /v1/events") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            raise RelayError(f"flux d'événements interrompu : {getattr(e, 'reason', e)}") from e

    def transfer(self, target: str, source: str | None = None, with_queue: bool = True) -> PlaybackState:
        body: dict[str, Any] = {"target": target, "with_queue": with_queue}
        if source:
            body["source"] = source
        res = self._req("POST", "/v1/transfer", body) or {}
        return PlaybackState.from_dict(res.get("state") or {})

    def get_state(self, node: str) -> PlaybackState | None:
        """État courant d'un appareil (position déjà extrapolée par le serveur)."""
        dev = (self.snapshot().get("devices") or {}).get(node) or {}
        view = dev.get("state")
        if not view or not dev.get("online"):
            return None
        view = dict(view)
        view.pop("upcoming", None)  # le serveur n'en donne que le nombre
        view.pop("queue", None)
        return PlaybackState.from_dict(view)
