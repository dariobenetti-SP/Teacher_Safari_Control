import streamlit as st
import pandas as pd
import requests
import time
import csv
import io
import gspread
import zoneinfo
from datetime import datetime, timedelta, time as dt_time
import os

# --- 1. CONFIGURAZIONE PAGINA ---
st.set_page_config(page_title="Teacher Jamf • Safari", page_icon="🔐", initial_sidebar_state="collapsed")

# --- 2. CONFIGURAZIONE API, GOOGLE SHEETS E FILE ---
JAMF_URL = "https://liceosportivopd.jamfcloud.com/api"
AUTH = (st.secrets["jamf"]["username"], st.secrets["jamf"]["password"])
HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}

NOME_FOGLIO = "Log-Teacher-Safari"

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

# --- GESTIONE ORA ITALIANA ---
def ora_ita():
    """Restituisce l'orario esatto di Roma/Padova ignorando l'orario del server cloud"""
    return datetime.now(zoneinfo.ZoneInfo("Europe/Rome"))

# --- CONNESSIONE GOOGLE SHEETS ---
@st.cache_resource
def inizializza_connessione_sheets():
    """Si connette a Google Sheets una sola volta all'avvio usando i Secrets."""
    try:
        gc = gspread.service_account_from_dict(st.secrets["gspread"])
        sh = gc.open(NOME_FOGLIO)
        ws = sh.get_worksheet(0)
        
        if len(ws.get_all_values()) == 0:
            intestazioni = ["Data", "Ora_Reale", "Azione", "Classe", "Docente", "Materia", "Durata_Minuti"]
            ws.append_row(intestazioni)
            
        return ws
    except Exception as e:
        st.error(f"Impossibile connettersi a Google Sheets: {e}")
        return None

worksheet = inizializza_connessione_sheets()

# --- 3. DETERMINAZIONE FASCIA ORARIA ---
def determina_ora_scolastica():
    current_time = ora_ita().time()
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
    if worksheet is not None:
        try:
            now = ora_ita()
            nuova_riga = [
                now.strftime("%d/%m/%Y"), 
                now.strftime("%H:%M:%S"), 
                azione, 
                classe, 
                docente, 
                materia, 
                str(durata)
            ]
            worksheet.append_row(nuova_riga)
        except Exception as e:
            st.error(f"Errore durante il salvataggio del log su Google Sheets: {e}")

# --- CONTROLLO GLOBALE SCADENZE PENDENTI (AUTO-HEALING) ---
def esegui_pulizia_scadenze():
    if worksheet is None:
        return
    try:
        records = worksheet.get_all_records()
        if not records: return
        
        df_log = pd.DataFrame(records)
        df_log.columns = df_log.columns.str.strip()
        
        oggi = ora_ita().strftime("%d/%m/%Y")
        if 'Data' not in df_log.columns: return
        
        df_oggi = df_log[df_log['Data'].astype(str).str.strip() == oggi]
        if df_oggi.empty: return
            
        ultime_azioni = df_oggi.sort_values(by=['Ora_Reale']).groupby('Classe').last()
        ora_attuale = ora_ita()
        
        for classe, row in ultime_azioni.iterrows():
            if str(row.get('Azione', '')).strip() == 'SBLOCCO':
                try:
                    ora_str = str(row['Ora_Reale']).strip()
                    if len(ora_str.split(':')) == 2: ora_str += ":00"
                    
                    ora_inizio = datetime.strptime(f"{oggi} {ora_str}", "%d/%m/%Y %H:%M:%S")
                    ora_inizio = ora_inizio.replace(tzinfo=zoneinfo.ZoneInfo("Europe/Rome"))
                    
                    durata = int(pd.to_numeric(row.get('Durata_Minuti', 0), errors='coerce') or 0)
                    if durata > 0:
                        ora_fine = ora_inizio + timedelta(minutes=durata)
                        if ora_attuale >= ora_fine:
                            blocca_classe_sicuro(classe)
                            scrivi_log("BLOCCO_AUTOMATICO", classe, row.get('Docente', ''), row.get('Materia', ''))
                except Exception:
                    continue
    except Exception:
        pass

