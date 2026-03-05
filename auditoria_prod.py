import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import plotly.graph_objects as go
import time
import os
import pytz

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
        # Normalizamos los datos: quitar espacios extras
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
        color: #E32B13; font-weight: bold; border-bottom: 2px solid #E32B13;
        margin-bottom: 15px; font-size: 20px;
    }
    </style>
    """, unsafe_allow_html=True)

# Inicializar sesión para evitar errores en VS Code
if 'form_id' not in st.session_state: st.session_state.form_id = 0

# Carga de datos inicial
df_programa = leer_datos_seguro("PROGRAMA", 1) #
df_bdd_raw = leer_datos_seguro("BDD", 0)       #
df_auditorias = leer_datos_seguro("AUDITAR", 0)

# Filtrar BDD por Estatus Activo (Columna E)
if not df_bdd_raw.empty:
    df_bdd_raw = df_bdd_raw[df_bdd_raw['COL_4'].str.upper() == 'TRUE'].copy()

# --- 3. SIDEBAR DINÁMICO ---
with st.sidebar:
    if os.path.exists(LOGO_FILENAME): st.image(LOGO_FILENAME, use_container_width=True)
    st.divider()
    fecha_dt = st.date_input("📅 Fecha", datetime.now())
    fecha_sel = fecha_dt.strftime('%d/%m/%Y')
    
    # Obtener áreas directamente del Excel
    if not df_programa.empty and 'ÁREA' in df_programa.columns:
        lista_areas = [a for a in df_programa['ÁREA'].unique().tolist() if a and a.upper() != "ÁREA"]
    else:
        lista_areas = ["MOLDEO", "CORAZONES", "CORTE", "ENSAMBLE"]
    
    area_sel = st.selectbox("📍 Área", lista_areas)
    cortes_dict = {"11:00 AM (3h)": 3, "14:00 PM (6h)": 6, "17:00 PM (9h)": 9}
    corte_sel = st.selectbox("⏱️ Corte", list(cortes_dict.keys()))
    horas_acum = cortes_dict[corte_sel]
    
    st.divider()
    st.subheader("📋 Plan del Día")
    df_plan_dia = pd.DataFrame()
    if not df_programa.empty:
        # Filtramos por fecha y área
        df_plan_dia = df_programa[(df_programa['FECHA'] == fecha_sel) & (df_programa['ÁREA'] == area_sel)].copy()
        
        # Filtro especial para MOLDEO
        if area_sel.upper() == "MOLDEO" and not df_plan_dia.empty:
            keywords = ["GENERAL", "VACIADO", "ADOBES"]
            df_moldeo = df_plan_dia[df_plan_dia['PIEZA'].str.contains('|'.join(keywords), case=False, na=False)]
            if not df_moldeo.empty: df_plan_dia = df_moldeo
        
        if not df_plan_dia.empty:
            st.dataframe(df_plan_dia[['PIEZA', 'TOTAL']], hide_index=True)

# --- 4. PANEL CENTRAL (CÁLCULO DE AVANCE REAL) ---
# --- 4. PANEL CENTRAL (CÁLCULO Y GRÁFICA COMPARATIVA) ---
st.markdown(f"## 🛡️ Panel de Auditoría: {area_sel}")

avance_global = 0.0
df_resumen_final = pd.DataFrame()

if not df_plan_dia.empty and not df_bdd_raw.empty:
    # 1. Mapear subprocesos de la BDD
    df_sub_base_total = df_bdd_raw[df_bdd_raw['PROCESO'] == area_sel][['PIEZA', 'SUB PROCESO']].copy()
    
    # 2. Cruzar con el Plan del Día
    piezas_en_plan = df_plan_dia['PIEZA'].unique()
    df_base = df_sub_base_total[df_sub_base_total['PIEZA'].isin(piezas_en_plan)].copy()
    
    if not df_base.empty:
        df_base = pd.merge(df_base, df_plan_dia[['PIEZA', 'TOTAL']], on='PIEZA', how='left')
        df_base['TOTAL'] = pd.to_numeric(df_base['TOTAL'], errors='coerce').fillna(0)

        # 3. Cruzar con lo auditado
        if not df_auditorias.empty:
            df_aud_hoy = df_auditorias[(df_auditorias['FECHA'] == fecha_sel) & (df_auditorias['ÁREA'] == area_sel)].copy()
            df_aud_hoy['REAL'] = pd.to_numeric(df_aud_hoy['REAL'], errors='coerce').fillna(0)
            df_max_real = df_aud_hoy.groupby(['PIEZA', 'SUBPROCESO'])['REAL'].max().reset_index()
            df_final = pd.merge(df_base, df_max_real, left_on=['PIEZA', 'SUB PROCESO'], right_on=['PIEZA', 'SUBPROCESO'], how='left').fillna(0)
        else:
            df_final = df_base.copy()
            df_final['REAL'] = 0

        # 4. Cálculo de %
        df_final['% REAL'] = (df_final['REAL'] / df_final['TOTAL'] * 100).clip(upper=100).fillna(0)
        
        df_resumen_final = df_final[['PIEZA', 'SUB PROCESO', 'TOTAL', 'REAL', '% REAL']].copy()
        df_resumen_final.columns = ['PIEZA', 'SUBPROCESO', 'PROGRAMADO', 'AVANCE', '% REAL']
        avance_global = round(df_final['% REAL'].mean(), 1)

# Visualización del Gauge y Gráfica Nueva
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
        # --- GRÁFICA COMPARATIVA REAL VS META ---
        fig_bar = go.Figure()
        
        # Barra de Meta (Caja de contorno azul)
        fig_bar.add_trace(go.Bar(
            name='Meta Programa',
            x=df_resumen_final['SUBPROCESO'],
            y=df_resumen_final['PROGRAMADO'],
            marker=dict(
                color='rgba(52, 152, 219, 0.1)', # Azul muy bajito
                line=dict(color='#2980b9', width=2) # Borde azul fuerte
            )
        ))
        
        # Barra de Avance (Rojo NSG)
        fig_bar.add_trace(go.Bar(
            name='Avance Real',
            x=df_resumen_final['SUBPROCESO'],
            y=df_resumen_final['AVANCE'],
            marker_color='#E32B13'
        ))

        fig_bar.update_layout(
            barmode='group', 
            height=280, 
            margin=dict(l=10, r=10, t=10, b=10),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)'
        )
        st.plotly_chart(fig_bar, use_container_width=True)
    else:
        st.info("No hay datos para graficar en este momento.")
# --- 5. REGISTRO ---
st.divider()
st.markdown("<div class='step-header'>🚀 CAPTURA DE AUDITORÍA</div>", unsafe_allow_html=True)
col_a, col_b = st.columns(2)

# Definir variables vacías para que VS Code no marque error
sub_sel = None
df_sub_base = pd.DataFrame()
f_id = st.session_state.form_id

with col_a:
    st.markdown("##### **Paso 1: Selección**")
    piezas_opciones = df_plan_dia['PIEZA'].unique().tolist() if not df_plan_dia.empty else []
    pieza_sel = st.selectbox("Seleccione Pieza", piezas_opciones)
    
    # Filtro de BDD para el selector de subprocesos
    df_sub_base = df_bdd_raw[(df_bdd_raw['PIEZA'] == pieza_sel) & (df_bdd_raw['PROCESO'] == area_sel)].copy()
    
    if not df_sub_base.empty:
        # Evitar duplicados en el mismo corte
        reportados = df_auditorias[(df_auditorias['FECHA'] == fecha_sel) & (df_auditorias['CORTE'] == corte_sel) & (df_auditorias['PIEZA'] == pieza_sel)]['SUBPROCESO'].tolist() if not df_auditorias.empty else []
        opciones = [s for s in df_sub_base['SUB PROCESO'].tolist() if s not in reportados]
        sub_sel = st.selectbox("Sub-proceso", opciones) if opciones else None
        if not opciones: st.success("✅ Proceso completado para este corte.")
    else:
        st.warning("⚠️ No se encontraron subprocesos en BDD para esta pieza.")

with col_b:
    st.markdown("##### **Paso 2: Condiciones**")
    num_ops = st.number_input("Operadores", min_value=1, value=1, key=f"ops_{f_id}")
    minutos_p = st.number_input("Min. Paro", min_value=0, key=f"min_{f_id}")
    motivo_p = st.selectbox("Motivo de Paro", MOTIVOS_PARO, key=f"mot_{f_id}")

if sub_sel:
    st.markdown("##### **Paso 3: Cantidades**")
    cc1, cc2, cc3 = st.columns([1.5, 1, 1])
    with cc1:
        real_in = st.number_input("CANTIDAD REAL ACUMULADA", min_value=0, key=f"real_{f_id}")
        notas_aud = st.text_input("Observaciones", key=f"note_{f_id}")
    with cc2:
        cap_row = df_sub_base[df_sub_base['SUB PROCESO'] == sub_sel]
        pz_h_p = float(cap_row['PZ X H'].iloc[0]) if not cap_row.empty else 0
        tiempo_ef = max(0, horas_acum - (minutos_p/60))
        meta_e = int((pz_h_p * tiempo_ef) * num_ops)
        dif = real_in - meta_e
        st.metric("Meta Teórica", f"{meta_e} pzs")
        st.metric("Diferencia", f"{dif} pzs", delta=dif)
    with cc3:
        st.write("")
        if st.button("💾 GUARDAR REGISTRO"):
            try:
                zona_mx = pytz.timezone('America/Mexico_City')
                hora_mx = datetime.now(zona_mx).strftime('%H:%M:%S')
                fila = [fecha_sel, area_sel, corte_sel, pieza_sel, sub_sel, int(real_in), meta_e, dif, int(num_ops), f"[{motivo_p}-{minutos_p}min] {notas_aud}", hora_mx]
                conectar_libro().worksheet("AUDITAR").append_row(fila)
                st.toast("✅ ¡Guardado!", icon="🚀")
                st.cache_data.clear()
                st.session_state.form_id += 1 
                time.sleep(0.5)
                st.rerun()
            except Exception as e: st.error(f"Error: {e}")

# --- 6. TABLAS FINALES (CON DESGLOSE) ---
st.divider()
c_t1, c_t2 = st.columns([0.7, 1.3])

with c_t1:
    st.markdown("##### 📖 Capacidades (PZ x Hora)")
    if not df_sub_base.empty:
        st.table(df_sub_base[['SUB PROCESO', 'PZ X H']])

with c_t2:
    st.markdown("##### 📊 Avance Detallado (Pieza + Subproceso)")
    if not df_resumen_final.empty:
        # Aplicar formato de semáforo manual para que se vea profesional
        def color_semaforo(val):
            color = "#2ecc71" if val >= 95 else "#e67e22" if val >= 80 else "#E32B13"
            return f"<b style='color: {color};'>{val:,.1f}%</b>"
        
        df_visual = df_resumen_final.copy()
        df_visual['% REAL'] = df_visual['% REAL'].apply(color_semaforo)
        st.write(df_visual.to_html(escape=False, index=False), unsafe_allow_html=True)
    else:
        st.info("Aún no hay registros para mostrar.")
