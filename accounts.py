"""User accounts, profiles and search history for CuiSync, stored in Supabase.

  Supabase Auth            - email + password login (auth.users)
  public.profiles          - username, name, role (user / admin), status, and the profile fields
  public.search_history    - every search a logged-in user makes, so they can open it again

Run supabase_setup.sql once in the Supabase SQL Editor, then put these in .env:

    SUPABASE_URL=https://<project>.supabase.co
    SUPABASE_ANON_KEY=<anon / publishable key>
    SUPABASE_SERVICE_ROLE_KEY=<service_role / secret key>   (server only, never in the browser)

The app talks to Supabase from the server with the service key (Flask keeps the login in its own
session cookie), so Row Level Security stays on and the browser never gets a Supabase key.

The functions keep the names the app already used with SQLite (authenticate, get_user, ...).
User ids are now Supabase UUIDs (text) instead of numbers.
"""
import datetime
import os
import re

import requests

# Always read the .env next to this file (not whichever folder the app was started from).
# override=True: an empty SUPABASE_... variable already set in Windows can't hide the .env value.
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
try:
    from dotenv import load_dotenv
    load_dotenv(ENV_PATH, override=True)
except ImportError:
    print("[CuiSync] python-dotenv is not installed in this Python, so .env is not read. "
          "Activate the venv or run: pip install python-dotenv")

ROLES = ("user", "admin")
COOKING_ROLES = ("Home cook", "Chef", "Student", "Food researcher", "Other")
ALLERGEN_CHOICES = ("Peanuts", "Tree nuts", "Milk", "Egg", "Fish", "Shellfish", "Soy", "Wheat/Gluten", "Sesame")

USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{3,30}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
TIMEOUT = 15

URL = ANON_KEY = SERVICE_KEY = ""
TIMEZONE = os.environ.get("CUISYNC_TIMEZONE", "Asia/Singapore")  # for "searches per day"


class SupabaseError(ValueError):
    """Supabase is not set up or did not answer. The message is safe to show."""


def init(_data_dir=None):
    """Read the Supabase settings. The site still runs without them (search works, accounts don't)."""
    global URL, ANON_KEY, SERVICE_KEY
    URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not configured():
        missing = [name for name, value in (("SUPABASE_URL", URL), ("SUPABASE_ANON_KEY", ANON_KEY),
                                            ("SUPABASE_SERVICE_ROLE_KEY", SERVICE_KEY)) if not value]
        print(f"[CuiSync] Supabase is not set up: missing {', '.join(missing)} "
              f"(looked in {ENV_PATH}{'' if os.path.exists(ENV_PATH) else ', which does not exist'}). "
              "Log in / accounts won't work until then.")
    else:
        print(f"[CuiSync] Supabase settings found ({URL}).")


def configured():
    return bool(URL and ANON_KEY and SERVICE_KEY)


# ---------------------------------------------------------------------------
# Talking to Supabase
# ---------------------------------------------------------------------------

def _headers(key, extra=None):
    headers = {"apikey": key, "Content-Type": "application/json"}
    if key.startswith("eyJ"):  # legacy JWT keys also go in Authorization; new sb_ keys only in apikey
        headers["Authorization"] = f"Bearer {key}"
    headers.update(extra or {})
    return headers


def _call(method, path, key=None, params=None, json=None, prefer=None, ok=(200, 201, 204)):
    if not configured():
        # Read .env again before giving up (in case the settings were added after the app started)
        try:
            from dotenv import load_dotenv
            load_dotenv(ENV_PATH, override=True)
        except ImportError:
            pass
        init()
    if not configured():
        raise SupabaseError("Accounts are not set up yet (Supabase settings are missing in .env).")
    extra = {"Prefer": prefer} if prefer else None
    try:
        response = requests.request(method, URL + path, headers=_headers(key or SERVICE_KEY, extra),
                                    params=params, json=json, timeout=TIMEOUT)
    except requests.RequestException:
        raise SupabaseError("Can't reach the account server right now. Please try again.")
    if response.status_code not in ok:
        raise _error_from(response)
    return response


def _error_from(response):
    try:
        data = response.json()
    except ValueError:
        data = {}
    if not isinstance(data, dict):   # some replies are a list, not an error object
        data = {}
    text = " ".join(str(data.get(k, "")) for k in ("msg", "message", "error_description", "error", "code", "details"))
    lowered = text.lower()
    if "already" in lowered and ("registered" in lowered or "exists" in lowered):
        return SupabaseError("That email is already registered.")
    if "profiles_username" in lowered or ("duplicate" in lowered and "username" in lowered):
        return SupabaseError("That username is already taken.")
    if "password" in lowered and ("weak" in lowered or "short" in lowered or "characters" in lowered):
        return SupabaseError("That password is too weak. Use at least 6 characters.")
    if "email" in lowered and ("invalid" in lowered or "format" in lowered):
        return SupabaseError("Please enter a valid email address.")
    if response.status_code in (401, 403) and ("api key" in lowered or "jwt" in lowered or "apikey" in lowered):
        return SupabaseError("The Supabase keys in .env are not valid.")
    print(f"[CuiSync] Supabase error {response.status_code}: {text.strip() or response.text[:300]}")
    return SupabaseError("Something went wrong with the account server. Please try again.")


