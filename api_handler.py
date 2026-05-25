"""
api_handler.py — All Spoonacular API calls for Recipe Finder.

HOW TO SET YOUR API KEY (safe method)
--------------------------------------
1. Copy the file  .env.example
2. Rename the copy to  .env
3. Open .env and replace  your_api_key_goes_here  with your real key
4. Save — the app reads it automatically

Get a FREE key at: https://spoonacular.com/food-api
(Free plan = 150 API calls per day)

WHY WE USE A .env FILE:
  Putting the key directly in this Python file is unsafe — if you
  ever share your code or upload it to GitHub, your key gets exposed.
  A .env file stays on your computer only and is never uploaded.
"""

import os
import time
import hashlib
import urllib.parse                # for building safe URL query strings
import requests                    # for making HTTP web requests
from dotenv import load_dotenv     # for reading the .env file
from logger import setup_logger

# ── Logger setup ──────────────────────────────────────────────────────────────
# This creates a logger named "API" — it writes messages to recipe_app.log
# so we can see exactly what API calls were made (useful for debugging)
log = setup_logger("API")

# ── Load environment variables from .env file ─────────────────────────────────
# load_dotenv() reads the .env file in the project folder.
# After this line, os.environ["SPOONACULAR_API_KEY"] will contain your key.
load_dotenv()

# ── Read the API key ──────────────────────────────────────────────────────────
# os.environ.get() reads the value from .env.
# If the .env file is missing or the key is not set, it falls back to the
# old method of checking the env variable directly — so both approaches work.
API_KEY = os.environ.get("SPOONACULAR_API_KEY", "YOUR_SPOONACULAR_API_KEY_HERE")

# ── Base URL ──────────────────────────────────────────────────────────────────
# All Spoonacular API endpoints start with this URL.
BASE = "https://api.spoonacular.com"

# ── Folder paths ──────────────────────────────────────────────────────────────
# os.path.dirname gets the folder where this file lives.
# This means paths work correctly no matter where the user puts the project.
_DIR     = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR   = os.path.join(_DIR, "cache")      # folder for saved images
LOGS_DIR    = os.path.join(_DIR, "logs")       # folder for log files
EXPORTS_DIR = os.path.join(_DIR, "exports")   # folder for PDFs/CSVs

# ── Create folders if they don't exist ────────────────────────────────────────
# exist_ok=True means "don't crash if the folder already exists"
for _folder in (CACHE_DIR, LOGS_DIR, EXPORTS_DIR):
    os.makedirs(_folder, exist_ok=True)

# ── Retry settings ────────────────────────────────────────────────────────────
# If the internet is slow or the server briefly fails, we try again.
MAX_RETRIES = 3          # try up to 3 times total
RETRY_DELAY = 1.5        # wait 1.5 seconds between retries
TIMEOUT     = 15         # give up if no response in 15 seconds

# These are the placeholder values that mean "key was never configured"
_PLACEHOLDER_KEYS = {
    "YOUR_SPOONACULAR_API_KEY_HERE",
    "your_api_key_goes_here",
    "",
}


# ═════════════════════════════════════════════════════════════════════════════
#  PUBLIC UTILITY 1: validate API key before making any real network call
# ═════════════════════════════════════════════════════════════════════════════
def validate_api_key():
    """
    Check whether the API key looks like it has been configured.

    WHY THIS EXISTS:
      Without this check, a missing key causes a confusing HTTP 401 error deep
      inside a search call.  It's much friendlier to catch it immediately at
      startup and show the user exactly how to fix it.

    Returns: (True, "")           — key looks OK, proceed
             (False, error_msg)   — key is missing/placeholder, show error_msg
    """
    if API_KEY in _PLACEHOLDER_KEYS:
        msg = (
            "Spoonacular API key is not configured!\n\n"
            "To fix this:\n"
            "  1. Create a file named  .env  in the project folder\n"
            "  2. Add this line to it:\n"
            "       SPOONACULAR_API_KEY=your_real_key_here\n"
            "  3. Get a FREE key at: https://spoonacular.com/food-api\n"
            "     (150 calls/day — no credit card needed)\n\n"
            "The app will still open but searches will fail until the key is set."
        )
        log.error("API key not configured — user sees setup prompt")
        return False, msg

    if len(API_KEY) < 10:
        # A real Spoonacular key is 32+ characters.  Anything shorter is wrong.
        msg = (
            "API key looks too short — it may be incomplete.\n"
            "Check your .env file and make sure the full key is pasted correctly."
        )
        log.error("API key looks too short: %d chars", len(API_KEY))
        return False, msg

    log.info("API key configured (%d chars)", len(API_KEY))
    return True, ""


