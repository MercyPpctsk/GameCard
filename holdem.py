"""No-limit Texas Hold'em engine.

Pure Python, no Streamlit. All state lives in plain dicts so it can sit inside the
shared room dict. Every function that changes the table validates first and raises
ValueError *before* touching anything, so a rejected action never corrupts a hand.

Seats are numbered 0..MAX_SEATS-1 clockwise. Cards are two characters: rank
(2-9, T, J, Q, K, A) + suit (s, h, d, c), e.g. "As", "Td".
"""
import copy
import itertools
import random
import time

RANKS = "23456789TJQKA"
SUITS = "shdc"
HAND_NAMES = [
    "High card",
    "Pair",
    "Two pair",
    "Three of a kind",
    "Straight",
    "Flush",
    "Full house",
    "Four of a kind",
    "Straight flush",
]
MAX_SEATS = 9
TURN_SECONDS = 60


# ------------------------------------------------------------------ cards
def new_deck():
    return [r + s for r in RANKS for s in SUITS]


def rank_value(card):
    return RANKS.index(card[0]) + 2


def score5(cards):
    """Score exactly five cards. Higher tuple wins; ties are real ties."""
    vals = sorted((rank_value(c) for c in cards), reverse=True)
    flush = len({c[1] for c in cards}) == 1
    uniq = sorted(set(vals), reverse=True)
    straight_high = None
    if len(uniq) == 5:
        if uniq[0] - uniq[4] == 4:
            straight_high = uniq[0]
        elif uniq == [14, 5, 4, 3, 2]:  # the wheel: A-2-3-4-5
            straight_high = 5
    counts = {}
    for v in vals:
        counts[v] = counts.get(v, 0) + 1
    groups = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    ordered = [v for v, _ in groups]
    shape = [c for _, c in groups]
    if straight_high and flush:
        return (8, straight_high)
    if shape == [4, 1]:
        return (7, ordered[0], ordered[1])
    if shape == [3, 2]:
        return (6, ordered[0], ordered[1])
    if flush:
        return (5, *vals)
    if straight_high:
        return (4, straight_high)
    if shape == [3, 1, 1]:
        return (3, *ordered)
    if shape == [2, 2, 1]:
        return (2, *ordered)
    if shape == [2, 1, 1, 1]:
        return (1, *ordered)
    return (0, *vals)


def best_hand(cards):
    """Best five of up to seven cards -> (score, the five cards used)."""
    best = None
    for combo in itertools.combinations(cards, 5):
        sc = score5(combo)
        if best is None or sc > best[0]:
            best = (sc, list(combo))
    return best


def hand_name(score):
    if score[0] == 8 and score[1] == 14:
        return "Royal flush"
    return HAND_NAMES[score[0]]


# ------------------------------------------------------------------ table
def new_table(start_chips=1000, sb=10, bb=20):
    return {
        "seats": [None] * MAX_SEATS,
        "start_chips": start_chips,
        "sb": sb,
        "bb": bb,
        "hand": None,
        "hand_no": 0,
        "dealer": -1,
        "ledger": {},  # pid -> {"name", "buys", "in", "out"}
        "chip_value": 1.0,  # THB per chip, only used for the settlement
        "ended": False,  # True once somebody pressed "End game"
    }


def ensure(t):
    """Fill in fields that tables created by older code do not have."""
    t.setdefault("chip_value", 1.0)
    t.setdefault("ended", False)
    return t


def active(t):
    h = t["hand"]
    return bool(h) and h["street"] != "done"


def seat_of(t, pid):
    for i, s in enumerate(t["seats"]):
        if s and s["pid"] == pid:
            return i
    return None


def in_hand(t, seat):
    return active(t) and seat in t["hand"]["seats_in"]


def seated(t):
    return [i for i, s in enumerate(t["seats"]) if s]


def can_deal(t):
    return (not t.get("ended")) and (not active(t)) and len([i for i in seated(t) if t["seats"][i]["chips"] > 0]) >= 2


def set_blinds(t, start_chips, sb, bb):
    if active(t):
        raise ValueError("Wait until the hand is over")
    if start_chips < 1 or sb < 1 or bb < sb:
        raise ValueError("Blinds must be at least 1 and big blind >= small blind")
    t["start_chips"], t["sb"], t["bb"] = int(start_chips), int(sb), int(bb)


def _open(t):
    if t.get("ended"):
        raise ValueError("The game is over: reset the table to play again")


