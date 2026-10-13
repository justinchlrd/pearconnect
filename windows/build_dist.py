"""Prépare server/dist/ : fichiers servis par le serveur Connect pour l'installation en une commande."""
import hashlib, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "server" / "dist"
BASE = "http://192.168.1.168:8787"
CONNECT = "https://192.168.1.168:8443"
PEAR_VERSION = "3.12.0"

def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()

def main(asar_zip: str, ca: str, apk: str) -> None:
    DIST.mkdir(parents=True, exist_ok=True)
    for f in DIST.iterdir():
        f.unlink()
    shutil.copy(asar_zip, DIST / "app-asar.zip")
    shutil.copy(ca, DIST / "pearconnect-ca.crt")
    shutil.copy(apk, DIST / "PearConnect.apk")
    tpl = (ROOT / "windows" / "install.ps1.in").read_text(encoding="utf-8")
    values = {
        "__BASE_URL__": BASE, "__CONNECT_URL__": CONNECT, "__PEAR_VERSION__": PEAR_VERSION,
        "__ASAR_SHA256__": sha(DIST / "app-asar.zip"), "__CA_SHA256__": sha(DIST / "pearconnect-ca.crt"),
    }
    for action in ("install", "uninstall"):
        text = tpl.replace("__ACTION__", action)
        for k, v in values.items():
            text = text.replace(k, v)
        assert "__" not in "".join(t for t in text.split() if t.startswith("'__")), "placeholder oublié"
        # servi en text/plain; charset=utf-8 : pas de BOM (irm | iex le garderait dans le code)
        (DIST / f"{action}.ps1").write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
    for f in sorted(DIST.iterdir()):
        print(f"{f.name:22} {sha(f)[:16]}  {f.stat().st_size} o")

if __name__ == "__main__":
    main(*sys.argv[1:4])
