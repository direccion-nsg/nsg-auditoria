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
        hoja = libro.worksheet(nombre_hoja)
        datos = hoja.get_all_values()
        nombres = [str(n).strip().upper() for n in datos[fila_encabezado]]
        return pd.DataFrame(datos[fila_encabezado+1:], columns=nombres)
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
    
    # Lista de Áreas (Unión de Programa y BDD para que no falte nada)
    areas_p = df_programa['ÁREA'].unique().tolist() if 'ÁREA' in df_programa.columns else []
    areas_b = df_bdd['ÁREA'].unique().tolist() if 'ÁREA' in df_bdd.columns else []
    lista_areas = sorted(list(set([str(a).strip().upper() for a in (areas_p + areas_b) if a])))

    fecha_sel = st.date_input("📅 Fecha", datetime.now()).strftime('%d/%m/%Y')
    area_sel = st.selectbox("📍 Área", lista_areas)
    cortes = {"11:00 AM (3h)": 3, "14:00 PM (6h)": 6, "17:00 PM (9h)": 9}
    corte_sel = st.selectbox("⏱️ Corte", list(cortes.keys()))
    
    # Filtrar Plan del Día
    col_a_p = 'ÁREA' if 'ÁREA' in df_programa.columns else 'AREA'
    df_plan = df_programa[(df_programa['FECHA'] == fecha_sel) & (df_programa[col_a_p].str.upper() == area_sel)].copy()
    if not df_plan.empty:
        st.subheader("📋 Plan del Día")
        st.dataframe(df_plan[['PIEZA', 'TOTAL']], hide_index=True)

# --- 4. PANEL CENTRAL ---
st.markdown(f"## 🛡️ Auditoría: {area_sel}")
st.divider()

# --- LÓGICA DE SELECCIÓN (EL CORAZÓN DEL SISTEMA) ---
st.markdown("### 🚀 CAPTURA")
c1, c2 = st.columns(2)

with c1:
    st.markdown("##### **Paso 1: Selección desde Programa**")
    # 1. PIEZA VIENE DEL PROGRAMA
    piezas_en_programa = df_plan['PIEZA'].unique().tolist() if not df_plan.empty else []
    pieza_sel = st.selectbox("Seleccione Pieza (Del Programa)", piezas_en_programa if piezas_en_programa else ["⚠️ SIN PIEZAS EN PROGRAMA"])
    
    # 2. SUBPROCESO VIENE DE LA BDD (Basado en la pieza seleccionada arriba)
    st.markdown("##### **Paso 2: Proceso desde BDD**")
    col_a_b = 'ÁREA' if 'ÁREA' in df_bdd.columns else 'AREA'
    # Buscamos en BDD la pieza que seleccionamos del Programa
    df_sub = df_bdd[(df_bdd['PIEZA'] == pieza_sel) & (df_bdd[col_a_b].str.upper() == area_sel)].copy()
    
    sub_sel = None
    if not df_sub.empty:
        col_s = 'SUB PROCESO' if 'SUB PROCESO' in df_sub.columns else 'SUBPROCESO'
        sub_sel = st.selectbox("Sub-proceso (Fases BDD)", df_sub[col_s].unique().tolist())
    else:
        st.warning("⚠️ No se encontraron procesos en BDD para esta pieza.")
        sub_sel = st.text_input("Escriba Proceso Manualmente", value="GENERAL")

with c2:
    st.markdown("##### **Paso 3: Datos de Campo**")
    f_id = st.session_state.form_id
    ops = st.number_input("Operadores", 1, key=f"ops_{f_id}")
    min_p = st.number_input("Min. Paro", 0, key=f"min_{f_id}")
    real = st.number_input("CANTIDAD REAL ACUMULADA", 0, key=f"re_{f_id}")

# --- BOTÓN DE GUARDADO ---
if st.button("💾 GUARDAR REGISTRO", use_container_width=True):
    if not piezas_en_programa:
        st.error("No hay piezas en el programa para registrar.")
    else:
        try:
            with st.spinner("Guardando en la nube..."):
                # Buscar PZ x Hora en BDD
                col_ph = 'PZ X H' if 'PZ X H' in df_sub.columns else 'PZH'
                # Filtramos la fila exacta del subproceso para sacar su capacidad
                if not df_sub.empty and sub_sel:
                    cap_fila = df_sub[df_sub['SUB PROCESO' if 'SUB PROCESO' in df_sub.columns else 'SUBPROCESO'] == sub_sel]
                    pzh = float(cap_fila[col_ph].iloc[0]) if not cap_fila.empty else 0
                else:
                    pzh = 0
                
                # Cálculo de Meta Teórica
                meta = int((pzh * (cortes[corte_sel] - (min_p/60))) * ops)
                
                zona_mx = pytz.timezone('America/Mexico_City')
                hora_mx = datetime.now(zona_mx).strftime('%H:%M:%S')
                
                fila = [fecha_sel, area_sel, corte_sel, pieza_sel, sub_sel, int(real), meta, real-meta, int(ops), f"Paro: {min_p}min", hora_mx]
                conectar_libro().worksheet("AUDITAR").append_row(fila)
                
                st.toast("✅ ¡Registro Exitoso!")
                st.session_state.form_id += 1
                time.sleep(1)
                st.rerun()
        except Exception as e: st.error(f"Error al guardar: {e}")
