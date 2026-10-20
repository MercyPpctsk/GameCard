import copy
import html
import threading
import time
import uuid

import pandas as pd
import streamlit as st

import holdem as hd

st.set_page_config(
    page_title="Dummy Score Calculator",
    page_icon="static/favicon.png",
    layout="wide",
)

st.markdown(
    """
    <style>
    /* cleaner look: hide Streamlit's toolbar buttons/footer (keep the sidebar toggle), tighter spacing */
    [data-testid="stToolbarActions"], [data-testid="stMainMenu"],
    [data-testid="stAppDeployButton"], [data-testid="stDecoration"], footer {display: none;}
    [data-testid="stHeader"] {background: transparent;}
    .block-container {padding-top: 2.5rem; padding-bottom: 2rem;}
    /* rate/penalty boxes are narrow: type the value, no +/- buttons */
    .st-key-settings_row input {padding-left: 0.25rem !important; padding-right: 0.25rem !important; text-align: center; font-size: 0.9rem !important;}
    .st-key-settings_row [data-testid="stColumn"]:nth-child(-n+2) {
      min-width: 5.5rem !important; flex: 0 0 5.5rem !important; width: 5.5rem !important;
    }
    /* type the number: no +/- stepper buttons on any number box */
    [data-testid="stNumberInputStepUp"],
    [data-testid="stNumberInputStepDown"] {display: none;}
    .st-key-settings3_row input {padding-left: 0.25rem !important; padding-right: 0.25rem !important; text-align: center; font-size: 0.9rem !important;}
    .st-key-settings3_row [data-testid="stColumn"]:nth-child(-n+4) {
      min-width: 5.5rem !important; flex: 0 0 5.5rem !important; width: 5.5rem !important;
    }
    /* Hold'em table */
    .pcard {display: inline-block; background: #fff; color: #111; border-radius: 7px; padding: 3px 7px;
            margin: 2px 3px 2px 0; font-weight: 700; font-size: 1.15rem; min-width: 2.1rem; text-align: center;
            box-shadow: 0 1px 3px rgba(0,0,0,.5);}
    .pcard.red {color: #d62839;}
    .pcard.back {background: repeating-linear-gradient(45deg,#26335f,#26335f 4px,#33437a 4px,#33437a 8px); color: transparent;}
    .pcard.empty {background: transparent; border: 1px dashed #4a5168; color: transparent; box-shadow: none;}
    .felt {background: radial-gradient(ellipse at center, #14573a, #0b3a26); border-radius: 18px; padding: 14px 12px;
           text-align: center; margin-bottom: 10px; border: 1px solid #1d7a52;}
    .felt .pot {color: #ffe08a; font-weight: 700; margin-bottom: 6px;}
    .seat {border: 1px solid #2b3142; border-radius: 12px; padding: 8px 10px; margin-bottom: 8px; background: #151a25;}
    .seat.turn {border-color: #ff4b4b; box-shadow: 0 0 0 1px #ff4b4b inset;}
    .seat.folded {opacity: .45;}
    .seat.me {background: #1a2133;}
    .seat .nm {font-weight: 700;}
    .seat .ch {color: #9fe3b4; font-weight: 600;}
    .seat .bd {display: inline-block; background: #33415f; border-radius: 6px; padding: 0 6px; margin-left: 4px; font-size: .75rem;}
    .seat .st {color: #9aa3b8; font-size: .85rem;}
    @media (max-width: 640px) {
      .block-container {padding-left: 0.9rem; padding-right: 0.9rem; padding-top: 2.5rem;}
      h1 {font-size: 1.6rem !important;}
      h2, h3 {font-size: 1.2rem !important;}
      /* keep each player row (name | points | no meld) on one line */
      .st-key-player_rows [data-testid="stHorizontalBlock"],
      .st-key-add_row [data-testid="stHorizontalBlock"],
      .st-key-settings_row [data-testid="stHorizontalBlock"],
      .st-key-bottom_row [data-testid="stHorizontalBlock"],
      .st-key-settings3_row [data-testid="stHorizontalBlock"],
      .st-key-act_row [data-testid="stHorizontalBlock"],
      .st-key-quick_row [data-testid="stHorizontalBlock"],
      .st-key-sit_row [data-testid="stHorizontalBlock"] {
        flex-wrap: nowrap !important; gap: 0.5rem !important; align-items: center;
      }
      .st-key-settings3_row [data-testid="stColumn"],
      .st-key-act_row [data-testid="stColumn"],
      .st-key-quick_row [data-testid="stColumn"],
      .st-key-sit_row [data-testid="stColumn"] {min-width: 0 !important;}
      .st-key-player_rows [data-testid="stColumn"],
      .st-key-add_row [data-testid="stColumn"],
      .st-key-settings_row [data-testid="stColumn"],
      .st-key-bottom_row [data-testid="stColumn"] {min-width: 0 !important;}
      .st-key-player_rows h3 {font-size: 1.3rem !important; padding: 0;}
      .st-key-player_rows .stButton button {min-height: 2.4rem; padding: 0 0.4rem;}
      /* bigger tap targets */
      .stButton button {min-height: 3rem;}
      input[type=number] {font-size: 1.1rem !important;}
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ------------------------------------------------------- shared storage
# One dict lives in the server process and is shared by every visitor, so
# everyone in the same room sees the same game. It is lost when the app
# restarts or goes to sleep.
@st.cache_resource
def get_store():
    return {"lock": threading.RLock(), "rooms": {}}


def get_room(code, mode="dummy"):
    """Return the room, creating it in `mode` ("dummy" or "poker") if it is new."""
    store = get_store()
    with store["lock"]:
        room = store["rooms"].setdefault(
            code,
            {
                "mode": mode,
                "players": ["A", "B", "C", "D"] if mode == "poker" else ["A", "B", "C"],
                "history": [],
                "multiplier": 0.1,
                "dark_fee": 15.0,
                # poker cash-game ledger: buy-ins per player, chips left at cash-out
                "poker": {"chip_value": 1.0, "buyin_chips": 100, "buys": {}, "cash": {}},
                "holdem": hd.new_table(),
                "version": 0,
            },
        )
        return room


def mutate(code, change):
    """Apply change(room) under the lock and bump the version so others refresh."""
    with get_store()["lock"]:
        room = get_room(code)
        change(room)
        room["version"] += 1


# ------------------------------------------------------------ the maths
def compute_round(players, scores, knocker, dark, no_meld, mult, fee):
    """Return (money per player, transfers [(payer, payee, amount)], winner score used, winners)"""
    top_score = max(scores.values())
    tops = [p for p in players if scores[p] == top_score]
    # Dark knock: the winner (knocker) never melded, winner's score x2
    winner_score = top_score * 2 if dark and knocker in tops else top_score

    money = {p: 0.0 for p in players}
    transfers = []
    for p in players:
        if p in tops:
            continue
        pay = (winner_score - scores[p]) * mult
        money[p] -= pay
        for t in tops:
            money[t] += pay / len(tops)
            transfers.append((p, t, pay / len(tops)))

    # Losers with no meld at all pay the penalty to everyone else
    for v in no_meld:
        for p in players:
            if p != v:
                money[v] -= fee
                money[p] += fee
                transfers.append((v, p, fee))
    return money, transfers, winner_score, tops


def settle(net):
    """Fewest-ish transfers that clear everyone's net: [(payer, payee, amount)]."""
    owes = sorted([[p, -v] for p, v in net.items() if v < -0.004], key=lambda x: -x[1])
    gets = sorted([[p, v] for p, v in net.items() if v > 0.004], key=lambda x: -x[1])
    out, i, j = [], 0, 0
    while i < len(owes) and j < len(gets):
        pay = round(min(owes[i][1], gets[j][1]), 2)
        if pay > 0:
            out.append((owes[i][0], gets[j][0], pay))
        owes[i][1] = round(owes[i][1] - pay, 2)
        gets[j][1] = round(gets[j][1] - pay, 2)
        if owes[i][1] <= 0.004:
            i += 1
        if gets[j][1] <= 0.004:
            j += 1
    return out


def synced_number(container, key, room_value, label, **kw):
    """number_input whose shown value follows the shared room value.

    When the room value changes (someone else edited, or New game), the box is
    refreshed. When this user types a new value it is returned so the caller can
    save it to the room.
    """
    synced = st.session_state.setdefault("_synced", {})
    if synced.get(key) != room_value:
        st.session_state[key] = room_value
        synced[key] = room_value
    return container.number_input(label, key=key, **kw)


def colored(amount):
    text = f"{amount:+.2f} THB"
    if amount > 0.004:
        return f":green[**{text}**]"
    if amount < -0.004:
        return f":red[**{text}**]"
    return f"**{amount:.2f} THB**"


def current_inputs(players):
    knocker = st.session_state.get("knocker")
    scores = {p: st.session_state.get(f"score_{p}", 0) for p in players}
    no_meld = [
        p
        for p in players
        if p != knocker and st.session_state.get(f"nomeld_{p}", False)
    ]
    return knocker, scores, st.session_state.get("dark", False), no_meld


# Runs on every page, including the room-code screen
# Blank a number box when it is focused, so typing replaces the "0"
# instead of becoming "0100".
st.iframe(
    """<script>
    // Home-screen icon + web-app title (iOS reads these when you tap "Add to Home Screen")
    for (const w of [window.parent, window.top]) {
      try {
        const d = w.document;
        if (d.__iconSet) continue;
        d.__iconSet = true;
        const base = w.location.origin + "/app/static/";
        const add = (tag, attrs) => {
          const el = d.createElement(tag);
          Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v));
          d.head.appendChild(el);
        };
        // Dark status/address-bar tint instead of the white one Streamlit Cloud sets
        let tc = d.querySelector("meta[name=theme-color]");
        if (!tc) { tc = d.createElement("meta"); tc.setAttribute("name", "theme-color"); d.head.appendChild(tc); }
        tc.setAttribute("content", "#0e1117");
        d.documentElement.style.background = "#0e1117";
        if (d.body) d.body.style.background = "#0e1117";
        d.title = "Dummy Score";
        d.querySelectorAll("link[rel*=icon]").forEach((l) => l.remove());
        add("link", {rel: "apple-touch-icon", href: base + "apple-touch-icon.png"});
        add("link", {rel: "icon", href: base + "favicon.png"});
        add("meta", {name: "apple-mobile-web-app-capable", content: "yes"});
        add("meta", {name: "mobile-web-app-capable", content: "yes"});
        add("meta", {name: "apple-mobile-web-app-title", content: "Dummy"});
        add("meta", {name: "apple-mobile-web-app-status-bar-style", content: "black"});
      } catch (e) { /* cross-origin parent: skip */ }
    }
    const doc = window.parent.document;
    // Phone keypad: digits only for points ("±" button flips the sign),
    // digits + decimal point for the rate and penalty boxes.
    if (!doc.__inputModeSet) {
      doc.__inputModeSet = true;
      const apply = () => {
        doc.querySelectorAll("input[type=number]").forEach((el) => {
          const label = el.getAttribute("aria-label") || "";
          el.setAttribute("inputmode", /^(Points of|Buy-ins of|Chips of|Chips [/] buy-in|Raise to|Start chips|Small blind|Big blind)/.test(label) ? "numeric" : "decimal");
        });
      };
      apply();
      new MutationObserver(apply).observe(doc.body, {childList: true, subtree: true});
    }
    if (!doc.__selectOnFocus) {
      doc.__selectOnFocus = true;
      // iOS cannot select the text of a number box, so blank it on focus: typing
      // then replaces the old number. If you leave it empty the old value returns.
      const setVal = (el, v) =>
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(el, v);
      const isNum = (el) => el && el.matches && el.matches("input[type=number]");
      doc.addEventListener("focusin", (e) => {
        if (isNum(e.target)) {
          e.target.dataset.prev = e.target.value;
          setVal(e.target, "");
        }
      });
      doc.addEventListener("focusout", (e) => {
        if (isNum(e.target) && e.target.value === "" && e.target.dataset.prev !== undefined) {
          setVal(e.target, e.target.dataset.prev);
        }
      });
    }
    </script>""",
    height=1,
)


# ------------------------------------------------------------ room gate
code = st.session_state.get("room") or st.query_params.get("room", "")
code = code.strip().lower()

if not code:
    st.title("🃏 Dummy Score Calculator")
    st.write("Enter a room code. Everyone who uses the same code sees the same game.")
    with st.form("join_form"):
        typed = st.text_input("Room code", placeholder="e.g. friday-night")
        game = st.radio(
            "Game (only used when the room is new)",
            ["Dummy", "Poker ledger", "Texas Hold'em"],
            horizontal=True,
        )
        joined = st.form_submit_button("Join room", type="primary")
    if joined and typed.strip():
        st.session_state["room"] = typed.strip().lower()
        st.session_state["new_mode"] = {"Dummy": "dummy", "Poker ledger": "poker", "Texas Hold'em": "holdem"}[game]
        st.query_params["room"] = typed.strip().lower()
        st.rerun()
    st.stop()

st.session_state["room"] = code
_hint = st.session_state.get("new_mode") or st.query_params.get("game", "dummy")
room = get_room(code, _hint if _hint in ("dummy", "poker", "holdem") else "dummy")
mode = room.get("mode", "dummy")
st.session_state["seen_version"] = room["version"]


@st.fragment(run_every=1.5 if mode == "holdem" else 3)
def watch_for_changes(room_code):
    """Reload the whole page when someone else changes the room.

    At the card table it also reloads once when the player to act runs out of time,
    so the "fold for them" button appears without anyone touching the page.
    """
    r = get_room(room_code)
    if r["version"] != st.session_state.get("seen_version"):
        st.rerun()
    if r.get("mode") == "holdem":
        T = r["holdem"]
        if hd.timed_out(T):
            key = (T["hand"]["id"], T["hand"]["to_act"])
            if st.session_state.get("seen_timeout") != key:
                st.session_state["seen_timeout"] = key
                st.rerun()


watch_for_changes(code)


def save_round():
    r = get_room(code)
    players = list(r["players"])
    knocker, scores, dark, no_meld = current_inputs(players)
    money, transfers, winner_score, _ = compute_round(
        players, scores, knocker, dark, no_meld, r["multiplier"], r["dark_fee"]
    )
    entry = {
        "knocker": knocker,
        "is_dark": dark,
        "dark_victims": no_meld,
        "transfers": transfers,
        "details": {
            p: {
                "score": scores[p],
                "diff_points": winner_score - scores[p],
                "money": money[p],
            }
            for p in players
        },
    }
    mutate(code, lambda rr: rr["history"].append(entry))
    # Clear the form for the next round
    for p in players:
        st.session_state[f"score_{p}"] = 0
        st.session_state[f"nomeld_{p}"] = False
    st.session_state["dark"] = False
    st.session_state["just_saved"] = True


# ----------------------------------------------------- player callbacks
def add_player_cb():
    name = st.session_state.get("new_player_input", "").strip()

    def add(r):
        limit = 10 if r.get("mode") == "poker" else 4
        if name and name not in r["players"] and len(r["players"]) < limit:
            r["players"].append(name)

    mutate(code, add)
    st.session_state["new_player_input"] = ""


def remove_player_cb(name):
    def remove(r):
        if name in r["players"] and len(r["players"]) > 1:
            r["players"].remove(name)
            r["poker"]["buys"].pop(name, None)
            r["poker"]["cash"].pop(name, None)

    mutate(code, remove)


def flip_sign_cb(name):
    key = f"score_{name}"
    st.session_state[key] = -st.session_state.get(key, 0)


def clear_history_cb():
    def clear(r):
        r["history"].clear()
        r["poker"]["buys"].clear()
        r["poker"]["cash"].clear()

    mutate(code, clear)


# ------------------------------------------------------- hold'em callbacks
def _hd_run(change):
    """Apply a table change under the lock; show the rule message if it is rejected."""
    def wrapped(r):
        hd.ensure(r["holdem"])
        change(r)

    try:
        mutate(code, wrapped)
        st.session_state["hd_err"] = None
    except ValueError as e:
        st.session_state["hd_err"] = str(e)


def hd_sit_cb():
    pid = st.session_state.get("pid") or uuid.uuid4().hex[:12]
    seat = int(st.session_state.get("hd_seat", 1)) - 1
    name = st.session_state.get("hd_name", "")
    _hd_run(lambda r: hd.sit(r["holdem"], seat, pid, name))
    if not st.session_state.get("hd_err"):
        st.session_state["pid"] = pid
        st.query_params["pid"] = pid


def hd_deal_cb():
    _hd_run(lambda r: hd.start_hand(r["holdem"]))


def hd_act_cb(hand_id, action, amount=None):
    pid = st.session_state.get("pid")

    def go(r):
        T = r["holdem"]
        if not T["hand"] or T["hand"]["id"] != hand_id:
            raise ValueError("The hand has moved on")
        seat = hd.seat_of(T, pid)
        if seat is None:
            raise ValueError("You are not seated")
        hd.act(T, seat, action, amount)

    _hd_run(go)


def hd_raise_cb(hand_id):
    hd_act_cb(hand_id, "raise", st.session_state.get("hd_raise"))


def hd_quick_cb(value):
    st.session_state["hd_raise"] = int(value)


def hd_timeout_cb(hand_id):
    def go(r):
        T = r["holdem"]
        if not T["hand"] or T["hand"]["id"] != hand_id:
            raise ValueError("The hand has moved on")
        hd.timeout_fold(T)

    _hd_run(go)


def hd_stand_cb():
    pid = st.session_state.get("pid")

    def go(r):
        T = r["holdem"]
        seat = hd.seat_of(T, pid)
        if seat is None:
            raise ValueError("You are not seated")
        hd.stand(T, seat)

    _hd_run(go)


def hd_rebuy_cb():
    pid = st.session_state.get("pid")

    def go(r):
        T = r["holdem"]
        seat = hd.seat_of(T, pid)
        if seat is None:
            raise ValueError("You are not seated")
        hd.rebuy(T, seat)

    _hd_run(go)


def hd_end_cb():
    _hd_run(lambda r: hd.end_game(r["holdem"]))


def hd_reset_cb():
    def go(r):
        old = r["holdem"]
        r["holdem"] = hd.new_table(old["start_chips"], old["sb"], old["bb"])
        r["holdem"]["chip_value"] = old.get("chip_value", 1.0)

    _hd_run(go)


def card_html(c, back=False):
    if back:
        return '<span class="pcard back">??</span>'
    sym = {"s": "&spades;", "h": "&hearts;", "d": "&diams;", "c": "&clubs;"}[c[1]]
    red = " red" if c[1] in "hd" else ""
    return f'<span class="pcard{red}">{"10" if c[0] == "T" else c[0]}{sym}</span>'


def cards_html(cards, hidden=False, n=2):
    if hidden:
        return "".join(card_html("", back=True) for _ in range(n))
    return "".join(card_html(c) for c in cards)


# ------------------------------------------------------------ hold'em mode
if mode == "holdem":
    with get_store()["lock"]:
        t = hd.snapshot(room.setdefault("holdem", hd.new_table()))
    h = t["hand"]
    live_hand = hd.active(t)
    pid = st.session_state.get("pid") or st.query_params.get("pid")
    if pid:
        st.session_state["pid"] = pid
    me = hd.seat_of(t, pid) if pid else None
    n_seats = hd.MAX_SEATS

    st.title("♠️ Texas Hold'em")
    st.caption(
        f"Room: {code} · No-limit · blinds {t['sb']}/{t['bb']} · "
        f"{t['start_chips']} chips to sit down"
    )
    if st.session_state.get("hd_err"):
        st.warning(st.session_state["hd_err"])

    locked = live_hand or t["ended"]
    with st.container(key="settings3_row"):
        c1, c2, c3, c4, _ = st.columns([1, 1, 1, 1, 10])
        n_start = synced_number(c1, "hd_start", int(t["start_chips"]), "Start chips", min_value=1, step=100, disabled=locked)
        n_sb = synced_number(c2, "hd_sb", int(t["sb"]), "Small blind", min_value=1, step=5, disabled=locked)
        n_bb = synced_number(c3, "hd_bb", int(t["bb"]), "Big blind", min_value=1, step=10, disabled=locked)
        n_val = synced_number(c4, "hd_val", float(t["chip_value"]), "THB / chip", min_value=0.0, step=0.5, format="%.2f")
    blinds_changed = (n_start, n_sb, n_bb) != (t["start_chips"], t["sb"], t["bb"]) and not locked
    value_changed = n_val != t["chip_value"]
    if blinds_changed or value_changed:

        def apply(r):
            T = r["holdem"]
            hd.ensure(T)
            if blinds_changed:
                hd.set_blinds(T, n_start, n_sb, n_bb)
            if value_changed:
                hd.set_chip_value(T, n_val)

        try:
            mutate(code, apply)
            st.session_state["hd_err"] = None
        except ValueError as e:
            st.session_state["hd_err"] = str(e)
        st.rerun()

    # ---- the felt: board and pot
    board = h["board"] if h else []
    slots = "".join(card_html(c) for c in board) + "".join(
        '<span class="pcard empty">..</span>' for _ in range(5 - len(board))
    )
    street = h["street"].capitalize() if h and live_hand else ("Hand over" if h else "Waiting to deal")
    st.markdown(
        f'<div class="felt"><div class="pot">{street} &nbsp;|&nbsp; Pot {hd.pot_total(h):,}</div>{slots}</div>',
        unsafe_allow_html=True,
    )

    # ---- result of the last hand
    if h and not live_hand and h.get("result"):
        res = h["result"]
        lines = []
        for p in res["pots"]:
            who = ", ".join(html.escape(t["seats"][s]["name"]) for s in p["winners"] if t["seats"][s])
            how = f" with {p['hand']}" if p["hand"] else " (everyone else folded)"
            lines.append(f"**{who}** wins **{p['amount']:,}**{how}")
        st.success("  \n".join(lines))
    if t["ended"]:
        st.info("Game over. The final settlement is below. Press Reset table to play again.")

    # ---- seats
    for i in range(n_seats):
        seat = t["seats"][i]
        if not seat:
            continue
        badges = ""
        folded = bool(h) and i in h["folded"]
        in_h = bool(h) and i in h["seats_in"]
        if h and i == h["dealer"]:
            badges += '<span class="bd">D</span>'
        if h and i == h["sb"] and in_h:
            badges += '<span class="bd">SB</span>'
        if h and i == h["bb"] and in_h:
            badges += '<span class="bd">BB</span>'
        status = ""
        if live_hand and in_h:
            if folded:
                status = "folded"
            elif i in h["allin"]:
                status = "all-in"
            elif h["bets"][i] > 0:
                status = f"bet {h['bets'][i]:,}"
            if h["to_act"] == i:
                status = (status + " " if status else "") + "&larr; to act"
        elif seat["chips"] == 0 and not live_hand:
            status = "busted: rebuy to play"
        cards = ""
        if in_h and not folded:
            revealed = h.get("result") and i in h["result"]["hands"]
            if i == me or revealed:
                cards = cards_html(h["hole"][i])
                if revealed:
                    cards += f' <span class="st">{h["result"]["hands"][i]["name"]}</span>'
            elif live_hand:
                cards = cards_html([], hidden=True)
        cls = "seat"
        cls += " turn" if live_hand and h["to_act"] == i else ""
        cls += " folded" if folded else ""
        cls += " me" if i == me else ""
        you = " (you)" if i == me else ""
        st.markdown(
            f'<div class="{cls}"><span class="nm">{html.escape(seat["name"])}{you}</span> '
            f'<span class="ch">{seat["chips"]:,}</span>{badges}'
            f'<div class="st">{status}</div><div>{cards}</div></div>',
            unsafe_allow_html=True,
        )

    # ---- my controls
    if me is None and t["ended"]:
        st.caption("The game is over: no new players can sit.")
    elif me is None:
        empty = [i + 1 for i, s in enumerate(t["seats"]) if s is None]
        with st.container(border=True):
            st.markdown("**Take a seat**")
            if not empty:
                st.info("The table is full")
            else:
                with st.container(key="sit_row"):
                    c_seat, c_name = st.columns([1, 2], vertical_alignment="center")
                    c_seat.selectbox("Seat", empty, key="hd_seat", label_visibility="collapsed")
                    c_name.text_input("Your name", key="hd_name", placeholder="Your name", label_visibility="collapsed")
                st.button(
                    f"Sit down with {t['start_chips']:,} chips",
                    type="primary",
                    width="stretch",
                    on_click=hd_sit_cb,
                )
                if live_hand:
                    st.caption("A hand is in progress: you will be dealt in next hand.")
    else:
        mine = t["seats"][me]
        if live_hand and h["to_act"] == me:
            L = hd.legal(t, me)
            st.markdown("**Your turn**")
            with st.container(key="act_row"):
                a1, a2 = st.columns(2)
                a1.button("Fold", width="stretch", key="a_fold", on_click=hd_act_cb, args=(h["id"], "fold"))
                if L["can_check"]:
                    a2.button("Check", width="stretch", type="primary", key="a_call", on_click=hd_act_cb, args=(h["id"], "check"))
                else:
                    label = f"Call {L['call_amount']:,}" + (" (all-in)" if L["call_amount"] >= mine["chips"] else "")
                    a2.button(label, width="stretch", type="primary", key="a_call", on_click=hd_act_cb, args=(h["id"], "call"))
            if L["can_raise"]:
                lo, hi = int(L["min_to"]), int(L["max_to"])
                cur = st.session_state.get("hd_raise", lo)
                st.session_state["hd_raise"] = max(lo, min(hi, int(cur)))
                st.number_input("Raise to", min_value=lo, max_value=hi, step=int(t["bb"]), key="hd_raise")
                pot = hd.pot_total(h)
                with st.container(key="quick_row"):
                    q1, q2, q3, q4 = st.columns(4)
                    q1.button("Min", width="stretch", key="q_min", on_click=hd_quick_cb, args=(lo,))
                    half = max(lo, min(hi, h["current_bet"] + (pot + L["to_call"]) // 2))
                    full = max(lo, min(hi, h["current_bet"] + pot + L["to_call"]))
                    q2.button("1/2 pot", width="stretch", key="q_half", on_click=hd_quick_cb, args=(half,))
                    q3.button("Pot", width="stretch", key="q_pot", on_click=hd_quick_cb, args=(full,))
                    q4.button("Max", width="stretch", key="q_max", on_click=hd_quick_cb, args=(hi,))
                verb = "Bet" if L["is_bet"] else "Raise to"
                st.button(
                    f"{verb} {st.session_state['hd_raise']:,}",
                    type="primary",
                    width="stretch",
                    key="a_raise",
                    on_click=hd_raise_cb,
                    args=(h["id"],),
                )
                st.button(
                    f"All-in {hi:,}",
                    width="stretch",
                    key="a_allin",
                    on_click=hd_act_cb,
                    args=(h["id"], "allin"),
                )
        elif live_hand and h["to_act"] is not None:
            actor = t["seats"][h["to_act"]]["name"]
            waited = int(time.time() - h["turn_started"])
            st.caption(f"Waiting for {actor} ({waited}s)")
            if hd.timed_out(t):
                st.button(
                    f"{actor} is out of time: fold them",
                    width="stretch",
                    key="a_timeout",
                    on_click=hd_timeout_cb,
                    args=(h["id"],),
                )

        if not live_hand and not t["ended"]:
            if hd.can_deal(t):
                st.button(
                    "Deal next hand" if h else "Deal",
                    type="primary",
                    width="stretch",
                    key="a_deal",
                    on_click=hd_deal_cb,
                )
            else:
                st.caption("Need at least 2 players with chips to deal.")
        if mine["chips"] == 0 and not hd.in_hand(t, me) and not t["ended"]:
            st.button("Rebuy", width="stretch", key="a_rebuy", on_click=hd_rebuy_cb)
        if hd.can_leave(t, me):
            with st.popover("Stand up and cash out"):
                st.write(f"Leave the table with {mine['chips']:,} chips?")
                st.button("Yes, stand up", type="primary", key="a_stand", on_click=hd_stand_cb)

    # ---- settlement: who pays whom
    if t["ledger"]:
        st.subheader("Settlement")
        if live_hand:
            st.caption("The settlement appears when this hand is over.")
        else:
            rows = hd.standings(t)
            cv = float(t["chip_value"])
            with st.container(border=True):
                st.markdown("**Net result**" + ("" if t["ended"] else " (if the game ended now)"))
                for r in sorted(rows, key=lambda x: -x["net"]):
                    st.markdown(
                        f"{html.escape(r['name'])} &nbsp; {colored(r['net'] * cv)} "
                        f"&nbsp; <span class='st'>({r['net']:+,} chips)</span>",
                        unsafe_allow_html=True,
                    )
            st.markdown("**Who pays whom**")
            with st.container(border=True):
                transfers = hd.settlements(t)
                if not transfers:
                    st.write("Nothing to settle")
                for payer, payee, chips in transfers:
                    st.markdown(
                        f"**{html.escape(payer)}** ➜ **{html.escape(payee)}** &nbsp; "
                        f"{chips * cv:,.2f} THB &nbsp; <span class='st'>({chips:,} chips)</span>",
                        unsafe_allow_html=True,
                    )
            if sum(r["net"] for r in rows) != 0:
                st.warning("The nets do not add up to zero: check the chip count.")
            if not t["ended"] and me is not None:
                holders = [i for i in hd.seated(t) if t["seats"][i]["chips"] > 0]
                if len(holders) <= 1 and len(rows) >= 2:
                    who = t["seats"][holders[0]]["name"] if holders else "Nobody"
                    st.info(f"{who} has all the chips. Press End game to settle.")
                with st.popover("End game"):
                    st.write("End the game for everyone? Nobody can deal, sit or rebuy after this.")
                    st.button("Yes, end the game", type="primary", key="a_end", on_click=hd_end_cb)

    # ---- ledger and log
    if t["ledger"]:
        with st.expander("Chip count"):
            rows = []
            for p_id, led in t["ledger"].items():
                seat_i = hd.seat_of(t, p_id)
                now = t["seats"][seat_i]["chips"] if seat_i is not None else 0
                rows.append(
                    {
                        "Player": led["name"],
                        "Buy-ins": led["buys"],
                        "Chips in": led["in"],
                        "Chips now": now + led["out"],
                        "Net": now + led["out"] - led["in"],
                    }
                )
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if h and h["events"]:
        with st.expander("Hand log"):
            st.text("\n".join(h["events"]))

    st.divider()
    with st.container(key="bottom_row"):
        c_new, c_leave, _ = st.columns([1, 1, 1])
        with c_new.popover("Reset table"):
            st.write("Remove everyone and start the table from scratch?")
            st.button("Yes, reset", type="primary", on_click=hd_reset_cb)
        if me is None:
            if c_leave.button("Leave room", type="primary"):
                st.session_state.pop("room", None)
                st.session_state.pop("new_mode", None)
                st.query_params.clear()
                st.rerun()
        else:
            c_leave.caption("Stand up first to leave")
    st.caption(
        "Cards are dealt by the server and only you can see yours. "
        "Data is kept on the server while the app is awake; if it restarts or sleeps, the table is cleared."
    )
    st.stop()


# ------------------------------------------------------------ poker mode
if mode == "poker":
    pk = room["poker"]
    players = list(room["players"])
    st.title("♠️ Poker Ledger")
    st.caption(f"Room: {code} · cash game")

    with st.container(key="settings_row"):
        c_val, c_buy, _ = st.columns([1, 1, 10])
        new_val = synced_number(
            c_val, "pk_val", float(pk["chip_value"]), "THB / chip",
            min_value=0.0, step=0.5, format="%.2f",
        )
        new_buy = synced_number(
            c_buy, "pk_buy", int(pk["buyin_chips"]), "Chips / buy-in",
            min_value=1, step=10,
        )
    if new_val != pk["chip_value"] or new_buy != pk["buyin_chips"]:
        mutate(
            code,
            lambda r: r["poker"].update(chip_value=float(new_val), buyin_chips=int(new_buy)),
        )
        st.rerun()

    p_left, p_right = st.columns([1.3, 1], gap="large")

    with p_left:
        st.subheader("① Buy-ins and cash-out")
        with st.container(key="player_rows"):
            h = st.columns([0.9, 1.5, 1.8, 0.9], vertical_alignment="center")
            h[1].caption("Buy-ins")
            h[2].caption("Chips left")
            edits_buys, edits_cash = {}, {}
            for p in players:
                c_name, c_buy, c_cash, c_del = st.columns(
                    [0.9, 1.5, 1.8, 0.9], vertical_alignment="center"
                )
                c_name.markdown(f"### {p}")
                b = synced_number(
                    c_buy, f"pk_b_{p}", int(pk["buys"].get(p, 1)), f"Buy-ins of {p}",
                    min_value=0, max_value=99, step=1, label_visibility="collapsed",
                )
                c = synced_number(
                    c_cash, f"pk_c_{p}", int(pk["cash"].get(p, 0)), f"Chips of {p}",
                    min_value=0, max_value=10_000_000, step=1, label_visibility="collapsed",
                )
                if b != int(pk["buys"].get(p, 1)):
                    edits_buys[p] = int(b)
                if c != int(pk["cash"].get(p, 0)):
                    edits_cash[p] = int(c)
                if len(players) > 1:
                    with c_del.popover("🗑️"):
                        st.write(f"Remove {p}?")
                        st.caption("This also removes their buy-ins and chips.")
                        st.button(
                            "Remove",
                            type="primary",
                            key=f"rm_{p}",
                            on_click=remove_player_cb,
                            args=(p,),
                        )
        if edits_buys or edits_cash:
            mutate(
                code,
                lambda r: (
                    r["poker"]["buys"].update(edits_buys),
                    r["poker"]["cash"].update(edits_cash),
                ),
            )
            st.rerun()

        if len(players) >= 10:
            st.caption("Maximum of 10 players")
        else:
            with st.container(key="add_row"):
                c_name, c_add = st.columns([3, 2], vertical_alignment="center")
                c_name.text_input(
                    "New player name",
                    key="new_player_input",
                    placeholder="New player name",
                    label_visibility="collapsed",
                )
                c_add.button("➕ Add player", width="stretch", on_click=add_player_cb)
        st.caption("Buy-ins start at 1. Fill in each player's chips when they cash out.")

    with p_right:
        st.subheader("② Result")
        bought = {p: int(pk["buys"].get(p, 1)) * int(pk["buyin_chips"]) for p in players}
        cash = {p: int(pk["cash"].get(p, 0)) for p in players}
        total_in, total_out = sum(bought.values()), sum(cash.values())
        diff = total_out - total_in
        net = {p: (cash[p] - bought[p]) * float(pk["chip_value"]) for p in players}

        with st.container(border=True):
            st.markdown(
                f"**On the table:** {total_in:,} chips · {total_in * pk['chip_value']:,.2f} THB"
            )
            if diff == 0:
                st.success("Chips balance: nothing missing")
            elif diff < 0:
                st.warning(f"{-diff:,} chips not counted yet (or missing)")
            else:
                st.error(f"{diff:,} chips too many: check the counts")
            for p, v in sorted(net.items(), key=lambda x: -x[1]):
                st.markdown(f"{p} &nbsp; {colored(v)}")

        st.markdown("**Who pays whom**")
        with st.container(border=True):
            lines = settle(net)
            if not lines:
                st.write("Nothing to settle")
            for payer, payee, amt in lines:
                st.markdown(f"**{payer}** ➜ **{payee}** &nbsp; {amt:.2f} THB")
        if diff != 0:
            st.caption("Settlement is only final when the chips balance.")

    st.divider()
    with st.container(key="bottom_row"):
        c_new, c_leave, _ = st.columns([1, 1, 1])
        with c_new.popover("🔄 New game"):
            st.write("Reset all buy-ins and chips for everyone in this room?")
            st.button("Yes, reset", type="primary", on_click=clear_history_cb)
        if c_leave.button("Leave room", type="primary"):
            st.session_state.pop("room", None)
            st.session_state.pop("new_mode", None)
            st.query_params.clear()
            st.rerun()
    st.caption(
        "Data is kept on the server while the app is awake. "
        "If the app restarts or sleeps, the room is cleared."
    )
    st.stop()


# ----------------------------------------------------------------- main
st.title("🃏 Dummy Score Calculator")
st.caption(f"Room: {code}")

with st.container(key="settings_row"):
    c_rate, c_fee, _ = st.columns([1, 1, 10])
    new_rate = synced_number(
        c_rate, "dm_rate", float(room["multiplier"]), "Rate (THB/pt)",
        min_value=0.0, step=0.01, format="%.2f",
    )
    new_fee = synced_number(
        c_fee, "dm_fee", float(room["dark_fee"]), "Penalty (THB)",
        min_value=0.0, step=5.0,
    )
if new_rate != room["multiplier"] or new_fee != room["dark_fee"]:
    mutate(
        code,
        lambda r: r.update(multiplier=float(new_rate), dark_fee=float(new_fee)),
    )
    st.rerun()

players = list(room["players"])
history = list(room["history"])

left, middle, right = st.columns([1.2, 1, 1], gap="large")

# ---------------- Left: record this round
with left:
    st.subheader("① Record this round")

    def add_player_box():
        if len(players) >= 4:
            st.caption("Maximum of 4 players")
            return
        with st.container(key="add_row"):
            c_name, c_add = st.columns([3, 2], vertical_alignment="center")
            c_name.text_input(
                "New player name",
                key="new_player_input",
                placeholder="New player name",
                label_visibility="collapsed",
            )
            c_add.button("➕ Add player", width="stretch", on_click=add_player_cb)

    if len(players) < 2:
        st.warning("You need at least 2 players. Add one below.")
        add_player_box()
    else:
        if st.session_state.pop("just_saved", False):
            st.toast("Round saved", icon="✅")

        if st.session_state.get("knocker") not in players:
            st.session_state.pop("knocker", None)
        st.radio("Who knocked?", players, key="knocker", horizontal=True)
        knocker = st.session_state["knocker"]

        st.markdown("**Points per player**")
        with st.container(key="player_rows"):
            for p in players:
                c_name, c_score, c_sign, c_flag, c_del = st.columns(
                    [0.6, 2, 0.8, 1.6, 1.0], vertical_alignment="center"
                )
                c_name.markdown(f"### {p}")
                c_score.number_input(
                    f"Points of {p}",
                    min_value=-999,
                    max_value=999,
                    value=0,
                    step=1,
                    key=f"score_{p}",
                    label_visibility="collapsed",
                )
                c_sign.button(
                    "±",
                    key=f"sign_{p}",
                    on_click=flip_sign_cb,
                    args=(p,),
                    help="Switch between positive and negative",
                )
                c_flag.checkbox(
                    "No meld",
                    key=f"nomeld_{p}",
                    disabled=(p == knocker),
                )
                with c_del.popover("🗑️"):
                    st.write(f"Remove {p} from the game?")
                    st.caption("Past rounds and balance stay in the summary.")
                    st.button(
                        "Remove",
                        type="primary",
                        key=f"rm_{p}",
                        on_click=remove_player_cb,
                        args=(p,),
                    )

        add_player_box()
        st.checkbox("Dark knock", key="dark")

        # ----- Preview before saving
        knocker, scores, dark, no_meld = current_inputs(players)
        money, _, winner_score, tops = compute_round(
            players,
            scores,
            knocker,
            dark,
            no_meld,
            room["multiplier"],
            room["dark_fee"],
        )
        with st.container(border=True):
            st.markdown("**This round (preview before saving)**")
            note = f"Winner: {', '.join(tops)}"
            if dark and knocker in tops:
                note += f" · points x2 = {winner_score}"
            st.caption(note)
            for p in players:
                st.markdown(f"{p} &nbsp; {colored(money[p])}")

        st.button(
            "💾 Save this round",
            type="primary",
            width="stretch",
            on_click=save_round,
        )

# ---------------- Middle: summary
with middle:
    st.subheader("② Summary")

    if not history:
        st.info("No data yet. Save the first round on the left.")
    else:
        totals = {}
        for rnd in history:
            for p, d in rnd["details"].items():
                totals[p] = totals.get(p, 0.0) + d["money"]

        with st.container(border=True):
            st.markdown(f"**Net result over {len(history)} round(s)**")
            for p, t in sorted(totals.items(), key=lambda x: -x[1]):
                st.markdown(f"{p} &nbsp; {colored(t)}")

        st.markdown("**Who pays whom**")
        pair_net = {}
        for rnd in history:
            for payer, payee, amt in rnd.get("transfers", []):
                key = tuple(sorted((payer, payee)))
                pair_net[key] = pair_net.get(key, 0.0) + (
                    amt if key[0] == payer else -amt
                )

        lines = []
        for (a, b), net in pair_net.items():
            net = round(net, 2)
            if net > 0:
                lines.append((a, b, net))
            elif net < 0:
                lines.append((b, a, -net))

        with st.container(border=True):
            if not lines:
                st.write("Nothing to settle")
            for payer, payee, amt in lines:
                st.markdown(f"**{payer}** ➜ **{payee}** &nbsp; {amt:.2f} THB")

# ---------------- Right: history
with right:
    st.subheader("③ History")

    if not history:
        st.info("No rounds played yet")
    else:
        if st.button("↩️ Undo last round", width="stretch"):
            mutate(code, lambda r: r["history"].pop() if r["history"] else None)
            st.rerun()

        total_rounds = len(history)
        for idx, rnd in enumerate(reversed(history)):
            tags = []
            if rnd.get("is_dark"):
                tags.append("Dark knock")
            if rnd.get("dark_victims"):
                tags.append("No meld: " + ", ".join(rnd["dark_victims"]))
            title = f"Round {total_rounds - idx} · {rnd['knocker']} knocked"
            if tags:
                title += " · " + " · ".join(tags)

            with st.expander(title, expanded=(idx == 0)):
                df = pd.DataFrame(
                    [
                        {
                            "Player": p,
                            "Points": d["score"],
                            "Money (THB)": round(d["money"], 2),
                        }
                        for p, d in rnd["details"].items()
                    ]
                )
                st.dataframe(df, hide_index=True, width="stretch")

# ---------------- Bottom: new game / leave the room
st.divider()
with st.container(key="bottom_row"):
    c_new, c_leave, _ = st.columns([1, 1, 1])
    with c_new.popover("🔄 New game"):
        st.write("Clear all history and balances for everyone in this room?")
        st.button("Yes, clear everything", type="primary", on_click=clear_history_cb)
    if c_leave.button("Leave room", type="primary"):
        st.session_state.pop("room", None)
        st.query_params.clear()
        st.rerun()

st.caption(
    "Data is kept on the server while the app is awake. "
    "If the app restarts or sleeps, the room is cleared."
)