# --- RECUPERO SESSIONE CORRENTE (REFRESH-RESISTANCE) ---
def recupera_sessione_attiva_corrente(docente):
    if worksheet is None: return None
    try:
        records = worksheet.get_all_records()
        if not records: return None
        
        df_log = pd.DataFrame(records)
        df_log.columns = df_log.columns.str.strip()
        
        oggi = ora_ita().strftime("%d/%m/%Y")
        if 'Data' not in df_log.columns or 'Docente' not in df_log.columns: return None
        
        df_oggi_docente = df_log[
            (df_log['Data'].astype(str).str.strip() == oggi) & 
            (df_log['Docente'].astype(str).str.strip().str.lower() == docente.strip().lower())
        ]
        
        if df_oggi_docente.empty: return None
            
        ultima_riga = df_oggi_docente.sort_values(by=['Ora_Reale']).iloc[-1]
        
        if str(ultima_riga.get('Azione', '')).strip() == 'SBLOCCO':
            try:
                ora_str = str(ultima_riga['Ora_Reale']).strip()
                if len(ora_str.split(':')) == 2: ora_str += ":00"
                
                ora_inizio = datetime.strptime(f"{oggi} {ora_str}", "%d/%m/%Y %H:%M:%S")
                ora_inizio = ora_inizio.replace(tzinfo=zoneinfo.ZoneInfo("Europe/Rome"))
                
                durata = int(pd.to_numeric(ultima_riga.get('Durata_Minuti', 0), errors='coerce') or 0)
                if durata > 0:
                    ora_fine = ora_inizio + timedelta(minutes=durata)
                    ora_attuale = ora_ita()
                    
                    if ora_fine > ora_attuale:
                        return {
                            "classe": ultima_riga.get('Classe', ''),
                            "materia": ultima_riga.get('Materia', ''),
                            "expiry_time": ora_fine,
                            "total_duration_secs": durata * 60
                        }
            except Exception:
                pass
        return None
    except Exception:
        return None

# --- LETTURA SESSIONI CONDIVISE DA GOOGLE SHEETS (CON CACHE) ---
@st.cache_data(ttl=20)
def ottieni_sessioni_attive_globali_cached():
    if worksheet is None: return []
    try:
        records = worksheet.get_all_records()
        if not records: return []
            
        df_log = pd.DataFrame(records)
        df_log.columns = df_log.columns.str.strip()
        
        if 'Data' not in df_log.columns: return []

        oggi = ora_ita().strftime("%d/%m/%Y")
        df_oggi = df_log[df_log['Data'].astype(str).str.strip() == oggi]
        
        if df_oggi.empty: return []
            
        ultime_azioni = df_oggi.sort_values(by=['Ora_Reale']).groupby('Classe').last()
        sessioni_attive = []
        ora_attuale = ora_ita()
        
        for classe, row in ultime_azioni.iterrows():
            if str(row.get('Azione', '')).strip() == 'SBLOCCO':
                try:
                    ora_str = str(row['Ora_Reale']).strip()
                    if len(ora_str.split(':')) == 2: ora_str += ":00"
                        
                    ora_inizio = datetime.strptime(f"{oggi} {ora_str}", "%d/%m/%Y %H:%M:%S")
                    ora_inizio = ora_inizio.replace(tzinfo=zoneinfo.ZoneInfo("Europe/Rome"))
                    
                    durata = int(pd.to_numeric(row.get('Durata_Minuti', 0), errors='coerce') or 0)
                    if durata > 0:
                        ora_fine = ora_inizio + timedelta(minutes=durata)
                        
                        if ora_fine > ora_attuale:
                            str_inizio = ora_inizio.strftime("%H:%M")
                            str_fine = ora_fine.strftime("%H:%M")
                            docente = row.get('Docente', 'Sconosciuto')
                            sessioni_attive.append(f"📍 **{docente}** ha sbloccato la **{classe}** dalle {str_inizio} alle {str_fine}.")
                except Exception:
                    continue
        
        return sessioni_attive
    except Exception:
        return []

