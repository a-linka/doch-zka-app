import calendar
import json
import os
import sqlite3
import tkinter as tk
import ttkbootstrap as tb
from datetime import date, datetime, time, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import ctypes
import hashlib as _hashlib
from update_check import UpdateNotifier

_original_md5 = _hashlib.md5
def _reportlab_compatible_md5(*args, **kwargs):
    kwargs.pop("usedforsecurity", None)
    return _original_md5(*args, **kwargs)
_hashlib.md5 = _reportlab_compatible_md5

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

APP_NAME = "Docházka MOP"
APP_VERSION = "0.5.5"
UPDATE_REPO = "a-linka/doch-zka-app"

DEFAULT_YEAR = 2026

CODES = {
    "": "Práce / čas",
    "D": "Dovolená",
    "D/2": "Dovolená – půlden",
    "N": "Nemoc",
    "O": "Ošetřování člena rodiny",
    "L": "Celodenní lékař",
    "P": "Paragraf",
    "VP": "Volno placené",
    "VNe": "Volno neplacené",
}
CODE_BY_LABEL = {label: code for code, label in CODES.items()}
BLANK = {"code": "", "od": "", "do": "", "pres_od": "", "pres_do": ""}
BACKUP_DIR_NAME = "Dochazka_MOP_zalohy"
BACKUP_KEEP = 14
MONTHS = [
    "Leden", "Únor", "Březen", "Duben", "Květen", "Červen",
    "Červenec", "Srpen", "Září", "Říjen", "Listopad", "Prosinec"
]
DAYS_CZ = ["Po", "Út", "St", "Čt", "Pá", "So", "Ne"]

def easter_sunday(year):
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19*a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2*e + 2*i - h - k) % 7
    m = (a + 11*h + 22*l) // 451
    month = (h + l - 7*m + 114) // 31
    day = ((h + l - 7*m + 114) % 31) + 1
    return date(year, month, day)

def czech_holidays(year):
    easter = easter_sunday(year)
    good_friday = easter - timedelta(days=2)
    easter_monday = easter + timedelta(days=1)
    return {
        date(year, 1, 1): "Nový rok / Den obnovy samostatného českého státu",
        good_friday: "Velký pátek",
        easter_monday: "Velikonoční pondělí",
        date(year, 5, 1): "Svátek práce",
        date(year, 5, 8): "Den vítězství",
        date(year, 7, 5): "Den slovanských věrozvěstů Cyrila a Metoděje",
        date(year, 7, 6): "Den upálení mistra Jana Husa",
        date(year, 9, 28): "Den české státnosti",
        date(year, 10, 28): "Den vzniku samostatného československého státu",
        date(year, 11, 17): "Den boje za svobodu a demokracii a Mezinárodní den studentstva",
        date(year, 12, 24): "Štědrý den",
        date(year, 12, 25): "1. svátek vánoční",
        date(year, 12, 26): "2. svátek vánoční",
    }

def parse_time(value):
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%H:%M", "%H.%M"):
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            pass
    return None

def normalize_time(value):
    s = (value or "").strip().replace(",", ":").replace(".", ":").replace(" ", "")
    if not s:
        return ""
    if ":" in s:
        h, _, m = s.partition(":")
        if not (h.isdigit() and m.isdigit() and 1 <= len(h) <= 2 and len(m) == 2):
            return value
    elif s.isdigit() and len(s) <= 4:
        if len(s) <= 2:
            h, m = s, "0"
        elif len(s) == 3:
            h, m = s[0], s[1:]
        else:
            h, m = s[:2], s[2:]
    else:
        return value
    h, m = int(h), int(m)
    if h > 23 or m > 59:
        return value
    return f"{h:02d}:{m:02d}"

def validate_times(od_s, do_s, po_s, pd_s):
  
    od_s, do_s, po_s, pd_s = [(s or "").strip() for s in (od_s, do_s, po_s, pd_s)]
    
    if not any((od_s, do_s, po_s, pd_s)):
        return True, None, set()

    invalid = set()
    od = parse_time(od_s) if od_s else None
    do = parse_time(do_s) if do_s else None
    po = parse_time(po_s) if po_s else None
    pd = parse_time(pd_s) if pd_s else None

    if od_s and not od:
        invalid.add("od")
    if do_s and not do:
        invalid.add("do")
    if po_s and not po:
        invalid.add("po")
    if pd_s and not pd:
        invalid.add("pd")

    if invalid:
        return False, "Neplatný formát času.", invalid

    if od and do and do <= od:
        invalid.update({"od", "do"})
        return False, "Čas DO musí být později než OD.", invalid

    if bool(po_s) != bool(pd_s):
        if not po_s: invalid.add("po")
        if not pd_s: invalid.add("pd")
        return False, "Vyplň obě pole pauzy (OD i DO), nebo žádné.", invalid

    if po and pd:
        if pd <= po:
            invalid.update({"po", "pd"})
            return False, "Konec pauzy musí být později než začátek.", invalid
        if od and do and not (od <= po and pd <= do):
            invalid.update({"po", "pd"})
            return False, "Pauza musí být v rozmezí OD–DO.", invalid

    return True, None, set()

def hours_between(od, do, pres_od, pres_do):
    a, b, c, d = map(parse_time, (od, do, pres_od, pres_do))
    if not a or not b:
        return 0.0
    mins = (b.hour * 60 + b.minute) - (a.hour * 60 + a.minute)
    if c and d:
        mins -= (d.hour * 60 + d.minute) - (c.hour * 60 + c.minute)
    return max(0.0, mins / 60.0)

