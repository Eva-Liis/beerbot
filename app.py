from flask import Flask, request, jsonify
import math

app = Flask(__name__)

# === KONSTANDID ===
LEAD_TIME = 2
K_SAFETY = 1.0
BETA_RETAILER = 0.4
BETA_OTHER = 0.15
MAX_ORDER_CHANGE = 0.25
ALPHA = 0.15
SEED = 2025

# Deterministlik
import random
random.seed(SEED)

# === ABI FUNKTSIOONID ===
def smooth_demand(prev_forecast, actual):
    return round(ALPHA * actual + (1 - ALPHA) * prev_forecast)

def calculate_pipeline(weeks, role, week_num):
    if week_num < 3:
        return 0 if week_num == 1 else weeks[0]["orders"][role]
    owed = 0
    for k in range(2, week_num):  # 0-based index, k from 2 to week_num-1
        order_2weeks_ago = weeks[k-2]["orders"][role]
        arriving = weeks[k]["roles"][role]["arriving_shipments"]
        delta = arriving - order_2weeks_ago
        if delta > 0:
            owed -= delta
        else:
            owed -= delta  # -= negative = +
        owed = max(0, owed)
    # Pipeline = owed + order_last_week
    return owed + weeks[-2]["orders"][role]

def calculate_order(role_data, prev_order, prev_forecast, beta, pipeline, week_num, weeks, role):
    incoming = role_data["incoming_orders"]
    backlog = role_data["backlog"]
    inventory = role_data["inventory"]
    arriving = role_data["arriving_shipments"]

    # Simuleeri uuendus
    effective_inventory = inventory + arriving
    shipped = min(effective_inventory, incoming + backlog)
    new_inventory = effective_inventory - shipped
    new_backlog = incoming + backlog - shipped

    # Desired = forecast * (lead_time + safety)
    desired = prev_forecast * (LEAD_TIME + K_SAFETY)

    # Korrektsioon
    adjustment = beta * (desired + new_backlog - new_inventory - pipeline)

    base_order = prev_forecast + adjustment

    # Piira muutust
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
            "student_email": "evtimm@taltech.ee",  # MUUDA!
            "algorithm_name": "EvaSellsBeerMuch",
            "version": "v27.0.0",
            "supports": {"blackbox": True, "glassbox": True},
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
    prev_orders = current_week["orders"] if week_num == 1 else weeks[-2]["orders"]

    # Forecast'i taastamine
    forecast = {}
    if week_num == 1:
        for role in roles:
            forecast[role] = roles[role]["incoming_orders"]
    else:
        for role in roles:
            actual = roles[role]["incoming_orders"]
            prev_f = forecast.get(role, actual) if 'forecast' in locals() else actual  # Inits
            forecast[role] = smooth_demand(prev_f, actual)

    # Arvuta tellimused
    orders = {}
    for role in ["retailer", "wholesaler", "distributor", "factory"]:
        role_data = roles[role]
        prev_order = prev_orders[role] if week_num > 1 else 10
        prev_forecast = forecast[role]

        beta = BETA_RETAILER if role == "retailer" else BETA_OTHER

        # Pipeline ainult oma rolli ajalooga (blackbox)
        pipeline = calculate_pipeline(weeks, role, week_num)

        order = calculate_order(role_data, prev_order, prev_forecast, beta, pipeline, week_num, weeks, role)
        orders[role] = order

    return jsonify({"orders": orders})

if __name__ == "__main__":
    app.run(debug=False)
