"""
blocco_mattutino.py

Script INDIPENDENTE, pensato per essere eseguito una volta al giorno (es. alle 8:00
tramite cron-job.org) per sbloccare Safari su TUTTI gli iPad degli studenti, di tutte
le classi, a fine giornata scolastica — indipendentemente da sessioni attive o log.

Credenziali lette da variabili d'ambiente (le stesse gi\u00e0 usate da riblocco_automatico.py):
- JAMF_USERNAME
- JAMF_PASSWORD
- GOOGLE_SERVICE_ACCOUNT_JSON
"""
import os
import json
import time
from datetime import datetime
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


def sposta_dispositivi(gruppo_id_da, gruppo_id_a, tentativi_massimi=2):
    for tentativo in range(1, tentativi_massimi + 1):
        devices = recupera_dispositivi_in_gruppo(gruppo_id_da)
        if not devices:
            return True  # nessun dispositivo da spostare: nulla da fare
        esegui_azione("remove", gruppo_id_da, devices)
        time.sleep(1.5)
        if esegui_azione("add", gruppo_id_a, devices):
            return True
        if tentativo < tentativi_massimi:
            time.sleep(2)
    return False


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


def scrivi_log(sheet, azione, classe, tentativi_massimi=3):
    now = datetime.now(FUSO_ORARIO)
    riga = [now.strftime("%d/%m/%Y"), now.strftime("%H:%M:%S"), azione, classe, "Sistema", "", ""]
    for tentativo in range(1, tentativi_massimi + 1):
        try:
            sheet.append_row(riga)
            return True
        except Exception as e:
            if tentativo < tentativi_massimi:
                time.sleep(2)
            else:
                print(f"  ✗ Impossibile scrivere il log per {classe}: {e}")
    return False


def e_da_saltare_oggi():
    """Restituisce True se oggi non si deve eseguire l'azione: weekend o data presente nel foglio 'Festivita'."""
    oggi_dt = datetime.now(FUSO_ORARIO)
    if oggi_dt.weekday() >= 5:  # 5=sabato, 6=domenica
        print("Oggi è weekend, nessuna azione.")
        return True
    try:
        creds_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
        creds = Credentials.from_service_account_info(creds_info, scopes=GOOGLE_SCOPES)
        client = gspread.authorize(creds)
        foglio_festivita = client.open(GOOGLE_SHEET_NAME).worksheet("Holiday")
        date_festive = [str(r.get("DATA", "")).strip() for r in foglio_festivita.get_all_records()]
        oggi_str = oggi_dt.strftime("%d/%m/%Y")
        if oggi_str in date_festive:
            print(f"Oggi ({oggi_str}) è nel foglio Festivita, nessuna azione.")
            return True
    except Exception as e:
        print(f"Impossibile controllare il foglio Festivita ({e}), procedo comunque.")
    return False


def main():
    if e_da_saltare_oggi():
        return
    sheet = get_gsheet()
    for classe, ids in MAPPA_CLASSI.items():
        print(f"Blocco mattutino: blocco {classe}...")
        if sposta_dispositivi(ids["libera"], ids["bloccata"]):
            scrivi_log(sheet, "BLOCCO_MATTUTINO", classe)
            print(f"  ✓ {classe} bloccata per l'inizio delle lezioni.")
        else:
            print(f"  ✗ ERRORE nel blocco mattutino di {classe}.")


if __name__ == "__main__":
    main()
