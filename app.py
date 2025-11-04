from __future__ import annotations
from typing import Dict, Any, Tuple, List
from flask import Flask, request, jsonify
import math
import numpy as np

# --- Handshake meta (MUUDA OMAKS) ---
STUDENT_EMAIL = "evtimm@taltech.ee"
ALGO_NAME = "EvaSellsBeer"
VERSION = "v2.0.1"
SUPPORTS = {"blackbox": True, "glassbox": False}
HANDSHAKE_MESSAGE = "Optimized BeerBot ready for smooth supply chain."

app = Flask(__name__)

# --- Optimeeritud parameetrid ---
ALPHA = 0.25           # Nõudluse silumine (madalam, et reaktsioon oleks rahulikum)
K_SAFETY = 0.8         # Ohutusvaru kordaja (natuke kõrgem, et absorbeerida prognoosiviga)
REVIEW_TIME = 1        # R: Iganädalane läbivaatus
LEAD_TIME = 2          # L: Tarnetsükli viivitus (Tavaline Beer Game'i standard)
H_TARGET = REVIEW_TIME + LEAD_TIME # H: Kogu täitmisaeg (Target Period)

# Dämpimise koefitsient (beta) rolli järgi. Madalamad väärtused ülesvoolu lülides (distributor, factory)
# vähendavad Bullwhip'i efekti, siludes reaktsiooni IP puudujäägile.
BETA_BY_ROLE = {
    "retailer": 0.8,
    "wholesaler": 0.6,
    "distributor": 0.4,
    "factory": 0.25, # Tehases on dämpimine kõige olulisem
}
MAX_ORDER_CHANGE = 0.3 # Maksimaalne lubatud muutus võrreldes eelmise tellimusega (±30%)
ROLES = ["retailer", "wholesaler", "distributor", "factory"]


def round_half_up(x: float) -> int:
    """Ümardab pool üles ja tagab, et tulemus pole negatiivne."""
    return max(0, int(math.floor(x + 0.5)))


def smooth_forecast_and_mad(weeks: List[Dict[str, Any]], role: str, alpha: float) -> Tuple[float, float]:
    """
    Eksponentsiaalne silumine ja eksponentsiaalselt silutud absoluutne hälve (MAD).
    Kasutab incoming_orders seeriat (nõudlust).
    """
    fc = None
    mad = 0.0
    
    # Eeldame, et nõudlus on eelmise lüli tellimus, jaemüüjal on see kliendi nõudlus.
    demand_key = "customer_orders" if role == "retailer" else "incoming_orders"

    for i, w in enumerate(weeks):
        d = max(0, int(w["roles"][role].get(demand_key, 0)))
        
        if fc is None:
            # Algne prognoos = esimene nõudlus
            fc = float(d)
        else:
            prev_d = float(weeks[i-1]["roles"][role].get(demand_key, d)) # Eelmise perioodi D
            # Prognoos (Exponential Smoothing)
            fc = alpha * d + (1 - alpha) * fc
            # MAD (Mean Absolute Deviation, silutud)
            mad = alpha * abs(d - prev_d) + (1 - alpha) * mad
            
    # Kasutame stabiilsuse tagamiseks MAD väärtuse asemel viimase MAE väärtust
    # Kui MAD on väga väike, kasutame vaikimisi väärtust, mis arvestab väikseima stabiilse nõudlusega.
    return (fc or 0.0), max(1.0, mad or fc / 5.0 or 1.0)


def calculate_pipeline(weeks: List[Dict[str, Any]], role: str) -> int:
    """
    Arvutab laoseisus olevad tellimused (Pipeline Inventory) nende saadetiste alusel, 
    mis on tellitud, aga pole veel saabunud.
    
    See on Beer Game'i kõige keerulisem osa: me ei tea otse, mis on "teel", 
    seega rekonstrueerime selle viimastest tellimustest L-1 nädala jooksul.
    """
    if LEAD_TIME <= 1 or not weeks:
        return 0
    
    # Võtame arvesse viimased (L - 1) nädalat, sest tellimus t-Lhat on just nüüd saabunud
    # ja tellimus t on antud praegu. Me ei tea, mis hetkel tellimus t-1 saabub.
    orders = [int(w.get("orders", {}).get(role, 0)) for w in weeks]
    
    # Tõhusam on eeldada, et O(t), O(t-1) ... O(t-L+1) on teel
    # Võtame viimased (L-1) tellimust, alates eelmisest nädalast (-2) kuni (-L)
    take = min(LEAD_TIME - 1, len(orders) - 1)
    
    # Summeerime tellimused alates eelmisest nädalast kuni (L-1) nädalat tagasi.
    pipeline_sum = sum(orders[-(i + 1)] for i in range(take))
    
    return pipeline_sum


