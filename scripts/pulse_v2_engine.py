"""
Motor Pulse v2.1 para el piloto — vive enteramente bajo scripts/, no toca
transformar_json.py (que queda congelado como generar_pulse() v1, tal como
pide PULSE_contexto_maestro.md sección 46: "Congelar PULSE actual como v1 /
Crear PULSE v2 en paralelo").

Historial de correcciones (revisión manual sobre la primera corrida real de
Fabiana, 2026-09-01/02):
  - v1 de este motor: planning_constraints + prompt sin funFact/seoulTip/
    injuryRisk.score + validación de 12 checks + 1 reparación.
  - v2 de este motor (este archivo): corrige 7 problemas que la validación
    automática de v1 no detectaba — ver CHANGELOG_V2.1_FIXES abajo.

Nada de esto se llama para los diez usuarios restantes del piloto en esta
tarea — eso lo decide el runner (run_pulse_v2_pilot.py), no este módulo.
"""

import json
import re
from collections import defaultdict
from datetime import date, timedelta

CONSTRAINTS_VERSION = "pilot-v2.1-guardrails-v4"
PROMPT_VERSION = "pilot-v2.1-prompt-v4"

# Fix v3 (segunda revisión manual, sobre un candidato que SÍ pasó la
# validación v2 automática): neutralizar_dias_pasados() borraba la carga
# YA REALIZADA esta semana (Fabiana corrió lunes y martes, 13.5km reales)
# en vez de leerla del export y descontarla de lo que falta por prescribir.
# El modelo terminó prescribiendo la semana completa desde cero sobre un
# lunes/martes en blanco, resultando en ~40km/5 sesiones reales cuando el
# techo semanal es 33.9km/3 sesiones. Ver calcular_semana_en_curso() y
# calcular_restricciones_residuales() abajo.
# "carrera"/"race" quedan afuera a propósito: en este export son un
# rótulo genérico de actividad de salida (ej. "Lanús Carrera"), no
# evidencia de que la sesión fue una carrera competitiva o de calidad —
# incluirlas producía falsos positivos (ver test real de Fabiana).
HARD_SESSION_KEYWORDS = ("tempo", "serie", "interval", "intervalo", "fartlek", "umbral")

CHANGELOG_V2_1_FIXES = """
1. running_sessions -> running_sessions_max: es un TECHO, nunca un objetivo
   exacto. Prompt y validador aplican la misma regla (<=), no un "==".
2. fecha_generacion explícita: cualquier día del weeklyPlan anterior a esa
   fecha se neutraliza (no se prescribe retroactivamente), y summary se
   recalcula solo con los días restantes.
3. Una única fecha de referencia (fecha_generacion) para dias_restantes en
   planning_constraints, el prompt, y meta.metaCarrera.diasPrep (el runner
   sobreescribe diasPrep con el mismo valor después de tj.transformar()).
4. El ritmo proyectado nunca se etiqueta "ritmo objetivo" salvo que exista
   un tiempoObjetivo declarado por el atleta; debe decir "ritmo proyectado"
   y usarse de forma conservadora (más lento, no calcado).
5. Prohibido interpretar una cifra de FC como "zona aeróbica" / "esfuerzo
   controlado/sostenible" sin zonas personales en el input (no existen hoy).
6. injuryRisk.level admite "unknown". Si level="low", signal DEBE incluir
   una de las frases canónicas que reconocen la ausencia de datos de dolor
   o fatiga autorreportados (ausencia de evidencia != evidencia de ausencia).
7. Los PRs se filtran con detección de valores atípicos (consistencia
   cruzada vía Riegel contra la mediana del atleta) ANTES de entrar al
   prompt, no se confía en meta["prs"] de transformar_json.py tal cual.
8. validation_report.json ahora enumera TODOS los checks (pass y fail) con
   nombre, estado, evidencia y mensaje — no solo la lista de violaciones.
9. keyMetrics limitado a 3 elementos, exigido en prompt y validador.
"""

CHANGELOG_PROMPT_V4_COMPARISONS = """
Integración del Weekly Comparison Engine (calcular_comparaciones_pulse) al
prompt v2.1 -- experimento aislado, NO cambia Goal Readiness, Planning
Constraints, robust pace filtering, reconciliación de semana en curso,
residual constraints, PR filtering, race projection, Pulse Score, ni el
schema de salida.

1. generar_pulse_v2() calcula comparisons = calcular_comparaciones_pulse(
   activities_cerradas, weekly_cerrado, ...) -- solo semanas cerradas,
   igual criterio que el resto de la función -- y lo agrega a
   input_context["comparisons"] para auditoría completa.
2. construir_prompts_v2() recibe comparisons y lo serializa en texto
   compacto (_fmt_comparisons) dentro de COMPARACIONES SEMANALES en el
   user_prompt. Nunca expone samples_total/samples_used/samples_excluded
   (metadata de auditoría, no contenido para el atleta).
3. system_prompt: nueva regla central "Python calcula qué cambió, Claude
   interpreta qué significa" + reglas explícitas de multideporte (no
   convertir disciplinas a running-equivalente; running bajando con carga
   total estable/al alza no es "caída general de entrenamiento"), pace/HR
   (nunca "mejoró fitness"/"mejor eficiencia" solo por dirección de
   pace/HR), historial preliminar (evitar lenguaje de "patrón habitual").
   Estructura de insight dominante para aiVerdict (una sola idea, 3
   oraciones: qué cambió / qué significa / cómo conecta con la próxima
   semana) -- "semana estable" es una conclusión válida, no forzar insight.
4. Reducción de redundancia (sección 9 del pedido): se retiró el bloque
   "SEMANA ANALIZADA" (km/sesiones/FC por disciplina de la semana actual,
   ahora cubierto por comparisons con MÁS información -- dirección/
   tendencia) y la lista cruda "Últimas 8 semanas (km running)" (misma
   pregunta que comparisons ya responde con dirección calculada). No se
   tocó el texto de Planning Constraints (RESTRICCIONES DE LA SEMANA
   COMPLETA / RESTANTES) -- esa posible redundancia se identificó pero se
   dejó intacta deliberadamente, fuera del alcance de esta iteración.
"""

CHANGELOG_GUARDRAILS_V4_FIXES = """
Auditoría de PULSE v2 (iteración separada): calcular_planning_constraints()
tenía tres gaps y un detalle de recencia que la propia auditoría señaló sin
corregir. guardrails-v4 los corrige, SOLO dentro de esta función:

1. post_race caía en las reglas genéricas de base/build (85-110% del
   promedio, load_direction podía ser "increase"). Ahora es una fase propia,
   siempre conservadora (reduce, sin sesiones duras, fondo opcional), y
   acotada a POST_RACE_RECOVERY_WEEKS: pasada esa ventana, una meta vencida
   deja de gobernar la fase (se trata como "sin objetivo activo"), para no
   quedar en modo recuperación indefinidamente por una meta desactualizada.
2. is_preliminary (historial < 6 semanas) se calculaba pero no cambiaba
   nada. Ahora recorta el techo superior de running_km_range y
   long_run_range a 100% del promedio/fondo observado (nunca por encima),
   y baja el fallback sin datos de running_sessions_max de 3 a 2.
3. calcular_planning_constraints() ignoraba por completo cualquier
   disciplina distinta a running. Se agrega multisport_load (low/normal/
   high): compara la duración reciente de actividades NO-running contra el
   propio promedio histórico del atleta (nunca contra un umbral universal,
   nunca convertida a "km equivalentes" de running). Se usa solo como
   guardrail: si es "high", nunca permite load_direction="increase" y
   recorta el mismo techo superior que is_preliminary. ACWR por disciplina
   (cycling/strength), cuando trae status con historial suficiente, puede
   escalar (nunca degradar) el nivel un escalón.
4. longest_long_run usaba "las últimas 8 semanas CON running"
   (run_weekly[-8:]), no 8 semanas de calendario — un atleta que corre
   esporádicamente podía terminar con un fondo de referencia de varios
   meses atrás. Ahora es una ventana calendario fija anclada a la última
   semana cerrada.

NO se tocó: reconciliación de semana en curso, restricciones residuales,
validar_pulse_v2(), filtrar_prs_atipicos(), el prompt, el schema, ni las
reglas de base/build/taper/race_week/high_risk/elevated que ya existían
(salvo agregar post_race donde antes taper/race_week ya compartían la
misma regla).
"""

# Fixes de recencia y multideporte (CHANGELOG_GUARDRAILS_V4_FIXES arriba).
POST_RACE_RECOVERY_WEEKS = 3
VENTANA_FONDO_SEMANAS = 8
MULTISPORT_VENTANA_RECIENTE_SEMANAS = 4
MULTISPORT_VENTANA_BASELINE_SEMANAS = 8
MULTISPORT_FLOOR_MIN_SEMANA = 30.0
MULTISPORT_RATIO_ALTA = 1.3
MULTISPORT_RATIO_BAJA = 0.5

# ── Race status: detección de finalización de carrera (auditoría race_status) ──
#
# post_race NO puede inferirse solo de que la fecha de la meta ya pasó (caso
# real: Álvaro, Maratón de Sydney 2026-08-30, seis días sin ninguna actividad
# alrededor de esa fecha -- el motor lo marcaba post_race sin evidencia).
# calcular_race_status() exige evidencia determinística: sport running,
# categoría de distancia soportada, actividad dentro de una ventana de fecha
# acotada. Bandas de distancia = mismo precedente que transformar_json.py usa
# para elegir referencia de Riegel (RANGO_21K_KM=(19,30), MINIMO_42K_KM=39;
# _candidatos_referencia()) -- no se importa tj acá (mismo aislamiento que el
# resto de este archivo: "no toca transformar_json.py"), se replican los
# mismos números. Techo de 42K (46km, nuevo): auditado contra los 11 atletas
# piloto reales -- el registro más largo de todo el dataset es 42.62km
# (Álvaro, 2026-04-26, "Greenwich Carrera"); 46km da margen a un GPS largo
# real sin aceptar un ultra cercano a la fecha como si fuera la maratón meta.
# Solo 21K/42K tienen evidencia real en el dataset piloto -- cualquier otra
# categoría de distancia degrada a "no soportada" (nunca confirma
# finalización) en vez de inventar una banda ±10% genérica en esta iteración.
RACE_DISTANCE_BANDS_KM = {
    "21K": (19.0, 30.0),
    "42K": (39.0, 46.0),
}
# race_date - 1 .. race_date + 2: cubre fecha_iso_a_date() truncando UTC sin
# corrección de huso horario (verificado en transformar_json.py) más margen
# de sincronización tardía del dispositivo/plataforma tras la carrera.
RACE_EVIDENCE_DIAS_ANTES = 1
RACE_EVIDENCE_DIAS_DESPUES = 2

# Campos retirados del schema v1 (PULSE_actualizacion_contexto_maestro.md #6, #12)
FORBIDDEN_TOP_LEVEL_KEYS = ("funFact", "seoulTip", "weekPlan")
FORBIDDEN_INJURY_KEYS = ("score", "topRisk")
INJURY_LEVELS_VALIDOS = ("low", "medium", "high", "unknown")

# Frases canónicas que el prompt exige textualmente cuando injuryRisk.level
# es "low": reconocen que la ausencia de dolor/fatiga reportada no es lo
# mismo que confirmar que no existen. Determinístico por diseño: el modelo
# elige una de estas, el validador hace match exacto (sin ambigüedad NLP).
HEDGE_PHRASES_AUSENCIA_DATOS = (
    "no se cuenta con datos de dolor o fatiga autorreportados",
    "no hay datos de dolor o fatiga autorreportados",
    "sin datos de dolor o fatiga autorreportados",
    "no se reportaron datos de dolor o fatiga",
)

# Términos anatómicos/clínicos: si aparecen en texto libre (aiVerdict, notes,
# injuryRisk.action/signal) sin que exista evidencia estructurada en el
# input (no la hay: el export no trae lesiones/dolor declarado), se
# considera una inferencia médica no respaldada.
MEDICAL_TERMS = [
    "lesión", "lesionado", "lesionada", "diagnóstico", "diagnostica",
    "tendinitis", "tendinopatía", "fascitis", "fascia plantar", "tendón",
    "cartílago", "menisco", "sobreuso articular", "fractura por estrés",
    "periostitis", "síndrome de la cintilla", "esguince",
]

# Afirmaciones fisiológicas sobre FC que requieren zonas personales, que
# hoy no existen en el input. Si un campo de texto menciona una cifra de
# bpm Y alguno de estos fragmentos, es una inferencia no respaldada.
FRASES_ZONA_FC_NO_RESPALDADAS = [
    "zona aeróbica", "zona objetivo", "zona 2", "zona de quema de grasa",
    "esfuerzo controlado", "esfuerzo sostenible", "esfuerzo adecuado",
    "frecuencia cardiaca óptima", "frecuencia cardiaca ideal",
    "buena señal de esfuerzo",
]

REQUIRED_TOP_LEVEL_KEYS = (
    "semana", "score", "headline", "subheadline", "readiness", "aiVerdict",
    "strengths", "warnings", "keyMetrics", "weeklyPlan", "injuryRisk", "projection",
)

MAX_KEY_METRICS = 3
DIAS_ORDEN = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]


# ── 0. Filtro de PRs atípicos (antes de entrar al prompt) ────────────────

