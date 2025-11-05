from flask import Flask, request, jsonify
import math

app = Flask(__name__)

# === KONSTANDID ===
K_SAFETY = 0.75
BETA_RETAILER = 0.35
BETA_OTHER = 0.10
MAX_ORDER_CHANGE = 0.30
ALPHA = 0.10
SEED = 2025

# Deterministliku käitumise tagamiseks (ei kasuta random, aga võid hiljem lisada)
import random
random.seed(SEED)

# === ABI FUNKTSIOONID ===
def smooth_demand(prev_forecast, actual, alpha=ALPHA):
    return round(alpha * actual + (1 - alpha) * prev_forecast)

def calculate_order(role_data, prev_order, prev_forecast, beta, is_retailer=False):
    incoming = role_data["incoming_orders"]
    backlog = role_data["backlog"]
    inventory = role_data["inventory"]
    arriving = role_data["arriving_shipments"]

    # Efektiivne nõudlus = sissetulev tellimus + backlog
    effective_demand = incoming + backlog

    # Soovitud laoseis = prognoos + ohutusvaru
    desired_inventory = prev_forecast * (1 + K_SAFETY)

    # Tellimus = prognoos + korrektsioon (laoseis + backlog)
    base_order = prev_forecast + beta * (effective_demand - arriving - inventory + desired_inventory - inventory)

    # Piirame muutust võrreldes eelmise tellimusega
    max_change = prev_order * MAX_ORDER_CHANGE
    order = max(prev_order - max_change, min(prev_order + max_change, base_order))

    return max(0, round(order))

@app.route('/api/decision', methods=['POST'])
def decision():
    data = request.get_json() or {}

    # === HANDSHAKE ===
    if data.get("handshake") is True:
        return jsonify({
            "ok": True,
            "student_email": "evtimm@taltech.ee",  # ← MUUDA SIIN!
            "algorithm_name": "MeSellingBeerMuch",
            "version": "v26.0.0",
            "supports": {"blackbox": True, "glassbox": False},
            "message": "BeerBot ready",
        })

    # === WEEKLY STEP ===
    weeks = data.get("weeks", [])
    if not weeks:
        return jsonify({"orders": {"retailer": 10, "wholesaler": 10, "distributor": 10, "factory": 10}})

    current_week = weeks[-1]
    week_num = current_week["week"]
    mode = data.get("mode", "blackbox")

    roles = current_week["roles"]
    prev_orders = current_week["orders"]

    # Algväärtused esimesel nädalal
    if week_num == 1:
        forecast = {role: roles[role]["incoming_orders"] for role in roles}
        prev_order_dict = {role: 10 for role in roles}  # default
    else:
        # Taasta eelmine prognoos ja tellimus
        prev_week = weeks[-2]
        prev_orders = prev_week["orders"]
        # Lihtne: kasuta eelmise nädala sissetulevat tellimust kui prognoosi alus
        forecast = {}
        for role in roles:
            actual_demand = roles[role]["incoming_orders"]
            prev_forecast = prev_orders[role] if week_num == 2 else forecast.get(role, actual_demand)
            forecast[role] = smooth_demand(prev_forecast, actual_demand, ALPHA)
        prev_order_dict = prev_orders

    # Arvuta uued tellimused
    orders = {}
    for role in ["retailer", "wholesaler", "distributor", "factory"]:
        role_data = roles[role]
        prev_order = prev_order_dict.get(role, 10)
        prev_forecast = forecast.get(role, role_data["incoming_orders"])

        beta = BETA_RETAILER if role == "retailer" else BETA_OTHER
        is_retailer = (role == "retailer")

        # Blackbox režiim: kasuta ainult oma rolli andmeid
        if mode == "blackbox" and role != "retailer":
            # Simuleeri, et teised rollid näevad ainult oma andmeid
            # (kuid meil on siiski kõik andmed, seega kasutame siiski oma rolli)
            pass

        order = calculate_order(role_data, prev_order, prev_forecast, beta, is_retailer)
        orders[role] = order

    return jsonify({"orders": orders})

if __name__ == "__main__":
    app.run(debug=False)
