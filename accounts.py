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
# Allergen choices on the profile come from the country labeling rules in regional_allergens.py
from regional_allergens import USER_ALLERGEN_MAIN as ALLERGEN_MAIN, USER_ALLERGEN_OTHER as ALLERGEN_OTHER
ALLERGEN_CHOICES = ALLERGEN_MAIN + ALLERGEN_OTHER

USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{3,30}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
TIMEOUT = 15

URL = ANON_KEY = SERVICE_KEY = ""
TIMEZONE = os.environ.get("CUISYNC_TIMEZONE", "Asia/Singapore")  # for "searches per day"


class SupabaseError(ValueError):
    """Supabase is not set up or did not answer. The message is safe to show."""


PASSWORD_MIN = 8
PASSWORD_RULE = f"at least {PASSWORD_MIN} characters, with at least one letter and one number"


def check_password(password):
    """Raises SupabaseError when the password is too weak (used for every new password)."""
    password = password or ""
    if (len(password) < PASSWORD_MIN or not re.search(r"[A-Za-z]", password)
            or not re.search(r"[0-9]", password)):
        raise SupabaseError(f"Your password needs {PASSWORD_RULE}.")


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


def _call(method, path, key=None, params=None, json=None, prefer=None, ok=(200, 201, 204), extra_auth=None):
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
    extra = {"Prefer": prefer} if prefer else {}
    if extra_auth:   # a signed-in user's token (the apikey header still identifies the project)
        extra["Authorization"] = f"Bearer {extra_auth}"
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
    user["avoid_allergens"] = list(user.get("avoid_allergens") or [])
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
            raise EmailNotConfirmed("Please confirm your email first (check your inbox).")
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


# ---------------------------------------------------------------------------
# Sign up with email confirmation
#   1. start_signup()  - Supabase Auth makes the (not yet confirmed) user and emails a
#                        confirmation link (and a 6-digit code if the email template has one)
#   2. the user clicks the link, or types the code on the "Check your email" page
#   3. confirm_with_code() / confirm_with_token_hash() / user_from_access_token()
#      return the user id once the email is confirmed; only then can they log in
# Needs "Confirm email" turned on in Supabase (Authentication > Sign In / Providers > Email).
# ---------------------------------------------------------------------------

class EmailNotConfirmed(SupabaseError):
    """The account exists but its email is not confirmed yet."""


def _signup_error(response):
    """Friendlier messages for the email-sending errors of /auth/v1/signup and /resend."""
    try:
        data = response.json()
    except ValueError:
        data = {}
    text = " ".join(str((data or {}).get(k, "")) for k in ("msg", "message", "error_description", "error", "error_code", "code")).lower()
    if "rate limit" in text or "over_email_send_rate_limit" in text or response.status_code == 429:
        return SupabaseError("Too many confirmation emails were sent just now. Please wait a few minutes and try again.")
    if "not authorized" in text or "email_address_not_authorized" in text:
        print("[CuiSync] Supabase could not send the confirmation email: set up custom SMTP in "
              "Supabase (Authentication > Emails > SMTP Settings). The built-in sender only emails team members.")
        return SupabaseError("We couldn't send the confirmation email right now. Please try again later.")
    if "error sending" in text or "smtp" in text:
        print(f"[CuiSync] Supabase email error: {text.strip()}")
        return SupabaseError("We couldn't send the confirmation email right now. Please try again later.")
    return _error_from(response)


def _auth_user(user_id):
    """The Supabase Auth record (email_confirmed_at, created_at, ...), or None."""
    response = _call("GET", f"/auth/v1/admin/users/{user_id}", ok=(200, 404))
    return response.json() if response.status_code == 200 else None


