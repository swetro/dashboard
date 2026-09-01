"""
transformar_json.py — Convierte el JSON de la app del socio
al formato que espera el dashboard de Swetro.

USO:
  python transformar_json.py input.json
  python transformar_json.py input.json --output public/data/u002.json
  python transformar_json.py input.json --con-pulse   # genera Pulse via Anthropic

DEPENDENCIAS:
  pip install anthropic   # solo si usas --con-pulse
"""

import csv
import json
import re
import sys
import os
import secrets
import argparse
from datetime import datetime, date, timedelta
from collections import defaultdict

TOKENS_PATH = "private/tokens.csv"


# ── Constantes de disciplinas ─────────────────────────────────

RUNNING_TIPOS   = {"running", "trail_running", "treadmill", "treadmillrunning", "treadmill_running", "virtualrun"}
CYCLING_TIPOS   = {"cycling", "indoorcycling", "indoor_cycling", "virtualride", "mountainbiking", "gravel_cycling"}
SWIMMING_TIPOS  = {"swimming", "openwater"}
STRENGTH_TIPOS  = {"strength", "strength_training", "weighttraining", "gym"}

def normalizar_tipo(tipo_raw):
    if not tipo_raw:
        return "other"
    t = tipo_raw.lower().strip()
    if t in RUNNING_TIPOS:   return "running"
    if t in CYCLING_TIPOS:   return "cycling"
    if t in SWIMMING_TIPOS:  return "swimming"
    if t in STRENGTH_TIPOS:  return "strength"
    return t


# ── Tokens de acceso (reemplazan userId secuencial en el link público) ──

def obtener_token(user_id):
    """
    Devuelve el token público de user_id, generándolo si no existe. El
    mapeo userId -> token vive en TOKENS_PATH (no versionado — ver
    .gitignore) y es la única forma de saber, puertas adentro, a quién
    pertenece cada archivo public/data/<token>.json. Idempotente: correr el
    pipeline de nuevo para el mismo usuario reutiliza su token, no invalida
    el link que ya le mandamos.
    """
    filas = []
    if os.path.exists(TOKENS_PATH):
        with open(TOKENS_PATH, "r", encoding="utf-8", newline="") as f:
            filas = list(csv.DictReader(f))

    for fila in filas:
        if fila["user_id"] == str(user_id):
            return fila["token"]

    token = secrets.token_urlsafe(8)
    filas.append({"user_id": str(user_id), "token": token})

    os.makedirs(os.path.dirname(TOKENS_PATH), exist_ok=True)
    with open(TOKENS_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["user_id", "token"])
        writer.writeheader()
        writer.writerows(filas)

    return token


# ── Helpers ───────────────────────────────────────────────────

def metros_a_km(m):
    return round(m / 1000, 2) if m else 0

def segundos_a_minutos(s):
    return round(s / 60, 1) if s else 0

def pace_a_string(pace_raw):
    if not pace_raw or pace_raw <= 0 or pace_raw > 30:
        return "—"
    mins = int(pace_raw)
    segs = round((pace_raw - mins) * 60)
    if segs >= 60:
        mins += 1
        segs -= 60
    return f"{mins}:{segs:02d}"

def segundos_a_tiempo(total_seconds):
    if not total_seconds or total_seconds <= 0:
        return "—"
    total_seconds = round(total_seconds)
    hours = total_seconds // 3600
    mins  = (total_seconds % 3600) // 60
    secs  = total_seconds % 60
    if hours > 0:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"

def fecha_iso_a_date(iso_str):
    if not iso_str:
        return ""
    return iso_str[:10]

def semana_str(start, end):
    return f"{fecha_iso_a_date(start)}/{fecha_iso_a_date(end)}"

def bandera_pais(country_code):
    flags = {
        "COL": "🇨🇴", "ARG": "🇦🇷", "MEX": "🇲🇽", "CHL": "🇨🇱",
        "ECU": "🇪🇨", "PER": "🇵🇪", "VEN": "🇻🇪", "BRA": "🇧🇷",
        "USA": "🇺🇸", "CRI": "🇨🇷", "URY": "🇺🇾", "PAN": "🇵🇦",
        "ESP": "🇪🇸", "CAN": "🇨🇦", "GBR": "🇬🇧",
    }
    return flags.get(country_code, "🏃")


# ── Transformar PRs ───────────────────────────────────────────

def transformar_prs(personal_records):
    if not personal_records:
        return []
    pr_colors = {
        "1K":  "#9CA0AA",
        "5K":  "#3D7EFF",
        "10K": "#7EB8FF",
        "21K": "#CAFF00",
        "42K": "#FFB800",
    }
    best_prs = {}
    for pr in personal_records:
        rt   = pr.get("record_type", "")
        rank = pr.get("rank", 99)
        if rank == 1 and rt not in best_prs:
            best_prs[rt] = pr

    result = []
    for rt in ["1K", "5K", "10K", "21K", "42K"]:
        if rt in best_prs:
            pr = best_prs[rt]
            result.append({
                "dist":  rt,
                "mark":  segundos_a_tiempo(pr.get("value")),
                "color": pr_colors.get(rt, "#9CA0AA"),
            })
    return result


# Distancia real (km) de cada categoría de PR — 21K/42K son las oficiales de
# media/maratón, no el redondeo del label.
DISTANCIAS_PR_KM = {"1K": 1, "5K": 5, "10K": 10, "21K": 21.0975, "42K": 42.195}


def _km_meta_de_label(label):
    """"42K MIA" -> 42.195. Usa la distancia oficial si el número coincide con
    una carrera estándar (21/42), si no toma el número tal cual."""
    m = re.match(r"(\d+)K", label or "")
    if not m:
        return None
    n = int(m.group(1))
    return {21: 21.0975, 42: 42.195}.get(n, float(n))


# Ningún humano corre 10K a menos de 2.5 min/km (récord mundial: ~2.86
# min/km) — por debajo de eso es GPS corrupto, no un atleta de élite.
PACE_MIN_VALIDO_MIN_KM = 2.5

# Media banda alrededor del ritmo de referencia (banda total 0.8 min/km,
# igual ancho que swetro-retro) para buscar sesiones "al mismo ritmo hoy".
MEDIA_BANDA_FC_MIN_KM = 0.4

