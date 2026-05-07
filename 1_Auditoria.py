import os
import re
import time
import unicodedata
from datetime import datetime, timedelta

import gspread
import pandas as pd
import plotly.graph_objects as go
import pytz
import streamlit as st
from gspread.exceptions import APIError
from oauth2client.service_account import ServiceAccountCredentials

# --- 1. CONFIGURACIÓN TÉCNICA ---
JSON_FILE = "creds_nsg.json"
ID_LIBRO = "13ZF5TXwgEZSlrODQFF43Rvs4JmB19s6V0KNV1l72RHA"
SCOPE = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]
LOGO_FILENAME = "LOGO NSG SFONDO.png"
TIMEZONE = "America/Mexico_City"
CORTES_DICT = {"11:00 AM (3h)": 3, "14:00 PM (6h)": 6, "17:00 PM (9h)": 9}
AREAS_DEFAULT = ["MOLDEO", "CORAZONES", "CORTE", "ENSAMBLE"]
PIEZA_TERMINADA = "PIEZA TERMINADA"

MOTIVOS_PARO = [
    "SIN PARO",
    "FALLA MECANICA",
    "FALLA ELECTRICA",
    "FALTA DE MATERIAL",
    "CAMBIO DE MODELO / SET-UP",
    "AUSENCIA DE OPERADOR",
    "JUNTA DE CALIDAD / SEGURIDAD",
    "LIMPIEZA / 5S",
    "OTRO (ESPECIFICAR EN NOTAS)",
]


def ahora_local():
    return datetime.now(pytz.timezone(TIMEZONE))


def normalizar_clave(texto):
    texto = "" if texto is None else str(texto).strip().upper()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return re.sub(r"[^A-Z0-9]", "", texto)


def obtener_version_hoja(nombre_hoja):
    return st.session_state.get(f"version_hoja_{nombre_hoja}", 0)


def invalidar_cache_hoja(nombre_hoja):
    clave = f"version_hoja_{nombre_hoja}"
    st.session_state[clave] = st.session_state.get(clave, 0) + 1


def encontrar_columna(df, aliases, contiene_todos=None):
    if df.empty:
        return None

    aliases_norm = {normalizar_clave(alias) for alias in aliases}
    for col in df.columns:
        if normalizar_clave(col) in aliases_norm:
            return col

    if contiene_todos:
        tokens = [normalizar_clave(token) for token in contiene_todos]
        for col in df.columns:
            clave = normalizar_clave(col)
            if all(token in clave for token in tokens):
                return col

    return None


def preparar_dataframe(nombre_hoja, fila_encabezado=0):
    df = leer_datos_seguro(
        nombre_hoja, fila_encabezado, obtener_version_hoja(nombre_hoja)
    )
    columnas = {
        "pieza": encontrar_columna(df, ["PIEZA"]),
        "area": encontrar_columna(df, ["AREA", "PROCESO", "ÁREA"]),
        "proceso": encontrar_columna(df, ["PROCESO", "AREA", "ÁREA"]),
        "subproceso": encontrar_columna(
            df,
            ["SUBPROCESO", "SUB PROCESO", "SUB_PROCESO", "SUB PRO CESO"],
            contiene_todos=["SUB", "CESO"],
        ),
        "activo": encontrar_columna(df, ["ACTIVO", "ESTATUS", "COL_4"]),
        "total": encontrar_columna(df, ["TOTAL"]),
        "real": encontrar_columna(df, ["REAL"]),
        "fecha": encontrar_columna(df, ["FECHA"]),
        "corte": encontrar_columna(df, ["CORTE"]),
        "pzxh": encontrar_columna(df, ["PZ X H", "PZXH", "PZS X H", "PZS/H"]),
    }
    return df, columnas


def validar_columnas(columnas, requeridas):
    faltantes = [nombre for nombre in requeridas if not columnas.get(nombre)]
    return faltantes


def convertir_serie_numerica(serie):
    serie_limpia = (
        serie.astype(str)
        .str.strip()
        .str.replace(",", "", regex=False)
        .str.replace(r"[^\d\.\-]", "", regex=True)
    )
    return pd.to_numeric(serie_limpia, errors="coerce")


@st.cache_resource
def obtener_cliente():
    try:
        creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_FILE, SCOPE)
        return gspread.authorize(creds)
    except Exception as exc:
        st.error(f"No se pudo autorizar Google Sheets: {exc}")
        return None


def conectar_libro():
    cliente = obtener_cliente()
    if not cliente:
        return None
    try:
        # Intentar abrir el libro
        return cliente.open_by_key(ID_LIBRO)
    except Exception as e:
        if "429" in str(e):
            # En lugar de solo time.sleep, notificamos al usuario sin bloquear todo el hilo
            st.warning(
                "⚠️ Google Sheets alcanzó su límite de lectura. La App usará datos en caché."
            )
        return None


@st.cache_data(ttl=600)
def leer_datos_seguro(nombre_hoja, fila_encabezado=0, version=0):
    try:
        libro = conectar_libro()
        if not libro:
            return pd.DataFrame()

        hoja = libro.worksheet(nombre_hoja)
        datos = leer_hoja_con_reintentos(hoja)
        if len(datos) <= fila_encabezado:
            return pd.DataFrame()

        nombres = datos[fila_encabezado]
        df = pd.DataFrame(datos[fila_encabezado + 1 :])
        df.columns = [
            str(nombre).strip().upper() if nombre else f"COL_{indice}"
            for indice, nombre in enumerate(nombres)
        ]
        for col in df.columns:
            df[col] = df[col].astype(str).str.strip()
        return df
    except Exception as exc:
        st.error(f"No se pudo leer la hoja '{nombre_hoja}': {exc}")
        return pd.DataFrame()


def es_error_cuota(exc):
    mensaje = str(exc)
    return "429" in mensaje or "Read requests per minute per user" in mensaje


def leer_hoja_con_reintentos(hoja, max_intentos=4, pausa_inicial=1.5):
    ultimo_error = None

    for intento in range(max_intentos):
        try:
            return hoja.get_all_values()
        except APIError as exc:
            ultimo_error = exc
            if not es_error_cuota(exc) or intento == max_intentos - 1:
                raise
            time.sleep(pausa_inicial * (2**intento))
        except Exception as exc:
            ultimo_error = exc
            if not es_error_cuota(exc) or intento == max_intentos - 1:
                raise
            time.sleep(pausa_inicial * (2**intento))

    if ultimo_error:
        raise ultimo_error

    return []


def obtener_color_nsg(valor):
    if valor >= 85:
        return "#2ecc71"
    if valor >= 80:
        return "#f1c40f"
    if valor >= 70:
        return "#e67e22"
    return "#E32B13"