def start_signup(username, password, display_name="", email="", redirect_to="", privacy_accepted_at=""):
    """Step 1: checks the form, makes the unconfirmed Supabase user and sends the email.
    Returns "sent" (check your email) or "active" (Supabase has email confirmation off),
    together with the user id."""
    username = (username or "").strip()
    email = (email or "").strip().lower()
    if not USERNAME_RE.match(username):
        raise SupabaseError("Username must be 3-30 characters: letters, numbers, . or _")
    if not EMAIL_RE.match(email):
        raise SupabaseError("Please enter a valid email address.")
    check_password(password)
    taken = _profile_by("username", username.lower())
    if taken:
        auth = _auth_user(taken["id"]) or {}
        unconfirmed = not auth.get("email_confirmed_at")
        if unconfirmed and (taken.get("email") or "").lower() == email:
            pass   # the same person signing up again: Supabase sends a new email below
        elif unconfirmed and _older_than(auth.get("created_at"), days=1):
            # Never confirmed in a day: free the username again
            _call("DELETE", f"/auth/v1/admin/users/{taken['id']}", ok=(200, 204, 404))
        else:
            raise SupabaseError("That username is already taken.")
    params = {"redirect_to": redirect_to} if redirect_to else None
    response = _call("POST", "/auth/v1/signup", key=ANON_KEY, params=params, json={
        "email": email, "password": password,
        # privacy_accepted_at: when they ticked "I agree to the Privacy Policy" (Data Privacy Act)
        "data": {"name": display_name.strip()[:60], "username": username.lower(),
                 "privacy_accepted_at": privacy_accepted_at},
    }, ok=(200, 201, 400, 422, 429, 500))
    if response.status_code not in (200, 201):
        raise _signup_error(response)
    data = response.json()
    user = data.get("user") or data
    # An email that already has a confirmed account comes back with no identities
    # (Supabase hides that the account exists).
    if isinstance(user.get("identities"), list) and not user["identities"]:
        raise SupabaseError("An account with this email already exists. Please log in instead.")
    user_id = user.get("id")
    # The on_auth_user_created trigger made the profile; make sure the name/username are set
    _rest("PATCH", "profiles", params={"id": f"eq.{user_id}"}, prefer="return=minimal",
          json={"username": username.lower(), "name": display_name.strip()[:60], "email": email})
    return ("active" if data.get("access_token") else "sent"), user_id


def _older_than(stamp, days):
    try:
        made = datetime.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return False
    return datetime.datetime.now(datetime.timezone.utc) - made > datetime.timedelta(days=days)


def resend_signup(email):
    response = _call("POST", "/auth/v1/resend", key=ANON_KEY, json={"type": "signup", "email": (email or "").strip().lower()},
                     ok=(200, 400, 422, 429, 500))
    if response.status_code != 200:
        raise _signup_error(response)


def _verified_user_id(response):
    if response.status_code != 200:
        return None
    data = response.json()
    return (data.get("user") or {}).get("id") or data.get("id")


def confirm_with_code(email, code):
    """The 6-digit code from the email. Returns the user id, or None for a wrong/expired code."""
    code = re.sub(r"\s", "", code or "")
    if not re.fullmatch(r"\d{6,10}", code):
        return None
    for kind in ("email", "signup"):
        response = _call("POST", "/auth/v1/verify", key=ANON_KEY, ok=(200, 400, 401, 403, 404, 422),
                         json={"type": kind, "email": (email or "").strip().lower(), "token": code})
        user_id = _verified_user_id(response)
        if user_id:
            return user_id
    return None


def confirm_with_token_hash(token_hash, kind="email"):
    """The link in the email when it points to /register/confirm?token_hash=..."""
    kind = kind if kind in ("email", "signup") else "email"
    response = _call("POST", "/auth/v1/verify", key=ANON_KEY, ok=(200, 400, 401, 403, 404, 422),
                     json={"type": kind, "token_hash": token_hash})
    return _verified_user_id(response)


def user_from_access_token(access_token):
    """The default Supabase link confirms the email itself and comes back with a session in
    the address (#access_token=...). Returns the user id if that session is real and confirmed."""
    if not access_token:
        return None
    response = _call("GET", "/auth/v1/user", key=ANON_KEY, ok=(200, 401, 403),
                     extra_auth=access_token)
    if response.status_code != 200:
        return None
    data = response.json()
    return data.get("id") if data.get("email_confirmed_at") else None


# ---------------------------------------------------------------------------
# Log in with Google (Supabase's Google provider, PKCE flow: the code from Google is
# exchanged on the server, so no tokens sit in the browser's address bar)
# ---------------------------------------------------------------------------