# Mínimo de sesiones en banda para confiar en el promedio de FC actual. Más
# bajo que el 8 de swetro-retro (ciclo completo de medio maratón, mucho más
# volumen por usuario) porque acá el histórico por usuario es más corto.
FC_SESIONES_MINIMAS_PROYECCION = 4

# A partir de qué fracción de la distancia meta se considera la referencia
# "cercana" (media maratón prediciendo maratón: 21.0975/42.195 = 0.5, la
# distancia de referencia clásica). Por debajo — un 10K o menos prediciendo
# una maratón — Riegel extrapola demasiado y es "lejana".
RATIO_DISTANCIA_CERCANA = 0.45

# Elevación aproximada (m) de las ciudades que aparecen en los nombres de
# actividad y en las metas de carrera conocidas. Solo para la nota de
# altitud (paso 6) — no corrige el número, es informativo. Ciudad no
# listada => sin nota, no se asume nada.
ALTITUD_CIUDAD_M = {
    "bogotá": 2640, "bogota": 2640, "soacha": 2560, "la calera": 2900,
    "zipaquirá": 2650, "zipaquira": 2650, "paipa": 2500,
    "medellín": 1495, "medellin": 1495, "fusagasugá": 1728, "fusagasuga": 1728,
    "cali": 1018, "tuluá": 973, "tulua": 973, "san gil": 1113,
    "puente nacional": 1230, "sincelejo": 213, "jordán": 600, "jordan": 600,
    "miami": 2, "sydney": 3, "nueva york": 10, "new york": 10,
}
ALTITUD_UMBRAL_ALTA_M = 1800
ALTITUD_UMBRAL_BAJA_M = 800


# El dispositivo genera "PR" (personal_records) solo para 1K/5K/10K — un
# fondo de 21K/42K corrido en una sesión normal NUNCA aparece ahí, aunque sea
# el mejor ancla de Riegel disponible. Se detecta directo en el historial de
# actividades con el mismo criterio que swetro-retro usa para "otras
# carreras": corridas largas por rango de distancia.
RANGO_21K_KM = (19, 30)
MINIMO_42K_KM = 39

# Solo se considera una referencia "disponible" si es de los últimos 12
# meses — una marca vieja no representa la forma actual del atleta.
VENTANA_REFERENCIA_DIAS = 365

DISTANCIA_A_LABEL = {"1K": "1K", "5K": "5K", "10K": "10K", "21K": "Media maratón", "42K": "Maratón"}


def _candidatos_referencia(personal_records, activities, fecha_limite):
    candidatos = []
    for pr in personal_records or []:
        rt = pr.get("record_type", "")
        d_km = DISTANCIAS_PR_KM.get(rt)
        valor = pr.get("value")
        fecha = (pr.get("start_date") or "")[:10]
        if not d_km or not valor or pr.get("rank", 99) > 3:
            continue
        if fecha < fecha_limite:
            continue
        if (valor / 60) / d_km < PACE_MIN_VALIDO_MIN_KM:
            continue
        candidatos.append({
            "grupo": rt, "km": d_km, "segundos": valor,
            "fc": pr.get("average_heart_rate_in_beats_per_minute"), "fecha": fecha,
        })

    for a in activities or []:
        if a.get("type") != "running":
            continue
        dist = a.get("dist_km") or 0
        segundos = (a.get("duration_min") or 0) * 60
        fecha = a.get("date") or ""
        if not segundos or fecha < fecha_limite:
            continue
        if (segundos / 60) / dist < PACE_MIN_VALIDO_MIN_KM:
            continue
        if RANGO_21K_KM[0] <= dist < RANGO_21K_KM[1]:
            grupo = "21K"
        elif dist >= MINIMO_42K_KM:
            grupo = "42K"
        else:
            continue
        candidatos.append({
            "grupo": grupo, "km": dist, "segundos": segundos,
            "fc": a.get("hr") or None, "fecha": fecha,
        })
    return candidatos


def _elegir_carrera_referencia(personal_records, activities, fecha_limite):
    """Ancla de Riegel. Regla de prioridad (en ese orden, nunca al revés):
    1. La distancia MÁS LARGA disponible dentro de los últimos 12 meses —
       un 21K siempre le gana a un 10K más reciente, la distancia manda.
    2. Solo entre carreras de esa MISMA distancia, la de RITMO MÁS RÁPIDO
       desempata — no la más reciente. Un plan de maratón genera fondos de
       20-22km cada 1-2 semanas; la fecha no distingue un esfuerzo de
       carrera real de un fondo cómodo a la misma distancia, el ritmo sí
       (confirmado con datos reales: el 26 jul es sistemáticamente el más
       rápido del grupo, no el más reciente). Para los PRs de 1K/5K/10K esto
       también implica que rank 1 gana sobre rank 2/3 dentro del grupo.
    Descarta paces imposibles (PACE_MIN_VALIDO_MIN_KM): dato de GPS
    corrupto, no una marca real."""
    candidatos = _candidatos_referencia(personal_records, activities, fecha_limite)
    if not candidatos:
        return None, None

    mejor_grupo = max({c["grupo"] for c in candidatos}, key=lambda g: DISTANCIAS_PR_KM[g])
    mismos = [c for c in candidatos if c["grupo"] == mejor_grupo]
    referencia = min(mismos, key=lambda c: c["segundos"] / c["km"])
    return mejor_grupo, referencia


def _ciudad_de_actividad_en_fecha(activities, fecha_iso):
    """Busca en las actividades ya transformadas la sesión de running de esa
    fecha para extraer la ciudad del nombre ("Bogotá, D.C. Carrera" ->
    "bogotá"). Best-effort: si no hay match o el nombre no tiene el patrón
    esperado, no hay ciudad — la nota de altitud simplemente no se muestra."""
    if not fecha_iso:
        return None
    for a in activities or []:
        if a.get("type") == "running" and a.get("date") == fecha_iso:
            nombre = a.get("name", "")
            m = re.match(r"([A-Za-zÀ-ÿ, .]+?)\s*(?:-\s*)?(?:Carrera|carrera)", nombre)
            if m:
                return m.group(1).strip(" ,").lower()
    return None


