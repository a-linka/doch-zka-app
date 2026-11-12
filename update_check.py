"""Kontrola nové verze Docházky MOP na GitHubu.

Jen UPOZORNÍ: nic se samo nestahuje ani nepřepisuje. Bez internetu, při chybě GitHubu
nebo při nevyplněném repozitáři se appka chová úplně normálně (kontrola tiše skončí).

Použití v dochazka_app.py:
    from update_check import UpdateNotifier
    ...
    self.update_notifier = UpdateNotifier(self, UPDATE_REPO, APP_VERSION, before=self.notebook)
"""
import json
import re
import threading
import tkinter as tk
import urllib.request
import webbrowser
from tkinter import messagebox, ttk

TIMEOUT_SECONDS = 6
MAX_POLLS = 60          # 60 x 0,5 s = nejvýš ~30 s čekání na odpověď GitHubu


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
    """Zeptá se GitHubu na poslední vydání. Vrátí {"version", "download"} nebo vyvolá výjimku."""
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "DochazkaMOP-update-check"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    download = data.get("html_url") or f"https://github.com/{repo}/releases/latest"
    for asset in data.get("assets") or []:
        name = str(asset.get("name", "")).lower()
        if name.endswith(".exe") and asset.get("browser_download_url"):
            download = asset["browser_download_url"]      # přímé stažení .exe
            break
    return {"version": str(data.get("tag_name", "")), "download": download}


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
                messagebox.showinfo("Nová verze", f"Je dostupná nová verze {self._clean(info['version'])}. Odkaz ke stažení je v pruhu nahoře.")
        elif manual:
            messagebox.showinfo("Nová verze", f"Používáte nejnovější verzi ({self.current}).")

    # ---- pruh s upozorněním
    @staticmethod
    def _clean(version):
        return str(version).lstrip("vV")

    def show_banner(self, info):
        self.hide_banner()
        bg, fg = "#fff3cd", "#664d03"
        banner = tk.Frame(self.root, bg=bg, highlightbackground="#ffe69c", highlightthickness=1)
        text = (f"Je dostupná nová verze {self._clean(info['version'])} (používáte {self.current}). "
                "Po stažení Docházku zavřete a nahraďte jí starý soubor DochazkaMOP.exe.")
        tk.Label(banner, text=text, bg=bg, fg=fg, font=("Segoe UI", 10), anchor="w",
                 justify="left", wraplength=880).pack(side="left", padx=12, pady=8)
        ttk.Button(banner, text="Skrýt", command=self.hide_banner).pack(side="right", padx=(0, 12), pady=6)
        ttk.Button(banner, text="Stáhnout", command=lambda: self.open_download(info)).pack(side="right", padx=6, pady=6)
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
        if url.startswith("https://github.com/"):        # otevíráme jen odkazy na GitHub
            webbrowser.open(url)