def filtrar_bdd_activa(df_bdd, columnas_bdd):
    if df_bdd.empty:
        return df_bdd

    col_sub = columnas_bdd["subproceso"]
    col_activo = columnas_bdd["activo"]
    if not col_sub:
        return pd.DataFrame()

    df_filtrado = df_bdd.copy()
    if col_activo:
        df_filtrado = df_filtrado[
            df_filtrado[col_activo].astype(str).str.upper().str.strip() == "TRUE"
        ].copy()

    df_filtrado = df_filtrado[df_filtrado[col_sub].str.len() > 1].copy()
    df_filtrado = df_filtrado[df_filtrado[col_sub] != "0"].copy()
    return df_filtrado


def obtener_lista_areas(df_programa, columnas_programa):
    col_area = columnas_programa["area"]
    if not df_programa.empty and col_area:
        areas = [
            area
            for area in df_programa[col_area].unique().tolist()
            if area and normalizar_clave(area) != "AREA"
        ]
        if areas:
            return areas
    return AREAS_DEFAULT


def sugerir_corte_actual():
    hora_actual = ahora_local().time()
    if hora_actual < datetime.strptime("11:30", "%H:%M").time():
        return 0
    if hora_actual < datetime.strptime("14:30", "%H:%M").time():
        return 1
    return 2


def obtener_plan_del_dia(df_programa, columnas_programa, fecha_sel, area_sel):
    requeridas = validar_columnas(
        columnas_programa, ["fecha", "area", "pieza", "total"]
    )
    if df_programa.empty or requeridas:
        return pd.DataFrame()

    df_plan = df_programa[
        (df_programa[columnas_programa["fecha"]] == fecha_sel)
        & (df_programa[columnas_programa["area"]] == area_sel)
    ].copy()

    if area_sel.upper() == "MOLDEO" and not df_plan.empty:
        df_plan = df_plan[
            df_plan[columnas_programa["pieza"]].str.contains(
                "GENERAL|VACIADO|ADOBES", case=False, na=False
            )
        ]

    df_plan[columnas_programa["total"]] = convertir_serie_numerica(
        df_plan[columnas_programa["total"]]
    )
    df_plan = df_plan[df_plan[columnas_programa["total"]] > 0].copy()

    return df_plan


def calcular_resumen(
    df_plan_dia, col_prog, df_bdd, col_bdd, df_auditorias, fecha_sel, area_sel
):
    avance_global = 0.0
    df_resumen = pd.DataFrame()

    requeridas_plan = validar_columnas(col_prog, ["pieza", "total"])
    requeridas_bdd = validar_columnas(col_bdd, ["pieza", "proceso", "subproceso"])
    if df_plan_dia.empty or df_bdd.empty or requeridas_plan or requeridas_bdd:
        return avance_global, df_resumen

    df_sub_base_total = df_bdd[df_bdd[col_bdd["proceso"]] == area_sel][
        [col_bdd["pieza"], col_bdd["subproceso"]]
    ].copy()
    piezas_en_plan = df_plan_dia[col_prog["pieza"]].unique()
    df_base = df_sub_base_total[
        df_sub_base_total[col_bdd["pieza"]].isin(piezas_en_plan)
    ].copy()

    if df_base.empty:
        return avance_global, df_resumen

    df_base = pd.merge(
        df_base,
        df_plan_dia[[col_prog["pieza"], col_prog["total"]]],
        left_on=col_bdd["pieza"],
        right_on=col_prog["pieza"],
        how="left",
    )
    df_base[col_prog["total"]] = convertir_serie_numerica(
        df_base[col_prog["total"]]
    ).fillna(0)

    if not df_auditorias.empty:
        col_aud = {
            "fecha": encontrar_columna(df_auditorias, ["FECHA"]),
            "area": encontrar_columna(df_auditorias, ["AREA", "ÁREA"]),
            "pieza": encontrar_columna(df_auditorias, ["PIEZA"]),
            "subproceso": encontrar_columna(
                df_auditorias,
                ["SUBPROCESO", "SUB PROCESO", "SUB_PROCESO"],
                contiene_todos=["SUB", "CESO"],
            ),
            "real": encontrar_columna(df_auditorias, ["REAL"]),
        }
        if not validar_columnas(
            col_aud, ["fecha", "area", "pieza", "subproceso", "real"]
        ):
            df_aud_hoy = df_auditorias[
                (df_auditorias[col_aud["fecha"]] == fecha_sel)
                & (df_auditorias[col_aud["area"]] == area_sel)
            ].copy()
            df_aud_hoy[col_aud["real"]] = pd.to_numeric(
                df_aud_hoy[col_aud["real"]], errors="coerce"
            ).fillna(0)
            df_max_real = (
                df_aud_hoy.groupby([col_aud["pieza"], col_aud["subproceso"]])[
                    col_aud["real"]
                ]
                .max()
                .reset_index()
            )
            df_final = pd.merge(
                df_base,
                df_max_real,
                left_on=[col_bdd["pieza"], col_bdd["subproceso"]],
                right_on=[col_aud["pieza"], col_aud["subproceso"]],
                how="left",
            ).fillna(0)
        else:
            df_final = df_base.assign(REAL=0)
            col_aud = {"real": "REAL"}
    else:
        df_final = df_base.assign(REAL=0)
        col_aud = {"real": "REAL"}

    total_seguro = convertir_serie_numerica(df_final[col_prog["total"]]).fillna(0)
    real_seguro = convertir_serie_numerica(df_final[col_aud["real"]]).fillna(0)
    df_final["% REAL"] = 0.0
    mask_total_valido = total_seguro > 0
    df_final.loc[mask_total_valido, "% REAL"] = (
        real_seguro[mask_total_valido] / total_seguro[mask_total_valido] * 100
    )

    df_resumen = df_final[
        [
            col_bdd["pieza"],
            col_bdd["subproceso"],
            col_prog["total"],
            col_aud["real"],
            "% REAL",
        ]
    ].copy()
    df_resumen.columns = ["PIEZA", "SUBPROCESO", "PROGRAMADO", "AVANCE", "% REAL"]
    avance_global = round(df_final["% REAL"].mean(), 1)
    return avance_global, df_resumen


def obtener_auditorias_hoy(df_auditorias, fecha_sel, area_sel):
    if df_auditorias.empty:
        return pd.DataFrame(), {}

    columnas = {
        "fecha": encontrar_columna(df_auditorias, ["FECHA"]),
        "area": encontrar_columna(df_auditorias, ["AREA", "ÁREA"]),
        "corte": encontrar_columna(df_auditorias, ["CORTE"]),
        "pieza": encontrar_columna(df_auditorias, ["PIEZA"]),
        "subproceso": encontrar_columna(
            df_auditorias,
            ["SUBPROCESO", "SUB PROCESO", "SUB_PROCESO"],
            contiene_todos=["SUB", "CESO"],
        ),
        "real": encontrar_columna(df_auditorias, ["REAL"]),
    }
    faltantes = validar_columnas(columnas, ["fecha", "area", "pieza", "subproceso"])
    if faltantes:
        return pd.DataFrame(), columnas

    df_hoy = df_auditorias[
        (df_auditorias[columnas["fecha"]] == fecha_sel)
        & (df_auditorias[columnas["area"]] == area_sel)
    ].copy()
    return df_hoy, columnas