def google_login_url(redirect_to, code_challenge):
    if not configured():
        init()
    from urllib.parse import urlencode
    return URL + "/auth/v1/authorize?" + urlencode({
        "provider": "google", "redirect_to": redirect_to,
        "code_challenge": code_challenge, "code_challenge_method": "s256",
    })


def google_finish(auth_code, code_verifier):
    """Returns the user id after Google sign-in, making sure the profile has a username."""
    response = _call("POST", "/auth/v1/token", key=ANON_KEY, params={"grant_type": "pkce"},
                     json={"auth_code": auth_code, "code_verifier": code_verifier}, ok=(200, 400, 401, 403, 404))
    if response.status_code != 200:
        return None
    user = response.json().get("user") or {}
    user_id = user.get("id")
    if not user_id:
        return None
    profile = _profile_by("id", user_id) or {}
    meta = user.get("user_metadata") or {}
    if not profile.get("username"):
        base = re.sub(r"[^a-z0-9._]", "", (user.get("email") or "user").split("@")[0].lower())[:24] or "user"
        base = base if len(base) >= 3 else (base + "user")[:24]
        username, n = base, 1
        while _profile_by("username", username):
            n += 1
            username = f"{base}{n}"
        _rest("POST", "profiles", params={"on_conflict": "id"}, prefer="resolution=merge-duplicates,return=minimal",
              json={"id": user_id, "email": (user.get("email") or "").lower(), "username": username,
                    "name": (profile.get("name") or meta.get("full_name") or meta.get("name") or "")[:60]})
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
    # Food preferences: only the allergens (avoid_allergens column of profiles)
    allergens = [a for a in form.getlist("avoid_allergens") if a in ALLERGEN_CHOICES]
    changes = {"avoid_allergens": allergens}
    # The profile page's preferences form has no name box: only change the name when it's sent
    if "display_name" in form:
        changes["name"] = form.get("display_name", "").strip()[:60]
    _rest("PATCH", "profiles", params={"id": f"eq.{user_id}"}, json=changes)


def update_names(user_id, display_name, username):
    """Profile header: change the display name and the username (@handle)."""
    if not _valid_id(user_id):
        raise SupabaseError("Account not found.")
    username = (username or "").strip().lstrip("@").lower()
    if not USERNAME_RE.match(username):
        raise SupabaseError("Username must be 3-30 characters: letters, numbers, dots ( . ) or underscores ( _ ).")
    other = _profile_by("username", username)
    if other and str(other.get("id")) != str(user_id):
        raise SupabaseError("That username is already taken.")
    _rest("PATCH", "profiles", params={"id": f"eq.{user_id}"}, prefer="return=minimal",
          json={"name": (display_name or "").strip()[:60], "username": username})


def _user_token(email, password):
    """Logs in as the user (email + password) and returns their access token, or None."""
    response = _call("POST", "/auth/v1/token", key=ANON_KEY, params={"grant_type": "password"},
                     json={"email": email, "password": password or ""}, ok=(200, 400, 401))
    return response.json().get("access_token") if response.status_code == 200 else None


def _email_of(user_id):
    user = get_user(user_id)
    if not user:
        raise SupabaseError("Account not found.")
    return user.get("email") or _call("GET", f"/auth/v1/admin/users/{user_id}").json().get("email")


def _update_as_user(token, changes, redirect_to=""):
    """Changes the account the way the user would (PUT /auth/v1/user with their own token).
    That's what makes Supabase send its security emails ("Your password was changed",
    "Confirm email change")."""
    params = {"redirect_to": redirect_to} if redirect_to else None
    response = _call("PUT", "/auth/v1/user", key=ANON_KEY, params=params, json=changes, extra_auth=token,
                     ok=(200, 400, 401, 403, 422, 429, 500))
    if response.status_code != 200:
        raise _signup_error(response)
    return response.json()


def change_password(user_id, old, new):
    token = _user_token(_email_of(user_id), old)
    if not token:
        raise SupabaseError("Your current password is not correct.")
    check_password(new)
    if old == new:
        raise SupabaseError("The new password must be different from the current one.")
    _update_as_user(token, {"password": new})


