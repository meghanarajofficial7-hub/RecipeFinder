"""
gui.py — Tkinter GUI for Recipe Finder with Nutrition Analysis.

WHAT THIS FILE DOES:
  This is the "face" of the application — everything the user sees and clicks.
  It builds the window, lays out every button/panel/tab, and wires up the
  actions (search, filter, show nutrition, save favourites, export PDF, etc.).

WINDOW LAYOUT (left side | right side):
  ┌──────────────────────┬──────────────────────────────────────────┐
  │ Search box           │  Recipe image                            │
  │ Dietary filters      │  [Add Fav] [Nutrition] [PDF]             │
  │ Status / progress    │  ┌─ Tabs ──────────────────────────────┐ │
  │ ┌─ Tabs ───────────┐ │  │ Recipe Info  │  Nutrition Chart     │ │
  │ │Results│Hist│Favs │ │  └─────────────────────────────────────┘ │
  │ └─────────────────┘ │                                          │
  └──────────────────────┴──────────────────────────────────────────┘

KEY CLASSES / FUNCTIONS:
  RecipeApp      — main window class (inherits from tk.Tk)
  _build()       — creates all widgets
  _theme_apply() — applies light or dark colour theme to every widget
  _recolour()    — recursively walks every widget and recolours it
  _search()      — starts the search in a background thread
  _show_recipe() — populates the right panel when a recipe is clicked
  _nutrition()   — fetches + draws the nutrition chart

THREADING MODEL:
  Tkinter runs on ONE thread (the "main thread").  If you make a slow
  network call on that thread, the window freezes — buttons don't respond,
  the progress bar doesn't animate.

  Solution: ALL API calls run in background daemon threads.
  Results are handed back to the main thread via self.after(0, callback, data).
  self.after(0, ...) is Tkinter's thread-safe "post to main thread" mechanism.

  Race condition prevention:
  • self._fetching      — boolean flag; True while any API request is in flight.
                          _pick(), _nutrition(), _loadfav() check this and bail
                          out immediately if another request is already running.
  • self._current_img_url — URL token for image downloads; if the user switches
                          recipe while an image is downloading, the stale bytes
                          are discarded when the old URL no longer matches.

  Thread-safety rule: NEVER call .configure() or any Tkinter method
  from inside a background thread function (_do_search, _do_info, etc.).
  Only call self.after(0, ...) from those functions.
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading, io, json, os, re
from datetime import datetime
from PIL import Image, ImageTk

import api_handler
import nutrition as nutr
from logger import setup_logger

log = setup_logger("GUI")

# ── Colour Themes ─────────────────────────────────────────────────────────────
# Food-inspired warm palette.
#
# Key meanings:
#   bg          — main window background
#   panel       — card/panel background
#   accent      — primary action colour (buttons, selected tabs)
#   accent2     — secondary/save action colour
#   text        — main body text
#   sub         — muted/hint text
#   entry_bg    — text entry background
#   entry_fg    — text entry foreground
#   sel         — list selection highlight
#   border      — borders and separators
#   btn_fg      — text ON accent-coloured buttons
#   btn_dis_fg  — text on DISABLED buttons (must stay readable)
#   hdr_bg      — header bar background
#   hdr_sub     — header subtitle text colour
LIGHT = dict(
    bg="#FDF6EE",       # warm cream
    panel="#FFFFFF",
    accent="#D4460A",   # rich tomato-orange
    accent2="#D4460A",  # same — unused now, kept for compat
    text="#1C1917",     # near-black warm
    sub="#78716C",      # warm stone
    entry_bg="#FFFFFF",
    entry_fg="#1C1917",
    sel="#FED7AA",      # peach selection
    border="#E7E5E4",
    btn_fg="#FFFFFF",
    btn_dis_fg="#FFFFFF",   # white — readable on orange disabled bg
    hdr_bg="#D4460A",
    hdr_sub="#FFD5B8",
    hdr_btn_bg="#3D1500",   # dark espresso — complements orange header
    hdr_btn_fg="#FFF0E0",
    hdr_btn_hov="#5C2300",  # hover: slightly lighter espresso
    accent_hov="#B83A08",   # hover: darker orange for primary buttons
)
DARK = dict(
    bg="#1A0800",       # deep burnt brown
    panel="#2C1500",    # dark wood
    accent="#F97316",   # bright flame orange
    accent2="#F97316",
    text="#FEF3C7",     # warm cream white
    sub="#A8A29E",
    entry_bg="#2C1500",
    entry_fg="#FEF3C7",
    sel="#7C2D12",      # deep ember
    border="#4B2400",
    btn_fg="#FFFFFF",
    btn_dis_fg="#FFFFFF",   # white — readable on orange disabled bg
    hdr_bg="#9A3412",
    hdr_sub="#FDBA74",
    hdr_btn_bg="#FEF3C7",   # cream on dark header
    hdr_btn_fg="#1A0800",
    hdr_btn_hov="#FFFFFF",
    accent_hov="#EA6010",   # hover: slightly lighter orange
)

# ── Persistent files ──────────────────────────────────────────────────────────
DIR   = os.path.dirname(os.path.abspath(__file__))
H_FILE = os.path.join(DIR, "search_history.json")
F_FILE = os.path.join(DIR, "favorites.json")

def _load(p):
    """
    Load a JSON file from disk and return its contents as a Python list.
    If the file doesn't exist yet, return an empty list (not an error).
    The 'with' keyword ensures the file is always closed after reading —
    important on Windows where open files can cause save errors.
    """
    try:
        if not os.path.exists(p):
            return []   # first run — file doesn't exist yet, that's fine
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []   # if the file is corrupted or unreadable, start fresh


def _save(p, d):
    """
    Save a Python list to a JSON file on disk.
    'with' ensures the file is properly closed (and flushed) after writing.
    indent=2 makes the file human-readable if you open it in a text editor.
    ensure_ascii=False preserves special characters like accents (é, ñ, etc.)
    """
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)

# ── PDF export ────────────────────────────────────────────────────────────────
def _pdf(recipe, df, path):
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib import colors
        doc  = SimpleDocTemplate(path, pagesize=A4)
        sty  = getSampleStyleSheet()
        body = []
        body.append(Paragraph(recipe.get("title","Recipe"), sty["Heading1"]))
        body.append(Spacer(1,12))
        body.append(Paragraph("Ingredients", sty["Heading2"]))
        for i in recipe.get("extendedIngredients",[]):
            body.append(Paragraph(f"• {i.get('original','')}", sty["Normal"]))
        body.append(Spacer(1,12))
        body.append(Paragraph("Instructions", sty["Heading2"]))
        for blk in recipe.get("analyzedInstructions",[]):
            for s in blk.get("steps",[]):
                body.append(Paragraph(f"{s.get('number','')}. {s.get('step','')}", sty["Normal"]))
                body.append(Spacer(1,4))
        if df is not None and not df.empty:
            body.append(Spacer(1,12))
            body.append(Paragraph("Nutrition", sty["Heading2"]))
            td = [["Nutrient","Value","Unit"]] + [[r.Nutrient,f"{r.Value:.0f}",r.Unit]
                                                   for r in df.itertuples()]
            t = Table(td, hAlign="LEFT")
            t.setStyle(TableStyle([
                ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#2563EB")),
                ("TEXTCOLOR",(0,0),(-1,0),colors.white),
                ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
                ("GRID",(0,0),(-1,-1),0.5,colors.grey),
            ]))
            body.append(t)
        doc.build(body)
    except ImportError:
        tp = path.replace(".pdf",".txt")
        with open(tp,"w",encoding="utf-8") as f:
            f.write(recipe.get("title","Recipe")+"\n"+"="*60+"\n\n")
            f.write("INGREDIENTS\n")
            for i in recipe.get("extendedIngredients",[]):
                f.write(f"  • {i.get('original','')}\n")
            f.write("\nINSTRUCTIONS\n")
            for blk in recipe.get("analyzedInstructions",[]):
                for s in blk.get("steps",[]):
                    f.write(f"  {s.get('number','')}. {s.get('step','')}\n")
        messagebox.showinfo("Saved as TXT", f"reportlab not installed.\nSaved as:\n{tp}")

# ─────────────────────────────────────────────────────────────────────────────
class RecipeApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("🍽  Recipe Finder with Nutrition Analysis")
        self.geometry("1280x780")
        self.minsize(960, 600)

        self._dark   = False
        self._theme  = LIGHT
        self._recipe = None
        self._df     = None
        self._results= []
        self._hist   = _load(H_FILE)
        self._favs   = _load(F_FILE)

        # _sec_btns holds references to "secondary" buttons (Clear, Clear History,
        # Remove Selected) so we can colour them differently from the main blue
        # action buttons.  Populated during _left() and _right().
        self._sec_btns = []

        # ── Threading guards ───────────────────────────────────────────────────
        # _fetching: True while ANY background API request is in flight.
        #   Used as a gate in _pick(), _nutrition(), _loadfav() to prevent
        #   multiple concurrent requests and the race conditions they cause.
        #
        # _current_img_url: the image URL we MOST RECENTLY started downloading.
        #   When the download finishes, we compare — if the URL no longer
        #   matches (because the user loaded a different recipe in the meantime),
        #   we discard the downloaded bytes instead of showing the wrong image.
        self._fetching        = False
        self._current_img_url = ""

        # _raw_nutr stores the raw nutrition API response dict for the current
        # recipe.  Used by _export_csv() to build the full nutrient DataFrame.
        # Reset to None each time a new recipe is loaded.
        self._raw_nutr = None

        self._build()
        self._theme_apply()
        self._bind_shortcuts()
        self._wire_hover()
        log.info("App started")

        # ── Check API key is configured ────────────────────────────────────────
        # We do this AFTER building the GUI so the window is visible when the
        # warning dialog pops up.  The user sees the app AND the warning together.
        # self.after(200, ...) = wait 200ms so the window fully renders first.
        self.after(200, self._check_api_key)

    # ── Build ─────────────────────────────────────────────────────────────────
    def _build(self):
        self.columnconfigure(0, weight=1, minsize=310)
        self.columnconfigure(1, weight=3)
        self.rowconfigure(0, weight=0)   # header — fixed height
        self.rowconfigure(1, weight=1)   # main content — stretches to fill window
        self.rowconfigure(2, weight=0)   # status bar — fixed height at bottom
        self._header()
        self._left()
        self._right()
        self._statusbar()   # thin bar at the very bottom of the window

    def _header(self):
        self._hdr = tk.Frame(self)
        self._hdr.grid(row=0, column=0, columnspan=2, sticky="ew")
        self._hdr.columnconfigure(0, weight=1)

        # Title + tagline stacked vertically on the left
        self._ttl = tk.Label(self._hdr,
            text="🍽  Recipe Finder",
            font=("Segoe UI", 16, "bold"))
        self._ttl.grid(row=0, column=0, padx=16, pady=(10, 1), sticky="w")

        self._sub = tk.Label(self._hdr,
            text="Discover recipes · Explore nutrition · Cook with confidence",
            font=("Segoe UI", 8, "italic"))
        self._sub.grid(row=1, column=0, padx=18, pady=(0, 8), sticky="w")

        self._tbtn = tk.Button(self._hdr, text="🌙  Dark Mode",
            command=self._toggle, font=("Segoe UI", 9, "bold"),
            relief="flat", cursor="hand2", padx=14, pady=6)
        self._tbtn.grid(row=0, column=1, rowspan=2, padx=14, sticky="e")

    def _left(self):
        lf = tk.Frame(self)
        lf.grid(row=1, column=0, sticky="nsew", padx=(10,4), pady=(6,10))
        lf.columnconfigure(0, weight=1)
        lf.rowconfigure(4, weight=1)
        self._lf = lf

        # Search box
        sf = tk.LabelFrame(lf, text="  🔍  Search Recipes  ", font=("Segoe UI", 9, "bold"), padx=8, pady=6)
        sf.grid(row=0, column=0, sticky="ew", pady=(0,6))
        # weight=1 on both columns means they share space equally.
        # Without this, column 1 (the Clear button) would be tiny/squished.
        sf.columnconfigure(0, weight=1)
        sf.columnconfigure(1, weight=1)
        self._ivar = tk.StringVar()
        self._ient = ttk.Entry(sf, textvariable=self._ivar, font=("Segoe UI",10))
        self._ient.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0,5))
        self._ient.insert(0, "e.g. chicken, rice, tomato")
        self._ient.bind("<FocusIn>",  lambda _: self._ient.delete(0,"end")
                        if self._ivar.get()=="e.g. chicken, rice, tomato" else None)
        self._ient.bind("<Return>",   lambda _: self._search())
        self._sbtn = tk.Button(sf, text="🔍  Search", command=self._search,
                               font=("Segoe UI", 10, "bold"), relief="flat",
                               cursor="hand2", padx=8, pady=5)
        self._sbtn.grid(row=1, column=0, sticky="ew", padx=(0,3))
        self._clrbtn = tk.Button(sf, text="✕  Clear", command=self._clear,
                                 font=("Segoe UI", 9), relief="flat",
                                 cursor="hand2", padx=8, pady=5)
        self._clrbtn.grid(row=1, column=1, sticky="ew")
        self._sec_btns.append(self._clrbtn)

        # Filters
        ff = tk.LabelFrame(lf, text="  🥗  Dietary Filters  ", font=("Segoe UI", 9, "bold"), padx=8, pady=5)
        ff.grid(row=1, column=0, sticky="ew", pady=(0,6))
        self._vg = tk.BooleanVar(); self._vn = tk.BooleanVar(); self._gf = tk.BooleanVar()
        ttk.Checkbutton(ff, text="🥦 Vegetarian",  variable=self._vg).pack(anchor="w")
        ttk.Checkbutton(ff, text="🌱 Vegan",        variable=self._vn).pack(anchor="w")
        ttk.Checkbutton(ff, text="🌾 Gluten Free",  variable=self._gf).pack(anchor="w")

        # Status + progress
        self._svar = tk.StringVar(value="Enter ingredients and press Search.")
        self._slbl = tk.Label(lf, textvariable=self._svar, font=("Segoe UI",8),
                              anchor="w", wraplength=290)
        self._slbl.grid(row=2, column=0, sticky="ew", pady=(0,2))
        self._prog = ttk.Progressbar(lf, mode="indeterminate")
        self._prog.grid(row=3, column=0, sticky="ew", pady=(0,5))

        # Tabs: Results / History / Favourites
        self._lnb = ttk.Notebook(lf)
        self._lnb.grid(row=4, column=0, sticky="nsew")

        def _lst(tab):
            tab.rowconfigure(0, weight=1); tab.columnconfigure(0, weight=1)
            lb = tk.Listbox(tab, font=("Segoe UI",9), selectmode="single",
                            activestyle="none", relief="flat", bd=0)
            lb.grid(row=0, column=0, sticky="nsew")
            sc = ttk.Scrollbar(tab, orient="vertical", command=lb.yview)
            sc.grid(row=0, column=1, sticky="ns")
            lb.configure(yscrollcommand=sc.set)
            return lb

        rt = tk.Frame(self._lnb); self._lnb.add(rt, text="Results")
        self._rlb = _lst(rt)
        self._rlb.bind("<<ListboxSelect>>", self._pick)

        ht = tk.Frame(self._lnb); self._lnb.add(ht, text="History")
        self._hlb = _lst(ht)
        self._hlb.bind("<Double-Button-1>", self._rerun)
        # Store reference so _recolour() can style it as secondary
        self._chistbtn = tk.Button(ht, text="🗑  Clear History", font=("Segoe UI",8),
                                   relief="flat", command=self._clrhist, cursor="hand2")
        self._chistbtn.grid(row=1, column=0, columnspan=2, pady=3, padx=4, sticky="ew")
        self._sec_btns.append(self._chistbtn)
        self._rh()

        ft = tk.Frame(self._lnb); self._lnb.add(ft, text="Favourites ♥")
        self._flb = _lst(ft)
        self._flb.bind("<Double-Button-1>", self._loadfav)
        # Store reference so _recolour() can style it as secondary
        self._rmfavbtn = tk.Button(ft, text="🗑  Remove Selected", font=("Segoe UI",8),
                                   relief="flat", command=self._rmfav, cursor="hand2")
        self._rmfavbtn.grid(row=1, column=0, columnspan=2, pady=3, padx=4, sticky="ew")
        self._sec_btns.append(self._rmfavbtn)
        self._rf()

    def _right(self):
        rf = tk.Frame(self)
        rf.grid(row=1, column=1, sticky="nsew", padx=(4,10), pady=(6,10))
        rf.columnconfigure(0, weight=1)
        rf.rowconfigure(2, weight=1)
        # _rpanel = the entire right half of the window (image + buttons + tabs)
        # Named clearly to avoid confusion with the _rf() method (which refreshes
        # the Favorites list — a completely different thing)
        self._rpanel = rf

        # Image
        self._img = tk.Label(rf, text="No recipe selected", font=("Segoe UI",12))
        self._img.grid(row=0, column=0, sticky="ew", pady=(0,6))

        # ── Action buttons ─────────────────────────────────────────────────────
        # Row 0: three primary actions (Add to Favourites / Show Nutrition / PDF)
        # Row 1: Copy Ingredients — full width, separate row so it doesn't crowd row 0
        # All start disabled; _show_recipe() enables them when a recipe is loaded.
        bf = tk.Frame(rf)
        bf.grid(row=1, column=0, sticky="ew", pady=(0,5))
        for i in range(3):
            bf.columnconfigure(i, weight=1)

        # ── Row 0: primary actions ─────────────────────────────────────────────
        self._fbtn = tk.Button(bf, text="♥  Add to Favourites",
            command=self._addfav, font=("Segoe UI", 9, "bold"), relief="flat",
            cursor="hand2", state="disabled", padx=6, pady=5)
        self._fbtn.grid(row=0, column=0, padx=3, pady=(0,3), sticky="ew")

        self._nbtn = tk.Button(bf, text="📊  Nutrition",
            command=self._nutrition, font=("Segoe UI", 9, "bold"), relief="flat",
            cursor="hand2", state="disabled", padx=6, pady=5)
        self._nbtn.grid(row=0, column=1, padx=3, pady=(0,3), sticky="ew")

        self._pbtn = tk.Button(bf, text="📄  Download PDF",
            command=self._pdf, font=("Segoe UI", 9, "bold"), relief="flat",
            cursor="hand2", state="disabled", padx=6, pady=5)
        self._pbtn.grid(row=0, column=2, padx=3, pady=(0,3), sticky="ew")

        self._cbtn = tk.Button(bf, text="📋  Copy Ingredients to Clipboard  (Ctrl+I)",
            command=self._copy_ingredients, font=("Segoe UI", 9), relief="flat",
            cursor="hand2", state="disabled", padx=6, pady=4)
        self._cbtn.grid(row=1, column=0, columnspan=3, padx=3, sticky="ew")

        # Notebook: Info / Nutrition
        self._rnb = ttk.Notebook(rf)
        self._rnb.grid(row=2, column=0, sticky="nsew")

        it = tk.Frame(self._rnb); self._rnb.add(it, text="📋  Recipe Info")
        it.rowconfigure(0, weight=1); it.columnconfigure(0, weight=1)
        self._txt = tk.Text(it, font=("Segoe UI",9), wrap="word",
                            relief="flat", bd=0, padx=10, pady=10, state="disabled")
        self._txt.grid(row=0, column=0, sticky="nsew")
        sc = ttk.Scrollbar(it, orient="vertical", command=self._txt.yview)
        sc.grid(row=0, column=1, sticky="ns")
        self._txt.configure(yscrollcommand=sc.set)
        self._txt.tag_configure("h1",  font=("Segoe UI",13,"bold"))
        self._txt.tag_configure("h2",  font=("Segoe UI",10,"bold"))
        self._txt.tag_configure("tag", font=("Segoe UI",9,"italic"))
        self._txt.tag_configure("bod", font=("Segoe UI",9))
        self._txt.tag_configure("bul", font=("Segoe UI",9), lmargin1=16, lmargin2=28)

        nt = tk.Frame(self._rnb); self._rnb.add(nt, text="🥗  Nutrition")
        nt.rowconfigure(1, weight=1)   # chart row stretches to fill space
        nt.columnconfigure(0, weight=1)

        # Row 0: nutrition summary — 2 lines of text showing macros + micronutrients
        # wraplength=700 allows long lines to wrap instead of being cut off
        self._nsum = tk.Label(nt, text="", font=("Segoe UI", 9),
                              anchor="w", justify="left", wraplength=700)
        self._nsum.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))

        # Row 1: chart image (fills all remaining vertical space)
        self._nchart = tk.Label(nt,
            text="Press  📊 Show Nutrition  to load chart.",
            font=("Segoe UI", 11))
        self._nchart.grid(row=1, column=0, sticky="nsew")

        # Row 2: Export CSV button — disabled until nutrition data is loaded
        # Exports the FULL nutrient list (all bad+good items) as a spreadsheet
        self._csvbtn = tk.Button(nt,
            text="💾  Export Full Nutrition as CSV",
            command=self._export_csv,
            font=("Segoe UI", 9, "bold"), relief="flat", cursor="hand2",
            state="disabled", padx=6, pady=5)
        self._csvbtn.grid(row=2, column=0, sticky="ew", padx=10, pady=(4, 8))

    # ── Theme ─────────────────────────────────────────────────────────────────
    def _theme_apply(self):
        t = self._theme
        self.configure(bg=t["bg"])
        s = ttk.Style(self); s.theme_use("clam")
        s.configure("TNotebook",     background=t["bg"],    borderwidth=0)
        s.configure("TNotebook.Tab", background=t["panel"], foreground=t["text"],
                    padding=[12, 5], font=("Segoe UI", 9, "bold"))
        s.map("TNotebook.Tab",
              background=[("selected", t["accent"])],
              foreground=[("selected", t["btn_fg"])])
        s.configure("TEntry",        fieldbackground=t["entry_bg"], foreground=t["entry_fg"],
                    font=("Segoe UI", 10))
        s.configure("TCheckbutton",  background=t["bg"], foreground=t["text"],
                    font=("Segoe UI", 9))
        s.configure("TScrollbar",    background=t["border"], troughcolor=t["bg"])
        s.configure("Horizontal.TProgressbar", background=t["accent"], troughcolor=t["bg"])
        self._recolour(self)

    def _recolour(self, w):
        t  = self._theme
        cn = w.__class__.__name__
        try:
            if   cn == "Frame":      w.configure(bg=t["bg"])
            elif cn == "LabelFrame": w.configure(bg=t["bg"], fg=t["accent"])
            elif cn == "Label":      w.configure(bg=t["panel"], fg=t["text"])
            elif cn == "Button":     w.configure(bg=t["accent"], fg=t["btn_fg"],
                                                 activebackground=t["accent2"],
                                                 activeforeground=t["btn_fg"],
                                                 disabledforeground=t["btn_dis_fg"])
            elif cn == "Listbox":    w.configure(bg=t["entry_bg"], fg=t["text"],
                                                 selectbackground=t["sel"],
                                                 selectforeground=t["text"])
            elif cn == "Text":       w.configure(bg=t["entry_bg"], fg=t["text"],
                                                 insertbackground=t["text"])
        except tk.TclError:
            pass
        for c in w.winfo_children():
            self._recolour(c)
        # ── Specific overrides ────────────────────────────────────────────────────
        # These widgets need colours different from what the generic loop above set.
        try:
            # Header bar
            self._hdr.configure(bg=t["hdr_bg"])
            self._ttl.configure(bg=t["hdr_bg"], fg=t["btn_fg"])
            self._sub.configure(bg=t["hdr_bg"], fg=t["hdr_sub"])
            # Dark mode toggle — espresso brown to complement orange header
            self._tbtn.configure(bg=t["hdr_btn_bg"], fg=t["hdr_btn_fg"],
                                 activebackground=t["hdr_btn_hov"],
                                 activeforeground=t["hdr_btn_fg"])
            # Status label (inside left panel) — blend with bg, use muted text
            self._slbl.configure(bg=t["bg"], fg=t["sub"])
            # Image area — panel background, muted placeholder text
            self._img.configure(bg=t["panel"], fg=t["sub"])
            # Chart area — entry background so it looks like a display box
            self._nchart.configure(bg=t["entry_bg"], fg=t["sub"])
            # Nutrition summary row — bg background
            self._nsum.configure(bg=t["bg"], fg=t["text"])
            # Text tags inside the recipe text box
            self._txt.tag_configure("h1", foreground=t["accent"])
            self._txt.tag_configure("h2", foreground=t["accent2"])
        except Exception:
            pass

        # ── Secondary buttons (Clear, Clear History, Remove Selected) ────────────
        # These are destructive/neutral actions — they should look visually
        # different from the primary blue action buttons so users don't confuse them.
        # We use t["sub"] (medium grey) as background instead of t["accent"] (blue).
        try:
            for btn in self._sec_btns:
                btn.configure(
                    bg=t["border"],
                    fg=t["text"],
                    activebackground=t["sub"],
                    activeforeground=t["btn_fg"],
                    disabledforeground=t["btn_dis_fg"],
                )
        except Exception:
            pass

        # ── Status bar at the bottom ──────────────────────────────────────────────
        # Slightly different background from main bg to visually separate it.
        # t["panel"] gives it a "card" feel — one shade lighter than the background.
        try:
            self._sb.configure(bg=t["panel"])
            self._sbar_info.configure(bg=t["panel"], fg=t["sub"])
            self._sbar_keys.configure(bg=t["panel"], fg=t["sub"])
        except Exception:
            pass

    def _toggle(self):
        self._dark  = not self._dark
        self._theme = DARK if self._dark else LIGHT
        self._tbtn.configure(text="☀  Light Mode" if self._dark else "🌙 Dark Mode")
        self._theme_apply()
        if self._df is not None:
            self._drawchart()

    # ── Search ────────────────────────────────────────────────────────────────
    def _diet(self):
        if self._vn.get(): return "vegan"
        if self._vg.get(): return "vegetarian"
        if self._gf.get(): return "gluten free"
        return ""

    def _search(self):
        """
        Called when the user presses Search or hits Enter.
        Validates the input, saves to history, then kicks off a background thread.
        """
        q = self._ivar.get().strip()
        # Don't search if the box is empty or still shows the placeholder text
        if not q or q == "e.g. chicken, rice, tomato":
            messagebox.showwarning("Input needed", "Please enter at least one ingredient.")
            return

        self._loading(True, "Searching recipes…")
        self._rlb.delete(0, "end")    # clear old results
        self._results.clear()

        diet = self._diet()   # get active dietary filter (or "" for none)

        # Build the history entry string — e.g. "chicken, rice  [vegan]"
        entry = q + (f"  [{diet}]" if diet else "")
        if entry not in self._hist:
            self._hist.insert(0, entry)     # newest searches at the top
            self._hist = self._hist[:30]    # keep only the last 30 searches
            _save(H_FILE, self._hist)
            self._rh()  # refresh the History tab to show the new entry

        # ── THREADING EXPLANATION ──────────────────────────────────────────────
        # We start the actual API call in a SEPARATE background thread.
        # This means the GUI stays responsive while waiting for the internet.
        # daemon=True means the thread auto-stops when the app window closes.
        # _do_search() runs in the background thread.
        # When it finishes, it uses self.after(0, ...) to safely hand results
        # back to the main GUI thread (Tkinter is not thread-safe, so we must
        # never update widgets from a background thread directly).
        threading.Thread(target=self._do_search, args=(q, diet), daemon=True).start()

    def _do_search(self, q, diet):
        """
        Runs in a BACKGROUND THREAD — do NOT update widgets here directly!
        Calls the API, then schedules _show_results to run on the main thread.
        self.after(0, func, arg) is the safe way to pass data back to Tkinter.
        """
        try:
            res = api_handler.search_recipes(q, diet=diet)
            # after(0, ...) = "run this on the main thread as soon as possible"
            self.after(0, self._show_results, res)
        except Exception as e:
            self.after(0, self._err, str(e))

    def _show_results(self, res):
        self._loading(False)
        self._results = res

        if not res:
            # Give actionable suggestions — "no results" alone is frustrating
            hint = (
                "No recipes found.\n\n"
                "Tips to get results:\n"
                "  • Use simpler ingredient names  (e.g. 'chicken' not 'boneless breast')\n"
                "  • Try fewer ingredients  (1-3 works best)\n"
                "  • Remove the dietary filter and try again\n"
                "  • Check spelling"
            )
            self._svar.set("No recipes found — try simpler ingredients or remove filters.")
            self._sbar_info.configure(text="No results — try simpler ingredients")
            messagebox.showinfo("No Recipes Found", hint)
            return

        # Populate the results list (left panel, Results tab)
        for r in res:
            self._rlb.insert("end", f"  {r.get('title', '?')}")

        msg = f"Found {len(res)} recipe(s). Click any to view details."
        self._svar.set(msg)
        self._sbar_info.configure(text=f"{len(res)} recipes found")

    def _pick(self, _=None):
        """
        Called when the user clicks a recipe in the Results list.

        THREADING GUARD:
          If _fetching is True, another request is already in flight.
          We silently ignore this click rather than starting a second
          thread that would race with the first one.
          The user sees the progress bar is active and naturally waits.
        """
        if self._fetching:
            return   # already loading — ignore this click

        sel = self._rlb.curselection()
        if not sel:
            return
        rid = self._results[sel[0]].get("id")
        if not rid:
            return

        self._loading(True, "Loading recipe details…")
        threading.Thread(target=self._do_info, args=(rid,), daemon=True).start()

    def _do_info(self, rid):
        try:
            info = api_handler.get_recipe_info(rid)
            self.after(0, self._show_recipe, info)
        except Exception as e:
            self.after(0, self._err, str(e))

    def _show_recipe(self, r):
        self._loading(False)
        self._recipe    = r
        self._df        = None
        self._raw_nutr  = None   # clear previous recipe's nutrition data
        # Enable the 4 main action buttons for the newly loaded recipe
        # _csvbtn stays disabled — re-enabled only after nutrition data loads
        for b in (self._fbtn, self._nbtn, self._pbtn, self._cbtn):
            b.configure(state="normal")
        self._csvbtn.configure(state="disabled")   # requires nutrition data first

        # ── Image loading ──────────────────────────────────────────────────────
        # We record the URL we WANT to show BEFORE starting the download.
        # _do_img() will check this when it finishes — if the URL no longer
        # matches (user loaded another recipe while this one was downloading),
        # it discards the bytes.  This prevents the wrong image appearing.
        img_url = r.get("image", "")
        self._current_img_url = img_url   # update the "expected" URL token
        if img_url:
            threading.Thread(target=self._do_img, args=(img_url,), daemon=True).start()
        else:
            self._img.configure(image="", text="No image available")

        # Text
        tw = self._txt
        tw.configure(state="normal"); tw.delete("1.0","end")
        tw.insert("end", r.get("title","Unknown")+"\n\n", "h1")
        tags = []
        if r.get("vegetarian"): tags.append("🥦 Vegetarian")
        if r.get("vegan"):      tags.append("🌱 Vegan")
        if r.get("glutenFree"): tags.append("🌾 Gluten Free")
        if r.get("dairyFree"):  tags.append("🥛 Dairy Free")
        if tags: tw.insert("end", "  ".join(tags)+"\n\n", "tag")
        if r.get("readyInMinutes"):
            tw.insert("end", f"⏱ Ready in {r['readyInMinutes']} min   ", "bod")
        if r.get("servings"):
            tw.insert("end", f"🍽 Serves {r['servings']}\n\n", "bod")

        tw.insert("end", "Ingredients\n", "h2")
        for ing in r.get("extendedIngredients",[]):
            tw.insert("end", f"  • {ing.get('original','')}\n", "bul")

        tw.insert("end", "\nInstructions\n", "h2")
        instrs = r.get("analyzedInstructions",[])
        if instrs:
            for blk in instrs:
                for s in blk.get("steps",[]):
                    tw.insert("end", f"  {s.get('number','')}. {s.get('step','')}\n\n", "bod")
        else:
            clean = re.sub(r"<[^>]+>", "", r.get("summary","No instructions."))
            tw.insert("end", clean+"\n", "bod")
        tw.configure(state="disabled")

        self._nsum.configure(text="")
        self._nchart.configure(image="", text="Press  📊 Show Nutrition  to load chart.")
        self._rnb.select(0)
        self._svar.set(f"Loaded: {r.get('title','')}")
        # Update status bar with the loaded recipe name
        self._sbar_info.configure(text=f"Recipe loaded:  {r.get('title','')}")

    # ── Image ─────────────────────────────────────────────────────────────────
    def _do_img(self, url):
        """
        Background thread: downloads image bytes for the given URL.

        RACE CONDITION GUARD:
          After the download finishes, we check whether 'url' still matches
          self._current_img_url.  If the user loaded a different recipe while
          we were downloading, _current_img_url has changed — so we discard
          these bytes silently rather than overwriting the correct new image.

          Example without this guard:
            1. Click Recipe A  → image A starts downloading (takes 2 seconds)
            2. Click Recipe B  → image B starts downloading (takes 0.3 seconds)
            3. B's image shows ✓
            4. A's image arrives → OVERWRITES B's image  ✗  (race condition)

          With the guard: step 4 detects url != _current_img_url → discards A's image.
        """
        raw = api_handler.fetch_image(url)
        if raw:
            # Only update the UI if this URL is still the one we want to show
            if url == self._current_img_url:
                self.after(0, self._show_img, raw)
            else:
                log.debug("Discarded stale image for: %s", url[:60])
        else:
            # Only show "unavailable" if this is still the current recipe's image
            if url == self._current_img_url:
                self.after(0, lambda: self._img.configure(image="", text="Image unavailable"))

    def _show_img(self, raw):
        """
        Main thread: decode and display image bytes in the image label.
        pil.thumbnail() resizes the image to fit without distorting proportions.
        Image.LANCZOS is the highest-quality resize algorithm in Pillow.
        We store the PhotoImage in self._img.image — IMPORTANT: if we don't,
        Python's garbage collector deletes it and the image goes blank.
        """
        try:
            pil = Image.open(io.BytesIO(raw))
            pil.thumbnail((760, 230), Image.LANCZOS)
            ph = ImageTk.PhotoImage(pil)
            self._img.configure(image=ph, text="")
            self._img.image = ph   # keep reference — Tkinter doesn't own this object
        except Exception as e:
            log.warning("Image display error: %s", e)
            self._img.configure(image="", text="Image could not be displayed")

    # ── Nutrition ─────────────────────────────────────────────────────────────
    def _nutrition(self):
        """
        Fetch and display the nutrition chart for the current recipe.

        THREADING GUARD:
          Double-clicking "Show Nutrition" or pressing Ctrl+N repeatedly
          would fire multiple _do_nutr threads without this check.
          Each thread makes a separate API call — wasting daily quota.
          We block concurrent calls with the _fetching flag.

        ALREADY LOADED CHECK:
          If self._df is already populated (chart was loaded before),
          we skip the API call and just redraw the chart from cached data.
          This saves an API call on every dark-mode toggle.
        """
        if not self._recipe:
            return   # no recipe loaded — button should be disabled but guard anyway

        if self._fetching:
            return   # another request is already in flight

        # If nutrition data is already loaded, just redraw (no API call needed)
        if self._df is not None:
            self._drawchart()
            self._rnb.select(1)   # switch to Nutrition tab
            return

        self._loading(True, "Fetching nutrition data…")
        threading.Thread(target=self._do_nutr, args=(self._recipe["id"],), daemon=True).start()

    def _do_nutr(self, rid):
        """
        Background thread: fetch nutrition data and build the macro DataFrame.
        We pass both df (for the chart) and raw data (for summary + CSV export)
        back to the main thread via self.after().
        """
        try:
            data = api_handler.get_nutrition(rid)
            df   = nutr.build_dataframe(data)   # 4-macro DataFrame for the chart
            self.after(0, self._show_nutr, df, data)
        except Exception as e:
            self.after(0, self._err, str(e))

    def _show_nutr(self, df, raw):
        """
        Main thread: display nutrition summary + chart.

        nutr.build_summary(raw) creates a two-line string:
          Line 1 — Calories / Protein / Carbs / Fat
          Line 2 — Fiber / Sugar / Sodium / Saturated Fat  (with % daily value)

        We also store raw in self._raw_nutr so _export_csv() can later build
        the full nutrient DataFrame without another API call.
        """
        self._loading(False)
        self._df       = df          # 4-macro DataFrame (for chart)
        self._raw_nutr = raw         # full raw response (for CSV export)

        # Build and display the 2-line nutrition summary
        summary_text = nutr.build_summary(raw)
        self._nsum.configure(text=summary_text)

        # Enable the CSV export button now that we have data
        self._csvbtn.configure(state="normal")

        self._drawchart()
        self._rnb.select(1)   # switch to Nutrition tab automatically

    def _drawchart(self):
        """
        Render the nutrition chart from self._df and display it in self._nchart.
        Called from:
          • _show_nutr()  — when nutrition data first loads
          • _nutrition()  — when chart is already cached (no new API call)
          • _toggle()     — when dark/light mode changes (redraw same data, new colours)
        """
        try:
            title = self._recipe.get("title", "") if self._recipe else ""
            chart_bytes = nutr.build_chart(self._df, title, dark=self._dark)
            pil = Image.open(io.BytesIO(chart_bytes))
            ph  = ImageTk.PhotoImage(pil)
            self._nchart.configure(image=ph, text="")
            self._nchart.image = ph   # keep reference — Python GC would delete it otherwise
        except Exception as e:
            log.error("Chart render error: %s", e)
            self._nchart.configure(image="", text="Chart could not be displayed.")

    def _export_csv(self):
        """
        Export the FULL nutrition data for the current recipe to a CSV file.

        WHY 'FULL' DATA?
          self._df only contains 4 macros (for the chart).
          For a useful spreadsheet, we build a complete DataFrame from
          self._raw_nutr using nutr.build_full_dataframe() — this includes
          all nutrients from the bad+good arrays (20-30+ rows typically).

        The file is saved to the exports/ folder by default, but the user can
        choose any location via the Save dialog.
        """
        if not self._raw_nutr:
            messagebox.showwarning("No Data", "Load nutrition data first (press 📊 Show Nutrition).")
            return

        # Suggest a filename based on the recipe title
        recipe_title = self._recipe.get("title", "nutrition") if self._recipe else "nutrition"
        safe_name    = re.sub(r'[<>:"/\\|?*]', "", recipe_title)   # remove invalid filename chars
        safe_name    = safe_name.replace(" ", "_")[:50]             # shorten and replace spaces

        default_path = os.path.join(api_handler.EXPORTS_DIR, f"{safe_name}_nutrition.csv")

        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV Spreadsheet", "*.csv"), ("All Files", "*.*")],
            initialfile=f"{safe_name}_nutrition.csv",
            initialdir=api_handler.EXPORTS_DIR,
            title="Save Nutrition Data as CSV",
        )
        if not path:
            return   # user cancelled the dialog

        try:
            # Build the full nutrient DataFrame (all bad+good items)
            full_df = nutr.build_full_dataframe(self._raw_nutr)
            if full_df.empty:
                messagebox.showwarning("No Detailed Data",
                    "This recipe doesn't have detailed per-nutrient data.\n"
                    "Only summary data (calories, protein, carbs, fat) is available.")
                return
            nutr.export_csv(full_df, path)
            messagebox.showinfo("Exported!", f"Nutrition data saved to:\n{path}")
            log.info("CSV exported: %s (%d rows)", path, len(full_df))
            self._sbar_info.configure(text=f"Exported: {os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("Export Failed", str(e))
            log.error("CSV export failed: %s", e)

    # ── Favourites ────────────────────────────────────────────────────────────
    def _addfav(self):
        if not self._recipe: return
        e = {"id": self._recipe["id"], "title": self._recipe.get("title","?"),
             "saved": datetime.now().strftime("%Y-%m-%d %H:%M")}
        if e["id"] in [f["id"] for f in self._favs]:
            messagebox.showinfo("Already saved","Already in favourites."); return
        self._favs.insert(0, e); _save(F_FILE, self._favs); self._rf()
        messagebox.showinfo("Saved!", f"'{e['title']}' added to favourites ♥")

    def _loadfav(self, _=None):
        """Load a saved favourite recipe — same guard as _pick()."""
        if self._fetching:
            return   # already loading another recipe
        sel = self._flb.curselection()
        if not sel:
            return
        self._loading(True, "Loading favourite recipe…")
        threading.Thread(target=self._do_info, args=(self._favs[sel[0]]["id"],), daemon=True).start()

    def _rmfav(self):
        sel = self._flb.curselection()
        if not sel: return
        self._favs.pop(sel[0]); _save(F_FILE, self._favs); self._rf()

    def _rf(self):
        self._flb.delete(0,"end")
        for f in self._favs: self._flb.insert("end", f"  ♥  {f['title']}")

    # ── History ───────────────────────────────────────────────────────────────
    def _rh(self):
        self._hlb.delete(0,"end")
        for h in self._hist: self._hlb.insert("end", f"  🕐  {h}")

    def _rerun(self, _=None):
        sel = self._hlb.curselection()
        if not sel: return
        q = self._hist[sel[0]].split("  [")[0].strip()
        self._ient.delete(0,"end"); self._ient.insert(0,q); self._search()

    def _clrhist(self):
        self._hist.clear(); _save(H_FILE, self._hist); self._rh()

    # ── PDF ───────────────────────────────────────────────────────────────────
    def _pdf(self):
        """
        Save the current recipe (ingredients + instructions + nutrition) as a PDF.
        If nutrition has been loaded, we include the full nutrient table.
        If not, the PDF is saved without nutrition data.
        """
        if not self._recipe:
            return

        safe_name = re.sub(r'[<>:"/\\|?*]', "", self._recipe.get("title", "recipe"))
        safe_name = safe_name.replace(" ", "_")[:50] + ".pdf"

        path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf"), ("All Files", "*.*")],
            initialfile=safe_name,
            initialdir=api_handler.EXPORTS_DIR,
        )
        if not path:
            return

        # Use full nutrition DataFrame if available, otherwise fall back to macros
        pdf_df = None
        if self._raw_nutr is not None:
            pdf_df = nutr.build_full_dataframe(self._raw_nutr)
        elif self._df is not None:
            pdf_df = self._df

        try:
            _pdf(self._recipe, pdf_df, path)
            messagebox.showinfo("Saved", f"Recipe saved to:\n{path}")
            self._sbar_info.configure(text=f"PDF saved: {os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("PDF Error", str(e))
            log.error("PDF save failed: %s", e)

    # ── Status bar ────────────────────────────────────────────────────────────
    def _statusbar(self):
        """
        Creates a thin bar at the very bottom of the window.

        LEFT side  : shows what the app is currently doing (updates dynamically)
        RIGHT side : always shows keyboard shortcut hints so the user can discover
                     them without reading any documentation

        Why at the bottom?  Professional desktop apps (VS Code, Excel, browsers)
        all have a status bar at the bottom.  It's a well-known UI pattern that
        makes the app feel more polished and gives the user constant feedback.
        """
        # Outer frame spans the full window width
        self._sb = tk.Frame(self)
        self._sb.grid(row=2, column=0, columnspan=2, sticky="ew")
        self._sb.columnconfigure(0, weight=1)

        # Left label: current app status (updated by various methods)
        self._sbar_info = tk.Label(
            self._sb,
            text="Ready — enter ingredients and press Search",
            font=("Segoe UI", 8),
            anchor="w",
        )
        self._sbar_info.grid(row=0, column=0, padx=10, pady=(3, 3), sticky="w")

        # Right label: keyboard shortcut hints (static — always visible)
        self._sbar_keys = tk.Label(
            self._sb,
            text="Ctrl+F: Search  •  Ctrl+N: Nutrition  •  Ctrl+I: Copy Ingredients  •  Ctrl+D: Dark Mode  •  Esc: Clear",
            font=("Segoe UI", 8),
            anchor="e",
        )
        self._sbar_keys.grid(row=0, column=1, padx=10, pady=(3, 3), sticky="e")

    # ── Hover effects ─────────────────────────────────────────────────────────
    def _wire_hover(self):
        """Bind mouse-enter/leave highlight to every interactive button."""
        def _hov(btn, hov_key, rest_key):
            def enter(_):
                if str(btn["state"]) != "disabled":
                    btn.configure(bg=self._theme[hov_key])
            def leave(_):
                if str(btn["state"]) != "disabled":
                    btn.configure(bg=self._theme[rest_key])
            btn.bind("<Enter>", enter, add="+")
            btn.bind("<Leave>", leave, add="+")

        for b in (self._fbtn, self._nbtn, self._pbtn, self._cbtn,
                  self._csvbtn, self._sbtn):
            _hov(b, "accent_hov", "accent")

        _hov(self._tbtn, "hdr_btn_hov", "hdr_btn_bg")

        for b in self._sec_btns:
            _hov(b, "sub", "border")

    # ── Keyboard shortcuts ────────────────────────────────────────────────────
    def _bind_shortcuts(self):
        """
        Wire up keyboard shortcuts for the whole window.

        self.bind() attaches a shortcut to the ROOT window, so it works
        no matter which widget currently has keyboard focus.

        HOW IT WORKS:
          self.bind("<Control-f>", callback)
          When the user presses Ctrl+F anywhere in the window, Tkinter calls
          the callback function automatically.  The 'lambda _:' just means
          "accept the event argument but ignore it — we don't need it."
        """
        # Ctrl+F — focus the search box and select all existing text
        # (same behaviour as in a browser — jump to search bar immediately)
        self.bind("<Control-f>", self._shortcut_search)

        # Escape — clear the search box (easy reset)
        self.bind("<Escape>", lambda _: self._clear())

        # Ctrl+N — show nutrition chart (only works if a recipe is already loaded)
        self.bind("<Control-n>", lambda _: self._nutrition() if self._recipe else None)

        # Ctrl+P — download PDF (only works if a recipe is already loaded)
        self.bind("<Control-p>", lambda _: self._pdf() if self._recipe else None)

        # Ctrl+D — toggle dark / light mode
        self.bind("<Control-d>", lambda _: self._toggle())

        # Ctrl+I — copy ingredients to clipboard
        self.bind("<Control-i>", lambda _: self._copy_ingredients())

        log.debug("Keyboard shortcuts bound")

    def _shortcut_search(self, _=None):
        """Focus the search entry and select all existing text (Ctrl+F)."""
        self._ient.focus_set()
        self._ient.select_range(0, "end")   # highlight so user can type immediately

    # ── Copy ingredients ──────────────────────────────────────────────────────
    def _copy_ingredients(self):
        """
        Copy all ingredient lines for the current recipe to the system clipboard.

        HOW THE CLIPBOARD WORKS IN TKINTER:
          self.clipboard_clear()   — empty whatever was on the clipboard
          self.clipboard_append()  — put our text on the clipboard

        After calling these, Ctrl+V in ANY other app (Notepad, WhatsApp, etc.)
        will paste the ingredient list.

        We show a 3-second "Copied!" confirmation in the status label, then
        restore it to the recipe name.  This gives the user clear feedback
        without interrupting them with a popup dialog.
        """
        if not self._recipe:
            return   # button should be disabled, but guard anyway

        ingredients = self._recipe.get("extendedIngredients", [])
        if not ingredients:
            messagebox.showinfo("Nothing to copy", "No ingredients found for this recipe.")
            return

        # Build a plain-text list:  "• 2 cups chicken broth\n• 1 tsp salt\n..."
        text = "\n".join(f"• {ing.get('original', '')}" for ing in ingredients)

        # Write to clipboard
        self.clipboard_clear()
        self.clipboard_append(text)

        # Brief confirmation — 3 seconds then restore original status text
        self._svar.set("✓  Ingredients copied to clipboard!")
        self._sbar_info.configure(text="✓  Ingredients copied to clipboard!")
        title = self._recipe.get("title", "")
        self.after(3000, lambda: self._svar.set(f"Loaded: {title}"))
        self.after(3000, lambda: self._sbar_info.configure(text=f"Recipe loaded:  {title}"))

        log.info("Ingredients copied for: %s (%d items)", title, len(ingredients))

    # ── API key startup check ─────────────────────────────────────────────────
    def _check_api_key(self):
        """
        Called 200ms after the window opens.

        Checks if the Spoonacular API key has been configured.  If not, shows
        a clear warning dialog BEFORE the user tries to search and gets a
        confusing HTTP 401 error.

        WHY 200ms DELAY?
          Tkinter builds and displays the window in stages.  Calling this too
          early (e.g. during __init__) can show the warning before the window
          even appears, which looks broken.  200ms is long enough for the window
          to render but short enough that the user won't notice a delay.
        """
        valid, msg = api_handler.validate_api_key()
        if not valid:
            # Update the status bar so the warning persists even after dialog closes
            self._svar.set("⚠  API key not configured — searches will fail")
            self._sbar_info.configure(text="⚠  API key not configured — see warning dialog")
            messagebox.showwarning("API Key Not Set", msg)
            log.warning("Startup: API key not configured")

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _loading(self, on, msg=""):
        """
        Turn the loading state on or off.

        on=True  → progress bar starts spinning, Search button disabled,
                   self._fetching = True  (blocks _pick / _nutrition / _loadfav)
        on=False → spinner stops, Search button re-enabled,
                   self._fetching = False (allows new requests)

        WHY CENTRALISE THIS?
          Every background operation calls _loading(True) at start and
          _loading(False) at end.  Putting the _fetching flag HERE means
          ALL operations share the same gate automatically — no need to
          duplicate the guard logic in each individual method.

        The progress bar is 'indeterminate' mode — it bounces back and forth
        to signal "working" without showing a percentage (we don't know how
        long the API will take).
        """
        self._fetching = on   # ← single source of truth for "is something loading?"

        if on:
            self._prog.start(10)              # 10ms step = smooth animation
            self._sbtn.configure(state="disabled")
            if msg:
                self._svar.set(msg)
                self._sbar_info.configure(text=msg)
        else:
            self._prog.stop()
            self._sbtn.configure(state="normal")

    def _clear(self):
        """Clear the search input and results list. (Does NOT unload the current recipe.)"""
        self._ient.delete(0, "end")
        self._rlb.delete(0, "end")
        self._results.clear()
        msg = "Enter ingredients and press Search."
        self._svar.set(msg)
        self._sbar_info.configure(text="Ready — enter ingredients to search")

    def _err(self, msg):
        """
        Display an error to the user.

        We parse the error message to give context-aware guidance:
        - API key errors → tell them exactly how to fix the .env file
        - Rate limit     → tell them to wait until tomorrow
        - No internet    → tell them to check their connection
        - Other          → show the raw message

        This turns a confusing technical error into an actionable instruction.
        """
        self._loading(False)
        self._svar.set(f"Error: {msg[:80]}")   # truncate long messages in status bar
        self._sbar_info.configure(text=f"Error — see dialog")

        # Give context-aware advice based on the error type
        msg_lower = msg.lower()
        if "401" in msg or "invalid" in msg_lower and "key" in msg_lower:
            title = "API Key Problem"
            full_msg = (
                f"{msg}\n\n"
                "How to fix:\n"
                "  • Open your .env file\n"
                "  • Check that SPOONACULAR_API_KEY is set to your real key\n"
                "  • Get a free key at: spoonacular.com/food-api"
            )
        elif "402" in msg or "limit" in msg_lower:
            title = "Daily Limit Reached"
            full_msg = (
                f"{msg}\n\n"
                "The free plan allows 150 API calls per day.\n"
                "The limit resets at midnight UTC.\n"
                "Try again tomorrow, or upgrade your Spoonacular plan."
            )
        elif "internet" in msg_lower or "network" in msg_lower or "connection" in msg_lower:
            title = "No Internet Connection"
            full_msg = (
                f"{msg}\n\n"
                "Please check:\n"
                "  • Your Wi-Fi or ethernet connection is active\n"
                "  • A firewall is not blocking the app\n"
                "  • Try opening spoonacular.com in a browser"
            )
        elif "timed out" in msg_lower:
            title = "Request Timed Out"
            full_msg = (
                f"{msg}\n\n"
                "The server took too long to respond.\n"
                "Try searching again — it usually succeeds on the second attempt."
            )
        else:
            title = "Error"
            full_msg = msg

        messagebox.showerror(title, full_msg)
        log.error("User-visible error [%s]: %s", title, msg)
