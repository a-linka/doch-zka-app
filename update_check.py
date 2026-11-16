"""Kontrola nové verze Docházky MOP na GitHubu + jednoklikové nahrazení programu.

- Při startu se na pozadí zeptá GitHubu na poslední vydání. Když je novější než APP_VERSION,
  ukáže se pod záhlavím žlutý pruh.
- V `.exe` (Windows), které leží ve složce s právem zápisu, je v pruhu tlačítko "Aktualizovat teď":
  stáhne nový soubor, zkontroluje ho a přímo v programu ho vymění za běžící (Windows dovolí přejmenovat
  spuštěný soubor). Žádný skript ani PowerShell se nespouští a nic se samo nerestartuje: po výměně stačí
  Docházku zavřít a spustit znovu. Předchozí verze zůstane vedle jako DochazkaMOP_predchozi.exe.
  (Dřívější verze používala skrytý PowerShell; Kaspersky ho vyhodnotil jako podezřelé chování.)
- Jinde (spuštění z Pythonu, složka bez práva zápisu, jiný systém) je v pruhu "Stáhnout" (otevře prohlížeč).
- Bez internetu, při chybě GitHubu nebo s nevyplněným repozitářem se appka chová úplně normálně.

Použití v dochazka_app.py (beze změny):
    from update_check import UpdateNotifier
    self.update_notifier = UpdateNotifier(self, UPDATE_REPO, APP_VERSION, before=self.notebook)
"""
import hashlib
import http.client
import json
import os
import re
import sys
import tempfile
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk

TIMEOUT_SECONDS = 6
MAX_POLLS = 60                                  # 60 x 0,5 s = nejvýš ~30 s čekání na odpověď GitHubu
ALLOWED_DOWNLOAD_PREFIX = "https://github.com/"  # stahujeme a otevíráme jen odkazy na GitHub
USER_AGENT = "DochazkaMOP-update-check"
SELF_UPDATE_ENABLED = True       # False = jen upozornění a ruční stažení (tlačítko Aktualizovat teď se nezobrazí)


# --------------------------------------------------------------------------- verze
def parse_version(text):
    """'v0.5.2' / '0.5.2' -> (0, 5, 2); když v textu žádné číslo není, vrátí None."""
    match = re.search(r"\d+(?:\.\d+)*", str(text or ""))
    return tuple(int(part) for part in match.group(0).split(".")) if match else None


def is_newer(latest, current):
    """True, jen když je `latest` skutečně vyšší než `current` (porovnává se po číslech: 0.5.10 > 0.5.9)."""
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return False
    size = max(len(a), len(b))
    return a + (0,) * (size - len(a)) > b + (0,) * (size - len(b))


def fetch_latest_release(repo, timeout=TIMEOUT_SECONDS):
    """Zeptá se GitHubu na poslední vydání. Vrátí slovník nebo vyvolá výjimku."""
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    download = data.get("html_url") or f"https://github.com/{repo}/releases/latest"
    digest = ""
    for asset in data.get("assets") or []:
        name = str(asset.get("name", "")).lower()
        if name.endswith(".exe") and asset.get("browser_download_url"):
            download = asset["browser_download_url"]          # přímé stažení .exe
            digest = str(asset.get("digest") or "")           # "sha256:..." (u starších souborů chybí)
            break
    return {"version": str(data.get("tag_name", "")), "download": download, "digest": digest}


# --------------------------------------------------------------------------- stažení a výměna souboru
class DownloadError(Exception):
    pass


class DownloadCancelled(Exception):
    pass


def _dir_writable(folder):
    try:
        with tempfile.NamedTemporaryFile(dir=str(folder), prefix=".zapis_", delete=True):
            pass
        return True
    except OSError:
        return False


def can_self_update():
    """Samoaktualizace jde jen ve Windows, v sestaveném .exe, které leží ve složce s právem zápisu."""
    if not SELF_UPDATE_ENABLED or sys.platform != "win32" or not getattr(sys, "frozen", False):
        return False
    return _dir_writable(Path(sys.executable).parent)


