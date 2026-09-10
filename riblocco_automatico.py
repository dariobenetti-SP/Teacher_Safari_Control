"""
riblocco_automatico.py

Script INDIPENDENTE da Streamlit, pensato per essere eseguito periodicamente
(es. ogni 2-5 minuti via GitHub Actions) da un server, non da un browser.

Legge il registro su Google Sheets, trova le classi il cui sblocco è scaduto
e non è ancora stato seguito da un blocco, e le riporta nel gruppo Jamf
"bloccata" — indipendentemente dal fatto che qualche docente abbia l'app
aperta o meno.

Credenziali lette da variabili d'ambiente (impostate come GitHub Secrets):
- JAMF_USERNAME
- JAMF_PASSWORD
- GOOGLE_SERVICE_ACCOUNT_JSON   (l'intero contenuto del file JSON del service account, come stringa)
"""
import os
import json
import time
from datetime import datetime, timedelta
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

AUTH = (os.environ["JAMF_USERNAME"], os.environ["JAMF_PASSWORD"])
HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}


def esegui_azione(azione, gruppo_id, udids):
    url = f"{JAMF_URL}/devices/groups/{azione}"
    try:
        r = requests.post(url, auth=AUTH, headers=HEADERS, json={"groupId": gruppo_id, "udids": udids}, timeout=15)
        return r.status_code in (200, 201)
    except requests.RequestException:
        return False


def recupera_dispositivi_in_gruppo(gruppo_id):
    try:
        r = requests.get(f"{JAMF_URL}/devices", auth=AUTH, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            return [d["UDID"] for d in r.json().get("devices", []) if str(gruppo_id) in [str(g) for g in d.get("groupIds", [])]]
        return []
    except requests.RequestException:
        return []


def blocca_classe_sicuro(classe_nome):
    if classe_nome not in MAPPA_CLASSI:
        return False
    ids = MAPPA_CLASSI[classe_nome]
    devices = recupera_dispositivi_in_gruppo(ids["libera"])
    if devices:
        esegui_azione("remove", ids["libera"], devices)
        time.sleep(1.5)
        return esegui_azione("add", ids["bloccata"], devices)
    return True  # nessun device da spostare: nulla da fare, non è un errore


def get_gsheet(tentativi_massimi=3):
    creds_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    creds = Credentials.from_service_account_info(creds_info, scopes=GOOGLE_SCOPES)
    client = gspread.authorize(creds)
    for tentativo in range(1, tentativi_massimi + 1):
        try:
            return client.open(GOOGLE_SHEET_NAME).sheet1
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


def main():
    sheet = get_gsheet()
    records = sheet.get_all_records()
    if not records:
        print("Log vuoto, nulla da fare.")
        return

    oggi = datetime.now(FUSO_ORARIO).strftime("%d/%m/%Y")
    righe_oggi = [r for r in records if r.get("Data") == oggi]
    if not righe_oggi:
        print("Nessuna riga di oggi nel registro.")
        return

    # L'ultima riga di oggi per ciascuna classe (il foglio è già in ordine cronologico
    # perché ogni azione viene sempre aggiunta in fondo con append_row)
    ultima_per_classe = {}
    for riga in righe_oggi:
        ultima_per_classe[riga.get("Classe")] = riga

    ora_attuale = datetime.now(FUSO_ORARIO)

    for classe, riga in ultima_per_classe.items():
        if riga.get("Azione") != "SBLOCCO":
            continue  # l'ultima azione registrata è già un blocco: nulla da fare

        try:
            ora_inizio = datetime.strptime(f"{oggi} {riga['Ora']}", "%d/%m/%Y %H:%M:%S").replace(tzinfo=FUSO_ORARIO)
            durata_minuti = int(riga["Durata"])
        except (ValueError, KeyError, TypeError):
            print(f"Riga non interpretabile per la classe {classe}, salto.")
            continue

        ora_fine = ora_inizio + timedelta(minutes=durata_minuti)

        if ora_fine <= ora_attuale:
            print(f"Sblocco scaduto per {classe} (fine prevista {ora_fine.strftime('%H:%M')}) → riblocco in corso...")
            if blocca_classe_sicuro(classe):
                scrivi_log(sheet, "BLOCCO_AUTOMATICO", classe, riga.get("Docente", ""), riga.get("Materia", ""))
                print(f"  ✓ {classe} ribloccata correttamente.")
            else:
                print(f"  ✗ ERRORE nel riblocco di {classe}.")
        else:
            print(f"{classe}: sblocco ancora valido fino alle {ora_fine.strftime('%H:%M')}.")


if __name__ == "__main__":
    main()
