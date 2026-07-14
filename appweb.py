import streamlit as st
import pandas as pd
import requests
import time
import csv
from datetime import datetime, timedelta, time as dt_time
import os

# --- 1. CONFIGURAZIONE PAGINA ---
st.set_page_config(page_title="Teacher Jamf • Safari", page_icon="🔐", initial_sidebar_state="collapsed")

# --- 2. CONFIGURAZIONE API E FILE ---
JAMF_URL = "https://liceosportivopd.jamfcloud.com/api"
AUTH = (st.secrets["jamf"]["username"], st.secrets["jamf"]["password"])
HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}
FILE_LOG = "log_utilizzi.csv"

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

DIZ_MATERIE = {
    "ITA": "Lingua e letteratura italiana",
    "ING": "Lingua e cultura inglese",
    "S&G": "Storia e Geografia",
    "STO": "Storia",
    "FIL": "Filosofia",
    "D&E": "Diritto ed Economia dello sport",
    "MAT": "Matematica",
    "FIS": "Fisica",
    "SN": "Scienze naturali",
    "SM": "Scienze motorie",
    "DS": "Discipline sportive",
    "EC": "Educazione civica"
}

def formatta_materia(sigla):
    if sigla == "Altro": return "Altro"
    nome_esteso = DIZ_MATERIE.get(sigla.strip())
    if nome_esteso:
        return f"{nome_esteso} · {sigla.strip()}"
    return sigla.strip()

# --- 3. DETERMINAZIONE FASCIA ORARIA ---
def determina_ora_scolastica():
    current_time = datetime.now().time()
    if dt_time(8, 0) <= current_time < dt_time(9, 0): return 1
    if dt_time(9, 0) <= current_time < dt_time(9, 55): return 2
    if dt_time(9, 55) <= current_time < dt_time(10, 50): return 3
    if dt_time(11, 5) <= current_time < dt_time(11, 55): return 4
    if dt_time(11, 55) <= current_time < dt_time(12, 45): return 5
    if dt_time(12, 45) <= current_time < dt_time(13, 40): return 6
    return None

# --- 4. FUNZIONI API E LOGGING ---
def esegui_azione(azione, gruppo_id, udids):
    url = f"{JAMF_URL}/devices/groups/{azione}"
    try:
        response = requests.post(url, auth=AUTH, headers=HEADERS, json={"groupId": gruppo_id, "udids": udids}, timeout=10)
        return response.status_code in (200, 201)
    except requests.RequestException: 
        return False

def recupera_dispositivi_in_gruppo(gruppo_id):
    try:
        response = requests.get(f"{JAMF_URL}/devices", auth=AUTH, headers=HEADERS, timeout=10)
        if response.status_code == 200:
            return [d["UDID"] for d in response.json().get("devices", []) if str(gruppo_id) in [str(g) for g in d.get("groupIds", [])]]
        return []
    except requests.RequestException: 
        return []

def blocca_classe_sicuro(classe_nome):
    if classe_nome not in MAPPA_CLASSI: return False
    ids = MAPPA_CLASSI[classe_nome]
    devices = recupera_dispositivi_in_gruppo(ids["libera"])
    if devices:
        esegui_azione("remove", ids["libera"], devices)
        time.sleep(1.5)
        return esegui_azione("add", ids["bloccata"], devices)
    return True

def scrivi_log(azione, classe, docente, materia, durata=""):
    file_exists = os.path.isfile(FILE_LOG)
    with open(FILE_LOG, "a", newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["Data", "Ora_Reale", "Azione", "Classe", "Docente", "Materia", "Durata_Minuti"])
        now = datetime.now()
        writer.writerow([now.strftime("%d/%m/%Y"), now.strftime("%H:%M:%S"), azione, classe, docente, materia, durata])

# --- NUOVA FUNZIONE: LETTURA SESSIONI ATTIVE CONDIVISE ---
def ottieni_sessioni_attive_globali():
    if not os.path.exists(FILE_LOG):
        return []
    
    try:
        df_log = pd.read_csv(FILE_LOG)
        oggi = datetime.now().strftime("%d/%m/%Y")
        df_oggi = df_log[df_log['Data'] == oggi]
        
        if df_oggi.empty:
            return []
            
        # Troviamo l'ultima azione registrata per ogni classe oggi
        ultime_azioni = df_oggi.sort_values(by=['Ora_Reale']).groupby('Classe').last()
        
        sessioni_attive = []
        ora_attuale = datetime.now()
        
        for classe, row in ultime_azioni.iterrows():
            if row['Azione'] == 'SBLOCCO':
                try:
                    ora_inizio = datetime.strptime(f"{oggi} {row['Ora_Reale']}", "%d/%m/%Y %H:%M:%S")
                    durata = int(row['Durata_Minuti'])
                    ora_fine = ora_inizio + timedelta(minutes=durata)
                    
                    if ora_fine > ora_attuale:
                        str_inizio = ora_inizio.strftime("%H:%M")
                        str_fine = ora_fine.strftime("%H:%M")
                        # Formattazione esatta richiesta: "📍 Rossi ha sbloccato la IA dalle 11:05 alle 11:30."
                        sessioni_attive.append(f"📍 {row['Docente']} ha sbloccato la {classe} dalle {str_inizio} alle {str_fine}.")
                except ValueError:
                    continue
        
        return sessioni_attive
    except Exception:
        return []

