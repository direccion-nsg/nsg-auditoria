import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import plotly.graph_objects as go
import time
import os
import pytz

# --- 1. CONFIGURACIÓN ---
JSON_FILE = 'creds_nsg.json' 
ID_LIBRO = '13ZF5TXwgEZSlrODQFF43Rvs4JmB19s6V0KNV1l72RHA'
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
LOGO_FILENAME = "LOGO NSG SFONDO.png"

@st.cache_resource
def obtener_cliente():
    try:
        creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_FILE, SCOPE)
        return gspread.authorize(creds)
    except: return None

def conectar_libro():
    cliente = obtener_cliente()
    try: return cliente.open_by_key(ID_LIBRO) if cliente else None
    except: return None

@st.cache_data(ttl=30)
def leer_datos(nombre_hoja, fila_encabezado=0):
    try:
        libro = conectar_libro()
        if not libro: return pd.DataFrame()
        hoja = libro.worksheet(nombre_hoja)
        datos = hoja.get_all_values()
        if len(datos) <= fila_encabezado: return pd.DataFrame()
        # Normalizamos encabezados: Mayúsculas y sin espacios
        nombres = [str(n).strip().upper() for n in datos[fila_encabezado]]
        df = pd.DataFrame(datos[fila_encabezado+1:], columns=nombres)
        return df
    except: return pd.DataFrame()

# --- 2. INTERFAZ ---
st.set_page_config(layout="wide", page_title="NSG Auditoría")

if 'form_id' not in st.session_state: st.session_state.form_id = 0

df_programa = leer_datos("PROGRAMA", 1)
df_bdd = leer_datos("BDD", 0)
df_auditorias = leer_datos("AUDITAR", 0)

# --- 3. SIDEBAR ---
with st.sidebar:
    if os.path.exists(LOGO_FILENAME): st.image(LOGO_FILENAME, use_container_width=True)
    st.markdown("### ⚙️ Ajustes")
    
    # Identificar columna AREA en ambos DFs
    c_area_p = 'ÁREA' if 'ÁREA' in df_programa.columns else 'AREA'
    c_area_b = 'ÁREA' if 'ÁREA' in df_bdd.columns else 'AREA'

    # Lista de Áreas
    list_p = df_programa[c_area_p].unique().tolist() if not df_programa.empty and c_area_p in df_programa.columns else []
    list_b = df_bdd[c_area_b].unique().tolist() if not df_bdd.empty and c_area_b in df_bdd.columns else []
    lista_areas = sorted(list(set([str(a).strip().upper() for a in (list_p + list_b) if a])))

    fecha_sel = st.date_input("📅 Fecha", datetime.now()).strftime('%d/%m/%Y')
    area_sel = st.selectbox("📍 Área", lista_areas if lista_areas else ["MOLDEO", "ENSAMBLE", "CORAZONES"])
    cortes = {"11:00 AM (3h)": 3, "14:00 PM (6h)": 6, "17:00 PM (9h)": 9}
    corte_sel = st.selectbox("⏱️ Corte", list(cortes.keys()))
    
    # Plan del Día
    df_plan = pd.DataFrame()
    if not df_programa.empty and c_area_p in df_programa.columns:
        df_plan = df_programa[(df_programa['FECHA'] == fecha_sel) & (df_programa[c_area_p].str.upper() == area_sel)].copy()
        if not df_plan.empty:
            st.subheader("📋 Plan del Día")
            st.dataframe(df_plan[['PIEZA', 'TOTAL']], hide_index=True)

# --- 4. PANEL CENTRAL ---
st.markdown(f"## 🛡️ Auditoría: {area_sel}")
st.divider()

st.markdown("### 🚀 CAPTURA")
c1, c2 = st.columns(2)

with c1:
    st.markdown("##### **1. Pieza (Programa)**")
    piezas_en_prog = df_plan['PIEZA'].unique().tolist() if not df_plan.empty else []
    pieza_sel = st.selectbox("Seleccione Pieza", piezas_en_prog if piezas_en_prog else ["⚠️ SIN PROGRAMA"])
    
    st.markdown("##### **2. Proceso (BDD)**")
    # Filtro robusto para BDD
    df_sub = pd.DataFrame()
    if not df_bdd.empty:
        c_area_b = 'ÁREA' if 'ÁREA' in df_bdd.columns else 'AREA'
        c_sub_b = 'SUB PROCESO' if 'SUB PROCESO' in df_bdd.columns else 'SUBPROCESO'
        df_sub = df_bdd[(df_bdd['PIEZA'] == pieza_sel) & (df_bdd[c_area_b].str.upper() == area_sel)].copy()
    
    sub_sel = None
    if not df_sub.empty and c_sub_b in df_sub.columns:
        sub_sel = st.selectbox("Sub-proceso", df_sub[c_sub_b].unique().tolist())
    else:
        st.warning("Pieza no encontrada en BDD. Escriba el proceso:")
        sub_sel = st.text_input("Proceso Manual", value="GENERAL")

with c2:
    st.markdown("##### **3. Datos**")
    f_id = st.session_state.form_id
    ops = st.number_input("Operadores", 1, key=f"ops_{f_id}")
    min_p = st.number_input("Min. Paro", 0, key=f"min_{f_id}")
    real = st.number_input("CANTIDAD REAL ACUMULADA", 0, key=f"re_{f_id}")

if st.button("💾 GUARDAR REGISTRO", use_container_width=True):
    try:
        with st.spinner("Guardando..."):
            # PZ X H
            c_ph = 'PZ X H' if 'PZ X H' in df_bdd.columns else 'PZH'
            pzh = 0
            if not df_sub.empty and sub_sel:
                c_sub_b = 'SUB PROCESO' if 'SUB PROCESO' in df_sub.columns else 'SUBPROCESO'
                fila_cap = df_sub[df_sub[c_sub_b] == sub_sel]
                if not fila_cap.empty: pzh = float(fila_cap[c_ph].iloc[0])
            
            meta = int((pzh * (cortes[corte_sel] - (min_p/60))) * ops)
            zona_mx = pytz.timezone('America/Mexico_City')
            hora_mx = datetime.now(zona_mx).strftime('%H:%M:%S')
            
            fila = [fecha_sel, area_sel, corte_sel, pieza_sel, sub_sel, int(real), meta, real-meta, int(ops), f"Paro: {min_p}min", hora_mx]
            conectar_libro().worksheet("AUDITAR").append_row(fila)
            
            st.toast("✅ ¡Guardado!")
            st.session_state.form_id += 1
            time.sleep(1)
            st.rerun()
    except Exception as e: st.error(f"Error: {e}")
