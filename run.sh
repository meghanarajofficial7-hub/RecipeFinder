#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
#  Recipe Finder — One-Click Launcher  (Linux / macOS)
#  Just double-click this file OR run:  bash run.sh
# ═══════════════════════════════════════════════════════════════════

set -e
cd "$(dirname "$0")"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║     🍽  Recipe Finder with Nutrition Analysis        ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── Step 1: Check Python ─────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "❌  Python 3 not found."
    echo "    Install from: https://www.python.org/downloads/"
    exit 1
fi

PYVER=$(python3 -c "import sys; print(sys.version_info.major*10+sys.version_info.minor)")
if [ "$PYVER" -lt 310 ]; then
    echo "❌  Python 3.10+ required. You have: $(python3 --version)"
    exit 1
fi
echo "✅  Python: $(python3 --version)"

# ── Step 2: Check tkinter ────────────────────────────────────────
if ! python3 -c "import tkinter" 2>/dev/null; then
    echo ""
    echo "❌  tkinter not found. Install it:"
    echo "    Ubuntu/Debian : sudo apt install python3-tk"
    echo "    Fedora        : sudo dnf install python3-tkinter"
    echo "    macOS         : brew install python-tk"
    exit 1
fi
echo "✅  tkinter: OK"

# ── Step 3: Virtual environment ──────────────────────────────────
if [ ! -d "venv" ]; then
    echo ""
    echo "📦  Creating virtual environment…"
    python3 -m venv venv
fi
echo "✅  Virtual environment: ready"

# ── Step 4: Activate venv ────────────────────────────────────────
source venv/bin/activate

# ── Step 5: Install packages ─────────────────────────────────────
echo ""
echo "📥  Installing / verifying required packages…"
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo "✅  All packages installed"

# ── Step 6: Check .env file ──────────────────────────────────────
echo ""
if [ ! -f ".env" ]; then
    echo "┌─────────────────────────────────────────────────────┐"
    echo "│  ⚠️   .env FILE NOT FOUND                           │"
    echo "│                                                     │"
    echo "│  You need to create a .env file with your API key. │"
    echo "│                                                     │"
    echo "│  QUICK SETUP:                                       │"
    echo "│  1. Get a FREE key at:                              │"
    echo "│     https://spoonacular.com/food-api                │"
    echo "│                                                     │"
    echo "│  2. Create a file named  .env  in this folder      │"
    echo "│                                                     │"
    echo "│  3. Add this line to it:                            │"
    echo "│     SPOONACULAR_API_KEY=your_actual_key_here        │"
    echo "│                                                     │"
    echo "│  4. Run this script again                           │"
    echo "│  See  .env.example  for a ready template            │"
    echo "└─────────────────────────────────────────────────────┘"
    echo ""
    read -p "Press Enter to open .env.example in an editor, or Ctrl+C to exit... "
    if command -v gedit  &>/dev/null; then gedit  .env.example &
    elif command -v nano &>/dev/null; then nano   .env.example
    elif command -v vim  &>/dev/null; then vim    .env.example
    else echo "Please create a .env file based on .env.example"
    fi
    exit 0
fi
echo "✅  .env file: found"

# ── Step 7: Launch app ───────────────────────────────────────────
echo ""
echo "🚀  Launching Recipe Finder…"
echo "     (Close the app window to stop)"
echo ""
python3 main.py
