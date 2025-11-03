#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import csv
import argparse
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Dict, Tuple
import heapq

RK_NS = {'rk': 'https://opendata.valimised.ee/schemas/election-result/rk/v2/'}
TOTAL_SEATS = 101
THRESHOLD = 0.05
ALPHA = 0.9

def _base_dir() -> Path:
    try:
        return Path(__file__).resolve().parent
    except NameError:
        return Path.cwd()

def _autodetect_xml(base: Path) -> Path:
    cand = base / "RESULTS.xml"
    if cand.exists():
        return cand
    xmls = sorted(base.glob("*.xml"), key=lambda p: p.stat().st_mtime, reverse=True)
    if xmls:
        return xmls[0]
    raise FileNotFoundError("Ei leitud RESULTS.xml ega ühtegi *.xml faili samas kaustas.")

def parse_votes_by_party(root) -> Dict[str, int]:
    votes = {}
    for p in root.findall('.//rk:data/rk:electionResult/rk:votesAndMandates/rk:party', RK_NS):
        code_el = p.find('rk:code', RK_NS)
        votes_el = p.find('rk:votes', RK_NS)
        if code_el is None or votes_el is None:
            continue
        code = (code_el.text or "").strip()
        vtxt = (votes_el.text or "0").replace('\u00A0','').replace(' ','')
        try:
            v = int(vtxt)
        except:
            v = int(float(vtxt))
        if code:
            votes[code] = v
    return votes

def map_registration_to_party(root) -> Dict[str, Tuple[str, str]]:
    """Kandidaadi registratsiooninumber -> (partyCode, partyName) KOGU electionResult harust."""
    reg2party = {}
    for party in root.findall('.//rk:data/rk:electionResult//rk:party', RK_NS):
        code = (party.findtext('rk:code', default='', namespaces=RK_NS) or '')
        name = (party.findtext('rk:name', default='', namespaces=RK_NS) or '')
        for cand in party.findall('.//rk:candidates/rk:candidate', RK_NS):
            reg = cand.findtext('rk:registrationNumber', default='', namespaces=RK_NS)
            if reg:
                reg2party[reg] = (code, name)
    return reg2party

def collect_elected(root, reg2party):
    elected_rows = []
    counts_by_party = {'PERSONAL': Counter(), 'DISTRICT': Counter(), 'COMPENSATION': Counter()}
    for cand in root.findall(".//rk:candidate[rk:elected='true']", RK_NS):
        g = lambda tag: cand.findtext('rk:'+tag, default='', namespaces=RK_NS)
        reg = g('registrationNumber')
        party_code, party_name = reg2party.get(reg, ('',''))
        row = {
            "finalPositionNumber": int(g('finalPositionNumber') or 0),
            "forename": g('forename'),
            "surname": g('surname'),
            "partyCode": party_code,
            "partyName": party_name,
            "districtNumber": g('districtNumber'),
            "votes": g('votes'),
            "quota": g('quota'),
            "comparativeFigure": g('comparativeFigure'),
            "registrationNumber": reg,
            "mandateType": g('mandateType'),
        }
        elected_rows.append(row)
        mt = row["mandateType"] or ""
        if mt in counts_by_party:
            counts_by_party[mt][party_code] += 1
    elected_rows.sort(key=lambda r: r["finalPositionNumber"])
    return elected_rows, counts_by_party

def allocate_compensation_modified_dhondt(
    votes: Dict[str, int],
    preallocated: Dict[str, int],
    total_seats: int = TOTAL_SEATS,
    threshold: float = THRESHOLD,
    alpha: float = ALPHA,
) -> Dict[str, int]:
    total_votes = sum(votes.values())
    eligible = {p for p, v in votes.items() if total_votes > 0 and (v / total_votes) >= threshold}
    seats_already = sum(preallocated.get(p, 0) for p in votes)
    seats_to_allocate = total_seats - seats_already
    if seats_to_allocate <= 0:
        return {p: 0 for p in votes}

    def push(heap, party, j):
        q = votes[party] / (j ** alpha) if votes[party] > 0 else 0.0
        heapq.heappush(heap, (-q, -votes[party], party, j))  # viik: rohkem hääli eelis

    heap = []
    comp = {p: 0 for p in votes}
    for p in eligible:
        j0 = preallocated.get(p, 0) + 1
        push(heap, p, j0)

    for _ in range(seats_to_allocate):
        if not heap:
            break
        _, _, p, j = heapq.heappop(heap)
        comp[p] += 1
        push(heap, p, j + 1)

    for p in votes:
        if p not in eligible:
            comp[p] = 0
    return comp

