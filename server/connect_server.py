#!/usr/bin/env python3
"""Serveur « Connect » pearsync : l'équivalent de Spotify Connect pour YouTube Music.

Il fait autorité sur la session d'écoute :
  - registre des appareils (PC sous Pear Desktop, téléphone…) et de leur état ;
  - appareil actif : celui qui joue. Quand un autre appareil se met à jouer, il
    devient actif et l'ancien est mis en pause (comme Spotify Connect) ;
  - session : dernier état de l'appareil actif (titre, mix, position…), conservée
    sur disque pour survivre aux redémarrages ;
  - commandes vers les appareils (relevées en long-poll, latence < 1 s) ;
  - flux d'événements temps réel pour la télécommande web.

Un jeton par appareil (stocké haché). Un appareil ne peut publier que SON état et
relever que SES commandes ; tout jeton peut piloter (télécommande).

API (sauf « / » et /healthz, tout exige « Authorization: Bearer <jeton> ») :
  GET  /                                   télécommande web
  GET  /healthz                            {"ok": true}
  GET  /v1/snapshot                        état complet
  GET  /v1/events                          flux text/event-stream (snapshot à chaque changement)
  PUT  /v1/devices/<nom>/state             {"kind", "state"}            (jeton de <nom>)
  POST /v1/devices/<nom>/commands/take?wait=N  -> {"commands": [...]}   (jeton de <nom>)
  POST /v1/devices/<nom>/commands          {"type": pause|play|next|previous|seek|volume|jump|enqueue|remove|move|resume, ...}
  POST /v1/control                         {"action": idem sauf resume, "seconds"?, "level"?, "video_id"?, "next"?,
                                            "index"? (remove/move : rang dans « à suivre »), "to"? (move : rang cible)}
  POST /v1/transfer                        {"target", "source"?, "with_queue"?}

Gestion des jetons :
  python3 connect_server.py --data-dir DIR add-device bureau   # affiche le jeton, une seule fois
  python3 connect_server.py --data-dir DIR remove-device bureau
  python3 connect_server.py --data-dir DIR list-devices
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import secrets
import socket
import ssl
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

MAX_BODY = 64 * 1024
MAX_COMMANDS = 20
COMMAND_TTL = 120.0  # s
ONLINE_AFTER = 45.0  # s sans publication : appareil hors ligne
MAX_WAIT = 25.0  # s : durée maximale d'un long-poll
EVENT_KEEPALIVE = 20.0  # s
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
KINDS = {"pear", "android", "web", "other"}
CONTROL_ACTIONS = {"pause", "play", "next", "previous", "seek", "volume", "jump", "enqueue", "remove", "move"}
MAX_QUEUE_INDEX = 1000  # rang dans « à suivre » (remove/move) : borne de validation
DEVICE_COMMANDS = CONTROL_ACTIONS | {"resume"}
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
MAX_QUEUE_VIEW = 50
ROUTE_RE = re.compile(r"^/v1/devices/([^/]+)/(state|commands|commands/take)$")
WEB_UI = Path(__file__).with_name("web") / "index.html"
# Installation en une commande (irm http://…/install.ps1 | iex) : fichiers publics, sans jeton
DIST_DIR = Path(__file__).with_name("dist")
DIST_FILES = {
    "install.ps1": "text/plain; charset=utf-8",
    "uninstall.ps1": "text/plain; charset=utf-8",
    "app-asar.zip": "application/zip",
    "pearconnect-ca.crt": "application/x-x509-ca-cert",
    "PearConnect.apk": "application/vnd.android.package-archive",
}


def clean_command(kind: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Commande de pilotage validée (sans « resume », traité à part)."""
    if kind not in CONTROL_ACTIONS:
        raise ValueError("commande inconnue")
    if kind == "seek":
        seconds = body.get("seconds")
        if not isinstance(seconds, int | float) or isinstance(seconds, bool):
            raise ValueError("seconds invalide")
        return {"type": "seek", "seconds": max(0.0, float(seconds))}
    if kind == "volume":
        level = body.get("level")
        if not isinstance(level, int | float) or isinstance(level, bool):
            raise ValueError("level invalide")
        return {"type": "volume", "level": int(min(100, max(0, level)))}
    if kind in ("jump", "enqueue", "remove", "move"):
        vid = body.get("video_id")
        if not isinstance(vid, str) or not VIDEO_ID_RE.match(vid):
            raise ValueError("video_id invalide")
        cmd: dict[str, Any] = {"type": kind, "video_id": vid}
        if kind == "enqueue":
            cmd["next"] = bool(body.get("next"))
        if kind in ("remove", "move"):
            # rang (0 = juste après le titre en cours) : départage les doublons dans la file
            index = _queue_index(body.get("index"))
            if index is not None:
                cmd["index"] = index
        if kind == "move":
            to = _queue_index(body.get("to"))
            if to is None:
                raise ValueError("to invalide")
            cmd["to"] = to
        return cmd
    return {"type": kind}