def _rest(method, table, **kwargs):
    return _call(method, f"/rest/v1/{table}", **kwargs)


def _rpc(name, **args):
    return _call("POST", f"/rest/v1/rpc/{name}", json=args).json()


def _count(table, params=None):
    # With "count=exact" and a limit, Supabase answers 206 (Partial Content) once there are more rows than the limit
    response = _rest("GET", table, params=dict(params or {}, select="id", limit="1"), prefer="count=exact",
                     ok=(200, 206))
    total = response.headers.get("Content-Range", "*/0").split("/")[-1]
    return int(total) if total.isdigit() else 0


def _valid_id(user_id):
    return bool(user_id) and bool(UUID_RE.match(str(user_id)))


def _utc_text(value):
    """'2026-11-16T07:30:00.123+00:00' -> '2026-11-16 07:30:00' (UTC, like the old SQLite dates)."""
    if not value:
        return ""
    try:
        when = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if when.tzinfo:
            when = when.astimezone(datetime.timezone.utc)
        return when.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return str(value)[:19].replace("T", " ")


def _user_dict(row):
    if not row:
        return None
    user = dict(row)
    user["id"] = str(user["id"])
    user["username"] = user.get("username") or (user.get("email") or "user").split("@")[0]
    user["display_name"] = user.get("name") or ""
    user["name"] = user["display_name"] or user["username"]
    user["favorite_cuisines"] = list(user.get("favorite_cuisines") or [])
    user["avoid_allergens"] = list(user.get("avoid_allergens") or [])
    for key in ("cooking_role", "home_country", "bio"):
        user[key] = user.get(key) or ""
    user["is_admin"] = user.get("role") == "admin"
    user["is_active"] = (user.get("status") or "active") == "active"
    user["created_at"] = _utc_text(user.get("created_at"))
    return user


def _history_dict(row):
    entry = dict(row)
    entry["user_id"] = str(entry.get("user_id", ""))
    entry["created_at"] = _utc_text(entry.get("created_at"))
    return entry


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

def _profile_by(column, value):
    rows = _rest("GET", "profiles", params={column: f"eq.{value}", "select": "*", "limit": "1"}).json()
    return rows[0] if rows else None


def _email_for_login(login):
    """The login box takes an email or a username."""
    login = (login or "").strip()
    if "@" in login:
        return login.lower()
    if not USERNAME_RE.match(login):
        return None
    row = _profile_by("username", login.lower())
    return (row or {}).get("email")


def authenticate(login, password):
    """Email or username + password. Returns the user, or None for a wrong login.
    Raises SupabaseError for a disabled account or when Supabase can't be reached."""
    email = _email_for_login(login)
    if not email or not password:
        return None
    response = _call("POST", "/auth/v1/token", key=ANON_KEY, params={"grant_type": "password"},
                     json={"email": email, "password": password}, ok=(200, 400, 401))
    if response.status_code != 200:
        data = response.json() if response.content else {}
        if "not confirmed" in str(data).lower():
            raise SupabaseError("Please confirm your email first (check your inbox).")
        return None
    user = get_user(response.json()["user"]["id"])
    if user and not user["is_active"]:
        raise SupabaseError("This account is disabled. Please contact the CuiSync team.")
    return user


def create_user(username, password, role="user", display_name="", email=""):
    """Sign up: makes the Supabase Auth user (email confirmed) and fills in the profile."""
    username = (username or "").strip()
    email = (email or "").strip().lower()
    if not USERNAME_RE.match(username):
        raise SupabaseError("Username must be 3-30 characters: letters, numbers, . or _")
    if not EMAIL_RE.match(email):
        raise SupabaseError("Please enter a valid email address.")
    if len(password or "") < 6:
        raise SupabaseError("Password must be at least 6 characters.")
    role = role if role in ROLES else "user"
    if _profile_by("username", username.lower()):
        raise SupabaseError("That username is already taken.")
    created = _call("POST", "/auth/v1/admin/users", json={
        "email": email, "password": password, "email_confirm": True,
        "user_metadata": {"name": display_name.strip()[:60], "username": username.lower()},
    }).json()
    user_id = created.get("id") or (created.get("user") or {}).get("id")
    profile = {"id": user_id, "username": username.lower(), "email": email,
               "name": display_name.strip()[:60], "role": role}
    try:
        # The on_auth_user_created trigger makes the row; this fills in / makes sure of the rest.
        _rest("POST", "profiles", json=profile, params={"on_conflict": "id"},
              prefer="resolution=merge-duplicates,return=minimal")
    except SupabaseError:
        _call("DELETE", f"/auth/v1/admin/users/{user_id}", ok=(200, 204, 404))
        raise
    return user_id


