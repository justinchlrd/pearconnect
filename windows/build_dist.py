"""Prépare server/dist/ : fichiers servis par le serveur Connect pour l'installation en une commande.

Usage : python windows/build_dist.py <app-asar.zip> <ca.crt> <apk> [<versionCode>:<versionName> de l'APK]

Produit install/uninstall.ps1 (Windows), install/uninstall.sh (Linux) et versions.json, que le plugin
Pear et l'appli Android comparent à leur propre version pour signaler une mise à jour.
La version de l'APK est lue dans android/app/build.gradle.kts, sauf si elle est donnée en 4e argument
(utile quand l'APK servi est plus ancien que les sources).
"""
import datetime as dt
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "server" / "dist"
BASE = "http://192.168.1.168:8787"
CONNECT = "https://192.168.1.168:8443"
PEAR_VERSION = "3.12.0"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def plugin_version() -> str:
    src = (ROOT / "forks" / "pear-desktop" / "plugin-src" / "version.ts").read_text(encoding="utf-8")
    m = re.search(r"PLUGIN_VERSION\s*=\s*'(\d+(?:\.\d+)*)'", src)
    if not m:
        sys.exit("PLUGIN_VERSION introuvable dans plugin-src/version.ts")
    return m.group(1)


def android_version(spec: str | None) -> tuple[int, str]:
    if spec:
        code, name = spec.split(":", 1)
        return int(code), name
    gradle = (ROOT / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
    code = re.search(r"versionCode\s*=\s*(\d+)", gradle)
    name = re.search(r'versionName\s*=\s*"([^"]+)"', gradle)
    if not code or not name:
        sys.exit("versionCode/versionName introuvables dans build.gradle.kts")
    print(f"version de l'APK lue dans build.gradle.kts : {name.group(1)} ({code.group(1)}) ; "
          "passer <code>:<nom> en 4e argument si l'APK servi est plus ancien")
    return int(code.group(1)), name.group(1)


def main(asar_zip: str, ca: str, apk: str, apk_version: str | None = None) -> None:
    DIST.mkdir(parents=True, exist_ok=True)
    for f in DIST.iterdir():
        f.unlink()
    shutil.copy(asar_zip, DIST / "app-asar.zip")
    shutil.copy(ca, DIST / "pearconnect-ca.crt")
    shutil.copy(apk, DIST / "PearConnect.apk")
    tpl = (ROOT / "windows" / "install.ps1.in").read_text(encoding="utf-8")
    tpl_sh = (ROOT / "linux" / "install.sh.in").read_text(encoding="utf-8")
    asar_sha = sha(DIST / "app-asar.zip")
    values = {
        "__BASE_URL__": BASE, "__CONNECT_URL__": CONNECT, "__PEAR_VERSION__": PEAR_VERSION,
        "__ASAR_SHA256__": asar_sha, "__CA_SHA256__": sha(DIST / "pearconnect-ca.crt"),
    }
    for action in ("install", "uninstall"):
        text = tpl.replace("__ACTION__", action)
        for k, v in values.items():
            text = text.replace(k, v)
        assert "__" not in "".join(t for t in text.split() if t.startswith("'__")), "placeholder oublié"
        # servi en text/plain; charset=utf-8 : pas de BOM (irm | iex le garderait dans le code)
        (DIST / f"{action}.ps1").write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
        # Linux : même logique en bash (curl …/install.sh | bash), fins de ligne LF
        sh = tpl_sh.replace("__ACTION__", action)
        for k, v in values.items():
            sh = sh.replace(k, v)
        assert "__" not in "".join(t for t in sh.split() if t.startswith("'__")), "placeholder oublié (sh)"
        (DIST / f"{action}.sh").write_bytes(sh.replace("\r\n", "\n").encode("utf-8"))

    code, name = android_version(apk_version)
    versions = {
        "pear": PEAR_VERSION,
        "plugin": {
            "version": plugin_version(),
            "asar_sha256": asar_sha,
            "install_windows": f"irm {BASE}/install.ps1 | iex",
            "install_linux": f"curl -fsSL {BASE}/install.sh | bash",
        },
        "android": {
            "version_code": code,
            "version_name": name,
            "url": f"{BASE}/dl/PearConnect.apk",
            "sha256": sha(DIST / "PearConnect.apk"),
        },
        "published": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
    }
    # write_bytes : fins de ligne LF même sous Windows
    (DIST / "versions.json").write_bytes((json.dumps(versions, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    for f in sorted(DIST.iterdir()):
        print(f"{f.name:22} {sha(f)[:16]}  {f.stat().st_size} o")
    print(f"plugin v{versions['plugin']['version']}, Android {name} ({code})")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    main(*sys.argv[1:5])
