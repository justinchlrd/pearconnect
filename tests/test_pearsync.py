from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "server"), str(ROOT / "tests")]

from connect_server import Hub, _hash, build_server  # noqa: E402
from fake_pear import FakePear  # noqa: E402

from pearsync.agent import Agent  # noqa: E402
from pearsync.endpoints import DirectEndpoint, transfer  # noqa: E402
from pearsync.handoff import ContextLoadError, HandoffError, PlaybackState, capture_state  # noqa: E402
from pearsync.launcher import LauncherError, PearLauncher  # noqa: E402
from pearsync.pear_api import PearClient, parse_queue, queue_index_of  # noqa: E402
from pearsync.relay_client import RelayClient  # noqa: E402


def authed(fake: FakePear) -> PearClient:
    c = PearClient(fake.url)
    c.request_token("test")
    return c


def authed_offline(fake: FakePear) -> PearClient:
    """Client vers un Pear fermé (pas de jeton obtenable)."""
    fake.auth = False
    return PearClient(fake.url)


class QueueParsing(unittest.TestCase):
    def test_direct_and_wrapper_items(self) -> None:
        payload = {
            "items": [
                {"playlistPanelVideoRenderer": {"videoId": "a", "selected": False}},
                {
                    "playlistPanelVideoWrapperRenderer": {
                        "primaryRenderer": {"playlistPanelVideoRenderer": {"videoId": "b", "selected": True}},
                        "counterpart": [{"counterpartRenderer": {"playlistPanelVideoRenderer": {"videoId": "b-clip"}}}],
                    }
                },
                {"unknownRenderer": {}},
            ]
        }
        self.assertEqual([(e.video_id, e.selected) for e in parse_queue(payload)], [("a", False), ("b", True)])
        self.assertEqual(queue_index_of(payload, "b-clip"), 1)
        self.assertIsNone(queue_index_of(payload, "a", after=0))
        self.assertEqual(parse_queue(None), [])

    def test_position_extrapolation(self) -> None:
        s = PlaybackState("x", position=10, paused=False, duration=100, ts=1000)
        self.assertEqual(s.position_now(1005), 15)
        self.assertEqual(s.position_now(5000), 99)  # bornée à la durée
        s.paused = True
        self.assertEqual(s.position_now(5000), 10)
        self.assertIn("v=x&t=10", s.music_url(1000))

    def test_state_validation(self) -> None:
        with self.assertRaises(HandoffError):
            PlaybackState.from_dict({"position": 3})


