import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import plotly.graph_objects as go
import time
import os
import pytz
import numpy as np

# --- 1. CONFIGURACIÓN TÉCNICA ---
JSON_FILE = 'creds_nsg.json' 
ID_LIBRO = '13ZF5TXwgEZSlrODQFF43Rvs4JmB19s6V0KNV1l72RHA'
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
LOGO_FILENAME = "LOGO NSG SFONDO.png"

MOTIVOS_PARO = [
    "SIN PARO", "FALLA MECÁNICA", "FALLA ELÉCTRICA", "FALTA DE MATERIAL",
    "CAMBIO DE MODELO / SET-UP", "AUSENCIA DE OPERADOR", "JUNTA DE CALIDAD / SEGURIDAD",
    "LIMPIEZA / 5S", "OTRO (ESPECIFICAR EN NOTAS)"
]

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

@st.cache_data(ttl=60)
def leer_datos_seguro(nombre_hoja, fila_encabezado=0):
    try:
        libro = conectar_libro()
        if not libro: return pd.DataFrame()
        hoja = libro.worksheet(nombre_hoja)
        datos = hoja.get_all_values()
        if len(datos) <= fila_encabezado: return pd.DataFrame()
        nombres = datos[fila_encabezado]
        df = pd.DataFrame(datos[fila_encabezado+1:])
        df.columns = [str(n).strip().upper() if n else f"COL_{i}" for i, n in enumerate(nombres)]
        for col in df.columns: df[col] = df[col].astype(str).str.strip()
        return df
    except: return pd.DataFrame()

def obtener_color_nsg(valor):
    if valor >= 85: return "#2ecc71" # Verde
    if valor >= 80: return "#f1c40f" # Amarillo
    if valor >= 70: return "#e67e22" # Naranja
    return "#E32B13" # Rojo

# --- 2. INTERFAZ ---
st.set_page_config(layout="wide", page_title="NSG Auditoría v2.9", page_icon="🛡️")

