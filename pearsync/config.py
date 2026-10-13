"""Chargement de la configuration (TOML) et stockage des jetons API."""

from __future__ import annotations

import json
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "pearsync"


@dataclass
class RelayConfig:
    url: str
    token: str
    node: str  # nom de CE poste auprès du relais
    ca: str | None = None  # autorité de certification du serveur (HTTPS du homelab)


@dataclass
class PearAppConfig:
    """Instance Pear de CE poste, pour recharger un mix en la redémarrant (Windows)."""

    reload_mix: bool = True
    exe: Path | None = None
    config: Path | None = None


@dataclass
class Config:
    client_id: str
    local: str  # nom de l'instance locale dans `instances`
    instances: dict[str, str] = field(default_factory=dict)  # nom -> URL de l'API
    relay: RelayConfig | None = None
    queue_limit: int = 25
    pear_app: PearAppConfig = field(default_factory=PearAppConfig)
    path: Path | None = None


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    p = Path(path) if path else Path(os.environ.get("PEARSYNC_CONFIG") or config_dir() / "config.toml")
    try:
        with p.open("rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        sys.exit(f"Configuration introuvable : {p} (voir config.example.toml)")

    instances = {str(k): str(v["url"]) for k, v in (raw.get("instances") or {}).items()}
    local = str(raw.get("local") or "local")
    instances.setdefault(local, "http://127.0.0.1:26538")

    relay = None
    if r := raw.get("relay"):
        token = os.environ.get(r.get("token_env") or "PEARSYNC_RELAY_TOKEN") or r.get("token")
        if not token:
            sys.exit("[relay] configuré mais aucun jeton (token_env / token)")
        ca = os.path.expandvars(str(r["ca"])) if r.get("ca") else None
        relay = RelayConfig(url=str(r["url"]).rstrip("/"), token=str(token), node=str(r.get("node") or local), ca=ca)

    app = raw.get("pear_app") or {}
    pear_app = PearAppConfig(
        reload_mix=bool(app.get("reload_mix", True)),
        exe=Path(os.path.expandvars(app["exe"])) if app.get("exe") else None,
        config=Path(os.path.expandvars(app["config"])) if app.get("config") else None,
    )

    return Config(
        client_id=str(raw.get("client_id") or "pearsync"),
        local=local,
        instances=instances,
        relay=relay,
        queue_limit=int(raw.get("queue_limit", 25)),
        pear_app=pear_app,
        path=p,
    )


class TokenStore:
    """Jetons JWT de l'API Pear, par URL d'instance (fichier JSON dans le profil utilisateur)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config_dir() / "tokens.json"

    def _load(self) -> dict[str, str]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def get(self, url: str) -> str | None:
        return self._load().get(url)

    def set(self, url: str, token: str) -> None:
        data = self._load()
        data[url] = token
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        if os.name != "nt":
            tmp.chmod(0o600)
        tmp.replace(self.path)  # écriture atomique