class DirectTransfer(unittest.TestCase):
    def setUp(self) -> None:
        self.src = FakePear(queue=["w", "x", "y", "z"], selected=1, position=100, playing=True, wrapper_ids={"y"})
        self.dst = FakePear(queue=["old1", "old2", "old3"], selected=0, position=5, playing=True)

    def tearDown(self) -> None:
        self.src.close()
        self.dst.close()

    def test_transfer_track_position_queue(self) -> None:
        src = DirectEndpoint("a", authed(self.src), 25)
        dst = DirectEndpoint("b", authed(self.dst), 25)
        transfer(src, dst)

        self.assertFalse(self.src.playing, "la source doit être en pause")
        self.assertEqual(self.dst.current, "x")
        self.assertTrue(self.dst.playing)
        self.assertAlmostEqual(self.dst.position(), 100, delta=4)
        q, sel = self.dst.queue, self.dst.selected
        self.assertEqual(q[sel + 1 :], ["y", "z"], "la suite de la file source doit être recopiée dans l'ordre")

    def test_keep_source_and_no_queue(self) -> None:
        transfer(
            DirectEndpoint("a", authed(self.src), 25),
            DirectEndpoint("b", authed(self.dst), 25),
            with_queue=False,
            keep_source=True,
        )
        self.assertTrue(self.src.playing)
        self.assertEqual(self.dst.current, "x")
        self.assertNotIn("y", self.dst.queue)

    def test_paused_source_stays_paused(self) -> None:
        self.src.playing = False
        transfer(DirectEndpoint("a", authed(self.src), 25), DirectEndpoint("b", authed(self.dst), 25))
        self.assertFalse(self.dst.playing)
        self.assertAlmostEqual(self.dst.position(), 100, delta=2)

    def test_same_track_only_seeks(self) -> None:
        self.dst.queue, self.dst.selected, self.dst.current = ["x"], 0, "x"
        transfer(DirectEndpoint("a", authed(self.src), 0), DirectEndpoint("b", authed(self.dst), 0))
        self.assertEqual(self.dst.queue, ["x"])
        self.assertAlmostEqual(self.dst.position(), 100, delta=4)

    def test_repeated_transfers_do_not_stack_the_track(self) -> None:
        """Bug signalé : le titre transféré s'intercalait entre chaque morceau du mix."""
        from pearsync.handoff import apply_state

        client = authed(self.dst)
        for _ in range(3):
            apply_state(client, PlaybackState("x", 10, False), with_queue=False)
            self.dst.selected -= 1  # le mix revient en arrière : « x » est de nouveau devant
            self.dst.current = self.dst.queue[self.dst.selected]
        self.assertEqual(self.dst.queue.count("x"), 1, self.dst.queue)

    def test_empty_target_queue_gives_clear_error(self) -> None:
        empty = FakePear()
        try:
            with self.assertRaisesRegex(HandoffError, "lance n'importe quel titre"):
                from pearsync.handoff import apply_state

                apply_state(authed(empty), PlaybackState("x", 10, False), timeout=1.0)
        finally:
            empty.close()

    def test_unauthorized(self) -> None:
        from pearsync.pear_api import PearApiError

        with self.assertRaisesRegex(PearApiError, "401"):
            PearClient(self.src.url).song()


