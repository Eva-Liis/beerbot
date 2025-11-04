from __future__ import annotations
from typing import Dict, Any, Tuple, List
from flask import Flask, request, jsonify
import math

# --- Handshake meta ---
STUDENT_EMAIL = "evtimm@taltech.ee"
ALGO_NAME = "EvaSellsBeer"
VERSION = "v1.0.2"
SUPPORTS = {"blackbox": True, "glassbox": False}
HANDSHAKE_MESSAGE = "BeerBot ready"

app = Flask(__name__)

# --- Tuuled (deterministlikud) ---
ALPHA = 0.35              # kiirem, kuid stabiilne silumine
K_SAFETY = 0.8            # mõõdukas ohutusvaru (backlog << inventory eesmärk)
BETA_BY_ROLE = {          # rollipõhine dämpimine (ülesvool aeglasem)
    "retailer": 0.65,
    "wholesaler": 0.55,
    "distributor": 0.45,
    "factory": 0.35,
}
MAX_LAG = 6               # max hinnatav viiteaeg
RAMP_FRAC = 0.25          # tellimuse muutus ±25% prognoosist nädalas
CAP_MULT = 1.6            # kõva lagi sihttaseme ümber
REVIEW_TIME = 1
LEAD_TIME_DEFAULT = 2
INITIAL_DEMAND_ESTIMATE = 12
ROLES = ["retailer", "wholesaler", "distributor", "factory"]


def round_half_up(x: float) -> int:
    return 0 if x <= 0 else int(math.floor(x + 0.5))


def series_from_weeks(weeks: List[Dict[str, Any]], role: str):
    orders = [int(w.get("orders", {}).get(role, 0)) for w in weeks]
    arrivals = [int(w["roles"][role].get("arriving_shipments", 0)) for w in weeks]
    return orders, arrivals


def estimate_lead_lag(weeks: List[Dict[str, Any]], role: str, max_lag: int = MAX_LAG) -> int:
    """Ristkorrelatsioon: leia lag, kus orders[t-lag] 'sobib' arrivals[t]-ga kõige paremini."""
    orders, arrivals = series_from_weeks(weeks, role)
    n = len(weeks)
    if n < 2:
        return LEAD_TIME_DEFAULT
    best_lag, best_score = 1, -1
    for lag in range(1, min(max_lag, n - 1) + 1):
        score = 0
        for t in range(lag, n):
            score += orders[t - lag] * arrivals[t]
        if score > best_score:
            best_score, best_lag = score, lag
    return max(1, best_lag)


def smooth_forecast_and_mae(weeks: List[Dict[str, Any]], role: str, alpha: float) -> Tuple[float, float]:
    """Eksponentsiaalne silumine prognoosiks ja MAE prognoosiveaks (|d - fc_prev|)."""
    fc, mae = None, 0.0
    for w in weeks:
        d = max(0, int(w["roles"][role].get("incoming_orders", 0)))  # NB: alati incoming_orders
        if fc is None:
            fc = float(d if d > 0 else INITIAL_DEMAND_ESTIMATE)
        else:
            mae = alpha * abs(d - fc) + (1 - alpha) * mae
            fc = alpha * d + (1 - alpha) * fc
    return (fc or float(INITIAL_DEMAND_ESTIMATE)), max(1.0, mae)


def last_order(weeks: List[Dict[str, Any]], role: str) -> int:
    return max(0, int(weeks[-1].get("orders", {}).get(role, 0))) if weeks else 0


def pipeline_on_order(weeks: List[Dict[str, Any]], role: str, Lhat: int) -> int:
    """Teel olev kaup: sum viimased L̂-1 meie enda tellimused (need pole veel kohal)."""
    if Lhat <= 1 or len(weeks) <= 1:
        return 0
    orders = [int(w.get("orders", {}).get(role, 0)) for w in weeks]
    take = min(Lhat - 1, len(weeks) - 1)
    return sum(orders[-i] for i in range(1, take + 1))


def decide_for_role(weeks: List[Dict[str, Any]], role: str) -> int:
    # 1) Hinda lead-time L̂, sea H = min(REVIEW + L̂, 6)
    Lhat = estimate_lead_lag(weeks, role, MAX_LAG) if len(weeks) >= 3 else LEAD_TIME_DEFAULT
    H = min(REVIEW_TIME + Lhat, 6)

    # 2) Prognoos + prognoosiviga
    fc, mae = smooth_forecast_and_mae(weeks, role, ALPHA)

    # 3) IP = (inventory + arriving_shipments) - backlog + pipeline
    state = weeks[-1]["roles"][role]
    inv = int(state.get("inventory", 0))
    arr = int(state.get("arriving_shipments", 0))
    bkl = int(state.get("backlog", 0))
    on_order = pipeline_on_order(weeks, role, Lhat)
    IP = (inv + arr) - bkl + on_order

    # 4) Siht (order-up-to) + ohutusvaru
    safety = K_SAFETY * mae * math.sqrt(H)
    target = fc * H + safety
    gap = target - IP

    # 5) Osaline samm + ramp + kõva lagi
    beta = BETA_BY_ROLE.get(role, 0.5)
    q_last = last_order(weeks, role)
    q_star = beta * gap + (1 - beta) * q_last

    ramp = max(1, int(math.floor(RAMP_FRAC * max(fc, 1.0) + 0.5)))
    q_min = max(0, q_last - ramp)
    q_max = q_last + ramp
    hard_cap = int(math.floor(CAP_MULT * (fc * H + safety) + 0.5))

    q = max(q_min, min(q_max, round_half_up(q_star)))
    q = min(q, hard_cap)

    # 6) Kui backlog = 0 ja oleme sihist üle → ära kuhja ladu
    if bkl == 0 and (inv + arr) > target:
        q = min(q, round_half_up(fc))

    return max(0, int(q))


@app.post("/api/decision")
def decision():
    try:
        body: Dict[str, Any] = request.get_json(force=True, silent=True) or {}

        # Handshake
        if body.get("handshake") is True:
            return jsonify({
                "ok": True,
                "student_email": STUDENT_EMAIL,
                "algorithm_name": ALGO_NAME,
                "version": VERSION,
                "supports": SUPPORTS,
                "message": HANDSHAKE_MESSAGE,
            }), 200

        # Weekly
        weeks = body.get("weeks", [])
        if not isinstance(weeks, list) or not weeks:
            return jsonify({"orders": {r: INITIAL_DEMAND_ESTIMATE for r in ROLES}}), 200

        orders = {r: decide_for_role(weeks, r) for r in ROLES}
        orders = {k: max(0, int(v)) for k, v in orders.items()}
        return jsonify({"orders": orders}), 200

    except Exception as e:
        print("ERROR /api/decision:", repr(e))
        # spec-sõbralik fallback, mitte 500
        return jsonify({
            "orders": {r: INITIAL_DEMAND_ESTIMATE for r in ROLES},
            "error": "internal"
        }), 200
