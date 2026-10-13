"""Rechargement d'un contexte de lecture (mix, playlist, album) dans Pear Desktop.

L'API de Pear ne sait ajouter que des titres isolés à la file : le contexte
(« Mix de … », avec sa suite générée à l'infini) est perdu. Pear sait en
revanche, au démarrage, rouvrir l'URL mémorisée dans sa configuration
(option « Reprendre la dernière chanson quand l'application démarre »).
Pour recharger un mix sur CE poste, on ferme donc Pear, on écrit
https://music.youtube.com/watch?v=<titre>&list=<mix> dans sa configuration, et
on le relance : c'est YouTube Music lui-même qui reconstruit le mix.

Windows uniquement (c'est là que tournent les instances Pear de l'utilisateur).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .handoff import ContextLoadError, PlaybackState

log = logging.getLogger("pearsync.launcher")

IMAGE_NAME = "YouTube Music.exe"  # productName de Pear Desktop
PLAYLIST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{2,128}$")
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


class LauncherError(ContextLoadError):
    pass


def default_config_candidates() -> list[Path]:
    appdata = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return [
        appdata / "YouTube Music" / "config.json",
        appdata / "com.github.th-ch.youtube-music" / "config.json",
    ]


def default_exe_candidates() -> list[Path]:
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return [local / "Programs" / "youtube-music" / IMAGE_NAME]


def context_url(state: PlaybackState, now: float | None = None) -> str:
    """URL de reprise : titre, mix et position (t=, en secondes).

    Pear se contente de charger cette URL ; c'est YouTube Music qui interprète t=.
    La position est donc aussi recalée par l'API après le démarrage (apply_state).
    """
    if not VIDEO_ID_RE.match(state.video_id):
        raise LauncherError("identifiant de titre invalide")
    url = f"https://music.youtube.com/watch?v={state.video_id}"
    if state.playlist_id:
        if not PLAYLIST_ID_RE.match(state.playlist_id):
            raise LauncherError("identifiant de mix invalide")
        url += f"&list={state.playlist_id}"
    pos = int(state.position_now(now))
    if pos >= 1:
        url += f"&t={pos}"
    return url


class PearLauncher:
    """Arrête, reconfigure et relance l'instance Pear locale."""

    def __init__(
        self,
        config_path: Path | None = None,
        exe_path: Path | None = None,
        stop_timeout: float = 8.0,
    ) -> None:
        self.config_path = config_path
        self.exe_path = exe_path
        self.stop_timeout = stop_timeout

    # -- opérations système (surchargées dans les tests) -----------------
    def _run(self, *args: str) -> str:
        try:
            res = subprocess.run(args, capture_output=True, text=True, check=False, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise LauncherError(f"{args[0]} : {e}") from e
        return res.stdout

    def is_running(self) -> bool:
        out = self._run("tasklist", "/FI", f"IMAGENAME eq {IMAGE_NAME}", "/FO", "CSV", "/NH")
        return IMAGE_NAME.lower() in out.lower()

    def running_exe(self) -> Path | None:
        out = self._run(
            "powershell",
            "-NoProfile",
            "-Command",
            f"(Get-Process -Name '{IMAGE_NAME[:-4]}' -ErrorAction SilentlyContinue | Select-Object -First 1).Path",
        ).strip()
        return Path(out) if out else None

    def request_close(self) -> None:
        self._run("taskkill", "/IM", IMAGE_NAME)

    def force_kill(self) -> None:
        self._run("taskkill", "/F", "/T", "/IM", IMAGE_NAME)

    def start(self, exe: Path) -> None:
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            subprocess.Popen([str(exe)], creationflags=flags, close_fds=True, cwd=str(exe.parent))
        except OSError as e:
            raise LauncherError(f"lancement de Pear impossible : {e}") from e

    # -- logique ------------------------------------------------------------
    def _config_file(self) -> Path:
        if self.config_path:
            return self.config_path
        for c in default_config_candidates():
            if c.exists():
                return c
        raise LauncherError("config.json de Pear introuvable (renseigner [pear_app] config)")

    def _exe(self) -> Path:
        exe = self.exe_path or self.running_exe()
        if not exe:
            exe = next((c for c in default_exe_candidates() if c.exists()), None)
        if not exe or not exe.exists():
            raise LauncherError("exécutable de Pear introuvable (renseigner [pear_app] exe)")
        return exe

    def _read_config(self) -> dict[str, object]:
        try:
            data = json.loads(self._config_file().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise LauncherError(f"lecture de la config Pear impossible : {e}") from e
        if not isinstance(data, dict):
            raise LauncherError("config Pear inattendue")
        return data

    def resume_on_start(self) -> bool:
        options = self._read_config().get("options")
        # absent = valeur par défaut de Pear (activée)
        return not isinstance(options, dict) or options.get("resumeOnStart", True) is not False

    def _stop(self) -> None:
        if not self.is_running():
            return
        self.request_close()  # fermeture propre (peut seulement masquer dans la zone de notification)
        deadline = time.monotonic() + self.stop_timeout
        while time.monotonic() < deadline:
            if not self.is_running():
                return
            time.sleep(0.5)
        self.force_kill()
        deadline = time.monotonic() + self.stop_timeout
        while time.monotonic() < deadline:
            if not self.is_running():
                return
            time.sleep(0.5)
        raise LauncherError("Pear ne s'est pas arrêté")

    def _write_url(self, url: str) -> None:
        path = self._config_file()
        data = self._read_config()
        data["url"] = url
        tmp = path.with_name(path.name + ".pearsync.tmp")
        try:
            shutil.copy2(path, path.with_name(path.name + ".pearsync.bak"))
            tmp.write_text(json.dumps(data, indent="\t", ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)  # atomique : jamais de config à moitié écrite
        except OSError as e:
            raise LauncherError(f"écriture de la config Pear impossible : {e}") from e

    def prime_resume(self, state: PlaybackState) -> bool:
        """Pear fermé : prépare sa reprise au démarrage sur `state`. Faux si Pear tourne."""
        url = context_url(state)
        if self.is_running() or not self.resume_on_start():
            return False
        self._write_url(url)
        return True

    def load_context(self, state: PlaybackState) -> None:
        """Relance Pear directement sur le titre, dans son mix. Lève LauncherError sinon."""
        context_url(state)  # validation avant de toucher à quoi que ce soit
        if not self.resume_on_start():
            raise LauncherError(
                "option « Reprendre la dernière chanson quand l'application démarre » désactivée dans Pear"
            )
        exe = self._exe()  # avant l'arrêt : le chemin se lit sur le processus en cours
        self._stop()
        try:
            # la config n'est écrite qu'une fois Pear arrêté, sinon il l'écraserait au prochain titre ;
            # l'URL est calculée à ce moment-là pour que t= tienne compte du temps d'arrêt
            url = context_url(state)
            log.info("redémarrage de Pear sur %s", url)
            self._write_url(url)
        finally:
            self.start(exe)  # quoi qu'il arrive, on ne laisse pas Pear fermé
