import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import plotly.graph_objects as go
import time
import os

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

# --- 2. INTERFAZ Y ESTILO ---
st.set_page_config(layout="wide", page_title="NSG Auditoría", page_icon="🛡️")

st.markdown("""
    <style>
    .stApp { background-color: #ffffff; }
    .stButton>button {
        background-color: #E32B13 !important;
        color: white !important;
        border-radius: 8px !important;
        font-weight: bold !important;
    }
    .step-header {
        color: #E32B13;
        font-weight: bold;
        border-bottom: 2px solid #E32B13;
        margin-bottom: 15px;
        font-size: 20px;
    }
    </style>
    """, unsafe_allow_html=True)

if 'form_id' not in st.session_state:
    st.session_state.form_id = 0

df_programa = leer_datos_seguro("PROGRAMA", 1)
df_bdd_raw = leer_datos_seguro("BDD", 0)
df_auditorias = leer_datos_seguro("AUDITAR", 0)

if not df_bdd_raw.empty:
    col_estatus = df_bdd_raw.columns[4]
    df_bdd_raw = df_bdd_raw[df_bdd_raw[col_estatus].str.upper() == 'TRUE'].copy()

# --- 3. SIDEBAR ---
with st.sidebar:
    if os.path.exists(LOGO_FILENAME):
        st.image(LOGO_FILENAME, use_container_width=True)
    
    st.divider()
    st.markdown("### ⚙️ Ajustes de Turno")
    fecha_dt = st.date_input("📅 Fecha", datetime.now(), help="Día de producción.")
    fecha_sel = fecha_dt.strftime('%d/%m/%Y')
    
    if not df_programa.empty and 'ÁREA' in df_programa.columns:
        lista_areas = [a for a in df_programa['ÁREA'].unique().tolist() if a and a.upper() != "ÁREA"]
    else:
        lista_areas = ["MOLDEO", "ENSAMBLE", "ACABADO"]
    
    area_sel = st.selectbox("📍 Área", lista_areas, help="Departamento a auditar.")
    cortes_dict = {"11:00 AM (3h)": 3, "14:00 PM (6h)": 6, "17:00 PM (9h)": 9}
    corte_sel = st.selectbox("⏱️ Corte", list(cortes_dict.keys()), help="Corte de tiempo a reportar.")
    horas_acum = cortes_dict[corte_sel]
    
    st.divider()
    st.subheader("📋 Plan del Día")
    df_plan_dia = pd.DataFrame()
    if not df_programa.empty:
        df_plan_dia = df_programa[(df_programa['FECHA'] == fecha_sel) & (df_programa['ÁREA'] == area_sel)].copy()
        if area_sel.upper() == "MOLDEO" and not df_plan_dia.empty:
            keywords = ["GENERAL", "VACIADO", "ADOBES"]
            df_moldeo = df_plan_dia[df_plan_dia['PIEZA'].str.contains('|'.join(keywords), case=False, na=False)]
            if not df_moldeo.empty: df_plan_dia = df_moldeo
        if not df_plan_dia.empty:
            st.dataframe(df_plan_dia[['PIEZA', 'TOTAL']], hide_index=True)

# --- 4. PANEL CENTRAL ---
st.markdown(f"## 🛡️ Panel de Auditoría: {area_sel}")

avance_global = 0
df_resumen_final = pd.DataFrame()

if not df_plan_dia.empty and not df_auditorias.empty:
    df_aud_hoy = df_auditorias[(df_auditorias['FECHA'] == fecha_sel) & (df_auditorias['ÁREA'] == area_sel)].copy()
    if not df_aud_hoy.empty:
        df_aud_hoy['REAL'] = pd.to_numeric(df_aud_hoy['REAL'], errors='coerce').fillna(0)
        df_max_real = df_aud_hoy.groupby('PIEZA')['REAL'].max().reset_index()
        df_metas = df_plan_dia[['PIEZA', 'TOTAL']].copy()
        df_metas['TOTAL'] = pd.to_numeric(df_metas['TOTAL'], errors='coerce').fillna(1)
        df_final = pd.merge(df_metas, df_max_real, on='PIEZA', how='left').fillna(0)
        df_final['% AVANCE REAL'] = (df_final['REAL'] / df_final['TOTAL'] * 100).clip(upper=100)
        df_resumen_final = df_final[['PIEZA', '% AVANCE REAL']]
        avance_global = round(df_final['% AVANCE REAL'].mean(), 1)

c_g, c_b = st.columns([1, 2])
with c_g:
    fig = go.Figure(go.Indicator(
        mode="gauge+number", value=avance_global,
        gauge={'bar':{'color':"#2ecc71"}, 'axis': {'range': [0, 100]}, 'bgcolor': "#f0f2f6"}
    ))
    fig.update_layout(height=240, margin=dict(l=20, r=20, t=20, b=20))
    st.plotly_chart(fig, use_container_width=True)