# ═════════════════════════════════════════════════════════════════════════════
#  PUBLIC UTILITY 2: quick internet connectivity check
# ═════════════════════════════════════════════════════════════════════════════
def check_internet():
    """
    Send a lightweight HEAD request to check if we can reach the internet.

    WHY HEAD (not GET)?
      A HEAD request fetches only the HTTP headers — no body data is transferred.
      It's extremely fast (< 100ms on a normal connection) and uses almost no
      bandwidth.  Perfect for a "are we online?" check.

    Returns: True if connected, False if offline
    """
    try:
        # We don't need the API key for this — just checking connectivity
        requests.head(BASE, timeout=3)
        return True
    except requests.exceptions.ConnectionError:
        log.warning("Internet connectivity check failed — no connection")
        return False
    except Exception:
        # On any other error (timeout, DNS, etc.) assume we're offline
        return False


# ═════════════════════════════════════════════════════════════════════════════
#  PRIVATE HELPER: make one GET request with retries
# ═════════════════════════════════════════════════════════════════════════════
def _get(endpoint, params):
    """
    Make a GET request to a Spoonacular endpoint.

    endpoint : the path like "/recipes/findByIngredients"
    params   : a dictionary of query parameters like {"ingredients": "chicken"}

    Returns  : the JSON response as a Python dictionary

    Raises   : ConnectionError if something goes wrong (no internet, bad key, etc.)
    """
    # Always attach the API key — Spoonacular requires it on every request
    params["apiKey"] = API_KEY

    # Build the full URL.  urllib.parse.urlencode turns a dict into "key=val&key2=val2"
    url = f"{BASE}{endpoint}"

    # Log the request — we hide the real API key in the log so it stays secret.
    # We use urllib.parse.urlencode (not requests.compat) because it's the
    # standard library version and doesn't depend on requests internals.
    safe_params = {k: ("***" if k == "apiKey" else v) for k, v in params.items()}
    log.debug("GET %s?%s", url, urllib.parse.urlencode(safe_params))

    # ── Retry loop ────────────────────────────────────────────────────────────
    # We try MAX_RETRIES times. On each failure we wait a bit, then try again.
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, params=params, timeout=TIMEOUT)

            # ── Handle specific HTTP error codes with helpful messages ─────────
            if response.status_code == 401:
                # 401 = "Unauthorised" — wrong or missing API key
                raise ConnectionError(
                    "API key is invalid (HTTP 401).\n"
                    "Check your .env file — make sure SPOONACULAR_API_KEY is set correctly."
                )
            elif response.status_code == 402:
                # 402 = "Payment Required" — daily free limit reached
                raise ConnectionError(
                    "Daily API limit reached (HTTP 402).\n"
                    "Free plan allows 150 calls/day. Try again tomorrow."
                )
            elif response.status_code == 429:
                # 429 = "Too Many Requests" — sending too fast
                log.warning("Rate limited (attempt %d/%d) — waiting…", attempt, MAX_RETRIES)
                time.sleep(RETRY_DELAY * 2)
                continue
            elif response.status_code >= 500:
                # 500+ = server error on Spoonacular's side
                log.warning("Server error %d (attempt %d/%d)", response.status_code, attempt, MAX_RETRIES)
                last_error = ConnectionError(f"Spoonacular server error (HTTP {response.status_code}). Try again.")
                time.sleep(RETRY_DELAY)
                continue

            # If we reach here the request was successful — parse and return JSON
            response.raise_for_status()
            return response.json()

        except ConnectionError:
            # Re-raise our custom errors immediately (no point retrying a bad key)
            raise
        except requests.exceptions.Timeout:
            log.warning("Request timed out (attempt %d/%d)", attempt, MAX_RETRIES)
            last_error = ConnectionError("Request timed out. Check your internet connection.")
            time.sleep(RETRY_DELAY)
        except requests.exceptions.ConnectionError:
            log.warning("No internet connection (attempt %d/%d)", attempt, MAX_RETRIES)
            last_error = ConnectionError("No internet connection. Please check your network.")
            time.sleep(RETRY_DELAY)
        except requests.exceptions.RequestException as e:
            log.error("Unexpected request error: %s", e)
            last_error = ConnectionError(f"Unexpected error: {e}")
            time.sleep(RETRY_DELAY)

    # All retries exhausted — raise the last error we saw
    raise last_error or ConnectionError("Request failed after multiple attempts.")