def ensure_admin(username, password, email):
    """Create the first admin account if Supabase has no admin yet. Returns True when it made one."""
    if not configured():
        return False
    try:
        if _count("profiles", {"role": "eq.admin"}):
            return False
        existing = _profile_by("username", (username or "").lower())
        if existing:  # the username exists already: make it the admin
            _rest("PATCH", "profiles", params={"id": f"eq.{existing['id']}"}, json={"role": "admin"})
            print(f"[CuiSync] Made @{existing.get('username')} the admin.")
            return False
        create_user(username, password, role="admin", display_name="Administrator", email=email)
        return True
    except SupabaseError as error:
        print("[CuiSync] Could not create the admin account:", error)
        return False


def get_user(user_id):
    if not _valid_id(user_id):
        return None
    return _user_dict(_profile_by("id", user_id))


def list_users():
    rows = _rest("GET", "profiles", params={"select": "*", "order": "created_at.desc"}).json()
    counts = {str(r["user_id"]): r["n"] for r in _rpc("cuisync_search_counts")}
    return [dict(_user_dict(r), searches=counts.get(str(r["id"]), 0)) for r in rows]


def update_profile(user_id, form):
    if not _valid_id(user_id):
        return
    favorite = [c for c in form.getlist("favorite_cuisines") if c][:13]
    allergens = [a for a in form.getlist("avoid_allergens") if a in ALLERGEN_CHOICES]
    cooking_role = form.get("cooking_role", "")
    if cooking_role not in COOKING_ROLES:
        cooking_role = ""
    _rest("PATCH", "profiles", params={"id": f"eq.{user_id}"}, json={
        "name": form.get("display_name", "").strip()[:60],
        "cooking_role": cooking_role,
        "home_country": form.get("home_country", "").strip()[:60],
        "favorite_cuisines": favorite,
        "avoid_allergens": allergens,
        "bio": form.get("bio", "").strip()[:500],
    })


def change_password(user_id, old, new):
    user = get_user(user_id)
    if not user:
        raise SupabaseError("Account not found.")
    email = user.get("email") or _call("GET", f"/auth/v1/admin/users/{user_id}").json().get("email")
    check = _call("POST", "/auth/v1/token", key=ANON_KEY, params={"grant_type": "password"},
                  json={"email": email, "password": old or ""}, ok=(200, 400, 401))
    if check.status_code != 200:
        raise SupabaseError("Your current password is not correct.")
    if len(new or "") < 6:
        raise SupabaseError("The new password must be at least 6 characters.")
    _call("PUT", f"/auth/v1/admin/users/{user_id}", json={"password": new})


def set_role(user_id, role):
    if role not in ROLES:
        raise SupabaseError("Unknown role.")
    if not _valid_id(user_id):
        raise SupabaseError("Account not found.")
    if role == "user" and not _count("profiles", {"role": "eq.admin", "id": f"neq.{user_id}"}):
        raise SupabaseError("There must be at least one admin.")
    _rest("PATCH", "profiles", params={"id": f"eq.{user_id}"}, json={"role": role})


def set_status(user_id, status):
    if status not in ("active", "disabled") or not _valid_id(user_id):
        raise SupabaseError("Unknown status.")
    if status == "disabled" and (get_user(user_id) or {}).get("is_admin") \
            and not _count("profiles", {"role": "eq.admin", "status": "eq.active", "id": f"neq.{user_id}"}):
        raise SupabaseError("You can't disable the last admin.")
    _rest("PATCH", "profiles", params={"id": f"eq.{user_id}"}, json={"status": status})


def delete_user(user_id):
    """Deletes the Supabase Auth user; the profile and search history go with it (ON DELETE CASCADE)."""
    user = get_user(user_id)
    if not user:
        return
    if user["is_admin"] and _count("profiles", {"role": "eq.admin"}) <= 1:
        raise SupabaseError("You can't delete the last admin.")
    _call("DELETE", f"/auth/v1/admin/users/{user_id}", ok=(200, 204, 404))


# ---------------------------------------------------------------------------
# Search history
# ---------------------------------------------------------------------------

HISTORY_FIELDS = ("search_type", "query", "dish_name", "origin_country", "target_country",
                  "recipe_id", "result_title", "result_country", "score")