def obtener_piezas_pendientes(
    df_plan_dia, col_prog, df_bdd, col_bdd, df_aud_hoy, col_aud, area_sel, corte_sel
):
    if df_plan_dia.empty or df_bdd.empty:
        return []

    faltantes_plan = validar_columnas(col_prog, ["pieza"])
    faltantes_bdd = validar_columnas(col_bdd, ["pieza", "proceso", "subproceso"])
    if faltantes_plan or faltantes_bdd:
        return []

    piezas_validas = df_plan_dia[col_prog["pieza"]].unique()
    piezas_pendientes = []

    for pieza in piezas_validas:
        mask = (df_bdd[col_bdd["pieza"]] == pieza) & (
            df_bdd[col_bdd["proceso"]] == area_sel
        )
        subs_totales = df_bdd[mask][col_bdd["subproceso"]].unique()

        reps_pieza = []
        if not df_aud_hoy.empty and col_aud.get("corte") and col_aud.get("pieza"):
            reps_pieza = df_aud_hoy[
                (df_aud_hoy[col_aud["corte"]] == corte_sel)
                & (df_aud_hoy[col_aud["pieza"]] == pieza)
            ][col_aud["subproceso"]].tolist()

        if any(sub for sub in subs_totales if sub not in reps_pieza):
            piezas_pendientes.append(pieza)

    return piezas_pendientes


def guardar_registro(
    fecha_sel,
    area_sel,
    corte_sel,
    pieza_sel,
    subproceso_sel,
    real,
    meta,
    ops,
    mot,
    notas,
):
    libro = conectar_libro()
    if not libro:
        st.error("No hay conexión disponible con el libro para guardar.")
        return False

    try:
        hora = ahora_local().strftime("%H:%M:%S")
        fila = [
            fecha_sel,
            area_sel,
            corte_sel,
            pieza_sel,
            subproceso_sel,
            int(real),
            int(meta),
            int(real - meta),
            int(ops),
            f"[{mot}] {notas}",
            hora,
        ]
        libro.worksheet("AUDITAR").append_row(fila)
        return True
    except Exception as exc:
        st.error(f"Error al guardar el registro: {exc}")
        return False


def render_kpis(avance_global, df_resumen_final):
    k1, k2, k3 = st.columns(3)
    with k1:
        st.markdown(
            f"<div class='metric-card'><small>EFICIENCIA GLOBAL</small><h2>{avance_global}%</h2></div>",
            unsafe_allow_html=True,
        )
    with k2:
        meta_turno = (
            int(df_resumen_final["PROGRAMADO"].sum())
            if not df_resumen_final.empty
            else 0
        )
        st.markdown(
            f"<div class='metric-card'><small>META TURNO</small><h2>{meta_turno} PZS</h2></div>",
            unsafe_allow_html=True,
        )
    with k3:
        real_turno = (
            int(df_resumen_final["AVANCE"].sum()) if not df_resumen_final.empty else 0
        )
        st.markdown(
            f"<div class='metric-card'><small>REAL TURNO</small><h2>{real_turno} PZS</h2></div>",
            unsafe_allow_html=True,
        )


def render_graficos(avance_global, df_resumen_final):
    c_g, c_b = st.columns([1, 2])
    with c_g:
        color_g = obtener_color_nsg(avance_global)
        fig = go.Figure(
            go.Indicator(
                mode="gauge+number",
                value=avance_global,
                gauge={
                    "bar": {"color": color_g},
                    "axis": {"range": [0, 100]},
                    "bgcolor": "#f0f2f6",
                    "steps": [
                        {"range": [0, 80], "color": "rgba(227, 43, 19, 0.05)"},
                        {"range": [80, 85], "color": "rgba(241, 196, 15, 0.1)"},
                        {"range": [85, 100], "color": "rgba(46, 204, 113, 0.1)"},
                    ],
                },
            )
        )
        fig.update_layout(height=280, margin=dict(l=30, r=30, t=30, b=10))
        st.plotly_chart(fig, use_container_width=True)

    with c_b:
        if not df_resumen_final.empty:
            fig_bar = go.Figure()
            fig_bar.add_trace(
                go.Bar(
                    name="Meta",
                    x=df_resumen_final["SUBPROCESO"],
                    y=df_resumen_final["PROGRAMADO"],
                    marker_color="#610B0B",
                )
            )
            fig_bar.add_trace(
                go.Bar(
                    name="Real",
                    x=df_resumen_final["SUBPROCESO"],
                    y=df_resumen_final["AVANCE"],
                    marker_color="#E32B13",
                )
            )
            fig_bar.update_layout(
                barmode="group",
                height=280,
                margin=dict(l=0, r=0, t=20, b=0),
                legend=dict(orientation="h", y=1.1),
            )
            st.plotly_chart(fig_bar, use_container_width=True)


def render_estatus_detallado(df_resumen_final):
    st.markdown("### ESTATUS DETALLADO")
    if df_resumen_final.empty:
        st.info("No hay datos suficientes para mostrar el estatus detallado.")
        return

    for _, row in df_resumen_final.iterrows():
        val_n = round(row["% REAL"], 1)
        color_n = obtener_color_nsg(val_n)
        ancho_barra = min(val_n, 100)

        st.markdown(
            f"""
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
            """,
            unsafe_allow_html=True,
        )


def render_capacidades(df_s, col_bdd, pieza_sel):
    st.divider()
    titulo = f"CONSULTAR CAPACIDADES (PZ/H) - PIEZA: {pieza_sel if pieza_sel else 'NO SELECCIONADA'}"
    with st.expander(titulo):
        if not df_s.empty and col_bdd.get("subproceso") and col_bdd.get("pzxh"):
            st.write(f"Capacidades estándar para la pieza: **{pieza_sel}**")
            st.table(df_s[[col_bdd["subproceso"], col_bdd["pzxh"]]])
        else:
            st.warning("Selecciona una pieza válida para ver sus capacidades.")