def _nota_altitud(ciudad_ref, nombre_meta):
    if not ciudad_ref:
        return None
    # Coincidencia por substring, no por igualdad: el nombre de actividad trae
    # sufijos ("Bogotá, D.C.") que no están en las claves del diccionario.
    alt_ref = next((v for k, v in ALTITUD_CIUDAD_M.items() if k in ciudad_ref), None)
    if alt_ref is None or alt_ref < ALTITUD_UMBRAL_ALTA_M:
        return None
    alt_meta = next(
        (v for k, v in ALTITUD_CIUDAD_M.items() if k in (nombre_meta or "").lower()), None
    )
    if alt_meta is None or alt_meta >= ALTITUD_UMBRAL_BAJA_M:
        return None
    return (
        f"Tu referencia fue a {int(alt_ref)}m de altitud; tu meta es prácticamente "
        f"a nivel del mar. Sin esa exigencia extra, es razonable esperar mejor "
        f"desempeño del que muestra el número — no lo estamos corrigiendo."
    )


def proyectar_tiempo_carrera(personal_records, distancia_meta_km, activities, nombre_meta, hoy):
    """Proyección en tres pasos, nunca calculada por un LLM (mismo motivo que
    dias_restantes: un modelo "calculando" esto a ojo puede alucinar un
    resultado incoherente con los datos reales del atleta):

    1. Tiempo base: Riegel (T2 = T1*(D2/D1)^1.06) desde la carrera de
       referencia elegida por _elegir_carrera_referencia — distancia más
       larga disponible en los últimos 12 meses primero, recencia solo
       desempata entre carreras de esa misma distancia.
    2. Ajuste por eficiencia cardiaca: compara la FC de la referencia contra
       la FC promedio actual en sesiones al mismo ritmo (±MEDIA_BANDA_FC_MIN_KM)
       posteriores a la referencia. Si la FC bajó al mismo ritmo, el atleta
       mejoró desde la referencia y el tiempo baja proporcionalmente (y
       viceversa si subió). Sin sesiones suficientes en banda, no hay ajuste.
    3. Confianza: "alta" con referencia de distancia cercana (>= la mitad de
       la meta) y ajuste de FC aplicado; "baja" con referencia lejana y sin
       ajuste; "media" en los dos casos intermedios.

    Devuelve None si no hay ninguna referencia utilizable."""
    if not distancia_meta_km:
        return None
    fecha_limite = (hoy - timedelta(days=VENTANA_REFERENCIA_DIAS)).isoformat()
    grupo, ref = _elegir_carrera_referencia(personal_records, activities, fecha_limite)
    if ref is None:
        return None

    d1 = ref["km"]
    t1 = ref["segundos"]
    fc_ref = ref.get("fc")
    fecha_ref = ref["fecha"]

    t_base = t1 * (distancia_meta_km / d1) ** 1.06
    pace_ref_min_km = (t1 / 60) / d1

    # Ajuste por eficiencia cardiaca: FC promedio actual al mismo ritmo que
    # la referencia, en sesiones posteriores a ella.
    fc_actual = None
    n_banda = 0
    if fc_ref:
        lo, hi = pace_ref_min_km - MEDIA_BANDA_FC_MIN_KM, pace_ref_min_km + MEDIA_BANDA_FC_MIN_KM
        muestras = [
            a["hr"] for a in (activities or [])
            if a.get("type") == "running" and a.get("hr")
            and a.get("date", "") > fecha_ref
            and lo <= (a.get("pace_raw") or 0) <= hi
        ]
        n_banda = len(muestras)
        if n_banda >= FC_SESIONES_MINIMAS_PROYECCION:
            fc_actual = sum(muestras) / n_banda

    delta_pct = None
    t_final = t_base
    if fc_actual is not None:
        delta_pct = (fc_ref - fc_actual) / fc_ref
        t_final = t_base * (1 - delta_pct)

    pace_final_seg_km = t_final / distancia_meta_km

    # Confianza
    cercana = (d1 / distancia_meta_km) >= RATIO_DISTANCIA_CERCANA
    tiene_ajuste = fc_actual is not None
    if cercana and tiene_ajuste:
        confianza = "alta"
    elif not cercana and not tiene_ajuste:
        confianza = "baja"
    else:
        confianza = "media"

    # Frase de contexto
    etiqueta = DISTANCIA_A_LABEL.get(grupo, grupo)
    nombre_ref = f"{etiqueta} del {fmt_fecha_es(date.fromisoformat(fecha_ref), False)}" if fecha_ref else etiqueta
    if delta_pct is not None:
        minutos_ganados = abs(t_base - t_final) / 60
        if delta_pct > 0:
            contexto = (
                f"Tu corazón trabaja un {round(delta_pct * 100)}% menos al mismo ritmo "
                f"que en tu {nombre_ref}. Eso mejora tu proyección en {minutos_ganados:.0f} minutos."
            )
        elif delta_pct < 0:
            contexto = f"Tu eficiencia cardiaca bajó un {round(abs(delta_pct) * 100)}% desde tu {nombre_ref}."
        else:
            contexto = f"Tu FC al mismo ritmo se mantiene igual que en tu {nombre_ref}."
    else:
        contexto = f"Basado en tu {nombre_ref}. Sin datos suficientes de FC para ajustar por preparación."

    referencia_str = f"{etiqueta} del {fmt_fecha_es(date.fromisoformat(fecha_ref), True)} en {segundos_a_tiempo(t1)}"
    if fc_ref:
        referencia_str += f" (FC {int(fc_ref)} bpm)"

    ciudad_ref = _ciudad_de_actividad_en_fecha(activities, fecha_ref)

    return {
        "tiempo":      segundos_a_tiempo(t_final),
        "ritmo":       pace_a_string(pace_final_seg_km / 60),
        "confianza":   confianza,
        "contexto":    contexto,
        "referencia":  referencia_str,
        "notaAltitud": _nota_altitud(ciudad_ref, nombre_meta),
    }


# ── Transformar actividad individual ─────────────────────────