# ═════════════════════════════════════════════════════════════════════════════
#  PUBLIC FUNCTION 1: search for recipes by ingredients
# ═════════════════════════════════════════════════════════════════════════════
def search_recipes(ingredients, number=15, diet=""):
    """
    Search for recipes using a list of ingredients.

    ingredients : comma-separated string, e.g. "chicken, rice, tomato"
    number      : how many results to return (max 15 on free plan)
    diet        : optional filter — "vegan", "vegetarian", or "gluten free"

    Returns: list of recipe dicts, each with at least {id, title, image}

    TWO ENDPOINTS — WHY?
      Spoonacular has two different search endpoints:
      • findByIngredients — fast, ingredient-focused, no diet filter support
      • complexSearch     — slower, supports dietary filters, richer filtering
      We choose between them based on whether a diet filter is active.
    """
    # ── Sanitize ingredients input ────────────────────────────────────────────
    # Strip leading/trailing whitespace from each ingredient so "chicken , rice"
    # becomes "chicken,rice" — the API is picky about extra spaces
    cleaned = ", ".join(i.strip() for i in ingredients.split(",") if i.strip())
    if not cleaned:
        log.warning("search_recipes called with empty ingredients after cleaning")
        return []

    log.info("Search | ingredients=%r  diet=%r", cleaned, diet)

    if diet:
        # ── complexSearch endpoint ────────────────────────────────────────────
        # Returns: {"results": [{id, title, image}, ...], "totalResults": N}
        # addRecipeInformation=False keeps the response small (faster)
        result = _get("/recipes/complexSearch", {
            "includeIngredients": cleaned,
            "diet": diet,
            "number": number,
            "addRecipeInformation": False,
        })
        # Validate response structure — guard against unexpected API changes
        if not isinstance(result, dict):
            log.error("complexSearch returned unexpected type: %s", type(result))
            return []
        results = result.get("results", [])
        log.info("complexSearch returned %d results", len(results))
        return results

    # ── findByIngredients endpoint ────────────────────────────────────────────
    # Returns: [{id, title, image, usedIngredients, missedIngredients}, ...]
    # ranking=1  → sort by maximising used ingredients (best matches first)
    # ignorePantry=True → don't count salt/water/oil as "missing" ingredients
    result = _get("/recipes/findByIngredients", {
        "ingredients": cleaned,
        "number": number,
        "ranking": 1,
        "ignorePantry": True,
    })
    # Validate — this endpoint returns a list directly (not wrapped in a dict)
    if not isinstance(result, list):
        log.error("findByIngredients returned unexpected type: %s", type(result))
        return []
    log.info("findByIngredients returned %d results", len(result))
    return result


