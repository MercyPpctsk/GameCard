"""Saving rooms so they survive restarts, sleep and redeploys.

The server's memory stays the source of truth (fast). This module copies each room
that changed to a database in the background, and loads it back the first time
somebody opens a room that is not in memory (for example after a restart).

Backends:
  SupabaseBackend    REST calls to a Supabase (PostgREST) table, key kept in Streamlit secrets
  AppsScriptBackend  a Google Sheet behind a small Apps Script web app (apps-script/rooms.gs)
  FileBackend        one JSON file per room, for tests and for running on your own PC
No backend configured -> RoomSaver.enabled is False and the app behaves as before.
"""
import copy
import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone

import requests

import holdem as hd

LOG = logging.getLogger("roomstore")
SAVE_INTERVAL_S = 1.0  # a room is written at most once per interval
HTTP_TIMEOUT_S = 6
APPS_SCRIPT_TIMEOUT_S = 20  # Apps Script can take a few seconds when it has been idle


class StoreError(Exception):
    """The database could not be reached. Never treat this as "room not found"."""


# ----------------------------------------------------------- room <-> JSON
def new_room(mode="dummy"):
    return {
        "mode": mode,
        "players": ["A", "B", "C", "D"] if mode == "poker" else ["A", "B", "C"],
        "history": [],
        "multiplier": 0.1,
        "dark_fee": 15.0,
        # poker cash-game ledger: buy-ins per player, chips left at cash-out
        "poker": {"chip_value": 1.0, "buyin_chips": 100, "buys": {}, "cash": {}},
        "holdem": hd.new_table(),
        "version": 0,
    }


def serialize_room(room):
    """A JSON-safe copy of the room. A Hold'em hand in progress is cancelled (chips returned)."""
    data = copy.deepcopy({k: v for k, v in room.items() if k not in ("version", "holdem")})
    data["holdem"] = hd.to_saveable(room["holdem"])
    return data


def restore_room(data):
    """Rebuild a room from saved JSON; missing keys fall back to the defaults."""
    room = new_room(data.get("mode", "dummy"))
    for key in room:
        if key in data and key != "version":
            room[key] = data[key]
    room["holdem"] = hd.ensure(room["holdem"])
    room["version"] = 0
    return room


# ---------------------------------------------------------------- backends
class FileBackend:
    def __init__(self, folder):
        self._folder = folder
        os.makedirs(folder, exist_ok=True)

    def _path(self, code):
        return os.path.join(self._folder, hashlib.sha1(code.encode("utf-8")).hexdigest() + ".json")

    def load(self, code):
        try:
            with open(self._path(code), encoding="utf-8") as f:
                return json.load(f)["data"]
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as e:
            raise StoreError(f"cannot read room file: {e}") from e

    def save(self, code, data):
        tmp = self._path(code) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"code": code, "data": data}, f)
        os.replace(tmp, self._path(code))  # atomic: a crash never leaves half a file

    def delete(self, code):
        try:
            os.remove(self._path(code))
        except FileNotFoundError:
            pass


class SupabaseBackend:
    """Table: rooms(code text primary key, data jsonb, updated_at timestamptz)."""

    def __init__(self, url, key, table="rooms"):
        self._base = f"{url.rstrip('/')}/rest/v1/{table}"
        self._headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def load(self, code):
        try:
            r = requests.get(
                self._base,
                params={"code": "eq." + code, "select": "data"},
                headers=self._headers,
                timeout=HTTP_TIMEOUT_S,
            )
        except requests.RequestException as e:
            raise StoreError(f"database unreachable: {type(e).__name__}") from e
        if r.status_code != 200:
            raise StoreError(f"database answered HTTP {r.status_code}")
        rows = r.json()
        return rows[0]["data"] if rows else None

    def save(self, code, data):
        body = {"code": code, "data": data, "updated_at": datetime.now(timezone.utc).isoformat()}
        headers = {**self._headers, "Prefer": "resolution=merge-duplicates,return=minimal"}
        try:
            r = requests.post(
                self._base,
                params={"on_conflict": "code"},
                headers=headers,
                data=json.dumps(body),
                timeout=HTTP_TIMEOUT_S,
            )
        except requests.RequestException as e:
            raise StoreError(f"database unreachable: {type(e).__name__}") from e
        if r.status_code not in (200, 201, 204):
            raise StoreError(f"database answered HTTP {r.status_code}: {r.text[:120]}")

    def delete(self, code):
        try:
            r = requests.delete(
                self._base, params={"code": "eq." + code}, headers=self._headers, timeout=HTTP_TIMEOUT_S
            )
        except requests.RequestException as e:
            raise StoreError(f"database unreachable: {type(e).__name__}") from e
        if r.status_code not in (200, 204):
            raise StoreError(f"database answered HTTP {r.status_code}")


