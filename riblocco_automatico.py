"""
riblocco_automatico.py

Script INDIPENDENTE da Streamlit, pensato per essere eseguito periodicamente
(es. ogni 2 minuti via cron-job.org -> GitHub Actions) da un server, non da un browser.

Legge il registro su Google Sheets, trova le classi il cui sblocco è scaduto
e non è ancora stato seguito da un blocco, e le riporta nel gruppo Jamf
"bloccata" — indipendentemente dal fatto che qualche docente abbia l'app
aperta o meno. Controlla anche (se configurato) dispositivi "orfani"
(spariti da entrambi i gruppi bloccata/libera per un errore API) e li
recupera in automatico.

Ottimizzazione: l'intero elenco dispositivi viene scaricato da Jamf UNA
SOLA VOLTA per esecuzione e riusato in memoria per tutte le classi, invece
di rifare la stessa richiesta pesante decine di volte (causa di forte
rallentamento e accumulo di esecuzioni in coda su GitHub Actions).

Credenziali lette da variabili d'ambiente (impostate come GitHub Secrets):
- JAMF_USERNAME
- JAMF_PASSWORD
- GOOGLE_SERVICE_ACCOUNT_JSON   (l'intero contenuto del file JSON del service account, come stringa)
"""
import os
import json
import time
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo

import requests
import gspread
from google.oauth2.service_account import Credentials

FUSO_ORARIO = ZoneInfo("Europe/Rome")
JAMF_URL = "https://liceosportivopd.jamfcloud.com/api"
GOOGLE_SHEET_NAME = "Log-Teacher-Safari"
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

MAPPA_CLASSI = {
    "IA":   {"bloccata": 9,  "libera": 10},
    "IIA":  {"bloccata": 11, "libera": 12},
    "IIIA": {"bloccata": 13, "libera": 14},
    "IIIB": {"bloccata": 7,  "libera": 8},
    "IVA":  {"bloccata": 19, "libera": 17},
    "IVB":  {"bloccata": 20, "libera": 15},
    "VA":   {"bloccata": 21, "libera": 18},
    "VB":   {"bloccata": 22, "libera": 16},
}

# Gruppi "maestro": contengono SEMPRE tutti i dispositivi di una classe, a prescindere
# dallo stato bloccata/libera. Servono come riferimento per scoprire dispositivi orfani.
GRUPPI_TUTTI = {
    "IA": 35,
    "IIA": 36,
    "IIIA": 37,
    "IIIB": 38,
    "IVA": 39,
    "IVB": 40,
    "VA": 41,
    "VB": 42,
}

AUTH = (os.environ["JAMF_USERNAME"], os.environ["JAMF_PASSWORD"])
HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}


def esegui_azione(azione, gruppo_id, udids):
    url = f"{JAMF_URL}/devices/groups/{azione}"
    try:
        r = requests.post(url, auth=AUTH, headers=HEADERS, json={"groupId": gruppo_id, "udids": udids}, timeout=15)
        return r.status_code in (200, 201)
    except requests.RequestException:
        return False


def recupera_tutti_dispositivi():
    """Scarica l'intero elenco dispositivi UNA SOLA VOLTA per esecuzione."""
    try:
        r = requests.get(f"{JAMF_URL}/devices", auth=AUTH, headers=HEADERS, timeout=20)
        if r.status_code == 200:
            return r.json().get("devices", [])
        return []
    except requests.RequestException:
        return []


def filtra_gruppo(dispositivi, gruppo_id):
    """Filtra in memoria (nessuna chiamata di rete) i dispositivi di un gruppo, dall'elenco già scaricato."""
    return [d["UDID"] for d in dispositivi if str(gruppo_id) in [str(g) for g in d.get("groupIds", [])]]


