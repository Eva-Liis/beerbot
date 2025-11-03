import pandas as pd
import numpy as np
import holidays
import matplotlib.pyplot as plt
from datetime import datetime
from pathlib import Path

# --- Konfiguratsioon ---
# TÄHELEPANU: Asenda siin oma faili teekond. Kasutame Path-i, et vältida teeprobleeme.
CSV_FAILI_NIMI = 'Tarbimine_elekter2024.csv'
RIIGI_PUHAD = holidays.country_holidays('EE', years=2024)

# Andmete veerud failis
KOLM_VEERGU = ['Periood', 'Tarbitud energia (kWh)', 'Börsihind (senti/kWh)']

# Testimiseks kasutatavad eraldajad
TESTITAVAD_ERALDAJAD = [';', ',', '\t']
MAX_SKIP_ROWS = 10 

# Fikseeritud tariifide testimise vahemik (senti/kWh)
PAEVA_HINNAD = np.arange(0.0, 30.5, 0.5) / 100.0  # 0.0 senti kuni 30.0 senti 0.5 senti sammuga (€/kWh)
OO_HINNAD = np.arange(0.0, 30.5, 0.5) / 100.0      # 0.0 senti kuni 30.0 senti 0.5 senti sammuga (€/kWh)

# --- TEEKONNA MÄÄRAMINE ---
# Leia .py skripti kaust ja konstrueeri faili täielik tee
try:
    # 1. Leia käivitatava skripti (.py) kaust.
    SKRIPTI_KATALOOG = Path(__file__).resolve().parent
    # 2. Ühenda kaust ja CSV-faili nimi.
    FAILI_TEE = SKRIPTI_KATALOOG / CSV_FAILI_NIMI
except NameError:
    # See juhtub, kui käivitatakse näiteks Jupyteris/interaktiivselt, 
    # kus __file__ pole määratud. Kasutame praegust töökataloogi.
    FAILI_TEE = Path(CSV_FAILI_NIMI)

# Kontrollime, kas fail eksisteerib enne lugemist
if not FAILI_TEE.exists():
    print(f"❌ Viga: Faili '{FAILI_TEE.name}' ei leitud asukohast: {FAILI_TEE.parent}")
    print("Palun veenduge, et .csv ja .py fail on samas kaustas.")
    exit()
# --- 1. Andmete laadimine ja puhastamine ---

df = None
leitud_seaded = None

print(f"🔎 Proovin laadida faili '{FAILI_TEE.name}' ja tuvastada andmete alguse...")

# Proovi erinevaid seadistusi andmete automaatseks leidmiseks (sep ja skiprows)
for sep_char in TESTITAVAD_ERALDAJAD:
    for skip_count in range(MAX_SKIP_ROWS):
        try:
            temp_df = pd.read_csv(
                FAILI_TEE,
                header=None,
                sep=sep_char,
                decimal='.',
                skiprows=skip_count
            )
            
            # Puhasta read/veerud ja kontrolli, kas leidsime 3 veergu
            temp_df.dropna(how='all', axis=0, inplace=True)
            temp_df.dropna(how='all', axis=1, inplace=True)

            if temp_df.shape[1] == len(KOLM_VEERGU):
                df = temp_df
                leitud_seaded = {'sep': sep_char, 'skiprows': skip_count}
                break 
        except Exception:
            continue
    if df is not None:
        break 

if df is None:
    print(f"❌ Viga: Ei leidnud '{FAILI_TEE.name}' failist 3 andmeveeruga plokki. Proovige eraldaja või 'MAX_SKIP_ROWS' väärtust käsitsi seadistada.")
    exit()

print(f"✅ Andmed laetud! Eraldaja: '{leitud_seaded['sep']}', Vahelejätud read: {leitud_seaded['skiprows']}")

# Veergude ümbernimetamine (kasutame nüüd meie kindlaid nimesid)
df.columns = KOLM_VEERGU

# --- 2. Andmete töötlemine ---

def maara_tariif(aeg):
    """Määrab, kas antud tund kuulub päeva- või öötariifi alla."""
    on_puha = aeg.date() in RIIGI_PUHAD
    on_lp = aeg.weekday() >= 5
    on_paeva_aeg = (aeg.hour >= 7) and (aeg.hour < 22)

    # Päevatund: E-R (0-4) JA EI OLE PÜHA JA 07:00-22:00
    if (aeg.weekday() < 5) and (not on_puha) and on_paeva_aeg:
        return 'Päev'
    else:
        # Muu aeg (ÖÖ: muu aeg + L/P + pühad)
        return 'Öö'

