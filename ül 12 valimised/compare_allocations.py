#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Võrdle kahte RK jaotusfaili (CSV või XLSX) sama veeruvorminguga kui sinu näidis:
  Jrk nr; Kvoot; Võrdlusarv; Ringkond; Nimekiri; Hääli kokku; Reg nr; Kandidaadi nimi; Mandaatide omandamise viis

Teeb:
  1) koondraport (summary.csv)
  2) erakondade kohtade vahe (party_counts_diff.csv)
  3) read, mis on ainult failis A (only_in_A.csv) ja ainult failis B (only_in_B.csv), täisread
  4) ridade võrdlus sama "Reg nr" korral: millised väljad erinevad (field_differences_on_common_Regs.csv)

Kasutus:
  python compare_allocations.py                              # võtab samast kaustast allocation_export.csv ja allocation_export_alt.csv
  python compare_allocations.py fileA.csv fileB.csv          # võrdleb ette antud faile
  python compare_allocations.py fileA.xlsx fileB.xlsx --outdir ./diff2
"""

from pathlib import Path
import argparse
import pandas as pd

REQ_COLS = [
    "Jrk nr","Kvoot","Võrdlusarv","Ringkond","Nimekiri",
    "Hääli kokku","Reg nr","Kandidaadi nimi","Mandaatide omandamise viis"
]

def read_any(path: Path) -> pd.DataFrame:
    """Loe semikooloniga CSV või XLSX. Tagasta ainult REQ_COLS, normaliseeritud whitespace’iga."""
    path = Path(path)
    if not path.exists():
        raise SystemExit(f"Faili ei leitud: {path}")

    if path.suffix.lower() in (".xlsx", ".xls"):
        df = pd.read_excel(path, dtype=str)
    else:
        # semikooloniga CSV (sama nagu sinu eksport)
        df = pd.read_csv(path, sep=";", dtype=str, keep_default_na=False, encoding="utf-8")

    # normaliseeri mittemurdvad tühikud ja lõpu-tühikud
    for c in df.columns:
        df[c] = df[c].astype(str).str.replace("\u00A0", " ", regex=False).str.strip()

    # veerunimede trim
    df.columns = [c.strip() for c in df.columns]

    missing = [c for c in REQ_COLS if c not in df.columns]
    if missing:
        raise SystemExit(f"{path.name}: puuduvad veerud: {missing}")

    return df[REQ_COLS].copy()

def main():
    base = Path(__file__).resolve().parent

    ap = argparse.ArgumentParser(description="Võrdle kahte RK jaotus-CSV/XLSX faili.")
    # positsioonilised argumendid on valikulised; kui puuduvad, kasutatakse vaikimisi nimesid samas kaustas
    ap.add_argument("a", nargs="?", help="Esimene fail (CSV/XLSX). Vaikimisi: allocation_export.csv")
    ap.add_argument("b", nargs="?", help="Teine fail (CSV/XLSX). Vaikimisi: allocation_export_alt.csv")
    ap.add_argument("--outdir", type=Path, default=base / "diff", help="Väljundkaust (vaikimisi ./diff sama kausta all)")
    args = ap.parse_args()

    a_path = Path(args.a) if args.a else (base / "allocation_export.csv")
    b_path = Path(args.b) if args.b else (base / "allocation_export_alt.csv")
    outdir = args.outdir
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"Võrdlen:\n  A = {a_path}\n  B = {b_path}\nVäljundkaust: {outdir}")

    A = read_any(a_path)
    B = read_any(b_path)

    # 1) Erakondade kohtade võrdlus
    pcA = A["Nimekiri"].value_counts().rename("A_seats")
    pcB = B["Nimekiri"].value_counts().rename("B_seats")
    party_diff = pd.concat([pcA, pcB], axis=1).fillna(0).astype(int).sort_index()
    party_diff["Δ"] = party_diff["A_seats"] - party_diff["B_seats"]

    # 2) Kandidaadid ainult A-s / ainult B-s (Reg nr järgi)
    regsA, regsB = set(A["Reg nr"]), set(B["Reg nr"])
    onlyA_regs = sorted(regsA - regsB)
    onlyB_regs = sorted(regsB - regsA)
    onlyA_df = A[A["Reg nr"].isin(onlyA_regs)].copy()
    onlyB_df = B[B["Reg nr"].isin(onlyB_regs)].copy()

    # 3) Sama Reg nr – millised väljad erinevad
    merged = A.merge(B, on="Reg nr", how="inner", suffixes=("_A","_B"))
    diffs = []
    for _, r in merged.iterrows():
        rowdiff = {"Reg nr": r["Reg nr"]}
        changed = False
        for col in REQ_COLS:
            if col == "Reg nr":
                continue
            va = str(r[f"{col}_A"])
            vb = str(r[f"{col}_B"])
            if va != vb:
                rowdiff[f"{col} (A)"] = va
                rowdiff[f"{col} (B)"] = vb
                changed = True
        if changed:
            diffs.append(rowdiff)
    field_diffs = pd.DataFrame(diffs)

    # 4) Koondtabel
    summary = pd.DataFrame({
        "Metric": [
            "Rows A", "Rows B", "Common Reg nr",
            "Only in A", "Only in B",
            "Parties with seat diff", "Rows with field diffs"
        ],
        "Value": [
            len(A), len(B), len(merged),
            len(onlyA_df), len(onlyB_df),
            int((party_diff["Δ"] != 0).sum()),
            len(field_diffs)
        ]
    })

    # 5) Salvesta (UTF-8 BOM → Excel tunneb täpitähed)
    (outdir / "summary.csv").write_text(summary.to_csv(index=False), encoding="utf-8-sig")
    party_diff.to_csv(outdir / "party_counts_diff.csv", encoding="utf-8-sig")
    onlyA_df.to_csv(outdir / "only_in_A.csv", index=False, encoding="utf-8-sig")
    onlyB_df.to_csv(outdir / "only_in_B.csv", index=False, encoding="utf-8-sig")
    field_diffs.to_csv(outdir / "field_differences_on_common_Regs.csv", index=False, encoding="utf-8-sig")

    print("Valmis.")
    print(f"- Koond: {outdir/'summary.csv'}")
    print(f"- Erakondade kohtade erinevused: {outdir/'party_counts_diff.csv'}")
    print(f"- Ainult failis A: {outdir/'only_in_A.csv'}")
    print(f"- Ainult failis B: {outdir/'only_in_B.csv'}")
    print(f"- Sama Reg nr, erinevad väljad: {outdir/'field_differences_on_common_Regs.csv'}")

if __name__ == "__main__":
    main()
