"""
nutrition.py — Pandas DataFrame building + Matplotlib chart generation.

WHAT THIS FILE DOES:
  Takes the raw JSON data from the Spoonacular nutrition API and turns it
  into structured, visual, and exportable forms.

  The Spoonacular nutritionWidget.json endpoint returns:
    {
      "calories": "543",       ← top-level summary (string)
      "protein":  "34g",
      "carbs":    "60g",
      "fat":      "20g",
      "bad":  [ {title, amount, percentOfDailyNeeds}, ... ],  ← nutrients to limit
      "good": [ {title, amount, percentOfDailyNeeds}, ... ],  ← beneficial nutrients
    }

  We provide four functions:
    build_dataframe(data)       → 4-row DataFrame (macros) used for the chart
    build_full_dataframe(data)  → all nutrients from bad+good, used for CSV export
    build_summary(data)         → 2-line formatted string for the GUI summary bar
    build_chart(df, title, dark)→ PNG bytes (embedded in the Tkinter window)
    export_csv(df, path)        → saves a DataFrame to a .csv file

HOW CHARTS WORK IN TKINTER:
  Matplotlib normally opens its own pop-up window for charts.  In Tkinter
  we use the 'Agg' backend instead — this renders to a PNG in memory (bytes)
  without showing any pop-up.  We then load those bytes as a Pillow image
  and display it inside a tk.Label widget.
"""

import io
import re
import textwrap

import pandas as pd
import matplotlib
matplotlib.use("Agg")          # non-interactive backend — renders to bytes, no pop-up window
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg

from logger import setup_logger

log = setup_logger("Nutrition")

# ── Colour palette for the 4 main macros ──────────────────────────────────────
# Consistent colours used in both the bar chart and the pie chart legend.
MACRO_COLOURS = {
    "Calories":      "#FF6B6B",   # warm red
    "Protein":       "#4ECDC4",   # teal
    "Carbohydrates": "#FFE66D",   # yellow
    "Fat":           "#A8E6CF",   # soft green
}


