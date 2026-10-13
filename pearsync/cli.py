"""Interface en ligne de commande : python -m pearsync <commande>."""

from __future__ import annotations

import argparse
import logging
import sys

from .agent import Agent
from .config import Config, TokenStore, load_config
from .endpoints import RelayEndpoint, direct_client, local_context_loader, relay_client, resolve, transfer
from .handoff import HandoffError
from .pear_api import PearApiError, PearClient
from .relay_client import RelayError


def _fmt(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"


def cmd_auth(cfg: Config, args: argparse.Namespace) -> None:
    url = cfg.instances[args.instance]
    print(f"Demande d'accès à {url} sous l'identifiant « {cfg.client_id} »…")
    print("→ Clique « Autoriser » dans la boîte de dialogue de Pear Desktop sur cette machine.")
    token = PearClient(url).request_token(cfg.client_id)
    TokenStore().set(url, token)
    print("Jeton enregistré.")


def cmd_status(cfg: Config, args: argparse.Namespace) -> None:
    tokens = TokenStore()
    for name in cfg.instances:
        try:
            state = resolve(cfg, name, tokens).capture()
            if state:
                icon = "⏸" if state.paused else "▶"
                desc = f"{state.artist} — {state.title} [{_fmt(state.position_now())} {icon}]"
                if state.playlist_id:
                    desc += f" (contexte {state.playlist_id})"
            else:
                desc = "rien en lecture"
        except PearApiError as e:
            desc = f"erreur : {e}"
        print(f"{name:12} (direct) {desc}")
    if cfg.relay:
        snap = relay_client(cfg).snapshot()
        for name, dev in (snap.get("devices") or {}).items():
            st = dev.get("state")
            mark = "*" if snap.get("active") == name else " "
            if not dev.get("online"):
                desc = "hors ligne"
            elif st:
                icon = "⏸" if st.get("paused") else "▶"
                desc = f"{st.get('artist')} — {st.get('title')} [{_fmt(st.get('position') or 0)} {icon}]"
            else:
                desc = "disponible"
            print(f"{mark}{name:11} (serveur) {desc}")


def cmd_transfer(cfg: Config, args: argparse.Namespace) -> None:
    tokens = TokenStore()
    src, dst = resolve(cfg, args.source, tokens), resolve(cfg, args.target, tokens)
    state = transfer(src, dst, with_queue=not args.no_queue, keep_source=args.keep_source)
    how = "commande envoyée à l'agent de" if isinstance(dst, RelayEndpoint) else "repris sur"
    print(f"{state.artist} — {state.title} à {_fmt(state.position_now())} : {how} « {dst.name} »")


def cmd_link(cfg: Config, args: argparse.Namespace) -> None:
    state = resolve(cfg, args.source).capture()
    if not state:
        raise HandoffError(f"rien en lecture sur « {args.source} »")
    print(state.music_url())


def cmd_agent(cfg: Config, args: argparse.Namespace) -> None:
    if not cfg.relay:
        sys.exit("Le mode agent nécessite une section [relay] dans la configuration.")
    loader = local_context_loader(cfg)
    launcher = getattr(loader, "__self__", None)  # PearLauncher local (Windows) ou None
    Agent(
        direct_client(cfg, cfg.local),
        relay_client(cfg),
        cfg.relay.node,
        cfg.queue_limit,
        loader,
        launcher,
    ).run(args.interval)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="pearsync", description="Transfert de lecture entre instances Pear Desktop")
    ap.add_argument("-c", "--config", help="fichier TOML (défaut : %%APPDATA%%\\pearsync\\config.toml)")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--log-file", help="journaliser dans ce fichier (utile pour l'agent sans console)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("auth", help="obtenir un jeton API pour une instance")
    p.add_argument("instance")
    p.set_defaults(fn=cmd_auth)

    sub.add_parser("status", help="état de toutes les instances").set_defaults(fn=cmd_status)

    for name, help_ in (
        ("transfer", "SOURCE CIBLE : transférer la lecture"),
        ("pull", "SOURCE : reprendre ici ce qui joue sur SOURCE"),
        ("push", "CIBLE : envoyer ce qui joue ici vers CIBLE"),
    ):
        p = sub.add_parser(name, help=help_)
        if name in ("transfer", "pull"):
            p.add_argument("source")
        if name in ("transfer", "push"):
            p.add_argument("target")
        p.add_argument("--no-queue", action="store_true", help="ne pas recopier la suite de la file")
        p.add_argument("--keep-source", action="store_true", help="ne pas mettre la source en pause")
        p.set_defaults(fn=cmd_transfer, kind=name)

    p = sub.add_parser("link", help="lien music.youtube.com (titre, mix et position) pour le téléphone")
    p.add_argument("source")
    p.set_defaults(fn=cmd_link)

    p = sub.add_parser("agent", help="publier l'état local sur le relais et exécuter les commandes")
    p.add_argument("--interval", type=float, default=3.0)
    p.set_defaults(fn=cmd_agent)

    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        filename=args.log_file,
        encoding="utf-8",
    )
    cfg = load_config(args.config)

    if getattr(args, "kind", None) == "pull":
        args.target = cfg.local
    elif getattr(args, "kind", None) == "push":
        args.source = cfg.local
    if args.cmd == "auth" and args.instance not in cfg.instances:
        sys.exit(f"Instance inconnue : {args.instance}")

    try:
        args.fn(cfg, args)
    except (HandoffError, PearApiError, RelayError) as e:
        sys.exit(f"Échec : {e}")
    except KeyboardInterrupt:
        pass


def agent_main() -> None:
    """Point d'entrée sans console (pearsync-agent.exe) : journal dans le profil utilisateur."""
    from .config import config_dir

    config_dir().mkdir(parents=True, exist_ok=True)
    main(["--log-file", str(config_dir() / "agent.log"), *sys.argv[1:], "agent"])