def _queue_index(value: Any) -> int | None:
    """Rang dans « à suivre », ou None s'il n'est pas fourni."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= MAX_QUEUE_INDEX:
        raise ValueError("rang invalide")
    return int(value)


def _text(value: Any, limit: int = 200) -> str:
    return value[:limit] if isinstance(value, str) else ""


def queue_view(state: dict[str, Any]) -> list[dict[str, str]]:
    """Titres à venir avec leurs métadonnées (pour les vues « Lecture sur… »)."""
    out = []
    for row in state.get("queue") or []:
        if isinstance(row, dict) and isinstance(row.get("video_id"), str) and VIDEO_ID_RE.match(row["video_id"]):
            out.append(
                {"video_id": row["video_id"], "title": _text(row.get("title")), "artist": _text(row.get("artist"))}
            )
        if len(out) >= MAX_QUEUE_VIEW:
            break
    return out


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def extrapolate(state: dict[str, Any], received: float, now: float) -> float:
    """Position d'un état « en lecture » à l'instant `now` (horloge du serveur)."""
    pos = float(state.get("position") or 0)
    if not state.get("paused"):
        pos += max(0.0, now - received)
    duration = float(state.get("duration") or 0)
    if duration > 0:
        pos = min(pos, max(0.0, duration - 1))
    return pos


class Hub:
    """État partagé du serveur. Toutes les mutations passent par `self.cond`."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.cond = threading.Condition()
        self.version = 0
        self.devices: dict[str, dict[str, Any]] = {}  # nom -> {kind, state, received}
        self.commands: dict[str, list[dict[str, Any]]] = {}
        self.active: str | None = None
        self.session: dict[str, Any] | None = None  # {"device", "state", "received"}
        self.tokens: dict[str, str] = {}  # sha256(jeton) -> nom d'appareil
        self._tokens_mtime = 0.0
        self._load()

    # -- persistance ---------------------------------------------------------
    @property
    def _tokens_file(self) -> Path:
        return self.data_dir / "tokens.json"

    @property
    def _session_file(self) -> Path:
        return self.data_dir / "session.json"

    def _load_tokens(self) -> None:
        try:
            mtime = self._tokens_file.stat().st_mtime
            raw = json.loads(self._tokens_file.read_text("utf-8"))
        except FileNotFoundError:
            self.tokens, self._tokens_mtime = {}, 0.0
            return
        except json.JSONDecodeError:
            return  # fichier en cours d'écriture : on garde les jetons connus
        self.tokens = {h: n for n, h in raw.items()}
        self._tokens_mtime = mtime

    def _load(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._load_tokens()
        try:
            saved = json.loads(self._session_file.read_text("utf-8"))
            self.active = saved.get("active")
            self.session = saved.get("session")
            for name, dev in (saved.get("devices") or {}).items():
                if name in self.tokens.values():
                    self.devices[name] = {"kind": dev.get("kind", "other"), "state": dev.get("state"), "received": 0.0}
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    @staticmethod
    def _atomic_write(path: Path, data: Any) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        if os.name != "nt":
            tmp.chmod(0o600)
        os.replace(tmp, path)

    def _persist(self) -> None:
        devices = {n: {"kind": d["kind"], "state": d["state"]} for n, d in self.devices.items()}
        self._atomic_write(self._session_file, {"active": self.active, "session": self.session, "devices": devices})

    def save_tokens(self) -> None:
        self._atomic_write(self._tokens_file, {n: h for h, n in self.tokens.items()})

    # -- authentification -----------------------------------------------------
    def device_for(self, authorization: str | None) -> str | None:
        if not authorization or not authorization.startswith("Bearer "):
            return None
        # add-device / remove-device réécrivent tokens.json : ajout et révocation immédiats
        try:
            mtime = self._tokens_file.stat().st_mtime
        except FileNotFoundError:
            mtime = 0.0
        if mtime != self._tokens_mtime:
            with self.cond:
                self._load_tokens()
        return self.tokens.get(_hash(authorization[7:].strip()))

    # -- mutations (appelant : self.cond tenu) ------------------------------------
    def _changed(self) -> None:
        self.version += 1
        self._persist()
        self.cond.notify_all()

    def _push(self, device: str, cmd: dict[str, Any]) -> None:
        q = self.commands.setdefault(device, [])
        q.append({**cmd, "issued": time.time()})
        del q[:-MAX_COMMANDS]
        self.cond.notify_all()

    def _online(self, name: str, now: float) -> bool:
        dev = self.devices.get(name)
        return dev is not None and now - dev["received"] < ONLINE_AFTER

    def put_state(self, name: str, kind: str, state: dict[str, Any] | None) -> None:
        now = time.time()
        with self.cond:
            self.devices[name] = {"kind": kind, "state": state, "received": now}
            playing = bool(state) and not state.get("paused")  # type: ignore[union-attr]
            if playing and self.active != name:
                previous = self.active
                self.active = name
                prev = self.devices.get(previous) if previous else None
                # Comme Spotify Connect : un seul appareil joue à la fois.
                if previous and prev and prev["state"] and not prev["state"].get("paused"):
                    self._push(previous, {"type": "pause"})
            if state and (self.active == name or self.active is None):
                self.active = name
                self.session = {"device": name, "state": state, "received": now}
            self._changed()

    def push_command(self, device: str, cmd: dict[str, Any]) -> None:
        with self.cond:
            self._push(device, cmd)

    def take_commands(self, device: str, wait: float) -> list[dict[str, Any]]:
        deadline = time.monotonic() + min(max(wait, 0.0), MAX_WAIT)
        with self.cond:
            while not self.commands.get(device):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self.cond.wait(remaining)
            queue = self.commands.pop(device)
        now = time.time()
        out = []
        for c in queue:
            if now - c["issued"] > COMMAND_TTL:
                continue
            if c["type"] == "resume":
                # position recalculée au moment de la remise : la cible la date avec sa propre horloge
                state = dict(c["state"])
                state["position"] = extrapolate(state, c["state_received"], now)
                state.pop("ts", None)
                c = {"type": "resume", "state": state, "with_queue": c.get("with_queue", True)}
            out.append({k: v for k, v in c.items() if k not in ("issued", "state_received")})
        return out

    def control(self, cmd: dict[str, Any]) -> str:
        """Commande (déjà validée par clean_command) vers l'appareil actif."""
        with self.cond:
            if not self.active:
                raise LookupError("aucun appareil actif")
            self._push(self.active, cmd)
            return self.active

    def transfer(self, target: str, source: str | None, with_queue: bool) -> dict[str, Any]:
        with self.cond:
            if target not in self.devices:
                raise LookupError(f"appareil inconnu : {target}")
            if source:
                dev = self.devices.get(source)
                if not dev or not dev["state"]:
                    raise LookupError(f"rien en lecture sur {source}")
                state, received = dev["state"], dev["received"]
            else:
                if not self.session:
                    raise LookupError("aucune session à transférer")
                source, state, received = self.session["device"], self.session["state"], self.session["received"]
            if source == target:
                raise ValueError("la source et la cible sont identiques")
            if self.devices.get(source, {}).get("state") and not state.get("paused"):
                self._push(source, {"type": "pause"})
            # « Écouter ici » lance la lecture sur la cible, même si la source était en pause
            if state.get("paused"):
                state = {**state, "paused": False}
            self._push(target, {"type": "resume", "state": state, "state_received": received, "with_queue": with_queue})
            self.active = target
            self.session = {"device": target, "state": state, "received": received}
            self._changed()
            return state

    # -- lecture ------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        with self.cond:
            devices = {
                name: {
                    "kind": d["kind"],
                    "online": self._online(name, now),
                    "seen": round(now - d["received"]) if d["received"] else None,
                    "state": self._view(d["state"], d["received"], now),
                }
                for name, d in sorted(self.devices.items())
            }
            session = None
            if self.session:
                session = {
                    "device": self.session["device"],
                    "state": self._view(self.session["state"], self.session["received"], now),
                }
            return {"version": self.version, "now": now, "active": self.active, "session": session, "devices": devices}

    @staticmethod
    def _view(state: dict[str, Any] | None, received: float, now: float) -> dict[str, Any] | None:
        if not state:
            return None
        view = {k: state.get(k) for k in ("video_id", "title", "artist", "duration", "paused", "playlist_id")}
        view["position"] = extrapolate(state, received, now) if received else float(state.get("position") or 0)
        view["upcoming"] = len(state.get("upcoming") or [])
        view["queue"] = queue_view(state)
        volume = state.get("volume")
        view["volume"] = int(volume) if isinstance(volume, int | float) and not isinstance(volume, bool) else None
        return view

    def wait_change(self, since: int, timeout: float) -> int:
        with self.cond:
            self.cond.wait_for(lambda: self.version != since, timeout)
            return self.version


def validate_state(state: Any) -> dict[str, Any] | None:
    if state is None:
        return None
    if not isinstance(state, dict) or not isinstance(state.get("video_id"), str) or not state["video_id"]:
        raise ValueError("état invalide")
    if len(state["video_id"]) > 64:
        raise ValueError("video_id invalide")
    return state


def make_handler(hub: Hub) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "pearsync-connect"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
            # ni paramètres d'URL ni en-têtes : rien de sensible dans les journaux
            sys.stderr.write(f"{self.address_string()} {self.command} {urlsplit(self.path).path} {code}\n")

        # -- outils ------------------------------------------------------------
        def _send(self, status: HTTPStatus, body: Any = None, ctype: str = "application/json") -> None:
            if body is None:
                data = b""
            elif isinstance(body, bytes):
                data = body
            else:
                data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            if data:
                self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if data:
                self.wfile.write(data)

        def _caller(self) -> str | None:
            return hub.device_for(self.headers.get("Authorization"))

        def _body(self) -> Any:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                raise ValueError("taille de corps invalide")
            return json.loads(self.rfile.read(length))

        def _route(self) -> tuple[str, str] | None:
            m = ROUTE_RE.match(urlsplit(self.path).path)
            if not m or not NAME_RE.match(m.group(1)):
                return None
            return m.group(1), m.group(2)

        # -- verbes --------------------------------------------------------------
        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/":
                try:
                    page = WEB_UI.read_bytes()
                except OSError:
                    return self._send(HTTPStatus.NOT_FOUND)
                return self._send(HTTPStatus.OK, page, "text/html; charset=utf-8")
            if path == "/healthz":
                return self._send(HTTPStatus.OK, {"ok": True})
            name = path[4:] if path.startswith("/dl/") else path[1:]
            if name in DIST_FILES and (path.startswith("/dl/") or name.endswith(".ps1")):
                try:
                    data = (DIST_DIR / name).read_bytes()
                except OSError:
                    return self._send(HTTPStatus.NOT_FOUND)
                return self._send(HTTPStatus.OK, data, DIST_FILES[name])
            if not path.startswith("/v1/"):
                return self._send(HTTPStatus.NOT_FOUND)
            if not self._caller():
                return self._send(HTTPStatus.UNAUTHORIZED)
            if path == "/v1/snapshot":
                return self._send(HTTPStatus.OK, hub.snapshot())
            if path == "/v1/events":
                return self._events()
            self._send(HTTPStatus.NOT_FOUND)

        def _events(self) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.close_connection = True
            version = -1
            try:
                while True:
                    if version != hub.version:
                        snap = hub.snapshot()
                        version = snap["version"]
                        self.wfile.write(f"data: {json.dumps(snap, ensure_ascii=False)}\n\n".encode())
                    else:
                        self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    hub.wait_change(version, EVENT_KEEPALIVE)
            except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
                return

        def do_PUT(self) -> None:
            caller = self._caller()
            if not caller:
                return self._send(HTTPStatus.UNAUTHORIZED)
            route = self._route()
            if not route or route[1] != "state":
                return self._send(HTTPStatus.NOT_FOUND)
            if route[0] != caller:
                return self._send(HTTPStatus.FORBIDDEN)
            try:
                body = self._body()
                kind = body.get("kind", "other")
                if kind not in KINDS:
                    raise ValueError
                state = validate_state(body.get("state"))
            except (ValueError, TypeError, AttributeError):
                return self._send(HTTPStatus.BAD_REQUEST)
            hub.put_state(caller, kind, state)
            self._send(HTTPStatus.NO_CONTENT)

        def do_POST(self) -> None:
            caller = self._caller()
            if not caller:
                return self._send(HTTPStatus.UNAUTHORIZED)
            path = urlsplit(self.path).path
            try:
                if path == "/v1/control":
                    body = self._body()
                    if not isinstance(body, dict):
                        raise ValueError
                    return self._send(
                        HTTPStatus.ACCEPTED, {"device": hub.control(clean_command(body.get("action"), body))}
                    )
                if path == "/v1/transfer":
                    body = self._body()
                    target, source = body.get("target"), body.get("source")
                    if not isinstance(target, str) or not NAME_RE.match(target):
                        raise ValueError
                    if source is not None and (not isinstance(source, str) or not NAME_RE.match(source)):
                        raise ValueError
                    state = hub.transfer(target, source, bool(body.get("with_queue", True)))
                    return self._send(HTTPStatus.ACCEPTED, {"state": state})
                route = self._route()
                if not route or route[1] == "state":
                    return self._send(HTTPStatus.NOT_FOUND)
                device, action = route
                if action == "commands/take":
                    if device != caller:
                        return self._send(HTTPStatus.FORBIDDEN)
                    wait = float((parse_qs(urlsplit(self.path).query).get("wait") or ["0"])[0])
                    return self._send(HTTPStatus.OK, {"commands": hub.take_commands(device, wait)})
                cmd = self._body()
                if not isinstance(cmd, dict) or cmd.get("type") not in DEVICE_COMMANDS:
                    raise ValueError
                if cmd["type"] == "resume":
                    resume_state = validate_state(cmd.get("state"))
                    if resume_state is None:
                        raise ValueError("état manquant")
                    cmd = {
                        "type": "resume",
                        "state": resume_state,
                        "state_received": time.time(),
                        "with_queue": bool(cmd.get("with_queue", True)),
                    }
                else:
                    cmd = clean_command(cmd["type"], cmd)
                if device not in hub.devices:
                    return self._send(HTTPStatus.NOT_FOUND)
                hub.push_command(device, cmd)
                return self._send(HTTPStatus.ACCEPTED)
            except LookupError as e:
                return self._send(HTTPStatus.CONFLICT, {"error": str(e)})
            except (ValueError, TypeError, AttributeError) as e:
                return self._send(HTTPStatus.BAD_REQUEST, {"error": str(e) or "requête invalide"})

    return Handler


class ConnectHTTPServer(ThreadingHTTPServer):
    request_queue_size = 64  # flux d'événements et long-polls gardent des connexions ouvertes
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        # Client parti en cours de route (onglet fermé, réseau mobile) : normal, pas de trace.
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError, ssl.SSLError)):
            return
        super().handle_error(request, client_address)


