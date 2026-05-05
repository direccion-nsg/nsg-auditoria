import os
import re
import time
import unicodedata
from datetime import datetime

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


def render_estadistica_rango(df_auditorias, df_programa, df_bdd, col_prog, col_bdd):
    st.divider()
    st.markdown("### DESEMPEÑO POR RANGO DE FECHAS")

    c_r1, c_r2 = st.columns(2)
    with c_r1:
        f_ini_stat = st.date_input("Desde:", ahora_local().date(), key="vfinal_ini")
    with c_r2:
        f_fin_stat = st.date_input("Hasta:", ahora_local().date(), key="vfinal_fin")

    if df_auditorias.empty or df_programa.empty or df_bdd.empty:
        return

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
    faltantes_aud = validar_columnas(col_aud, ["fecha", "pieza", "subproceso", "real"])
    faltantes_prog = validar_columnas(col_prog, ["fecha", "area", "pieza", "total"])
    faltantes_bdd = validar_columnas(col_bdd, ["pieza", "subproceso", "proceso"])
    if faltantes_aud or faltantes_prog or faltantes_bdd:
        st.warning("Faltan columnas requeridas para construir la estadística final.")
        return

    df_a_v = df_auditorias.copy()
    df_a_v[col_aud["real"]] = pd.to_numeric(
        df_a_v[col_aud["real"]], errors="coerce"
    ).fillna(0)

    df_p_v = df_programa.copy()
    df_p_v["FECHA_DT"] = pd.to_datetime(
        df_p_v[col_prog["fecha"]], format="%d/%m/%Y", errors="coerce"
    )
    df_p_v = df_p_v[
        (df_p_v["FECHA_DT"].dt.date >= f_ini_stat)
        & (df_p_v["FECHA_DT"].dt.date <= f_fin_stat)
    ]

    mask_m = df_p_v[col_prog["area"]].str.upper() == "MOLDEO"
    df_p_m = df_p_v[
        mask_m
        & df_p_v[col_prog["pieza"]].str.contains(
            "GENERAL|VACIADO|ADOBES", case=False, na=False
        )
    ]
    df_p_o = df_p_v[~mask_m]
    df_p_final = pd.concat([df_p_m, df_p_o])
    total_original = len(df_p_final)
    df_p_final[col_prog["total"]] = convertir_serie_numerica(
        df_p_final[col_prog["total"]]
    )
    df_p_final = df_p_final[df_p_final[col_prog["total"]] > 0].copy()
    filas_excluidas = total_original - len(df_p_final)
    if filas_excluidas > 0:
        st.caption(
            f"Se excluyeron {filas_excluidas} registros del programa con TOTAL vacío, inválido o igual a 0."
        )

    df_max_a = (
        df_a_v.groupby([col_aud["fecha"], col_aud["pieza"], col_aud["subproceso"]])[
            col_aud["real"]
        ]
        .max()
        .reset_index()
    )

    df_base_v = pd.merge(
        df_p_final[
            [col_prog["fecha"], col_prog["area"], col_prog["pieza"], col_prog["total"]]
        ],
        df_bdd[[col_bdd["pieza"], col_bdd["subproceso"], col_bdd["proceso"]]],
        left_on=col_prog["pieza"],
        right_on=col_bdd["pieza"],
        how="inner",
    )
    df_base_v = df_base_v[df_base_v[col_prog["area"]] == df_base_v[col_bdd["proceso"]]]

    df_unificado_v = pd.merge(
        df_base_v,
        df_max_a,
        left_on=[col_prog["fecha"], col_prog["pieza"], col_bdd["subproceso"]],
        right_on=[col_aud["fecha"], col_aud["pieza"], col_aud["subproceso"]],
        how="left",
    ).fillna(0)

    df_unificado_v[col_prog["total"]] = convertir_serie_numerica(
        df_unificado_v[col_prog["total"]]
    ).fillna(0)
    total_seguro = convertir_serie_numerica(df_unificado_v[col_prog["total"]]).fillna(0)
    real_seguro = convertir_serie_numerica(df_unificado_v[col_aud["real"]]).fillna(0)
    df_unificado_v["% REAL"] = 0.0
    mask_total_valido = total_seguro > 0
    df_unificado_v.loc[mask_total_valido, "% REAL"] = (
        real_seguro[mask_total_valido] / total_seguro[mask_total_valido] * 100
    )

    if df_unificado_v.empty:
        st.info("No hay datos en el rango seleccionado.")
        return

    res_final = df_unificado_v.groupby(col_prog["area"])["% REAL"].mean().reset_index()
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
        df_plan_dia, col_prog, df_bdd, col_bdd, df_aud_hoy, col_aud, area_sel, corte_sel
    )
    lista_desplegable = piezas_pendientes if piezas_pendientes else list(piezas_validas)

    c1, c2, c3 = st.columns([1, 1, 1])
    f_id = st.session_state.form_id

    with c1:
        if not lista_desplegable:
            st.warning("No hay piezas disponibles para auditar.")
            p_sel = None
            df_s = pd.DataFrame()
            sub_list = []
        else:
            p_sel = st.selectbox("PIEZA", lista_desplegable, help="Piezas programadas.")
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
                [sub for sub in df_s[col_bdd["subproceso"]].unique() if sub not in reps]
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
        real = st.number_input("CANTIDAD REAL", min_value=0, step=1, key=f"r_{f_id}")

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


if __name__ == "__main__":
    main()
