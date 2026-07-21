"""
transformar_json.py — Convierte el JSON de la app del socio
al formato que espera el dashboard de Swetro.

USO:
  python transformar_json.py input.json
  python transformar_json.py input.json --output public/data/u002.json
  python transformar_json.py input.json --con-sway   # genera Pulse via Anthropic

DEPENDENCIAS:
  pip install anthropic   # solo si usas --con-sway
"""

import json
import sys
import os
import argparse
from datetime import datetime, date
from collections import defaultdict


# ── Constantes de disciplinas ─────────────────────────────────

RUNNING_TIPOS   = {"running", "trail_running", "treadmill", "virtualrun"}
CYCLING_TIPOS   = {"cycling", "indoorcycling", "virtualride", "mountainbiking", "gravel_cycling"}
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
        "hr":           a.get("average_heart_rate_in_beats_per_minute", 0),
        "kcal":         a.get("active_kilocalories", 0),
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


# ── Generar Pulse via Anthropic ───────────────────────────────

def generar_sway(activities, weekly, meta, profile):
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

    # Solo running para métricas de pace/ACWR
    run_acts    = [a for a in activities if a.get("type") == "running"]
    run_weekly  = [w for w in weekly if w.get("total_km", 0) > 0 or w.get("running", {}).get("km", 0) > 0]

    last_week   = weekly[-1] if weekly else {}
    recent_run  = run_acts[-1] if run_acts else (activities[-1] if activities else {})

    # ACWR basado solo en km de running
    run_kms_weekly = [w.get("running", {}).get("km", w.get("total_km", 0)) for w in weekly]
    if len(run_kms_weekly) >= 5:
        acute   = run_kms_weekly[-1]
        chronic = sum(run_kms_weekly[-5:-1]) / 4
        acwr    = round(acute / chronic, 2) if chronic > 0 else 0.0
    else:
        acwr = 1.0

    nombre   = meta.get("nombre", profile.get("full_name", "Atleta"))
    carrera  = meta.get("metaCarrera", {})
    resumen  = resumen_disciplinas(activities)

    # PRs para contexto
    prs_str = ""
    prs = meta.get("prs", [])
    if prs:
        prs_str = "PRs registrados: " + ", ".join(f"{p['dist']} {p['mark']}" for p in prs)

    # Últimas 8 semanas de running
    ultimas_run_km = [w.get("running", {}).get("km", w.get("total_km", 0)) for w in weekly[-8:]]

    system_prompt = """Eres Pulse, el motor de análisis semanal de Swetro.
Generas análisis de entrenamiento personalizados para atletas que pueden practicar múltiples deportes.
Español latinoamericano. SIEMPRE en segunda persona dirigiéndote al atleta por su nombre — escribe "Juan, cerraste..." nunca "Juan cerró...".
Sin bullets en aiVerdict. Sin emojis en texto de análisis.
IMPORTANTE: Las métricas semanales de km y ACWR reflejan SOLO running. El atleta puede tener otras disciplinas que complementan su carga total. No interpretes semanas de bajo km de running como inactividad si hay otras disciplinas activas esa semana. Cuando calcules fatiga o recuperación, considera la carga total de todas las disciplinas.
Responde ÚNICAMENTE con JSON válido, sin markdown, sin backticks."""

    user_prompt = f"""Genera análisis Pulse semanal.

ATLETA: {nombre}
{f"Meta: {carrera.get('nombre', '')} el {carrera.get('fecha', 'TBD')}" if carrera.get('nombre') and carrera.get('nombre') != '¿Cuál es tu próxima carrera?' else "Sin meta de carrera definida"}
{prs_str}

SEMANA ANALIZADA: {last_week.get('week', 'N/A')}
Running esta semana: {last_week.get('running', {}).get('km', last_week.get('total_km', 0))} km | {last_week.get('running', {}).get('sessions', last_week.get('sessions', 0))} sesiones | FC promedio running: {last_week.get('running', {}).get('avg_hr', last_week.get('avg_hr', 0))} bpm
Ciclismo esta semana: {last_week.get('cycling', {}).get('km', 0)} km | {last_week.get('cycling', {}).get('sessions', 0)} sesiones
Natación esta semana: {last_week.get('swimming', {}).get('metros', 0)} metros | {last_week.get('swimming', {}).get('sessions', 0)} sesiones
Fuerza esta semana: {last_week.get('strength', {}).get('minutos', 0)} min | {last_week.get('strength', {}).get('sessions', 0)} sesiones

ÚLTIMA SESIÓN DE RUNNING: {recent_run.get('name', 'N/A')} ({recent_run.get('date', 'N/A')})
{recent_run.get('dist_km', 0)}km | {recent_run.get('pace', 'N/A')}/km | {recent_run.get('hr', 0)}bpm

CONTEXTO HISTÓRICO ({len(activities)} actividades totales):
{resumen}
ACWR running: {acwr}x
Últimas 8 semanas (km running): {ultimas_run_km}

Responde con JSON: {{"semana":"rango fechas","score":0-100,"headline":"máx 8 palabras","subheadline":"máx 12 palabras","readiness":0-100,"projectedTime":"hh:mm:ss o null","projectedPace":"m:ss o null","aiVerdict":"párrafo 3-4 oraciones análisis longitudinal en segunda persona","strengths":["s1","s2","s3"],"warnings":["w1","w2"],"keyMetrics":[{{"label":"nombre","value":"valor","trend":"up|down|stable","status":"green|yellow|red","note":"nota corta"}}],"weekPlan":[{{"day":"Lun|Mar|Mié|Jue|Vie|Sáb|Dom","type":"tipo sesión","km":"X km o —","notes":"instrucción concreta"}}],"injuryRisk":{{"level":"low|medium|high","score":0-100,"topRisk":"zona anatómica","action":"acción concreta"}},"funFact":"dato curioso sobre su entrenamiento o null","seoulTip":null}}"""

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
        return json.loads(text)
    except Exception as e:
        print(f"  ✗ Error generando Pulse: {e}")
        return None