def add_history(user_id, **entry):
    if not _valid_id(user_id):
        return
    values = {f: (entry.get(f) if f == "score" else str(entry.get(f) or "")) for f in HISTORY_FIELDS}
    last = _rest("GET", "search_history", params={"user_id": f"eq.{user_id}", "select": "*",
                                                  "order": "id.desc", "limit": "1"}).json()
    # Same search twice in a row = just refresh the time
    if last and all(str(last[0].get(f) or "") == str(values[f] or "") for f in HISTORY_FIELDS[:6]):
        _rest("PATCH", "search_history", params={"id": f"eq.{last[0]['id']}"}, json={
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "result_title": values["result_title"], "result_country": values["result_country"],
            "score": values["score"]})
        return
    _rest("POST", "search_history", json=dict(values, user_id=user_id), prefer="return=minimal")


def get_history(user_id, limit=200):
    if not _valid_id(user_id):
        return []
    rows = _rest("GET", "search_history", params={"user_id": f"eq.{user_id}", "select": "*",
                                                  "order": "created_at.desc,id.desc", "limit": str(limit)}).json()
    return [_history_dict(r) for r in rows]


def get_history_entry(user_id, entry_id):
    if not _valid_id(user_id):
        return None
    rows = _rest("GET", "search_history", params={"user_id": f"eq.{user_id}", "id": f"eq.{int(entry_id)}",
                                                  "select": "*"}).json()
    return _history_dict(rows[0]) if rows else None


def delete_history(user_id, entry_id=None):
    if not _valid_id(user_id):
        return
    params = {"user_id": f"eq.{user_id}"}
    if entry_id is not None:
        params["id"] = f"eq.{int(entry_id)}"
    _rest("DELETE", "search_history", params=params)


def history_stats():
    return {"total_searches": _count("search_history"), "users": _count("profiles"),
            "top_dishes": _rpc("cuisync_top_dishes", top_n=5)}


def searches_per_day(days=7):
    """Number of saved searches on each of the last `days` days (oldest first), as [(date, n)]."""
    rows = _rpc("cuisync_searches_per_day", days=days, tz=TIMEZONE)
    found = {str(r["day"]): r["n"] for r in rows}
    today = datetime.datetime.now(datetime.timezone.utc).astimezone().date()
    try:
        from zoneinfo import ZoneInfo
        today = datetime.datetime.now(ZoneInfo(TIMEZONE)).date()
    except Exception:
        pass
    first = today - datetime.timedelta(days=days - 1)
    return [(first + datetime.timedelta(days=i), found.get((first + datetime.timedelta(days=i)).isoformat(), 0))
            for i in range(days)]


def recent_searches(limit=8):
    """Latest searches by any logged-in user, for the admin dashboard."""
    rows = _rest("GET", "search_history", params={"select": "*,profiles(username,name,email)",
                                                  "order": "created_at.desc,id.desc", "limit": str(limit)}).json()
    out = []
    for r in rows:
        who = r.pop("profiles", None) or {}
        entry = _history_dict(r)
        entry["username"] = who.get("username") or (who.get("email") or "").split("@")[0]
        entry["user_name"] = who.get("name") or entry["username"]
        out.append(entry)
    return out


# ---------------- recent searches (opened comparisons, home page row) ----------------
# Table public.recent_comparisons (see supabase_setup.sql, section "Recent searches").
# If the table isn't made yet, these do nothing / return nothing instead of breaking pages.
RECENT_MAX = 100   # kept per user


def add_recent(user_id, pair_key, entry):
    """Save an opened comparison. The same pair moves back to the top instead of repeating."""
    if not _valid_id(user_id):
        return
    try:
        _rest("DELETE", "recent_comparisons", params={"user_id": f"eq.{user_id}", "pair_key": f"eq.{pair_key}"})
        _rest("POST", "recent_comparisons", json={"user_id": user_id, "pair_key": pair_key, "data": entry},
              prefer="return=minimal")
        # Keep only the newest RECENT_MAX
        old = _rest("GET", "recent_comparisons", params={
            "user_id": f"eq.{user_id}", "select": "id", "order": "id.desc",
            "offset": str(RECENT_MAX), "limit": "1000"}).json()
        if old:
            ids = ",".join(str(r["id"]) for r in old)
            _rest("DELETE", "recent_comparisons", params={"id": f"in.({ids})"})
    except SupabaseError as error:
        print("[CuiSync] Recent search not saved (run the Recent searches part of supabase_setup.sql):", error)


def list_recent(user_id):
    if not _valid_id(user_id):
        return []
    try:
        rows = _rest("GET", "recent_comparisons", params={
            "user_id": f"eq.{user_id}", "select": "data", "order": "id.desc", "limit": str(RECENT_MAX)}).json()
    except SupabaseError:
        return []
    return [r["data"] for r in rows if isinstance(r.get("data"), dict)]


def clear_recent(user_id):
    if not _valid_id(user_id):
        return
    try:
        _rest("DELETE", "recent_comparisons", params={"user_id": f"eq.{user_id}"})
    except SupabaseError:
        pass
