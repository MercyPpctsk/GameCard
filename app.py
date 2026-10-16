import threading

import pandas as pd
import streamlit as st

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
    @media (max-width: 640px) {
      .block-container {padding-left: 0.9rem; padding-right: 0.9rem; padding-top: 2.5rem;}
      h1 {font-size: 1.6rem !important;}
      h2, h3 {font-size: 1.2rem !important;}
      /* keep each player row (name | points | no meld) on one line */
      .st-key-player_rows [data-testid="stHorizontalBlock"],
      .st-key-add_row [data-testid="stHorizontalBlock"],
      .st-key-settings_row [data-testid="stHorizontalBlock"],
      .st-key-bottom_row [data-testid="stHorizontalBlock"] {
        flex-wrap: nowrap !important; gap: 0.5rem !important; align-items: center;
      }
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


def get_room(code):
    store = get_store()
    with store["lock"]:
        return store["rooms"].setdefault(
            code,
            {
                "players": ["A", "B", "C"],
                "history": [],
                "multiplier": 0.1,
                "dark_fee": 15.0,
                "version": 0,
            },
        )


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
# Select the whole number when a score box is focused, so typing replaces the
# "0" instead of becoming "0100".
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
          el.setAttribute("inputmode", label.startsWith("Points of") ? "numeric" : "decimal");
        });
      };
      apply();
      new MutationObserver(apply).observe(doc.body, {childList: true, subtree: true});
    }
    if (!doc.__selectOnFocus) {
      doc.__selectOnFocus = true;
      doc.addEventListener("focusin", (e) => {
        if (e.target.matches && e.target.matches("input[type=number]")) {
          setTimeout(() => e.target.select(), 0);
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
        joined = st.form_submit_button("Join room", type="primary")
    if joined and typed.strip():
        st.session_state["room"] = typed.strip().lower()
        st.query_params["room"] = typed.strip().lower()
        st.rerun()
    st.stop()

st.session_state["room"] = code
room = get_room(code)
st.session_state["seen_version"] = room["version"]


@st.fragment(run_every=3)
def watch_for_changes(room_code):
    """Reload the whole page when someone else changes the room."""
    if get_room(room_code)["version"] != st.session_state.get("seen_version"):
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
        if name and name not in r["players"] and len(r["players"]) < 4:
            r["players"].append(name)

    mutate(code, add)
    st.session_state["new_player_input"] = ""


def remove_player_cb(name):
    def remove(r):
        if name in r["players"] and len(r["players"]) > 1:
            r["players"].remove(name)

    mutate(code, remove)


def flip_sign_cb(name):
    key = f"score_{name}"
    st.session_state[key] = -st.session_state.get(key, 0)


def clear_history_cb():
    mutate(code, lambda r: r["history"].clear())


# ----------------------------------------------------------------- main
st.title("🃏 Dummy Score Calculator")
st.caption(f"Room: {code}")

with st.container(key="settings_row"):
    c_rate, c_fee, _ = st.columns([1, 1, 10])
    new_rate = c_rate.number_input(
        "Rate (THB/pt)",
        min_value=0.0,
        value=float(room["multiplier"]),
        step=0.01,
        format="%.2f",
    )
    new_fee = c_fee.number_input(
        "Penalty (THB)",
        min_value=0.0,
        value=float(room["dark_fee"]),
        step=5.0,
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