def _fmt_int_spaces(s: str) -> str:
    s_clean = (s or "").replace("\u00A0","").replace(" ","")
    if s_clean == "":
        return ""
    try:
        n = int(s_clean)
    except:
        n = int(float(s_clean))
    return f"{n:,}".replace(",", " ")

def _dec_comma(s: str) -> str:
    return (s or "").replace(".", ",") if s else ""

def write_output_csv(elected_rows, out_csv: Path):
    mandate_text = {
        "PERSONAL": "Ringkondades lihtkvoodi alusel jaotatud isikumandaadid",
        "DISTRICT": "Ringkondades lihtkvoodi alusel jaotatud ringkonnamandaadid",
        "COMPENSATION": "Kogu riigi peale jaotatud kompensatsioonimandaadid",
    }
    fields = ["Jrk nr","Kvoot","Võrdlusarv","Ringkond","Nimekiri","Hääli kokku","Reg nr","Kandidaadi nimi","Mandaatide omandamise viis"]
    with out_csv.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter=';')
        w.writeheader()
        for row in elected_rows:
            comp = row["comparativeFigure"]
            comp_out = "" if comp in ("0","0.0","0.00","0.000","0.0000","") else comp
            w.writerow({
                "Jrk nr": row["finalPositionNumber"],
                "Kvoot": _dec_comma(row["quota"]),
                "Võrdlusarv": _dec_comma(comp_out),
                "Ringkond": row["districtNumber"],
                "Nimekiri": row["partyCode"],
                "Hääli kokku": _fmt_int_spaces(row["votes"]),
                "Reg nr": row["registrationNumber"],
                "Kandidaadi nimi": f"{row['forename']} {row['surname']}".strip(),
                "Mandaatide omandamise viis": mandate_text.get(row["mandateType"], row["mandateType"]),
            })

def main():
    base = _base_dir()
    ap = argparse.ArgumentParser(description="EE RK kompensatsioon (mod d’Hondt α=0.9) + eksport CSV, lokaalsest XML-ist.")
    ap.add_argument("--xml", type=Path, default=None, help="Sisend XML (vaikimisi sama kausta RESULTS.xml või uusim *.xml)")
    ap.add_argument("--out", type=Path, default=None, help="Väljund CSV (vaikimisi allocation_export.csv samas kaustas)")
    ap.add_argument("--alpha", type=float, default=ALPHA)
    ap.add_argument("--threshold", type=float, default=THRESHOLD)
    ap.add_argument("--total-seats", type=int, default=TOTAL_SEATS)
    args = ap.parse_args()

    xml_path = args.xml if args.xml is not None else _autodetect_xml(base)
    out_csv = args.out if args.out is not None else (base / "allocation_export.csv")

    tree = ET.parse(xml_path)
    root = tree.getroot()

    votes = parse_votes_by_party(root)
    reg2party = map_registration_to_party(root)
    elected_rows, counts = collect_elected(root, reg2party)

    preallocated = Counter()
    for typ in ("PERSONAL","DISTRICT"):
        preallocated.update(counts[typ])

    comp_calc = allocate_compensation_modified_dhondt(
        votes=votes,
        preallocated=preallocated,
        total_seats=args.total_seats,
        threshold=args.threshold,
        alpha=args.alpha,
    )

    # kontroll XML vs arvutus (peaks nüüd klappima)
    comp_xml = counts["COMPENSATION"]
    diffs = [(p, comp_xml.get(p,0), comp_calc.get(p,0)) for p in set(comp_xml)|set(comp_calc) if comp_xml.get(p,0)!=comp_calc.get(p,0)]
    if diffs:
        sys.stderr.write("HOIATUS: kompensatsioonikohtade jaotus ei kattu XML-iga:\n")
        for p, xml_n, calc_n in diffs:
            sys.stderr.write(f"  {p or '(puuduv kood)'}: XML={xml_n}, arvutus={calc_n}\n")
    else:
        sys.stderr.write("OK: kompensatsioonikohtade jaotus kattub XML-iga.\n")

    write_output_csv(elected_rows, out_csv)
    print(f"OK: kirjutasin {out_csv}  (kasutatud XML: {xml_path})")

if __name__ == "__main__":
    main()
