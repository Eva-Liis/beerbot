#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
EE RK — 'listitu' kompensatsioon Rootsi modifitseeritud Sainte-Laguë'ga:
- Parteidevaheline jaotus: modified Sainte-Laguë (Rootsi): divisors = [1.2, 3, 5, 7, ...],
  "jäta m jagajat vahele" (m = isiku+ringkonna kohad).
- Parteisisene valik kompensatsioonikohtadele: isiklike häälte DESC, viigis quota DESC, siis nimi.
- Vaikimisi: parteikünnis 5% (üleriigiline), kandidaadi lävend quota ≥ 0.05.
- Sisend: RESULTS.xml samast kaustast (või uusim *.xml).
- Väljund: allocation_export_alt.csv (semikoolon; UTF-8 BOM).

Kasutus:
  python rk_sweden_msl_listitu.py
  # valikuline:
  #   --xml PATH --out PATH --threshold-party 0.05 --candidate-min-quota 0.05 --total-seats 101
"""

import sys
import csv
import argparse
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Tuple, List
import heapq

NS = {'rk': 'https://opendata.valimised.ee/schemas/election-result/rk/v2/'}
TOTAL_SEATS_DEFAULT = 101
THRESHOLD_PARTY_DEFAULT = 0.05
CANDIDATE_MIN_QUOTA_DEFAULT = 0.05  # 5% ringkonna lihtkvoodist

# ---------- util ----------
def base_dir() -> Path:
    try:
        return Path(__file__).resolve().parent
    except NameError:
        return Path.cwd()

def autodetect_xml(base: Path) -> Path:
    cand = base / "RESULTS.xml"
    if cand.exists():
        return cand
    xmls = sorted(base.glob("*.xml"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not xmls:
        raise FileNotFoundError("Ei leitud RESULTS.xml ega *.xml.")
    return xmls[0]

def to_int_votes(s: str) -> int:
    s = (s or "").replace("\u00A0","").replace(" ","")
    if s == "":
        return 0
    try:
        return int(s)
    except:
        try:
            return int(float(s))
        except:
            return 0

def to_float(s: str) -> float:
    try:
        return float(s)
    except:
        return 0.0

def dec_comma(s: str) -> str:
    return (s or "").replace(".", ",") if s else ""

def fmt_int_spaces(s: str) -> str:
    n = to_int_votes(s)
    return f"{n:,}".replace(",", " ")

# ---------- XML ----------
def parse_votes_by_party(root) -> Dict[str, int]:
    votes = {}
    for p in root.findall('.//rk:data/rk:electionResult/rk:votesAndMandates/rk:party', NS):
        code = (p.findtext('rk:code', default='', namespaces=NS) or '').strip()
        vtxt = (p.findtext('rk:votes', default='0', namespaces=NS) or '0').replace('\u00A0','').replace(' ','')
        v = int(vtxt) if vtxt.isdigit() else int(float(vtxt))
        if code:
            votes[code] = v
    return votes

def map_registration_to_party(root) -> Dict[str, Tuple[str,str]]:
    reg2party = {}
    for party in root.findall('.//rk:data/rk:electionResult//rk:party', NS):
        code = party.findtext('rk:code', default='', namespaces=NS) or ''
        name = party.findtext('rk:name', default='', namespaces=NS) or ''
        for cand in party.findall('.//rk:candidates/rk:candidate', NS):
            reg = cand.findtext('rk:registrationNumber', default='', namespaces=NS) or ''
            if reg:
                reg2party[reg] = (code, name)
    return reg2party

def collect_all_candidates(root, reg2party) -> List[dict]:
    rows = []
    for cand in root.findall(".//rk:candidate", NS):
        g = lambda tag: cand.findtext('rk:'+tag, default='', namespaces=NS)
        reg = g('registrationNumber')
        pc, pn = reg2party.get(reg, ('',''))
        rows.append({
            "forename": g('forename'),
            "surname": g('surname'),
            "partyCode": pc,
            "partyName": pn,
            "districtNumber": g('districtNumber'),
            "votes": g('votes'),
            "quota": g('quota'),
            "comparativeFigure": g('comparativeFigure'),
            "registrationNumber": reg,
            "mandateType": g('mandateType'),    # PERSONAL/DISTRICT/COMPENSATION/'' 
            "elected": (g('elected').lower() == 'true'),
            "finalPositionNumber": g('finalPositionNumber') or '',
        })
    return rows

# ---------- Rootsi MSL jaotus ----------
def allocate_compensation_sweden_msl(
    votes: Dict[str,int],
    preallocated: Dict[str,int],
    total_seats: int,
    threshold_party: float,
    first_divisor: float = 1.2,   # Rootsi alates 2018: 1.2 (varem 1.4)
) -> Dict[str,int]:
    total_votes = sum(votes.values())
    eligible = {p for p,v in votes.items() if total_votes>0 and (v/total_votes)>=threshold_party}
    seats_already = sum(preallocated.get(p,0) for p in votes)
    seats_to_alloc = total_seats - seats_already
    if seats_to_alloc <= 0:
        return {p:0 for p in votes}

    # divisors sequence per party: [1.2, 3, 5, 7, ...]
    def divisor_for(j_index: int) -> float:
        return first_divisor if j_index == 0 else (2*j_index + 1)

    def push(heap, party, j_index):
        d = divisor_for(j_index)
        q = votes[party] / d if votes[party] > 0 else 0.0
        heapq.heappush(heap, (-q, -votes[party], party, j_index))

    heap = []
    comp = {p: 0 for p in votes}

    for p in eligible:
        m = preallocated.get(p,0)  # "jäta m jagajat vahele"
        push(heap, p, m)

    for _ in range(seats_to_alloc):
        if not heap: break
        _,_,p,j = heapq.heappop(heap)
        comp[p] += 1
        push(heap, p, j+1)

    for p in votes:
        if p not in eligible:
            comp[p] = 0
    return comp

# ---------- CSV ----------
def write_csv_like_example(rows_out: List[dict], out_path: Path):
    fields = ["Jrk nr","Kvoot","Võrdlusarv","Ringkond","Nimekiri","Hääli kokku","Reg nr","Kandidaadi nimi","Mandaatide omandamise viis"]
    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter=';')
        w.writeheader()
        for r in rows_out:
            w.writerow(r)

# ---------- pipeline ----------
def build_output_listitu_sweden(
    all_cands: List[dict],
    votes: Dict[str,int],
    total_seats: int,
    threshold_party: float,
    candidate_min_quota: float,
    first_divisor: float = 1.2,
) -> List[dict]:
    # 1) olemasolevad PERSONAL ja DISTRICT
    elected_personal = [c for c in all_cands if c["elected"] and c["mandateType"]=="PERSONAL"]
    elected_district = [c for c in all_cands if c["elected"] and c["mandateType"]=="DISTRICT"]

    # 2) preallocated
    preallocated = Counter()
    for c in elected_personal + elected_district:
        preallocated[c["partyCode"]] += 1

    # 3) Rootsi MSL kompensatsioon jaotus parteidele
    comp_counts = allocate_compensation_sweden_msl(
        votes=votes,
        preallocated=preallocated,
        total_seats=total_seats,
        threshold_party=threshold_party,
        first_divisor=first_divisor,
    )

    # 4) partei-sisene pingerida (listitu): votes DESC, tie quota DESC, then name; quota ≥ kand. lävend
    not_elected = [c for c in all_cands if not c["elected"]]
    pool = defaultdict(list)
    for c in not_elected:
        if not c["partyCode"]:
            continue
        if to_float(c["quota"]) < candidate_min_quota:
            continue
        pool[c["partyCode"]].append(c)

    for p in pool:
        pool[p].sort(
            key=lambda c: (
                -to_int_votes(c["votes"]),
                -to_float(c["quota"]),
                c["surname"], c["forename"]
            )
        )

    # 5) vormista: PERSONAL -> DISTRICT -> COMPENSATION (ALT: Rootsi MSL, listitu)
    out_rows = []

    def row_of(c, label):
        comp = c["comparativeFigure"]
        comp_out = "" if comp in ("0","0.0","0.00","0.000","0.0000","") else comp
        return {
            "Kvoot": (c["quota"] or "").replace(".", ","),
            "Võrdlusarv": (comp_out or "").replace(".", ","),
            "Ringkond": c["districtNumber"],
            "Nimekiri": c["partyCode"],
            "Hääli kokku": fmt_int_spaces(c["votes"]),
            "Reg nr": c["registrationNumber"],
            "Kandidaadi nimi": f"{c['forename']} {c['surname']}".strip(),
            "Mandaatide omandamise viis": label,
        }

    for c in sorted(elected_personal, key=lambda x: (x["partyCode"], -to_float(x["quota"]))):
        out_rows.append(row_of(c, "Ringkondades lihtkvoodi alusel jaotatud isikumandaadid"))

    for c in sorted(elected_district, key=lambda x: (x["partyCode"], -to_float(x["comparativeFigure"]))):
        out_rows.append(row_of(c, "Ringkondades lihtkvoodi alusel jaotatud ringkonnamandaadid"))

    label_alt = "Kogu riigi peale jaotatud kompensatsioonimandaadid (ALT: Rootsi MSL, listitu)"
    for party, need in comp_counts.items():
        cand_list = pool.get(party, [])
        take = min(need, len(cand_list))
        for i in range(take):
            out_rows.append(row_of(cand_list[i], label_alt))

    # JRK NR
    for i, r in enumerate(out_rows, start=1):
        r["Jrk nr"] = i

    # ohutus
    if len(out_rows) > total_seats:
        out_rows = out_rows[:total_seats]
        sys.stderr.write("Märkus: lõikasin ülejäägi, et ridu oleks täpselt 101.\n")

    return out_rows

def main():
    base = base_dir()
    ap = argparse.ArgumentParser(description="EE RK — 'listitu' kompensatsioon Rootsi modifitseeritud Sainte-Laguë’ga.")
    ap.add_argument("--xml", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--threshold-party", type=float, default=THRESHOLD_PARTY_DEFAULT)
    ap.add_argument("--candidate-min-quota", type=float, default=CANDIDATE_MIN_QUOTA_DEFAULT)
    ap.add_argument("--total-seats", type=int, default=TOTAL_SEATS_DEFAULT)
    ap.add_argument("--first-divisor", type=float, default=1.2, help="Rootsi esimene jagaja (1.2; varem 1.4)")
    args = ap.parse_args()

    xml_path = args.xml if args.xml else autodetect_xml(base)
    out_csv = args.out if args.out else (base / "allocation_export_alt.csv")

    root = ET.parse(xml_path).getroot()
    votes = parse_votes_by_party(root)
    reg2party = map_registration_to_party(root)
    all_cands = collect_all_candidates(root, reg2party)

    rows = build_output_listitu_sweden(
        all_cands=all_cands,
        votes=votes,
        total_seats=args.total_seats,
        threshold_party=args.threshold_party,
        candidate_min_quota=args.candidate_min_quota,
        first_divisor=args.first_divisor,
    )

    write_csv_like_example(rows, out_csv)
    print(f"OK: kirjutasin {out_csv} (Rootsi MSL; esimene jagaja={args.first_divisor}, party_künnis={args.threshold_party}, cand_quota≥{args.candidate_min_quota})")

if __name__ == "__main__":
    main()