def sign_in_methods(user_id):
    """["email"] for email + password accounts, ["google"] for Google-only accounts, or both."""
    response = _call("GET", f"/auth/v1/admin/users/{user_id}", ok=(200, 404))
    if response.status_code != 200:
        return []
    data = response.json()
    providers = (data.get("app_metadata") or {}).get("providers") or []
    if not providers:
        providers = [i.get("provider") for i in data.get("identities") or [] if i.get("provider")]
    return providers or ["email"]


def change_email(user_id, password, new_email, redirect_to=""):
    """Supabase emails a confirmation link to the new address (and the old one too, if
    "Secure email change" is on). The email only changes once it's confirmed."""
    new_email = (new_email or "").strip().lower()
    if not EMAIL_RE.match(new_email):
        raise SupabaseError("Please enter a valid email address.")
    current = (_email_of(user_id) or "").lower()
    if new_email == current:
        raise SupabaseError("That is already your email address.")
    if _profile_by("email", new_email):
        raise SupabaseError("That email is already used by another account.")
    token = _user_token(current, password)
    if not token:
        raise SupabaseError("Your password is not correct.")
    _update_as_user(token, {"email": new_email}, redirect_to=redirect_to)


def delete_own_account(user_id, password=None):
    """The user deletes their own account (profile and search history go with it)."""
    if "email" in sign_in_methods(user_id):
        if not _user_token(_email_of(user_id), password):
            raise SupabaseError("Your password is not correct.")
    delete_user(user_id)


# ---------------------------------------------------------------------------
# Forgot password
#   1. send_password_reset() - Supabase emails a reset link (and a code if the template has one)
#   2. the link / code proves it's their inbox: reset_user_from_code() / reset_user_from_token_hash()
#      / user_from_access_token() return the user id
#   3. set_new_password() saves the new password
# ---------------------------------------------------------------------------

def send_password_reset(email, redirect_to=""):
    """Always looks the same to the user, whether or not the email has an account
    (so the form can't be used to find out who has one)."""
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise SupabaseError("Please enter a valid email address.")
    params = {"redirect_to": redirect_to} if redirect_to else None
    response = _call("POST", "/auth/v1/recover", key=ANON_KEY, params=params, json={"email": email},
                     ok=(200, 400, 404, 422, 429, 500))
    if response.status_code == 200:
        return
    error = _signup_error(response)
    if "Too many" in str(error) or "couldn't send" in str(error):
        raise error
    # Anything else (e.g. no such user) is not shown, for the reason above


def reset_user_from_code(email, code):
    code = re.sub(r"\s", "", code or "")
    if not re.fullmatch(r"\d{6,10}", code):
        return None
    response = _call("POST", "/auth/v1/verify", key=ANON_KEY, ok=(200, 400, 401, 403, 404, 422),
                     json={"type": "recovery", "email": (email or "").strip().lower(), "token": code})
    return _verified_user_id(response)


def reset_user_from_token_hash(token_hash):
    """Returns (user id, access token) for a good reset link, or (None, None)."""
    response = _call("POST", "/auth/v1/verify", key=ANON_KEY, ok=(200, 400, 401, 403, 404, 422),
                     json={"type": "recovery", "token_hash": token_hash})
    user_id = _verified_user_id(response)
    return (user_id, response.json().get("access_token")) if user_id else (None, None)


def recovery_user_from_access_token(access_token):
    """Like user_from_access_token, for the reset link (the email counts as confirmed by it)."""
    if not access_token:
        return None
    response = _call("GET", "/auth/v1/user", key=ANON_KEY, ok=(200, 401, 403), extra_auth=access_token)
    return response.json().get("id") if response.status_code == 200 else None


def set_new_password(user_id, password, token=None):
    """Forgot password, last step. With the user's token (from the reset link) Supabase also
    sends its "Your password was changed" email."""
    check_password(password)
    if not _valid_id(user_id):
        raise SupabaseError("Account not found.")
    if token:
        try:
            _update_as_user(token, {"password": password})
            return
        except SupabaseError as error:
            if "different" in str(error).lower():
                raise
    _call("PUT", f"/auth/v1/admin/users/{user_id}", json={"password": password})


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
