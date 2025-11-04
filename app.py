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

# --- Optimeeritud parameetrid (ÄÄRMUSLIK KONTROLL) ---
ALPHA = 0.10           # Nõudluse silumine: Hoiame stabiilsena.
K_SAFETY = 2.50        # Ohutusvaru kordaja: ÄÄRMUSLIKULT agressiivne seade Backlog'i vältimiseks.

REVIEW_TIME = 1        # R: Iganädalane läbivaatus
LEAD_TIME = 2          # L: Tarnetsükli viivitus (Standard)
H_TARGET = REVIEW_TIME + LEAD_TIME # H: Kogu täitmisaeg (Target Period = 3 nädalat)

# Dämpimise koefitsient (beta): ÄÄRMUSLIKULT madalad väärtused, et neutraliseerida Bullwhip'i, mille võiks tekitada kõrge K_SAFETY.
BETA_BY_ROLE = {
    "retailer": 0.08,      # Väga madal.
    "wholesaler": 0.05,    # Äärmiselt madal.
    "distributor": 0.03,   # Peaaegu null.
    "factory": 0.01,       # Peaaegu null.
}
# Piirame tellimuse muutust (RAMP): Peab olema väga väike, et vältida K_SAFETY põhjustatud ületellimisi.
MAX_ORDER_CHANGE = 0.1 # VÄGA VÄIKE PIIRANG.
ROLES = ["retailer", "wholesaler", "distributor", "factory"]
INITIAL_DEMAND_ESTIMATE = 10 # Eeldame esimesel nädalal algnõudlust 10


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
    
    # Esimestel nädalatel kasutame algset hinnangut, et vältida nulliga jagamist või ebastabiilsust
    initial_demand = INITIAL_DEMAND_ESTIMATE

    for i, w in enumerate(weeks):
        # Võtame sissetuleva tellimuse (mis on tegelik nõudlus sellele lülile)
        d = max(0, int(w["roles"][role].get(demand_key, 0)))
        
        if fc is None:
            # Algne prognoos (FC)
            # Eeldus: FC0 = esimene tegelik nõudlus, kui see on olemas, muidu algväärtus.
            fc = float(d) if d > 0 else float(initial_demand)
        else:
            # Eelmise perioodi nõudluse leidmine MAD arvutamiseks
            if i > 0:
                # Kui i > 0, peaks weeks[i-1] sisaldama tegeliku nõudluse andmeid.
                prev_w = weeks[i-1]
                prev_d = float(prev_w["roles"][role].get(demand_key, initial_demand)) 
            else:
                # i=0 (esimene nädal), kasutame algväärtust
                prev_d = float(initial_demand)
            
            # Prognoos (Exponential Smoothing)
            fc = alpha * d + (1 - alpha) * fc
            # MAD (Mean Absolute Deviation, silutud)
            mad = alpha * abs(d - prev_d) + (1 - alpha) * mad
            
    # Tagasta prognoos ja stabiilne MAD väärtus (garanteeri vähemalt 1.0)
    return (fc or float(initial_demand)), max(1.0, mad or fc / 5.0 or 1.0)


def calculate_pipeline(weeks: List[Dict[str, Any]], role: str) -> int:
    """
    Arvutab laoseisus olevad tellimused (Pipeline Inventory) nende saadetiste alusel, 
    mis on tellitud, aga pole veel saabunud.
    """
    # Eeldus: L=2. Teel on tellimused, mis anti nädalal t-1.
    if LEAD_TIME <= 1 or len(weeks) < LEAD_TIME:
         # Kui andmeid pole piisavalt, on pipeline 0.
        return 0
    
    # Vastavalt juhendile on tellimus Q(t) tehtud nädalal t ja see saabub nädalal t+L.
    # Nädalal N on teel tellimused, mis anti N-1, N-2, ... N-(L-1) nädalatel.
    
    # Orders massiivi pikkus on N (alates nädalast 1 kuni N)
    orders = [int(w.get("orders", {}).get(role, 0)) for w in weeks]
    
    # Teel olevate tellimuste arv on L-1
    take = LEAD_TIME - 1
    
    # Summeerime tellimused, mis on teel (nt L=2, siis teel on 2-1=1 tellimus: weeks[-1])
    # Tuleb veenduda, et orders massiivis on piisavalt elemente, et tagasi vaadata take võrra.
    if len(orders) < take:
        return 0

    # orders[-1] on eelmise nädala tellimus (Q(N-1)), orders[-2] on Q(N-2) jne.
    # Alustame tagant ja summeerime 'take' arvu tellimusi.
    pipeline_sum = sum(orders[-(i + 1)] for i in range(take))
    
    return pipeline_sum


def decide_for_role(weeks: List[Dict[str, Any]], role: str) -> int:
    """Põhiline Order-Up-To poliitika loogika (S-s poliitika)."""
    
    # Nädala 1 erikäsitlus: kui weeks on pikkusega 1 ja see on simulatsiooni algus
    if len(weeks) == 1 and weeks[0].get("week") == 1:
        # Hoidke esimene tellimus madalal, et vältida esialgset Bullwhip'i
        return INITIAL_DEMAND_ESTIMATE

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
    # 'arriving_shipments' on juba Inventori poolt arvesse võetud (selle nädala saabumine)
    # See väärtus on juba 'inventory' sees, seega ei lisa seda IP-le, vaid arvestame 
    # ainult teel olevaid tellimusi, mis POLE veel saabunud (st Q(t-1), Q(t-2)).
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
    
    # Eelmise nädala tellimus (see on weeks[-1]["orders"])
    q_last = max(0, int(weeks[-1].get("orders", {}).get(role, 0)))
    
    # Uus tellimus on tasakaalustatud: reaktsioon puudujäägile ja inerts eelmise tellimuse suhtes
    q_base = beta * gap + (1 - beta) * q_last

    # 8) Piirangud (Ramp Constraint)
    # Piirame tellimuste muutuse maksimaalselt MAX_ORDER_CHANGE protsendiga.
    
    # Maksimaalne lubatud muutus (minimaalselt 1)
    ramp = max(1, round_half_up(MAX_ORDER_CHANGE * max(fc, 1.0)))
    
    # Min ja Max tellimuse piirid eelmise tellimuse suhtes
    q_min_ramp = max(0, q_last - ramp)
    q_max_ramp = q_last + ramp
    
    # Rakendame piirangud
    q_final = min(q_max_ramp, max(q_min_ramp, round_half_up(q_base)))
    
    # Lõplik tellimus ei saa olla väiksem kui 0.
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
        return jsonify({"orders": {r: INITIAL_DEMAND_ESTIMATE for r in ROLES}}), 200

    try:
        orders = {r: decide_for_role(weeks, r) for r in ROLES}
        # Garanteeri, et tellimus on täisarv ja >= 0
        orders = {k: max(0, int(v)) for k, v in orders.items()}
        return jsonify({"orders": orders}), 200
    except Exception as e:
        # Vigade käsitlemine
        print(f"Decision calculation failed: {e}")
        # Vaikimisi tellimus veaolukorras
        return jsonify({"orders": {r: INITIAL_DEMAND_ESTIMATE for r in ROLES}}), 200
