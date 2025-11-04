# app.py (või api/app.py, kui kasutad Verceli Flask presetit)
from __future__ import annotations
from typing import Dict, Any, Tuple, List
from flask import Flask, request, jsonify
import math

# --- Handshake meta (MUUDA OMAKS) ---
STUDENT_EMAIL = "evtimm@taltech.ee"
ALGO_NAME = "EvaSellsBeer"
VERSION = "v1.0.1"
SUPPORTS = {"blackbox": True, "glassbox": False}
HANDSHAKE_MESSAGE = "BeerBot ready"

app = Flask(__name__)

# --- Parameetrid (deterministlikud) ---
ALPHA = 0.3              # nõudluse silumine
K_SAFETY = 0.6           # ohutusvaru kordaja (pisut kõrgem, et backlog ei paisuks)
BETA_BY_ROLE = {         # ülesvoolu rahulikum dämpimine
    "retailer": 0.60,
    "wholesaler": 0.50,
    "distributor": 0.40,
    "factory": 0.35,
}
MAX_LAG = 8              # otsitav maks viiteaeg ristkorrelatsiooniga
RAMP_FRAC = 0.30         # max muutus vs forecast ühe nädalaga (±30%)
CAP_MULT = 1.8           # kõva lagi: tellimus ≤ CAP_MULT * (forecast*H + safety)

ROLES = ["retailer", "wholesaler", "distributor", "factory"]


def round_half_up(x: float) -> int:
    return 0 if x <= 0 else int(math.floor(x + 0.5))


def series_from_weeks(weeks: List[Dict[str, Any]], role: str) -> Tuple[List[int], List[int]]:
    """Võta ajaread: meie tellimused ja meile saabunud saadetised per roll."""
    orders = [int(w.get("orders", {}).get(role, 0)) for w in weeks]
    arrivals = [int(w["roles"][role].get("arriving_shipments", 0)) for w in weeks]
    return orders, arrivals


def estimate_lead_lag(weeks: List[Dict[str, Any]], role: str, max_lag: int = MAX_LAG) -> int:
    """Hinda efektiivne viiteaeg L̂ ristkorrelatsiooniga (deterministlik, väikseima lag'i eelistus)."""
    orders, arrivals = series_from_weeks(weeks, role)
    n = len(weeks)
    best_lag, best_score = 2, -1  # vaikimisi 2
    for lag in range(1, min(max_lag, max(1, n - 1)) + 1):
        # joonda: orders[t-lag] vs arrivals[t]
        score = 0
        for t in range(lag, n):
            score += orders[t - lag] * arrivals[t]
        if score > best_score:
            best_score, best_lag = score, lag
    return max(1, best_lag)


def smooth_forecast_and_mae(weeks: List[Dict[str, Any]], role: str, alpha: float) -> Tuple[float, float]:
    """Eksponentsiaalne silumine + MAE (deterministlik). Kasutab incoming_orders seeriat."""
    fc, mae = None, 0.0
    for w in weeks:
        d = max(0, int(w["roles"][role].get("incoming_orders", 0)))
        if fc is None:
            fc = float(d)
        else:
            prev = fc
            fc = alpha * d + (1 - alpha) * fc
            mae = alpha * abs(d - prev) + (1 - alpha) * mae
    return (fc or 0.0), mae


def projected_on_hand_and_backlog(state: Dict[str, int]) -> Tuple[int, int]:
    inv = int(state.get("inventory", 0))
    bkl = int(state.get("backlog", 0))
    inc = int(state.get("incoming_orders", 0))
    arr = int(state.get("arriving_shipments", 0))
    on_hand = max(0, inv + arr - bkl - inc)
    backlog = max(0, bkl + inc - (inv + arr))
    return on_hand, backlog


def last_order(weeks: List[Dict[str, Any]], role: str) -> int:
    return max(0, int(weeks[-1].get("orders", {}).get(role, 0))) if weeks else 0


def pipeline_on_order(weeks: List[Dict[str, Any]], role: str, Lhat: int) -> int:
    """Lihtne rekonstruktsioon: sum viimased L̂-1 meie enda tellimused (need pole veel jõudnud)."""
    if Lhat <= 1 or len(weeks) <= 1:
        return 0
    orders = [int(w.get("orders", {}).get(role, 0)) for w in weeks]
    take = min(Lhat - 1, len(weeks) - 1)
    return sum(orders[-i] for i in range(1, take + 1))


def decide_for_role(weeks: List[Dict[str, Any]], role: str) -> int:
    # 1) Hinda viiteaeg L̂ ja vali H = L̂ + 1 (review + lead)
    Lhat = estimate_lead_lag(weeks, role, MAX_LAG)
    H = Lhat + 1

    # 2) Prognoos + viga
    fc, mae = smooth_forecast_and_mae(weeks, role, ALPHA)

    # 3) IP = on_hand - backlog + pipeline (rekonstrueeritud)
    on_hand, backlg = projected_on_hand_and_backlog(weeks[-1]["roles"][role])
    on_order = pipeline_on_order(weeks, role, Lhat)
    IP = on_hand - backlg + on_order

    # 4) siht + ohutusvaru + osaline samm
    safety = K_SAFETY * mae * math.sqrt(H)
    target = fc * H + safety
    gap = target - IP

    beta = BETA_BY_ROLE.get(role, 0.5)
    q_last = last_order(weeks, role)
    q_star = beta * gap + (1 - beta) * q_last

    # 5) ramp + lagi + integer >= 0
    # ramp: piirame muutuse võrreldes eelmise tellimusega ±RAMP_FRAC*fc
    ramp = max(1, int(math.floor(RAMP_FRAC * max(fc, 1) + 0.5)))
    q_min = max(0, q_last - ramp)
    q_max_ramp = q_last + ramp

    # absoluutne lagi sihttaseme ümber
    hard_cap = int(math.floor(CAP_MULT * (fc * H + safety) + 0.5))
    q_capped = min(q_max_ramp, hard_cap, max(q_min, round_half_up(q_star)))

    return max(0, int(q_capped))


@app.post("/api/decision")
def decision():
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
        return jsonify({"orders": {r: 10 for r in ROLES}}), 200

    orders = {r: decide_for_role(weeks, r) for r in ROLES}
    orders = {k: max(0, int(v)) for k, v in orders.items()}  # ints only
    return jsonify({"orders": orders}), 200