# --- 5. CARICAMENTO E PULIZIA DATI ---
if os.path.exists('orario.csv'):
    df = pd.read_csv('orario.csv', encoding='utf-8')
    df.columns = df.columns.str.strip()
    
    df['Classe'] = df['Classe'].astype(str).str.strip()
    if 'Ora' in df.columns: df['Ora'] = df['Ora'].astype(str).str.strip()
    if 'Docente' in df.columns: df['Docente'] = df['Docente'].astype(str).str.strip()
    if 'Materia' in df.columns: df['Materia'] = df['Materia'].astype(str).str.strip()
    if 'Giorno' in df.columns: df['Giorno'] = df['Giorno'].astype(str).str.strip()
    
    lista_classi = sorted([c for c in df['Classe'].unique().tolist() if c in MAPPA_CLASSI])
    lista_docenti = ["Altro"]
    if 'Docente' in df.columns: 
        lista_docenti = sorted(df['Docente'].dropna().unique().tolist()) + ["Altro"]
    
    lista_materie_display = ["Altro"]
    if 'Materia' in df.columns:
        sigle_raw = sorted(df['Materia'].dropna().unique().tolist())
        lista_materie_display = [formatta_materia(s) for s in sigle_raw] + ["Altro"]
else:
    st.error("File 'orario1.csv' non trovato.")
    st.stop()

# --- 6. IDENTIFICAZIONE DOCENTE (URL PAYLOAD JAMF O FALLBACK) ---
# Legge l'URL: es. https://liceo.streamlit.app/?docente=Benetti
param_docente = st.query_params.get("docente", None)

if param_docente:
    proprietario = param_docente
else:
    # Se non c'è parametro URL (es. test sul Mac), usiamo un selettore fittizio
    proprietario = st.selectbox("Seleziona il tuo Profilo (Modalità Test Locale):", lista_docenti)

# --- 7. LOGICA AUTO-COMPILAZIONE ---
ora_scolastica_attuale = determina_ora_scolastica()
giorni_it = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
giorno_oggi = giorni_it[datetime.now().weekday()]

classe_suggerita = lista_classi[0] if lista_classi else ""
materia_suggerita = lista_materie_display[0] if lista_materie_display else ""

if ora_scolastica_attuale is not None:
    lezione_attuale = df[
        (df['Giorno'].astype(str).str.strip().str.lower() == giorno_oggi.lower()) & 
        (df['Ora'].astype(str).str.strip() == str(ora_scolastica_attuale)) & 
        (df['Docente'].astype(str).str.strip().str.lower() == proprietario.strip().lower())
    ]
    if not lezione_attuale.empty:
        c_suggerita = str(lezione_attuale.iloc[0]['Classe']).strip()
        if c_suggerita in MAPPA_CLASSI:
            classe_suggerita = c_suggerita
        sigla_orario = str(lezione_attuale.iloc[0]['Materia']).strip()
        materia_suggerita = formatta_materia(sigla_orario)

# --- 8. STATO E CSS DINAMICO ---
if "expiry_time" not in st.session_state: st.session_state.expiry_time = None

css_style = """
<style>
div[data-testid="stStatusWidget"] { visibility: hidden !important; display: none !important; }
footer {visibility: hidden !important;}
div[data-testid="stDeployButton"] {display:none !important;}
div[data-testid="stToolbarAction"] {display:none !important;}
div[data-testid="stMainMenu"] {display:none !important;}
"""

if st.session_state.expiry_time is not None:
    css_style += """
    button[kind="primary"] {
        background-color: #0068c9 !important;
        border-color: #0068c9 !important;
        color: white !important;
    }
    """

css_style += "</style>"
st.markdown(css_style, unsafe_allow_html=True)

# --- 9. INTERFACCIA PRINCIPALE ---
st.title("🔐 Teacher Jamf • Safari 🦏")
st.caption(f"Accesso effettuato come: **{proprietario}**")

# --- PANNELLO CONDIVISO: SESSIONI ATTIVE ISTITUTO ---
sessioni_globali = ottieni_sessioni_attive_globali()
if sessioni_globali:
    with st.expander("🌐 Sessioni Safari sbloccate in questo momento nell'istituto", expanded=True):
        for s in sessioni_globali:
            st.write(s)

st.markdown("---")