def transformar_actividad(a):
    tipo = normalizar_tipo(a.get("activity_type", "running"))
    dist_km      = metros_a_km(a.get("distance_in_meters", 0))
    duration_min = segundos_a_minutos(a.get("duration_in_seconds", 0))

    # Pace solo tiene sentido en running y ciclismo
    pace_raw = a.get("average_pace_in_minutes_per_kilometer", 0)
    if tipo in ("swimming", "strength", "other"):
        pace_raw = 0

    return {
        "date":         fecha_iso_a_date(a.get("start_time_utc")),
        "name":         a.get("name", "Actividad"),
        "type":         tipo,
        "dist_km":      dist_km,
        "duration_min": duration_min,
        "pace":         pace_a_string(pace_raw),
        "pace_raw":     round(pace_raw, 3) if pace_raw else 0,
        "hr":           a.get("average_heart_rate_in_beats_per_minute") or 0,
        "kcal":         a.get("active_kilocalories") or 0,
        "elevation":    a.get("total_elevation_gain_in_meters"),
        "points":       0,
        "effort":       a.get("effort_density"),
        "heart_eff":    round(a.get("heart_efficiency") or 0, 5),
    }

# Distancia mínima por disciplina para filtrar basura
MIN_DIST = {
    "running":  0.5,    # km
    "cycling":  1.0,    # km
    "swimming": 0.1,    # km (= 100m)
    "strength": 0.0,    # sin distancia mínima
}

def es_actividad_valida(a):
    tipo = a.get("type", "other")
    min_km = MIN_DIST.get(tipo, 0.5)
    if tipo == "strength":
        return a.get("duration_min", 0) >= 10   # al menos 10 min de fuerza
    return a.get("dist_km", 0) >= min_km


# ── Métricas semanales por disciplina ────────────────────────