def sit(t, seat, pid, name):
    _open(t)
    name = (name or "").strip()[:16]
    if not name:
        raise ValueError("Enter your name first")
    if not 0 <= seat < len(t["seats"]):
        raise ValueError("Pick a seat")
    if t["seats"][seat] is not None:
        raise ValueError("That seat is taken")
    if seat_of(t, pid) is not None:
        raise ValueError("You are already seated")
    if any(s and s["name"].lower() == name.lower() for s in t["seats"]):
        raise ValueError("That name is already used at this table")
    t["seats"][seat] = {"pid": pid, "name": name, "chips": t["start_chips"]}
    led = t["ledger"].setdefault(pid, {"name": name, "buys": 0, "in": 0, "out": 0})
    led["name"] = name
    led["buys"] += 1
    led["in"] += t["start_chips"]


def can_leave(t, seat):
    return not in_hand(t, seat)


def stand(t, seat):
    s = t["seats"][seat]
    if s is None:
        raise ValueError("Empty seat")
    if not can_leave(t, seat):
        raise ValueError("You can stand up after this hand")
    t["ledger"][s["pid"]]["out"] += s["chips"]
    t["seats"][seat] = None


def rebuy(t, seat):
    _open(t)
    s = t["seats"][seat]
    if s is None:
        raise ValueError("Empty seat")
    if s["chips"] > 0:
        raise ValueError("You still have chips")
    if in_hand(t, seat):
        raise ValueError("You can rebuy after this hand")
    s["chips"] = t["start_chips"]
    led = t["ledger"][s["pid"]]
    led["buys"] += 1
    led["in"] += t["start_chips"]


# ------------------------------------------------------------------ a hand
def _put(t, h, seat, pay):
    s = t["seats"][seat]
    s["chips"] -= pay
    h["bets"][seat] += pay
    h["total"][seat] += pay
    if s["chips"] == 0:
        h["allin"].add(seat)


def _log(h, text):
    h["events"].append(text)


def _pending(h, s):
    return (
        s in h["seats_in"]
        and s not in h["folded"]
        and s not in h["allin"]
        and (s not in h["acted"] or h["bets"][s] < h["current_bet"])
    )


def _next_pending(h, after):
    for k in range(1, MAX_SEATS + 1):
        s = (after + k) % MAX_SEATS
        if _pending(h, s):
            return s
    return None


def start_hand(t, rng=None, deck=None):
    _open(t)
    if active(t):
        raise ValueError("A hand is already in progress")
    n = MAX_SEATS
    elig = [i for i, s in enumerate(t["seats"]) if s and s["chips"] > 0]
    if len(elig) < 2:
        raise ValueError("Need at least 2 players with chips")
    dealer = None
    for k in range(1, n + 1):
        c = (t["dealer"] + k) % n
        if c in elig:
            dealer = c
            break
    order = [(dealer + k) % n for k in range(1, n + 1) if (dealer + k) % n in elig]
    if deck is None:
        deck = new_deck()
        (rng or random.SystemRandom()).shuffle(deck)
    else:
        deck = list(deck)
    hole = {s: [] for s in elig}
    for _ in range(2):
        for s in order:
            hole[s].append(deck.pop(0))
    if len(elig) == 2:  # heads-up: the dealer posts the small blind
        sb_seat, bb_seat = dealer, order[0]
    else:
        sb_seat, bb_seat = order[0], order[1]
    h = {
        "id": t["hand_no"] + 1,
        "deck": deck,
        "board": [],
        "hole": hole,
        "seats_in": elig,
        "folded": set(),
        "allin": set(),
        "bets": {s: 0 for s in elig},
        "total": {s: 0 for s in elig},
        "street": "preflop",
        "current_bet": 0,
        "min_raise": t["bb"],
        "acted": set(),
        "to_act": None,
        "dealer": dealer,
        "sb": sb_seat,
        "bb": bb_seat,
        "turn_started": time.time(),
        "events": [],
        "result": None,
    }
    t["hand_no"] += 1
    t["dealer"] = dealer
    t["hand"] = h
    sbn = t["seats"][sb_seat]["name"]
    bbn = t["seats"][bb_seat]["name"]
    _put(t, h, sb_seat, min(t["sb"], t["seats"][sb_seat]["chips"]))
    _put(t, h, bb_seat, min(t["bb"], t["seats"][bb_seat]["chips"]))
    _log(h, f"Hand #{h['id']}: {sbn} posts small blind {h['bets'][sb_seat]}")
    _log(h, f"{bbn} posts big blind {h['bets'][bb_seat]}")
    h["current_bet"] = max(h["bets"].values())
    first = sb_seat if len(elig) == 2 else order[2 % len(order)]
    nxt = _next_pending(h, (first - 1) % n)
    if nxt is None:
        _street_done(t)
    else:
        h["to_act"] = nxt