class TLSConnectHTTPServer(ConnectHTTPServer):
    """HTTPS. La poignée de main TLS se fait dans le thread de la requête, pas dans
    la boucle d'acceptation : un client lent ou malveillant ne bloque pas les autres."""

    HANDSHAKE_TIMEOUT = 10.0

    def __init__(self, address: tuple[str, int], handler: Any, context: ssl.SSLContext) -> None:
        self.context = context
        super().__init__(address, handler)

    def finish_request(self, request: Any, client_address: Any) -> None:
        request.settimeout(self.HANDSHAKE_TIMEOUT)
        try:
            tls = self.context.wrap_socket(request, server_side=True)
        except (ssl.SSLError, OSError):
            return  # client HTTP en clair, certificat refusé, connexion coupée
        try:
            tls.settimeout(None)
            super().finish_request(tls, client_address)
        finally:
            with contextlib.suppress(OSError):
                tls.shutdown(socket.SHUT_RDWR)
            tls.close()


def tls_context(cert: Path, key: Path) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(cert), str(key))
    return ctx


def build_server(
    bind: str, port: int, data_dir: Path, hub: Hub | None = None, context: ssl.SSLContext | None = None
) -> tuple[ThreadingHTTPServer, Hub]:
    hub = hub or Hub(data_dir)
    handler = make_handler(hub)
    server = (
        TLSConnectHTTPServer((bind, port), handler, context) if context else ConnectHTTPServer((bind, port), handler)
    )
    return server, hub