st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap');
    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
    .stApp { background-color: #F8F9FA; }
    .metric-card {
        background: white; padding: 20px; border-radius: 12px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.05);
        border-left: 5px solid #E32B13; text-align: center;
    }
    .capture-container {
        background: white; padding: 30px; border-radius: 15px;
        border: 1px solid #E0E0E0; margin-top: 20px;
        box-shadow: 0 10px 25px rgba(0,0,0,0.03);
    }
    .stButton>button {
        width: 100%; background: linear-gradient(135deg, #E32B13 0%, #B2220F 100%) !important;
        color: white !important; border-radius: 10px !important;
        padding: 18px !important; font-weight: 700 !important; border: none !important;
    }
    </style>
    """, unsafe_allow_html=True)

if 'form_id' not in st.session_state: st.session_state.form_id = 0

# Carga de datos
df_programa = leer_datos_seguro("PROGRAMA", 1)
df_bdd_raw = leer_datos_seguro("BDD", 0)
df_auditorias = leer_datos_seguro("AUDITAR", 0)

# Buscador de columna dinámico para SUB PRO CESO
col_sub_actual = ""
if not df_bdd_raw.empty:
    for c in df_bdd_raw.columns:
        if 'SUB' in c and 'CESO' in c:
            col_sub_actual = c
            break
    if not col_sub_actual: col_sub_actual = 'SUB PRO CESO'
    if 'COL_4' in df_bdd_raw.columns:
        df_bdd_raw = df_bdd_raw[df_bdd_raw['COL_4'].str.upper().str.contains('TRUE', na=False)].copy()
    df_bdd_raw = df_bdd_raw[df_bdd_raw[col_sub_actual].str.len() > 1].copy()
    df_bdd_raw = df_bdd_raw[df_bdd_raw[col_sub_actual] != '0'].copy()

# Sidebar
with st.sidebar:
    if os.path.exists(LOGO_FILENAME): st.image(LOGO_FILENAME, use_container_width=True)
    st.markdown("<h3 style='text-align: center;'>CONTROL DE ACCESO</h3>", unsafe_allow_html=True)
    fecha_dt = st.date_input("📅 FECHA", datetime.now(), help="Día de producción.")
    fecha_sel = fecha_dt.strftime('%d/%m/%Y')
    
    if not df_programa.empty and 'ÁREA' in df_programa.columns:
        lista_areas = [a for a in df_programa['ÁREA'].unique().tolist() if a and a.upper() != "ÁREA"]
    else:
        lista_areas = ["MOLDEO", "CORAZONES", "CORTE", "ENSAMBLE"]
    
    area_sel = st.selectbox("📍 ÁREA", lista_areas, help="Departamento auditado.")
    cortes_dict = {"11:00 AM (3h)": 3, "14:00 PM (6h)": 6, "17:00 PM (9h)": 9}
    corte_sel = st.selectbox("⏱️ CORTE", list(cortes_dict.keys()), help="Corte de producción.")
    horas_acum = cortes_dict[corte_sel]

    st.divider()
    st.markdown("### 📋 Plan del Día")
    df_plan_dia = pd.DataFrame()
    if not df_programa.empty:
        df_plan_dia = df_programa[(df_programa['FECHA'] == fecha_sel) & (df_programa['ÁREA'] == area_sel)].copy()
        if area_sel.upper() == "MOLDEO" and not df_plan_dia.empty:
            df_plan_dia = df_plan_dia[df_plan_dia['PIEZA'].str.contains('GENERAL|VACIADO|ADOBES', case=False, na=False)]
        if not df_plan_dia.empty:
            st.dataframe(df_plan_dia[['PIEZA', 'TOTAL']], hide_index=True)

# Encabezado Superior
st.markdown(f"""
    <div style='display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px;'>
        <h1 style='margin:0;'>🛡️ SISTEMA <span style='color:#E32B13;'>NSG</span> AUDITORÍA</h1>
        <div style='background:#EEE; padding: 8px 20px; border-radius:25px; font-weight:bold; color:#333; border: 1px solid #CCC;'>
            📍 ÁREA: {area_sel}
        </div>
    </div>
""", unsafe_allow_html=True)

avance_global = 0.0
df_resumen_final = pd.DataFrame()

if not df_plan_dia.empty and not df_bdd_raw.empty:
    df_sub_base_total = df_bdd_raw[df_bdd_raw['PROCESO'] == area_sel][['PIEZA', col_sub_actual]].copy()
    piezas_en_plan = df_plan_dia['PIEZA'].unique()
    df_base = df_sub_base_total[df_sub_base_total['PIEZA'].isin(piezas_en_plan)].copy()
    
    if not df_base.empty:
        df_base = pd.merge(df_base, df_plan_dia[['PIEZA', 'TOTAL']], on='PIEZA', how='left')
        df_base['TOTAL'] = pd.to_numeric(df_base['TOTAL'], errors='coerce').fillna(0)
        if not df_auditorias.empty:
            df_aud_hoy = df_auditorias[(df_auditorias['FECHA'] == fecha_sel) & (df_auditorias['ÁREA'] == area_sel)].copy()
            df_aud_hoy['REAL'] = pd.to_numeric(df_aud_hoy['REAL'], errors='coerce').fillna(0)
            df_max_real = df_aud_hoy.groupby(['PIEZA', 'SUBPROCESO'])['REAL'].max().reset_index()
            df_final = pd.merge(df_base, df_max_real, left_on=['PIEZA', col_sub_actual], right_on=['PIEZA', 'SUBPROCESO'], how='left').fillna(0)
        else:
            df_final = df_base.assign(REAL=0)

        # --- CAMBIO AQUÍ: QUITAMOS EL CLIP PARA PERMITIR MÁS DEL 100% ---
        df_final['% REAL'] = (df_final['REAL'] / df_final['TOTAL'] * 100).fillna(0)
        df_resumen_final = df_final[['PIEZA', col_sub_actual, 'TOTAL', 'REAL', '% REAL']].copy()
        df_resumen_final.columns = ['PIEZA', 'SUBPROCESO', 'PROGRAMADO', 'AVANCE', '% REAL']
        
        # El promedio ya no tendrá tope, dando las décimas exactas
        avance_global = round(df_final['% REAL'].mean(), 1)

# KPIs y Gráficos
k1, k2, k3 = st.columns(3)
with k1: st.markdown(f"<div class='metric-card'><small>EFICIENCIA GLOBAL</small><h2>{avance_global}%</h2></div>", unsafe_allow_html=True)
with k2: st.markdown(f"<div class='metric-card'><small>META TURNO</small><h2>{int(df_resumen_final['PROGRAMADO'].sum()) if not df_resumen_final.empty else 0} PZS</h2></div>", unsafe_allow_html=True)
with k3: st.markdown(f"<div class='metric-card'><small>REAL TURNO</small><h2>{int(df_resumen_final['AVANCE'].sum()) if not df_resumen_final.empty else 0} PZS</h2></div>", unsafe_allow_html=True)

c_g, c_b = st.columns([1, 2])
with c_g:
    color_g = obtener_color_nsg(avance_global)
    # El gráfico se detiene visualmente en 100 para no romperse, pero el número muestra lo real
    fig = go.Figure(go.Indicator(mode="gauge+number", value=avance_global, gauge={
        'bar':{'color': color_g}, 'axis': {'range': [0, 100]}, 'bgcolor': "#f0f2f6",
        'steps': [{'range': [0, 80], 'color': "rgba(227, 43, 19, 0.05)"}, {'range': [80, 85], 'color': "rgba(241, 196, 15, 0.1)"}, {'range': [85, 100], 'color': "rgba(46, 204, 113, 0.1)"}]
    }))
    fig.update_layout(height=280, margin=dict(l=30, r=30, t=30, b=10))
    st.plotly_chart(fig, use_container_width=True)

with c_b:
    if not df_resumen_final.empty:
        fig_bar = go.Figure()
        fig_bar.add_trace(go.Bar(name='Meta', x=df_resumen_final['SUBPROCESO'], y=df_resumen_final['PROGRAMADO'], marker_color='#610B0B'))
        fig_bar.add_trace(go.Bar(name='Real', x=df_resumen_final['SUBPROCESO'], y=df_resumen_final['AVANCE'], marker_color='#E32B13'))
        fig_bar.update_layout(barmode='group', height=280, margin=dict(l=0, r=0, t=20, b=0), legend=dict(orientation="h", y=1.1))
        st.plotly_chart(fig_bar, use_container_width=True)

# --- 1. REGISTRO DE AUDITORÍA (COMPLETO: SALTO + AYUDAS) ---
st.markdown("<div class='capture-container'>", unsafe_allow_html=True)
st.subheader("🚀 REGISTRO DE AUDITORÍA")

piezas_validas = df_plan_dia['PIEZA'].unique() if not df_plan_dia.empty else []
piezas_pendientes = []

if not df_plan_dia.empty and not df_bdd_raw.empty:
    for p in piezas_validas:
        subs_totales = df_bdd_raw[(df_bdd_raw['PIEZA'] == p) & (df_bdd_raw['PROCESO'] == area_sel)][col_sub_actual].unique()
        reps_p = df_auditorias[(df_auditorias['FECHA'] == fecha_sel) & (df_auditorias['CORTE'] == corte_sel) & (df_auditorias['PIEZA'] == p)]['SUBPROCESO'].tolist() if not df_auditorias.empty else []
        if any(s for s in subs_totales if s not in reps_p):
            piezas_pendientes.append(p)

lista_desplegable = piezas_pendientes if piezas_pendientes else piezas_validas

c1, c2, c3 = st.columns([1,1,1])
f_id = st.session_state.form_id

with c1:
    p_sel = st.selectbox("PIEZA 📦", lista_desplegable, help="Piezas programadas. Salta automáticamente a la siguiente pendiente.")
    df_s = df_bdd_raw[(df_bdd_raw['PIEZA'] == p_sel) & (df_bdd_raw['PROCESO'] == area_sel)].copy()
    reps = df_auditorias[(df_auditorias['FECHA'] == fecha_sel) & (df_auditorias['CORTE'] == corte_sel) & (df_auditorias['PIEZA'] == p_sel)]['SUBPROCESO'].tolist() if not df_auditorias.empty else []
    sub_list = [s for s in df_s[col_sub_actual].unique() if s not in reps]
    s_sel = st.selectbox("SUB-PROCESO ⚙️", sub_list if sub_list else ["✅ PIEZA TERMINADA"], help="Operación a auditar.")

with c2:
    ops = st.number_input("OPERADORES 👥", 1, key=f"ops_{f_id}", help="Personal en línea.")
    real = st.number_input("CANTIDAD REAL 🔢", 0, key=f"r_{f_id}", help="Piezas buenas acumuladas.")

with c3:
    mins = st.number_input("MIN. PARO ⏳", 0, key=f"m_{f_id}", help="Tiempo muerto.")
    mot = st.selectbox("MOTIVO PARO ❓", MOTIVOS_PARO, key=f"mot_{f_id}", help="Causa del paro.")

notas = st.text_input("📝 NOTAS", key=f"n_{f_id}", help="Observaciones adicionales.")

if s_sel and s_sel != "✅ PIEZA TERMINADA":
    pz_h = float(df_s[df_s[col_sub_actual] == s_sel]['PZ X H'].iloc[0]) if not df_s.empty else 0
    meta = int((pz_h * max(0, horas_acum - (mins/60))) * ops)
    st.info(f"💡 **Meta:** {meta} piezas")
    if st.button("💾 GUARDAR REGISTRO"):
        try:
            h = datetime.now(pytz.timezone('America/Mexico_City')).strftime('%H:%M:%S')
            fila = [fecha_sel, area_sel, corte_sel, p_sel, s_sel, int(real), meta, int(real-meta), int(ops), f"[{mot}] {notas}", h]
            conectar_libro().worksheet("AUDITAR").append_row(fila)
            st.toast("✅ GUARDADO EXITOSO")
            st.cache_data.clear()
            st.session_state.form_id += 1
            time.sleep(0.5)
            st.rerun()
        except Exception as e: st.error(f"Error: {e}")
else:
    st.success("🎉 ¡Pieza completada!")
st.markdown("</div>", unsafe_allow_html=True)

# --- 2. ESTATUS DETALLADO (CON BARRAS DE AVANCE) ---
st.markdown("### 📊 ESTATUS DETALLADO")
if not df_resumen_final.empty:
    for _, row in df_resumen_final.iterrows():
        val_n = round(row['% REAL'], 1)
        color_n = obtener_color_nsg(val_n)
        ancho_barra = min(val_n, 100) 
        
        st.markdown(f"""
            <div style='background:white; padding:10px 15px; border-radius:8px; border-left:5px solid {color_n}; margin-bottom:8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1);'>
                <div style='display:flex; justify-content:space-between; align-items:center;'>
                    <div style='font-size:14px;'><b>{row['PIEZA']}</b> - <span style='color:#555;'>{row['SUBPROCESO']}</span></div>
                    <div style='color:{color_n}; font-weight:bold; font-size:16px;'>{val_n}%</div>
                </div>
                <div style='background:#eee; height:6px; border-radius:3px; margin: 6px 0;'>
                    <div style='background:{color_n}; width:{ancho_barra}%; height:100%; border-radius:3px;'></div>
                </div>
                <div style='display:flex; justify-content:space-between; font-size:11px; color:gray;'>
                    <span>Prog: <b>{row['PROGRAMADO']}</b></span>
                    <span>Real: <b>{row['AVANCE']}</b></span>
                </div>
            </div>
        """, unsafe_allow_html=True)

# --- TABLA DE CAPACIDADES DINÁMICA ---
st.divider()
titulo_expander = f"📖 CONSULTAR CAPACIDADES (PZ/H) - PIEZA: {p_sel if p_sel else 'NO SELECCIONADA'}"

with st.expander(titulo_expander):
    if not df_s.empty:
        st.write(f"Capacidades estándar para la pieza: **{p_sel}**")
        st.table(df_s[[col_sub_actual, 'PZ X H']])
    else:
        st.warning("Selecciona una pieza para ver sus capacidades.")

st.markdown("</div>", unsafe_allow_html=True)

# --- SECCIÓN: VISIÓN ESTADÍSTICA FINAL (SIN TOPES + BARRAS) ---
st.divider()
st.markdown("### 📊 DESEMPEÑO POR RANGO DE FECHAS")

c_r1, c_r2 = st.columns(2)
with c_r1:
    f_ini_stat = st.date_input("📅 Desde:", datetime.now(), key="vfinal_ini")
with c_r2:
    f_fin_stat = st.date_input("📅 Hasta:", datetime.now(), key="vfinal_fin")

if not df_auditorias.empty and not df_programa.empty and not df_bdd_raw.empty:
    # 1. Normalizar Auditoría
    df_a_v = df_auditorias.copy()
    df_a_v.columns = [c.upper() for c in df_a_v.columns]
    df_a_v['REAL'] = pd.to_numeric(df_a_v['REAL'], errors='coerce').fillna(0)

    # 2. Programa en el rango
    df_p_v = df_programa.copy()
    df_p_v.columns = [c.upper() for c in df_p_v.columns]
    df_p_v['FECHA_DT'] = pd.to_datetime(df_p_v['FECHA'], format='%d/%m/%Y', errors='coerce')
    df_p_v = df_p_v[(df_p_v['FECHA_DT'].dt.date >= f_ini_stat) & (df_p_v['FECHA_DT'].dt.date <= f_fin_stat)]

    # 3. Filtro Moldeo (Tu lógica)
    mask_m = (df_p_v['ÁREA'].str.upper() == "MOLDEO")
    df_p_m = df_p_v[mask_m & df_p_v['PIEZA'].str.contains('GENERAL|VACIADO|ADOBES', case=False, na=False)]
    df_p_o = df_p_v[~mask_m]
    df_p_final = pd.concat([df_p_m, df_p_o])

    # 4. Máximo Real de Auditoría
    df_max_a = df_a_v.groupby(['FECHA', 'PIEZA', 'SUBPROCESO'])['REAL'].max().reset_index()

    # 5. BDD Subprocesos
    df_bdd_v = df_bdd_raw.copy()
    df_bdd_v.columns = [c.upper() for c in df_bdd_v.columns]
    col_sub_v = next((c for c in df_bdd_v.columns if 'SUB' in c and 'CESO' in c), 'SUB PRO CESO')

    # 6. Cruce Maestro
    df_base_v = pd.merge(df_p_final[['FECHA', 'ÁREA', 'PIEZA', 'TOTAL']], 
                         df_bdd_v[['PIEZA', col_sub_v, 'PROCESO']], on='PIEZA')
    df_base_v = df_base_v[df_base_v['ÁREA'] == df_base_v['PROCESO']]
    
    df_unificado_v = pd.merge(df_base_v, df_max_a, 
                              left_on=['FECHA', 'PIEZA', col_sub_v], 
                              right_on=['FECHA', 'PIEZA', 'SUBPROCESO'], 
                              how='left').fillna(0)

    # 7. CÁLCULO SIN TOPE (Para que coincida con tu promedio manual)
    df_unificado_v['TOTAL'] = pd.to_numeric(df_unificado_v['TOTAL'], errors='coerce').fillna(0)
    # Eliminamos el .clip(upper=100)
    df_unificado_v['% REAL'] = (df_unificado_v['REAL'] / df_unificado_v['TOTAL'] * 100).fillna(0)

    # 8. RENDERIZADO CON BARRAS
    if not df_unificado_v.empty:
        res_final = df_unificado_v.groupby('ÁREA')['% REAL'].mean().reset_index()

        for _, row in res_final.iterrows():
            area_n, val_n = row['ÁREA'], round(row['% REAL'], 1)
            color_n = obtener_color_nsg(val_n)
            
            # Ajuste visual: la barra no puede pasar de 100% en ancho de pantalla
            ancho_barra = min(val_n, 100) 

            st.markdown(f"""
                <div style='background:white; padding:18px; border-radius:15px; border-left:8px solid {color_n}; margin-bottom:15px; box-shadow: 0 4px 10px rgba(0,0,0,0.06);'>
                    <div style='display:flex; justify-content:space-between; align-items:center; margin-bottom: 8px;'>
                        <span style='font-weight:bold; font-size:16px;'>{area_n}</span>
                        <span style='color:{color_n}; font-weight:800; font-size:24px;'>{val_n}%</span>
                    </div>
                    <div style='background:#e9ecef; height:14px; border-radius:10px; overflow:hidden;'>
                        <div style='background:{color_n}; width:{ancho_barra}%; height:100%; border-radius:10px;'></div>
                    </div>
                </div>
            """, unsafe_allow_html=True)