try:
    # Kuupäeva/kellaaja parsimine (Formaat: DD.MM.YYYY HH:MM)
    df['Aeg'] = pd.to_datetime(df['Periood'], format='%d.%m.%Y %H:%M', errors='coerce')

    # --- KRITILINE PARANDUS: Tagame, et väärtused on numbrilised ---
    
    # 1. Puhasta Tarbitud energia (kWh) veerg
    df['Tarbitud (kWh)'] = (
        df['Tarbitud energia (kWh)']
        .astype(str)
        .str.replace(',', '.', regex=False)  # Asendame komad punktidega, kui neid on
        .str.strip()  # Eemaldame tühikud
    )
    df['Tarbitud (kWh)'] = pd.to_numeric(df['Tarbitud (kWh)'], errors='coerce')

    # 2. Puhasta Börsihind veerg ja teisenda €/kWh-ks
    df['Börsihind (€/kWh)'] = (
        df['Börsihind (senti/kWh)']
        .astype(str)
        .str.replace(',', '.', regex=False) # Asendame komad punktidega, kui neid on
        .str.strip()
    )
    df['Börsihind (€/kWh)'] = pd.to_numeric(df['Börsihind (€/kWh)'], errors='coerce') / 100.0

    # Puhasta ebanormaalsed või puuduvad väärtused (NaN)
    df.dropna(subset=['Aeg', 'Tarbitud (kWh)', 'Börsihind (€/kWh)'], inplace=True)
    df.set_index('Aeg', inplace=True)

    # Määrame päeva/öö kategooria
    df['Tariif'] = df.index.to_series().apply(maara_tariif)
    df['Börsi kulu (€)'] = df['Börsihind (€/kWh)'] * df['Tarbitud (kWh)']
    kogukulu_bors = df['Börsi kulu (€)'].sum()
    

except Exception as e:
    print(f"Viga andmete töötlemisel: {e}")
    exit()


# --- 3. Fikseeritud hindade testimine ja tulemuste kogumine ---

tulemused = []
for paeva_tariif in PAEVA_HINNAD:
    for oo_tariif in OO_HINNAD:
        # Arvuta kulu fikseeritud tariifidega
        kulu_paev = (df['Tariif'] == 'Päev') * df['Tarbitud (kWh)'] * paeva_tariif
        kulu_oo = (df['Tariif'] == 'Öö') * df['Tarbitud (kWh)'] * oo_tariif
        kogukulu_fikseeritud = kulu_paev.sum() + kulu_oo.sum()

        # Erinevus: Börs kulu - Fikseeritud kulu
        kumulatiivne_erinevus = kogukulu_bors - kogukulu_fikseeritud

        tulemused.append({
            'Päevatariif (€/kWh)': paeva_tariif,
            'Öötariif (€/kWh)': oo_tariif,
            'Kumulatiivne Erinevus (€)': kumulatiivne_erinevus,
            'Kasulikum kui Börs': kumulatiivne_erinevus > 0
        })

df_tulemused = pd.DataFrame(tulemused)

# --- 4. Graafiku loomine ---
print("📈 Loo graafik...")

# Eralda ainult tasuvad punktid (rohelised)
df_tasuvad = df_tulemused[df_tulemused['Kasulikum kui Börs'] == True]

plt.figure(figsize=(12, 8))

# Graafik: Tasuvad kombinatsioonid (Kasulikum kui börs)
plt.scatter(
    df_tasuvad['Päevatariif (€/kWh)'] * 100,
    df_tasuvad['Öötariif (€/kWh)'] * 100,
    s=5,
    color='green',
    alpha=0.5,
    label='Kasulikum kui börs (Fikseeritud kulu < Börsi kulu)'
)

# Lineaarse tasuvuse piirjoone arvutamine (Börsi kogukulu = Fikseeritud kogukulu)

tarb_paev_kokku = df[df['Tariif'] == 'Päev']['Tarbitud (kWh)'].sum()
tarb_oo_kokku = df[df['Tariif'] == 'Öö']['Tarbitud (kWh)'].sum()

# Tasuvuse võrrand (Öötariif = m * Päevatariif + b) sentides
m = - tarb_paev_kokku / tarb_oo_kokku
b = kogukulu_bors / tarb_oo_kokku * 100  # Vaba liige sentides

# Loome joonlaua (x_joon)
# X-telje vahemik: Päevatariifide (sentides) miinimumist maksimumini, mis testitud
x_min_sent = 0 
x_max_sent = PAEVA_HINNAD.max() * 100

x_joon = np.linspace(x_min_sent, x_max_sent, 100) # Päevatariifide vahemik sentides
y_joon = m * x_joon + b # Öötariif sentides

# Joonista tasuvuse piirjoon
plt.plot(
    x_joon,
    y_joon,
    color='blue',
    linestyle='-',
    linewidth=2,
    label='Arvutuslik tasuvuse piirjoon'
)

# Graafiku seaded
plt.title('Fikseeritud tariifide tasuvus võrreldes börsielektriga')
plt.xlabel('Päevatariif (senti/kWh) [E-R 07:00-22:00 v.a pühad]')
plt.ylabel('Öötariif (senti/kWh) [Muu aeg + L/P + pühad]')

# Telgede piirangud (alates nullist)
plt.xlim(xmin=0)
plt.ylim(ymin=0)

plt.grid(True, linestyle=':', alpha=0.6)
plt.legend(markerscale=3)

# Kuva graafik 
plt.show()

print("✅ Graafik loodud ja kuvatud.")