def blocca_classe_sicuro(classe_nome, dispositivi, tentativi_massimi=3):
    if classe_nome not in MAPPA_CLASSI:
        return False
    ids = MAPPA_CLASSI[classe_nome]
    devices = filtra_gruppo(dispositivi, ids["libera"])
    if not devices:
        return True  # nessun device da spostare: nulla da fare, non è un errore

    if not esegui_azione("remove", ids["libera"], devices):
        return False  # rimozione mai avvenuta: nessun dispositivo orfano

    time.sleep(1.5)

    for tentativo in range(1, tentativi_massimi + 1):
        if esegui_azione("add", ids["bloccata"], devices):
            return True
        if tentativo < tentativi_massimi:
            time.sleep(2)

    print(f"  ⚠️ ATTENZIONE: dispositivi di {classe_nome} rimossi da 'libera' ma MAI aggiunti a 'bloccata'.")
    if esegui_azione("add", ids["libera"], devices):
        print(f"  ↩️ Recuperati riportandoli in 'libera'.")
    else:
        print(f"  ✗✗ CRITICO: dispositivi di {classe_nome} ora orfani da entrambi i gruppi. Serve intervento manuale su Jamf.")
    return False


def apri_spreadsheet(tentativi_massimi=3):
    """Apre l'intero file Google Sheets (log + foglio Holiday) con qualche tentativo."""
    creds_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds = Credentials.from_service_account_info(creds_info, scopes=GOOGLE_SCOPES)
    client = gspread.authorize(creds)
    for tentativo in range(1, tentativi_massimi + 1):
        try:
            return client.open(GOOGLE_SHEET_NAME)
        except Exception as e:
            if tentativo < tentativi_massimi:
                print(f"Tentativo {tentativo} fallito nell'apertura del foglio ({e}), riprovo...")
                time.sleep(3)
            else:
                raise


def scrivi_log(sheet, azione, classe, docente, materia, durata="", tentativi_massimi=3):
    now = datetime.now(FUSO_ORARIO)
    riga = [now.strftime("%d/%m/%Y"), now.strftime("%H:%M:%S"), azione, classe, docente, materia, durata]
    for tentativo in range(1, tentativi_massimi + 1):
        try:
            sheet.append_row(riga)
            return True
        except Exception as e:
            if tentativo < tentativi_massimi:
                time.sleep(2)
            else:
                print(f"  ✗ Impossibile scrivere il log dopo {tentativi_massimi} tentativi: {e}")
    return False


# Finestra in cui il riblocco automatico ha senso: lezioni (lun-ven). Dopo le 13:40 il
# rilascio serale libera comunque tutti gli iPad, quindi non c'è più nulla da ribloccare.
ORA_INIZIO = dt_time(8, 0)
ORA_FINE = dt_time(13, 40)


def fuori_finestra_oraria():
    """True se adesso è weekend o fuori dall'orario delle lezioni (nessuna chiamata di rete)."""
    adesso = datetime.now(FUSO_ORARIO)
    if adesso.weekday() >= 5:
        print("Oggi è weekend, nessuna azione.")
        return True
    if not (ORA_INIZIO <= adesso.time() < ORA_FINE):
        print(f"Ore {adesso.strftime('%H:%M')}: fuori dall'orario delle lezioni, nessuna azione.")
        return True
    return False


def oggi_e_festivo(spreadsheet):
    """True se la data di oggi è nel foglio 'Holiday' (colonna DATA, formato GG/MM/AAAA)."""
    try:
        foglio = spreadsheet.worksheet("Holiday")
        date_festive = [str(r.get("DATA", "")).strip() for r in foglio.get_all_records()]
        oggi = datetime.now(FUSO_ORARIO).strftime("%d/%m/%Y")
        if oggi in date_festive:
            print(f"Oggi ({oggi}) è nel foglio Holiday, nessuna azione.")
            return True
    except Exception as e:
        # Meglio procedere che saltare per errore un giorno di scuola.
        print(f"Impossibile controllare il foglio Holiday ({e}), procedo comunque.")
    return False


def trova_orfani(dispositivi):
    """Restituisce {classe: set(UDID)} dei dispositivi presenti nel gruppo maestro ma in nessuno
    tra bloccata e libera, usando l'elenco già scaricato (nessuna chiamata di rete)."""
    risultato = {}
    for classe, id_tutti in GRUPPI_TUTTI.items():
        if classe not in MAPPA_CLASSI:
            continue
        ids = MAPPA_CLASSI[classe]
        tutti = set(filtra_gruppo(dispositivi, id_tutti))
        bloccati = set(filtra_gruppo(dispositivi, ids["bloccata"]))
        liberi = set(filtra_gruppo(dispositivi, ids["libera"]))
        orfani = tutti - bloccati - liberi
        if orfani:
            risultato[classe] = orfani
    return risultato