class ConnectServer(unittest.TestCase):
    """Serveur Connect : registre, appareil actif, transfert, commandes, temps réel."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.server, self.hub = build_server("127.0.0.1", 0, Path(self.tmp.name))
        self.tokens = {}
        for name in ("bureau", "portable", "web"):
            self.tokens[name] = secrets.token_urlsafe(16)
            self.hub.tokens[_hash(self.tokens[name])] = name
        self.hub.save_tokens()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.fakes: list[FakePear] = []

    def tearDown(self) -> None:
        for f in self.fakes:
            f.close()
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def client(self, name: str) -> RelayClient:
        return RelayClient(self.url, self.tokens[name])

    def pear(self, **kw: object) -> FakePear:
        f = FakePear(**kw)  # type: ignore[arg-type]
        self.fakes.append(f)
        return f

    def call(self, method: str, path: str, token: str | None, body: bytes | None = None) -> int:
        req = urllib.request.Request(self.url + path, data=body, method=method)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_auth_and_validation(self) -> None:
        t = self.tokens["bureau"]
        self.assertEqual(self.call("GET", "/healthz", None), 200)
        self.assertEqual(self.call("GET", "/", None), 200, "la télécommande se charge sans jeton")
        self.assertEqual(self.call("GET", "/v1/snapshot", None), 401)
        self.assertEqual(self.call("GET", "/v1/snapshot", "mauvais"), 401)
        state = json.dumps({"kind": "pear", "state": None}).encode()
        self.assertEqual(self.call("PUT", "/v1/devices/portable/state", t, state), 403, "usurpation d'appareil")
        self.assertEqual(self.call("POST", "/v1/devices/portable/commands/take", t), 403)
        self.assertEqual(self.call("PUT", "/v1/devices/bureau/state", t, state), 204)
        self.assertEqual(self.call("POST", "/v1/devices/bureau/commands", t, b'{"type":"rm -rf"}'), 400)
        self.assertEqual(self.call("POST", "/v1/devices/bureau/commands", t, b'{"type":"resume"}'), 400)
        self.assertEqual(self.call("PUT", "/v1/devices/bureau/state", t, b"x" * (70 * 1024)), 400)
        self.assertEqual(self.call("POST", "/v1/control", t, b'{"action":"next"}'), 409, "aucun appareil actif")
        self.assertEqual(self.call("POST", "/v1/transfer", t, b'{"target":"inconnu"}'), 409)

    def test_transfer_between_agents(self) -> None:
        a = self.pear(queue=["w", "x", "y"], selected=1, position=42, playing=True, playlist_id="")
        b = self.pear(queue=["old"], selected=0, playing=False)
        agent_a = Agent(authed(a), self.client("bureau"), "bureau")
        agent_b = Agent(authed(b), self.client("portable"), "portable")
        agent_a.tick()
        agent_b.tick()
        snap = self.client("web").snapshot()
        self.assertEqual(snap["active"], "bureau")
        self.assertTrue(snap["devices"]["portable"]["online"])

        self.client("web").transfer("portable")  # comme le bouton « Écouter ici »
        agent_a.tick()
        agent_b.tick()
        self.assertFalse(a.playing)
        self.assertEqual(b.current, "x")
        self.assertTrue(b.playing)
        self.assertAlmostEqual(b.position(), 42, delta=4)
        self.assertEqual(b.queue[b.selected + 1 :], ["y"])
        agent_a.tick()
        agent_b.tick()
        snap = self.client("web").snapshot()
        self.assertEqual((snap["active"], snap["session"]["device"]), ("portable", "portable"))

    def test_only_one_device_plays(self) -> None:
        a = self.pear(queue=["x"], selected=0, playing=True)
        b = self.pear(queue=["z"], selected=0, playing=False)
        agent_a = Agent(authed(a), self.client("bureau"), "bureau")
        agent_b = Agent(authed(b), self.client("portable"), "portable")
        agent_a.tick()
        agent_b.tick()
        b.playing = True  # l'utilisateur lance la lecture sur le portable
        agent_b.tick()
        agent_a.tick()  # reçoit la pause, comme avec Spotify Connect
        self.assertFalse(a.playing)
        self.assertEqual(self.client("web").snapshot()["active"], "portable")

    def test_remote_controls(self) -> None:
        a = self.pear(queue=["x", "y"], selected=0, playing=True)
        agent = Agent(authed(a), self.client("bureau"), "bureau")
        agent.tick()
        web = self.client("web")
        self.assertEqual(web.control("seek", 120), "bureau")
        web.control("pause")
        agent.tick()
        self.assertFalse(a.playing)
        self.assertAlmostEqual(a.position(), 120, delta=2)
        web.control("next")
        agent.tick()
        time.sleep(0.4)
        self.assertEqual(a.current, "y")

    def test_long_poll_delivers_quickly(self) -> None:
        bureau = self.client("bureau")
        bureau.put_state("bureau", None)
        got: list[list[dict[str, object]]] = []
        start = time.monotonic()
        t = threading.Thread(target=lambda: got.append(bureau.take_commands("bureau", wait=10)))
        t.start()
        time.sleep(0.3)
        self.client("web").send_command("bureau", {"type": "pause"})
        t.join(5)
        self.assertEqual(got, [[{"type": "pause"}]])
        self.assertLess(time.monotonic() - start, 2)

    def test_resume_position_computed_at_delivery(self) -> None:
        self.client("bureau").put_state("bureau", None)
        st = PlaybackState("abcdefghijk", position=10, paused=False, duration=300)
        self.client("web").send_command("bureau", {"type": "resume", "state": st.to_dict()})
        time.sleep(1.5)
        cmd = self.client("bureau").take_commands("bureau")[0]
        self.assertAlmostEqual(cmd["state"]["position"], 11.5, delta=0.5)
        self.assertNotIn("ts", cmd["state"])

    def test_pear_closed_device_stays_available(self) -> None:
        a = self.pear(queue=["x"], selected=0)
        a.down = True
        Agent(authed_offline(a), self.client("bureau"), "bureau").tick()
        dev = self.client("web").snapshot()["devices"]["bureau"]
        self.assertTrue(dev["online"])
        self.assertIsNone(dev["state"])

    def test_session_survives_restart(self) -> None:
        self.client("bureau").put_state("bureau", PlaybackState("abcdefghijk", 30, True, playlist_id="RDAMVMabc"))
        hub2 = Hub(Path(self.tmp.name))
        self.assertEqual(hub2.active, "bureau")
        self.assertEqual(hub2.session["state"]["playlist_id"], "RDAMVMabc")  # type: ignore[index]

    def test_agent_survives_unexpected_error(self) -> None:
        agent = Agent(PearClient("http://127.0.0.1:9"), self.client("bureau"), "bureau")
        calls = {"n": 0}

        def flaky(wait: float = 0.0) -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("réponse inattendue")
            raise KeyboardInterrupt  # sortie du test

        agent.tick = flaky  # type: ignore[method-assign]
        with self.assertLogs("pearsync.agent", "ERROR"), self.assertRaises(KeyboardInterrupt):
            agent.run(interval=0.01)
        self.assertEqual(calls["n"], 2, "la boucle doit continuer après l'erreur")

    def test_volume_jump_enqueue_and_queue_view(self) -> None:
        ids = ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"]
        a = self.pear(queue=ids, selected=0, playing=True)
        agent = Agent(authed(a), self.client("bureau"), "bureau")
        agent.tick()
        st = self.client("web").snapshot()["devices"]["bureau"]["state"]
        self.assertEqual(st["volume"], 70)
        self.assertEqual(st["queue"][0], {"video_id": "bbbbbbbbbbb", "title": "T-bbbbbbbbbbb", "artist": "A"})
        web = self.client("web")
        web.control("volume", level=150)  # borné à 100
        agent.tick()
        self.assertEqual(a.volume, 100)
        web.control("jump", video_id="ccccccccccc")
        agent.tick()
        time.sleep(0.4)
        self.assertEqual(a.current, "ccccccccccc")
        web.control("enqueue", video_id="ddddddddddd", next=True)
        agent.tick()
        time.sleep(0.4)
        self.assertEqual(a.queue[a.selected + 1], "ddddddddddd")
        t = self.tokens["web"]
        self.assertEqual(self.call("POST", "/v1/control", t, b'{"action":"jump","video_id":"x; rm"}'), 400)
        self.assertEqual(self.call("POST", "/v1/control", t, b'{"action":"volume","level":"fort"}'), 400)

    def test_relay_client_event_stream(self) -> None:
        self.client("bureau").put_state("bureau", PlaybackState("abcdefghijk", 30, True))
        stream = self.client("web").events(timeout=5)
        first = next(stream)
        self.assertEqual(first["active"], "bureau")
        threading.Timer(
            0.3, lambda: self.client("bureau").put_state("bureau", PlaybackState("zzzzzzzzzzz", 0, True))
        ).start()
        second = next(stream)
        self.assertEqual(second["session"]["state"]["video_id"], "zzzzzzzzzzz")
        stream.close()

    def test_transfer_starts_playback_on_target(self) -> None:
        self.client("bureau").put_state("bureau", PlaybackState("abcdefghijk", 30, paused=True, duration=200))
        self.client("portable").put_state("portable", None)
        self.client("web").transfer("portable")
        cmd = self.client("portable").take_commands("portable")[0]
        self.assertEqual(cmd["type"], "resume")
        self.assertFalse(cmd["state"]["paused"])
        self.assertAlmostEqual(cmd["state"]["position"], 30, delta=1)

    def test_summary_state_from_server_is_accepted(self) -> None:
        # vue du serveur : « upcoming » n'est qu'un nombre
        st = PlaybackState.from_dict({"video_id": "abcdefghijk", "position": 3, "upcoming": 24})
        self.assertEqual(st.upcoming, [])

    def test_tokens_added_and_revoked_without_restart(self) -> None:
        other = Hub(Path(self.tmp.name))  # comme la commande add-device, autre processus
        new = secrets.token_urlsafe(16)
        other.tokens[_hash(new)] = "telephone"
        other.tokens = {h: n for h, n in other.tokens.items() if n != "portable"}
        time.sleep(0.05)  # mtime distinct
        other.save_tokens()
        self.assertEqual(self.call("GET", "/v1/snapshot", new), 200)
        self.assertEqual(self.call("GET", "/v1/snapshot", self.tokens["portable"]), 401)

    def test_event_stream(self) -> None:
        req = urllib.request.Request(self.url + "/v1/events")
        req.add_header("Authorization", f"Bearer {self.tokens['web']}")
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.headers["Content-Type"], "text/event-stream")
            line = r.readline().decode()
            self.assertTrue(line.startswith("data: "))
            self.assertIn("devices", json.loads(line[6:]))
            self.client("bureau").put_state("bureau", None)
            r.readline()
            line = r.readline().decode()
            self.assertIn("bureau", json.loads(line[6:])["devices"])


MIX = "RDAMVMseedSeed01"


class Https(unittest.TestCase):
    """HTTPS natif du serveur, avec une autorité de certification privée (comme sur le homelab)."""

    def test_tls_listener_with_private_ca(self) -> None:
        import shutil
        import ssl
        import subprocess

        if not shutil.which("openssl"):
            self.skipTest("openssl absent")
        from connect_server import tls_context

        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            run = lambda *a: subprocess.run(a, cwd=d, check=True, capture_output=True)  # noqa: E731
            run(
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "ec",
                "-pkeyopt",
                "ec_paramgen_curve:P-256",
                "-nodes",
                "-keyout",
                "ca.key",
                "-out",
                "ca.crt",
                "-days",
                "2",
                "-subj",
                "/CN=Test CA",
                "-addext",
                "basicConstraints=critical,CA:TRUE",
                "-addext",
                "keyUsage=critical,keyCertSign",
            )
            run(
                "openssl",
                "req",
                "-newkey",
                "ec",
                "-pkeyopt",
                "ec_paramgen_curve:P-256",
                "-nodes",
                "-keyout",
                "srv.key",
                "-out",
                "srv.csr",
                "-subj",
                "/CN=127.0.0.1",
            )
            (p / "ext.cnf").write_text("subjectAltName=IP:127.0.0.1\nextendedKeyUsage=serverAuth\n")
            run(
                "openssl",
                "x509",
                "-req",
                "-in",
                "srv.csr",
                "-CA",
                "ca.crt",
                "-CAkey",
                "ca.key",
                "-CAcreateserial",
                "-out",
                "srv.crt",
                "-days",
                "2",
                "-extfile",
                "ext.cnf",
            )
            server, hub = build_server("127.0.0.1", 0, p / "data", context=tls_context(p / "srv.crt", p / "srv.key"))
            token = secrets.token_urlsafe(16)
            hub.tokens[_hash(token)] = "bureau"
            hub.save_tokens()
            threading.Thread(target=server.serve_forever, daemon=True).start()
            url = f"https://127.0.0.1:{server.server_address[1]}"
            try:
                client = RelayClient(url, token, cafile=str(p / "ca.crt"))
                client.put_state("bureau", PlaybackState("abcdefghijk", 3, True))
                self.assertEqual(client.snapshot()["active"], "bureau")
                from pearsync.relay_client import RelayError

                with self.assertRaises(RelayError):  # sans l'AC : certificat refusé
                    RelayClient(url, token).snapshot()
                # un client HTTP en clair ne bloque pas le serveur
                with self.assertRaises((urllib.error.URLError, ConnectionError, OSError)):
                    urllib.request.urlopen(url.replace("https", "http") + "/healthz", timeout=3).read()
                self.assertEqual(client.snapshot()["active"], "bureau")
                del ssl
            finally:
                server.shutdown()
                server.server_close()


class MixContext(unittest.TestCase):
    """Le contexte (mix) doit survivre au transfert quand la cible sait le recharger."""

    def setUp(self) -> None:
        self.src = FakePear(queue=["w", "x", "y", "z"], selected=1, position=100, playing=True, playlist_id=MIX)
        self.dst = FakePear(queue=["old1", "old2"], selected=0, playing=True, playlist_id="PLautre")
        self.calls: list[PlaybackState] = []

    def tearDown(self) -> None:
        self.src.close()
        self.dst.close()

    def restarting_loader(self, state: PlaybackState) -> None:
        self.calls.append(state)
        self.dst.restart_into(state.video_id, state.playlist_id, mix=["m1", "m2", "m3"])

    def test_mix_reloaded_and_left_untouched(self) -> None:
        transfer(
            DirectEndpoint("a", authed(self.src), 25),
            DirectEndpoint("b", authed(self.dst), 25, self.restarting_loader),
        )
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0].playlist_id, MIX)
        self.assertEqual((self.dst.current, self.dst.playlist_id), ("x", MIX))
        self.assertAlmostEqual(self.dst.position(), 100, delta=4)
        self.assertTrue(self.dst.playing)
        self.assertEqual(self.dst.queue, ["x", "m1", "m2", "m3"], "la file du mix rechargé ne doit pas être réécrite")

    def test_target_already_in_mix_uses_queue(self) -> None:
        self.dst.playlist_id = MIX
        transfer(
            DirectEndpoint("a", authed(self.src), 25),
            DirectEndpoint("b", authed(self.dst), 25, self.restarting_loader),
        )
        self.assertEqual(self.calls, [])
        self.assertEqual(self.dst.current, "x")

    def test_loader_failure_falls_back_to_queue(self) -> None:
        from pearsync.launcher import LauncherError

        def failing(state: PlaybackState) -> None:
            raise LauncherError("option désactivée")

        with self.assertLogs("pearsync.handoff", "WARNING"):
            transfer(DirectEndpoint("a", authed(self.src), 25), DirectEndpoint("b", authed(self.dst), 25, failing))
        self.assertEqual(self.dst.current, "x")
        self.assertEqual(self.dst.queue[self.dst.selected + 1 :], ["y", "z"])

    def test_agent_passes_loader(self) -> None:
        relay = RelayClient("http://127.0.0.1:9", "t")  # non sollicité par handle()
        agent = Agent(authed(self.dst), relay, "b", context_loader=self.restarting_loader)
        state = capture_state(authed(self.src))
        assert state is not None
        agent.handle({"type": "resume", "state": state.to_dict()})
        self.assertEqual((self.dst.current, self.dst.playlist_id), ("x", MIX))

    def test_state_carries_playlist(self) -> None:
        s = PlaybackState("abcdefghijk", position=42, paused=True, playlist_id=MIX, ts=0)
        self.assertEqual(s.music_url(), f"https://music.youtube.com/watch?v=abcdefghijk&list={MIX}&t=42")
        self.assertEqual(PlaybackState.from_dict(s.to_dict()).playlist_id, MIX)
        bad = PlaybackState.from_dict({"video_id": "abcdefghijk", "playlist_id": "x&evil=1"})
        self.assertEqual(bad.playlist_id, "")


class FakeLauncher(PearLauncher):
    def __init__(self, config: Path, exe: Path) -> None:
        super().__init__(config, exe, stop_timeout=1.0)
        self.running = True
        self.events: list[str] = []
        self.graceful_works = True

    def is_running(self) -> bool:
        return self.running

    def running_exe(self) -> Path | None:
        return None

    def request_close(self) -> None:
        self.events.append("close")
        if self.graceful_works:
            self.running = False

    def force_kill(self) -> None:
        self.events.append("kill")
        self.running = False

    def start(self, exe: Path) -> None:
        self.events.append(f"start:{exe.name}")
        self.running = True


class Launcher(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.cfg = d / "config.json"
        self.cfg.write_text(json.dumps({"url": "https://music.youtube.com/", "options": {"tray": True}}), "utf-8")
        self.exe = d / "YouTube Music.exe"
        self.exe.write_bytes(b"")
        self.state = PlaybackState("abcdefghijk", 30, False, playlist_id=MIX)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_restart_writes_url_between_stop_and_start(self) -> None:
        launcher = FakeLauncher(self.cfg, self.exe)
        launcher._write_url = self._spy(launcher._write_url, launcher)  # type: ignore[method-assign]
        launcher.load_context(self.state)
        self.assertEqual(launcher.events, ["close", "write", "start:YouTube Music.exe"])
        data = json.loads(self.cfg.read_text("utf-8"))
        self.assertRegex(data["url"], rf"^https://music\.youtube\.com/watch\?v=abcdefghijk&list={MIX}&t=3[01]$")
        self.assertEqual(data["options"], {"tray": True}, "le reste de la config doit être préservé")
        self.assertTrue((self.cfg.parent / "config.json.pearsync.bak").exists())

    def test_resume_url_carries_timecode(self) -> None:
        from pearsync.launcher import context_url

        playing = PlaybackState("abcdefghijk", 30, False, duration=200, ts=1000.0)
        self.assertTrue(context_url(playing, now=1012.0).endswith("&t=42"), "la position avance pendant la lecture")
        paused = PlaybackState("abcdefghijk", 30, True, ts=1000.0)
        self.assertTrue(context_url(paused, now=1500.0).endswith("&t=30"))
        start = PlaybackState("abcdefghijk", 0.4, True)
        self.assertEqual(context_url(start), "https://music.youtube.com/watch?v=abcdefghijk")

    def test_hidden_in_tray_is_force_killed(self) -> None:
        launcher = FakeLauncher(self.cfg, self.exe)
        launcher.graceful_works = False
        launcher.load_context(self.state)
        self.assertEqual(launcher.events, ["close", "kill", "start:YouTube Music.exe"])

    def test_resume_on_start_disabled_does_nothing(self) -> None:
        self.cfg.write_text(json.dumps({"options": {"resumeOnStart": False}}), "utf-8")
        launcher = FakeLauncher(self.cfg, self.exe)
        with self.assertRaises(ContextLoadError):
            launcher.load_context(self.state)
        self.assertEqual(launcher.events, [], "Pear ne doit pas être fermé si on ne peut pas le relancer sur le mix")

    def test_pear_restarted_even_if_write_fails(self) -> None:
        launcher = FakeLauncher(self.cfg, self.exe)

        def boom(url: str) -> None:
            raise LauncherError("disque plein")

        launcher._write_url = boom  # type: ignore[method-assign]
        with self.assertRaises(LauncherError):
            launcher.load_context(self.state)
        self.assertEqual(launcher.events[-1], "start:YouTube Music.exe")

    def test_invalid_ids_rejected_before_touching_anything(self) -> None:
        launcher = FakeLauncher(self.cfg, self.exe)
        with self.assertRaises(LauncherError):
            launcher.load_context(PlaybackState("abc", 0, False, playlist_id=MIX))
        self.assertEqual(launcher.events, [])

    @staticmethod
    def _spy(fn: Callable[[str], None], launcher: FakeLauncher) -> Callable[[str], None]:
        def wrapped(url: str) -> None:
            launcher.events.append("write")
            fn(url)

        return wrapped


class Cli(unittest.TestCase):
    def test_transfer_command(self) -> None:
        src = FakePear(queue=["x"], selected=0, position=30, playing=True, auth=False)
        dst = FakePear(queue=["o"], selected=0, auth=False)
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "config.toml"
            cfg.write_text(
                'client_id = "t"\nlocal = "ici"\n'
                f'[instances.ici]\nurl = "{dst.url}"\n[instances.la]\nurl = "{src.url}"\n',
                encoding="utf-8",
            )
            os.environ["XDG_CONFIG_HOME"] = os.environ["APPDATA"] = d
            try:
                from pearsync.cli import main

                main(["-c", str(cfg), "pull", "la"])
            finally:
                src.close()
                dst.close()
        self.assertEqual(dst.current, "x")
        self.assertFalse(src.playing)


if __name__ == "__main__":
    unittest.main()