# --- 5. CARICAMENTO E PULIZIA DATI LOCALI ---
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
    st.error("File 'orario.csv' non trovato.")
    st.stop()

# --- 6. IDENTIFICAZIONE DOCENTE ---
param_docente = st.query_params.get("docente", None)

if param_docente:
    proprietario = param_docente
else:
    proprietario = st.selectbox("Seleziona il tuo Profilo (Modalità Test Locale):", lista_docenti)

# --- GESTIONE SINCRONIZZAZIONE STATO DAL DB ---
if "ultimo_docente" not in st.session_state:
    st.session_state.ultimo_docente = proprietario

if st.session_state.ultimo_docente != proprietario:
    st.session_state.ultimo_docente = proprietario
    st.session_state.db_synced = False

if "db_synced" not in st.session_state or not st.session_state.db_synced:
    with st.spinner("Sincronizzazione in corso con il database di istituto..."):
        esegui_pulizia_scadenze()
        sessione_db = recupera_sessione_attiva_corrente(proprietario)
        if sessione_db:
            st.session_state.expiry_time = sessione_db["expiry_time"]
            st.session_state.total_duration_secs = sessione_db["total_duration_secs"]
            st.session_state.classe_attiva = sessione_db["classe"]
            st.session_state.docente_effettivo = proprietario
            st.session_state.materia_effettiva = sessione_db["materia"]
        else:
            st.session_state.expiry_time = None
        st.session_state.db_synced = True

# --- 7. LOGICA AUTO-COMPILAZIONE DA ORARIO ---
ora_scolastica_attuale = determina_ora_scolastica()
giorni_it = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
giorno_oggi = giorni_it[ora_ita().weekday()]

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
        if c_suggerita in MAPPA_CLASSI: classe_suggerita = c_suggerita
        sigla_orario = str(lezione_attuale.iloc[0]['Materia']).strip()
        materia_suggerita = formatta_materia(sigla_orario)

# --- 8. STATO E CSS DINAMICO ---
css_style = """
<style>
div[data-testid="stStatusWidget"] { visibility: hidden !important; display: none !important; }
footer {visibility: hidden !important;}
div[data-testid="stDeployButton"] {display:none !important;}
div[data-testid="stToolbarAction"] {display:none !important;}
div[data-testid="stMainMenu"] {display:none !important;}
</style>
"""
if st.session_state.expiry_time is not None:
    css_style += """
    <style>
    button[kind="primary"] { background-color: #0068c9 !important; border-color: #0068c9 !important; color: white !important; }
    </style>
    """
st.markdown(css_style, unsafe_allow_html=True)

# --- 9. INTERFACCIA PRINCIPALE ---
st.title("🔐 Teacher Jamf • Safari 🦏")
st.caption(f"Accesso effettuato come: **{proprietario}**")

# --- PANNELLO CONDIVISO ---
sessioni_globali = ottieni_sessioni_attive_globali_cached()
if sessioni_globali:
    with st.expander("🌐 Sessioni Safari sbloccate in questo momento nell'istituto", expanded=True):
        for s in sessioni_globali:
            st.markdown(s)

st.markdown("---")