def main() -> None:
    ap = argparse.ArgumentParser(description="Serveur Connect pearsync")
    ap.add_argument("--data-dir", default=os.environ.get("PEARSYNC_DATA_DIR", "./data"))
    ap.add_argument("--bind", default=os.environ.get("PEARSYNC_BIND", "127.0.0.1"))
    ap.add_argument(
        "--port", type=int, default=int(os.environ.get("PEARSYNC_PORT", "8787")), help="HTTP (0 : désactivé)"
    )
    ap.add_argument("--https-port", type=int, default=int(os.environ.get("PEARSYNC_HTTPS_PORT", "8443")))
    ap.add_argument("--tls-cert", default=os.environ.get("PEARSYNC_TLS_CERT"), help="certificat (chaîne PEM)")
    ap.add_argument("--tls-key", default=os.environ.get("PEARSYNC_TLS_KEY"), help="clé privée PEM")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("serve")
    p = sub.add_parser("add-device")
    p.add_argument("name")
    p = sub.add_parser("remove-device")
    p.add_argument("name")
    sub.add_parser("list-devices")
    args = ap.parse_args()
    data_dir = Path(args.data_dir)

    if args.cmd in ("add-device", "remove-device", "list-devices"):
        hub = Hub(data_dir)
        if args.cmd == "list-devices":
            print("\n".join(sorted(hub.tokens.values())) or "(aucun appareil)")
            return
        if not NAME_RE.match(args.name):
            sys.exit("nom invalide (lettres, chiffres, . _ -)")
        hub.tokens = {h: n for h, n in hub.tokens.items() if n != args.name}
        if args.cmd == "add-device":
            token = secrets.token_urlsafe(32)
            hub.tokens[_hash(token)] = args.name
            hub.save_tokens()
            print(token)
        else:
            hub.save_tokens()
            print(f"{args.name} supprimé (jeton refusé dès la prochaine requête)")
        return

    hub = Hub(data_dir)
    servers: list[ThreadingHTTPServer] = []
    if args.port:
        servers.append(build_server(args.bind, args.port, data_dir, hub)[0])
        print(f"serveur Connect en écoute sur http://{args.bind}:{args.port}", file=sys.stderr)
    if args.tls_cert and args.tls_key:
        ctx = tls_context(Path(args.tls_cert), Path(args.tls_key))
        servers.append(build_server(args.bind, args.https_port, data_dir, hub, ctx)[0])
        print(f"serveur Connect en écoute sur https://{args.bind}:{args.https_port}", file=sys.stderr)
    if not servers:
        sys.exit("aucune écoute : --port 0 exige --tls-cert et --tls-key")
    if not hub.tokens:
        print("aucun appareil : créer un jeton avec « add-device <nom> »", file=sys.stderr)
    for extra in servers[1:]:
        threading.Thread(target=extra.serve_forever, daemon=True).start()
    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for srv in servers:
            srv.server_close()


if __name__ == "__main__":
    main()