def legal(t, seat):
    """What `seat` may do right now (only meaningful when it is their turn)."""
    h = t["hand"]
    s = t["seats"][seat]
    to_call = h["current_bet"] - h["bets"][seat]
    chips = s["chips"]
    max_to = h["bets"][seat] + chips
    return {
        "to_call": max(0, to_call),
        "call_amount": max(0, min(to_call, chips)),
        "can_check": to_call <= 0,
        "can_raise": seat not in h["acted"] and chips > to_call,
        "min_to": min(h["current_bet"] + h["min_raise"], max_to),
        "max_to": max_to,
        "is_bet": h["current_bet"] == 0,
    }


def act(t, seat, action, amount=None):
    if not active(t):
        raise ValueError("No hand in progress")
    h = t["hand"]
    if h["to_act"] != seat:
        raise ValueError("It is not your turn")
    s = t["seats"][seat]
    name = s["name"]
    L = legal(t, seat)

    if action == "fold":
        h["folded"].add(seat)
        _log(h, f"{name} folds")
    elif action == "check":
        if L["to_call"] > 0:
            raise ValueError("You cannot check, there is a bet to call")
        h["acted"].add(seat)
        _log(h, f"{name} checks")
    elif action == "call":
        if L["to_call"] <= 0:
            raise ValueError("There is nothing to call")
        pay = L["call_amount"]
        _put(t, h, seat, pay)
        h["acted"].add(seat)
        _log(h, f"{name} calls {pay}" + (" (all-in)" if seat in h["allin"] else ""))
    elif action in ("raise", "allin"):
        amt = L["max_to"] if action == "allin" else amount
        if amt is None:
            raise ValueError("Enter an amount")
        amt = int(amt)
        if amt > L["max_to"]:
            raise ValueError("You do not have that many chips")
        if amt <= h["current_bet"]:
            if action != "allin":
                raise ValueError("A raise must be higher than the current bet")
            pay = s["chips"]  # all-in for less than (or equal to) the bet = a call
            _put(t, h, seat, pay)
            h["acted"].add(seat)
            _log(h, f"{name} calls {pay} (all-in)")
        else:
            if not L["can_raise"]:
                raise ValueError("Betting is not reopened for you: call or fold")
            if amt < L["min_to"] and amt < L["max_to"]:
                raise ValueError(f"Minimum raise is to {L['min_to']}")
            verb = "bets" if L["is_bet"] else "raises to"
            pay = amt - h["bets"][seat]
            _put(t, h, seat, pay)
            size = amt - h["current_bet"]
            if size >= h["min_raise"]:
                h["min_raise"] = size
                h["acted"] = {seat}  # a full raise reopens the betting for everyone
            else:
                h["acted"].add(seat)  # a short all-in does not reopen it
            h["current_bet"] = amt
            _log(h, f"{name} {verb} {amt}" + (" (all-in)" if seat in h["allin"] else ""))
    else:
        raise ValueError("Unknown action")
    _after_action(t, seat)


def _after_action(t, seat):
    h = t["hand"]
    live = [s for s in h["seats_in"] if s not in h["folded"]]
    if len(live) == 1:
        _win_uncontested(t, live[0])
        return
    nxt = _next_pending(h, seat)
    if nxt is None:
        _street_done(t)
    else:
        h["to_act"] = nxt
        h["turn_started"] = time.time()


def _street_done(t):
    h = t["hand"]
    live = [s for s in h["seats_in"] if s not in h["folded"]]
    while True:
        if h["street"] == "river":
            _showdown(t)
            return
        h["bets"] = {s: 0 for s in h["seats_in"]}
        h["current_bet"] = 0
        h["min_raise"] = t["bb"]
        h["acted"] = set()
        h["deck"].pop(0)  # burn card
        if h["street"] == "preflop":
            h["board"] += [h["deck"].pop(0) for _ in range(3)]
            h["street"] = "flop"
        elif h["street"] == "flop":
            h["board"].append(h["deck"].pop(0))
            h["street"] = "turn"
        else:
            h["board"].append(h["deck"].pop(0))
            h["street"] = "river"
        _log(h, f"{h['street'].capitalize()}: {' '.join(h['board'])}")
        actors = [s for s in live if s not in h["allin"]]
        if len(actors) <= 1:
            continue  # nobody left to bet against: run the board out
        h["to_act"] = _next_pending(h, h["dealer"])
        h["turn_started"] = time.time()
        return


def _build_pots(h):
    total = h["total"]
    live = [s for s in h["seats_in"] if s not in h["folded"]]
    pots, prev = [], 0
    for lv in sorted({v for v in total.values() if v > 0}):
        amount = sum(min(v, lv) - min(v, prev) for v in total.values())
        pots.append({"amount": amount, "eligible": [s for s in live if total[s] >= lv]})
        prev = lv
    merged, carry = [], 0
    for p in pots:  # chips from folded players above everyone's level go to the next pot
        if not p["eligible"]:
            carry += p["amount"]
            continue
        p["amount"] += carry
        carry = 0
        merged.append(p)
    if carry and merged:
        merged[-1]["amount"] += carry
    return merged