class Database:
    def __init__(self, path):
        self.path = path
        self.con = sqlite3.connect(path)
        self.con.row_factory = sqlite3.Row
        self.init_db()

    def init_db(self):
        self.con.executescript('''
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS attendance(
            year INTEGER NOT NULL,
            month INTEGER NOT NULL,
            day INTEGER NOT NULL,
            employee INTEGER NOT NULL,
            code TEXT DEFAULT '',
            od TEXT DEFAULT '',
            do TEXT DEFAULT '',
            pres_od TEXT DEFAULT '',
            pres_do TEXT DEFAULT '',
            PRIMARY KEY(year,month,day,employee)
        );
        CREATE TABLE IF NOT EXISTS locked_months(
            year INTEGER NOT NULL,
            month INTEGER NOT NULL,
            PRIMARY KEY(year,month)
        );
        ''')
        defaults = {
            "year": str(DEFAULT_YEAR),
            "store": "",
            "center": "",
            "daily_hours": "8",
            "employee1": "",
            "employee1_personal": "",
            "employee2": "",
            "employee2_personal": "",
            "employee3": "",
            "employee3_personal": "",
            "allow_weekend_codes": "0",
        }
        for k, v in defaults.items():
            self.con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
        # Prázdný řádek nenese žádnou informaci (chybějící den = prázdný den).
        self.con.execute("DELETE FROM attendance WHERE code='' AND od='' AND do='' AND pres_od='' AND pres_do=''")
        self.con.commit()

    def get_settings(self):
        return {r["key"]: r["value"] for r in self.con.execute("SELECT key,value FROM settings")}

    def set_settings(self, data):
        with self.con:
            for k, v in data.items():
                self.con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (k, str(v)))

    def get_day(self, year, month, day, employee):
        r = self.con.execute(
            "SELECT * FROM attendance WHERE year=? AND month=? AND day=? AND employee=?",
            (year, month, day, employee)).fetchone()
        return dict(r) if r else dict(BLANK)

    def get_month(self, year, month):
        """Celý měsíc všech pracovníků jedním dotazem: {(den, pracovník): záznam}."""
        rows = self.con.execute("SELECT * FROM attendance WHERE year=? AND month=?", (year, month))
        return {(r["day"], r["employee"]): dict(r) for r in rows}

    def get_employee_month(self, year, month, employee):
        """Celý měsíc jednoho pracovníka jedním dotazem: {den: záznam}."""
        rows = self.con.execute(
            "SELECT * FROM attendance WHERE year=? AND month=? AND employee=?", (year, month, employee))
        return {r["day"]: dict(r) for r in rows}

    def save_day(self, year, month, day, employee, values):
        with self.con:
            if not any((values["code"], values["od"], values["do"], values["pres_od"], values["pres_do"])):
                self.con.execute(
                    "DELETE FROM attendance WHERE year=? AND month=? AND day=? AND employee=?",
                    (year, month, day, employee))
                return
            self.con.execute('''
            INSERT INTO attendance(year,month,day,employee,code,od,do,pres_od,pres_do)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(year,month,day,employee) DO UPDATE SET
              code=excluded.code, od=excluded.od, do=excluded.do,
              pres_od=excluded.pres_od, pres_do=excluded.pres_do
            ''', (year,month,day,employee,values["code"],values["od"],values["do"],values["pres_od"],values["pres_do"]))

    # ---- pracovníci: ochrana před zděděním cizí docházky
    def count_records(self, employee):
        row = self.con.execute(
            "SELECT COUNT(*) FROM attendance WHERE employee=? AND "
            "(code<>'' OR od<>'' OR do<>'' OR pres_od<>'' OR pres_do<>'')", (employee,)).fetchone()
        return row[0]

    def archive_employee(self, employee):
        with self.con:
            row = self.con.execute("SELECT COALESCE(MAX(employee), 0) FROM attendance").fetchone()
            new_id = max(100, row[0] + 1)
            self.con.execute("UPDATE attendance SET employee=? WHERE employee=?", (new_id, employee))
        return new_id

    # ---- uzavření měsíce
    def is_locked(self, year, month):
        return self.con.execute(
            "SELECT 1 FROM locked_months WHERE year=? AND month=?", (year, month)).fetchone() is not None

    def set_locked(self, year, month, locked):
        with self.con:
            if locked:
                self.con.execute("INSERT OR IGNORE INTO locked_months(year,month) VALUES(?,?)", (year, month))
            else:
                self.con.execute("DELETE FROM locked_months WHERE year=? AND month=?", (year, month))

    # ---- zálohy
    @property
    def backup_dir(self):
        return Path(self.path).parent / BACKUP_DIR_NAME

    def backup_to(self, path):
        dest = sqlite3.connect(path)
        try:
            self.con.backup(dest)
        finally:
            dest.close()

    def backup_named(self, tag):
        self.backup_dir.mkdir(exist_ok=True)
        target = self.backup_dir / f"Dochazka_MOP_{tag}_{datetime.now():%Y-%m-%d_%H%M%S}.sqlite3"
        self.backup_to(str(target))
        return target

    def backup_rotating(self, keep=BACKUP_KEEP):
        """Jedna záloha za den (poslední stav dne), uchovává se posledních `keep` dní."""
        self.backup_dir.mkdir(exist_ok=True)
        target = self.backup_dir / f"Dochazka_MOP_{date.today():%Y-%m-%d}.sqlite3"
        self.backup_to(str(target))
        old = sorted(self.backup_dir.glob("Dochazka_MOP_????-??-??.sqlite3"))
        for f in old[:-keep]:
            try:
                f.unlink()
            except OSError:
                pass
        return target

    def close(self):
        self.con.close()

class AttendanceApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {APP_VERSION}")
        screen_width = self.winfo_screenwidth()

        screen_height = self.winfo_screenheight()
        app_width = int(screen_width * 0.8)
        app_height = int(screen_height * 0.8)
        
        x_position = int((screen_width / 2) - (app_width / 2))
        y_position = int((screen_height / 2) - (app_height / 2))
        
        self.geometry(f"{app_width}x{app_height}+{x_position}+{y_position}")
        
        self.minsize(1180, 750)
        self.db = Database(str(Path.home() / "Dochazka_MOP.sqlite3"))
        self.settings = self.db.get_settings()
        self.year = int(self.settings.get("year", DEFAULT_YEAR))
        self.holidays = czech_holidays(self.year)
        self.month_var = tk.IntVar(value=date.today().month if date.today().year == self.year else 1)
        self.employee_vars = [tk.StringVar(value=self.settings.get(f"employee{i}", "")) for i in range(1,4)]
        self.employee_personal_vars = [tk.StringVar(value=self.settings.get(f"employee{i}_personal", "")) for i in range(1,4)]
        self._vars = {}
        self._updating = False
        self._active_popup = None
        self._pending = {}          # odložená uložení časů: {(měsíc, den, pracovník): (timer, funkce)}
        self._summary_job = None    # naplánované přepočítání rekapitulace
        self._loaded_month = None   # který měsíc je právě vykreslený
        self._build_style()
        self._build_menu()
        self._build_header()
        self._build_month_tabs()
        self._build_summary()
        self._load_month(rebuild_summary=False)
        self.notebook.select(self.month_var.get() - 1)
        self.update_notifier = UpdateNotifier(self, UPDATE_REPO, APP_VERSION, before=self.notebook)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind_all("<Button-1>", self._maybe_close_popup_on_outside_click, add="+")
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):     # Button-4/5 = Linux
            self.bind_all(seq, self._on_mousewheel, add="+")

    def _on_mousewheel(self, event):
        try:
            w = self.winfo_containing(event.x_root, event.y_root)
        except Exception:
            return
        while w is not None:
            if getattr(w, "_wheel_target", False):
                num = getattr(event, "num", None)
                if num == 4:
                    step = -1
                elif num == 5:
                    step = 1
                else:
                    step = -1 if event.delta > 0 else 1
                try:
                    if event.state & 0x0001:
                        w.xview_scroll(step * 3, "units")
                    else:
                        w.yview_scroll(step * 3, "units")
                except tk.TclError:
                    pass
                return
            w = getattr(w, "master", None)

    def _build_style(self):
        self.style = tb.Style(theme="flatly")
        style = self.style
        c = style.colors

        self.c_bg = c.light
        self.c_surface = c.bg
        self.c_fg = c.fg
        self.c_muted = c.secondary
        self.c_accent = c.primary
        self.c_danger = c.danger
        self.c_danger_bg = "#fdecea"
        self.c_row_alt = "#f4f7fa"            # každý druhý pracovní den (střídání řádků)
        self.c_weekend_bg = "#e6eaee"         # víkend: výraznější šedá než střídání řádků
        self.c_weekend_fg = c.secondary
        # podbarvení dne podle zadaného kódu absence
        self.c_code_bg = {
            "D": "#d7f0dd", "D/2": "#e8f6ec",   # dovolená – zelená
            "N": "#ffe5cc",                      # nemoc – oranžová
            "O": "#fff5c2",                      # ošetřování člena rodiny – žlutá
            "L": "#e8e0f7", "P": "#e8e0f7",      # lékař, paragraf – fialová
            "VP": "#dbeafe", "VNe": "#d5f0f0",   # volno placené – modrá, neplacené – tyrkysová
        }
        self.c_holiday_bg = "#fdecea"
        self.c_holiday_fg = c.danger
        self.c_border = c.border

        self.configure(bg=self.c_bg)
        cfg = lambda name, **kw: ttk.Style.configure(style, name, **kw)
        cfg(".", background=self.c_bg, foreground=self.c_fg, font=("Segoe UI", 10))

    def _build_menu(self):
        menubar = tk.Menu(self)
        filem = tk.Menu(menubar, tearoff=False)
        filem.add_command(label="Nastavení", command=self.open_settings)
        filem.add_separator()
        filem.add_command(label="Export aktuálního měsíce do PDF", command=self.export_pdf)
        filem.add_command(label="Export celého roku do PDF", command=self.export_year_pdf)
        filem.add_separator()
        filem.add_command(label="Uzavřít / odemknout aktuální měsíc", command=self.toggle_month_lock)
        filem.add_command(label="Zálohovat databázi do souboru…", command=self.backup_database)
        filem.add_separator()
        filem.add_command(label="Konec", command=self.on_close)
        menubar.add_cascade(label="Soubor", menu=filem)
        helpm = tk.Menu(menubar, tearoff=False)
        helpm.add_command(label="Kódy", command=self.show_codes)
        helpm.add_command(label="Zkontrolovat novou verzi", command=lambda: self.update_notifier.check(manual=True))
        menubar.add_cascade(label="Nápověda", menu=helpm)
        self.config(menu=menubar)

    def _build_header(self):
        top = tk.Frame(self, bg=self.c_bg)
        top.pack(fill="x")
        tk.Label(top, text="Docházka MOP", font=("Segoe UI", 22, "bold"), bg=self.c_bg, fg=self.c_accent).pack(side="left", padx=(20, 0), pady=16)
        self.header_info = tk.Label(top, text="", font=("Segoe UI", 11), bg=self.c_bg, fg=self.c_muted)
        self.header_info.pack(side="left", padx=24, pady=(24, 0))
        ttk.Button(top, text="⚙ Nastavení", command=self.open_settings).pack(side="right", padx=(0, 20), pady=16)
        ttk.Button(top, text="📄 PDF Export", command=self.export_pdf).pack(side="right", padx=(0,12), pady=16)
        self.lock_btn = ttk.Button(top, text="🔒 Uzavřít měsíc", command=self.toggle_month_lock)
        self.lock_btn.pack(side="right", padx=(0,12), pady=16)

    def _build_month_tabs(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=16)
        self.month_frames = []
        for i, name in enumerate(MONTHS, 1):
            frame = ttk.Frame(self.notebook)
            self.notebook.add(frame, text=name)
            self.month_frames.append(frame)
        self.notebook.bind("<<NotebookTabChanged>>", self._tab_changed)

    def _active_employees(self):
        result = []
        for idx, var in enumerate(self.employee_vars, 1):
            name = var.get().strip()
            if not name or name.lower() == f"pracovník {idx}".lower():
                continue
            result.append((idx, name))
        return result

    def _build_summary(self):
        outer = tk.Frame(self, bg=self.c_bg)
        outer.pack(fill="x", padx=16, pady=12)

        tk.Label(outer, text="Měsíční rekapitulace", font=("Segoe UI", 11, "bold"), bg=self.c_bg, fg=self.c_accent).pack(anchor="w", pady=(0, 6))

        summary_frame = tk.Frame(outer, bg=self.c_bg)
        summary_frame.pack(fill="x")
        canvas = tk.Canvas(summary_frame, height=190, highlightthickness=0, bg=self.c_bg)
        canvas._wheel_target = True
        vs = ttk.Scrollbar(summary_frame, orient="vertical", command=canvas.yview)
        self.summary_container = tk.Frame(canvas, bg=self.c_bg)
        self._summary_window = canvas.create_window((0, 0), window=self.summary_container, anchor="nw")

        self.summary_container.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(self._summary_window, width=e.width))
        canvas.configure(yscrollcommand=vs.set)
        canvas.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")

        self.summary_labels = []
        self.summary_vars = []
        self._rebuild_summary_widgets()

    def _rebuild_summary_widgets(self):
        for child in self.summary_container.winfo_children():
            child.destroy()
        self.summary_labels = []
        self.summary_vars = []
        active = self._active_employees()
        if not active:
            tk.Label(self.summary_container, text="V Nastavení přidejte alespoň jednoho pracovníka.", bg=self.c_bg, fg=self.c_fg).pack(anchor="w")
            return

        summary_rows = [
            "Odpracováno",
            "Odpracované svátky",
            "Odpracované soboty",
            "Neodpracováno",
            "Dovolená",
            "Nemoc",
            "Ošetřování člena rodiny",
            "Celodenní lékař",
            "Paragraf",
            "Volno placené",
            "Volno neplacené",
        ]

        for emp, name in active:
            bg_frame = tk.Frame(self.summary_container, bg=self.c_surface, bd=1, relief="flat", highlightbackground=self.c_border, highlightthickness=1)
            bg_frame.pack(side="left", fill="both", expand=True, padx=8)
            
            inner = tk.Frame(bg_frame, bg=self.c_surface)
            inner.pack(fill="both", expand=True, padx=12, pady=12)

            personal = self.settings.get(f"employee{emp}_personal", "").strip()
            tk.Label(inner, text=name, font=("Segoe UI", 11, "bold"), bg=self.c_surface, fg=self.c_accent).grid(row=0, column=0, sticky="w", pady=(0, 2))
            tk.Label(inner, text=f"Osobní číslo: {personal or '—'}", font=("Segoe UI", 10), bg=self.c_surface, fg=self.c_muted).grid(row=1, column=0, sticky="w", pady=(0, 10))
            
            tk.Label(inner, text="Dny", font=("Segoe UI", 9, "bold"), bg=self.c_surface, fg=self.c_fg, width=9, anchor="center").grid(row=1, column=1, padx=4, pady=(0, 10))
            tk.Label(inner, text="Hodiny", font=("Segoe UI", 9, "bold"), bg=self.c_surface, fg=self.c_fg, width=10, anchor="center").grid(row=1, column=2, padx=4, pady=(0, 10))
            
            vars_for_emp = {}
            for r, label in enumerate(summary_rows, start=2):
                tk.Label(inner, text=label + ":", font=("Segoe UI", 10), bg=self.c_surface, fg=self.c_fg).grid(row=r, column=0, sticky="w", pady=1)
                day_var = tk.StringVar(value="—")
                hour_var = tk.StringVar(value="—")
                tk.Label(inner, textvariable=day_var, width=9, anchor="e", font=("Segoe UI", 10), bg=self.c_surface, fg=self.c_fg).grid(row=r, column=1, padx=4)
                tk.Label(inner, textvariable=hour_var, width=10, anchor="e", font=("Segoe UI", 10), bg=self.c_surface, fg=self.c_fg).grid(row=r, column=2, padx=4)
                vars_for_emp[label] = (day_var, hour_var)
                
            self.summary_labels.append(name)
            self.summary_vars.append(vars_for_emp)

    def _tab_changed(self, _=None):
        self._flush_pending()
        month = self.notebook.index(self.notebook.select()) + 1
        self.month_var.set(month)
        if month == self._loaded_month:
            return
        self._load_month(rebuild_summary=False)

    def _render_table(self, month):
        self._flush_pending()
        frame = self.month_frames[month-1]
        for child in frame.winfo_children(): child.destroy()

        locked = self._month_locked(month)
        month_data = self.db.get_month(self.year, month)   # celý měsíc jedním dotazem

        canvas = tk.Canvas(frame, highlightthickness=0, bg=self.c_surface, yscrollincrement=28)
        canvas._wheel_target = True
        vs = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        hs = ttk.Scrollbar(frame, orient="horizontal", command=canvas.xview)
        inner = tk.Frame(canvas, bg=self.c_surface, padx=10, pady=10)
        
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0,0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        canvas.grid(row=0,column=0,sticky="nsew")
        vs.grid(row=0,column=1,sticky="ns")
        hs.grid(row=1,column=0,sticky="ew")
        frame.rowconfigure(0,weight=1)
        frame.columnconfigure(0,weight=1)
        
        headers = ["Datum","Den","SV","Kód","OD","DO","Pauza OD","Pauza DO","Hod"]
        self._vars[month] = {}
        active = self._active_employees()

        def paint(ent, bgc, fgc):
            ent.configure(bg=bgc, fg=fgc, disabledbackground=bgc, disabledforeground=fgc)

        header_pairs = []     # (skutečný štítek v tabulce, vzhled jeho kopie v pevné hlavičce)
        for visual_idx, (emp_id, name) in enumerate(active):
            base = visual_idx*9
            personal = self.settings.get(f"employee{emp_id}_personal", "").strip()
            header_text = f"{name}  •  Osobní číslo: {personal or '—'}"
            title_font = ("Segoe UI", 12, "bold")
            title_lbl = tk.Label(inner, text=header_text, font=title_font, bg=self.c_surface, fg=self.c_accent)
            title_lbl.grid(row=0, column=base, columnspan=9, pady=(10, 8), sticky="w")
            header_pairs.append((title_lbl, dict(text=header_text, font=title_font, bg=self.c_surface, fg=self.c_accent, anchor="w")))
            for j,h in enumerate(headers): 
                head_font = ("Segoe UI", 9, "bold")
                head_lbl = tk.Label(inner, text=h, font=head_font, bg=self.c_bg, fg=self.c_muted, width=10, pady=4)
                head_lbl.grid(row=1, column=base+j, padx=1, pady=1, sticky="nsew")
                header_pairs.append((head_lbl, dict(text=h, font=head_font, bg=self.c_bg, fg=self.c_muted, anchor="center")))
        self._make_sticky_header(canvas, inner, header_pairs, hs)
                
        days = calendar.monthrange(self.year,month)[1]
        for d in range(1,days+1):
            dt = date(self.year, month, d)
            holiday = dt in self.holidays
            weekend = dt.weekday() >= 5
            
            for visual_idx, (emp_id, name) in enumerate(active):
                base = visual_idx*9
                rec = month_data.get((d, emp_id), BLANK)
                rec_vals = {k: (rec.get(k) or "") for k in BLANK}
                row = d+1
                
                if weekend or holiday:
                    bg = self.c_weekend_bg
                else:
                    bg = self.c_row_alt if d % 2 == 0 else self.c_surface
                rpad = (8, 1) if (dt.weekday() == 0 and d > 1) else 1     # mezera před každým pondělkem = začátek týdne
                fg = self.c_holiday_fg if holiday else (self.c_weekend_fg if weekend else self.c_fg)
                
                date_lbl = tk.Label(inner, text=dt.strftime("%d.%m."), width=10, bg=bg, fg=fg, font=("Segoe UI", 10))
                date_lbl.grid(row=row, column=base, sticky="nsew", padx=1, pady=rpad)
                day_lbl = tk.Label(inner, text=DAYS_CZ[dt.weekday()], width=6, bg=bg, fg=fg, font=("Segoe UI", 10))
                day_lbl.grid(row=row, column=base+1, sticky="nsew", padx=1, pady=rpad)
                tk.Label(inner, text="SV" if holiday else "", width=5, bg=self.c_holiday_bg if holiday else bg, fg=self.c_holiday_fg if holiday else fg, font=("Segoe UI", 10, "bold")).grid(row=row, column=base+2, sticky="nsew", padx=1, pady=rpad)
                
                code = tk.StringVar(value=rec_vals["code"])
                od = tk.StringVar(value=rec_vals["od"])
                do = tk.StringVar(value=rec_vals["do"])
                po = tk.StringVar(value=rec_vals["pres_od"])
                pd = tk.StringVar(value=rec_vals["pres_do"])
                hour = tk.StringVar(value="")
                
                vals = (code,od,do,po,pd,hour)
                self._vars[month][(d,emp_id)] = vals
                key = (month, d, emp_id)
                
                code_cell = tk.Label(inner, textvariable=code, width=5, bg=bg, fg=self.c_fg,
                                      font=("Segoe UI", 10, "bold"), relief="solid", bd=1,
                                      highlightthickness=0, cursor="arrow" if locked else "hand2", takefocus=1)
                code_cell.grid(row=row, column=base+3, sticky="nsew", padx=1, pady=rpad)

                entries = []
                for col, var in zip(range(base+4, base+8), (od, do, po, pd)):
                    ent = tk.Entry(inner, textvariable=var, width=8, justify="center",
                                    relief="solid", bd=1, highlightthickness=0,
                                    bg=self.c_surface, fg=self.c_fg,
                                    disabledbackground=self.c_surface, disabledforeground=self.c_fg,
                                    state="disabled" if locked else "normal", font=("Segoe UI", 10))
                    ent.grid(row=row, column=col, sticky="nsew", padx=1, pady=rpad)
                    entries.append(ent)
                    
                hour_lbl = tk.Label(inner, textvariable=hour, width=7, anchor="center", bg=bg, fg=self.c_accent, font=("Segoe UI", 10, "bold"))
                hour_lbl.grid(row=row, column=base+8, sticky="nsew", padx=1, pady=rpad)
                rowcells = [date_lbl, day_lbl, code_cell, hour_lbl]     # tyhle se podbarvují podle kódu absence
                
                def update(*_, day=d, emp=emp_id, c=code, a=od, b=do, pa=po, pb=pd, hv=hour, ents=entries,
                           cells=rowcells, base_bg=bg, save=True, refresh=True, compare=None):
                    if self._updating: return
                    self._updating = True
                    invalid_fields = set()
                    try:
                        has_any_time = any(v.get().strip() for v in (a, b, pa, pb))
                        if has_any_time and c.get() and c.get() != "D/2":
                            c.set("")

                        if c.get() and c.get() != "D/2":
                            for sv in (a,b,pa,pb): sv.set("")
                            hv.set("0.00")
                        else:
                            ok, _msg, invalid_fields = validate_times(a.get(), b.get(), pa.get(), pb.get())
                            hv.set(f"{hours_between(a.get(),b.get(),pa.get(),pb.get()):.2f}" if ok else "!")

                        values = dict(code=c.get(), od=a.get(), do=b.get(), pres_od=pa.get(), pres_do=pb.get())
                        # Uložení je PŘED úpravou vzhledu: i kdyby políčko mezitím zaniklo, data se neztratí.
                        # Při prvním vykreslení se ukládá jen tehdy, když se hodnoty po úpravě liší od databáze.
                        if save or (compare is not None and values != compare):
                            self.db.save_day(self.year, month, day, emp, values)
                    finally:
                        self._updating = False
                    try:
                        for fname, ent in zip(("od", "do", "po", "pd"), ents):
                            if fname in invalid_fields:
                                paint(ent, self.c_danger_bg, self.c_danger)
                            else:
                                paint(ent, self.c_surface, self.c_fg)
                        row_bg = self.c_code_bg.get(c.get(), base_bg)
                        for cell in cells:
                            cell.configure(bg=row_bg)
                    except tk.TclError:
                        pass
                    if refresh:
                        self._schedule_summary()


                def schedule_update(*_a, k=key, upd=update):
                    if self._updating:      
                        return
                    self._cancel_pending(k)
                    def fire(k=k, upd=upd):
                        self._pending.pop(k, None)
                        upd()
                    self._pending[k] = (self.after(600, fire), fire)

                code.trace_add("write", update)
                for sv in (od,do,po,pd):
                    sv.trace_add("write", schedule_update)
                update(save=False, refresh=False, compare=rec_vals)

                def choose_code(chosen_code, c_var=code, update_fn=update, is_hol=holiday, is_week=weekend, time_vars=(od, do, po, pd)):
                    if chosen_code and chosen_code != "D/2" and any(v.get().strip() for v in time_vars):
                        messagebox.showinfo("Kód", "Tento den má vyplněné časy. Nejprve časy smažte, potom lze zadat kód absence.")
                        return
                    if chosen_code and (is_hol or is_week) and not self._weekend_codes_allowed():
                        self._updating = True
                        try: c_var.set("")
                        finally: self._updating = False
                        update_fn()
                        messagebox.showwarning("Kód", "Kód absence se do soboty, neděle ani svátku nezadává.\nZadávání lze povolit v Nastavení.")
                        return
                    self._updating = True
                    try: c_var.set(chosen_code)
                    finally: self._updating = False
                    update_fn()

                if locked:
                    code_cell.bind("<Button-1>", lambda e: self._locked_notice())
                else:
                    code_cell.bind("<Button-1>", lambda e, cc=code_cell, cv=code, fn=choose_code: self.open_code_popup(cc, cv, fn))
                    code_cell.bind("<Return>", lambda e, cc=code_cell, cv=code, fn=choose_code: self.open_code_popup(cc, cv, fn))
                    code_cell.bind("<space>", lambda e, cc=code_cell, cv=code, fn=choose_code: self.open_code_popup(cc, cv, fn))

                for ent, var in zip(entries, (od, do, po, pd)):
                    ent.bind("<Tab>", lambda e: (e.widget.tk_focusNext().focus_set(), "break")[1])
                    ent.bind("<Return>", lambda e: (e.widget.tk_focusNext().focus_set(), "break")[1])
                    ent.bind("<FocusOut>", lambda e, v=var, k=key: self._on_time_leave(v, k), add="+")
                
                self._add_context_menu(code_cell, month, d, emp_id)
                for ent in entries: self._add_context_menu(ent, month, d, emp_id)
                for w in [code_cell]+entries: w.bind("<Control-c>", lambda e, m=month, day=d, emp=emp_id: self.copy_day(m,day,emp))
        
        self._update_summary(month)

    def _make_sticky_header(self, canvas, inner, header_pairs, hs):

        backing = tk.Frame(canvas, bg=self.c_surface)
        overlays = [(real, tk.Label(canvas, **spec)) for real, spec in header_pairs]

        def sync(_event=None):
            try:
                cx = int(canvas.canvasx(0))
                bottom = 0
                for real, over in overlays:
                    x, y, w, h = real.winfo_x(), real.winfo_y(), real.winfo_width(), real.winfo_height()
                    over.place(x=x - cx, y=y, width=w, height=h)
                    bottom = max(bottom, y + h)
                backing.place(x=0, y=0, relwidth=1.0, height=bottom + 2)
                backing.lift()
                for _real, over in overlays:
                    over.lift()
            except tk.TclError:
                pass

        inner.bind("<Configure>", sync, add="+")
        canvas.configure(xscrollcommand=lambda *a: (hs.set(*a), sync()))
        canvas.after_idle(sync)

    # ---- odložená uložení časů
    def _cancel_pending(self, key):
        item = self._pending.pop(key, None)
        if item:
            try: self.after_cancel(item[0])
            except Exception: pass
        return item

    def _flush_pending(self, key=None):

        keys = [key] if key is not None else list(self._pending)
        for k in keys:
            item = self._cancel_pending(k)
            if item:
                try: item[1]()
                except tk.TclError: pass

    def _on_time_leave(self, var, key):

        normalized = normalize_time(var.get())
        if normalized != var.get():
            var.set(normalized)
        self._flush_pending(key)

    def _schedule_summary(self):
        if self._summary_job is None:
            self._summary_job = self.after_idle(self._run_summary)

    def _run_summary(self):
        self._summary_job = None
        try:
            self._update_summary(self.month_var.get())
        except tk.TclError:
            pass

    def open_code_popup(self, anchor_widget, code_var, on_choose):
        self._close_active_popup()
        popup = tk.Toplevel(self)
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        self._active_popup = popup
        self._active_popup_anchor = anchor_widget

        keys = list(CODES.keys())
        labels = list(CODES.values())

        frame = tk.Frame(popup, bg=self.c_surface, highlightbackground=self.c_accent, highlightthickness=1, bd=0)
        frame.pack()
        listbox = tk.Listbox(frame, height=len(labels), width=max(len(l) for l in labels) + 2,
                              activestyle="none", relief="flat", bg=self.c_surface, fg=self.c_fg,
                              selectbackground=self.c_accent, selectforeground=self.c_surface,
                              font=("Segoe UI", 10), exportselection=False, highlightthickness=0, bd=0)
        listbox.pack(padx=1, pady=1)
        for lab in labels:
            listbox.insert("end", lab)

        current = code_var.get()
        if current in keys:
            idx = keys.index(current)
            listbox.selection_set(idx)
            listbox.see(idx)
            listbox.activate(idx)

        def on_mouse_move(event):
            idx = listbox.nearest(event.y)
            if 0 <= idx < len(labels):
                listbox.selection_clear(0, "end")
                listbox.selection_set(idx)
                listbox.activate(idx)

        listbox.bind("<Motion>", on_mouse_move)

        def choose(_event=None):
            sel = listbox.curselection()
            self._close_active_popup()
            if sel:
                on_choose(keys[sel[0]])

        listbox.bind("<ButtonRelease-1>", choose)
        listbox.bind("<Return>", choose)
        listbox.bind("<Escape>", lambda e: self._close_active_popup())
        popup.bind("<Escape>", lambda e: self._close_active_popup())

        popup.update_idletasks()
        pw, ph = popup.winfo_reqwidth(), popup.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = anchor_widget.winfo_rootx()
        y = anchor_widget.winfo_rooty() + anchor_widget.winfo_height()
        if x + pw > sw: x = max(0, sw - pw - 4)
        if y + ph > sh: y = anchor_widget.winfo_rooty() - ph
        popup.geometry(f"+{x}+{y}")

        listbox.focus_set()

    def _close_active_popup(self):
        popup = getattr(self, "_active_popup", None)
        if popup is not None:
            try: popup.destroy()
            except Exception: pass
            self._active_popup = None

    def _maybe_close_popup_on_outside_click(self, event):
        popup = getattr(self, "_active_popup", None)
        if popup is None:
            return
        w = event.widget
        if w is getattr(self, "_active_popup_anchor", None):
            return
        while w is not None:
            if w == popup:
                return
            w = getattr(w, "master", None)
        self._close_active_popup()

    def _add_context_menu(self, widget, month, day, emp):
        menu=tk.Menu(widget, tearoff=False)
        menu.add_command(label="Kopírovat tento den", command=lambda: self.copy_day(month,day,emp))
        menu.add_command(label="Vložit do tohoto dne", command=lambda: self.paste_day(month,day,emp))
        menu.add_command(label="Vložit na celý pracovní týden", command=lambda: self.copy_to_week(month,day,emp))
        widget.bind("<Button-3>", lambda e: (menu.tk_popup(e.x_root,e.y_root), menu.grab_release()))

    def copy_day(self, month, day, emp):
        self._flush_pending()
        self._clipboard_day = dict(self.db.get_day(self.year,month,day,emp))

    def paste_day(self, month, day, emp):
        if self._month_locked(month):
            self._locked_notice()
            return
        if not hasattr(self, '_clipboard_day'): return
        self._flush_pending()
        dt = date(self.year, month, day)
        r = self._clipboard_day
        code = r.get("code", "") or ""
        if code and (dt in self.holidays or dt.weekday() >= 5) and not self._weekend_codes_allowed():
            messagebox.showwarning("Kód", "Kód absence se do soboty, neděle ani svátku nezadává.\nZadávání lze povolit v Nastavení.")
            code = ""
        values = {
            "code": code,
            "od": (r.get("od", "") or "") if code == "D/2" or not code else "",
            "do": (r.get("do", "") or "") if code == "D/2" or not code else "",
            "pres_od": (r.get("pres_od", "") or "") if code == "D/2" or not code else "",
            "pres_do": (r.get("pres_do", "") or "") if code == "D/2" or not code else "",
        }
        self.db.save_day(self.year, month, day, emp, values)
        self._render_table(month)

    def copy_to_week(self, month, day, emp):
        if self._month_locked(month):
            self._locked_notice()
            return
        vals = self._vars.get(month, {}).get((day, emp))
        if vals:
            keys = ("code", "od", "do", "pres_od", "pres_do")
            current = {k: vals[i].get() for i, k in enumerate(keys)}
        else:
            current = self.db.get_day(self.year, month, day, emp)
        self._flush_pending()

        code = current.get("code", "") or ""
        values = {
            "code": code,
            "od": current.get("od", "") or "",
            "do": current.get("do", "") or "",
            "pres_od": current.get("pres_od", "") or "",
            "pres_do": current.get("pres_do", "") or "",
        }

        if code and code != "D/2":
            values["od"] = values["do"] = values["pres_od"] = values["pres_do"] = ""

        dt = date(self.year, month, day)
        monday = dt - timedelta(days=dt.weekday())
        copied = 0

        for i in range(5):
            target = monday + timedelta(days=i)
            if target.month != month or target.weekday() >= 5 or target in self.holidays:
                continue
            self.db.save_day(self.year, target.month, target.day, emp, values)
            copied += 1

        self._render_table(month)
        messagebox.showinfo("Hotovo", f"Záznam byl vložen do {copied} pracovních dnů.")

    def _load_month(self, rebuild_summary=True):
        month = self.month_var.get()
        self._refresh_header()
        if rebuild_summary:
            self._rebuild_summary_widgets()
        self._render_table(month)
        self._loaded_month = month

    def _refresh_header(self):
        store=self.settings.get("store","")
        center=self.settings.get("center","")
        locked = self._month_locked(self.month_var.get())
        info = f"{self.year}  •  {store or 'Bez prodejny'}  •  {center or 'Bez střediska'}"
        if locked:
            info += "  •  měsíc je uzavřen"
        self.header_info.config(text=info)
        self.lock_btn.config(text="🔓 Odemknout měsíc" if locked else "🔒 Uzavřít měsíc")

    # ---- uzavření měsíce
    def _weekend_codes_allowed(self):
        return str(self.settings.get("allow_weekend_codes", "0")) == "1"

    def _month_locked(self, month):
        return self.db.is_locked(self.year, month)

    def _locked_notice(self):
        messagebox.showinfo("Měsíc je uzavřen",
                            "Tento měsíc je uzavřen proti úpravám.\nChcete-li ho upravit, nejprve ho odemkněte tlačítkem v záhlaví.")

    def toggle_month_lock(self):
        self._flush_pending()
        month = self.month_var.get()
        label = f"{MONTHS[month-1]} {self.year}"
        if self._month_locked(month):
            if not messagebox.askyesno("Odemknout měsíc", f"Odemknout měsíc {label} pro úpravy?"):
                return
            self.db.set_locked(self.year, month, False)
        else:
            if not messagebox.askyesno("Uzavřít měsíc",
                                       f"Uzavřít měsíc {label}?\n\nÚpravy budou zablokovány, dokud měsíc neodemknete."):
                return
            self.db.set_locked(self.year, month, True)
        self._load_month(rebuild_summary=False)

    # ---- kontrola před exportem
    def _collect_export_issues(self, months):
        errors = []
        missing = {}
        today = date.today()
        for month in months:
            days = calendar.monthrange(self.year, month)[1]
            for emp_id, name in self._active_employees():
                records = self.db.get_employee_month(self.year, month, emp_id)
                for d in range(1, days+1):
                    r = records.get(d, BLANK)
                    code = r["code"] or ""
                    if not code or code == "D/2":
                        ok, msg, _fields = validate_times(r["od"], r["do"], r["pres_od"], r["pres_do"])
                        if not ok:
                            errors.append(f"{name} – {d}.{month}.: {msg}")
                            continue
                    has_time = any(r[k] for k in ("od", "do", "pres_od", "pres_do"))
                    dt = date(self.year, month, d)
                    if not code and not has_time and dt.weekday() < 5 and dt not in self.holidays and dt < today:
                        missing.setdefault((name, month), []).append(f"{d}.{month}.")
        return errors, missing

    def _confirm_export(self, months):
        self._flush_pending()
        errors, missing = self._collect_export_issues(months)
        if not errors and not missing:
            return True
        parts = []
        if errors:
            text = "Chybně vyplněné časy:\n" + "\n".join(errors[:10])
            if len(errors) > 10:
                text += f"\n… a dalších {len(errors) - 10}"
            parts.append(text)
        if missing:
            lines = []
            for (name, month), day_list in list(missing.items())[:10]:
                shown = ", ".join(day_list[:12]) + (" …" if len(day_list) > 12 else "")
                lines.append(f"{name}, {MONTHS[month-1].lower()}: {shown}")
            if len(missing) > 10:
                lines.append(f"… a další ({len(missing) - 10})")
            parts.append("Pracovní dny bez záznamu (pokud jde o dny volna, je to v pořádku):\n" + "\n".join(lines))
        message = "\n\n".join(parts) + "\n\nPřesto vytvořit PDF?"
        return messagebox.askyesno("Kontrola před exportem", message, icon="warning",
                                   default="no" if errors else "yes")

    def _daily_hours(self):
        try:
            value = float(str(self.settings.get("daily_hours", "8")).replace(",", "."))
            return value if value > 0 else 8.0
        except (TypeError, ValueError):
            return 8.0

    def _summary_for(self, month, emp, records=None):
        if records is None:
            records = self.db.get_employee_month(self.year, month, emp)
        days=calendar.monthrange(self.year,month)[1]
        worked_days=0; worked_hours=0.0; absent_days=0.0
        codes={k:0 for k in CODES if k}
        vacation_days=0.0
        vacation_hours=0.0
        daily_hours=self._daily_hours()
        for d in range(1,days+1):
            r=records.get(d, BLANK)
            c=r["code"] or ""
            h=hours_between(r["od"],r["do"],r["pres_od"],r["pres_do"])
            if h>0:
                worked_days += 1
                worked_hours += h
            if c:
                absent_days += 0.5 if c == "D/2" else 1.0
                codes[c]=codes.get(c,0)+1
            if c == "D":
                vacation_days += 1.0
                vacation_hours += daily_hours
            elif c == "D/2":
                vacation_days += 0.5
                vacation_hours += daily_hours / 2.0
        return {
            "worked_days": worked_days,
            "worked_hours": worked_hours,
            "absent_days": absent_days,
            "codes": codes,
            "vacation_days": vacation_days,
            "vacation_hours": vacation_hours,
        }

    @staticmethod
    def _fmt_days(value):
        value=float(value)
        if value.is_integer():
            return str(int(value))
        return f"{value:.1f}".replace(".", ",")

    @staticmethod
    def _fmt_hours(value):
        return f"{float(value):.2f}".replace(".", ",")

    def _update_summary(self, month):
        for idx, (emp_id, name) in enumerate(self._active_employees()):
            if idx >= len(self.summary_vars):
                continue
            records=self.db.get_employee_month(self.year,month,emp_id)
            data=self._summary_for(month,emp_id,records)
            rows=self.summary_vars[idx]
            
            values = {
                "Odpracováno": (str(data["worked_days"]), self._fmt_hours(data["worked_hours"])),
                "Odpracované svátky": (str(self._count_holiday_workdays(month,emp_id,records)), self._fmt_hours(self._hours_holiday(month,emp_id,records))),
                "Odpracované soboty": (str(self._count_saturday_workdays(month,emp_id,records)), self._fmt_hours(self._hours_saturday(month,emp_id,records))),
                "Neodpracováno": (self._fmt_days(data["absent_days"]), "—"),
                "Dovolená": (self._fmt_days(data["vacation_days"]), self._fmt_hours(data["vacation_hours"])),
                "Nemoc": (str(data["codes"].get("N",0)), "—"),
                "Ošetřování člena rodiny": (str(data["codes"].get("O",0)), "—"),
                "Celodenní lékař": (str(data["codes"].get("L",0)), "—"),
                "Paragraf": (str(data["codes"].get("P",0)), "—"),
                "Volno placené": (str(data["codes"].get("VP",0)), "—"),
                "Volno neplacené": (str(data["codes"].get("VNe",0)), "—"),
            }
            for label, (day_text, hour_text) in values.items():
                if label in rows:
                    rows[label][0].set(day_text)
                    rows[label][1].set(hour_text)

    def open_settings(self):
        win=tk.Toplevel(self)
        win.title("Nastavení")
        win.transient(self)
        win.grab_set()
        win.geometry("700x660")
        win.configure(bg=self.c_surface)
        
        frm=ttk.Frame(win, padding=24)
        frm.pack(fill="both", expand=True)
        
        fields=[("Rok", "year"),("Název prodejny", "store"),("Středisko", "center"),("Denní pracovní fond (hod.)", "daily_hours")]
        for i in range(1,4):
            fields.extend([(f"Pracovník {i}", f"employee{i}"),(f"Osobní číslo {i}", f"employee{i}_personal")])
        vars={k:tk.StringVar(value=self.settings.get(k,"")) for _,k in fields}
        
        for r,(label,k) in enumerate(fields):
            tk.Label(frm,text=label+":", bg=self.c_surface, font=("Segoe UI", 10)).grid(row=r,column=0,sticky="w",pady=6)
            ttk.Entry(frm,textvariable=vars[k],width=45).grid(row=r,column=1,sticky="ew",pady=6, padx=(10,0))
            
        allow_var = tk.BooleanVar(value=self._weekend_codes_allowed())
        ttk.Checkbutton(frm, text="Povolit kód absence (např. dovolenou) i na sobotu, neděli a svátek", variable=allow_var).grid(row=len(fields), column=0, columnspan=2, sticky="w", pady=(14, 0))
        note_row=len(fields)+1
        tk.Label(frm,text="Vyplnění roku ovlivní kalendář pro vyplňování docházky.", wraplength=570, bg=self.c_surface, fg=self.c_muted, justify="left").grid(row=note_row,column=0,columnspan=2,pady=(20,10),sticky="w")
        
        def save():
            try:
                new_year = int(vars["year"].get())
            except ValueError:
                messagebox.showerror("Chyba","Rok musí být celé číslo.",parent=win)
                return
            try:
                daily_hours = float(vars["daily_hours"].get().replace(",", "."))
                if daily_hours <= 0 or daily_hours > 24:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Chyba","Denní pracovní fond musí být číslo větší než 0 a nejvýše 24.",parent=win)
                return
                
            new_settings = {k:v.get().strip() for k,v in vars.items()}
            new_settings["daily_hours"] = str(daily_hours).replace(".", ",")
            new_settings["allow_weekend_codes"] = "1" if allow_var.get() else "0"
            for i in range(1,4):
                if new_settings.get(f"employee{i}") and not new_settings.get(f"employee{i}_personal"):
                    messagebox.showerror("Chyba", f"Pracovník {i} má jméno, ale chybí mu osobní číslo.", parent=win)
                    return

            # Docházka je uložená podle pořadí pracovníka (1–3). Při změně jména nebo čísla
            # tedy nový člověk zdědí staré záznamy - proto se na to před uložením zeptáme.
            self._flush_pending()
            keep_records = {}
            for i in range(1,4):
                old_name = (self.settings.get(f"employee{i}", "") or "").strip()
                old_pers = (self.settings.get(f"employee{i}_personal", "") or "").strip()
                new_name = new_settings.get(f"employee{i}", "")
                new_pers = new_settings.get(f"employee{i}_personal", "")
                if (old_name, old_pers) == (new_name, new_pers):
                    continue
                if not new_name and not new_pers:
                    continue
                count = self.db.count_records(i)
                if not count:
                    continue
                if old_name or old_pers:
                    old_label = (old_name or "bez jména") + (f" (os. č. {old_pers})" if old_pers else "")
                    new_label = (new_name or "bez jména") + (f" (os. č. {new_pers})" if new_pers else "")
                    intro = (f"Pracovník {i}: změna z „{old_label}“ na „{new_label}“.\n\n"
                             f"Na tomto místě je uloženo {count} vyplněných dnů docházky.")
                else:
                    intro = (f"Pracovník {i}: na tomto místě zůstala uložená docházka dřívějšího pracovníka "
                             f"({count} vyplněných dnů).")
                answer = messagebox.askyesnocancel(
                    "Změna pracovníka",
                    intro + "\n\n"
                    "Ano – jde o tutéž osobu (např. oprava překlepu), záznamy zůstanou.\n"
                    "Ne – jde o jinou osobu: dosavadní záznamy se archivují (v aplikaci se už nezobrazí, "
                    "v databázi zůstanou) a nový pracovník začne s prázdnou docházkou.\n"
                    "Zrušit – nic neměnit.",
                    icon="warning", parent=win)
                if answer is None:
                    return
                keep_records[i] = answer

            to_archive = [i for i, keep in keep_records.items() if not keep]
            if to_archive:
                try:
                    self.db.backup_named("pred_zmenou_pracovnika")
                    for i in to_archive:
                        self.db.archive_employee(i)
                except Exception as e:
                    messagebox.showerror("Chyba", f"Záznamy se nepodařilo archivovat, změna nebyla provedena:\n{e}", parent=win)
                    return

            self.settings = {**self.settings, **new_settings}
            self.db.set_settings(self.settings)
            self.year=new_year; self.holidays=czech_holidays(self.year)
            for i in range(3):
                self.employee_vars[i].set(self.settings.get(f"employee{i+1}",""))
                self.employee_personal_vars[i].set(self.settings.get(f"employee{i+1}_personal",""))
            self.month_var.set(self.notebook.index(self.notebook.select())+1)
            self._load_month()
            win.destroy()
            
        btns = tk.Frame(frm, bg=self.c_surface)
        btns.grid(row=note_row+1, column=0, columnspan=2, sticky="e", pady=16)
        ttk.Button(btns,text="Uložit",command=save).pack(side="right", padx=(10,0))
        ttk.Button(btns,text="Zrušit",command=win.destroy).pack(side="right")

    def show_codes(self):
        text="\n".join(f"{k or '—'}  =  {v}" for k,v in CODES.items())
        text += "\n\nDovolená = 1 den; D/2 = 0,5 dne dovolené. U D/2 se dá ještě napsat čas pro zbytek dne."
        messagebox.showinfo("Kódy", text)

    def backup_database(self):
        self._flush_pending()
        path=filedialog.asksaveasfilename(defaultextension=".sqlite3",initialfile=f"Dochazka_MOP_{date.today():%Y-%m-%d}.sqlite3",filetypes=[("Databáze SQLite", "*.sqlite3")])
        if not path: return
        try: self.db.backup_to(path)
        except Exception as e: messagebox.showerror("Chyba zálohy", f"Zálohu se nepodařilo vytvořit:\n{e}")
        else: messagebox.showinfo("Záloha", f"Záloha byla uložena do:\n{path}\n\nAutomatická denní záloha se ukládá při zavření aplikace do složky:\n{self.db.backup_dir}")

    def export_pdf(self):
        month=self.month_var.get()
        if not self._confirm_export([month]): return
        center=self.settings.get("center","").strip()
        store=self.settings.get("store","").strip()
        path=filedialog.asksaveasfilename(defaultextension=".pdf",initialfile=f"{center}_{store}_dochazka_{self.year}_{month:02d}.pdf",filetypes=[("PDF", "*.pdf")])
        if not path: return
        try: self._make_pdf(path,[month])
        except Exception as e: messagebox.showerror("Chyba exportu", f"PDF se nepodařilo vytvořit:\n{e}")
        else: messagebox.showinfo("Export dokončen", f"PDF bylo uloženo do:\n{path}")

    def export_year_pdf(self):
        if not self._confirm_export(list(range(1,13))): return
        center=self.settings.get("center","").strip()
        store=self.settings.get("store","").strip()
        path=filedialog.asksaveasfilename(defaultextension=".pdf",initialfile=f"{center}_{store}_dochazka_{self.year}_rok.pdf",filetypes=[("PDF", "*.pdf")])
        if not path: return
        try: self._make_pdf(path,list(range(1,13)))
        except Exception as e: messagebox.showerror("Chyba exportu", f"PDF se nepodařilo vytvořit:\n{e}")
        else: messagebox.showinfo("Export dokončen", f"PDF za celý rok bylo uloženo do:\n{path}")

    def _make_pdf(self,path,months):
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfbase import pdfmetrics
        from reportlab.lib.enums import TA_CENTER

        font="Helvetica"; bold="Helvetica-Bold"
        candidates=[
            ("DejaVu", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            ("Arial", "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
        ]
        for n,normal,b in candidates:
            if os.path.exists(normal) and os.path.exists(b):
                try:
                    pdfmetrics.registerFont(TTFont(n,normal))
                    pdfmetrics.registerFont(TTFont(n+"B",b))
                    font=n; bold=n+"B"; break
                except Exception:
                    pass

        styles=getSampleStyleSheet()
        title_style=ParagraphStyle("TitleCZ", parent=styles["Title"], fontName=bold, fontSize=15, leading=17, alignment=TA_CENTER, spaceAfter=4)
        normal_style=ParagraphStyle("NormalCZ", parent=styles["Title"], fontName=bold, fontSize=12, leading=17, aligment=TA_CENTER, spaceAfter=2)

        active=self._active_employees()
        doc=SimpleDocTemplate(path, pagesize=A4, leftMargin=22, rightMargin=22, topMargin=20, bottomMargin=20)
        elems=[]
        info = f"{self.settings.get('center', '')} - {self.settings.get('store', '')}".strip(" -")
        
        for month in months:
            if not active:
                elems.append(Paragraph(f"Docházka – {MONTHS[month-1]} {self.year}", title_style))
                elems.append(Paragraph("Nejsou zadáni žádní pracovníci.", normal_style))
                if month != months[-1]: elems.append(PageBreak())
                continue
                
            for emp_pos,(emp_id,name) in enumerate(active):
                elems.append(Paragraph(f"Docházka – {MONTHS[month-1]} {self.year}", title_style))
                personal = self.settings.get(f"employee{emp_id}_personal", "").strip()
                elems.append(Paragraph(f"{name}  •  Osobní číslo: {personal or '—'}", ParagraphStyle("Emp", parent=normal_style, fontName=bold, fontSize=10, leading=11, alignment=TA_CENTER)))
                if info:
                    elems.append(Paragraph(info, normal_style))
                elems.append(Spacer(1,4))

                header=["Datum","Den","SV","Kód","OD","DO","Pauza OD","Pauza DO","Hod"]
                rows=[header]
                days=calendar.monthrange(self.year,month)[1]
                
                for d in range(1,days+1):
                    dt=date(self.year,month,d); hol=dt in self.holidays
                    r=self.db.get_day(self.year,month,d,emp_id)
                    rows.append([
                        dt.strftime("%d.%m."), DAYS_CZ[dt.weekday()], "SV" if hol else "", r["code"],
                        r["od"], r["do"], r["pres_od"], r["pres_do"],
                        f"{hours_between(r['od'],r['do'],r['pres_od'],r['pres_do']):.2f}"
                    ])

                tbl=Table(rows, colWidths=[42,28,24,34,38,38,48,48,38], repeatRows=1)
                style=[
                    ('FONTNAME',(0,0),(-1,-1),font),('FONTNAME',(0,0),(-1,0),bold),
                    ('FONTSIZE',(0,0),(-1,-1),9),('GRID',(0,0),(-1,-1),0.3,colors.grey),
                    ('FONTSIZE',(3,1),(8,-1),9),
                    ('ALIGN',(0,0),(-1,-1),'CENTER'),('VALIGN',(0,0),(-1,-1),'MIDDLE'),
                    ('BACKGROUND',(0,0),(-1,0),colors.HexColor("#d5d9df")),
                    ('TOPPADDING',(0,0),(-1,-1),2),('BOTTOMPADDING',(0,0),(-1,-1),2),
                ]
                for ridx in range(1,days+1):
                    dt=date(self.year,month,ridx)
                    if dt in self.holidays:
                        style.append(('BACKGROUND',(0,ridx),(-1,ridx),colors.HexColor("#e6f1ff")))
                    elif dt.weekday()>=5:
                        style.append(('BACKGROUND',(0,ridx),(-1,ridx),colors.HexColor('#f1f1f1')))
                tbl.setStyle(TableStyle(style))
                elems.append(tbl)
                elems.append(Spacer(1,5))

                data=self._summary_for(month,emp_id)
                summary=[
                    ["Rekapitulace","Dny","Hodiny"],
                    ["Odpracováno", data["worked_days"], self._fmt_hours(data["worked_hours"])],
                    ["Odpracované svátky", self._count_holiday_workdays(month,emp_id), self._fmt_hours(self._hours_holiday(month,emp_id))],
                    ["Odpracované soboty", self._count_saturday_workdays(month,emp_id), self._fmt_hours(self._hours_saturday(month,emp_id))],
                    ["Neodpracováno", self._fmt_days(data["absent_days"]), "—"],
                    ["Dovolená (Kód - D, D/2)", self._fmt_days(data["vacation_days"]), self._fmt_hours(data["vacation_hours"])],
                    ["Nemoc (Kód - N)", data["codes"].get("N",0), "—"],
                    ["Ošetřování člena rodiny (Kód - O)", data["codes"].get("O",0), "—"],
                    ["Celodenní lékař (Kód - L)", data["codes"].get("L",0), "—"],
                    ["Paragraf (Kód - P)", data["codes"].get("P",0), "—"],
                    ["Volno placené (Kód - VP)", data["codes"].get("VP",0), "—"],
                    ["Volno neplacené (Kód - VNe)", data["codes"].get("VNe",0), "—"],
                ]
                st=[
                    ('FONTNAME',(0,0),(-1,-1),font),('FONTNAME',(0,0),(-1,0),bold),
                    ('FONTNAME',(0,1),(0,-1),bold),('FONTSIZE',(0,0),(-1,-1),8.5),
                    ('GRID',(0,0),(-1,-1),0.35,colors.grey),('VALIGN',(0,0),(-1,-1),'MIDDLE'),
                    ('ALIGN',(1,0),(-1,-1),'CENTER'),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e8edf3')),
                    ('TOPPADDING',(0,0),(-1,-1),2.5),('BOTTOMPADDING',(0,0),(-1,-1),2.5),
                ]
                summary_tbl=Table(summary,colWidths=[300,55,65])
                summary_tbl.setStyle(TableStyle(st))
                elems.append(summary_tbl)
                
                if not (month == months[-1] and emp_pos == len(active)-1):
                    elems.append(PageBreak())
        doc.build(elems)

    def _hours_for_condition(self, month, emp, predicate, records=None):
        if records is None:
            records = self.db.get_employee_month(self.year, month, emp)
        total=0.0
        days=calendar.monthrange(self.year,month)[1]
        for d in range(1,days+1):
            dt=date(self.year,month,d)
            if predicate(dt):
                r=records.get(d, BLANK)
                total += hours_between(r["od"],r["do"],r["pres_od"],r["pres_do"])
        return total

    def _count_condition(self, month, emp, predicate, records=None):
        if records is None:
            records = self.db.get_employee_month(self.year, month, emp)
        count=0
        days=calendar.monthrange(self.year,month)[1]
        for d in range(1,days+1):
            dt=date(self.year,month,d)
            if predicate(dt):
                r=records.get(d, BLANK)
                if hours_between(r["od"],r["do"],r["pres_od"],r["pres_do"]) > 0:
                    count += 1
        return count

    def _hours_holiday(self, month, emp, records=None):
        return self._hours_for_condition(month, emp, lambda dt: dt in self.holidays, records)

    def _count_holiday_workdays(self, month, emp, records=None):
        return self._count_condition(month, emp, lambda dt: dt in self.holidays, records)

    def _hours_saturday(self, month, emp, records=None):
        return self._hours_for_condition(month, emp, lambda dt: dt.weekday()==5, records)

    def _count_saturday_workdays(self, month, emp, records=None):
        return self._count_condition(month, emp, lambda dt: dt.weekday()==5, records)

    def on_close(self):
        self._flush_pending()
        self.db.set_settings({"year":self.year,"store":self.settings.get("store",""),"center":self.settings.get("center",""),"daily_hours":self.settings.get("daily_hours","8")})
        for i,v in enumerate(self.employee_vars,1):
            self.db.set_settings({f"employee{i}":v.get(), f"employee{i}_personal": self.employee_personal_vars[i-1].get()})
        try:
            self.db.backup_rotating()
        except Exception as e:
            messagebox.showwarning("Záloha", f"Automatická záloha se nepodařila:\n{e}")
        self.db.close()
        self.destroy()

if __name__ == "__main__":
    app=AttendanceApp()
    app.mainloop()