def calcular_weekly_multidisciplina(activities):
    """
    Calcula métricas semanales agrupando por semana ISO y disciplina.
    Retorna lista de semanas con breakdown por deporte.

    Incluye la semana en curso (incompleta) a propósito: esta lista alimenta
    la pestaña SEMANA, que muestra progreso en vivo. El análisis de Pulse
    NO usa esta lista directamente — filtra a la última semana completa por
    su cuenta (ver `ultima_semana_completa` y `generar_pulse`).
    """
    from datetime import datetime, timedelta

    def iso_week_start(date_str):
        if not date_str:
            return None
        d = datetime.fromisoformat(date_str)
        # Lunes de esa semana
        return (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")

    # Agrupar actividades por semana
    semanas = defaultdict(lambda: defaultdict(list))
    for a in activities:
        ws = iso_week_start(a.get("date", ""))
        if ws:
            semanas[ws][a.get("type", "other")].append(a)

    weekly = []
    for ws in sorted(semanas.keys()):
        we = (datetime.fromisoformat(ws) + timedelta(days=6)).strftime("%Y-%m-%d")
        por_tipo = semanas[ws]

        # Running
        run_acts   = por_tipo.get("running", [])
        run_km     = round(sum(a["dist_km"] for a in run_acts), 2)
        run_sess   = len(run_acts)
        run_avg_hr = round(sum(a["hr"] for a in run_acts) / len(run_acts)) if run_acts else 0
        run_avg_pace = round(
            sum(a["pace_raw"] for a in run_acts if a["pace_raw"] > 0) /
            max(len([a for a in run_acts if a["pace_raw"] > 0]), 1), 3
        )

        # Ciclismo (indoor + outdoor)
        cyc_acts  = por_tipo.get("cycling", [])
        cyc_km    = round(sum(a["dist_km"] for a in cyc_acts), 2)
        cyc_sess  = len(cyc_acts)

        # Natación
        swm_acts  = por_tipo.get("swimming", [])
        swm_m     = round(sum(a["dist_km"] * 1000 for a in swm_acts))  # en metros
        swm_sess  = len(swm_acts)

        # Fuerza
        str_acts  = por_tipo.get("strength", [])
        str_min   = round(sum(a["duration_min"] for a in str_acts))
        str_sess  = len(str_acts)

        # Totales
        all_acts  = [a for acts in por_tipo.values() for a in acts]
        total_kcal = sum(a["kcal"] for a in all_acts)
        total_sess = sum(len(v) for v in por_tipo.values())

        weekly.append({
            "week":         f"{ws}/{we}",
            # Running (para compatibilidad con dashboard existente)
            "total_km":     run_km,
            "sessions":     run_sess,
            "avg_hr":       run_avg_hr,
            "avg_pace":     run_avg_pace,
            "total_kcal":   total_kcal,
            # Por disciplina
            "running":      {"km": run_km,  "sessions": run_sess, "avg_hr": run_avg_hr, "avg_pace": run_avg_pace},
            "cycling":      {"km": cyc_km,  "sessions": cyc_sess},
            "swimming":     {"metros": swm_m, "sessions": swm_sess},
            "strength":     {"minutos": str_min, "sessions": str_sess},
            "total_sessions": total_sess,
        })

    return weekly


# ── ACWR ──────────────────────────────────────────────────────

def ultima_semana_completa(hoy=None):
    """
    (lunes, domingo) de la última semana COMPLETA relativa a hoy. La semana
    que contiene "hoy" nunca cuenta como completa, sin importar qué día de
    esa semana sea — lunes o domingo, siempre es la semana anterior.
    """
    hoy = hoy or date.today()
    lunes_semana_actual = hoy - timedelta(days=hoy.weekday())
    lunes = lunes_semana_actual - timedelta(days=7)
    domingo = lunes_semana_actual - timedelta(days=1)
    return lunes, domingo


MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
            "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def fmt_fecha_es(d, incluir_anio):
    base = f"{d.day} de {MESES_ES[d.month - 1]}"
    return f"{base} de {d.year}" if incluir_anio else base


def fmt_rango_semana_es(inicio, fin):
    incluir_anio = inicio.year != fin.year
    return f"{fmt_fecha_es(inicio, incluir_anio)} al {fmt_fecha_es(fin, incluir_anio)}"


def _date_de_iso_z(iso_str):
    """Azure exporta fechas con sufijo 'Z' (p.ej. '2026-07-27T00:00:00Z') que
    datetime.fromisoformat no soporta en Python 3.9. Los primeros 10
    caracteres siempre son la fecha, así que basta con parsear eso."""
    return date.fromisoformat(iso_str[:10])


# Disciplinas de ACWR que sí se muestran en el dashboard. "aerobic" es el
# indicador global (suma de todas las disciplinas aeróbicas) y es el que
# alimenta la caja de riesgo; "impact" viene en el export pero no se usa acá.
ACWR_DISCIPLINAS = ("aerobic", "running", "cycling", "strength")

# Traducción del status para el prompt de Pulse. El modelo interpreta el
# status, nunca la cifra (ver reglas del system_prompt en generar_pulse).
ACWR_STATUS_ES = {
    "optimal":           "zona segura",
    "elevated":          "elevado",
    "high_risk":         "riesgo alto",
    "undertraining":     "baja carga",
    "insufficient_data": "datos insuficientes",
}


def _acwr_status_es(status):
    return ACWR_STATUS_ES.get(status, "sin datos")


def _round2(v):
    return round(v, 2) if v is not None else None


def procesar_acwr(acwr_raw, lunes_analizado, domingo_analizado):
    """
    El ACWR ya no se calcula localmente: viene del export de Azure como una
    lista de registros semana × disciplina (ver private/inputs/export_2.json
    para un ejemplo real), con carga en calorías (minutos para "strength"),
    no en minutos totales como el cálculo local que reemplaza. "aerobic" es
    el indicador global, tal como antes.

    Se descarta cualquier semana posterior a la última semana completa — el
    ACWR nunca se calcula ni se grafica sobre una semana en curso, mismo
    criterio que Pulse (ver ultima_semana_completa) — y la disciplina
    "impact" (no se muestra en el dashboard).

    Retorna {"valor", "status", "confiable", "semana", "series"}, donde
    "series" trae, por disciplina, la lista de semanas ya filtrada y
    ordenada (para la pestaña TENDENCIA). Si el export no trae bloque
    "acwr" (exports viejos, bootstrapeados desde el CSV de la retro), todo
    queda en None/vacío y el frontend lo maneja con su propio fallback.
    """
    if not acwr_raw:
        return {"valor": None, "status": None, "confiable": False, "semana": None, "series": {}}

    registros = [
        r for r in acwr_raw
        if r.get("activity_type") in ACWR_DISCIPLINAS
        and _date_de_iso_z(r["week_start_date"]) <= lunes_analizado
    ]

    series = {tipo: [] for tipo in ACWR_DISCIPLINAS}
    for r in sorted(registros, key=lambda r: r["week_start_date"]):
        series[r["activity_type"]].append({
            "weekStart": r["week_start_date"][:10],
            "weekEnd":   r["week_end_date"][:10],
            "valor":     _round2(r.get("acwr_value")),
            "status":    r.get("status"),
        })

    aerobic_actual = next(
        (r for r in registros
         if r["activity_type"] == "aerobic" and _date_de_iso_z(r["week_start_date"]) == lunes_analizado),
        None,
    )
    if not aerobic_actual:
        return {"valor": None, "status": None, "confiable": False, "semana": None, "series": series}

    return {
        "valor":     _round2(aerobic_actual.get("acwr_value")),
        "status":    aerobic_actual.get("status"),
        "confiable": bool(aerobic_actual.get("has_sufficient_history")),
        "semana":    f"{lunes_analizado.isoformat()}/{domingo_analizado.isoformat()}",
        "series":    series,
    }


# ── Resumen de disciplinas para Pulse ────────────────────────

def resumen_disciplinas(activities):
    """Genera un resumen textual de actividad por disciplina para el prompt."""
    por_tipo = defaultdict(list)
    for a in activities:
        por_tipo[a.get("type", "other")].append(a)

    lineas = []
    if por_tipo.get("running"):
        acts = por_tipo["running"]
        km = sum(a["dist_km"] for a in acts)
        lineas.append(f"Running: {len(acts)} sesiones, {km:.1f}km totales")

    if por_tipo.get("cycling"):
        acts = por_tipo["cycling"]
        km = sum(a["dist_km"] for a in acts)
        lineas.append(f"Ciclismo: {len(acts)} sesiones, {km:.1f}km totales")

    if por_tipo.get("swimming"):
        acts = por_tipo["swimming"]
        m = sum(a["dist_km"] * 1000 for a in acts)
        lineas.append(f"Natación: {len(acts)} sesiones, {m:.0f}m totales")

    if por_tipo.get("strength"):
        acts = por_tipo["strength"]
        mins = sum(a["duration_min"] for a in acts)
        lineas.append(f"Fuerza: {len(acts)} sesiones, {mins:.0f} min totales")

    return "\n".join(lineas) if lineas else "Solo running"


DIAS_SEMANA_ES = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")


def resumir_patron_semanal(activities, domingo_limite, semanas=8):
    """Resume la rutina real reciente para que el modelo no invente qué día
    ubicar fondos, fuerza o descansos. El "fondo" inferido es simplemente la
    salida más larga de cada semana con running; no intenta clasificar el
    propósito fisiológico de la sesión."""
    lunes_ventana = domingo_limite - timedelta(days=semanas * 7 - 1)
    seleccionadas = []
    for actividad in activities or []:
        try:
            fecha = date.fromisoformat((actividad.get("date") or "")[:10])
        except ValueError:
            continue
        if lunes_ventana <= fecha <= domingo_limite:
            seleccionadas.append((fecha, actividad))

    running_por_dia = [0] * 7
    fuerza_por_dia = [0] * 7
    running_por_semana = defaultdict(list)
    for fecha, actividad in seleccionadas:
        tipo = actividad.get("type")
        if tipo == "running":
            running_por_dia[fecha.weekday()] += 1
            lunes = fecha - timedelta(days=fecha.weekday())
            running_por_semana[lunes].append((fecha, actividad))
        elif tipo == "strength":
            fuerza_por_dia[fecha.weekday()] += 1

    fondo_por_dia = [0] * 7
    for sesiones in running_por_semana.values():
        fecha_fondo, _ = max(sesiones, key=lambda item: item[1].get("dist_km") or 0)
        fondo_por_dia[fecha_fondo.weekday()] += 1

    def formatear(conteos):
        partes = [f"{DIAS_SEMANA_ES[i]} {n}" for i, n in enumerate(conteos) if n]
        return ", ".join(partes) if partes else "sin sesiones"

    return (
        f"Ventana: {lunes_ventana.isoformat()} a {domingo_limite.isoformat()} ({semanas} semanas)\n"
        f"Running por día: {formatear(running_por_dia)}\n"
        f"Día de la salida más larga de cada semana: {formatear(fondo_por_dia)}\n"
        f"Fuerza por día: {formatear(fuerza_por_dia)}"
    )


def disponibilidad_declarada(meta, profile):
    """Lee preferencias si el export o el archivo --meta ya las incluye.
    Mientras no existan, el prompt deja claro que debe inferir la rutina."""
    preferencias = meta.get("planPreferences") or {}
    candidatos = (
        preferencias.get("disponibilidad"),
        preferencias.get("availableDays"),
        profile.get("training_availability"),
        profile.get("available_days"),
    )
    valor = next((v for v in candidatos if v), None)
    if isinstance(valor, (list, tuple)):
        return ", ".join(str(v) for v in valor)
    return str(valor) if valor else "No declarada; inferir del patrón histórico."


# ── Generar Pulse via Anthropic ───────────────────────────────

def generar_pulse(activities, weekly, meta, profile, acwr_info):
    """Llama a Anthropic para generar el análisis Pulse."""
    try:
        import anthropic
    except ImportError:
        print("  ⚠ anthropic no instalado. Usa: pip install anthropic")
        return None

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("  ⚠ ANTHROPIC_API_KEY no configurada. Pulse omitido.")
        return None

    client = anthropic.Anthropic(api_key=api_key)

    # Pulse SIEMPRE analiza la última semana COMPLETA (lunes a domingo). La
    # semana en curso puede existir en `activities`/`weekly` (los usa la
    # pestaña SEMANA para mostrar progreso en vivo) pero no debe llegar al
    # modelo bajo ningún concepto: ni en la semana analizada, ni en el
    # contexto histórico, ni como "última sesión".
    lunes_analizado, domingo_analizado = ultima_semana_completa()
    lunes_semana_actual = lunes_analizado + timedelta(days=7)
    semana_analizada_str = f"{lunes_analizado.isoformat()}/{domingo_analizado.isoformat()}"

    activities_cerradas = [a for a in activities if a.get("date", "9999") < lunes_semana_actual.isoformat()]
    weekly_cerrado       = [w for w in weekly if w["week"].split("/")[0] < lunes_semana_actual.isoformat()]

    # Solo running para métricas de pace/ACWR
    run_acts    = [a for a in activities_cerradas if a.get("type") == "running"]
    run_weekly  = [w for w in weekly_cerrado if w.get("total_km", 0) > 0 or w.get("running", {}).get("km", 0) > 0]

    last_week   = next((w for w in weekly_cerrado if w["week"] == semana_analizada_str), {})
    recent_run  = run_acts[-1] if run_acts else (activities_cerradas[-1] if activities_cerradas else {})

    # Estado de carga (ACWR) para el prompt: SOLO el status interpretado,
    # nunca la cifra — el valor exacto ya viene de procesar_acwr() y se
    # muestra en la caja del dashboard (ver ACWR_STATUS_ES más arriba).
    estado_carga = _acwr_status_es((acwr_info or {}).get("status"))
    desglose_disciplinas = []
    for tipo, nombre_es in (("running", "Running"), ("cycling", "Cycling"), ("strength", "Strength")):
        serie_tipo = (acwr_info or {}).get("series", {}).get(tipo) or []
        if serie_tipo:
            desglose_disciplinas.append(f"{nombre_es}: {_acwr_status_es(serie_tipo[-1].get('status'))}")
    desglose_carga_str = ", ".join(desglose_disciplinas) if desglose_disciplinas else "sin desglose por disciplina disponible"

    nombre   = meta.get("nombre", profile.get("full_name", "Atleta"))
    carrera  = meta.get("metaCarrera", {})
    resumen  = resumen_disciplinas(activities_cerradas)
    patron_semanal = resumir_patron_semanal(activities_cerradas, domingo_analizado)
    disponibilidad = disponibilidad_declarada(meta, profile)
    domingo_plan = lunes_semana_actual + timedelta(days=6)

    tiempo_objetivo = (
        carrera.get("tiempoObjetivo")
        or carrera.get("targetTime")
        or carrera.get("tiempo_objetivo")
    )

    # Tiempo restante hasta la carrera para el prompt: se precalcula acá,
    # nunca lo estima el modelo (mismo motivo que ACWR — un LLM calculando
    # diferencias de fecha a mano se equivoca, p.ej. confundió 115 días con
    # "16 meses"). Referencia: domingo de la semana analizada, no "hoy", para
    # que sea coherente con el resto del análisis (que tampoco conoce "hoy").
    tiene_meta = carrera.get("nombre") and carrera.get("nombre") != "¿Cuál es tu próxima carrera?"
    dias_restantes = None
    if tiene_meta and carrera.get("fecha"):
        try:
            fecha_carrera = date.fromisoformat(carrera["fecha"][:10])
            dias_restantes = (fecha_carrera - domingo_analizado).days
        except ValueError:
            dias_restantes = None

    # Proyección de tiempo de meta: Riegel + ajuste por eficiencia cardiaca,
    # precalculada acá, nunca estimada por el modelo (mismo motivo que
    # dias_restantes — un LLM "calculando" un tiempo de maratón a ojo puede
    # terminar incoherente con los PRs y la FC reales del atleta). Se
    # recalcula en cada corrida de Pulse porque la FC en la banda de
    # referencia cambia semana a semana con el entrenamiento.
    km_meta = _km_meta_de_label(carrera.get("label", "")) if tiene_meta else None
    proyeccion = proyectar_tiempo_carrera(
        profile.get("personal_records", []), km_meta, activities_cerradas,
        carrera.get("nombre", ""), domingo_analizado,
    )

    # PRs para contexto
    prs_str = ""
    prs = meta.get("prs", [])
    if prs:
        prs_str = "PRs registrados: " + ", ".join(f"{p['dist']} {p['mark']}" for p in prs)

    # Últimas 8 semanas de running
    ultimas_run_km = [w.get("running", {}).get("km", w.get("total_km", 0)) for w in weekly_cerrado[-8:]]

    rango_semana_es = fmt_rango_semana_es(lunes_analizado, domingo_analizado)

    system_prompt = f"""Eres Pulse, el motor de análisis semanal de Swetro.
Analizas la semana del {rango_semana_es}. Las actividades posteriores al domingo {fmt_fecha_es(domingo_analizado, False)} no existen para este análisis, aunque estén en los datos. Nunca menciones actividades de la semana en curso.
Generas análisis de entrenamiento personalizados para atletas que pueden practicar múltiples deportes.
Español latinoamericano. SIEMPRE en segunda persona dirigiéndote al atleta por su nombre — escribe "Juan, cerraste..." nunca "Juan cerró...".
Sin bullets en aiVerdict. Sin emojis en texto de análisis.
IMPORTANTE: Las métricas semanales de km y ACWR reflejan SOLO running. El atleta puede tener otras disciplinas que complementan su carga total. No interpretes semanas de bajo km de running como inactividad si hay otras disciplinas activas esa semana. Cuando calcules fatiga o recuperación, considera la carga total de todas las disciplinas.
El valor exacto de ACWR ya se muestra en la interfaz. NUNCA lo menciones con cifra en el texto. Si necesitas referirte a la carga, usa "tu carga está en zona segura" o "tu carga subió respecto a semanas anteriores", sin número específico.
El tiempo restante hasta la carrera ya está calculado y se muestra en la interfaz. NO lo calcules ni lo conviertas a meses o semanas. Si necesitas referirte al tiempo restante, usa la cifra exacta de días que viene en los datos.
La proyección de tiempo de meta (y su ritmo) ya viene calculada con los PRs reales del atleta y se muestra en la interfaz — no la recalcules, no la menciones con una cifra distinta a la que viene en los datos.
No confundas tres conceptos distintos: (1) el ritmo objetivo declarado por el atleta, (2) el ritmo de su proyección actual y (3) el ritmo prescrito para una sesión. Nunca llames "ritmo objetivo" al ritmo de la proyección. Si no existe un tiempo objetivo declarado, dilo internamente como "no disponible" y no lo inventes.
Esta regla aplica a TODA métrica que se muestre como número en una caja de la interfaz: ACWR, Pulse score, ritmo promedio, FC promedio, días restantes, proyección de meta. El texto interpreta, las cajas muestran los números. Nunca dupliques una cifra que ya está visible.
El weekPlan cubre EXACTAMENTE del lunes {lunes_semana_actual.isoformat()} al domingo {domingo_plan.isoformat()}, la semana inmediatamente posterior a la semana analizada. Incluye los siete días una sola vez y no saltes ninguna semana.
La disponibilidad declarada por el atleta tiene prioridad absoluta. Si no existe, conserva por defecto su patrón de las últimas 8 semanas: ubica el fondo, la fuerza y los descansos en sus días habituales. Solo cambia un día habitual cuando exista una razón concreta de carga o recuperación; explica esa razón brevemente en notes. No optimices el calendario ignorando la rutina real del atleta.
Responde ÚNICAMENTE con JSON válido, sin markdown, sin backticks."""

    user_prompt = f"""Genera análisis Pulse semanal.

ATLETA: {nombre}
{f"Meta: {carrera.get('nombre', '')} el {carrera.get('fecha', 'TBD')}" if tiene_meta else "Sin meta de carrera definida"}
{f"Faltan {dias_restantes} días para {carrera.get('nombre', '')}." if dias_restantes is not None else ""}
{f"Tu proyección para {carrera.get('nombre', '')} ({km_meta}km) ya calculada: {proyeccion['tiempo']} a {proyeccion['ritmo']}/km. {proyeccion['contexto']}" if proyeccion else ""}
{f"Tiempo objetivo declarado por el atleta: {tiempo_objetivo}." if tiempo_objetivo else "Tiempo objetivo declarado por el atleta: no disponible."}
{prs_str}

SEMANA ANALIZADA: {last_week.get('week', 'N/A')}
Running esta semana: {last_week.get('running', {}).get('km', last_week.get('total_km', 0))} km | {last_week.get('running', {}).get('sessions', last_week.get('sessions', 0))} sesiones | FC promedio running: {last_week.get('running', {}).get('avg_hr', last_week.get('avg_hr', 0))} bpm
Ciclismo esta semana: {last_week.get('cycling', {}).get('km', 0)} km | {last_week.get('cycling', {}).get('sessions', 0)} sesiones
Natación esta semana: {last_week.get('swimming', {}).get('metros', 0)} metros | {last_week.get('swimming', {}).get('sessions', 0)} sesiones
Fuerza esta semana: {last_week.get('strength', {}).get('minutos', 0)} min | {last_week.get('strength', {}).get('sessions', 0)} sesiones

ÚLTIMA SESIÓN DE RUNNING: {recent_run.get('name', 'N/A')} ({recent_run.get('date', 'N/A')})
{recent_run.get('dist_km', 0)}km | {recent_run.get('pace', 'N/A')}/km | {recent_run.get('hr', 0)}bpm

CONTEXTO HISTÓRICO ({len(activities_cerradas)} actividades totales):
{resumen}
Estado de tu carga (ACWR): {estado_carga}
Desglose de carga por disciplina: {desglose_carga_str}
Últimas 8 semanas (km running): {ultimas_run_km}

PATRÓN HABITUAL DE LAS ÚLTIMAS 8 SEMANAS:
{patron_semanal}
Disponibilidad declarada: {disponibilidad}

SEMANA QUE DEBES PLANIFICAR: {lunes_semana_actual.isoformat()}/{domingo_plan.isoformat()}

Responde con JSON: {{"semana":"rango fechas","score":0-100,"headline":"máx 8 palabras","subheadline":"máx 12 palabras","readiness":0-100,"aiVerdict":"párrafo 3-4 oraciones análisis longitudinal en segunda persona","strengths":["s1","s2","s3"],"warnings":["w1","w2"],"keyMetrics":[{{"label":"nombre","value":"valor","trend":"up|down|stable","status":"green|yellow|red","note":"nota corta"}}],"weekPlan":[{{"day":"Lun|Mar|Mié|Jue|Vie|Sáb|Dom","type":"tipo sesión","km":"X km o —","notes":"instrucción concreta"}}],"injuryRisk":{{"level":"low|medium|high","score":0-100,"topRisk":"zona anatómica","action":"acción concreta"}},"funFact":"dato curioso sobre su entrenamiento o null","seoulTip":null}}"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2500,
            temperature=0.3,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = response.content[0].text.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(text)
        # La proyección nunca viene del modelo — ver proyectar_tiempo_carrera().
        parsed["projection"] = proyeccion
        return parsed
    except Exception as e:
        print(f"  ✗ Error generando Pulse: {e}")
        return None


# ── Construir JSON final ──────────────────────────────────────

def transformar(input_data, meta_override=None, con_pulse=False):
    profile        = input_data.get("profile", {})
    raw_activities = input_data.get("activities", [])
    raw_challenges = input_data.get("challenges")

    meta_override  = meta_override or {}

    nombre       = profile.get("full_name", "Atleta")
    country_code = profile.get("country", "COL")

    meta_carrera = meta_override.get("metaCarrera", {
        "nombre":   "¿Cuál es tu próxima carrera?",
        "fecha":    "2027-01-01",
        "label":    "META",
    })
    meta_carrera["diasPrep"] = max(
        (date.fromisoformat(meta_carrera["fecha"]) - date.today()).days, 0
    )

    # ── Transformar actividades ──
    activities = [transformar_actividad(a) for a in raw_activities]
    activities = [a for a in activities if es_actividad_valida(a)]

    # ── Métricas semanales multidisciplina ──
    weekly = calcular_weekly_multidisciplina(activities)

    meta = {
        "userId":     str(profile.get("user_id", "u000")),
        "nombre":     nombre.upper(),
        "genero":     profile.get("gender", "N/A"),
        "edad":       profile.get("age", 0),
        "pais":       country_code,
        "avatar":     bandera_pais(country_code),
        "prs":        transformar_prs(profile.get("personal_records", [])),
        "metaCarrera": meta_carrera,
        "planPreferences": meta_override.get("planPreferences", {}),
        "isPro":      meta_override.get("isPro", True),
        "generadoEn": datetime.utcnow().isoformat() + "Z",
    }

    # ── Semana analizada por Pulse (última semana completa, lunes a domingo) ──
    lunes_analizado, domingo_analizado = ultima_semana_completa()
    semana_analizada = {
        "inicio": lunes_analizado.isoformat(),
        "fin":    domingo_analizado.isoformat(),
    }

    # ── ACWR (del export de Azure, ver procesar_acwr) ──
    acwr = procesar_acwr(input_data.get("acwr"), lunes_analizado, domingo_analizado)

    # ── Pulse ──
    pulse = None
    if con_pulse and activities and weekly:
        print("  → Generando Pulse...")
        pulse = generar_pulse(activities, weekly, meta, profile, acwr)
        if pulse:
            print(f"  ✓ Pulse generado (score: {pulse.get('score', '?')})")

    # ── Retos ──
    retos = []
    if raw_challenges:
        for c in raw_challenges:
            retos.append({
                "id":          c.get("id", 1),
                "name":        c.get("name", "Reto"),
                "org":         c.get("org", "SWETRO COMMUNITY"),
                "type":        c.get("type", "distancia"),
                "typeIcon":    "🏃",
                "goal":        c.get("goal", 0),
                "unit":        c.get("unit", "km"),
                "userProgress": c.get("user_progress", 0),
                "startDate":   c.get("start_date", ""),
                "endDate":     c.get("end_date", ""),
                "daysLeft":    c.get("days_left", 0),
                "totalDays":   c.get("total_days", 0),
                "participants": c.get("participants", 0),
                "status":      c.get("status", "activo"),
                "virtual":     True,
                "rank":        c.get("rank"),
                "totalRanked": c.get("total_ranked", 0),
                "prizeTiers":  [],
                "description": c.get("description", ""),
                "leaderboard": c.get("leaderboard", []),
            })

    return {
        "meta":            meta,
        "activities":      activities,
        "weekly":          weekly,
        "acwr":            acwr,
        "pulse":           pulse,
        "semanaAnalizada": semana_analizada,
        "taper":           [],
        "retos":           retos,
    }


# ── Entry point ───────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Transforma JSON del socio → formato dashboard Swetro"
    )
    parser.add_argument("input",         help="Archivo JSON de entrada")
    parser.add_argument("--output", "-o", help="Archivo de salida (default: public/data/<token>.json)")
    parser.add_argument("--con-pulse",   action="store_true", help="Generar Pulse via Anthropic API")
    parser.add_argument("--meta",  "-m", help="Archivo JSON con metaCarrera y otras configuraciones")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        input_data = json.load(f)

    meta_override = None
    if args.meta:
        with open(args.meta, "r", encoding="utf-8") as f:
            meta_override = json.load(f)

    nombre = input_data.get("profile", {}).get("full_name", "?")
    acts   = input_data.get("activities", [])
    print(f"→ Transformando: {args.input}")
    print(f"  Usuario: {nombre}")
    print(f"  Actividades: {len(acts)}")

    # Resumen de tipos
    from collections import Counter
    tipos = Counter(normalizar_tipo(a.get("activity_type","")) for a in acts)
    for t, n in sorted(tipos.items(), key=lambda x: -x[1]):
        print(f"    {t}: {n}")

    result  = transformar(input_data, meta_override, con_pulse=args.con_pulse)
    user_id = result["meta"]["userId"]
    token   = obtener_token(user_id)

    if args.output:
        output_path = args.output
    else:
        os.makedirs("public/data", exist_ok=True)
        output_path = f"public/data/{token}.json"

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))

    size_kb = os.path.getsize(output_path) // 1024
    print(f"  ✓ Guardado: {output_path} ({size_kb} KB)")
    print(f"  → Dashboard: ?u={token}")
    print(f"  → Semanas calculadas: {len(result['weekly'])}")


if __name__ == "__main__":
    main()