def decide_for_role(weeks: List[Dict[str, Any]], role: str) -> int:
    """Põhiline Order-Up-To poliitika loogika."""
    
    current_state = weeks[-1]["roles"][role]
    
    # 1) H: Kogu täitmisaeg (fikseeritud)
    H = H_TARGET 

    # 2) Prognoos (fc) ja Viga (mad)
    fc, mad = smooth_forecast_and_mad(weeks, role, ALPHA)

    # 3) Ohutusvaru (Safety Stock)
    # SS = K * MAD * sqrt(H)
    safety_stock = K_SAFETY * mad * math.sqrt(H)
    
    # 4) Inventuuri positsioon (Inventory Position, IP)
    # IP = Inventory + Pipeline - Backlog
    
    inventory = int(current_state.get("inventory", 0))
    backlog = int(current_state.get("backlog", 0))
    pipeline = calculate_pipeline(weeks, role)
    
    IP = inventory - backlog + pipeline

    # 5) Sihttase (Order-Up-To Level, S)
    # S = H * FC + SS
    target_S = fc * H + safety_stock

    # 6) Puudujääk (GAP)
    gap = target_S - IP
    
    # 7) Baas-tellimus Q* (silutud reaktsioon puudujäägile)
    # Q* = BETA * GAP + (1 - BETA) * Previous_Order
    
    beta = BETA_BY_ROLE.get(role, 0.5)
    q_last = max(0, int(weeks[-1].get("orders", {}).get(role, 0)))
    
    q_base = beta * gap + (1 - beta) * q_last

    # 8) Piirangud (Ramp Constraint)
    # Peamine parandus: piirame tellimuste muutuse maksimaalselt MAX_ORDER_CHANGE protsendiga.
    
    # Maksimaalne lubatud muutus (minimaalselt 1)
    ramp = max(1, round_half_up(MAX_ORDER_CHANGE * max(fc, 1.0)))
    
    # Min ja Max tellimuse piirid eelmise tellimuse suhtes
    q_min_ramp = max(0, q_last - ramp)
    q_max_ramp = q_last + ramp
    
    # Rakendame piirangud
    q_final = min(q_max_ramp, max(q_min_ramp, round_half_up(q_base)))

    return max(0, int(q_final))


@app.post("/api/decision")
def decision():
    """Võtab vastu API päringu ja väljastab tellimused."""
    body: Dict[str, Any] = request.get_json(force=True, silent=True) or {}

    # Handshake - Protokolli kinnitus
    if body.get("handshake") is True:
        return jsonify({
            "ok": True,
            "student_email": STUDENT_EMAIL,
            "algorithm_name": ALGO_NAME,
            "version": VERSION,
            "supports": SUPPORTS,
            "message": HANDSHAKE_MESSAGE,
        }), 200

    # Weekly Decision - Iganädalane otsus
    weeks = body.get("weeks", [])
    if not isinstance(weeks, list) or not weeks:
        # Vaikimisi tellimus, kui andmed puuduvad
        return jsonify({"orders": {r: 10 for r in ROLES}}), 200

    try:
        orders = {r: decide_for_role(weeks, r) for r in ROLES}
        # Garanteeri, et tellimus on täisarv ja >= 0
        orders = {k: max(0, int(v)) for k, v in orders.items()}
        return jsonify({"orders": orders}), 200
    except Exception as e:
        # Vigade käsitlemine
        print(f"Decision calculation failed: {e}")
        # Vaikimisi tellimus veaolukorras
        return jsonify({"orders": {r: 10 for r in ROLES}}), 200