# ── Construir JSON final ──────────────────────────────────────

def transformar(input_data, meta_override=None, con_sway=False):
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
        "diasPrep": 90,
    })

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
        "isPro":      meta_override.get("isPro", True),
        "generadoEn": datetime.utcnow().isoformat() + "Z",
    }

    # ── Pulse ──
    sway = None
    if con_sway and activities and weekly:
        print("  → Generando Pulse...")
        sway = generar_sway(activities, weekly, meta, profile)
        if sway:
            print(f"  ✓ Pulse generado (score: {sway.get('score', '?')})")

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
        "meta":       meta,
        "activities": activities,
        "weekly":     weekly,
        "sway":       sway,
        "taper":      [],
        "retos":      retos,
    }


# ── Entry point ───────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Transforma JSON del socio → formato dashboard Swetro"
    )
    parser.add_argument("input",         help="Archivo JSON de entrada")
    parser.add_argument("--output", "-o", help="Archivo de salida (default: public/data/<userId>.json)")
    parser.add_argument("--con-sway",    action="store_true", help="Generar Pulse via Anthropic API")
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

    result  = transformar(input_data, meta_override, con_sway=args.con_sway)
    user_id = result["meta"]["userId"]

    if args.output:
        output_path = args.output
    else:
        os.makedirs("public/data", exist_ok=True)
        output_path = f"public/data/{user_id}.json"

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))

    size_kb = os.path.getsize(output_path) // 1024
    print(f"  ✓ Guardado: {output_path} ({size_kb} KB)")
    print(f"  → Dashboard: ?u={user_id}")
    print(f"  → Semanas calculadas: {len(result['weekly'])}")


if __name__ == "__main__":
    main()