def obtener_datos_unificados(
    df_auditorias, df_programa, df_bdd, col_prog, col_bdd, f_ini, f_fin
):
    # Detectamos columnas de auditoría
    col_aud = {
        "fecha": encontrar_columna(df_auditorias, ["FECHA"]),
        "pieza": encontrar_columna(df_auditorias, ["PIEZA"]),
        "subproceso": encontrar_columna(
            df_auditorias,
            ["SUBPROCESO", "SUB PROCESO", "SUB_PROCESO"],
            contiene_todos=["SUB", "CESO"],
        ),
        "real": encontrar_columna(df_auditorias, ["REAL"]),
    }

    if df_auditorias.empty or df_programa.empty or df_bdd.empty:
        return pd.DataFrame(), col_aud

    # Limpiamos Auditorías
    df_a_v = df_auditorias.copy()
    df_a_v[col_aud["real"]] = pd.to_numeric(
        df_a_v[col_aud["real"]], errors="coerce"
    ).fillna(0)

    # Filtramos Programa por Fechas
    df_p_v = df_programa.copy()
    df_p_v["FECHA_DT"] = pd.to_datetime(
        df_p_v[col_prog["fecha"]], format="%d/%m/%Y", errors="coerce"
    )
    df_p_v = df_p_v[
        (df_p_v["FECHA_DT"].dt.date >= f_ini) & (df_p_v["FECHA_DT"].dt.date <= f_fin)
    ]

    if df_p_v.empty:
        return pd.DataFrame(), col_aud

    # Filtro específico de Moldeo que tenías en tu código original
    mask_m = df_p_v[col_prog["area"]].str.upper() == "MOLDEO"
    df_p_m = df_p_v[
        mask_m
        & df_p_v[col_prog["pieza"]].str.contains(
            "GENERAL|VACIADO|ADOBES", case=False, na=False
        )
    ]
    df_p_o = df_p_v[~mask_m]
    df_p_final = pd.concat([df_p_m, df_p_o])

    df_p_final[col_prog["total"]] = convertir_serie_numerica(
        df_p_final[col_prog["total"]]
    )
    df_p_final = df_p_final[df_p_final[col_prog["total"]] > 0].copy()

    # Sacamos lo máximo reportado en auditoría por cada subproceso
    df_max_a = (
        df_a_v.groupby([col_aud["fecha"], col_aud["pieza"], col_aud["subproceso"]])[
            col_aud["real"]
        ]
        .max()
        .reset_index()
    )

    # Cruzamos Programa con BDD para saber qué subprocesos debe llevar cada pieza
    df_base_v = pd.merge(
        df_p_final[
            [
                col_prog["fecha"],
                col_prog["area"],
                col_prog["pieza"],
                col_prog["total"],
                "FECHA_DT",
            ]
        ],
        df_bdd[[col_bdd["pieza"], col_bdd["subproceso"], col_bdd["proceso"]]],
        left_on=col_prog["pieza"],
        right_on=col_bdd["pieza"],
        how="inner",
    )
    df_base_v = df_base_v[df_base_v[col_prog["area"]] == df_base_v[col_bdd["proceso"]]]

    # Unificamos todo
    df_uni = pd.merge(
        df_base_v,
        df_max_a,
        left_on=[col_prog["fecha"], col_prog["pieza"], col_bdd["subproceso"]],
        right_on=[col_aud["fecha"], col_aud["pieza"], col_aud["subproceso"]],
        how="left",
    ).fillna(0)

    # Cálculo final del % REAL basado en lo Programado
    total_seguro = convertir_serie_numerica(df_uni[col_prog["total"]]).fillna(0)
    real_seguro = convertir_serie_numerica(df_uni[col_aud["real"]]).fillna(0)
    df_uni["% REAL"] = 0.0
    mask_total = total_seguro > 0
    df_uni.loc[mask_total, "% REAL"] = (
        real_seguro[mask_total] / total_seguro[mask_total] * 100
    )

    return df_uni, col_aud


