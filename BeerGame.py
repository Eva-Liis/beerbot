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
ALGO_NAME = "EvaFeaturing5.0"                    # 3..32 chars [A-Za-z0-9_]
VERSION = "v1.0.0"                               # semantic-style string
SUPPORTS = {"blackbox": True, "glassbox": False}
HANDSHAKE_MESSAGE = "BeerBot ready"               # must match exactly

# ============================ App ============================
app = Flask(__name__)


def round_half_up(x: float) -> int:
    if x <= 0:
        return 0
    return int(x + 0.5)


def smooth_forecast_and_mae(weeks: list, role: str, alpha: float) -> tuple[float, float]:
    """Return (forecast, mae) after processing the full weeks history for a role.
    Cold start: forecast = first observed demand, mae = 0. Deterministic.
    """
    fc = None
    mae = 0.0
    for w in weeks:
        r = w["roles"][role]
        d = max(0, int(r.get("incoming_orders", 0)))
        if fc is None:
            fc = float(d)
        else:
            prev_fc = fc
            fc = alpha * d + (1 - alpha) * fc
            mae = alpha * abs(d - prev_fc) + (1 - alpha) * mae
    if fc is None:
        fc = 0.0
    return fc, mae


def projected_position(role_state: Dict[str, int]) -> tuple[int, int, int]:
    """Compute projected on-hand, projected backlog, and inventory position
    at end of this week for a role, ignoring unknown pipeline.
    IP = on_hand_proj - backlog_proj (since pipeline is unknown in spec)
    """
    inv = int(role_state.get("inventory", 0))
    bkl = int(role_state.get("backlog", 0))
    inc = int(role_state.get("incoming_orders", 0))
    arr = int(role_state.get("arriving_shipments", 0))

    on_hand_proj = max(0, inv + arr - bkl - inc)
    backlog_proj = max(0, bkl + inc - (inv + arr))
    ip = on_hand_proj - backlog_proj
    return on_hand_proj, backlog_proj, ip


def last_order(weeks: list, role: str) -> int:
    if not weeks:
        return 0
    lo = weeks[-1].get("orders", {}).get(role, 0)
    try:
        return max(0, int(lo))
    except Exception:
        return 0


def decide_for_role(weeks: list, role: str) -> int:
    fc, mae = smooth_forecast_and_mae(weeks, role, ALPHA)
    _, _, ip = projected_position(weeks[-1]["roles"][role])

    safety = K_SAFETY * mae * (HORIZON ** 0.5)
    target = fc * HORIZON + safety

    gap = target - ip
    order_star = BETA * gap + (1 - BETA) * last_order(weeks, role)

    return round_half_up(order_star)


@app.post("/api/decision")
def decision():
    body: Dict[str, Any] = request.get_json(force=True, silent=True) or {}

    # ---------------- Handshake ----------------
    if body.get("handshake") is True:
        # Must satisfy strict validation per spec
        resp = {
            "ok": True,
            "student_email": STUDENT_EMAIL,
            "algorithm_name": ALGO_NAME,
            "version": VERSION,
            "supports": SUPPORTS,
            "message": HANDSHAKE_MESSAGE,
        }
        return jsonify(resp), 200

    # ---------------- Weekly Step ----------------
    weeks = body.get("weeks", [])
    if not isinstance(weeks, list) or len(weeks) == 0:
        # Return a safe default that still obeys JSON/int rules
        return jsonify({"orders": {
            "retailer": 10, "wholesaler": 10, "distributor": 10, "factory": 10
        }}), 200

    mode = body.get("mode", "blackbox").lower()

    # BlackBox: decide per role using only own fields
    roles = ["retailer", "wholesaler", "distributor", "factory"]
    orders: Dict[str, int] = {}

    if mode == "blackbox":
        for role in roles:
            orders[role] = decide_for_role(weeks, role)
    else:
        # GlassBox placeholder: currently same as blackbox; can be upgraded to coordinate
        for role in roles:
            orders[role] = decide_for_role(weeks, role)

    # Ensure non-negative integers (and Content-Type is JSON by Flask)
    for k, v in list(orders.items()):
        try:
            orders[k] = max(0, int(v))
        except Exception:
            orders[k] = 10  # safe fallback per spec's spirit

    return jsonify({"orders": orders}), 200


if __name__ == "__main__":
    # Development server; deploy behind a production server (e.g., Vercel/Cloud Run/Gunicorn)
    app.run(host="0.0.0.0", port=8080)
