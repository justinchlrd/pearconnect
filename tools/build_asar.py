#!/usr/bin/env python3
"""Construit l'app.asar « Pear + plugin Pear Connect » depuis Windows (ou Linux), sans conteneur.

Équivalent de tools/e2e/rebuild.sh, mais autonome :
  1. copie forks/pear-desktop/plugin-src/ dans le dépôt de build (src/plugins/pear-connect/) ;
  2. oxlint + tsc sur le plugin, puis `electron-vite build` (dist/) ;
  3. extrait l'app.asar officiel 3.12.0 (app.asar.original de l'installation locale), remplace dist/,
     réempaquette avec les assets hors archive (`--unpack-dir assets`, comme l'original) ;
  4. zippe l'asar et, avec --dist, régénère server/dist/ (installeur, SHA-256) via windows/build_dist.py.

Prérequis (une fois) :
  - dépôt de build : archive du tag v3.12.0 de pear-devs/pear-desktop dans <build>/pear-desktop, patches
    appliqués, `pnpm install --frozen-lockfile --ignore-scripts --config.engine-strict=false`
    (ELECTRON_SKIP_BINARY_DOWNLOAD=1 : le binaire Electron n'est pas nécessaire pour construire) ;
  - outil asar : `npm install @electron/asar@3` dans <build>/tools.

Usage :
  python tools/build_asar.py                      # construit <build>/out/app.asar et app-asar.zip
  python tools/build_asar.py --dist               # + server/dist/ (reprend l'AC et l'APK déjà présents)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_SRC = ROOT / "forks" / "pear-desktop" / "plugin-src"
DEFAULT_BUILD = ROOT.parent / "build"
PEAR_VERSION = "3.12.0"


def default_original() -> Path | None:
    local = os.environ.get("LOCALAPPDATA")
    candidates = []
    if local:
        candidates.append(Path(local) / "Programs" / "youtube-music" / "resources" / "app.asar.original")
    candidates.append(Path(r"C:\Program Files\YouTube Music\resources\app.asar.original"))
    return next((c for c in candidates if c.is_file()), None)


def run(cmd: list[str], cwd: Path, **kw: object) -> subprocess.CompletedProcess[str]:
    print("$", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=cwd, text=True, encoding="utf-8", errors="replace", **kw)  # type: ignore[call-overload]


def bin_path(node_modules: Path, name: str) -> str:
    exe = node_modules / ".bin" / (f"{name}.cmd" if os.name == "nt" else name)
    if not exe.exists():
        sys.exit(f"outil absent : {exe} (voir les prérequis en tête de script)")
    return str(exe)


def sync_plugin(repo: Path) -> None:
    dest = repo / "src" / "plugins" / "pear-connect"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(PLUGIN_SRC, dest)
    print(f"plugin copié dans {dest}")


def check(repo: Path, nm: Path) -> None:
    lint = run([bin_path(nm, "oxlint"), "--type-aware", "src/plugins/pear-connect"], repo, capture_output=True)
    print(lint.stdout[-2000:], lint.stderr[-2000:], sep="")
    if lint.returncode != 0:
        sys.exit("oxlint : erreurs dans le plugin")
    tsc = run([bin_path(nm, "tsc"), "-p", "tsconfig.json", "--noEmit"], repo, capture_output=True)
    ours = [line for line in tsc.stdout.splitlines() if "pear-connect" in line]
    if ours:
        sys.exit("tsc :\n" + "\n".join(ours))
    print("tsc : plugin sans erreur de type")


def build(repo: Path, nm: Path) -> Path:
    dist = repo / "dist"
    if dist.exists():
        shutil.rmtree(dist)
    env = {**os.environ, "ELECTRON_SKIP_BINARY_DOWNLOAD": "1"}
    res = run([bin_path(nm, "electron-vite"), "build"], repo, env=env, capture_output=True)
    (repo.parent / "build.log").write_text(res.stdout + res.stderr, "utf-8")
    if res.returncode != 0 or not (dist / "main").exists():
        sys.exit(f"electron-vite build en échec (voir {repo.parent / 'build.log'})\n{res.stderr[-3000:]}")
    print("electron-vite : dist/ construit")
    return dist


def repack(original: Path, dist: Path, asar_bin: str, out: Path) -> Path:
    work = out / "asar-work"
    app = work / "app"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    # asar lit les fichiers « unpacked » dans <nom>.unpacked/ à côté de l'archive : on recopie
    # l'original sous un nom neutre avec son dossier app.asar.unpacked (assets/)
    src = work / "source.asar"
    shutil.copy(original, src)
    unpacked = original.parent / "app.asar.unpacked"
    if unpacked.is_dir():
        shutil.copytree(unpacked, work / "source.asar.unpacked")
    r = run([asar_bin, "extract", str(src), str(app)], work)
    if r.returncode != 0:
        sys.exit("asar extract en échec")
    shutil.rmtree(app / "dist")
    shutil.copytree(dist, app / "dist")
    target = out / "app.asar"
    for stale in (target, out / "app.asar.unpacked"):
        if stale.is_dir():
            shutil.rmtree(stale)
        elif stale.exists():
            stale.unlink()
    # comme l'asar officiel : assets/ hors archive (app.asar.unpacked)
    r = run([asar_bin, "pack", str(app), str(target), "--unpack-dir", "assets"], work)
    if r.returncode != 0:
        sys.exit("asar pack en échec")
    return target


def zip_asar(asar: Path) -> Path:
    z = asar.with_name("app-asar.zip")
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(asar, "app.asar")
    return z


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--build", type=Path, default=DEFAULT_BUILD, help=f"dossier de build (défaut {DEFAULT_BUILD})")
    ap.add_argument("--original", type=Path, default=default_original(), help="app.asar officiel 3.12.0")
    ap.add_argument("--skip-check", action="store_true", help="sans oxlint ni tsc")
    ap.add_argument("--dist", action="store_true", help="régénère aussi server/dist/ (installeur)")
    ap.add_argument("--apk-version", help="<versionCode>:<versionName> de l'APK servi, s'il diffère des sources")
    args = ap.parse_args()

    repo = args.build / "pear-desktop"
    nm = repo / "node_modules"
    tools_nm = args.build / "tools" / "node_modules"
    out = args.build / "out"
    out.mkdir(parents=True, exist_ok=True)
    if not (repo / "package.json").exists():
        sys.exit(f"dépôt de build absent : {repo}")
    if not args.original or not args.original.is_file():
        sys.exit("app.asar.original introuvable : passer --original <chemin>")
    version = (repo / "package.json").read_text("utf-8")
    if f'"version": "{PEAR_VERSION}"' not in version:
        sys.exit(f"le dépôt de build n'est pas Pear {PEAR_VERSION}")

    sync_plugin(repo)
    if not args.skip_check:
        check(repo, nm)
    dist = build(repo, nm)
    asar = repack(args.original, dist, bin_path(tools_nm, "asar"), out)
    z = zip_asar(asar)
    print(f"\n{asar}  ({asar.stat().st_size} o)\n{z}  ({z.stat().st_size} o)")

    if args.dist:
        sys.path.insert(0, str(ROOT / "windows"))
        import build_dist  # noqa: PLC0415

        server_dist = ROOT / "server" / "dist"
        ca, apk = server_dist / "pearconnect-ca.crt", server_dist / "PearConnect.apk"
        if not ca.is_file() or not apk.is_file():
            sys.exit("server/dist/ doit déjà contenir pearconnect-ca.crt et PearConnect.apk")
        keep = out / "dist-inputs"
        keep.mkdir(exist_ok=True)
        shutil.copy(ca, keep / ca.name)
        shutil.copy(apk, keep / apk.name)
        build_dist.main(str(z), str(keep / ca.name), str(keep / apk.name), args.apk_version)


if __name__ == "__main__":
    main()