def render_dashboard_direccion(df_auditorias, df_programa, df_bdd, col_prog, col_bdd):
    st.markdown("## 🏭 Resultados del Programa de Producción NSG")

    # --- 1. FILTROS DE FECHA (Blindados) ---
    c1, c2 = st.columns(2)
    with c1:
        f_ini = st.date_input(
            "Analizar desde:", ahora_local().date() - timedelta(days=7), key="dash_f1"
        )
    with c2:
        f_fin = st.date_input("Hasta:", ahora_local().date(), key="dash_f2")

    # --- VALIDACIÓN DE HOJAS ---
    hojas_vacias = []
    if df_auditorias.empty:
        hojas_vacias.append("AUDITAR")
    if df_programa.empty:
        hojas_vacias.append("PROGRAMA")
    if df_bdd.empty:
        hojas_vacias.append("BDD")

    if hojas_vacias:
        st.warning(
            f"⚠️ Esperando datos... No se detectaron registros en: {', '.join(hojas_vacias)}"
        )
        return

    # --- 2. MOTOR UNIFICADO ---
    df_uni, col_aud = obtener_datos_unificados(
        df_auditorias, df_programa, df_bdd, col_prog, col_bdd, f_ini, f_fin
    )

    if df_uni.empty:
        st.info("No hay registros que coincidan con el rango de fechas.")
        return

    # --- PREPARACIÓN DE MÉTRICAS TEMPORALES ---
    df_dias = df_uni.groupby("FECHA_DT")["% REAL"].mean().reset_index()
    total_dias = len(df_dias)
    dias_ganados = len(df_dias[df_dias["% REAL"] >= 80])
    dias_riesgo = len(df_dias[(df_dias["% REAL"] >= 70) & (df_dias["% REAL"] < 80)])
    dias_perdidos = len(df_dias[df_dias["% REAL"] < 70])

    # --- 3. KPIs GLOBALES Y SEMÁFORO (Diseño Operativo) ---
    cumplimiento_total = df_uni["% REAL"].mean()

    st.markdown(
        f"""
<div style='display: flex; justify-content: space-between; gap: 15px; margin-bottom: 20px; flex-wrap: wrap;'>
    <div style='background: white; border-top: 5px solid #3498db; padding: 15px; border-radius: 8px; flex: 1; text-align: center; min-width: 150px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);'>
        <div style='font-size: 12px; color: #7f8c8d; font-weight: bold; text-transform: uppercase;'>🎯 CUMPLIMIENTO DEL PROGRAMA</div>
        <div style='font-size: 28px; color: #2980b9; font-weight: 900; margin-top: 5px;'>{cumplimiento_total:.1f}%</div>
    </div>
    <div style='background: white; border-top: 5px solid #95a5a6; padding: 15px; border-radius: 8px; flex: 1; text-align: center; min-width: 150px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);'>
        <div style='font-size: 12px; color: #7f8c8d; font-weight: bold; text-transform: uppercase;'>🗓️ DÍAS TRABAJADOS (PERIODO)</div>
        <div style='font-size: 28px; color: #7f8c8d; font-weight: 900; margin-top: 5px;'>{total_dias}</div>
    </div>
    <div style='background: white; border-top: 5px solid #2ecc71; padding: 15px; border-radius: 8px; flex: 1; text-align: center; min-width: 150px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);'>
        <div style='font-size: 12px; color: #7f8c8d; font-weight: bold; text-transform: uppercase;'>🟢 META LOGRADA (80% o más)</div>
        <div style='font-size: 28px; color: #27ae60; font-weight: 900; margin-top: 5px;'>{dias_ganados}</div>
    </div>
    <div style='background: white; border-top: 5px solid #f1c40f; padding: 15px; border-radius: 8px; flex: 1; text-align: center; min-width: 150px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);'>
        <div style='font-size: 12px; color: #7f8c8d; font-weight: bold; text-transform: uppercase;'>🟡 CASI LLEGAMOS (70% - 79%)</div>
        <div style='font-size: 28px; color: #f39c12; font-weight: 900; margin-top: 5px;'>{dias_riesgo}</div>
    </div>
    <div style='background: white; border-top: 5px solid #e74c3c; padding: 15px; border-radius: 8px; flex: 1; text-align: center; min-width: 150px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);'>
        <div style='font-size: 12px; color: #7f8c8d; font-weight: bold; text-transform: uppercase;'>🔴 DÍAS CRÍTICOS (Menos de 70%)</div>
        <div style='font-size: 28px; color: #c0392b; font-weight: 900; margin-top: 5px;'>{dias_perdidos}</div>
    </div>
</div>
""",
        unsafe_allow_html=True,
    )

    st.divider()

    # --- 4. RESUMEN EJECUTIVO (Con Diagnóstico y Producto Terminado) ---

    # A. Cálculos de Líderes y Cuellos de Botella
    area_estrella = df_uni.groupby(col_prog["area"])["% REAL"].mean().idxmax()
    area_estrella_val = df_uni.groupby(col_prog["area"])["% REAL"].mean().max()
    sub_critico = df_uni.groupby(col_bdd["subproceso"])["% REAL"].mean().idxmin()

    # B. Cálculo de Pieza Estrella (Solo Producto Terminado: Excluyendo Corazones, Moldeo y General)
    mask_pt = ~(
        df_uni[col_prog["area"]].str.upper().isin(["CORAZONES", "MOLDEO"])
        | df_uni[col_prog["pieza"]].str.contains("GENERAL", case=False, na=False)
    )
    df_pt_only = df_uni[mask_pt]

    if not df_pt_only.empty:
        pieza_estrella = df_pt_only.groupby(col_prog["pieza"])["% REAL"].mean().idxmax()
        p_est_val = df_pt_only.groupby(col_prog["pieza"])["% REAL"].mean().max()
        texto_pieza = f"La pieza líder en <b>Producto Terminado</b> es <b>{pieza_estrella}</b> ({p_est_val:.1f}%)."
    else:
        texto_pieza = "No hay datos suficientes de piezas terminadas (Ensamble/Corte) en este rango."

    # C. Cálculo de Causa Raíz (Motivo de Paro Principal)
    df_a_raw = df_auditorias.copy()
    c_f_raw = encontrar_columna(df_a_raw, ["FECHA"])
    c_n_raw = encontrar_columna(df_a_raw, ["NOTAS", "NOTA"])

    df_a_raw["FECHA_DT"] = pd.to_datetime(
        df_a_raw[c_f_raw], format="%d/%m/%Y", errors="coerce"
    )
    df_a_raw = df_a_raw[
        (df_a_raw["FECHA_DT"].dt.date >= f_ini)
        & (df_a_raw["FECHA_DT"].dt.date <= f_fin)
    ]
    df_a_raw["MOTIVO"] = (
        df_a_raw[c_n_raw].astype(str).str.extract(r"^\[(.*?)\]")[0] if c_n_raw else None
    )

    paro_principal = "No hay paros registrados."
    df_paros = df_a_raw[df_a_raw["MOTIVO"].notna() & (df_a_raw["MOTIVO"] != "SIN PARO")]
    if not df_paros.empty:
        paro_top = df_paros["MOTIVO"].value_counts().idxmax()
        paro_principal = f"El motivo de paro más frecuente fue <b>{paro_top}</b>."

    # D. Evaluación de la salud de la planta para el título dinámico
    if cumplimiento_total >= 80:
        estado_planta = (
            "<span style='color: #27ae60;'>🟢 OPERACIÓN RENTABLE (&ge; 80%)</span>"
        )
        borde_color = "#27ae60"
    elif cumplimiento_total >= 70:
        estado_planta = (
            "<span style='color: #f39c12;'>🟡 ZONA DE RIESGO (70% - 79%)</span>"
        )
        borde_color = "#f39c12"
    else:
        estado_planta = (
            "<span style='color: #c0392b;'>🔴 ESTADO CRÍTICO (&lt; 70%)</span>"
        )
        borde_color = "#c0392b"

    # E. Renderizado del Cuadro Ejecutivo
    st.markdown(
        f"""
    <div style='background-color: #f8f9fa; border-left: 6px solid {borde_color}; padding: 15px; border-radius: 5px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);'>
        <h4 style='margin-top: 0; color: #2c3e50;'>🤖 Diagnóstico del Sistema: {estado_planta}</h4>
        <ul style='margin-bottom: 0; font-size: 15px; color: #34495e;'>
            <li><b>Líder de Área:</b> <b>{area_estrella}</b> lidera con un <b>{area_estrella_val:.1f}%</b> de cumplimiento.</li>
            <li><b>Análisis de Salida:</b> {texto_pieza}</li>
            <li><b>Alerta de Proceso:</b> El subproceso <b>{sub_critico}</b> es el mayor cuello de botella actual.</li>
            <li><b>Causa Raíz Operativa:</b> {paro_principal}</li>
        </ul>
    </div>
    """,
        unsafe_allow_html=True,
    )

    # --- 5. GRÁFICAS PRINCIPALES (Con Semáforo y Escala al 100%) ---
    col_izq, col_der = st.columns(2)

    with col_izq:
        st.subheader("📈 Tendencia Diaria del periodo")
        df_dias["%_VISUAL"] = df_dias["% REAL"].clip(upper=100)

        fig_t = go.Figure()

        # 1. Fondos de colores (El techo sube a 115 para dar aire)
        fig_t.add_hrect(
            y0=0,
            y1=70,
            fillcolor="#e74c3c",
            opacity=0.1,
            line_width=0,
            annotation_text="ZONA CRÍTICA",
            annotation_position="bottom right",
            annotation_font_color="#c0392b",
        )
        fig_t.add_hrect(y0=70, y1=80, fillcolor="#f1c40f", opacity=0.15, line_width=0)
        fig_t.add_hrect(
            y0=80,
            y1=115,
            fillcolor="#2ecc71",
            opacity=0.1,
            line_width=0,
            annotation_text="ZONA SEGURA",
            annotation_position="top right",
            annotation_font_color="#27ae60",
        )

        # 2. Truco experto: Alternar posición del texto para evitar choques
        posiciones_texto = [
            "bottom center" if val >= 92 else "top center"
            for val in df_dias["%_VISUAL"]
        ]

        # 3. Línea de tendencia y puntos
        fig_t.add_trace(
            go.Scatter(
                x=df_dias["FECHA_DT"],
                y=df_dias["%_VISUAL"],
                mode="lines+markers+text",
                text=[f"{x:.0f}%" for x in df_dias["% REAL"]],
                textposition=posiciones_texto,  # Aplicamos el esquive
                textfont=dict(size=13, color="black", family="Arial Black"),
                line=dict(color="#2c3e50", width=3),
                marker=dict(
                    size=12,
                    color=[
                        "#2ecc71" if x >= 80 else "#f1c40f" if x >= 70 else "#e74c3c"
                        for x in df_dias["% REAL"]
                    ],
                    line=dict(width=2, color="white"),
                ),
            )
        )

        # 4. Línea de Objetivo movida a la izquierda (top left)
        fig_t.add_hline(
            y=80,
            line_dash="dash",
            line_color="#27ae60",
            line_width=3,
            annotation_text="🎯 META (80%)",
            annotation_position="top left",  # Ya no estorbará en medio
            annotation_font=dict(size=13, color="#27ae60", weight="bold"),
        )

        fig_t.update_layout(
            height=380,  # Le damos más altura a la gráfica para despejar los picos
            yaxis=dict(range=[0, 115], title="Cumplimiento (%)", showgrid=False),
            xaxis=dict(showgrid=False),
            margin=dict(t=30, b=10, l=10, r=10),
        )
        st.plotly_chart(fig_t, use_container_width=True)

    with col_der:
        st.subheader("🏢 Cumplimiento del periodo por Área")
        df_area = df_uni.groupby(col_prog["area"])["% REAL"].mean().reset_index()
        df_area["%_VISUAL"] = df_area["% REAL"].clip(upper=100)

        fig_a = go.Figure()

        # Zonas de Semáforo también para las barras
        fig_a.add_hrect(y0=0, y1=70, fillcolor="#e74c3c", opacity=0.1, line_width=0)
        fig_a.add_hrect(y0=70, y1=80, fillcolor="#f1c40f", opacity=0.15, line_width=0)
        fig_a.add_hrect(y0=80, y1=100, fillcolor="#2ecc71", opacity=0.1, line_width=0)

        fig_a.add_trace(
            go.Bar(
                x=df_area[col_prog["area"]],
                y=df_area["%_VISUAL"],
                marker_color=[
                    "#2ecc71" if x >= 80 else "#f1c40f" if x >= 70 else "#e74c3c"
                    for x in df_area["% REAL"]
                ],
                text=[f"{x:.1f}%" for x in df_area["% REAL"]],
                textposition="auto",
                textfont=dict(size=14, color="white", weight="bold"),
            )
        )

        fig_a.add_hline(
            y=80,
            line_dash="dash",
            line_color="#27ae60",
            line_width=3,
            annotation_text="🎯 OBJETIVO MÍNIMO (80%)",
            annotation_font=dict(size=13, color="#27ae60", weight="bold"),
        )

        fig_a.update_layout(
            yaxis=dict(range=[0, 105], title="Cumplimiento (%)", showgrid=False),
            margin=dict(t=30, b=10, l=10, r=10),
        )
        st.plotly_chart(fig_a, use_container_width=True)

    st.divider()

    # --- 6. RANKINGS (VISUALES PARA OPERADORES) ---
    st.markdown("### 🥇 Ranking de Avance del Programa de Producción")

    # 1. Preparar datos (Intacto)
    df_p_rank = df_pt_only.groupby(col_prog["pieza"])["% REAL"].mean().reset_index()
    df_s_rank = df_uni.groupby(col_bdd["subproceso"])["% REAL"].mean().reset_index()

    # 2. El "Truco Visual": Convertir números en barras de "Batería"
    cfg_progreso = st.column_config.ProgressColumn(
        "NIVEL DE AVANCE",
        help="Barra de cumplimiento",
        format="%.1f%%",
        min_value=0,
        max_value=100,
    )

    c_best, c_worst = st.columns(2)

    with c_best:
        st.success("🏆 EQUIPOS ESTRELLA (Aplaude a estos procesos)")

        st.write("📦 **TOP 5: Piezas con mayor avance:**")
        top_p = df_p_rank.nlargest(5, "% REAL")
        st.dataframe(
            top_p,
            column_config={col_prog["pieza"]: "PIEZA", "% REAL": cfg_progreso},
            hide_index=True,
            use_container_width=True,
        )

        st.write("⭐ **TOP 5: Operaciones con mejor avance:**")
        top_s = df_s_rank.nlargest(5, "% REAL")
        st.dataframe(
            top_s,
            column_config={col_bdd["subproceso"]: "OPERACIÓN", "% REAL": cfg_progreso},
            hide_index=True,
            use_container_width=True,
        )

    with c_worst:
        st.error("🚨 FOCOS ROJOS (Aquí hay que meter las manos)")

        st.write("⚠️ **ALERTA: Piezas con mayor atraso:**")
        bot_p = df_p_rank.nsmallest(5, "% REAL")
        st.dataframe(
            bot_p,
            column_config={col_prog["pieza"]: "PIEZA", "% REAL": cfg_progreso},
            hide_index=True,
            use_container_width=True,
        )

        st.write("🛑 **ALERTA: Operaciones con mayor rezago:**")
        bot_s = df_s_rank.nsmallest(5, "% REAL")
        st.dataframe(
            bot_s,
            column_config={col_bdd["subproceso"]: "OPERACIÓN", "% REAL": cfg_progreso},
            hide_index=True,
            use_container_width=True,
        )

    st.divider()

    # --- 7. COMPORTAMIENTO Y PAROS ---
    c_san, c_paro = st.columns(2)

    with c_san:
        st.subheader("📅 Cumplimiento por Día de la Semana")
        st.caption("Comparativo del periodo contra la meta del 80%")
        map_dias = {
            0: "Lunes",
            1: "Martes",
            2: "Miércoles",
            3: "Jueves",
            4: "Viernes",
            5: "Sábado",
            6: "Domingo",
        }
        df_dias["DIA"] = df_dias["FECHA_DT"].dt.dayofweek.map(map_dias)
        ord_d = pd.CategoricalDtype(categories=list(map_dias.values()), ordered=True)
        df_dias["DIA"] = df_dias["DIA"].astype(ord_d)

        san_lunes = df_dias.groupby("DIA")["% REAL"].mean().dropna().reset_index()
        san_lunes["%_VISUAL"] = san_lunes["% REAL"].clip(upper=100)

        fig_san = go.Figure()

        # 1. Zonas de Semáforo de Fondo
        fig_san.add_hrect(y0=0, y1=70, fillcolor="#e74c3c", opacity=0.1, line_width=0)
        fig_san.add_hrect(y0=70, y1=80, fillcolor="#f1c40f", opacity=0.15, line_width=0)
        fig_san.add_hrect(y0=80, y1=100, fillcolor="#2ecc71", opacity=0.1, line_width=0)

        # 2. Barras Dinámicas con colores de Semáforo
        fig_san.add_trace(
            go.Bar(
                x=san_lunes["DIA"],
                y=san_lunes["%_VISUAL"],
                marker_color=[
                    "#2ecc71" if x >= 80 else "#f1c40f" if x >= 70 else "#e74c3c"
                    for x in san_lunes["% REAL"]
                ],
                text=[f"{x:.0f}%" for x in san_lunes["% REAL"]],
                textposition="auto",
                textfont=dict(size=14, color="white", weight="bold"),
            )
        )

        # 3. Línea de Objetivo
        fig_san.add_hline(
            y=80,
            line_dash="dash",
            line_color="#27ae60",
            line_width=3,
            annotation_text="🎯 META (80%)",
            annotation_font=dict(size=13, color="#27ae60", weight="bold"),
        )

        fig_san.update_layout(
            height=380,  # Misma altura para que se vean simétricas
            yaxis=dict(range=[0, 105], title="Cumplimiento (%)", showgrid=False),
            margin=dict(t=30, b=10, l=10, r=10),
        )
        st.plotly_chart(fig_san, use_container_width=True)

    with c_paro:
        st.subheader("🛑 Motivos de Paro del Periodo")
        st.caption("Causas registradas que afectaron el cumplimiento del programa")
        if not df_paros.empty:
            cp = df_paros["MOTIVO"].value_counts().reset_index()
            cp.columns = ["MOTIVO", "FRECUENCIA"]  # Aseguramos los nombres de columnas

            fig_p = go.Figure(
                go.Pie(
                    labels=cp["MOTIVO"],
                    values=cp["FRECUENCIA"],
                    hole=0.45,  # Esto la convierte en Dona
                    marker=dict(
                        colors=["#e74c3c", "#f39c12", "#3498db", "#9b59b6", "#34495e"]
                    ),
                )
            )

            fig_p.update_traces(
                textinfo="percent+label",
                textposition="inside",  # Textos adentro para no ensuciar la pantalla
                textfont=dict(size=12, color="white", weight="bold"),
            )

            fig_p.update_layout(
                height=380,  # Misma altura que la gráfica de barras
                showlegend=False,  # Ocultamos la leyenda porque los textos ya están en la dona
                margin=dict(t=30, b=10, l=10, r=10),
            )
            st.plotly_chart(fig_p, use_container_width=True)
        else:
            # Si no hay paros, mostramos un mensaje de éxito grande y visible
            st.markdown(
                """
                <div style='background-color: #e8f8f5; border: 2px dashed #2ecc71; padding: 40px 20px; text-align: center; border-radius: 10px; height: 380px; display: flex; flex-direction: column; justify-content: center;'>
                    <h1 style='font-size: 50px; margin: 0;'>🎉</h1>
                    <h3 style='color: #27ae60; margin-top: 10px;'>¡CERO PAROS!</h3>
                    <p style='color: #7f8c8d; font-size: 16px;'>No hay incidencias registradas en este periodo.</p>
                </div>
                """,
                unsafe_allow_html=True,
            )

    # --- 8. BITÁCORA VINCULADA (Con diseño visual) ---
    st.subheader("📝 Reporte Detallado de Fallas (< 80%)")
    df_desv = df_uni[df_uni["% REAL"] < 80].copy()

    if not df_desv.empty:
        # Cruce con notas de auditoría
        df_a_notes = df_auditorias.copy()
        c_f_a = encontrar_columna(df_a_notes, ["FECHA"])
        c_p_a = encontrar_columna(df_a_notes, ["PIEZA"])
        c_s_a = encontrar_columna(df_a_notes, ["SUBPROCESO", "SUB PRO CESO"])
        c_n_a = encontrar_columna(df_a_notes, ["NOTAS", "NOTA"])

        df_bit = pd.merge(
            df_desv,
            df_a_notes[[c_f_a, c_p_a, c_s_a, c_n_a]],
            left_on=[col_prog["fecha"], col_prog["pieza"], col_bdd["subproceso"]],
            right_on=[c_f_a, c_p_a, c_s_a],
            how="left",
        ).drop_duplicates(
            subset=[col_prog["fecha"], col_prog["pieza"], col_bdd["subproceso"]]
        )

        df_bit_show = df_bit[
            [
                col_prog["fecha"],
                col_prog["area"],
                col_prog["pieza"],
                col_bdd["subproceso"],
                "% REAL",
                c_n_a,
            ]
        ]
        df_bit_show.columns = [
            "FECHA",
            "ÁREA",
            "PIEZA",
            "SUBPROCESO",
            "AVANCE",
            "NOTAS (MOTIVO)",
        ]

        # Le aplicamos la barra visual a los números crudos
        cfg_progreso_rojo = st.column_config.ProgressColumn(
            "AVANCE (%)",
            format="%.1f%%",
            min_value=0,
            max_value=100,
        )

        st.dataframe(
            df_bit_show.sort_values("FECHA", ascending=False),
            column_config={"AVANCE": cfg_progreso_rojo},
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.success(
            "¡Excelente! No hay desviaciones menores al 80% reportadas en este periodo."
        )


def render_estadistica_rango(df_auditorias, df_programa, df_bdd, col_prog, col_bdd):
    st.divider()
    st.markdown("### 📊 DESEMPEÑO POR RANGO DE FECHAS (CUMPLIMIENTO REAL)")

    c_r1, c_r2 = st.columns(2)
    with c_r1:
        f_ini_stat = st.date_input(
            "Desde:", ahora_local().date() - timedelta(days=30), key="vfinal_ini"
        )
    with c_r2:
        f_fin_stat = st.date_input("Hasta:", ahora_local().date(), key="vfinal_fin")

    # Usamos el mismo motor para que esta gráfica devuelva los mismos números que el Dashboard
    df_uni, col_aud = obtener_datos_unificados(
        df_auditorias, df_programa, df_bdd, col_prog, col_bdd, f_ini_stat, f_fin_stat
    )

    if df_uni.empty:
        st.warning(
            "Faltan datos en las hojas para calcular la estadística o no hay programación."
        )
        return

    res_final = df_uni.groupby(col_prog["area"])["% REAL"].mean().reset_index()

    for _, row in res_final.iterrows():
        area_n = row[col_prog["area"]]
        val_n = round(row["% REAL"], 1)
        color_n = obtener_color_nsg(val_n)
        ancho_barra = min(val_n, 100)

        st.markdown(
            f"""
            <div style='background:white; padding:18px; border-radius:15px; border-left:8px solid {color_n}; margin-bottom:15px; box-shadow: 0 4px 10px rgba(0,0,0,0.06);'>
                <div style='display:flex; justify-content:space-between; align-items:center; margin-bottom: 8px;'>
                    <span style='font-weight:bold; font-size:16px;'>{area_n}</span>
                    <span style='color:{color_n}; font-weight:800; font-size:24px;'>{val_n}%</span>
                </div>
                <div style='background:#e9ecef; height:14px; border-radius:10px; overflow:hidden;'>
                    <div style='background:{color_n}; width:{ancho_barra}%; height:100%; border-radius:10px;'></div>
                </div>
            </div>
        """,
            unsafe_allow_html=True,
        )


def main():
    st.set_page_config(layout="wide", page_title="NSG Auditoría v2.9", page_icon="🛡️")

    st.markdown(
        """
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
        """,
        unsafe_allow_html=True,
    )

    if "form_id" not in st.session_state:
        st.session_state.form_id = 0

    df_programa, col_prog = preparar_dataframe("PROGRAMA", 1)
    df_bdd_raw, col_bdd = preparar_dataframe("BDD", 0)
    df_auditorias, _ = preparar_dataframe("AUDITAR", 0)
    df_bdd = filtrar_bdd_activa(df_bdd_raw, col_bdd)

    with st.sidebar:
        if os.path.exists(LOGO_FILENAME):
            st.image(LOGO_FILENAME, use_container_width=True)

        st.markdown(
            "<h3 style='text-align: center;'>CONTROL DE ACCESO</h3>",
            unsafe_allow_html=True,
        )

        fecha_dt = st.date_input("FECHA", ahora_local().date())
        fecha_sel = fecha_dt.strftime("%d/%m/%Y")

        lista_areas = obtener_lista_areas(df_programa, col_prog)
        area_sel = st.selectbox("AREA", lista_areas, help="Departamento auditado.")

        corte_sel = st.selectbox(
            "CORTE",
            list(CORTES_DICT.keys()),
            index=sugerir_corte_actual(),
            help="Sugerido automáticamente por la hora actual.",
        )
        horas_acum = CORTES_DICT[corte_sel]

        st.divider()
        st.markdown("### Plan del Día")
        df_plan_dia = obtener_plan_del_dia(df_programa, col_prog, fecha_sel, area_sel)
        if not df_plan_dia.empty:
            st.dataframe(
                df_plan_dia[[col_prog["pieza"], col_prog["total"]]],
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.caption("No hay piezas programadas para la fecha y área seleccionadas.")

    # Línea 823
    tab_captura, tab_dashboard = st.tabs(
        ["📦 CAPTURA OPERATIVA", "📊 DASHBOARD DIRECCIÓN"]
    )

    with tab_captura:

        st.markdown(
            f"""
            <div style='display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px;'>
                <h1 style='margin:0;'>SISTEMA <span style='color:#E32B13;'>NSG</span> AUDITORÍA</h1>
                <div style='background:#EEE; padding: 8px 20px; border-radius:25px; font-weight:bold; color:#333; border: 1px solid #CCC;'>
                    ÁREA: {area_sel}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        avance_global, df_resumen_final = calcular_resumen(
            df_plan_dia, col_prog, df_bdd, col_bdd, df_auditorias, fecha_sel, area_sel
        )

        render_kpis(avance_global, df_resumen_final)
        render_graficos(avance_global, df_resumen_final)

        st.markdown("<div class='capture-container'>", unsafe_allow_html=True)
        st.subheader("REGISTRO DE AUDITORÍA")

        df_aud_hoy, col_aud = obtener_auditorias_hoy(df_auditorias, fecha_sel, area_sel)
        piezas_validas = (
            df_plan_dia[col_prog["pieza"]].unique()
            if not df_plan_dia.empty and col_prog.get("pieza")
            else []
        )
        piezas_pendientes = obtener_piezas_pendientes(
            df_plan_dia,
            col_prog,
            df_bdd,
            col_bdd,
            df_aud_hoy,
            col_aud,
            area_sel,
            corte_sel,
        )
        lista_desplegable = (
            piezas_pendientes if piezas_pendientes else list(piezas_validas)
        )

        c1, c2, c3 = st.columns([1, 1, 1])
        f_id = st.session_state.form_id

        with c1:
            if not lista_desplegable:
                st.warning("No hay piezas disponibles para auditar.")
                p_sel = None
                df_s = pd.DataFrame()
                sub_list = []
            else:
                p_sel = st.selectbox(
                    "PIEZA", lista_desplegable, help="Piezas programadas."
                )
                if not validar_columnas(col_bdd, ["pieza", "proceso", "subproceso"]):
                    df_s = df_bdd[
                        (df_bdd[col_bdd["pieza"]] == p_sel)
                        & (df_bdd[col_bdd["proceso"]] == area_sel)
                    ].copy()
                else:
                    df_s = pd.DataFrame()
                    st.error("No se encontraron columnas clave en la hoja BDD.")

                reps = []
                if (
                    p_sel
                    and not df_aud_hoy.empty
                    and col_aud.get("corte")
                    and col_aud.get("pieza")
                ):
                    reps = df_aud_hoy[
                        (df_aud_hoy[col_aud["corte"]] == corte_sel)
                        & (df_aud_hoy[col_aud["pieza"]] == p_sel)
                    ][col_aud["subproceso"]].tolist()

                sub_list = (
                    [
                        sub
                        for sub in df_s[col_bdd["subproceso"]].unique()
                        if sub not in reps
                    ]
                    if not df_s.empty
                    else []
                )

            s_sel = st.selectbox(
                "SUB-PROCESO",
                sub_list if sub_list else [PIEZA_TERMINADA],
                help="Operación a auditar.",
            )

        with c2:
            ops = st.number_input("OPERADORES", min_value=1, step=1, key=f"ops_{f_id}")
            real = st.number_input(
                "CANTIDAD REAL", min_value=0, step=1, key=f"r_{f_id}"
            )

        with c3:
            mins = st.number_input("MIN. PARO", min_value=0, step=1, key=f"m_{f_id}")
            mot = st.selectbox("MOTIVO PARO", MOTIVOS_PARO, key=f"mot_{f_id}")

        notas = st.text_input("NOTAS", key=f"n_{f_id}")

        if s_sel and s_sel != PIEZA_TERMINADA:
            try:
                if not df_s.empty and s_sel in df_s[col_bdd["subproceso"]].values:
                    if not col_bdd.get("pzxh"):
                        st.warning(
                            "No se encontró la columna de capacidad PZ X H en la BDD."
                        )
                    else:
                        pz_h_val = df_s[df_s[col_bdd["subproceso"]] == s_sel][
                            col_bdd["pzxh"]
                        ].iloc[0]
                        pz_h = float(pz_h_val) if pz_h_val else 0
                        meta = int((pz_h * max(0, horas_acum - (mins / 60))) * ops)
                        st.info(f"Meta: {meta} piezas")

                        if mot == "OTRO (ESPECIFICAR EN NOTAS)" and not notas.strip():
                            st.warning(
                                "Captura una nota cuando el motivo de paro sea OTRO."
                            )
                        elif st.button("GUARDAR REGISTRO"):
                            exito = guardar_registro(
                                fecha_sel,
                                area_sel,
                                corte_sel,
                                p_sel,
                                s_sel,
                                real,
                                meta,
                                ops,
                                mot,
                                notas,
                            )
                            if exito:
                                st.toast("Guardado exitoso")
                                invalidar_cache_hoja("AUDITAR")
                                st.session_state.form_id += 1
                                time.sleep(0.5)
                                st.rerun()
                else:
                    st.warning("No hay datos de capacidad para este subproceso.")
            except ValueError:
                st.error("La capacidad PZ X H contiene un valor no numérico.")
            except Exception as exc:
                st.error(f"Error al calcular o guardar: {exc}")
        else:
            st.success("Pieza completada.")

        st.markdown("</div>", unsafe_allow_html=True)

        render_estatus_detallado(df_resumen_final)
        render_capacidades(
            df_s if "df_s" in locals() else pd.DataFrame(),
            col_bdd,
            p_sel if "p_sel" in locals() else None,
        )
        render_estadistica_rango(df_auditorias, df_programa, df_bdd, col_prog, col_bdd)

    with tab_dashboard:
        render_dashboard_direccion(
            df_auditorias, df_programa, df_bdd, col_prog, col_bdd
        )


if __name__ == "__main__":
    main()