# ══════════════════════════════════════════════════════════════════════════════
#  PRIVATE HELPER
# ══════════════════════════════════════════════════════════════════════════════
def _num(text):
    """
    Extract the first number from a string like "34.5 g" or "800mg".
    Returns 0.0 if no number is found.

    Example:
      _num("34.5 g")  → 34.5
      _num("800mg")   → 800.0
      _num("?")       → 0.0
    """
    match = re.search(r"[\d.]+", str(text))
    return float(match.group()) if match else 0.0


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC FUNCTION 1: 4-macro DataFrame (used for the chart)
# ══════════════════════════════════════════════════════════════════════════════
def build_dataframe(data):
    """
    Build a simple 4-row Pandas DataFrame from the top-level summary fields.

    WHY ONLY 4 ROWS?
      The chart needs all values on a similar scale (grams or kcal) to be
      visually comparable.  Mixing in sodium (milligrams) or vitamins (micrograms)
      would make those bars invisible next to Calories (hundreds of kcal).
      A second, dedicated chart would be needed for micronutrients — kept
      as a future enhancement.

    Returns: DataFrame with columns [Nutrient, Value, Unit]
      Example row:  Protein | 34.0 | g
    """
    # Maps API key → (display name, unit)
    mapping = {
        "calories": ("Calories",      "kcal"),
        "carbs":    ("Carbohydrates", "g"),
        "fat":      ("Fat",           "g"),
        "protein":  ("Protein",       "g"),
    }
    rows = [
        {"Nutrient": display_name, "Value": _num(data.get(api_key, 0)), "Unit": unit}
        for api_key, (display_name, unit) in mapping.items()
    ]
    df = pd.DataFrame(rows)
    log.info("Macro DataFrame built:\n%s", df.to_string(index=False))
    return df


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC FUNCTION 2: full nutrient DataFrame (used for CSV export)
# ══════════════════════════════════════════════════════════════════════════════
def build_full_dataframe(data):
    """
    Build a comprehensive DataFrame from the 'bad' and 'good' nutrient arrays.

    This extracts EVERY nutrient Spoonacular returns — macros, fiber, sugar,
    sodium, saturated fat, cholesterol, vitamins, minerals, etc.

    Returns: DataFrame with columns [Nutrient, Value, Unit, Daily %, Group]
      Example row:  Fiber | 4.5 | g | 18.0 | Good

    Used for:
      • CSV export (all nutrients in a spreadsheet)
      • Detailed summary display in the GUI
    """
    rows = []
    seen = set()   # avoid duplicate nutrient names if they appear in both bad+good

    for group_name in ("bad", "good"):
        for item in data.get(group_name, []):
            title = item.get("title", "").strip()
            if not title or title in seen:
                continue   # skip empty names and duplicates
            seen.add(title)

            # Parse "34.5 g" → value=34.5, unit="g"
            # split(None, 1) splits on any whitespace, max 1 time
            amount_str = item.get("amount", "0").strip()
            parts = amount_str.split(None, 1)
            value = _num(parts[0]) if parts else 0.0
            unit  = parts[1].strip() if len(parts) > 1 else ""

            pct_daily = item.get("percentOfDailyNeeds", 0.0)

            rows.append({
                "Nutrient": title,
                "Value":    value,
                "Unit":     unit,
                "Daily %":  round(pct_daily, 1),
                "Group":    group_name.capitalize(),   # "Bad" or "Good"
            })

    if not rows:
        # Return empty DataFrame with correct column structure
        log.warning("build_full_dataframe: no bad/good nutrient data found")
        return pd.DataFrame(columns=["Nutrient", "Value", "Unit", "Daily %", "Group"])

    df = pd.DataFrame(rows)
    # Sort: Good nutrients first (alphabetically), then Bad (alphabetically)
    df = df.sort_values(["Group", "Nutrient"]).reset_index(drop=True)
    log.info("Full nutrition DataFrame: %d nutrients extracted", len(df))
    return df


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC FUNCTION 3: formatted summary string for the GUI
# ══════════════════════════════════════════════════════════════════════════════
def build_summary(data):
    """
    Build a two-line human-readable summary string for display in the GUI.

    Line 1: Main macros (from the top-level summary fields)
    Line 2: Key micronutrients with % Daily Value (from bad/good arrays)

    Returns: string with a newline in the middle
    """
    # ── Line 1: main macros ────────────────────────────────────────────────────
    # We use the top-level summary fields — they're pre-formatted by Spoonacular
    cal  = data.get("calories", "?")
    prot = data.get("protein",  "?")
    carb = data.get("carbs",    "?")
    fat  = data.get("fat",      "?")
    line1 = (
        f"Calories: {cal} kcal     "
        f"Protein: {prot}     "
        f"Carbs: {carb}     "
        f"Fat: {fat}"
    )

    # ── Line 2: key micronutrients with % daily value ─────────────────────────
    # We look for these specific nutrients in the bad/good arrays
    targets = ["Fiber", "Sugar", "Sodium", "Saturated Fat"]
    found   = {}
    for group_name in ("bad", "good"):
        for item in data.get(group_name, []):
            t = item.get("title", "")
            if t in targets and t not in found:
                pct = item.get("percentOfDailyNeeds", 0)
                amt = item.get("amount", "?")
                found[t] = f"{t}: {amt}  ({pct:.0f}% DV)"

    # Build line 2 only from nutrients we actually found in the data
    line2_parts = [found[k] for k in targets if k in found]
    line2 = "     ".join(line2_parts)

    return (line1 + ("\n" + line2 if line2 else "")).strip()


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC FUNCTION 4: PNG chart (bar + pie, embedded in Tkinter)
# ══════════════════════════════════════════════════════════════════════════════
def build_chart(df, title="", dark=False):
    """
    Render a bar chart (all 4 macros) and a pie chart (3 macros, no calories)
    side-by-side into a PNG image and return the raw bytes.

    WHY RETURN BYTES INSTEAD OF SHOWING THE CHART?
      Matplotlib's normal plt.show() opens a new pop-up window.  In a Tkinter
      app we want the chart inside the main window.  The Agg backend renders
      to memory (bytes) instead of the screen.  We then load those bytes
      with Pillow and display them in a tk.Label widget.

    Parameters:
      df    — the 4-row macro DataFrame from build_dataframe()
      title — recipe name shown above the chart
      dark  — True for dark colour scheme

    Returns: bytes (PNG image data)
    """
    # ── Colour scheme ─────────────────────────────────────────────────────────
    if dark:
        bg, fg, grid = "#1E1E2E", "#E0E0E0", "#444466"
    else:
        bg, fg, grid = "#FFFFFF", "#2D2D2D", "#E0E0E0"

    # 9×5 inches at 100 DPI = 900×500 pixel image
    # The extra height (was 4, now 5) gives more room for value labels on tall bars
    fig = Figure(figsize=(9, 5), dpi=100, facecolor=bg)

    # ── Left subplot: bar chart of all 4 macros ────────────────────────────────
    ax1 = fig.add_subplot(1, 2, 1)
    ax1.set_facecolor(bg)

    bar_colours = [MACRO_COLOURS.get(n, "#AAAAAA") for n in df["Nutrient"]]
    bars = ax1.bar(
        df["Nutrient"], df["Value"],
        color=bar_colours,
        edgecolor="none",
        width=0.60,
    )

    # Add value + unit label on top of each bar
    max_val = max(df["Value"]) if max(df["Value"]) > 0 else 1
    for bar, row in zip(bars, df.itertuples()):
        # Two-line label: "543" on top, "kcal" below it
        label = f"{row.Value:.0f}\n{row.Unit}"
        ax1.text(
            bar.get_x() + bar.get_width() / 2,   # x = centre of bar
            bar.get_height() + max_val * 0.025,   # y = just above bar top
            label,
            ha="center", va="bottom",
            color=fg, fontsize=8.5, fontweight="bold",
        )

    ax1.set_title("Nutritional Breakdown", color=fg, fontsize=11, pad=12, fontweight="bold")
    ax1.set_ylabel("Amount", color=fg, fontsize=9)
    ax1.tick_params(axis="both", colors=fg, labelsize=8.5)
    for spine in ax1.spines.values():
        spine.set_color(grid)
        spine.set_linewidth(0.5)
    ax1.yaxis.grid(True, color=grid, linewidth=0.5, linestyle="--")
    ax1.set_axisbelow(True)                        # grid lines behind bars
    ax1.set_ylim(0, max_val * 1.35)               # extra headroom for labels

    # ── Right subplot: pie chart of macros (Protein / Carbs / Fat only) ───────
    # Calories is excluded because it uses a different unit (kcal vs grams).
    # Mixing kcal into a gram-based pie chart would be misleading.
    ax2 = fig.add_subplot(1, 2, 2)
    ax2.set_facecolor(bg)

    macro_df = df[df["Nutrient"] != "Calories"].copy()
    pie_vals  = macro_df["Value"].tolist()
    if sum(pie_vals) == 0:
        pie_vals = [1, 1, 1]   # prevent crash on all-zero data

    pie_colours = [MACRO_COLOURS.get(n, "#AAAAAA") for n in macro_df["Nutrient"]]
    _, _, auto_pcts = ax2.pie(
        pie_vals,
        colors=pie_colours,
        autopct="%1.1f%%",
        startangle=140,
        pctdistance=0.75,
        wedgeprops={"edgecolor": bg, "linewidth": 2},
    )
    for pct_text in auto_pcts:
        pct_text.set_color(fg)
        pct_text.set_fontsize(8.5)
        pct_text.set_fontweight("bold")

    # Legend: shows nutrient name AND its value (e.g. "Protein  34g")
    legend_labels = [
        f"{row.Nutrient}   {row.Value:.0f}{row.Unit}"
        for row in macro_df.itertuples()
    ]
    legend_patches = [
        mpatches.Patch(color=MACRO_COLOURS.get(row.Nutrient, "#AAA"), label=lbl)
        for row, lbl in zip(macro_df.itertuples(), legend_labels)
    ]
    ax2.legend(
        handles=legend_patches,
        loc="lower center",
        bbox_to_anchor=(0.5, -0.14),
        ncol=3,
        fontsize=8,
        frameon=False,
        labelcolor=fg,
    )
    ax2.set_title("Macro Distribution", color=fg, fontsize=11, pad=12, fontweight="bold")

    # ── Figure title (recipe name) ─────────────────────────────────────────────
    if title:
        # textwrap.shorten keeps the title to one line with "..." if too long.
        # This is cleaner than the old [:55] slice which could cut mid-word.
        short_title = textwrap.shorten(title, width=65, placeholder=" …")
        fig.suptitle(short_title, color=fg, fontsize=10, fontweight="bold", y=1.01)

    # ── Render to PNG bytes ────────────────────────────────────────────────────
    fig.tight_layout(pad=2.0)
    buf = io.BytesIO()
    FigureCanvasAgg(fig).print_png(buf)
    plt.close("all")   # free memory — important inside a long-running GUI app
    return buf.getvalue()


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC FUNCTION 5: CSV export
# ══════════════════════════════════════════════════════════════════════════════
def export_csv(df, path):
    """
    Save a nutrition DataFrame to a CSV file.

    WHY utf-8-sig ENCODING?
      Regular utf-8 CSV files sometimes show garbled characters when opened
      in Microsoft Excel on Windows.  The 'utf-8-sig' encoding adds a BOM
      (Byte Order Mark) at the start of the file — a small hidden marker
      that tells Excel "this file is UTF-8".  Excel then opens it correctly,
      showing special characters (accents, symbols) properly.

    Parameters:
      df   — DataFrame from build_full_dataframe() (has all nutrients)
      path — full file path where the CSV should be saved

    Returns: path (so caller can display it in a confirmation message)
    """
    df.to_csv(path, index=False, encoding="utf-8-sig")
    log.info("Nutrition CSV exported to: %s", path)
    return path