def filtrar_prs_atipicos(tj, personal_records):
    """
    Detección determinística de valores atípicos entre los PRs de un
    atleta ANTES de formatearlos para el prompt. Causa raíz observada con
    Fabiana: personal_records mezcla varias fuentes/dispositivos por
    record_type, y transformar_prs() en transformar_json.py toma
    ciegamente el rank=1 de la primera fuente que encuentra — en su caso
    un 1K de 2:53 (probable interval/lap mal detectado o GPS corrupto)
    frente a un 5K real de 30:04 (ritmo ~6:00/km), mutuamente incompatibles.

    Método: para cada candidato (running + treadmill_running, walking se
    excluye por no ser comparable), calcula un "5K equivalente" vía Riegel.
    La mediana de todos los equivalentes es el ancla robusta del nivel real
    del atleta. Para cada distancia, se conserva el candidato MÁS RÁPIDO
    cuyo equivalente cae dentro de una banda de tolerancia [0.65x, 1.45x]
    de la mediana; si ninguno de esa distancia pasa, esa distancia queda
    sin PR (mejor no mostrar nada que mostrar un valor incoherente).

    Devuelve (prs_limpios, descartados) — prs_limpios tiene el mismo shape
    que meta["prs"] ({"dist","mark"}); descartados es una lista de motivos
    para trazabilidad (se guarda en input_context.json).
    """
    ACTIVITY_TYPES_VALIDOS = ("running", "treadmill_running")
    candidatos = []
    for pr in personal_records or []:
        rt = pr.get("record_type")
        km = tj.DISTANCIAS_PR_KM.get(rt)
        val = pr.get("value")
        if not km or not val or val <= 0:
            continue
        if pr.get("activity_type") not in ACTIVITY_TYPES_VALIDOS:
            continue
        pace_min_km = val / 60.0 / km
        if pace_min_km < tj.PACE_MIN_VALIDO_MIN_KM:
            continue  # piso absoluto de plausibilidad (GPS corrupto), ya existente en transformar_json.py
        equiv_5k = val * (5.0 / km) ** 1.06
        candidatos.append({"record_type": rt, "value": val, "activity_type": pr.get("activity_type"),
                            "equiv_5k": equiv_5k})

    descartados = []
    if len(candidatos) < 3:
        # Muestra insuficiente para una mediana robusta: no se filtra, solo
        # se aplicó el piso de plausibilidad de arriba.
        pool = candidatos
    else:
        equivalentes = sorted(c["equiv_5k"] for c in candidatos)
        n = len(equivalentes)
        mediana = (equivalentes[n // 2] if n % 2 else (equivalentes[n // 2 - 1] + equivalentes[n // 2]) / 2)
        lo, hi = mediana * 0.65, mediana * 1.45
        pool = []
        for c in candidatos:
            if lo <= c["equiv_5k"] <= hi:
                pool.append(c)
            else:
                descartados.append({
                    "record_type": c["record_type"], "activity_type": c["activity_type"],
                    "value_seconds": c["value"],
                    "motivo": f"equivalente_5k={c['equiv_5k']:.0f}s fuera de banda [{lo:.0f},{hi:.0f}]s "
                              f"(mediana atleta={mediana:.0f}s) — inconsistente con el resto de sus PRs",
                })

    mejores = {}
    for c in sorted(pool, key=lambda c: c["value"]):
        if c["record_type"] not in mejores:
            mejores[c["record_type"]] = c

    prs_limpios = []
    for rt in ("1K", "5K", "10K", "21K", "42K"):
        if rt in mejores:
            prs_limpios.append({"dist": rt, "mark": tj.segundos_a_tiempo(mejores[rt]["value"])})

    return prs_limpios, descartados


# ── 1. Planning constraints (determinístico, sin LLM) ───────────────────

def _minutos_no_running_en_rango(activities, inicio, fin):
    """Suma duration_min de actividades NO running dentro de [inicio, fin]
    (fechas inclusive). Nunca incluye running: esta señal es exclusivamente
    sobre otras disciplinas, para no volver a contar la misma carga que ya
    gobierna avg4_km/avg4_sessions (ver _calcular_multisport_load)."""
    total = 0.0
    for a in activities or []:
        if a.get("type") == "running":
            continue
        fecha_str = a.get("date")
        if not fecha_str:
            continue
        try:
            fecha = date.fromisoformat(fecha_str)
        except ValueError:
            continue
        if inicio <= fecha <= fin:
            total += a.get("duration_min") or 0
    return total


def _calcular_multisport_load(activities_cerradas, domingo_cierre, acwr_info):
    """
    Señal cualitativa (low/normal/high) de carga reciente en disciplinas
    DISTINTAS a running, comparada contra el propio promedio histórico del
    atleta — nunca contra un umbral universal, y nunca convertida a "km
    equivalentes de running" (no tenemos una fórmula validada para eso).

    Ventanas calendario contiguas y ancladas a domingo_cierre (la última
    semana ya cerrada): MULTISPORT_VENTANA_RECIENTE_SEMANAS (4) semanas
    recientes vs. las MULTISPORT_VENTANA_BASELINE_SEMANAS (8) semanas
    inmediatamente anteriores. Ambos promedios dividen por el tamaño FIJO
    de su ventana, no por "semanas con datos" — una semana sin actividad
    no-running cuenta como 0, no se ignora.

    ACWR por disciplina (cycling/strength) es una señal ADICIONAL, nunca la
    única fuente: si su status más reciente es "elevated"/"high_risk" (con
    historial suficiente — "insufficient_data" no corrobora nada), puede
    escalar el nivel un escalón, nunca degradarlo.

    Devuelve un dict con el nivel y los números crudos, para trazabilidad.
    """
    reciente_fin = domingo_cierre
    reciente_inicio = reciente_fin - timedelta(weeks=MULTISPORT_VENTANA_RECIENTE_SEMANAS) + timedelta(days=1)
    baseline_fin = reciente_inicio - timedelta(days=1)
    baseline_inicio = baseline_fin - timedelta(weeks=MULTISPORT_VENTANA_BASELINE_SEMANAS) + timedelta(days=1)

    avg_reciente = _minutos_no_running_en_rango(activities_cerradas, reciente_inicio, reciente_fin) / MULTISPORT_VENTANA_RECIENTE_SEMANAS
    avg_baseline = _minutos_no_running_en_rango(activities_cerradas, baseline_inicio, baseline_fin) / MULTISPORT_VENTANA_BASELINE_SEMANAS

    if avg_baseline < MULTISPORT_FLOOR_MIN_SEMANA:
        # Sin historial propio de otras disciplinas para comparar: no se
        # puede afirmar "más que su propio promedio" sin promedio. Si
        # tampoco hay nada reciente, es sencillamente un atleta que solo
        # corre (el caso más común, debe quedar en "low" = sin efecto).
        nivel = "low" if avg_reciente < MULTISPORT_FLOOR_MIN_SEMANA else "normal"
    else:
        ratio = avg_reciente / avg_baseline
        if ratio >= MULTISPORT_RATIO_ALTA and avg_reciente >= MULTISPORT_FLOOR_MIN_SEMANA:
            nivel = "high"
        elif ratio <= MULTISPORT_RATIO_BAJA:
            nivel = "low"
        else:
            nivel = "normal"

    series = (acwr_info or {}).get("series") or {}
    corrobora_alta = any(
        serie[-1].get("status") in ("elevated", "high_risk")
        for disciplina in ("cycling", "strength")
        for serie in [series.get(disciplina) or []]
        if serie
    )
    if corrobora_alta:
        if nivel == "low":
            nivel = "normal"
        elif nivel == "normal":
            nivel = "high"

    return {
        "nivel": nivel,
        "avg_reciente_min_semana": round(avg_reciente, 1),
        "avg_baseline_min_semana": round(avg_baseline, 1),
        "acwr_corrobora_alta": corrobora_alta,
    }


def _categoria_distancia_meta(label):
    """
    "42K SYD" -> "42K"; "21K MIA" -> "21K"; cualquier otra distancia (sin
    evidencia real en el dataset piloto) -> None, "categoría no soportada".
    Decisión de producto explícita: nada de banda ±10% genérica para
    distancias arbitrarias en esta iteración -- ver RACE_DISTANCE_BANDS_KM.
    """
    m = re.match(r"(\d+)K", label or "")
    if not m:
        return None
    categoria = f"{m.group(1)}K"
    return categoria if categoria in RACE_DISTANCE_BANDS_KM else None


def calcular_race_status(activities, carrera, tiene_meta, fecha_generacion,
                          lunes_semana_actual, domingo_plan):
    """
    Determina si hay evidencia defendible de que la carrera meta ocurrió,
    sin inferir finalización solo porque la fecha ya pasó (ver auditoría:
    Álvaro tenía goal_phase="post_race" con seis días sin ninguna actividad
    alrededor de la fecha de su maratón). Determinístico y conservador:
    sport running, categoría de distancia soportada (RACE_DISTANCE_BANDS_KM),
    ventana de fecha acotada (RACE_EVIDENCE_DIAS_ANTES/DESPUES). El NOMBRE de
    la actividad NUNCA es evidencia (rótulo libre del atleta/dispositivo --
    ver el comentario sobre HARD_SESSION_KEYWORDS más arriba: "carrera" en el
    nombre no prueba que la sesión fue una carrera competitiva real).

    `activities` es el export COMPLETO (no activities_cerradas): la ventana
    de evidencia de una carrera es un concepto de la META, no de la semana
    analizada -- puede caer dentro de la semana en curso o incluso dentro de
    la semana a planificar (ver race_falls_in_planning_week, caso William).

    state ∈ {"no_goal", "scheduled", "confirmed_completed", "unconfirmed_after_date"}.
    goal_phase (calcular_planning_constraints) es un concepto separado: la
    fase de entrenamiento. race_status solo responde "¿qué evidencia hay de
    que la carrera meta ocurrió?".
    """
    fecha_carrera = None
    if tiene_meta and carrera.get("fecha"):
        try:
            fecha_carrera = date.fromisoformat(carrera["fecha"][:10])
        except ValueError:
            fecha_carrera = None

    if not tiene_meta or fecha_carrera is None:
        return {
            "state": "no_goal", "race_date": None, "dias_restantes": None,
            "race_falls_in_planning_week": False, "race_planning_day": None,
            "evidence": {"checked": False, "window": None, "distance_band_km": None,
                         "matched_activity": None, "reason": "sin_meta_activa"},
        }

    dias_restantes = (fecha_carrera - fecha_generacion).days
    race_falls_in_planning_week = lunes_semana_actual <= fecha_carrera <= domingo_plan
    race_planning_day = (DIAS_ORDEN[(fecha_carrera - lunes_semana_actual).days]
                          if race_falls_in_planning_week else None)

    if fecha_carrera >= fecha_generacion:
        return {
            "state": "scheduled", "race_date": fecha_carrera.isoformat(),
            "dias_restantes": dias_restantes,
            "race_falls_in_planning_week": race_falls_in_planning_week,
            "race_planning_day": race_planning_day,
            "evidence": {"checked": False, "window": None, "distance_band_km": None,
                         "matched_activity": None, "reason": "fecha_no_ha_ocurrido"},
        }

    # Fecha ya pasó: buscar evidencia. Categoría soportada primero -- sin
    # ella nunca se puede confirmar finalización (degrada a unconfirmed,
    # nunca a confirmed_completed "por defecto").
    categoria = _categoria_distancia_meta(carrera.get("label", ""))
    ventana_inicio = fecha_carrera - timedelta(days=RACE_EVIDENCE_DIAS_ANTES)
    ventana_fin = fecha_carrera + timedelta(days=RACE_EVIDENCE_DIAS_DESPUES)
    evidence = {
        "checked": True,
        "window": [ventana_inicio.isoformat(), ventana_fin.isoformat()],
        "distance_band_km": list(RACE_DISTANCE_BANDS_KM[categoria]) if categoria else None,
        "matched_activity": None,
    }

    if categoria is None:
        evidence["reason"] = "categoria_de_distancia_no_soportada"
        return {
            "state": "unconfirmed_after_date", "race_date": fecha_carrera.isoformat(),
            "dias_restantes": dias_restantes,
            "race_falls_in_planning_week": race_falls_in_planning_week,
            "race_planning_day": race_planning_day,
            "evidence": evidence,
        }

    lo, hi = RACE_DISTANCE_BANDS_KM[categoria]
    candidatos = []
    for a in activities or []:
        if a.get("type") != "running" or not a.get("date"):
            continue
        try:
            fecha_act = date.fromisoformat(a["date"])
        except ValueError:
            continue
        if not (ventana_inicio <= fecha_act <= ventana_fin):
            continue
        if lo <= (a.get("dist_km") or 0) <= hi:
            candidatos.append(a)

    if candidatos:
        mejor = max(candidatos, key=lambda a: a.get("dist_km", 0))
        evidence["matched_activity"] = {
            "date": mejor.get("date"), "name": mejor.get("name"),
            "dist_km": mejor.get("dist_km"), "type": mejor.get("type"),
        }
        evidence["reason"] = "actividad_dentro_de_ventana_y_banda"
        state = "confirmed_completed"
    else:
        evidence["reason"] = "sin_actividad_en_ventana_y_banda"
        state = "unconfirmed_after_date"

    return {
        "state": state, "race_date": fecha_carrera.isoformat(),
        "dias_restantes": dias_restantes,
        "race_falls_in_planning_week": race_falls_in_planning_week,
        "race_planning_day": race_planning_day,
        "evidence": evidence,
    }


def calcular_planning_constraints(activities_cerradas, weekly_cerrado, acwr_info,
                                   meta_carrera, tiene_meta, fecha_generacion, race_status=None):
    """
    Guardrails conservadores (PULSE_contexto_maestro.md #20-21 y
    PULSE_actualizacion_contexto_maestro.md #5; guardrails-v4 corrige los
    gaps de post_race/historial preliminar/multideporte/recencia del fondo
    encontrados en la auditoría — ver CHANGELOG_GUARDRAILS_V4_FIXES).
    Devuelve límites que el prompt recibe como restricciones duras y que
    validar_pulse_v2() vuelve a comprobar sobre la salida real del modelo.

    fecha_generacion es la ÚNICA fecha de referencia para todo cálculo de
    "días restantes" / fase — antes este motor usaba domingo_analizado (el
    cierre de la semana analizada), que no coincidía con
    meta.metaCarrera.diasPrep de transformar_json.py (que usa date.today()).
    Ahora ambos deben recibir la misma fecha_generacion (ver run_pulse_v2_pilot.py).

    Fases respecto al objetivo (actualizacion #4, adaptado con dos
    subniveles de afinamiento porque "menos de 8 semanas" es demasiado
    ancho para separar semana de carrera de una semana de recorte normal):
      >16 sem: base · 8-16 sem: build · 1-8 sem: taper · <=1 sem: race_week
      · <0 sem (dentro de POST_RACE_RECOVERY_WEEKS): post_race · más allá: None
    """
    run_weekly = [w for w in weekly_cerrado if w.get("total_km", 0) > 0]
    last4 = run_weekly[-4:]
    avg4_km = round(sum(w["total_km"] for w in last4) / len(last4), 1) if last4 else 0.0
    avg4_sessions = round(sum(w.get("sessions", 0) for w in last4) / len(last4)) if last4 else 0
    history_weeks = len(run_weekly)
    is_preliminary = history_weeks < 6
    history_quality = "preliminary" if is_preliminary else "sufficient"

    # Ancla calendario para las ventanas de recencia (fondo largo y
    # multideporte, fixes #3/#4): el domingo de la última semana YA
    # cerrada respecto a fecha_generacion — mismo cálculo que
    # tj.ultima_semana_completa(hoy=fecha_generacion), reescrito acá para
    # no acoplar esta función al módulo tj.
    lunes_semana_actual = fecha_generacion - timedelta(days=fecha_generacion.weekday())
    domingo_cierre = lunes_semana_actual - timedelta(days=1)

    # Fix #4: ventana CALENDARIO de 8 semanas cerradas, no "las últimas 8
    # semanas CON running" — un atleta que corre esporádicamente podía
    # terminar usando un fondo de varios meses atrás como referencia actual.
    inicio_ventana_fondo = domingo_cierre - timedelta(weeks=VENTANA_FONDO_SEMANAS) + timedelta(days=1)
    running_ventana_fondo = [
        a for a in activities_cerradas
        if a.get("type") == "running" and a.get("date")
        and inicio_ventana_fondo <= date.fromisoformat(a["date"]) <= domingo_cierre
    ]
    longest_long_run = round(max((a.get("dist_km", 0) for a in running_ventana_fondo), default=0.0), 1)

    # Fix #3: señal multideporte, calculada siempre (barata, determinística),
    # usada más abajo solo como guardrail sobre volumen/dirección de carga.
    multisport = _calcular_multisport_load(activities_cerradas, domingo_cierre, acwr_info)

    dias_restantes = None
    weeks_restantes = None
    if tiene_meta and meta_carrera.get("fecha"):
        try:
            fecha_carrera = date.fromisoformat(meta_carrera["fecha"][:10])
            dias_restantes = (fecha_carrera - fecha_generacion).days
            weeks_restantes = dias_restantes / 7
        except ValueError:
            pass

    if not tiene_meta or weeks_restantes is None:
        goal_phase = None
    elif weeks_restantes < 0:
        # Fix #1: post_race es una ventana de recuperación ACOTADA, no un
        # estado indefinido. Pasadas POST_RACE_RECOVERY_WEEKS desde la
        # carrera, una meta ya vencida deja de gobernar la fase — se trata
        # igual que "sin objetivo activo" en vez de seguir forzando reglas
        # de recuperación semanas o meses después.
        # race_status (auditoría race_status): post_race ya NO se infiere
        # solo de que la fecha pasó -- requiere evidencia confirmada de
        # calcular_race_status(). Sin confirmación, "race_unconfirmed"
        # (nunca asume que la carrera ocurrió; ver decisión de producto #2).
        if weeks_restantes < -POST_RACE_RECOVERY_WEEKS:
            goal_phase = None
        elif (race_status or {}).get("state") == "confirmed_completed":
            goal_phase = "post_race"
        else:
            goal_phase = "race_unconfirmed"
    elif weeks_restantes <= 1:
        goal_phase = "race_week"
    elif weeks_restantes < 8:
        goal_phase = "taper"
    elif weeks_restantes <= 16:
        goal_phase = "build"
    else:
        goal_phase = "base"

    status = (acwr_info or {}).get("status")

    # ── volumen semanal de running ──
    if avg4_km <= 0:
        running_km_range, volume_cap_reason = None, "no_recent_history"
    elif goal_phase == "race_week":
        running_km_range = [round(avg4_km * 0.15, 1), round(avg4_km * 0.30, 1)]
        volume_cap_reason = "race_week_taper"
    elif goal_phase == "post_race":
        # Fix #1: más reducido que taper — la fatiga/daño post-carrera no
        # es lo mismo que un afinamiento pre-carrera deliberado. Nunca
        # depende del ACWR (que suele marcar "undertraining" justo después
        # de una carrera, lo que antes disparaba "increase").
        running_km_range = [round(avg4_km * 0.30, 1), round(avg4_km * 0.50, 1)]
        volume_cap_reason = "post_race_recovery"
    elif goal_phase == "race_unconfirmed":
        # Decisión de producto #3: conservador, pero SIN piso agresivo -- no
        # sabemos si la carrera ocurrió, así que el plan debe poder
        # recomendar cero running adicional si corresponde (piso 0, a
        # diferencia de post_race que sí asume fatiga real de haber
        # corrido). Techo igual de conservador que post_race.
        running_km_range = [0.0, round(avg4_km * 0.50, 1)]
        volume_cap_reason = "race_unconfirmed_conservative"
    elif goal_phase == "taper":
        running_km_range = [round(avg4_km * 0.55, 1), round(avg4_km * 0.75, 1)]
        volume_cap_reason = "taper_phase"
    elif status in ("elevated", "high_risk"):
        running_km_range = [round(avg4_km * 0.85, 1), avg4_km]
        volume_cap_reason = "acwr_" + status
    else:
        # Fixes #2/#3: con historial preliminar o carga multideporte
        # claramente alta, no se ofrece el 10% de margen de crecimiento —
        # el techo se queda en el 100% del promedio observado.
        razones_techo = []
        factor_superior = 1.10
        if is_preliminary:
            factor_superior = 1.0
            razones_techo.append("preliminary_history")
        if multisport["nivel"] == "high":
            factor_superior = min(factor_superior, 1.0)
            razones_techo.append("multisport_load_high")
        running_km_range = [round(avg4_km * 0.85, 1), round(avg4_km * factor_superior, 1)]
        volume_cap_reason = "+".join(razones_techo) if razones_techo else "recent_four_week_average"

    # ── fondo largo ──
    if longest_long_run <= 0:
        long_run_range, long_run_cap_reason = None, "no_recent_long_run"
    elif goal_phase == "race_week":
        long_run_range = [0.0, round(min(longest_long_run * 0.2, 8.0), 1)]
        long_run_cap_reason = "race_week_taper"
    elif goal_phase == "post_race":
        # Fix #1: fondo largo NO obligatorio (piso 0), techo absoluto de
        # 10km para que un fondo reciente muy largo (p. ej. la carrera
        # misma) no infle el permiso.
        long_run_range = [0.0, round(min(longest_long_run * 0.3, 10.0), 1)]
        long_run_cap_reason = "post_race_recovery"
    elif goal_phase == "race_unconfirmed":
        # Mismo piso 0 / mismo techo absoluto que post_race: si la carrera sí
        # ocurrió sin confirmarse, seguimos protegiendo contra fatiga real.
        long_run_range = [0.0, round(min(longest_long_run * 0.3, 10.0), 1)]
        long_run_cap_reason = "race_unconfirmed_conservative"
    elif goal_phase == "taper":
        long_run_range = [round(longest_long_run * 0.4, 1), round(longest_long_run * 0.65, 1)]
        long_run_cap_reason = "taper_phase"
    elif is_preliminary:
        # Fix #2: sin el 20% de margen de crecimiento sobre un fondo con
        # poco respaldo histórico.
        long_run_range = [round(longest_long_run * 0.8, 1), longest_long_run]
        long_run_cap_reason = "preliminary_history"
    else:
        long_run_range = [round(longest_long_run * 0.8, 1), round(longest_long_run * 1.20, 1)]
        long_run_cap_reason = "recent_eight_week_history"

    # ── dirección de carga (actualizacion #5: elevated/high_risk nunca "increase") ──
    if goal_phase in ("taper", "race_week", "post_race", "race_unconfirmed"):
        load_direction = "reduce"
    elif status == "high_risk":
        load_direction = "reduce"
    elif status == "elevated":
        load_direction = "maintain"
    elif status == "undertraining" or avg4_km <= 0:
        load_direction = "increase"
    else:
        load_direction = "maintain"

    # Fix #3: carga multideporte claramente alta nunca permite "increase"
    # en running — el atleta puede no estar "undertraining" en absoluto,
    # solo entrenando mucho en otra disciplina que el ACWR de running no ve.
    if multisport["nivel"] == "high" and load_direction == "increase":
        load_direction = "maintain"

    # ── sesiones duras / consecutivos / descanso mínimo ──
    if goal_phase in ("race_week", "post_race", "race_unconfirmed"):
        hard_sessions_max, recovery_days_min, max_consecutive_running_days = 0, 3, 1
    elif goal_phase == "taper":
        hard_sessions_max, recovery_days_min, max_consecutive_running_days = 1, 2, 2
    elif status == "high_risk":
        hard_sessions_max, recovery_days_min, max_consecutive_running_days = 1, 3, 2
    else:
        hard_sessions_max, recovery_days_min, max_consecutive_running_days = 2, 2, 3

    # running_sessions_max es un TECHO (fix #1 de guardrails-v3): nunca un
    # número exacto que el modelo deba calzar sí o sí. min(3, ...) porque
    # en taper/race_week/post_race/race_unconfirmed no tiene sentido pedir
    # más de 3 salidas de running en la semana.
    if goal_phase in ("taper", "race_week", "post_race", "race_unconfirmed"):
        running_sessions_max = min(3, avg4_sessions) if avg4_sessions else 2
    elif is_preliminary:
        # Fix #2: sin ninguna sesión observada, el fallback conservador es
        # 2, no 3 — no hay patrón propio que respalde un valor mayor.
        running_sessions_max = avg4_sessions or 2
    else:
        running_sessions_max = avg4_sessions or 3

    stimuli_por_fase = {
        "race_week":  ["easy", "rest"],
        "taper":      ["easy", "easy", "short_quality", "rest", "short_long_run"],
        "build":      ["easy", "tempo", "easy", "long_run"],
        "base":       ["easy", "easy", "strength", "long_run"],
        None:         ["easy", "easy", "long_run"],
        "post_race":  ["easy", "rest"],
        "race_unconfirmed": ["easy", "rest"],
    }

    return {
        "constraints_version": CONSTRAINTS_VERSION,
        "fecha_generacion": fecha_generacion.isoformat(),
        "goal_phase": goal_phase,
        "dias_restantes": dias_restantes,
        "load_direction": load_direction,
        "running_km_range": running_km_range,
        "long_run_range": long_run_range,
        "running_sessions_max": running_sessions_max,
        "hard_sessions_max": hard_sessions_max,
        "recovery_days_min": recovery_days_min,
        "max_consecutive_running_days": max_consecutive_running_days,
        "recommended_stimuli": stimuli_por_fase.get(goal_phase, stimuli_por_fase[None]),
        "history_weeks_running": history_weeks,
        "history_quality": history_quality,
        "is_preliminary": is_preliminary,
        "avg4_km": avg4_km,
        "longest_long_run_8w": longest_long_run,
        "acwr_status": status,
        "volume_cap_reason": volume_cap_reason,
        "long_run_cap_reason": long_run_cap_reason,
        "multisport_load": multisport["nivel"],
        "multisport_signal": multisport,
    }


# ── 1b. Semana en curso: completado vs. restante (fix v3) ────────────

def _es_sesion_dura_por_nombre(actividades_del_dia):
    """Evidencia explícita y determinística de sesión dura: el propio
    nombre de la actividad (dado por el atleta/dispositivo) contiene una
    palabra de intensidad. No se infiere nada que no esté en el dato."""
    for a in actividades_del_dia:
        if a.get("type") != "running":
            continue
        nombre = (a.get("name") or "").lower()
        if any(kw in nombre for kw in HARD_SESSION_KEYWORDS):
            return True
    return False


def calcular_semana_en_curso(activities, lunes_semana_actual, domingo_plan, fecha_generacion):
    """
    Lee del export TODAS las actividades reales ya ocurridas esta semana,
    desde lunes_semana_actual hasta (sin incluir) fecha_generacion — nunca
    mezcladas con activities_cerradas (la semana cerrada que usa el
    análisis de Pulse; ver punto 2 del pedido de corrección).

    Devuelve, por día, o bien la actividad real (tipo "completado") o la
    ausencia explícita de actividad ("sin_actividad") — nunca una fecha sin
    resolver. Los días >= fecha_generacion no aparecen en dias: todavía no
    han pasado, no hay nada que leer.
    """
    dias = {}
    cur = lunes_semana_actual
    while cur <= domingo_plan and cur < fecha_generacion:
        fecha_str = cur.isoformat()
        acts_del_dia = [a for a in activities if a.get("date") == fecha_str]
        if acts_del_dia:
            running_km = round(sum(a.get("dist_km", 0) for a in acts_del_dia if a.get("type") == "running"), 2)
            dias[fecha_str] = {
                "estado": "completado",
                "running_km": running_km,
                "tipos": sorted({a.get("type") for a in acts_del_dia}),
                "nombres": [a.get("name") for a in acts_del_dia],
                "es_sesion_dura": _es_sesion_dura_por_nombre(acts_del_dia),
            }
        else:
            dias[fecha_str] = {"estado": "sin_actividad", "running_km": 0.0, "tipos": [], "nombres": [], "es_sesion_dura": False}
        cur += timedelta(days=1)

    completed_km = round(sum(d["running_km"] for d in dias.values()), 2)
    completed_running_sessions = sum(1 for d in dias.values() if d["running_km"] > 0)
    completed_hard_sessions = sum(1 for d in dias.values() if d["es_sesion_dura"])

    return {
        "dias": dias,
        "completed_km": completed_km,
        "completed_running_sessions": completed_running_sessions,
        "completed_hard_sessions": completed_hard_sessions,
    }


def calcular_restricciones_residuales(constraints, semana_en_curso):
    """
    remaining_* = lo que le queda disponible al modelo para prescribir,
    después de descontar lo que Fabiana (o cualquier atleta) ya corrió esta
    semana. Es el límite DURO que va al prompt — constraints (la semana
    completa) queda solo como contexto informativo.
    """
    completed_km = semana_en_curso["completed_km"]
    completed_sessions = semana_en_curso["completed_running_sessions"]
    completed_hard = semana_en_curso["completed_hard_sessions"]

    if constraints.get("running_km_range"):
        lo, hi = constraints["running_km_range"]
        remaining_km_range = [round(max(0.0, lo - completed_km), 1), round(max(0.0, hi - completed_km), 1)]
    else:
        remaining_km_range = None

    remaining_sessions_max = max(0, (constraints.get("running_sessions_max") or 0) - completed_sessions)
    remaining_hard_sessions_max = max(0, (constraints.get("hard_sessions_max") or 0) - completed_hard)

    return {
        "completed_km": completed_km,
        "completed_running_sessions": completed_sessions,
        "completed_hard_sessions": completed_hard,
        "remaining_km_range": remaining_km_range,
        "remaining_sessions_max": remaining_sessions_max,
        "remaining_hard_sessions_max": remaining_hard_sessions_max,
    }


# ── 1c. Weekly Comparison Engine (determinístico, NO integrado al prompt) ──
#
# calcular_comparaciones_pulse() responde "¿qué cambió esta semana respecto
# a lo que este atleta venía haciendo?" en números crudos -- Python calcula,
# Claude interpretará (en una iteración futura). Esta función NO se llama
# todavía desde construir_prompts_v2() ni generar_pulse_v2() (ver tarea:
# "no integres todavía"). Existe y está probada, nada más.
#
# Fuente de datos: weekly_cerrado para lo que ya agrega bien (km/metros/
# sesiones por disciplina) y activities_cerradas para lo que weekly[] no
# tiene granularidad para dar sin inventar (pace/HR con exclusión de
# valores inválidos, minutos totales multideporte, días activos). NUNCA
# metrics_week de Azure (no está presente en todos los exports).
#
# Ventanas SIEMPRE calendario, nunca "semanas con actividad" (mismo
# criterio que el fix #4 de guardrails-v4 en Planning Constraints, pero
# implementado independientemente acá -- ver nota de is_preliminary abajo
# sobre por qué no se comparte código con esa función en esta iteración).

VOLUME_STABLE_PCT = 10.0          # ±10% de banda de estabilidad para métricas de volumen (km/metros/minutos)
RUNNING_KM_MIN_DELTA = 2.0        # km mínimos de cambio absoluto para no ser "stable" pese al %
CYCLING_KM_MIN_DELTA = 3.0
SWIMMING_METROS_MIN_DELTA = 300.0
MINUTES_MIN_DELTA = 20.0          # minutos mínimos de cambio absoluto (total_training y por disciplina)
SESSIONS_STABLE_DELTA = 1.0       # sesiones/días: diferencia absoluta mínima para no ser "stable"
PACE_STABLE_SEC_KM = 5.0          # seg/km mínimos de cambio para no ser "stable"
HR_STABLE_BPM = 3.0               # bpm mínimos de cambio para no ser "stable"

# Filtrado robusto de pace (auditoría real: William/Álvaro traían actividades
# "running" con pace_raw de 15-31 min/km -- FC 107-135bpm, sesiones cortas de
# nombre genérico: tramos de descanso/parado dentro de una sesión más larga,
# no carreras lentas reales -- que inflaban avg4 y producían deltas de
# -98/-163 seg/km sin sentido). Regla: SOLO se excluye una observación si es
# (a) estadísticamente atípica para el PROPIO patrón del atleta (modified
# z-score de Iglewicz & Hoaglin sobre mediana/MAD) Y (b) cruza un guardrail
# absoluto muy permisivo -- nunca (a) o (b) solos. Esto protege a un atleta
# genuinamente lento (trail/ultra/run-walk/principiante): sus sesiones
# típicas, aunque lentas en términos absolutos, no son atípicas respecto a
# SU propia mediana, así que (a) nunca se activa para ellas.
PACE_MAD_Z_THRESHOLD = 3.5          # |modified z| > 3.5 = outlier definitivo (regla estándar, no inventada)
PACE_MIN_MUESTRAS_REFERENCIA = 8    # bajo esto, sin base para el criterio relativo -- solo guardrail absoluto
PACE_LENTO_EXTREMO_MIN_KM = 15.0    # guardrail absoluto lado lento -- MUY permisivo, nunca actúa solo
# Lado rápido: mismo valor que tj.PACE_MIN_VALIDO_MIN_KM (2.5 min/km) --
# duplicado deliberadamente para no acoplar este motor a transformar_json.py.
PACE_RAPIDO_EXTREMO_MIN_KM = 2.5

# Umbral de "historial preliminar" para este motor -- mismo VALOR que usa
# Planning Constraints (is_preliminary = history_weeks < 6), pero calculado
# de forma independiente porque mide algo distinto: acá son semanas
# cerradas CON CUALQUIER ACTIVIDAD (espíritu multideporte de este motor);
# en Planning Constraints son semanas cerradas CON RUNNING específicamente
# (avg4_km es running-only por diseño). Compartir una función requeriría
# parametrizar "historial de qué", lo cual toca calcular_planning_
# constraints() -- fuera de alcance en esta iteración (congelado). Deuda
# documentada, no refactor silencioso.
HISTORIAL_PRELIMINAR_SEMANAS_COMPARISON = 6


def _semana_inicio(fecha_iso):
    d = date.fromisoformat(fecha_iso[:10])
    return d - timedelta(days=d.weekday())


def _bucketize_por_semana(activities):
    """dict {lunes: [actividades de esa semana calendario]} a partir de
    activities_cerradas. Nunca filtra por tipo -- eso lo decide cada
    consumidor según qué necesita."""
    por_semana = defaultdict(list)
    for a in activities or []:
        fecha_str = a.get("date")
        if not fecha_str:
            continue
        try:
            por_semana[_semana_inicio(fecha_str)].append(a)
        except ValueError:
            continue
    return por_semana


def _weekly_por_inicio(weekly_cerrado):
    """dict {lunes: entrada de weekly[]} -- ausente = esa semana calendario
    no tuvo NINGUNA actividad (información válida: son 0, no "faltan datos")."""
    mapa = {}
    for w in weekly_cerrado or []:
        try:
            mapa[date.fromisoformat(w["week"].split("/")[0])] = w
        except (KeyError, ValueError):
            continue
    return mapa


def _direccion_volumen(current, denom, min_delta_abs, stable_pct=VOLUME_STABLE_PCT):
    """
    (pct, direction) para una métrica de volumen (km/metros/minutos).
    Nunca infinito ni 100% cuando denom==0: pct queda None. direction
    exige DOS condiciones para no ser "stable": el delta absoluto debe
    superar min_delta_abs Y (cuando hay pct) el % debe superar stable_pct
    -- evita tanto ruido de bases minúsculas (1km->1.3km es 30% pero
    trivial) como reaccionar a diferencias de +1% en bases grandes.
    """
    if current is None or denom is None:
        return None, "unknown"
    delta = current - denom
    if denom == 0:
        if current == 0 or abs(delta) < min_delta_abs:
            return None, "stable"
        return None, "up"  # de 0 a algo real: cambio inequívoco, sin % que lo exprese
    pct = round(delta / denom * 100, 1)
    if abs(delta) < min_delta_abs or abs(pct) < stable_pct:
        return pct, "stable"
    return pct, "up" if delta > 0 else "down"


def _direccion_conteo(current, denom, min_delta=SESSIONS_STABLE_DELTA):
    """direction para un conteo (sesiones, días activos): delta absoluto,
    nunca porcentaje (pedido explícito -- un número pequeño como 2->3
    sesiones es más interpretable que "50%")."""
    if current is None or denom is None:
        return "unknown"
    delta = current - denom
    if abs(delta) < min_delta:
        return "stable"
    return "up" if delta > 0 else "down"


def _mediana(valores):
    valores = sorted(valores)
    n = len(valores)
    if n == 0:
        return None
    return valores[n // 2] if n % 2 else (valores[n // 2 - 1] + valores[n // 2]) / 2


def _mad(valores, mediana):
    """Median Absolute Deviation -- dispersión robusta (no la infla un
    puñado de outliers, a diferencia de la desviación estándar)."""
    return _mediana([abs(v - mediana) for v in valores])


def _referencia_pace_atleta(por_semana_acts, semana_excluir):
    """
    Mediana y MAD de pace_raw (min/km) de TODO el historial cerrado
    disponible en por_semana_acts, EXCLUYENDO semana_excluir (la semana
    analizada) -- ninguna semana valida sus propias muestras. Nunca
    incluye la semana abierta: por_semana_acts se construye a partir de
    activities_cerradas, que ya la excluye aguas arriba (ver "data
    leakage" en el entregable de esta iteración).

    None si no hay al menos PACE_MIN_MUESTRAS_REFERENCIA muestras válidas
    -- el llamador cae entonces solo al guardrail absoluto, nunca al revés
    (nunca más agresivo con menos evidencia).
    """
    muestras = []
    for lunes, acts in por_semana_acts.items():
        if lunes == semana_excluir:
            continue
        muestras.extend(a["pace_raw"] for a in acts
                         if a.get("type") == "running" and (a.get("pace_raw") or 0) > 0)
    if len(muestras) < PACE_MIN_MUESTRAS_REFERENCIA:
        return None
    mediana = _mediana(muestras)
    return {"mediana": mediana, "mad": _mad(muestras, mediana), "n": len(muestras)}


def _pace_confiable(pace_raw_min_km, referencia):
    """
    True si la observación se CONSERVA para comparar pace. extremo_absoluto
    por sí solo NUNCA excluye (protege al atleta lento/rápido legítimo);
    atípico_relativo por sí solo tampoco (protege contra excluir por ser
    simplemente lento/rápido sin evidencia de artefacto). Solo se excluye
    cuando AMBAS condiciones coinciden.
    """
    extremo_absoluto = pace_raw_min_km > PACE_LENTO_EXTREMO_MIN_KM or pace_raw_min_km < PACE_RAPIDO_EXTREMO_MIN_KM
    if referencia is None or referencia["mad"] == 0:
        return not extremo_absoluto
    z = 0.6745 * (pace_raw_min_km - referencia["mediana"]) / referencia["mad"]
    atipico_relativo = abs(z) > PACE_MAD_Z_THRESHOLD
    return not (atipico_relativo and extremo_absoluto)


def _comparar_pace_robusto(por_semana_acts, semana_actual, semanas_avg4):
    """
    Igual que _comparar_pool() pero con el filtrado robusto de arriba.
    Función dedicada (no se parametrizó _comparar_pool) para no arriesgar
    tocar el comportamiento de heart_rate, que esta iteración no debe
    modificar. La actividad NUNCA se borra de activities_cerradas/weekly_
    cerrado -- se sigue contando en km/minutos/sesiones/días activos/carga
    multideporte; se excluye SOLO de esta comparación de pace.
    """
    referencia = _referencia_pace_atleta(por_semana_acts, semana_actual)

    def _muestras(lunes):
        crudas = [a["pace_raw"] for a in por_semana_acts.get(lunes, [])
                  if a.get("type") == "running" and (a.get("pace_raw") or 0) > 0]
        confiables = [p for p in crudas if _pace_confiable(p, referencia)]
        return crudas, confiables

    crudas_actual, confiables_actual = _muestras(semana_actual)
    current_sec_km = round(sum(p * 60 for p in confiables_actual) / len(confiables_actual), 1) if confiables_actual else None

    crudas_avg4, confiables_avg4, semanas_con_dato = [], [], 0
    for s in semanas_avg4:
        crudas_s, confiables_s = _muestras(s)
        crudas_avg4.extend(crudas_s)
        confiables_avg4.extend(confiables_s)
        if confiables_s:
            semanas_con_dato += 1
    avg4_sec_km = round(sum(p * 60 for p in confiables_avg4) / len(confiables_avg4), 1) if confiables_avg4 else None

    resultado = {
        "current_sec_km": current_sec_km, "avg4_sec_km": avg4_sec_km, "weeks_with_data": semanas_con_dato,
        "samples_total": {"current": len(crudas_actual), "avg4": len(crudas_avg4)},
        "samples_used": {"current": len(confiables_actual), "avg4": len(confiables_avg4)},
        "samples_excluded": {"current": len(crudas_actual) - len(confiables_actual),
                              "avg4": len(crudas_avg4) - len(confiables_avg4)},
    }
    if current_sec_km is None or avg4_sec_km is None:
        resultado["delta_sec_per_km"] = None
        resultado["direction"] = "unknown"
        return resultado

    delta = round(current_sec_km - avg4_sec_km, 1)
    resultado["delta_sec_per_km"] = delta
    resultado["direction"] = "stable" if abs(delta) < PACE_STABLE_SEC_KM else ("faster" if delta < 0 else "slower")
    return resultado


def _muestras_hr(actividades):
    return [a["hr"] for a in actividades
            if a.get("type") == "running" and (a.get("hr") or 0) > 0]


def _comparar_pool(por_semana, semana_actual, semanas_avg4, extractor, umbral_estable, direcciones):
    """
    Compara un promedio "pooled" (todas las muestras individuales juntas,
    no promedio-de-promedios semanales -- pesa correctamente semanas con
    más o menos sesiones) de la semana analizada contra las semanas avg4.
    Usado por pace y HR -- misma forma, distinta unidad y umbral.
    direcciones: (etiqueta_baja, etiqueta_sube) -- p.ej. ("faster","slower").
    """
    muestras_actual = extractor(por_semana.get(semana_actual, []))
    current = round(sum(muestras_actual) / len(muestras_actual), 1) if muestras_actual else None

    muestras_avg4 = []
    semanas_con_dato = 0
    for s in semanas_avg4:
        m = extractor(por_semana.get(s, []))
        if m:
            semanas_con_dato += 1
            muestras_avg4.extend(m)
    avg4 = round(sum(muestras_avg4) / len(muestras_avg4), 1) if muestras_avg4 else None

    if current is None or avg4 is None:
        return {"current": current, "avg4": avg4, "delta": None, "direction": "unknown",
                "weeks_with_data": semanas_con_dato}

    delta = round(current - avg4, 1)
    etiqueta_baja, etiqueta_sube = direcciones
    if abs(delta) < umbral_estable:
        direction = "stable"
    else:
        direction = etiqueta_baja if delta < 0 else etiqueta_sube
    return {"current": current, "avg4": avg4, "delta": delta, "direction": direction,
            "weeks_with_data": semanas_con_dato}


def _magnitud(delta_abs, bandas):
    """bandas = (small_min, moderate_min, large_min). None si delta_abs es None."""
    if delta_abs is None:
        return None
    delta_abs = abs(delta_abs)
    small_min, moderate_min, large_min = bandas
    if delta_abs >= large_min:
        return "large"
    if delta_abs >= moderate_min:
        return "moderate"
    if delta_abs >= small_min:
        return "small"
    return None  # por debajo del piso de "small" -- no debería ocurrir si direction ya no es stable


def calcular_comparaciones_pulse(activities_cerradas, weekly_cerrado, lunes_analizado, domingo_analizado,
                                  acwr_info=None):
    """
    Motor determinístico de comparaciones semanales. Ver cabecera de esta
    sección para el rol dentro de PULSE v2. NO llama a Claude, NO se
    integra todavía a construir_prompts_v2()/generar_pulse_v2().

    lunes_analizado/domingo_analizado: la semana YA cerrada que se analiza
    (mismo valor que usa el resto de generar_pulse_v2() -- se recibe, no se
    recalcula, para no triplicar la lógica de "qué semana es la analizada").
    """
    por_semana_acts = _bucketize_por_semana(activities_cerradas)
    weekly_por_inicio = _weekly_por_inicio(weekly_cerrado)

    semana_actual = lunes_analizado
    semana_anterior = semana_actual - timedelta(days=7)
    semanas_avg4 = [semana_actual - timedelta(days=7 * k) for k in (1, 2, 3, 4)]

    def _w(lunes):
        return weekly_por_inicio.get(lunes)

    def _running_km(lunes):
        w = _w(lunes)
        return w["running"]["km"] if w else 0.0

    def _running_sessions(lunes):
        w = _w(lunes)
        return w["running"]["sessions"] if w else 0

    def _disciplina_km_o_metros(lunes, disciplina, campo):
        w = _w(lunes)
        return (w.get(disciplina, {}).get(campo, 0)) if w else 0

    def _disciplina_sessions(lunes, disciplina):
        w = _w(lunes)
        return (w.get(disciplina, {}).get("sessions", 0)) if w else 0

    def _minutos_disciplina(lunes, tipo):
        return round(sum(a.get("duration_min") or 0 for a in por_semana_acts.get(lunes, []) if a.get("type") == tipo), 1)

    def _minutos_totales(lunes):
        return round(sum(a.get("duration_min") or 0 for a in por_semana_acts.get(lunes, [])), 1)

    def _dias_activos(lunes):
        return len({a.get("date") for a in por_semana_acts.get(lunes, []) if a.get("date")})

    def _sesiones_totales(lunes):
        return len(por_semana_acts.get(lunes, []))

    def _avg(fn):
        return round(sum(fn(s) for s in semanas_avg4) / len(semanas_avg4), 1)

    # ── history_quality ──
    weeks_available = len(weekly_cerrado or [])
    is_preliminary = weeks_available < HISTORIAL_PRELIMINAR_SEMANAS_COMPARISON

    # ── running.km ──
    km_current, km_previous, km_avg4 = _running_km(semana_actual), _running_km(semana_anterior), _avg(_running_km)
    pct_vs_previous, _ = _direccion_volumen(km_current, km_previous, RUNNING_KM_MIN_DELTA)
    pct_vs_avg4, km_direction = _direccion_volumen(km_current, km_avg4, RUNNING_KM_MIN_DELTA)
    running_km = {
        "current": km_current, "previous": km_previous, "avg4": km_avg4,
        "vs_previous_pct": pct_vs_previous, "vs_avg4_pct": pct_vs_avg4, "direction": km_direction,
    }

    # ── running.sessions ──
    sess_current, sess_previous = _running_sessions(semana_actual), _running_sessions(semana_anterior)
    sess_avg4 = _avg(_running_sessions)
    running_sessions = {
        "current": sess_current, "previous": sess_previous, "avg4": sess_avg4,
        "vs_previous": round(sess_current - sess_previous, 1) if sess_previous is not None else None,
        "vs_avg4": round(sess_current - sess_avg4, 1),
        "direction": _direccion_conteo(sess_current, sess_avg4),
    }

    # ── running.pace (pooled, sec/km, excluye inválidos + outliers robustos) ──
    running_pace = _comparar_pace_robusto(por_semana_acts, semana_actual, semanas_avg4)

    # ── running.heart_rate (pooled, bpm, excluye inválidos) ──
    hr_stats = _comparar_pool(por_semana_acts, semana_actual, semanas_avg4, _muestras_hr,
                               HR_STABLE_BPM, ("down", "up"))
    running_hr = {
        "current": hr_stats["current"], "avg4": hr_stats["avg4"], "delta_bpm": hr_stats["delta"],
        "direction": hr_stats["direction"], "weeks_with_data": hr_stats["weeks_with_data"],
    }

    # ── running.hard_sessions: ver auditoría (sección D del entregable) --
    # deliberadamente unknown en v1. _es_sesion_dura_por_nombre() se
    # construyó para reconciliar la semana en curso (subestimar ahí es
    # seguro); usarla para una afirmación retrospectiva sobre el atleta es
    # un uso distinto y no defendible con la evidencia disponible hoy.
    running_hard_sessions = {
        "current": None, "avg4": None, "direction": "unknown",
        "reason": "sin evidencia suficiente (tipo/intensidad explícita, zonas personales) para clasificar "
                  "sesiones duras de forma retrospectiva defendible -- ver auditoría.",
    }

    running = {
        "km": running_km, "sessions": running_sessions, "pace": running_pace,
        "heart_rate": running_hr, "hard_sessions": running_hard_sessions,
    }

    # ── total_training (multideporte, sin convertir a running-equivalente) ──
    min_current, min_previous, min_avg4 = _minutos_totales(semana_actual), _minutos_totales(semana_anterior), _avg(_minutos_totales)
    pct_min_previous, _ = _direccion_volumen(min_current, min_previous, MINUTES_MIN_DELTA)
    pct_min_avg4, min_direction = _direccion_volumen(min_current, min_avg4, MINUTES_MIN_DELTA)
    total_training = {
        "minutes": {
            "current": min_current, "previous": min_previous, "avg4": min_avg4,
            "vs_previous_pct": pct_min_previous, "vs_avg4_pct": pct_min_avg4, "direction": min_direction,
        },
        "active_days": {
            "current": _dias_activos(semana_actual), "previous": _dias_activos(semana_anterior),
            "avg4": _avg(_dias_activos),
        },
        "sessions": {
            "current": _sesiones_totales(semana_actual), "previous": _sesiones_totales(semana_anterior),
            "avg4": _avg(_sesiones_totales),
        },
    }

    # ── disciplines: current vs avg4 únicamente (no dupl. previous/pct por disciplina) ──
    def _bloque_disciplina(disciplina, campo_volumen, min_delta_volumen):
        vol_fn = lambda lunes: _disciplina_km_o_metros(lunes, disciplina, campo_volumen)
        sess_fn = lambda lunes: _disciplina_sessions(lunes, disciplina)
        min_fn = lambda lunes: _minutos_disciplina(lunes, disciplina)
        vol_current, vol_avg4 = vol_fn(semana_actual), _avg(vol_fn)
        sess_current_d, sess_avg4_d = sess_fn(semana_actual), _avg(sess_fn)
        min_current_d, min_avg4_d = min_fn(semana_actual), _avg(min_fn)
        _, vol_dir = _direccion_volumen(vol_current, vol_avg4, min_delta_volumen)
        return {
            "sessions": {"current": sess_current_d, "avg4": sess_avg4_d,
                         "direction": _direccion_conteo(sess_current_d, sess_avg4_d)},
            campo_volumen: {"current": vol_current, "avg4": vol_avg4, "direction": vol_dir},
            "minutes": {"current": min_current_d, "avg4": min_avg4_d,
                        "direction": _direccion_volumen(min_current_d, min_avg4_d, MINUTES_MIN_DELTA)[1]},
        }

    disciplines = {
        "running": _bloque_disciplina("running", "km", RUNNING_KM_MIN_DELTA),
        "cycling": _bloque_disciplina("cycling", "km", CYCLING_KM_MIN_DELTA),
        "swimming": _bloque_disciplina("swimming", "metros", SWIMMING_METROS_MIN_DELTA),
        "strength": {
            "sessions": {"current": _disciplina_sessions(semana_actual, "strength"),
                         "avg4": _avg(lambda l: _disciplina_sessions(l, "strength")),
                         "direction": _direccion_conteo(_disciplina_sessions(semana_actual, "strength"),
                                                         _avg(lambda l: _disciplina_sessions(l, "strength")))},
            "minutes": {"current": _disciplina_km_o_metros(semana_actual, "strength", "minutos"),
                        "avg4": _avg(lambda l: _disciplina_km_o_metros(l, "strength", "minutos")),
                        "direction": _direccion_volumen(
                            _disciplina_km_o_metros(semana_actual, "strength", "minutos"),
                            _avg(lambda l: _disciplina_km_o_metros(l, "strength", "minutos")), MINUTES_MIN_DELTA)[1]},
        },
    }

    # ── load: SOLO status ya existente, nunca recalculado; multisport_direction
    # es la misma señal que total_training.minutes.direction, expuesta acá
    # para agrupar narrativamente con ACWR (no es un segundo cálculo). Esto
    # es DISTINTO de multisport_load del Planning Engine (que es un guardrail
    # de prescripción) -- acá es puramente descriptivo/retrospectivo.
    def _ultimo_status_disciplina(disciplina):
        serie = (acwr_info or {}).get("series", {}).get(disciplina) or []
        return serie[-1].get("status") if serie else None

    discipline_statuses = {}
    for disciplina in ("running", "cycling", "strength"):
        st = _ultimo_status_disciplina(disciplina)
        if st is not None:
            discipline_statuses[disciplina] = st

    load = {
        "running_acwr_status": _ultimo_status_disciplina("running"),
        "discipline_statuses": discipline_statuses,
        "multisport_direction": min_direction,
    }

    comparaciones = {
        "week": f"{lunes_analizado.isoformat()}/{domingo_analizado.isoformat()}",
        "history_quality": {"weeks_available": weeks_available, "is_preliminary": is_preliminary},
        "running": running,
        "total_training": total_training,
        "disciplines": disciplines,
        "load": load,
        "signals": [],
    }
    comparaciones["signals"] = _generar_signals(comparaciones)
    return comparaciones


def _generar_signals(comparaciones):
    """
    Señales ESTRUCTURADAS (id/metric/direction/magnitude), nunca texto
    interpretativo libre -- eso es trabajo de Claude, después. magnitude
    siempre se deriva del delta ABSOLUTO (nunca del %), para que quede
    definida incluso cuando pct es None (caso "de 0 a algo real").
    """
    signals = []
    running = comparaciones["running"]
    total = comparaciones["total_training"]

    km = running["km"]
    if km["direction"] in ("up", "down"):
        delta_abs = abs(km["current"] - km["avg4"]) if km["current"] is not None and km["avg4"] is not None else None
        signals.append({"id": f"running_volume_{km['direction']}", "metric": "running_km",
                         "direction": km["direction"], "magnitude": _magnitud(delta_abs, (2.0, 5.0, 10.0))})

    minutos = total["minutes"]
    if minutos["direction"] in ("up", "down"):
        delta_abs = abs(minutos["current"] - minutos["avg4"]) if minutos["current"] is not None and minutos["avg4"] is not None else None
        signals.append({"id": f"total_training_{minutos['direction']}", "metric": "total_training_minutes",
                         "direction": minutos["direction"], "magnitude": _magnitud(delta_abs, (20.0, 60.0, 120.0))})

    # Patrones compuestos running vs. total_training -- SOLO estructura,
    # sin nombrarlos "progreso"/"fatiga"/"adaptación"/"sobrecarga".
    if km["direction"] == "down" and minutos["direction"] in ("up", "stable"):
        delta_abs = abs(km["current"] - km["avg4"]) if km["current"] is not None and km["avg4"] is not None else None
        signals.append({"id": f"running_down_total_training_{minutos['direction']}", "metric": "multisport",
                         "direction": "redistributed", "magnitude": _magnitud(delta_abs, (2.0, 5.0, 10.0))})
    elif km["direction"] == "up" and minutos["direction"] == "up":
        delta_abs = abs(minutos["current"] - minutos["avg4"]) if minutos["current"] is not None and minutos["avg4"] is not None else None
        signals.append({"id": "running_up_total_training_up", "metric": "multisport",
                         "direction": "concurrent_increase", "magnitude": _magnitud(delta_abs, (20.0, 60.0, 120.0))})

    active_days = total["active_days"]
    dir_dias = _direccion_conteo(active_days["current"], active_days["avg4"])
    if dir_dias in ("up", "down"):
        delta_abs = abs(active_days["current"] - active_days["avg4"])
        signals.append({"id": f"active_days_{dir_dias}", "metric": "active_days",
                         "direction": dir_dias, "magnitude": _magnitud(delta_abs, (1.0, 2.0, 3.0))})

    pace = running["pace"]
    if pace["direction"] in ("faster", "slower"):
        signals.append({"id": f"pace_{pace['direction']}", "metric": "running_pace",
                         "direction": pace["direction"], "magnitude": _magnitud(pace["delta_sec_per_km"], (5.0, 15.0, 30.0))})

    hr = running["heart_rate"]
    if hr["direction"] in ("up", "down"):
        signals.append({"id": f"hr_{hr['direction']}", "metric": "running_hr",
                         "direction": hr["direction"], "magnitude": _magnitud(hr["delta_bpm"], (3.0, 8.0, 15.0))})

    return signals


# ── 2. Prompt v2.1 ────────────────────────────────────────────────────

def _fmt_range(r, unit="km"):
    if not r:
        return "sin referencia suficiente — sé conservador"
    return f"{r[0]}–{r[1]} {unit}"


def _fmt_comparisons(comparisons):
    """
    Representación compacta (texto, no JSON) de calcular_comparaciones_pulse()
    para el prompt. Nunca incluye samples_total/samples_used/samples_excluded
    (metadata de auditoría interna, no debe llegar al atleta -- ver
    input_context['comparisons'] para la versión completa auditable).
    Omite pace/HR cuando su direction es "unknown" (sin base confiable) en
    vez de mandar un número dudoso; omite disciplinas sin ninguna actividad
    reciente ni histórica para no ensuciar el prompt con puros ceros.
    """
    running = comparisons["running"]
    total = comparisons["total_training"]
    disciplines = comparisons["disciplines"]
    load = comparisons["load"]
    hist = comparisons["history_quality"]

    km = running["km"]
    km_pct = f"{km['vs_avg4_pct']:+.0f}%" if km["vs_avg4_pct"] is not None else "sin % (referencia insuficiente)"
    sess = running["sessions"]
    running_line = (f"Running: {km['current']}km ({km['direction']} vs. promedio 4 sem., {km_pct}) | "
                     f"sesiones {sess['current']} (promedio {sess['avg4']}, {sess['direction']})")

    pace = running["pace"]
    if pace["direction"] != "unknown" and pace["weeks_with_data"] >= 2:
        pace_line = (f"Ritmo: {pace['direction']} ({pace['delta_sec_per_km']:+.0f} seg/km vs. promedio), "
                      f"cobertura {pace['weeks_with_data']}/4 semanas.")
    else:
        pace_line = "Ritmo: sin comparación confiable esta semana (cobertura insuficiente) -- no la menciones."

    hr = running["heart_rate"]
    if hr["direction"] != "unknown":
        hr_line = f"FC: {hr['direction']} ({hr['delta_bpm']:+.0f} bpm vs. promedio)."
    else:
        hr_line = "FC: sin comparación confiable esta semana -- no la menciones."

    minutos = total["minutes"]
    min_pct = f"{minutos['vs_avg4_pct']:+.0f}%" if minutos["vs_avg4_pct"] is not None else "sin % (referencia insuficiente)"
    total_line = (f"Carga total (TODAS las disciplinas): {minutos['current']} min ({minutos['direction']} vs. "
                   f"promedio, {min_pct}) | días activos {total['active_days']['current']} "
                   f"(promedio {total['active_days']['avg4']}) | sesiones totales {total['sessions']['current']} "
                   f"(promedio {total['sessions']['avg4']})")

    disc_partes = []
    for disc, campo_vol, unidad in (("cycling", "km", "km"), ("swimming", "metros", "m")):
        vol = disciplines[disc][campo_vol]
        if not (vol["direction"] == "stable" and vol["current"] == 0 and vol["avg4"] == 0):
            nombre_es = {"cycling": "Ciclismo", "swimming": "Natación"}[disc]
            disc_partes.append(f"{nombre_es} {vol['direction']} ({vol['current']}{unidad})")
    fuerza_min = disciplines["strength"]["minutes"]
    if not (fuerza_min["direction"] == "stable" and fuerza_min["current"] == 0 and fuerza_min["avg4"] == 0):
        disc_partes.append(f"Fuerza {fuerza_min['direction']} ({fuerza_min['current']} min)")
    disc_line = "Disciplinas: " + (", ".join(disc_partes) if disc_partes else "sin cambios relevantes fuera de running")

    señales = comparisons.get("signals") or []
    señales_line = "Señales detectadas: " + (", ".join(s["id"] for s in señales) if señales else "ninguna señal relevante esta semana")

    hist_line = f"Historial: {hist['weeks_available']} semanas cerradas disponibles"
    if hist["is_preliminary"]:
        hist_line += (" (PRELIMINAR -- evita \"tu patrón habitual\"/\"normalmente\"/\"tu tendencia histórica\", "
                       "usa \"con el historial disponible hasta ahora\")")

    return "\n".join([
        running_line, pace_line, hr_line, total_line,
        f"Multideporte: dirección de carga total = {load['multisport_direction']}",
        disc_line, señales_line, hist_line,
    ])


def construir_prompts_v2(tj, activities_cerradas, weekly_cerrado, meta, profile,
                          acwr_info, lunes_analizado, domingo_analizado,
                          lunes_semana_actual, domingo_plan, fecha_generacion, constraints,
                          semana_en_curso, restantes, comparisons, race_status):
    """Adapta generar_pulse() v1 (transformar_json.py) agregando las reglas
    de esquema v2.1 y las restricciones de planning_constraints como límites
    duros. Reusa los helpers de contexto de tj (resumen, patrón semanal,
    disponibilidad, proyección) sin duplicarlos."""
    carrera = meta.get("metaCarrera", {})
    nombre = meta.get("nombre", profile.get("full_name", "Atleta"))
    tiene_meta = bool(carrera.get("nombre") and carrera.get("nombre") != "¿Cuál es tu próxima carrera?")

    resumen = tj.resumen_disciplinas(activities_cerradas)
    patron_semanal = tj.resumir_patron_semanal(activities_cerradas, domingo_analizado)
    disponibilidad = tj.disponibilidad_declarada(meta, profile)
    estado_carga = tj._acwr_status_es((acwr_info or {}).get("status"))

    dias_restantes = constraints["dias_restantes"]
    km_meta = tj._km_meta_de_label(carrera.get("label", "")) if tiene_meta else None
    proyeccion = tj.proyectar_tiempo_carrera(
        profile.get("personal_records", []), km_meta, activities_cerradas,
        carrera.get("nombre", ""), domingo_analizado,
    )

    # Fix #7: PRs filtrados por consistencia ANTES de entrar al prompt, en
    # vez de confiar en meta["prs"] (que puede mezclar un valor corrupto de
    # una sola fuente/dispositivo, como el 1K de Fabiana).
    prs_limpios, prs_descartados = filtrar_prs_atipicos(tj, profile.get("personal_records", []))
    prs_str = ("PRs registrados (verificados por consistencia): " +
               ", ".join(f"{p['dist']} {p['mark']}" for p in prs_limpios)) if prs_limpios else ""

    tiempo_objetivo = (
        carrera.get("tiempoObjetivo") or carrera.get("targetTime") or carrera.get("tiempo_objetivo")
    )

    rango_semana_es = tj.fmt_rango_semana_es(lunes_analizado, domingo_analizado)

    # Nota (integración de comparisons): "última semana" en bruto (km/
    # sesiones/FC por disciplina) y la lista cruda de últimas 8 semanas ya
    # NO se arman acá -- comparisons (calcular_comparaciones_pulse) cubre
    # exactamente esa pregunta con dirección/tendencia ya calculada, así
    # que mandar ambas cosas era redundante (ver entregable, sección C).
    run_acts = [a for a in activities_cerradas if a.get("type") == "running"]
    recent_run = run_acts[-1] if run_acts else (activities_cerradas[-1] if activities_cerradas else {})

    # Fix v3: días de la semana a planificar que ya pasaron respecto a
    # fecha_generacion se describen con lo que REALMENTE pasó (o no pasó) —
    # ver calcular_semana_en_curso(). El backend los va a forzar igual
    # (aplicar_dias_completados), pero el modelo necesita saber que ya
    # corrió 13.5km el lunes/martes para no volver a planificarlos desde
    # cero sobre el resto de la semana.
    lineas_completados, dias_restantes_semana = [], []
    for i, etiqueta in enumerate(DIAS_ORDEN):
        fecha_dia = lunes_semana_actual + timedelta(days=i)
        fecha_str = fecha_dia.isoformat()
        if fecha_str in semana_en_curso["dias"]:
            info = semana_en_curso["dias"][fecha_str]
            if info["estado"] == "completado":
                detalle = f"{info['running_km']} km running" if info["running_km"] > 0 else ", ".join(info["tipos"]) or "actividad registrada"
                dura = " (sesión DURA, ya cuenta contra el máximo de sesiones duras)" if info["es_sesion_dura"] else ""
                lineas_completados.append(f"{etiqueta} {fecha_str}: COMPLETADO — {detalle}{dura}")
            else:
                lineas_completados.append(f"{etiqueta} {fecha_str}: transcurrido, sin actividad registrada")
        else:
            dias_restantes_semana.append(f"{etiqueta} {fecha_str}")

    # race_status (auditoría race_status, decisión de producto #6): reglas
    # mínimas, solo cuando aplican -- no se toca ninguna otra sección del
    # prompt. unconfirmed_after_date da la regla de "no asumir finalización"
    # sin imponerle al modelo una frase textual fija. race_falls_in_planning_week
    # exige la sesión de carrera como evento obligatorio, fuera del
    # presupuesto normal de entrenamiento (decisión #5).
    race_status = race_status or {}
    race_prompt_lines = []
    if race_status.get("state") == "unconfirmed_after_date":
        race_prompt_lines.append(
            f'CARRERA META SIN CONFIRMAR: {carrera.get("nombre","")} estaba programada para el '
            f'{race_status.get("race_date")}, esa fecha ya pasó, pero no se encontró ninguna actividad de '
            f'running que coincida con la distancia de esa carrera en la ventana esperada. NO asumas ni '
            f'dés por hecho que la carrera se corrió; si es relevante para tu análisis, explicá que estaba '
            f'programada pero no hay evidencia de que haya ocurrido, sin inventar una razón.'
        )
    if race_status.get("race_falls_in_planning_week"):
        race_prompt_lines.append(
            f'CARRERA DENTRO DE LA SEMANA A PLANIFICAR: {carrera.get("nombre","")} cae el '
            f'{race_status.get("race_planning_day")} {race_status.get("race_date")}, dentro de la semana que '
            f'estás planificando, distancia {km_meta}km. Ese día del weeklyPlan DEBE ser la carrera -- nunca '
            f'un día de descanso ni una sesión de entrenamiento normal, y nunca la omitas. La distancia de esa '
            f'carrera es un evento, no volumen de entrenamiento: NO cuenta contra ningún rango ni techo de '
            f'esta semana (volumen total, fondo largo, máximo de sesiones) aunque los supere ampliamente.'
        )
    race_prompt_block = ("\n".join(race_prompt_lines) + "\n") if race_prompt_lines else ""

    race_status_linea = ""
    if race_status.get("state") == "unconfirmed_after_date":
        race_status_linea = (
            f"Estado de la carrera meta: programada para el {race_status.get('race_date')}, fecha ya pasada, "
            f"sin actividad de running que coincida con la distancia esperada en la ventana evaluada -- no confirmada."
        )
    elif race_status.get("race_falls_in_planning_week"):
        race_status_linea = (
            f"Estado de la carrera meta: cae el {race_status.get('race_planning_day')} {race_status.get('race_date')} "
            f"de la semana a planificar, distancia {km_meta}km -- sesión obligatoria ese día, fuera del presupuesto normal de entrenamiento."
        )

    system_prompt = f"""Eres Pulse, el motor de análisis semanal de Swetro (contrato de schema v2.1).
Hoy, al momento de generar este análisis, es {fecha_generacion.isoformat()}.
Analizas la semana del {rango_semana_es}. Las actividades posteriores al domingo {tj.fmt_fecha_es(domingo_analizado, False)} no existen para este análisis, aunque estén en los datos. Nunca menciones actividades de la semana en curso.
Español latinoamericano. SIEMPRE en segunda persona dirigiéndote al atleta por su nombre — escribe "Juan, cerraste..." nunca "Juan cerró...".
COMPARACIONES SEMANALES — regla central: Python ya calculó qué cambió esta semana respecto al patrón reciente (dirección, magnitud, cobertura de datos). Tu trabajo es interpretar qué significa, nunca recalcular. No contradigas ninguna "direction" que te llega en COMPARACIONES SEMANALES. No inventes porcentajes que no vengan en esos datos. No conviertas ciclismo, natación o fuerza a "kilómetros de running equivalentes" bajo ninguna circunstancia -- no existe esa fórmula. Si el running bajó pero la carga total (todas las disciplinas) está estable o subió, NO describas la semana como una caída general de entrenamiento: distingue explícitamente "bajó el running" de "bajó el entrenamiento total"; si una disciplina concreta explica la redistribución, podés nombrarla, sin convertirla a unidades equivalentes. Un ritmo más rápido NUNCA implica automáticamente mejor condición física o fitness -- solo describe el cambio de ritmo ("ritmo medio más rápido" es válido, "mejoraste tu condición física" no lo es solo con esa señal). Una FC menor NUNCA implica automáticamente mejor eficiencia cardiovascular -- solo reporta que la FC media fue menor/mayor/estable. Usa pace o FC en tu interpretación SOLO si su comparación no quedó marcada como no confiable; si no hay comparación confiable, no la menciones. Nunca menciones al atleta las palabras "outlier", "muestra excluida", "filtrado", ni ninguna cifra de cobertura/muestras -- son metadata de auditoría interna. Si el historial está marcado PRELIMINAR, nunca digas "tu patrón habitual", "normalmente" ni "tu tendencia histórica" -- usa "con el historial disponible hasta ahora".
INSIGHT DOMINANTE — aiVerdict debe girar alrededor de UNA sola idea: ¿cuál fue el cambio más importante de esta semana respecto al patrón reciente? No es un inventario de métricas. Ejemplos de ideas dominantes válidas: aumento real de carga, caída general de entrenamiento, descarga/afinamiento coherente con la fase, redistribución hacia otra disciplina, volumen estable con cambio de ritmo o FC, consolidación sin cambios relevantes. Si no ocurrió nada importante, "semana estable" es una conclusión válida -- no fuerces un insight que las comparaciones no respaldan. Estructura en máximo 3 oraciones: (1) qué cambió, (2) qué significa en el contexto de este atleta (objetivo, fase, ACWR, historial), (3) cómo conecta con la dirección del plan de la próxima semana. No repitas cifras que ya aparecen en las cajas de la interfaz.
aiVerdict: máximo 3 oraciones, aproximadamente 80-120 palabras, sin bullets, sin emojis, sin guiones largos ni medios (usa coma o punto en su lugar), nunca la palabra "oficial" para un tiempo del reloj.
El valor exacto de ACWR ya se muestra en la interfaz. NUNCA lo menciones con cifra (nada de "0.95x") en el texto. Si necesitas referirte a la carga, usa lenguaje cualitativo ("tu carga está en zona segura"), sin número.
El tiempo restante hasta la carrera y la proyección de meta (con su ritmo) ya vienen calculados y se muestran en la interfaz — no los recalcules ni los menciones con una cifra propia. Si mencionás días restantes, usá EXACTAMENTE {dias_restantes if dias_restantes is not None else "N/A"} (no otro número).
Cualquier ritmo que menciones va en formato min:seg (ej. "5:38"), nunca decimal (nunca "5.63").
RITMO PROYECTADO VS. OBJETIVO — regla estricta: {carrera.get('nombre','')} no tiene un tiempoObjetivo declarado por el atleta salvo que se indique abajo. El ritmo de la proyección (si aparece) es una ESTIMACIÓN, no una meta que el atleta fijó. Nunca lo llames "ritmo objetivo" ni "ritmo de maratón objetivo". Si lo usás como referencia para una sesión, decí explícitamente "ritmo proyectado" y prescribí un ritmo IGUAL o MÁS LENTO que esa proyección, nunca más rápido.
FRECUENCIA CARDIACA — regla estricta: no existen zonas personales de FC en los datos disponibles. Nunca interpretes un valor de FC (bpm) como "zona aeróbica", "esfuerzo controlado", "esfuerzo sostenible" ni ninguna afirmación fisiológica similar. Podés reportar la cifra y su tendencia (sube/baja/estable), nada más.
AUSENCIA DE DATOS DE DOLOR/FATIGA — regla estricta: no hay ningún campo de dolor o fatiga autorreportada en los datos. La ausencia de esa señal NO es evidencia de que el atleta esté bien. Si injuryRisk.level es "low", el campo signal DEBE incluir textualmente una de estas frases: {list(HEDGE_PHRASES_AUSENCIA_DATOS)}. Si la carga reciente es muy baja o hay señales mixtas y preferís no afirmar nada, usa injuryRisk.level="unknown" en vez de "low".
No confundas: (1) el ritmo objetivo declarado, (2) el ritmo de la proyección actual, (3) el ritmo prescrito para una sesión.
No afirmes causalidad cuando los datos solo muestran correlación. No inventes ni infieras una zona anatómica, lesión o diagnóstico: PULSE evalúa señales de carga y recuperación, no diagnostica. Si no hay evidencia explícita de dolor o lesión en los datos, injuryRisk.area debe ser null.
keyMetrics: máximo {MAX_KEY_METRICS} elementos. Elegí solo las más relevantes de esta semana.
El weeklyPlan cubre EXACTAMENTE del lunes {lunes_semana_actual.isoformat()} al domingo {domingo_plan.isoformat()}, siete días una sola vez.
DÍAS YA TRANSCURRIDOS de esta semana — el backend va a reemplazar lo que pongas en estos días de todos modos (con la actividad real o "sin actividad registrada"), así que no gastes esfuerzo en prescribirlos y NO los cuentes como pendientes:
{chr(10).join("  - " + l for l in lineas_completados) if lineas_completados else "  - ninguno, hoy es lunes"}
Días que SÍ tenés que planificar de verdad: {", ".join(dias_restantes_semana) if dias_restantes_semana else "ninguno, toda la semana ya transcurrió"}.
RESTRICCIONES DE LA SEMANA COMPLETA — contexto, NO es lo que tenés que respetar directamente (ver RESTANTES abajo):
  - Fase respecto al objetivo: {constraints['goal_phase'] or 'sin objetivo activo'}
  - Dirección de carga: {constraints['load_direction']}
  - Rango de volumen de running de TODA la semana (completado + prescrito): {_fmt_range(constraints['running_km_range'])}
  - Rango del fondo largo permitido (una sola sesión): {_fmt_range(constraints['long_run_range'])}
  - Máximo de sesiones de running de la semana completa (TECHO): {constraints['running_sessions_max']}
  - Máximo de sesiones duras de la semana completa (TECHO): {constraints['hard_sessions_max']}
  - Mínimo de días de descanso o recuperación en la semana: {constraints['recovery_days_min']}
  - Máximo de días consecutivos corriendo (cuenta completado + prescrito): {constraints['max_consecutive_running_days']}
  - Estímulos sugeridos: {', '.join(constraints['recommended_stimuli'])}
RESTRICCIONES RESTANTES — esto es lo que SÍ tenés que respetar al prescribir los días que faltan, ya con lo ya corrido esta semana descontado:
  - Ya corrido esta semana: {restantes['completed_km']} km en {restantes['completed_running_sessions']} sesión/es{' (incluye 1 sesión dura)' if restantes['completed_hard_sessions'] else ''}
  - Volumen que TODAVÍA podés prescribir: {_fmt_range(restantes['remaining_km_range'])}
  - Sesiones de running que TODAVÍA podés agregar (TECHO, no obligatorio): {restantes['remaining_sessions_max']}
  - Sesiones duras que TODAVÍA podés agregar: {restantes['remaining_hard_sessions_max']}
Si remaining_sessions_max es 0, TODOS los días restantes deben ser Descanso o recuperación muy suave sin kilometraje real. Si remaining_hard_sessions_max es 0, ninguna sesión restante puede ser dura (tempo/series/umbral), sin importar lo que diga el rango de fondo largo.
Si la fase es "taper" o "race_week", el plan DEBE reducir volumen respecto a semanas previas y priorizar la llegada fresca a la carrera — nunca proponer una semana de construcción o un salto de volumen en esta fase.
Cada sesión debe incluir purpose: la adaptación buscada en palabras simples. No prescribas ritmos más rápidos que los respaldados por los PRs verificados y la proyección actual.
El plan y el análisis son una misma recomendación: el objetivo y la justificación del plan deben explicar qué se mantiene, qué se ajusta y por qué.
Disponibilidad declarada tiene prioridad absoluta; si no existe, conserva el patrón habitual de las últimas 8 semanas salvo que las restricciones de arriba obliguen a cambiarlo — en ese caso explica el motivo brevemente en notes.
{race_prompt_block}SCHEMA: responde ÚNICAMENTE con JSON válido, sin markdown, sin backticks, y SOLO con estas llaves de nivel superior: semana, score, headline, subheadline, readiness, aiVerdict, strengths, warnings, keyMetrics, weeklyPlan, injuryRisk. NO incluyas funFact, seoulTip, ni weekPlan (weeklyPlan es la única fuente del plan). injuryRisk es {{"level":"low|medium|high|unknown","signal":"señal breve de sobrecarga o recuperación, no una cifra","area":null,"action":"acción concreta"}} — nunca agregues "score" ni "topRisk"."""

    meta_linea = (
        f"Meta: {carrera.get('nombre', '')} el {carrera.get('fecha', 'TBD')} (label {carrera.get('label', '')})"
        if tiene_meta else "Sin meta de carrera definida"
    )

    user_prompt = f"""Genera análisis Pulse semanal (schema v2.1).

ATLETA: {nombre}
{meta_linea}
{f"Faltan {dias_restantes} días para {carrera.get('nombre', '')} (calculado desde hoy, {fecha_generacion.isoformat()})." if dias_restantes is not None else ""}
{f"Tu proyección para {carrera.get('nombre', '')} ({km_meta}km) ya calculada: {proyeccion['tiempo']} a {proyeccion['ritmo']}/km (ritmo PROYECTADO, no objetivo declarado). {proyeccion['contexto']}" if proyeccion else ""}
{f"Tiempo objetivo declarado por el atleta: {tiempo_objetivo}." if tiempo_objetivo else "Tiempo objetivo declarado por el atleta: no disponible — no existe un ritmo objetivo, solo el proyectado."}
{prs_str}
{race_status_linea}

SEMANA ANALIZADA: {lunes_analizado.isoformat()}/{domingo_analizado.isoformat()}

COMPARACIONES SEMANALES (calculadas por Python -- respeta estas direcciones, no las recalcules):
{_fmt_comparisons(comparisons)}

ÚLTIMA SESIÓN DE RUNNING: {recent_run.get('name', 'N/A')} ({recent_run.get('date', 'N/A')})
{recent_run.get('dist_km', 0)}km | {recent_run.get('pace', 'N/A')}/km | {recent_run.get('hr', 0)}bpm

CONTEXTO HISTÓRICO ({len(activities_cerradas)} actividades totales):
{resumen}
Estado de tu carga (ACWR): {estado_carga}

PATRÓN HABITUAL DE LAS ÚLTIMAS 8 SEMANAS:
{patron_semanal}
Disponibilidad declarada: {disponibilidad}

SEMANA QUE DEBES PLANIFICAR: {lunes_semana_actual.isoformat()}/{domingo_plan.isoformat()}
Ya transcurrido esta semana:
{chr(10).join("  " + l for l in lineas_completados) if lineas_completados else "  ninguno"}
Días disponibles para planificar de verdad: {", ".join(dias_restantes_semana) if dias_restantes_semana else "ninguno"}
Volumen restante permitido: {_fmt_range(restantes['remaining_km_range'])} | Sesiones restantes permitidas: {restantes['remaining_sessions_max']} | Sesiones duras restantes permitidas: {restantes['remaining_hard_sessions_max']}

Responde con JSON: {{"semana":"rango de la semana analizada","score":0-100,"headline":"máx 8 palabras","subheadline":"máx 12 palabras","readiness":0-100,"aiVerdict":"máx 3 oraciones, 80-120 palabras, segunda persona","strengths":["s1","s2","s3"],"warnings":["w1","w2"],"keyMetrics":[{{"label":"nombre","value":"valor","trend":"up|down|stable","status":"green|yellow|red","note":"nota corta"}}] (máximo {MAX_KEY_METRICS} elementos),"weeklyPlan":{{"objective":"objetivo concreto de esta semana, máx 16 palabras","rationale":"1-2 oraciones que conectan el análisis Pulse con el plan","sessions":[{{"day":"Lun|Mar|Mié|Jue|Vie|Sáb|Dom","type":"tipo de sesión o Descanso (para días ya transcurridos el backend reemplaza lo que pongas, no hace falta que aciertes)","km":"X km o —","notes":"instrucción concreta con intensidad o ritmo cuando corresponda, ritmo en min:seg","purpose":"adaptación buscada"}}]}},"injuryRisk":{{"level":"low|medium|high|unknown","signal":"","area":null,"action":""}}}}"""

    return system_prompt, user_prompt, proyeccion, tiene_meta, prs_descartados


# ── 3. Llamada a Anthropic + normalización ───────────────────────────

def _extraer_json(texto):
    limpio = texto.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(limpio)


DIAS_YA_RESUELTOS = ("Completado", "Día transcurrido sin actividad registrada")


def aplicar_dias_completados(plan_normalizado, semana_en_curso, fecha_generacion):
    """
    Fix v3: para cada día < fecha_generacion, reemplaza la sesión del
    modelo con lo que REALMENTE pasó, leído del export (semana_en_curso) —
    nunca con un placeholder ciego, y nunca inventando una prescripción
    retroactiva. summary pasa a representar la semana COMPLETA proyectada
    (completado + prescrito), que es lo que espera mostrar la UI.
    Devuelve (plan_normalizado, weekly_totals) — weekly_totals separa
    completado/prescrito/proyectado para el reporte de validación (no es
    parte del contrato de weeklyPlan que consume el dashboard).
    """
    dias = semana_en_curso["dias"]
    sesiones = []
    for s in plan_normalizado["sessions"]:
        info = dias.get(s["date"])
        if info is None:
            sesiones.append(s)  # día futuro: respeta lo que prescribió el modelo
            continue
        if info["estado"] == "completado":
            km_txt = f"{info['running_km']} km" if info["running_km"] > 0 else "—"
            nombres_str = "; ".join(n for n in info["nombres"] if n) or ", ".join(t for t in info["tipos"] if t) or "actividad"
            sesiones.append({
                "date": s["date"], "day": s["day"], "type": "Completado", "km": km_txt,
                "notes": f"Ya realizado: {nombres_str}." + (" Sesión dura." if info["es_sesion_dura"] else ""),
                "purpose": "Registrado automáticamente desde tus datos reales",
            })
        else:
            sesiones.append({
                "date": s["date"], "day": s["day"], "type": "Día transcurrido sin actividad registrada",
                "km": "—", "notes": "No hay actividad registrada para este día; no se prescribe retroactivamente.",
                "purpose": "—",
            })

    prescrito_km, prescrito_sesiones, prescrito_fuerza = 0.0, 0, 0
    for s in sesiones:
        if s["type"] in DIAS_YA_RESUELTOS:
            continue
        m = re.search(r"\d+(?:[.,]\d+)?", s.get("km") or "")
        if m:
            prescrito_km += float(m.group(0).replace(",", "."))
            prescrito_sesiones += 1
        if "fuerza" in (s.get("type") or "").lower():
            prescrito_fuerza += 1
    prescrito_km = round(prescrito_km, 1)

    completado_km = semana_en_curso["completed_km"]
    completado_sesiones = semana_en_curso["completed_running_sessions"]

    plan_normalizado["sessions"] = sesiones
    plan_normalizado["summary"] = {
        "totalKm": round(completado_km + prescrito_km, 1),
        "runningSessions": completado_sesiones + prescrito_sesiones,
        "strengthSessions": prescrito_fuerza,  # completado no distingue fuerza hoy (no hay ese caso en el export actual)
    }

    weekly_totals = {
        "completedKm": completado_km, "prescribedKm": prescrito_km,
        "projectedTotalKm": round(completado_km + prescrito_km, 1),
        "completedSessions": completado_sesiones, "prescribedSessions": prescrito_sesiones,
        "projectedTotalSessions": completado_sesiones + prescrito_sesiones,
    }
    return plan_normalizado, weekly_totals


def _limpiar_schema(parsed, tj, lunes_semana_actual, domingo_plan, proyeccion, fecha_generacion,
                     dias_restantes, semana_en_curso):
    """Fuerza el contrato v2.1 independientemente de lo que haya devuelto el
    modelo: retira funFact/seoulTip/weekPlan, normaliza injuryRisk, recorta
    keyMetrics, aplica lo REALMENTE completado esta semana (fix v3), inyecta
    projection. Devuelve (parsed, weekly_totals)."""
    for k in FORBIDDEN_TOP_LEVEL_KEYS:
        parsed.pop(k, None)

    plan_crudo = parsed.get("weeklyPlan") or {"sessions": parsed.get("weekPlan", [])}
    plan_normalizado = tj.normalizar_plan_semanal(plan_crudo, lunes_semana_actual, domingo_plan)
    parsed["weeklyPlan"], weekly_totals = aplicar_dias_completados(plan_normalizado, semana_en_curso, fecha_generacion)

    injury = parsed.get("injuryRisk") if isinstance(parsed.get("injuryRisk"), dict) else {}
    parsed["injuryRisk"] = {
        "level": injury.get("level") if injury.get("level") in INJURY_LEVELS_VALIDOS else "low",
        "signal": injury.get("signal", ""),
        "area": None,  # nunca se infiere zona anatómica sin evidencia estructurada (no existe en el input)
        "action": injury.get("action", ""),
    }

    if isinstance(parsed.get("keyMetrics"), list) and len(parsed["keyMetrics"]) > MAX_KEY_METRICS:
        parsed["keyMetrics"] = parsed["keyMetrics"][:MAX_KEY_METRICS]

    parsed["projection"] = proyeccion
    return parsed, weekly_totals


def llamar_anthropic(system_prompt, messages, api_key):
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2500,
        temperature=0.3,
        system=system_prompt,
        messages=messages,
    )
    return response.content[0].text.strip()


# ── 4. Validación individual (12 checks, enumeración completa) ────────

def _contar_dias_consecutivos_running(sessions, race_date=None):
    """La racha cuenta días con km real, sean COMPLETADOS (de verdad
    corridos) o prescritos — un lunes+martes ya corridos seguidos de un
    miércoles prescrito con km es una racha de 3, no de 1 (punto 6).

    race_date (opcional): el día de la carrera meta, cuando cae dentro de
    la semana a planificar -- es un EVENTO, no entrenamiento (decisión de
    producto), así que nunca cuenta como día de running para esta racha.
    Se fuerza corre=False para esa fecha en vez de quitarla de la lista,
    para que siga cortando la adyacencia calendario entre el día anterior
    y el día siguiente (un shakeout el jueves y una carrera el viernes no
    deben sumar racha; una carrera el viernes y un trote de recuperación
    el sábado tampoco)."""
    max_racha = racha = 0
    for s in sessions:
        if race_date and s.get("date") == race_date:
            corre = False
        else:
            corre = (s.get("type") != "Día transcurrido sin actividad registrada"
                      and s.get("km") not in (None, "", "—", "-"))
        racha = racha + 1 if corre else 0
        max_racha = max(max_racha, racha)
    return max_racha


def _es_sesion_dura_prescrita(s):
    tipo = (s.get("type") or "").lower()
    return any(kw in tipo for kw in HARD_SESSION_KEYWORDS)


def validar_pulse_v2(parsed, meta_esperada, constraints, proyeccion_esperada, dias_restantes,
                      meta_diasprep=None, fecha_generacion=None,
                      semana_en_curso=None, restantes=None, weekly_totals=None, race_status=None):
    """
    Devuelve (ok, checks). checks es la lista COMPLETA (pass y fail), no
    solo las violaciones — cada item es {"check", "status", "evidence",
    "message"}. generar_pulse_v2() además expone una vista filtrada
    ("violations") para no romper el código que ya la consumía.

    semana_en_curso / restantes / weekly_totals (fix v3): separan
    explícitamente lo YA COMPLETADO esta semana de lo PRESCRITO y del
    TOTAL proyectado — sin esto, un candidato puede pasar comparando solo
    la prescripción restante contra los límites de la semana completa,
    ignorando que ya se corrió una parte de esa semana (bug real detectado
    con Fabiana: 13.5km/2 sesiones ya corridas + 27km/3 sesiones prescritas
    = ~40.5km/5 sesiones reales, aunque cada mitad por separado "pasaba").
    """
    checks = []

    def record(name, passed, message, evidence=None):
        checks.append({"check": name, "status": "pass" if passed else "fail",
                        "message": message, "evidence": evidence})

    carrera = meta_esperada.get("metaCarrera", {}) if meta_esperada else {}
    wp = parsed.get("weeklyPlan") or {}
    sesiones = wp.get("sessions") or []
    injury = parsed.get("injuryRisk") or {}
    verdict = parsed.get("aiVerdict") or ""

    # race_status (decisión de producto #5): si la carrera meta cae dentro de
    # la semana a planificar, su sesión es un EVENTO, no volumen de
    # entrenamiento -- se excluye de los checks de presupuesto de abajo
    # (volumen semana completa, fondo largo, techo de sesiones, restantes) y
    # se valida por separado (día correcto, distancia compatible).
    race_status = race_status or {}
    race_date_semana = race_status.get("race_date") if race_status.get("race_falls_in_planning_week") else None
    sesion_carrera = next((s for s in sesiones if race_date_semana and s.get("date") == race_date_semana), None)
    km_carrera_prescrita = 0.0
    if sesion_carrera is not None:
        m_carrera = re.search(r"\d+(?:[.,]\d+)?", sesion_carrera.get("km") or "")
        if m_carrera:
            km_carrera_prescrita = float(m_carrera.group(0).replace(",", "."))

    # 1-2. meta: usuario/fecha/tipo/nombre exactos
    if carrera:
        nombre_esp, fecha_esp, label_esp = carrera.get("nombre"), carrera.get("fecha"), carrera.get("label")
        completa = bool(nombre_esp and fecha_esp and label_esp)
        record("meta_config_completa", completa, "metaCarrera del roster tiene nombre/fecha/label",
               evidence=carrera)
        record("meta_reconocida", dias_restantes is not None,
               "dias_restantes se calculó (implica que la meta llegó al motor)",
               evidence={"dias_restantes": dias_restantes})
    else:
        record("meta_config_completa", True, "sin meta configurada para este usuario, no aplica", evidence=None)

    # 3. ventana semanal correcta + 7 días en orden
    orden_ok = len(sesiones) == 7 and [s.get("day") for s in sesiones] == DIAS_ORDEN
    record("ventana_semanal", orden_ok,
           "weeklyPlan.sessions tiene 7 días en orden Lun..Dom" if orden_ok
           else f"esperado 7 días en orden, encontrado {[s.get('day') for s in sesiones]}",
           evidence=[s.get("day") for s in sesiones])

    # 4. ausencia de campos retirados
    for k in FORBIDDEN_TOP_LEVEL_KEYS:
        record("campo_retirado", k not in parsed, f"'{k}' no debe existir en el schema v2.1",
               evidence=parsed.get(k) if k in parsed else None)
    for k in FORBIDDEN_INJURY_KEYS:
        record("campo_retirado_injury", k not in injury, f"injuryRisk.{k} no debe existir en el schema v2.1",
               evidence=injury.get(k) if k in injury else None)
    record("sin_inferencia_anatomica", injury.get("area") is None,
           "injuryRisk.area debe ser null sin evidencia estructurada", evidence=injury.get("area"))

    # 5. sin duplicado weekPlan/weeklyPlan
    record("sin_duplicado_weekplan", "weekPlan" not in parsed,
           "weekPlan no debe coexistir con weeklyPlan", evidence="weekPlan" in parsed)

    # 6-9. límites de carga / consecutivos / descanso / fase (semana COMPLETA: completado + prescrito)
    # La carrera meta (si cae esta semana) se descuenta de totalKm antes de
    # comparar contra el presupuesto de ENTRENAMIENTO -- ver nota race_status arriba.
    summary = wp.get("summary") or {}
    total_km = summary.get("totalKm")
    total_km_sin_carrera = (round(total_km - km_carrera_prescrita, 2)
                             if total_km is not None else None)
    if constraints.get("running_km_range") and total_km_sin_carrera is not None:
        lo, hi = constraints["running_km_range"]
        ok = total_km_sin_carrera <= hi * 1.05
        record("limite_volumen_semana_completa", ok,
               f"totalKm proyectado sin la carrera meta (completado+prescrito)={total_km_sin_carrera} "
               f"vs. rango de la semana completa {constraints['running_km_range']}"
               + (f" (se excluyeron {km_carrera_prescrita}km de la carrera meta del {race_date_semana})"
                  if km_carrera_prescrita else ""),
               evidence={"total_km": total_km, "total_km_sin_carrera": total_km_sin_carrera,
                         "range": constraints["running_km_range"]})
    else:
        record("limite_volumen_semana_completa", True, "sin rango calculable, no se penaliza", evidence=None)

    kms_sesion = []
    for s in sesiones:
        if s.get("type") == "Día transcurrido sin actividad registrada":
            continue
        if race_date_semana and s.get("date") == race_date_semana:
            continue  # evento, no cuenta contra el fondo largo de entrenamiento
        m = re.search(r"\d+(?:[.,]\d+)?", s.get("km") or "")
        if m:
            kms_sesion.append(float(m.group(0).replace(",", ".")))
    fondo_max = max(kms_sesion) if kms_sesion else 0
    if constraints.get("long_run_range") and kms_sesion:
        lo, hi = constraints["long_run_range"]
        ok = fondo_max <= hi * 1.05
        record("limite_fondo_largo", ok,
               f"sesión más larga (completada o prescrita, sin contar la carrera meta)={fondo_max}km "
               f"vs. rango permitido {constraints['long_run_range']}",
               evidence={"fondo_max": fondo_max, "range": constraints["long_run_range"]})
    else:
        record("limite_fondo_largo", True, "sin rango calculable, no se penaliza", evidence=None)

    # Fix #1 (se mantiene): running_sessions_max es un TECHO sobre la semana
    # COMPLETA (completado + prescrito), nunca un valor exacto. La sesión de
    # la carrera meta (si aplica) no cuenta -- es un evento, no una salida más.
    n_sesiones_running = sum(1 for s in sesiones if s.get("type") != "Día transcurrido sin actividad registrada"
                              and s.get("km") not in (None, "", "—", "-")
                              and not (race_date_semana and s.get("date") == race_date_semana))
    max_sesiones = constraints.get("running_sessions_max")
    ok_sesiones = max_sesiones is None or n_sesiones_running <= max_sesiones
    record("limite_sesiones_running_semana_completa", ok_sesiones,
           f"{n_sesiones_running} sesiones de entrenamiento (completadas+prescritas, sin contar la carrera meta) "
           f"vs. techo semanal {max_sesiones} (regla: <=, no ==)",
           evidence={"n_sesiones": n_sesiones_running, "max": max_sesiones})

    # Decisión de producto #5: si la carrera meta cae esta semana, el modelo
    # NO puede omitirla solo porque su distancia excede el presupuesto normal
    # -- se valida por separado (día correcto, distancia compatible con la
    # categoría de la meta cuando esa categoría está soportada).
    if race_status.get("race_falls_in_planning_week"):
        tiene_sesion_carrera = bool(
            sesion_carrera and km_carrera_prescrita > 0
            and sesion_carrera.get("type") not in ("Descanso", "Día transcurrido sin actividad registrada")
        )
        record("carrera_meta_presente_en_semana", tiene_sesion_carrera,
               f"la semana a planificar incluye la carrera meta el {race_date_semana} -- weeklyPlan DEBE "
               f"tener una sesión ese día con distancia real, nunca descanso ni omitirla por exceder el "
               f"presupuesto normal de entrenamiento",
               evidence={"race_date": race_date_semana, "sesion_encontrada": sesion_carrera})

        categoria_carrera = _categoria_distancia_meta(carrera.get("label", "")) if carrera else None
        if categoria_carrera and tiene_sesion_carrera:
            lo_r, hi_r = RACE_DISTANCE_BANDS_KM[categoria_carrera]
            ok_dist_carrera = lo_r <= km_carrera_prescrita <= hi_r
            record("carrera_meta_distancia_coherente", ok_dist_carrera,
                   f"distancia prescrita para la carrera meta ({km_carrera_prescrita}km) vs. banda esperada "
                   f"para {categoria_carrera} ({lo_r}-{hi_r}km)",
                   evidence={"km_carrera": km_carrera_prescrita, "banda": [lo_r, hi_r]})

    racha = _contar_dias_consecutivos_running(sesiones, race_date=race_date_semana)
    ok_racha = racha <= constraints.get("max_consecutive_running_days", 3)
    record("dias_consecutivos", ok_racha,
           f"{racha} días consecutivos corriendo (incluye completados y prescritos, sin contar la carrera meta como día "
           f"de entrenamiento) vs. máximo {constraints.get('max_consecutive_running_days')}",
           evidence=racha)

    dias_descanso = sum(1 for s in sesiones if s.get("type") == "Día transcurrido sin actividad registrada"
                         or s.get("km") in (None, "", "—", "-"))
    ok_descanso = dias_descanso >= constraints.get("recovery_days_min", 0)
    record("descanso_minimo", ok_descanso,
           f"{dias_descanso} días de descanso/sin actividad vs. mínimo {constraints.get('recovery_days_min')}",
           evidence=dias_descanso)

    # Fix v3, punto 6: la PRESCRIPCIÓN (sin contar lo ya completado) no
    # puede exceder lo que efectivamente queda disponible. La carrera meta
    # (si aplica) tampoco cuenta acá -- mismo criterio que arriba.
    if restantes is not None and weekly_totals is not None:
        prescribed_km = round(weekly_totals["prescribedKm"] - km_carrera_prescrita, 2)
        prescribed_sessions = weekly_totals["prescribedSessions"] - (1 if sesion_carrera and km_carrera_prescrita > 0 else 0)
        if restantes.get("remaining_km_range") is not None:
            ok_km_restante = prescribed_km <= restantes["remaining_km_range"][1] * 1.05
            record("prescripcion_dentro_de_lo_restante_km", ok_km_restante,
                   f"prescribedKm sin la carrera meta={prescribed_km} vs. remaining_km_range={restantes['remaining_km_range']}",
                   evidence={"prescribed_km": prescribed_km, "remaining_km_range": restantes["remaining_km_range"]})
        ok_sesiones_restante = prescribed_sessions <= restantes.get("remaining_sessions_max", 99)
        record("prescripcion_dentro_de_lo_restante_sesiones", ok_sesiones_restante,
               f"prescribedSessions sin la carrera meta={prescribed_sessions} vs. remaining_sessions_max={restantes.get('remaining_sessions_max')}",
               evidence={"prescribed_sessions": prescribed_sessions, "remaining_sessions_max": restantes.get("remaining_sessions_max")})

        sesiones_duras_prescritas = sum(1 for s in sesiones
                                         if s.get("type") not in DIAS_YA_RESUELTOS and _es_sesion_dura_prescrita(s))
        ok_duras = sesiones_duras_prescritas <= restantes.get("remaining_hard_sessions_max", 99)
        record("sesiones_duras_prescritas_dentro_de_lo_restante", ok_duras,
               f"{sesiones_duras_prescritas} sesión/es dura/s prescrita/s vs. remaining_hard_sessions_max="
               f"{restantes.get('remaining_hard_sessions_max')} (las ya completadas se descuentan del techo semanal por evidencia de nombre)",
               evidence={"prescritas_duras": sesiones_duras_prescritas,
                         "remaining_hard_sessions_max": restantes.get("remaining_hard_sessions_max")})

    # Fix v3: coherencia interna — completado+prescrito debe ser exactamente
    # el total que muestra weeklyPlan.summary (la UI solo ve ese campo).
    if weekly_totals is not None:
        ok_proy_km = abs((weekly_totals["completedKm"] + weekly_totals["prescribedKm"]) - (total_km or 0)) < 0.15
        record("projected_total_consistente", ok_proy_km,
               f"completedKm({weekly_totals['completedKm']})+prescribedKm({weekly_totals['prescribedKm']}) "
               f"debe igualar weeklyPlan.summary.totalKm({total_km})",
               evidence={"completed": weekly_totals["completedKm"], "prescribed": weekly_totals["prescribedKm"],
                         "summary_total": total_km})

    # Fix v3: los días marcados como completados/sin-actividad deben
    # coincidir exactamente con lo que hay (o no hay) en el export.
    if semana_en_curso is not None:
        discrepancias = []
        for s in sesiones:
            info = semana_en_curso["dias"].get(s["date"])
            if info is None:
                continue  # día futuro, no aplica
            esperado = "Completado" if info["estado"] == "completado" else "Día transcurrido sin actividad registrada"
            if s.get("type") != esperado:
                discrepancias.append({"date": s["date"], "esperado": esperado, "encontrado": s.get("type")})
        record("dias_completados_coinciden_con_export", len(discrepancias) == 0,
               "cada día ya transcurrido debe reflejar exactamente lo que hay (o no hay) en el export"
               if not discrepancias else f"discrepancias: {discrepancias}",
               evidence=discrepancias or None)

    # Fix #2 (se mantiene, ahora con las dos etiquetas de día resuelto):
    # ningún día antes de fecha_generacion debe llevar una prescripción nueva.
    if fecha_generacion:
        dias_retroactivos = [
            s for s in sesiones
            if date.fromisoformat(s["date"]) < fecha_generacion and s.get("type") not in DIAS_YA_RESUELTOS
        ]
        record("sin_prescripcion_retroactiva", len(dias_retroactivos) == 0,
               "ningún día anterior a fecha_generacion debe tener una sesión prescrita nueva "
               "(debe ser Completado o Día transcurrido sin actividad registrada)",
               evidence=[s["date"] for s in dias_retroactivos])

    # 10. projection presente cuando hay datos suficientes
    if proyeccion_esperada is not None:
        record("projection_presente", bool(parsed.get("projection")),
               "había proyección calculable y projection no debe quedar vacío", evidence=parsed.get("projection"))
    else:
        record("projection_ausente_correctamente", not parsed.get("projection"),
               "no había proyección calculable, projection debe quedar vacío", evidence=parsed.get("projection"))

    # Fix #3: una única fecha de referencia — dias_restantes debe coincidir
    # con meta.metaCarrera.diasPrep del resultado final.
    if meta_diasprep is not None and dias_restantes is not None:
        record("consistencia_dias_restantes", meta_diasprep == dias_restantes,
               f"meta.diasPrep={meta_diasprep} debe ser igual a planning_constraints.dias_restantes={dias_restantes}",
               evidence={"diasPrep": meta_diasprep, "dias_restantes": dias_restantes})
    dias_en_texto = set(int(n) for n in re.findall(r"(\d+)\s*días?\b", verdict))
    if dias_restantes is not None and dias_en_texto:
        ok_texto = dias_en_texto.issubset({dias_restantes})
        record("dias_restantes_en_texto", ok_texto,
               f"cifras de días mencionadas en aiVerdict {sorted(dias_en_texto)} deben coincidir con {dias_restantes}",
               evidence=sorted(dias_en_texto))

    # 11. afirmaciones médicas/causales no respaldadas
    campos_libres = {
        "aiVerdict": verdict, "injuryRisk.signal": injury.get("signal") or "",
        "injuryRisk.action": injury.get("action") or "",
    }
    for s in sesiones:
        campos_libres[f"sesion_{s.get('day')}.notes"] = s.get("notes") or ""
    for km in (parsed.get("keyMetrics") or []):
        campos_libres[f"keyMetric_{km.get('label')}"] = km.get("note") or ""
    for i, s_ in enumerate(parsed.get("strengths") or []):
        campos_libres[f"strength_{i}"] = s_

    encontrados_medicos = [(campo, t) for campo, t in campos_libres.items()
                           for t_ in [t.lower()] for t in [t_]
                           if any(term in t for term in MEDICAL_TERMS)]
    record("sin_inferencia_medica", len(encontrados_medicos) == 0,
           "sin términos clínicos/anatómicos en texto libre" if not encontrados_medicos
           else f"término clínico encontrado en: {[c for c, _ in encontrados_medicos]}",
           evidence=encontrados_medicos or None)

    # Fix #5: cifra de FC + afirmación de zona/esfuerzo sin zonas personales
    encontrados_fc = []
    for campo, texto in campos_libres.items():
        t = texto.lower()
        if re.search(r"\b\d{2,3}\s*bpm\b", t) and any(frase in t for frase in FRASES_ZONA_FC_NO_RESPALDADAS):
            encontrados_fc.append(campo)
    record("sin_afirmacion_fc_no_respaldada", len(encontrados_fc) == 0,
           "sin afirmaciones de zona/esfuerzo fisiológico atadas a una cifra de FC" if not encontrados_fc
           else f"cifra de FC + afirmación de zona/esfuerzo en: {encontrados_fc}",
           evidence=encontrados_fc or None)

    # Fix #6: "sin fatiga"/injuryRisk low sin reconocer ausencia de datos
    if injury.get("level") == "low":
        tiene_hedge = any(frase in (injury.get("signal") or "").lower() for frase in HEDGE_PHRASES_AUSENCIA_DATOS)
        record("hedge_ausencia_datos_dolor_fatiga", tiene_hedge,
               "injuryRisk.level='low' debe incluir una frase canónica reconociendo que no hay datos de dolor/fatiga",
               evidence=injury.get("signal"))
    else:
        record("hedge_ausencia_datos_dolor_fatiga", True,
               "no aplica: injuryRisk.level no es 'low'", evidence=injury.get("level"))

    absolutas = re.findall(r"sin (?:señales? de )?fatiga(?: acumulada)?\b|no (?:presenta|hay|hubo) fatiga\b|sin dolor\b",
                            verdict.lower())
    record("sin_certeza_ausente_en_aiverdict", len(absolutas) == 0,
           "aiVerdict no debe afirmar en términos absolutos la ausencia de fatiga/dolor" if not absolutas
           else "aiVerdict usa lenguaje absoluto de ausencia de fatiga/dolor sin poder confirmarlo",
           evidence=absolutas or None)

    # Fix #4: ritmo proyectado mal etiquetado como "objetivo"
    tiene_tiempo_objetivo = bool(carrera.get("tiempoObjetivo") or carrera.get("targetTime") or carrera.get("tiempo_objetivo"))
    mal_etiquetado = []
    if not tiene_tiempo_objetivo:
        for campo, texto in campos_libres.items():
            if re.search(r"ritmo (de \w+ )?objetivo\b", texto.lower()):
                mal_etiquetado.append(campo)
    record("ritmo_proyectado_bien_etiquetado", len(mal_etiquetado) == 0,
           "sin tiempoObjetivo declarado, ningún ritmo debe llamarse 'ritmo objetivo'" if not mal_etiquetado
           else f"'ritmo objetivo' usado sin tiempoObjetivo declarado en: {mal_etiquetado}",
           evidence=mal_etiquetado or None)

    # 12. trazabilidad de ritmos/zonas/FC
    record("sin_cifra_acwr_en_texto", not re.search(r"\b\d\.\d{1,2}x\b", verdict),
           "aiVerdict no debe mencionar una cifra de ACWR (formato 0.NNx)", evidence=verdict)
    record("sin_guion_largo", not re.search(r"[—–]", verdict),
           "aiVerdict no debe usar guion largo/medio", evidence=verdict)
    record("sin_palabra_oficial", "oficial" not in verdict.lower(),
           "aiVerdict no debe usar la palabra 'oficial' para un tiempo del reloj", evidence=verdict)
    decimales = [t for t in campos_libres.values() if re.search(r"\b\d\.\d{2}\s*/\s*km\b", t)]
    record("sin_ritmo_decimal", len(decimales) == 0,
           "los ritmos deben estar en formato min:seg, no decimal", evidence=decimales or None)

    # 13. schema completo, tipos, enums
    faltantes = [k for k in REQUIRED_TOP_LEVEL_KEYS if k not in parsed]
    record("schema_completo", len(faltantes) == 0, f"llaves requeridas presentes (faltan: {faltantes})" if faltantes
           else "todas las llaves requeridas están presentes", evidence=faltantes or None)
    score_ok = isinstance(parsed.get("score"), (int, float)) and 0 <= parsed.get("score", -1) <= 100
    record("schema_score_valido", score_ok, f"score={parsed.get('score')!r}", evidence=parsed.get("score"))
    record("schema_strengths_max3", len(parsed.get("strengths") or []) <= 3,
           f"strengths tiene {len(parsed.get('strengths') or [])} elementos (máx 3)", evidence=parsed.get("strengths"))
    record("schema_warnings_max2", len(parsed.get("warnings") or []) <= 2,
           f"warnings tiene {len(parsed.get('warnings') or [])} elementos (máx 2)", evidence=parsed.get("warnings"))
    record("schema_keymetrics_max", len(parsed.get("keyMetrics") or []) <= MAX_KEY_METRICS,
           f"keyMetrics tiene {len(parsed.get('keyMetrics') or [])} elementos (máx {MAX_KEY_METRICS})",
           evidence=parsed.get("keyMetrics"))
    record("schema_injury_level_valido", injury.get("level") in INJURY_LEVELS_VALIDOS,
           f"injuryRisk.level={injury.get('level')!r}", evidence=injury.get("level"))

    ok = all(c["status"] == "pass" for c in checks)
    return ok, checks


# ── 5. Orquestación: generar + validar + un intento de reparación ────

def generar_pulse_v2(tj, activities, weekly, meta, profile, acwr_info, api_key, fecha_generacion=None):
    """
    Equivalente a generar_pulse() v1 pero con schema v2.1, planning
    constraints y validación con un único intento de reparación. Devuelve un
    dict con: status ("valid" | "valid_after_repair" | "invalid" | "error"),
    pulse (dict o None), validation ({"ok","checks","violations"}), attempts,
    dias_restantes (para que el runner sincronice meta.diasPrep), input_context,
    raw_responses.
    """
    fecha_generacion = fecha_generacion or date.today()
    lunes_analizado, domingo_analizado = tj.ultima_semana_completa(hoy=fecha_generacion)
    lunes_semana_actual = lunes_analizado + timedelta(days=7)
    domingo_plan = lunes_semana_actual + timedelta(days=6)
    semana_analizada_str = f"{lunes_analizado.isoformat()}/{domingo_analizado.isoformat()}"

    activities_cerradas = [a for a in activities if a.get("date", "9999") < lunes_semana_actual.isoformat()]
    weekly_cerrado = [w for w in weekly if w["week"].split("/")[0] < lunes_semana_actual.isoformat()]

    carrera = meta.get("metaCarrera", {})
    tiene_meta = bool(carrera.get("nombre") and carrera.get("nombre") != "¿Cuál es tu próxima carrera?")

    # race_status (auditoría race_status): evidencia de finalización de la
    # carrera meta, calculada ANTES de planning_constraints -- goal_phase
    # ("post_race" vs. "race_unconfirmed") depende de este resultado. Usa
    # `activities` completo (no activities_cerradas): la ventana de evidencia
    # de una carrera es de la META, no de la semana analizada (puede caer
    # dentro de la semana a planificar, ver race_falls_in_planning_week).
    race_status = calcular_race_status(
        activities, carrera, tiene_meta, fecha_generacion, lunes_semana_actual, domingo_plan,
    )

    constraints = calcular_planning_constraints(
        activities_cerradas, weekly_cerrado, acwr_info, carrera, tiene_meta, fecha_generacion,
        race_status=race_status,
    )
    dias_restantes = constraints["dias_restantes"]

    # Fix v3: lo que YA pasó esta semana se lee del export completo
    # (activities, no activities_cerradas — nunca se mezcla con la semana
    # cerrada de análisis) y se descuenta de lo que el modelo puede prescribir.
    semana_en_curso = calcular_semana_en_curso(activities, lunes_semana_actual, domingo_plan, fecha_generacion)
    restantes = calcular_restricciones_residuales(constraints, semana_en_curso)

    # Weekly Comparison Engine: exclusivamente semanas cerradas (activities_
    # cerradas/weekly_cerrado, nunca `activities`/`weekly` completos) -- la
    # semana en curso nunca contamina comparisons, igual que ya pasa con el
    # resto de generar_pulse_v2(). No se modifica calcular_comparaciones_
    # pulse() en esta iteración, solo se integra su resultado al prompt.
    comparisons = calcular_comparaciones_pulse(
        activities_cerradas, weekly_cerrado, lunes_analizado, domingo_analizado, acwr_info=acwr_info,
    )

    system_prompt, user_prompt, proyeccion, _, prs_descartados = construir_prompts_v2(
        tj, activities_cerradas, weekly_cerrado, meta, profile, acwr_info,
        lunes_analizado, domingo_analizado, lunes_semana_actual, domingo_plan, fecha_generacion, constraints,
        semana_en_curso, restantes, comparisons, race_status,
    )

    input_context = {
        "prompt_version": PROMPT_VERSION,
        "constraints_version": CONSTRAINTS_VERSION,
        "fecha_generacion": fecha_generacion.isoformat(),
        "semana_analizada": semana_analizada_str,
        "semana_a_planificar": f"{lunes_semana_actual.isoformat()}/{domingo_plan.isoformat()}",
        "meta_carrera": carrera,
        "dias_restantes": dias_restantes,
        "planning_constraints": constraints,
        "semana_en_curso": semana_en_curso,
        "restricciones_residuales": restantes,
        "prs_descartados_por_atipicos": prs_descartados,
        "acwr_status": (acwr_info or {}).get("status"),
        "comparisons": comparisons,
        "race_status": race_status,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
    }

    raw_responses = []
    messages = [{"role": "user", "content": user_prompt}]

    try:
        raw = llamar_anthropic(system_prompt, messages, api_key)
    except Exception as e:
        return {"status": "error", "pulse": None, "validation": None, "attempts": 0,
                "dias_restantes": dias_restantes,
                "input_context": input_context, "raw_responses": [], "error": str(e)}

    raw_responses.append(raw)

    def intentar_parsear_y_validar(texto_crudo):
        try:
            parsed = _extraer_json(texto_crudo)
        except Exception as e:
            return None, None, (False, [{"check": "json_valido", "status": "fail", "message": str(e), "evidence": None}])
        parsed, weekly_totals = _limpiar_schema(parsed, tj, lunes_semana_actual, domingo_plan, proyeccion,
                                                 fecha_generacion, dias_restantes, semana_en_curso)
        ok, checks = validar_pulse_v2(parsed, meta, constraints, proyeccion, dias_restantes,
                                       meta_diasprep=dias_restantes, fecha_generacion=fecha_generacion,
                                       semana_en_curso=semana_en_curso, restantes=restantes, weekly_totals=weekly_totals,
                                       race_status=race_status)
        return parsed, weekly_totals, (ok, checks)

    def validation_view(ok, checks, weekly_totals):
        return {"ok": ok, "checks": checks, "violations": [c for c in checks if c["status"] == "fail"],
                "weekly_totals": weekly_totals}

    parsed, weekly_totals, (ok, checks) = intentar_parsear_y_validar(raw)
    attempts = 1

    if ok:
        return {"status": "valid", "pulse": parsed, "validation": validation_view(ok, checks, weekly_totals),
                "attempts": attempts, "dias_restantes": dias_restantes,
                "input_context": input_context, "raw_responses": raw_responses}

    # ── único intento de reparación automática ──
    violaciones = [c for c in checks if c["status"] == "fail"]
    reparo_prompt = (
        "Tu respuesta anterior no cumplió el contrato v2.1. Corrige EXACTAMENTE estos problemas y "
        "responde de nuevo con el JSON COMPLETO (mismo schema, sin markdown). Recordá: los días ya "
        "transcurridos de esta semana ya tienen actividad real registrada (o no), no los vuelvas a "
        "prescribir, y la prescripción de los días restantes debe caber dentro de los límites "
        "RESTANTES, no de los límites de la semana completa:\n"
        + "\n".join(f"- [{item['check']}] {item['message']}" for item in violaciones)
    )
    messages = messages + [
        {"role": "assistant", "content": raw},
        {"role": "user", "content": reparo_prompt},
    ]
    try:
        raw2 = llamar_anthropic(system_prompt, messages, api_key)
        raw_responses.append(raw2)
        parsed2, weekly_totals2, (ok2, checks2) = intentar_parsear_y_validar(raw2)
        attempts = 2
    except Exception as e:
        return {"status": "invalid", "pulse": parsed,
                "validation": validation_view(ok, checks, weekly_totals),
                "attempts": attempts, "dias_restantes": dias_restantes,
                "input_context": input_context, "raw_responses": raw_responses, "repair_error": str(e)}

    if ok2:
        vista = validation_view(ok2, checks2, weekly_totals2)
        vista["checks_before_repair"] = checks
        return {"status": "valid_after_repair", "pulse": parsed2, "validation": vista,
                "attempts": attempts, "dias_restantes": dias_restantes,
                "input_context": input_context, "raw_responses": raw_responses}

    vista = validation_view(ok2, checks2, weekly_totals2)
    vista["checks_first_attempt"] = checks
    return {"status": "invalid", "pulse": parsed2 or parsed, "validation": vista,
            "attempts": attempts, "dias_restantes": dias_restantes,
            "input_context": input_context, "raw_responses": raw_responses}