def download_asset(url, dest, progress=None, cancelled=None, timeout=20):
    """Stáhne soubor do `dest`. Vrátí {"size", "total", "sha256"}."""
    if not str(url).startswith(ALLOWED_DOWNLOAD_PREFIX):
        raise DownloadError("Neplatný odkaz ke stažení.")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    sha = hashlib.sha256()
    done = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, open(dest, "wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            while True:
                if cancelled and cancelled():
                    raise DownloadCancelled()
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                sha.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as exc:
        raise DownloadError(str(exc) or exc.__class__.__name__)
    return {"size": done, "total": total, "sha256": sha.hexdigest()}


def verify_download(path, result, expected_digest=""):
    """Zkontroluje úplnost, že jde o program pro Windows a (když ho GitHub uvádí) kontrolní součet."""
    if result["total"] and result["size"] != result["total"]:
        raise DownloadError("Soubor se stáhl neúplný.")
    with open(path, "rb") as handle:
        if handle.read(2) != b"MZ":
            raise DownloadError("Stažený soubor není program pro Windows.")
    match = re.fullmatch(r"sha256:([0-9a-fA-F]{64})", expected_digest or "")
    if match and match.group(1).lower() != result["sha256"]:
        raise DownloadError("Kontrolní součet staženého souboru nesouhlasí s tím, který uvádí GitHub.")


def swap_files(exe, new_path, backup):
    """Vymění program: běžící soubor se přejmenuje na `backup` a na jeho místo přijde `new_path`.
    Když se druhý krok nezdaří, původní soubor se vrátí zpátky."""
    exe, new_path, backup = Path(exe), Path(new_path), Path(backup)
    if backup.exists():
        backup.unlink()
    os.replace(str(exe), str(backup))            # přejmenovat běžící .exe Windows dovolí
    try:
        os.replace(str(new_path), str(exe))
    except OSError:
        os.replace(str(backup), str(exe))
        raise


def install_with_retry(exe, new_path, backup, attempts=6, delay=0.5):
    """Antivir může nový soubor na chvíli podržet, proto několik pokusů."""
    last = None
    for _ in range(attempts):
        try:
            swap_files(exe, new_path, backup)
            return
        except OSError as exc:
            last = exc
            time.sleep(delay)
    raise DownloadError(f"Soubor se nepodařilo nahradit: {last}")


# --------------------------------------------------------------------------- okno aplikace
class UpdateNotifier:
    def __init__(self, root, repo, current_version, before=None):
        self.root = root
        self.repo = (repo or "").strip()
        self.current = current_version
        self.before = before            # nad který widget se pruh vloží (např. notebook)
        self.banner = None
        self._busy = False
        # vyplněný repozitář ve tvaru uzivatel/nazev; zástupný text UZIVATEL = kontrola vypnutá
        self.enabled = bool(re.fullmatch(r"[\w.-]+/[\w.-]+", self.repo)) and "UZIVATEL" not in self.repo
        if self.enabled:
            root.after(1500, lambda: self.check(manual=False))      # až po otevření okna

    # ---- kontrola
    def check(self, manual=False):
        if self._busy:
            return
        if not self.enabled:
            if manual:
                messagebox.showinfo("Nová verze", "Kontrola nové verze není nastavena (konstanta UPDATE_REPO v kódu).")
            return
        self._busy = True
        state = {"done": False, "info": None}

        def worker():
            try:
                state["info"] = fetch_latest_release(self.repo)
            except Exception:
                state["info"] = None
            finally:
                state["done"] = True

        threading.Thread(target=worker, daemon=True).start()
        self._poll(state, manual, 0)

    def _poll(self, state, manual, polls):
        # Tkinter se nesmí volat z jiného vlákna, proto se výsledek jen vyzvedává odsud.
        if not state["done"] and polls < MAX_POLLS:
            self.root.after(500, lambda: self._poll(state, manual, polls + 1))
            return
        self._busy = False
        try:
            self._finish(state["info"], manual)
        except tk.TclError:
            pass

    def _finish(self, info, manual):
        if info is None:
            if manual:
                messagebox.showwarning(
                    "Nová verze",
                    "Nepodařilo se zjistit, jestli existuje novější verze.\nZkontrolujte připojení k internetu a zkuste to znovu.")
            return
        if is_newer(info["version"], self.current):
            self.show_banner(info)
            if manual:
                messagebox.showinfo("Nová verze", f"Je dostupná nová verze {self._clean(info['version'])}. Pokračujte tlačítkem v pruhu nahoře.")
        elif manual:
            messagebox.showinfo("Nová verze", f"Používáte nejnovější verzi ({self.current}).")

    # ---- pruh s upozorněním
    @staticmethod
    def _clean(version):
        return str(version).lstrip("vV")

    def show_banner(self, info):
        self.hide_banner()
        bg, fg = "#fff3cd", "#664d03"
        auto = can_self_update()
        banner = tk.Frame(self.root, bg=bg, highlightbackground="#ffe69c", highlightthickness=1)
        if auto:
            text = (f"Je dostupná nová verze {self._clean(info['version'])} (používáte {self.current}). "
                    "Tlačítko Aktualizovat teď novou verzi stáhne a nainstaluje. Poté Docházku zavřete a spusťte znovu, data zůstanou beze změny.")
        else:
            text = (f"Je dostupná nová verze {self._clean(info['version'])} (používáte {self.current}). "
                    "Po stažení Docházku zavřete a nahraďte jí starý soubor DochazkaMOP.exe.")
        # tlačítka se vkládají první, aby zůstala vidět i při užším okně
        ttk.Button(banner, text="Skrýt", command=self.hide_banner).pack(side="right", padx=(0, 12), pady=6)
        if auto:
            ttk.Button(banner, text="Aktualizovat teď", command=lambda: self.start_self_update(info)).pack(side="right", padx=6, pady=6)
            ttk.Button(banner, text="Stáhnout ručně", command=lambda: self.open_download(info)).pack(side="right", padx=6, pady=6)
        else:
            ttk.Button(banner, text="Stáhnout", command=lambda: self.open_download(info)).pack(side="right", padx=6, pady=6)
        tk.Label(banner, text=text, bg=bg, fg=fg, font=("Segoe UI", 10), anchor="w", justify="left",
                 wraplength=700).pack(side="left", padx=12, pady=8)
        options = {"fill": "x", "padx": 16, "pady": (0, 8)}
        if self.before is not None:
            options["before"] = self.before
        banner.pack(**options)
        self.banner = banner

    def hide_banner(self):
        if self.banner is not None:
            try:
                self.banner.destroy()
            except tk.TclError:
                pass
            self.banner = None

    def open_download(self, info):
        url = str(info.get("download", ""))
        if url.startswith(ALLOWED_DOWNLOAD_PREFIX):
            webbrowser.open(url)

    # ---- jednoklikové nahrazení programu
    def start_self_update(self, info):
        if not messagebox.askyesno(
                "Aktualizace",
                f"Nainstalovat verzi {self._clean(info['version'])}?\n\n"
                "Nová verze se stáhne a nahradí současný program. Potom Docházku stačí zavřít a spustit znovu. "
                "Data zůstanou beze změny a předchozí verze zůstane vedle jako DochazkaMOP_predchozi.exe."):
            return
        exe = Path(sys.executable)
        backup = exe.with_name(f"{exe.stem}_predchozi{exe.suffix}")
        new_path = exe.with_name(f"{exe.stem}.update{exe.suffix}")
        part = new_path.with_name(new_path.name + ".part")
        for leftover in (new_path, part):
            try:
                leftover.unlink()
            except OSError:
                pass

        state = {"done": False, "error": None, "cancelled": False, "bytes": 0, "total": 0}

        def progress(done, total):
            state["bytes"], state["total"] = done, total

        def worker():
            try:
                result = download_asset(info["download"], str(part), progress, lambda: state["cancelled"])
                verify_download(str(part), result, info.get("digest", ""))
                os.replace(str(part), str(new_path))
                install_with_retry(exe, new_path, backup)
            except DownloadCancelled:
                state["cancelled"] = True
            except Exception as exc:
                state["error"] = str(exc) or exc.__class__.__name__
            finally:
                state["done"] = True

        win = tk.Toplevel(self.root)
        win.title("Aktualizace")
        win.transient(self.root)
        win.resizable(False, False)
        label = tk.Label(win, text="Stahuji novou verzi…", padx=24, pady=14)
        label.pack()
        bar = ttk.Progressbar(win, length=360, mode="determinate", maximum=100)
        bar.pack(padx=24, pady=(0, 10))

        def cancel():
            state["cancelled"] = True

        ttk.Button(win, text="Zrušit", command=cancel).pack(pady=(0, 14))
        win.protocol("WM_DELETE_WINDOW", cancel)
        try:
            win.grab_set()
        except tk.TclError:
            pass

        threading.Thread(target=worker, daemon=True).start()
        self._poll_download(win, label, bar, state, new_path, part, info)

    def _poll_download(self, win, label, bar, state, new_path, part, info):
        if not state["done"]:
            total = state["total"]
            if total:
                bar.configure(value=state["bytes"] * 100.0 / total)
                label.configure(text=f"Stahuji novou verzi… {state['bytes'] // 1024 // 1024} / {max(1, total // 1024 // 1024)} MB")
            self.root.after(150, lambda: self._poll_download(win, label, bar, state, new_path, part, info))
            return
        try:
            win.destroy()
        except tk.TclError:
            pass
        if state["cancelled"] or state["error"]:
            for leftover in (new_path, part):
                try:
                    Path(leftover).unlink()
                except OSError:
                    pass
            if state["error"]:
                exe = Path(sys.executable)
                if exe.exists():
                    tail = "Program zůstává beze změny. Novou verzi můžete stáhnout ručně tlačítkem Stáhnout ručně."
                else:
                    tail = f"Původní program najdete pod názvem {exe.stem}_predchozi{exe.suffix}."
                messagebox.showerror("Aktualizace", f"Aktualizace se nezdařila:\n{state['error']}\n\n{tail}")
            return
        self._after_install(info)

    def _after_install(self, info):
        self.hide_banner()
        if messagebox.askyesno(
                "Aktualizace nainstalována",
                f"Verze {self._clean(info['version'])} je nainstalována.\n\n"
                "Aby se začala používat, Docházku zavřete a spusťte znovu (ikonou na ploše nebo souborem DochazkaMOP.exe).\n\n"
                "Zavřít program teď?"):
            quit_app = getattr(self.root, "on_close", None) or self.root.destroy
            quit_app()