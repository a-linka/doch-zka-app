"""Vydání nové verze jedním příkazem.

Použití (ve složce projektu):
    python vydat.py "Stručný popis změny"

Co udělá:
  1. přečte APP_VERSION z dochazka_app.py (tag se tak vždy shoduje s verzí v appce),
  2. zkontroluje, že je verze VYŠŠÍ než všechna dosavadní vydání na GitHubu,
  3. ukáže, co se nahraje, a zeptá se na potvrzení,
  4. provede commit všech změn, vytvoří tag (např. v0.5.3) a nahraje obojí na GitHub.
Sestavení .exe pak spustí GitHub sám (záložka Actions).
"""
import re
import subprocess
import sys
from pathlib import Path

APP_FILE = "dochazka_app.py"


def git(*args, check=True):
    result = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8")
    if check and result.returncode != 0:
        raise SystemExit(f"Chyba příkazu 'git {' '.join(args)}':\n{(result.stderr or result.stdout).strip()}")
    return result.stdout.strip()


def parse_version(text):
    match = re.search(r"\d+(?:\.\d+)*", text)
    return tuple(int(p) for p in match.group(0).split(".")) if match else None


def main():
    if len(sys.argv) < 2:
        raise SystemExit('Chybí popis změny.\nPoužití: python vydat.py "Stručný popis změny"')
    message = " ".join(sys.argv[1:]).strip()

    if not Path(APP_FILE).exists():
        raise SystemExit(f"Nenašel jsem {APP_FILE}. Spusťte skript ve složce projektu.")
    found = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', Path(APP_FILE).read_text(encoding="utf-8"), re.M)
    if not found:
        raise SystemExit(f"V {APP_FILE} jsem nenašel řádek APP_VERSION = \"...\".")
    version = found.group(1)
    tag = f"v{version}"
    new = parse_version(version)
    if new is None:
        raise SystemExit(f"APP_VERSION \"{version}\" nevypadá jako číslo verze (např. 0.5.3).")

    git("rev-parse", "--is-inside-work-tree")
    remote_tags = git("ls-remote", "--tags", "origin")                 # vyžaduje internet
    existing = [parse_version(line.split("refs/tags/")[-1]) for line in remote_tags.splitlines() if "refs/tags/" in line]
    existing += [parse_version(t) for t in git("tag", "--list").splitlines()]
    existing = [v for v in existing if v]
    if existing:
        highest = max(existing)
        pad = lambda v: v + (0,) * (max(len(new), len(highest)) - len(v))
        if pad(new) <= pad(highest):
            raise SystemExit(
                f"APP_VERSION {version} není vyšší než dosavadní vydání (nejvyšší je {'.'.join(map(str, highest))}).\n"
                "Zvyšte APP_VERSION v dochazka_app.py (a nikdy nenechávejte zkušební hodnotu jako 0.0.1).")

    changes = git("status", "--short")
    print(f"Verze: {version}  ->  tag {tag}")
    print("Změny, které se nahrají:" if changes else "Žádné neuložené změny (vytvoří se jen tag).")
    if changes:
        print(changes)
    if input("Pokračovat? [a/N] ").strip().lower() not in ("a", "ano", "y", "yes"):
        raise SystemExit("Zrušeno, nic se nenahrálo.")

    if changes:
        git("add", "-A")
        git("commit", "-m", message)
    git("tag", "-a", tag, "-m", message)
    git("push", "origin", "HEAD", tag)
    print(f"Hotovo. Na GitHubu v záložce Actions teď běží sestavení verze {version}.")


if __name__ == "__main__":
    main()