# --- 10. SIDEBAR AMMINISTRATORE ---
with st.sidebar:
    st.header("⚙️ Amministrazione")
    if password_inserita == st.secrets["ADMIN_PASSWORD"]:
    st.write("Accesso amministratore effettuato!")
    else:
    st.write("Password errata.")
        st.subheader("📊 Esportazione Registro")
        if os.path.exists(FILE_LOG):
            with open(FILE_LOG, "r", encoding="utf-8") as f:
                st.download_button("📥 Scarica log_utilizzi.csv", f, "log_utilizzi.csv", "text/csv", use_container_width=True)
        else:
            st.info("Nessun dato registrato nel log.")
            
        st.markdown("---")
        if st.button("🔄 RIPRISTINA TUTTI I GRUPPI", use_container_width=True):
            with st.spinner("Sincronizzazione in corso..."):
                for nome, ids in MAPPA_CLASSI.items():
                    devs = recupera_dispositivi_in_gruppo(ids["libera"])
                    if devs:
                        esegui_azione("remove", ids["libera"], devs)
                        esegui_azione("add", ids["bloccata"], devs)
            st.success("Gruppi ripristinati correttamente.")

# --- 11. GESTIONE PANNELLI INTERFACCIA ---
zona_dinamica = st.empty()

with zona_dinamica.container():
    if st.session_state.expiry_time is None:
        idx_classe = lista_classi.index(classe_suggerita) if classe_suggerita in lista_classi else 0
        classe_sel = st.selectbox("Seleziona Classe:", lista_classi, index=idx_classe)
        
        col1, col2 = st.columns(2)
        with col1:
            # Anche se il proprietario è letto dall'URL, lo mettiamo di default nella selectbox
            # nel caso volesse sbloccare per conto di un collega assente
            idx_doc = lista_docenti.index(proprietario) if proprietario in lista_docenti else 0
            doc_sel = st.selectbox("Docente responsabile:", lista_docenti, index=idx_doc)
            doc_effettivo = st.text_input("Specifica Cognome:", key="doc_altro_input") if doc_sel == "Altro" else doc_sel

        with col2:
            idx_mat = lista_materie_display.index(materia_suggerita) if materia_suggerita in lista_materie_display else 0
            mat_sel = st.selectbox("Materia:", lista_materie_display, index=idx_mat)
            mat_effettiva = st.text_input("Specifica Materia:", key="mat_altro_input") if mat_sel == "Altro" else mat_sel

        durata = st.slider("Durata sblocco (minuti):", 1, 120, 20, step=1)
        
        st.write("") 
        
        if st.button("🔓 SBLOCCA SAFARI", type="primary", use_container_width=True, key="btn_sblocca_final"):
            ids = MAPPA_CLASSI[classe_sel]
            udids = recupera_dispositivi_in_gruppo(ids["bloccata"])
            if udids:
                if esegui_azione("remove", ids["bloccata"], udids):
                    time.sleep(1.5)
                    if esegui_azione("add", ids["libera"], udids):
                        st.session_state.expiry_time = datetime.now() + timedelta(minutes=durata)
                        st.session_state.total_duration_secs = durata * 60
                        st.session_state.classe_attiva = classe_sel
                        st.session_state.docente_effettivo = doc_effettivo
                        st.session_state.materia_effettiva = mat_effettiva
                        
                        scrivi_log("SBLOCCO", classe_sel, doc_effettivo, mat_effettiva, durata)
                        st.rerun()
                    else: st.error("Errore API: Impossibile aggiungere al gruppo 'libera'.")
                else: st.error("Errore API: Impossibile rimuovere dal gruppo 'bloccata'.")
            else: st.warning("Nessun iPad rilevato nel gruppo bloccato di questa classe.")

    else:
        now = datetime.now()
        if now >= st.session_state.expiry_time:
            blocca_classe_sicuro(st.session_state.classe_attiva)
            scrivi_log("BLOCCO_AUTOMATICO", st.session_state.classe_attiva, st.session_state.docente_effettivo, st.session_state.materia_effettiva)
            st.session_state.expiry_time = None
            st.rerun()
            
        rimanente = st.session_state.expiry_time - now
        secondi_totali = int(rimanente.total_seconds())
        durata_totale = st.session_state.get("total_duration_secs", secondi_totali)
        
        st.info(f"🏫 Sessione attiva in **{st.session_state.classe_attiva}** | Insegnante: **{st.session_state.docente_effettivo}** | Lezione: **{st.session_state.materia_effettiva}**")
        
        st.markdown(f"### ⏳ Tempo rimanente: **{secondi_totali // 60}m {secondi_totali % 60}s**")
        
        percentuale_residua = max(0.0, min(1.0, secondi_totali / durata_totale)) if durata_totale > 0 else 0.0
        st.progress(percentuale_residua)
        st.write("")

        if st.button("🔒 BLOCCA SAFARI", type="primary", use_container_width=True, key="btn_blocca_final"):
            blocca_classe_sicuro(st.session_state.classe_attiva)
            scrivi_log("BLOCCO_MANUALE", st.session_state.classe_attiva, st.session_state.docente_effettivo, st.session_state.materia_effettiva)
            st.session_state.expiry_time = None
            st.rerun()
            
        time.sleep(1)
        st.rerun()
