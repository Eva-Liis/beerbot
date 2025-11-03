"""
BeerBot API v1.2-compliant handler (Flask)
-----------------------------------------
Single endpoint `/api/decision` that handles both the handshake and weekly decision steps
as specified in **BeerBot API Specification v1.2 (Student Edition)**.

Design goals:
- Deterministic (no RNG). Same input JSON → same orders.
- Stateless across requests: uses only the provided `weeks` array (growing history) to compute decisions.
- BlackBox mode supported (uses each role's own fields only). GlassBox can be added later.

How the controller works (APIO-lite):
1) For each role, compute an exponential-smoothed demand forecast from the role's
   `incoming_orders` history and an exponentially-smoothed absolute error (MAE).
2) Project end-of-week on-hand and backlog given this week's `inventory`, `backlog`,
   `incoming_orders`, and `arriving_shipments`.
3) Compute an inventory position (ignoring unknown pipeline), then partially adjust
   toward an order-up-to target with a safety buffer. Damps bullwhip.
4) Deterministic rounding half-up, clamp to non-negative integers.

This is intentionally light-weight and fast for the ≤3s timeout budget.
"""
from __future__ import annotations
from typing import Dict, Any
from flask import Flask, request, jsonify

# ===================== Tunables (deterministic) =====================
ALPHA = 0.3     # exponential smoothing weight for demand forecast
BETA = 0.6      # partial adjustment toward target (damping)
K_SAFETY = 0.5  # safety buffer multiplier on smoothed absolute error
HORIZON = 4     # review + effective lead-time proxy (weeks)

STUDENT_EMAIL = "evtimm@taltech.ee"
ALGO_NAME = "EvaFeaturing5.0"
VERSION = "v1.0.0"
SUPPORTS = {"blackbox": True, "glassbox": False}
HANDSHAKE_MESSAGE = "BeerBot ready"

# ============================ App ============================
app = Flask(__name__)

def round_half_up(x: float) -> int:
    return 0 if x <= 0 else int(x + 0.5)

def smooth_forecast_and_mae(weeks: list, role: str, alpha: float) -> tuple[float, float]:
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

def projected_position(role_state: Dict[str, int]) -> int:
    inv = int(role_state.get("inventory", 0))
    bkl = int(role_state.get("backlog", 0))
    inc = int(role_state.get("incoming_orders", 0))
    arr = int(role_state.get("arriving_shipments", 0))
    on_hand = max(0, inv + arr - bkl - inc)
    backlg = max(0, bkl + inc - (inv + arr))
    return on_hand - backlg  # IP (pipeline teadmata, ignoreerime)

def last_order(weeks: list, role: str) -> int:
    return max(0, int(weeks[-1].get("orders", {}).get(role, 0))) if weeks else 0

def decide_for_role(weeks: list, role: str) -> int:
    fc, mae = smooth_forecast_and_mae(weeks, role, ALPHA)
    ip = projected_position(weeks[-1]["roles"][role])
    safety = K_SAFETY * mae * (HORIZON ** 0.5)
    target = fc * HORIZON + safety
    gap = target - ip
    q_star = BETA * gap + (1 - BETA) * last_order(weeks, role)
    return round_half_up(q_star)

@app.post("/")  # Vercel mountib selle /api/decision alla
def decision():
    body: Dict[str, Any] = request.get_json(force=True, silent=True) or {}

    if body.get("handshake") is True:
        return jsonify({
            "ok": True,
            "student_email": STUDENT_EMAIL,
            "algorithm_name": ALGO_NAME,
            "version": VERSION,
            "supports": SUPPORTS,
            "message": HANDSHAKE_MESSAGE,
        }), 200

    weeks = body.get("weeks", [])
    if not isinstance(weeks, list) or not weeks:
        return jsonify({"orders": {
            "retailer": 10, "wholesaler": 10, "distributor": 10, "factory": 10
        }}), 200

    roles = ["retailer", "wholesaler", "distributor", "factory"]
    orders: Dict[str, int] = {r: decide_for_role(weeks, r) for r in roles}
    for k, v in list(orders.items()):
        orders[k] = max(0, int(v))  # ints only

    return jsonify({"orders": orders}), 200

if __name__ == "__main__":
    # Development server; deploy behind a production server (e.g., Vercel/Cloud Run/Gunicorn)
    app.run(host="0.0.0.0", port=8080)