def _win_uncontested(t, winner):
    h = t["hand"]
    amount = sum(h["total"].values())
    t["seats"][winner]["chips"] += amount
    name = t["seats"][winner]["name"]
    _log(h, f"{name} wins {amount} (everyone else folded)")
    h["result"] = {
        "type": "fold",
        "pots": [{"amount": amount, "winners": [winner], "hand": None}],
        "hands": {},
        "won": {winner: amount},
    }
    h["street"] = "done"
    h["to_act"] = None


def _showdown(t):
    h = t["hand"]
    live = [s for s in h["seats_in"] if s not in h["folded"]]
    scored = {s: best_hand(h["hole"][s] + h["board"]) for s in live}
    hands = {
        s: {"cards": h["hole"][s], "name": hand_name(scored[s][0]), "best": scored[s][1]}
        for s in live
    }
    n = MAX_SEATS
    won, out = {}, []
    for p in _build_pots(h):
        top = max(scored[s][0] for s in p["eligible"])
        winners = [s for s in p["eligible"] if scored[s][0] == top]
        # odd chips go to the winner closest to the left of the dealer
        winners.sort(key=lambda s: (s - h["dealer"]) % n)
        share, extra = divmod(p["amount"], len(winners))
        for i, s in enumerate(winners):
            gain = share + (1 if i < extra else 0)
            t["seats"][s]["chips"] += gain
            won[s] = won.get(s, 0) + gain
        out.append({"amount": p["amount"], "winners": winners, "hand": hand_name(top)})
    for pot in out:
        names = ", ".join(t["seats"][s]["name"] for s in pot["winners"])
        _log(h, f"{names} wins {pot['amount']} with {pot['hand']}")
    h["result"] = {"type": "showdown", "pots": out, "hands": hands, "won": won}
    h["street"] = "done"
    h["to_act"] = None


def set_chip_value(t, value):
    value = float(value)
    if value < 0:
        raise ValueError("THB per chip cannot be negative")
    t["chip_value"] = value


def end_game(t):
    """Lock the table so the final settlement cannot change any more."""
    if t.get("ended"):
        raise ValueError("The game is already over")
    if active(t):
        raise ValueError("Finish the current hand first")
    t["ended"] = True


def standings(t):
    """One row per person who ever bought in: chips put in, chips now, net (in chips)."""
    rows = []
    for pid, led in t["ledger"].items():
        i = seat_of(t, pid)
        now = (t["seats"][i]["chips"] if i is not None else 0) + led["out"]
        rows.append(
            {
                "pid": pid,
                "name": led["name"],
                "buys": led["buys"],
                "in": led["in"],
                "now": now,
                "net": now - led["in"],
            }
        )
    return rows


def settlements(t):
    """Who pays whom, in chips: [(payer name, payee name, chips)], fewest transfers.

    Only meaningful when no hand is running (chips in the pot belong to nobody yet).
    """
    rows = standings(t)
    label, seen = {}, {}
    for r in rows:  # keep names unique if two people used the same name at different times
        seen[r["name"]] = seen.get(r["name"], 0) + 1
        k = seen[r["name"]]
        label[r["pid"]] = r["name"] if k == 1 else f"{r['name']} #{k}"
    owes = sorted([[label[r["pid"]], -r["net"]] for r in rows if r["net"] < 0], key=lambda x: -x[1])
    gets = sorted([[label[r["pid"]], r["net"]] for r in rows if r["net"] > 0], key=lambda x: -x[1])
    out, i, j = [], 0, 0
    while i < len(owes) and j < len(gets):
        pay = min(owes[i][1], gets[j][1])
        out.append((owes[i][0], gets[j][0], pay))
        owes[i][1] -= pay
        gets[j][1] -= pay
        if owes[i][1] == 0:
            i += 1
        if gets[j][1] == 0:
            j += 1
    return out


def pot_total(h):
    return sum(h["total"].values()) if h else 0


def timed_out(t, now=None):
    if not active(t) or t["hand"]["to_act"] is None:
        return False
    return (now or time.time()) - t["hand"]["turn_started"] >= TURN_SECONDS


def timeout_fold(t, now=None):
    """Fold (or check when free) for the player who ran out of time."""
    if not timed_out(t, now):
        raise ValueError("The player still has time")
    h = t["hand"]
    seat = h["to_act"]
    act(t, seat, "check" if legal(t, seat)["can_check"] else "fold")


def snapshot(t):
    return copy.deepcopy(ensure(t))