def verifica_e_recupera_orfani(sheet, dispositivi):
    """Recupera in 'bloccata' i dispositivi orfani (spariti da bloccata e libera).

    Durante uno sblocco/blocco in corso (remove -> pausa -> add) i dispositivi risultano per
    qualche secondo in nessuno dei due gruppi: sono orfani 'apparenti'. Per non confonderli con
    quelli veri, quando troviamo dei sospetti aspettiamo, riscarichiamo l'elenco e agiamo solo
    su chi è ancora orfano."""
    if not GRUPPI_TUTTI:
        return dispositivi

    sospetti = trova_orfani(dispositivi)
    if not sospetti:
        return dispositivi

    print(f"Possibili orfani in: {', '.join(sospetti)}. Ricontrollo tra 20 secondi...")
    time.sleep(20)
    dispositivi = recupera_tutti_dispositivi()
    if not dispositivi:
        print("Impossibile riscaricare l'elenco dispositivi: rimando il recupero al prossimo ciclo.")
        return []

    for classe, orfani in trova_orfani(dispositivi).items():
        ids = MAPPA_CLASSI[classe]
        print(f"⚠️ {len(orfani)} dispositivi orfani confermati in {classe}: {sorted(orfani)}")
        if esegui_azione("add", ids["bloccata"], list(orfani)):
            print("  ✓ Recuperati in 'bloccata'.")
            scrivi_log(sheet, "RECUPERO_ORFANI", classe, "Sistema", f"{len(orfani)} dispositivi recuperati")
        else:
            print(f"  ✗✗ CRITICO: impossibile recuperare i dispositivi orfani di {classe}. Intervento manuale necessario.")
    return dispositivi


def main():
    if fuori_finestra_oraria():
        return

    spreadsheet = apri_spreadsheet()
    if oggi_e_festivo(spreadsheet):
        return

    sheet = spreadsheet.sheet1
    dispositivi = recupera_tutti_dispositivi()
    if not dispositivi:
        # Un elenco vuoto non è "tutto a posto": significa che Jamf non ha risposto.
        # Falliamo in modo visibile invece di far finta di non avere nulla da fare.
        print("✗ Impossibile scaricare l'elenco dispositivi da Jamf.")
        raise SystemExit(1)

    dispositivi = verifica_e_recupera_orfani(sheet, dispositivi) or dispositivi

    records = sheet.get_all_records()
    if not records:
        print("Log vuoto, nulla da fare.")
        return

    oggi = datetime.now(FUSO_ORARIO).strftime("%d/%m/%Y")
    righe_oggi = [r for r in records if r.get("Data") == oggi]
    if not righe_oggi:
        print("Nessuna riga di oggi nel registro.")
        return

    ultima_per_classe = {}
    for riga in righe_oggi:
        ultima_per_classe[riga.get("Classe")] = riga

    ora_attuale = datetime.now(FUSO_ORARIO)

    for classe, riga in ultima_per_classe.items():
        if riga.get("Azione") != "SBLOCCO":
            continue

        try:
            ora_inizio = datetime.strptime(f"{oggi} {riga['Ora']}", "%d/%m/%Y %H:%M:%S").replace(tzinfo=FUSO_ORARIO)
            durata_minuti = int(riga["Durata"])
        except (ValueError, KeyError, TypeError):
            print(f"Riga non interpretabile per la classe {classe}, salto.")
            continue

        ora_fine = ora_inizio + timedelta(minutes=durata_minuti)

        if ora_fine <= ora_attuale:
            print(f"Sblocco scaduto per {classe} (fine prevista {ora_fine.strftime('%H:%M')}) → riblocco in corso...")
            if blocca_classe_sicuro(classe, dispositivi):
                scrivi_log(sheet, "BLOCCO_AUTOMATICO", classe, riga.get("Docente", ""), riga.get("Materia", ""))
                print(f"  ✓ {classe} ribloccata correttamente.")
            else:
                print(f"  ✗ ERRORE nel riblocco di {classe}.")
        else:
            print(f"{classe}: sblocco ancora valido fino alle {ora_fine.strftime('%H:%M')}.")


if __name__ == "__main__":
    main()
