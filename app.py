import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Dummy Score Calculator", page_icon="🃏", layout="wide"
)

# ---------------------------------------------------------------- state
st.session_state.setdefault("players", ["A", "B", "C"])
st.session_state.setdefault("history", [])
st.session_state.setdefault("multiplier", 0.1)
st.session_state.setdefault("dark_fee", 15.0)


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


def save_round():
    players = st.session_state.players
    knocker, scores, dark, no_meld = current_inputs(players)
    money, transfers, winner_score, _ = compute_round(
        players,
        scores,
        knocker,
        dark,
        no_meld,
        st.session_state.multiplier,
        st.session_state.dark_fee,
    )
    st.session_state.history.append(
        {
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
    )
    # Clear the form for the next round
    for p in players:
        st.session_state[f"score_{p}"] = 0
        st.session_state[f"nomeld_{p}"] = False
    st.session_state["dark"] = False
    st.session_state["just_saved"] = True


# -------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("⚙️ Game settings")
    st.session_state.multiplier = st.number_input(
        "Rate per point (THB)",
        min_value=0.0,
        value=float(st.session_state.multiplier),
        step=0.01,
        format="%.2f",
    )
    st.session_state.dark_fee = st.number_input(
        "No-meld penalty (THB per player)",
        min_value=0.0,
        value=float(st.session_state.dark_fee),
        step=5.0,
    )

    st.divider()
    st.header("👥 Players")
    st.write("  ".join(f"`{p}`" for p in st.session_state.players))

    if len(st.session_state.players) < 4:
        c_name, c_add = st.columns([3, 2])
        new_name = c_name.text_input(
            "New player name",
            key="new_player_input",
            placeholder="Name...",
            label_visibility="collapsed",
        )
        if c_add.button("➕ Add", width="stretch"):
            name = new_name.strip()
            if name and name not in st.session_state.players:
                st.session_state.players.append(name)
                st.rerun()
    else:
        st.caption("Maximum of 4 players")

    if len(st.session_state.players) > 1:
        with st.popover("🗑️ Remove player", width="stretch"):
            rem_p = st.selectbox("Choose a player to remove", st.session_state.players)
            st.caption("Their history and balance stay in the summary")
            if st.button("Confirm remove", type="primary"):
                st.session_state.players.remove(rem_p)
                st.rerun()

    st.divider()
    with st.popover("🔄 New game", width="stretch"):
        st.write("Clear all history and balances?")
        if st.button("Yes, clear everything", type="primary"):
            st.session_state.history = []
            st.rerun()

    st.caption("Data lives in this page only. Refreshing or closing it loses everything.")

# ----------------------------------------------------------------- main
st.title("🃏 Dummy Score Calculator")
players = st.session_state.players

left, middle, right = st.columns([1.2, 1, 1], gap="large")

# ---------------- Left: record this round
with left:
    st.subheader("① Record this round")

    if len(players) < 2:
        st.warning("You need at least 2 players. Add them in the left sidebar.")
    else:
        if st.session_state.pop("just_saved", False):
            st.toast("Round saved", icon="✅")

        if st.session_state.get("knocker") not in players:
            st.session_state.pop("knocker", None)
        st.radio("Who knocked?", players, key="knocker", horizontal=True)
        knocker = st.session_state["knocker"]

        for p in players:
            st.session_state.setdefault(f"score_{p}", 0)
        st.markdown("**Points per player**")
        for p in players:
            c_name, c_score, c_flag = st.columns([1, 2, 2], vertical_alignment="center")
            c_name.markdown(f"### {p}")
            c_score.number_input(
                f"Points of {p}",
                min_value=-500,
                max_value=500,
                step=1,
                key=f"score_{p}",
                label_visibility="collapsed",
            )
            c_flag.checkbox(
                "No meld",
                key=f"nomeld_{p}",
                disabled=(p == knocker),
            )

        st.checkbox("Dark knock", key="dark")

        # ----- Preview before saving
        knocker, scores, dark, no_meld = current_inputs(players)
        money, _, winner_score, tops = compute_round(
            players,
            scores,
            knocker,
            dark,
            no_meld,
            st.session_state.multiplier,
            st.session_state.dark_fee,
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

    history = st.session_state.history
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
            st.session_state.history.pop()
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