class AppsScriptBackend:
    """Google Sheet storage through the Apps Script web app in apps-script/rooms.gs.

    Every call is a POST with the shared token in the body (not in the URL). Google answers a
    POST with a redirect to the result page, which `requests` follows.
    """

    save_interval = 3.0  # each call takes a second or two, so write a room less often

    def __init__(self, url, token):
        self._url = url
        self._token = token

    def _call(self, payload):
        try:
            r = requests.post(
                self._url,
                data=json.dumps({**payload, "token": self._token}),
                headers={"Content-Type": "text/plain;charset=utf-8"},
                timeout=APPS_SCRIPT_TIMEOUT_S,
            )
        except requests.RequestException as e:
            raise StoreError(f"sheet unreachable: {type(e).__name__}") from e
        try:
            answer = r.json()
        except ValueError as e:  # Google sends an HTML page when the deployment is not open to "Anyone"
            raise StoreError(f"sheet answered HTTP {r.status_code} but not JSON: check the web app access") from e
        if not answer.get("ok"):
            raise StoreError(f"sheet script refused: {str(answer.get('error'))[:80]}")
        return answer

    def load(self, code):
        return self._call({"action": "load", "code": code}).get("data")

    def save(self, code, data):
        self._call({"action": "save", "code": code, "data": data})

    def delete(self, code):
        self._call({"action": "delete", "code": code})


def make_backend(secrets=None, environ=None):
    """Pick the backend: [supabase] url/key, else [sheets] url/token, else ROOM_STORE_DIR."""
    environ = os.environ if environ is None else environ
    sb = (secrets or {}).get("supabase") or {}
    if sb.get("url") and sb.get("key"):
        return SupabaseBackend(sb["url"], sb["key"])
    sh = (secrets or {}).get("sheets") or {}
    if sh.get("url") and sh.get("token"):
        return AppsScriptBackend(sh["url"], sh["token"])
    folder = environ.get("ROOM_STORE_DIR")
    return FileBackend(folder) if folder else None


# ------------------------------------------------------------------ saver
class RoomSaver:
    """Writes changed rooms in the background so a button press never waits for the network."""

    def __init__(self, backend, lock, rooms, interval=None, start_thread=True):
        self.backend = backend
        if interval is None:
            interval = getattr(backend, "save_interval", SAVE_INTERVAL_S)
        self.enabled = backend is not None
        self.last_error = None
        self._lock = lock
        self._rooms = rooms
        self._interval = interval
        self._dirty = set()
        self._dirty_lock = threading.Lock()
        self._stop = threading.Event()
        if self.enabled and start_thread:
            threading.Thread(target=self._run, name="room-saver", daemon=True).start()

    def load(self, code):
        """Saved JSON for the room, or None if it was never saved. Raises StoreError on trouble."""
        return self.backend.load(code) if self.enabled else None

    def mark_dirty(self, code):
        if self.enabled:
            with self._dirty_lock:
                self._dirty.add(code)

    def flush(self):
        """Write every changed room now. Returns how many were written."""
        if not self.enabled:
            return 0
        with self._dirty_lock:
            codes = list(self._dirty)
            self._dirty.clear()
        written = 0
        for code in codes:
            with self._lock:
                room = self._rooms.get(code)
                data = serialize_room(room) if room else None
            if data is None:
                continue
            try:
                self.backend.save(code, data)
                self.last_error = None
                written += 1
            except Exception as e:  # keep the room dirty and try again on the next tick
                if str(e) != self.last_error:
                    LOG.warning("saving room failed: %s", e)
                self.last_error = str(e)[:200]
                self.mark_dirty(code)
        return written

    def _run(self):
        while not self._stop.wait(self._interval):
            try:
                self.flush()
            except Exception:  # never let the saver thread die
                LOG.exception("room saver loop")

    def stop(self):
        self._stop.set()