# ═════════════════════════════════════════════════════════════════════════════
#  PUBLIC FUNCTION 2: get full details for one recipe
# ═════════════════════════════════════════════════════════════════════════════
def get_recipe_info(recipe_id):
    """
    Get full recipe details: ingredients, instructions, dietary tags, image.

    recipe_id : the numeric ID from the search results

    Returns: dict with all recipe data

    WHAT DATA COMES BACK:
      The response is a large dict containing:
      - title, image, readyInMinutes, servings
      - vegetarian, vegan, glutenFree, dairyFree  (True/False flags)
      - extendedIngredients  → list of {original, name, amount, unit, ...}
      - analyzedInstructions → list of {steps: [{number, step}, ...]}
      - summary              → HTML string (fallback if no analyzedInstructions)
    """
    log.info("Info | id=%s", recipe_id)
    data = _get(f"/recipes/{recipe_id}/information", {"includeNutrition": False})

    # Validate response — should be a dict with at least a title
    if not isinstance(data, dict):
        raise ValueError(f"Unexpected response type for recipe {recipe_id}: {type(data)}")
    if "title" not in data:
        raise ValueError(f"Recipe {recipe_id} response missing expected fields")

    return data


# ═════════════════════════════════════════════════════════════════════════════
#  PUBLIC FUNCTION 3: get nutrition data for one recipe
# ═════════════════════════════════════════════════════════════════════════════
def get_nutrition(recipe_id):
    """
    Get nutrition information for a recipe.

    recipe_id : the numeric ID from the search results

    Returns: dict with these keys:
      "calories"  — e.g. "543"         (string, just the number)
      "protein"   — e.g. "34g"         (string with unit)
      "carbs"     — e.g. "60g"
      "fat"       — e.g. "20g"
      "bad"       — list of nutrients to limit (sodium, sugar, fat, etc.)
      "good"      — list of beneficial nutrients (vitamins, fibre, etc.)

    Each item in "bad"/"good" looks like:
      {"title": "Saturated Fat", "amount": "4.5g", "percentOfDailyNeeds": 22.4}

    WHY nutritionWidget.json?
      Spoonacular has multiple nutrition endpoints.  This one returns a clean
      summary (top-level calories/protein/carbs/fat) PLUS detailed breakdown
      in bad/good lists — perfect for building the chart without needing to
      make two separate API calls.
    """
    log.info("Nutrition | id=%s", recipe_id)
    data = _get(f"/recipes/{recipe_id}/nutritionWidget.json", {})

    # Validate — should be a dict with at least "calories"
    if not isinstance(data, dict):
        raise ValueError(f"Unexpected nutrition response type: {type(data)}")
    if "calories" not in data:
        log.warning("Nutrition response missing 'calories' key — fields: %s",
                    list(data.keys())[:8])

    return data


# ═════════════════════════════════════════════════════════════════════════════
#  PUBLIC FUNCTION 4: download a recipe image (with disk caching)
# ═════════════════════════════════════════════════════════════════════════════
def fetch_image(url):
    """
    Download an image from a URL and return its raw bytes (PNG/JPG data).

    CACHING: The first time we download an image, we save it to the cache/
    folder. Next time the same image is requested, we load it from disk
    instead of downloading it again. This saves API quota and is much faster.

    url : the image URL from the recipe data

    Returns: bytes (the raw image file) or None if download failed
    """
    if not url:
        return None

    # ── Check cache first ─────────────────────────────────────────────────────
    # We create a unique filename based on the URL using MD5 hashing.
    # MD5 turns a long URL into a short fixed-length string like "a3f8e1c2..."
    cache_key  = hashlib.md5(url.encode()).hexdigest()
    cache_path = os.path.join(CACHE_DIR, f"{cache_key}.img")

    if os.path.exists(cache_path):
        # Cache hit — load from disk (instant, no network needed)
        log.debug("Image cache hit: %s", cache_key)
        with open(cache_path, "rb") as f:
            return f.read()

    # ── Cache miss — download from internet ───────────────────────────────────
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        image_bytes = response.content

        # Save to cache for next time
        with open(cache_path, "wb") as f:
            f.write(image_bytes)
        log.debug("Image downloaded and cached: %s", cache_key)
        return image_bytes

    except Exception as e:
        log.warning("Image fetch failed for %s: %s", url, e)
        return None