# --- 10. SIDEBAR AMMINISTRATORE ---
with st.sidebar:
    st.header("⚙️ Amministrazione")
    password_inserita = st.text_input("Password Amministratore:", type="password")

    if "ADMIN_PASSWORD" not in st.secrets:
        st.error("⚠️ Password amministratore mancante nel Cloud.")
    elif password_inserita == st.secrets["ADMIN_PASSWORD"]:
        st.success("Accesso effettuato!")
        st.subheader("📊 Esportazione Registro")
        if worksheet is not None:
            try:
                records = worksheet.get_all_values()
                if len(records) > 1: 
                    output = io.StringIO()
                    csv.writer(output).writerows(records)
                    st.download_button("📥 Scarica log_utilizzi.csv", output.getvalue(), "log_utilizzi.csv", "text/csv", use_container_width=True)
                else:
                    st.info("Nessun dato registrato.")
            except Exception as e:
                st.error(f"Errore: {e}")
        st.markdown("---")
        if st.button("🔄 RIPRISTINA TUTTI I GRUPPI", use_container_width=True):
            with st.spinner("Sincronizzazione in corso..."):
                for nome, ids in MAPPA_CLASSI.items():
                    devs = recupera_dispositivi_in_gruppo(ids["libera"])
                    if devs:
                        esegui_azione("remove", ids["libera"], devs)
                        esegui_azione("add", ids["bloccata"], devs)
                st.success("Gruppi ripristinati correttamente.")
    elif password_inserita:
        st.error("Password errata.")
            
# --- 11. GESTIONE PANNELLI INTERFACCIA ---
zona_dinamica = st.empty()

with zona_dinamica.container():
    if st.session_state.expiry_time is None:
        idx_classe = lista_classi.index(classe_suggerita) if classe_suggerita in lista_classi else 0
        classe_sel = st.selectbox("Seleziona Classe:", lista_classi, index=idx_classe)
        
        col1, col2 = st.columns(2)
        with col1:
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
                        st.session_state.expiry_time = ora_ita() + timedelta(minutes=durata)
                        st.session_state.total_duration_secs = durata * 60
                        st.session_state.classe_attiva = classe_sel
                        st.session_state.docente_effettivo = doc_effettivo
                        st.session_state.materia_effettiva = mat_effettiva
                        
                        scrivi_log("SBLOCCO", classe_sel, doc_effettivo, mat_effettiva, durata)
                        ottieni_sessioni_attive_globali_cached.clear() # Svuota la cache in modo mirato
                        st.rerun()
                    else: st.error("Errore API (add libera).")
                else: st.error("Errore API (remove bloccata).")
            else: st.warning("Nessun iPad rilevato nel gruppo bloccato.")

    else:
        now = ora_ita()
        if now >= st.session_state.expiry_time:
            blocca_classe_sicuro(st.session_state.classe_attiva)
            scrivi_log("BLOCCO_AUTOMATICO", st.session_state.classe_attiva, st.session_state.docente_effettivo, st.session_state.materia_effettiva)
            st.session_state.expiry_time = None
            st.session_state.db_synced = False
            ottieni_sessioni_attive_globali_cached.clear()
            st.rerun()
            
        rimanente = st.session_state.expiry_time - now
        secondi_totali = int(rimanente.total_seconds())
        durata_totale = st.session_state.get("total_duration_secs", secondi_totali)
        
        st.info(f"🏫 **{st.session_state.classe_attiva}** | 👨‍🏫 **{st.session_state.docente_effettivo}** | 📚 **{st.session_state.materia_effettiva}**")
        
        # Miglioramento UI Timer
        st.markdown(f"<h2 style='text-align: center; color: #ff4b4b;'>⏳ {secondi_totali // 60:02d}:{secondi_totali % 60:02d}</h2>", unsafe_allow_html=True)
        
        percentuale_residua = max(0.0, min(1.0, secondi_totali / durata_totale)) if durata_totale > 0 else 0.0
        st.progress(percentuale_residua)
        st.write("")

        if st.button("🔒 BLOCCA SAFARI", type="primary", use_container_width=True, key="btn_blocca_final"):
            blocca_classe_sicuro(st.session_state.classe_attiva)
            scrivi_log("BLOCCO_MANUALE", st.session_state.classe_attiva, st.session_state.docente_effettivo, st.session_state.materia_effettiva)
            st.session_state.expiry_time = None
            st.session_state.db_synced = False
            ottieni_sessioni_attive_globali_cached.clear()
            st.rerun()
            
        time.sleep(1)
        try:
            st.rerun()
        except Exception:
            pass # Previene falsi errori in console durante il refresh forzato
