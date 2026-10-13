"""Faux serveur « API Server » Pear Desktop pour les tests.

Reproduit les comportements qui comptent pour le transfert, observés dans le
code v3.12 : insertion en file asynchrone (fetch /music/get_queue côté
renderer), changement de titre différé, seek ignoré juste après un changement,
éléments de file directs ou « wrapper ».
"""

from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class FakePear:
    INSERT_DELAY = 0.25
    SWITCH_DELAY = 0.2
    SEEK_DEAD_TIME = 0.3

    def __init__(
        self,
        queue: list[str] | None = None,
        selected: int = 0,
        position: float = 0.0,
        playing: bool = True,
        auth: bool = True,
        wrapper_ids: set[str] | None = None,
        playlist_id: str = "",
    ) -> None:
        self.lock = threading.RLock()
        self.queue = list(queue or [])
        self.selected = selected if self.queue else -1
        self.current = self.queue[selected] if self.queue else None
        self._pos, self._pos_at = position, time.monotonic()
        self.playing = playing and self.current is not None
        self.auth = auth
        self.tokens: set[str] = set()
        self.wrapper_ids = wrapper_ids or set()
        self.playlist_id = playlist_id
        self.down = False  # simule Pear fermé (redémarrage)
        self.volume = 70
        self.switched_at = 0.0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def restart_into(self, video_id: str, playlist_id: str, mix: list[str], downtime: float = 0.5) -> None:
        """Simule l'arrêt de Pear puis sa relance sur watch?v=…&list=… (mix régénéré par YouTube)."""
        with self.lock:
            self.down = True

        def up() -> None:
            with self.lock:
                self.queue = [video_id, *mix]
                self.selected, self.current = 0, video_id
                self.playlist_id = playlist_id
                self._set_pos(0.0)
                self.playing = True
                self.switched_at = time.monotonic()
                self.down = False

        threading.Timer(downtime, up).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    # -- état -----------------------------------------------------------------
    def position(self) -> float:
        with self.lock:
            drift = time.monotonic() - self._pos_at if self.playing else 0.0
            return self._pos + drift

    def _set_pos(self, p: float) -> None:
        self._pos, self._pos_at = p, time.monotonic()

    def _item(self, i: int, vid: str) -> dict[str, Any]:
        r = {
            "videoId": vid,
            "selected": i == self.selected,
            "title": {"runs": [{"text": f"T-{vid}"}]},
            "longBylineText": {"runs": [{"text": "A"}, {"text": " • "}, {"text": "1M vues"}]},
        }
        if vid in self.wrapper_ids:
            return {
                "playlistPanelVideoWrapperRenderer": {
                    "primaryRenderer": {"playlistPanelVideoRenderer": r},
                    "counterpart": [],
                }
            }
        return {"playlistPanelVideoRenderer": r}

    def _switch(self, index: int) -> None:
        def go() -> None:
            with self.lock:
                self.selected = index
                self.current = self.queue[index]
                self._set_pos(0.0)
                self.switched_at = time.monotonic()
                self.playing = True

        threading.Timer(self.SWITCH_DELAY, go).start()

    def _insert(self, vid: str, pos: str) -> None:
        def go() -> None:
            with self.lock:
                if not self.queue:  # sur le vrai client, pas de queueContextParams : rien n'est ajouté
                    return
                at = self.selected + 1 if pos == "INSERT_AFTER_CURRENT_VIDEO" else len(self.queue)
                self.queue.insert(at, vid)

        threading.Timer(self.INSERT_DELAY, go).start()

    # -- HTTP -----------------------------------------------------------------
    def _handler(self) -> type[BaseHTTPRequestHandler]:
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                pass

            def _reply(self, code: int, body: Any = None) -> None:
                data = b"" if body is None else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _json(self) -> Any:
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n)) if n else None

            def _dispatch(self) -> None:
                if fake.down:  # Pear fermé : connexion coupée sans réponse
                    self.close_connection = True
                    return
                path = self.path
                if self.command == "POST" and (m := re.fullmatch(r"/auth/([^/]+)", path)):
                    tok = f"tok-{m.group(1)}"
                    fake.tokens.add(tok)
                    return self._reply(200, {"accessToken": tok})
                if not path.startswith("/api/v1/"):
                    return self._reply(404)
                if fake.auth and (self.headers.get("Authorization") or "")[7:] not in fake.tokens:
                    return self._reply(401)
                route = (self.command, path[len("/api/v1") :])
                body = self._json() if self.command in ("POST", "PATCH") else None
                with fake.lock:
                    if route == ("GET", "/song"):
                        if not fake.current:
                            return self._reply(204)
                        return self._reply(
                            200,
                            {
                                "videoId": fake.current,
                                "title": f"T-{fake.current}",
                                "artist": "A",
                                "elapsedSeconds": int(fake.position()),
                                "isPaused": not fake.playing,
                                "songDuration": 300,
                                "playlistId": fake.playlist_id,
                                "views": 0,
                                "mediaType": "AUDIO",
                            },
                        )
                    if route == ("GET", "/queue"):
                        return self._reply(200, {"items": [fake._item(i, v) for i, v in enumerate(fake.queue)]})
                    if route == ("POST", "/queue"):
                        fake._insert(body["videoId"], body.get("insertPosition", "INSERT_AT_END"))
                        return self._reply(204)
                    if route == ("PATCH", "/queue"):
                        fake._switch(int(body["index"]))
                        return self._reply(204)
                    if self.command == "DELETE" and (m := re.fullmatch(r"/queue/(\d+)", route[1])):
                        i = int(m.group(1))
                        if i < len(fake.queue) and i != fake.selected:
                            del fake.queue[i]
                            if i < fake.selected:
                                fake.selected -= 1
                        return self._reply(204)
                    if route == ("POST", "/seek-to"):
                        if time.monotonic() - fake.switched_at > fake.SEEK_DEAD_TIME:
                            fake._set_pos(float(body["seconds"]))
                        return self._reply(204)
                    if route == ("POST", "/play"):
                        fake._set_pos(fake.position())
                        fake.playing = fake.current is not None
                        return self._reply(204)
                    if route in (("POST", "/next"), ("POST", "/previous")):
                        step = 1 if route[1] == "/next" else -1
                        i = fake.selected + step
                        if 0 <= i < len(fake.queue):
                            fake._switch(i)
                        return self._reply(204)
                    if route == ("GET", "/volume"):
                        return self._reply(200, {"state": fake.volume, "isMuted": False})
                    if route == ("POST", "/volume"):
                        fake.volume = int(body["volume"])
                        return self._reply(204)
                    if route == ("POST", "/pause"):
                        fake._set_pos(fake.position())
                        fake.playing = False
                        return self._reply(204)
                return self._reply(404)

            do_GET = do_POST = do_PATCH = do_DELETE = _dispatch

        return H