with c_b:
    if not df_resumen_final.empty:
        st.bar_chart(df_resumen_final, x="PIEZA", y="% AVANCE REAL", color="#E32B13")

# --- 5. REGISTRO ---
st.divider()
st.markdown("<div class='step-header'>🚀 CAPTURA DE AUDITORÍA</div>", unsafe_allow_html=True)
col_a, col_b = st.columns(2)
with col_a:
    st.markdown("##### **Paso 1: Selección**")
    piezas_finales = df_plan_dia['PIEZA'].unique().tolist() if not df_plan_dia.empty else (df_bdd_raw['PIEZA'].unique().tolist() if not df_bdd_raw.empty else [])
    pieza_sel = st.selectbox("Seleccione Pieza", piezas_finales, help="Producto auditado.")
    df_sub_base = df_bdd_raw[(df_bdd_raw['PIEZA'] == pieza_sel) & (df_bdd_raw['PROCESO'] == area_sel)].copy()
    if not df_sub_base.empty:
        reportados = df_auditorias[(df_auditorias['FECHA'] == fecha_sel) & (df_auditorias['CORTE'] == corte_sel) & (df_auditorias['PIEZA'] == pieza_sel)]['SUBPROCESO'].tolist() if not df_auditorias.empty else []
        opciones = [s for s in df_sub_base['SUB PROCESO'].tolist() if s not in reportados]
        sub_sel = st.selectbox("Sub-proceso", opciones, help="Fase de la pieza.") if opciones else None
        if not opciones: st.success("✅ Proceso completado.")
    else: sub_sel = None

with col_b:
    st.markdown("##### **Paso 2: Condiciones**")
    f_id = st.session_state.form_id
    c_op, c_pa = st.columns(2)
    num_ops = c_op.number_input("Operadores", min_value=1, value=1, key=f"ops_{f_id}", help="Cantidad de personas operando en este sub-proceso.")
    minutos_p = c_pa.number_input("Min. Paro", min_value=0, key=f"min_{f_id}", help="Tiempo total que la máquina o proceso estuvo detenido.")
    # AYUDA AGREGADA AQUÍ
    motivo_p = st.selectbox("Motivo de Paro", MOTIVOS_PARO, key=f"mot_{f_id}", help="Selecciona la causa principal de la detención en este corte.")

if sub_sel:
    st.markdown("##### **Paso 3: Cantidades**")
    cc1, cc2, cc3 = st.columns([1.5, 1, 1])
    with cc1:
        real_in = st.number_input("CANTIDAD REAL ACUMULADA", min_value=0, key=f"real_{f_id}", help="Ingrese el total de piezas buenas acumuladas al momento del corte.")
        # AYUDA AGREGADA AQUÍ
        notas_aud = st.text_input("Observaciones", key=f"note_{f_id}", placeholder="Notas técnicas...", help="Agrega detalles adicionales o justifica desviaciones importantes.")
    with cc2:
        cap_row = df_sub_base[df_sub_base['SUB PROCESO'] == sub_sel]
        pz_h_p = float(cap_row['PZ X H'].iloc[0]) if not cap_row.empty else 0
        h_c = horas_acum if 'horas_acum' in locals() else 3
        tiempo_ef = max(0, h_c - (minutos_p/60))
        meta_e = int((pz_h_p * tiempo_ef) * num_ops)
        dif = real_in - meta_e
        st.metric("Meta Teórica", f"{meta_e} pzs")
        st.metric("Diferencia", f"{dif} pzs", delta=dif)
    with cc3:
        st.write("")
        if st.button("💾 GUARDAR REGISTRO"):
            try:
                with st.spinner("Transmitiendo..."):
                    libro_actual = conectar_libro()
                    fila = [fecha_sel, area_sel, corte_sel, pieza_sel, sub_sel, int(real_in), meta_e, dif, int(num_ops), f"[{motivo_p}-{minutos_p}min] {notas_aud}", datetime.now().strftime('%H:%M:%S')]
                    libro_actual.worksheet("AUDITAR").append_row(fila)
                
                st.toast(f"✅ ¡Guardado!", icon="🚀")
                st.cache_data.clear()
                st.session_state.form_id += 1 
                time.sleep(0.5)
                st.rerun()
            except Exception as e: st.error(f"Error de red: {e}")

# --- 6. TABLAS FINALES ---
st.divider()
c_t1, c_t2 = st.columns(2)
with c_t1:
    st.markdown("##### 📖 Capacidades (PZ x Hora)")
    if 'df_sub_base' in locals() and not df_sub_base.empty:
        st.table(df_sub_base[['SUB PROCESO', 'PZ X H']])
with c_t2:
    st.markdown("##### 📊 Avance de Auditoría Real")
    if not df_resumen_final.empty:
        st.dataframe(df_resumen_final, hide_index=True, use_container_width=True)

