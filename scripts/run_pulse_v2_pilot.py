#!/usr/bin/env python3
"""
Runner del piloto Pulse v2.1.

Reprocesa el cohort de 11 usuarios piloto (los "6 pilotos" ya tokenizados en
private/tokens.csv, más 5 nuevos) contra los exports frescos de
swetro-export/output/ (corrida del 2026-09-01 23:14, 11 archivos con el
mismo timestamp de lote), usando:
  - transformar_json.py (v1, sin modificar) para actividades/semanas/ACWR;
  - scripts/pulse_v2_engine.py (nuevo, v2.1) para planning_constraints,
    el prompt con schema retirado de funFact/seoulTip/injuryRisk.score, y
    la validación individual con un intento de reparación.

Aislamiento deliberado:
  - swetro-export/output/          se trata como SOLO LECTURA.
  - dashboard/private/tokens.csv   se trata como SOLO LECTURA (se reusan
                                    tokens existentes de los pilotos viejos,
                                    nunca se escribe ahí).
  - dashboard/private/metas/       se trata como SOLO LECTURA (fallback si
                                    el roster de este script no trae una
                                    meta explícita para ese usuario).
  - Todo lo que este script escribe vive bajo pilot_outputs/pulse_v2_1/:
    tokens nuevos (_tokens.csv), reportes de validación y los JSON finales
    por usuario. Nunca toca public/data/ ni hace commit/push/build/deploy.

Uso:
  python scripts/run_pulse_v2_pilot.py --list
  python scripts/run_pulse_v2_pilot.py --validate-only
  python scripts/run_pulse_v2_pilot.py --run fabiana
"""

import argparse
import csv
import glob
import json
import os
import secrets
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
EXPORT_DIR = Path("/Users/alejandroordonez/swetro-export/output")
OUT_DIR = DASHBOARD_ROOT / "pilot_outputs" / "pulse_v2_1"
PILOT_TOKENS_PATH = OUT_DIR / "_tokens.csv"           # el único tokens.csv que este script escribe
EXISTING_TOKENS_PATH = DASHBOARD_ROOT / "private" / "tokens.csv"  # solo lectura
METAS_DIR = DASHBOARD_ROOT / "private" / "metas"                  # solo lectura, fallback

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(DASHBOARD_ROOT))
import transformar_json as tj  # v1, sin modificar
import pulse_v2_engine as v2   # motor v2.1, nuevo, aislado bajo scripts/

# Lote piloto: mismos 11 usuarios del export conjunto
# swetro_output_<prefix>_20260901_231403.json. El token de Fabiana viene
# asignado por el equipo (no se genera acá); el resto reusa su token viejo
# si ya estaba tokenizado, o se genera y persiste solo en pilot_outputs/
# cuando efectivamente se corre ese usuario.
#
# "meta": cuando el roster trae una meta explícita para ese usuario, tiene
# PRIORIDAD ABSOLUTA sobre private/metas/meta_<id>.json — así se evita la
# causa raíz del bug con Fabiana: no existía meta_4778.json en ningún lado
# del repo, y el runner original caía en el sentinel por defecto sin avisar.
PILOT_ROSTER = [
    {"slug": "alejandro",     "export_prefix": "alejandro_ordo_gmail_com"},
    {"slug": "ana",           "export_prefix": "analeech_yahoo_com"},
    {"slug": "miguel_diaz",   "export_prefix": "dmiguel29_yahoo_com_ar"},
    {"slug": "luis",          "export_prefix": "gluiss66_gmail_com"},
    {"slug": "fabiana",       "export_prefix": "haedofabiana1980_gmail_com",
     "token_seed": "HtmEXNzVWjYe08uRpD5MPw",
     "meta": {"metaCarrera": {
         "nombre": "Maratón de Buenos Aires",
         "fecha": "2026-09-20",
         "label": "42K BUE",
     }}},
    {"slug": "jose_luis",     "export_prefix": "josluperez_hotmail_com"},
    {"slug": "miguel_cortes", "export_prefix": "miacortesve_unal_edu_co"},
    {"slug": "natalia",       "export_prefix": "natapalaciolarte_hotmail_com"},
    {"slug": "nicole",        "export_prefix": "nicolespinozapena_gmail_com"},
    {"slug": "alvaro",        "export_prefix": "ocaa22_hotmail_com"},
    {"slug": "william",       "export_prefix": "williamralbornozalvarado_gmail_com"},
]


def find_latest_export(prefix):
    """Export más reciente para ese prefijo de correo saneado. Nunca escribe."""
    pattern = str(EXPORT_DIR / f"swetro_output_{prefix}_*.json")
    matches = sorted(glob.glob(pattern))  # el timestamp en el nombre ordena lexicográficamente
    return Path(matches[-1]) if matches else None


def load_existing_token(user_id):
    """Lee (solo lectura) el tokens.csv real del dashboard, si el userId ya está ahí."""
    if not EXISTING_TOKENS_PATH.exists():
        return None
    with open(EXISTING_TOKENS_PATH, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["user_id"] == str(user_id):
                return row["token"]
    return None


def load_pilot_token(user_id):
    """Lee (solo lectura) el mapeo de tokens propio del piloto, si ya se generó antes."""
    if not PILOT_TOKENS_PATH.exists():
        return None
    with open(PILOT_TOKENS_PATH, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["user_id"] == str(user_id):
                return row["token"]
    return None


def persist_pilot_token(user_id, token):
    """Agrega (userId, token) a pilot_outputs/pulse_v2_1/_tokens.csv. Idempotente."""
    filas = []
    if PILOT_TOKENS_PATH.exists():
        with open(PILOT_TOKENS_PATH, "r", encoding="utf-8", newline="") as f:
            filas = list(csv.DictReader(f))
    if any(fila["user_id"] == str(user_id) for fila in filas):
        return
    filas.append({"user_id": str(user_id), "token": token})
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(PILOT_TOKENS_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["user_id", "token"])
        writer.writeheader()
        writer.writerows(filas)


def resolve_token(entry, user_id, persist):
    """
    Orden de resolución:
      1. token_seed explícito en el roster (Fabiana).
      2. Token ya existente en el tokens.csv real del dashboard (pilotos viejos).
      3. Token ya generado antes en pilot_outputs/ para este piloto v2.
      4. Nuevo token (solo si persist=True, es decir: corrida real, no validate-only).
    Devuelve (token_o_None, origen).
    """
    seed = entry.get("token_seed")
    if seed:
        if persist:
            persist_pilot_token(user_id, seed)  # idempotente: no pisa si ya existe
        return seed, "asignado (equipo)"

    existing = load_existing_token(user_id)
    if existing:
        return existing, "reusado de private/tokens.csv"

    pilot_existing = load_pilot_token(user_id)
    if pilot_existing:
        return pilot_existing, "reusado de pilot_outputs/_tokens.csv"

    if not persist:
        return None, "pendiente (se generaría en la primera corrida real)"

    token = secrets.token_urlsafe(8)
    persist_pilot_token(user_id, token)
    return token, "nuevo (generado ahora)"


def resolve_meta(entry, user_id):
    """
    Orden de resolución (CORREGIDO — causa raíz del bug de Fabiana):
      1. entry["meta"] embebida en el roster de ESTE script (fuente de la
         verdad para el piloto: es la meta que el equipo confirmó).
      2. private/metas/meta_<userId>.json si existe (solo lectura, fallback
         para los pilotos viejos que ya tenían meta ahí).
      3. None → transformar_json.py usa su sentinel por defecto
         ("¿Cuál es tu próxima carrera?" / 2027-01-01).
    Devuelve (meta_override, origen_str).
    """
    if entry.get("meta"):
        return entry["meta"], "roster de run_pulse_v2_pilot.py (explícita)"

    path = METAS_DIR / f"meta_{user_id}.json"
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), f"private/metas/meta_{user_id}.json (fallback, solo lectura)"

    return None, "sin meta configurada (default sentinel de transformar_json.py)"


def process_entry_v1(entry, con_pulse, persist_tokens, fecha_generacion=None):
    """
    Corre el pipeline v1 (transformar_json.py) para un usuario del roster,
    SIN pulse v2.1 — se usa tal cual en --validate-only para confirmar que
    activities/weekly/ACWR/meta están sanos, y también se usa como base
    (con con_pulse=False) antes de inyectar el pulse v2.1 en --run.
    Nunca lanza: los errores quedan en el dict de retorno.

    fecha_generacion (fix #3): única fecha de referencia para diasPrep /
    dias_restantes. Si no se pasa, usa date.today() (comportamiento previo).
    """
    fecha_generacion = fecha_generacion or date.today()
    slug = entry["slug"]
    report = {"slug": slug, "export_prefix": entry["export_prefix"], "ok": False}

    export_path = find_latest_export(entry["export_prefix"])
    if export_path is None:
        report["error"] = f"No se encontró export en {EXPORT_DIR} para prefijo {entry['export_prefix']!r}"
        return report
    report["export_file"] = export_path.name

    try:
        with open(export_path, "r", encoding="utf-8") as f:
            input_data = json.load(f)
    except Exception as e:
        report["error"] = f"Export ilegible: {e}"
        return report

    profile = input_data.get("profile", {})
    user_id = profile.get("user_id")
    report["user_id"] = user_id
    report["full_name"] = profile.get("full_name")
    report["n_activities_raw"] = len(input_data.get("activities", []))

    meta_override, meta_origin = resolve_meta(entry, user_id)
    report["meta_override"] = meta_override.get("metaCarrera") if meta_override else None
    report["meta_origin"] = meta_origin

    token, token_origin = resolve_token(entry, user_id, persist=persist_tokens)
    report["token"] = token
    report["token_origin"] = token_origin

    try:
        result = tj.transformar(input_data, meta_override, con_pulse=con_pulse)
    except Exception as e:
        report["error"] = f"transformar() falló: {e}"
        report["traceback"] = traceback.format_exc()
        return report

    report["n_activities_validas"] = len(result["activities"])
    report["n_semanas"] = len(result["weekly"])
    report["acwr_status"] = (result.get("acwr") or {}).get("status")

    # Preview del motor determinístico v2.1, siempre calculable sin llamar a
    # Anthropic — esto es lo que --validate-only usa para "verificar que el
    # runner está usando el contrato v2.1" sin gastar ni un solo request.
    carrera = (meta_override or {}).get("metaCarrera", {})
    tiene_meta = bool(carrera.get("nombre") and carrera.get("nombre") != "¿Cuál es tu próxima carrera?")
    lunes_analizado, domingo_analizado = tj.ultima_semana_completa(hoy=fecha_generacion)
    lunes_semana_actual = lunes_analizado + timedelta(days=7)
    activities_cerradas = [a for a in result["activities"] if a.get("date", "9999") < lunes_semana_actual.isoformat()]
    weekly_cerrado = [w for w in result["weekly"] if w["week"].split("/")[0] < lunes_semana_actual.isoformat()]
    constraints = v2.calcular_planning_constraints(
        activities_cerradas, weekly_cerrado, result.get("acwr"), carrera, tiene_meta, fecha_generacion,
    )
    report["planning_constraints_v2_preview"] = constraints
    # Consistencia (fix #3): diasPrep de transformar_json.py (date.today()-based)
    # debe coincidir con dias_restantes de planning_constraints (misma fecha_generacion).
    if constraints["dias_restantes"] is not None:
        result["meta"]["metaCarrera"]["diasPrep"] = constraints["dias_restantes"]

    report["ok"] = True
    report["_result"] = result
    report["_profile"] = profile
    report["_meta_override"] = meta_override
    return report


def strip_internal(report):
    return {k: v for k, v in report.items() if not k.startswith("_")}


def cmd_list(_args):
    print(f"Roster piloto Pulse v2.1 ({len(PILOT_ROSTER)} usuarios):\n")
    for entry in PILOT_ROSTER:
        export_path = find_latest_export(entry["export_prefix"])
        if export_path:
            data = json.load(open(export_path, encoding="utf-8"))
            profile = data.get("profile", {})
            user_id = profile.get("user_id")
            token, origin = resolve_token(entry, user_id, persist=False)
            meta_override, meta_origin = resolve_meta(entry, user_id)
            print(f"  {entry['slug']:<14} user_id={user_id!s:<7} {profile.get('full_name', '?'):<35} "
                  f"export={export_path.name}")
            print(f"  {'':<14} token={token or '(pendiente)':<24} [{origin}]")
            print(f"  {'':<14} meta={(meta_override or {}).get('metaCarrera', {})!s:<40} [{meta_origin}]")
        else:
            print(f"  {entry['slug']:<14} SIN EXPORT encontrado (prefijo {entry['export_prefix']})")
        print()


def cmd_validate(_args):
    print(f"=== validate-only: {len(PILOT_ROSTER)} usuarios, sin llamar a Anthropic, sin escribir tokens nuevos ===\n")
    reports = []
    for entry in PILOT_ROSTER:
        r = process_entry_v1(entry, con_pulse=False, persist_tokens=False)
        reports.append(strip_internal(r))
        if r["ok"]:
            c = r["planning_constraints_v2_preview"]
            detail = (f"user_id={r.get('user_id')} actividades_validas={r.get('n_activities_validas')} "
                      f"semanas={r.get('n_semanas')} acwr={r.get('acwr_status')} meta=[{r.get('meta_origin')}] "
                      f"goal_phase={c['goal_phase']} load_direction={c['load_direction']} "
                      f"km_range={c['running_km_range']}")
        else:
            detail = r.get("error")
        status = "OK " if r["ok"] else "ERROR"
        print(f"[{status}] {entry['slug']:<14} {detail}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = OUT_DIR / f"validate_report_{ts}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({"generated_at": ts, "results": reports}, f, ensure_ascii=False, indent=2)

    n_ok = sum(1 for r in reports if r["ok"])
    print(f"\n{n_ok}/{len(reports)} OK. Reporte: {report_path.relative_to(DASHBOARD_ROOT)}")
    return 0 if n_ok == len(reports) else 1


def cmd_run(args):
    slug = args.run
    entry = next((e for e in PILOT_ROSTER if e["slug"] == slug), None)
    if entry is None:
        slugs = ", ".join(e["slug"] for e in PILOT_ROSTER)
        print(f"ERROR: '{slug}' no está en el roster piloto. Slugs válidos: {slugs}", file=sys.stderr)
        return 1

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY no está exportada en esta shell.\n"
              "  Este runner NO escribe un archivo sin Pulse cuando falta la key —\n"
              "  export la key primero y volvé a correr --run.", file=sys.stderr)
        return 1
    try:
        import anthropic  # noqa: F401
    except ImportError:
        print("ERROR: el paquete 'anthropic' no está instalado (pip install anthropic).", file=sys.stderr)
        return 1

    fecha_generacion = date.today()  # única fecha de referencia (fix #3), compartida por ambas llamadas
    print(f"=== corrida real Pulse v2.1 para: {slug} (fecha_generacion={fecha_generacion.isoformat()}) ===\n")
    r = process_entry_v1(entry, con_pulse=False, persist_tokens=True, fecha_generacion=fecha_generacion)  # base sin pulse

    if not r["ok"]:
        print(f"[ERROR] {r.get('error')}")
        if r.get("traceback"):
            print(r["traceback"])
        return 1

    result = r.pop("_result")
    profile = r.pop("_profile")
    meta_override = r.pop("_meta_override")

    print(f"  Usuario:       {r['full_name']} (user_id={r['user_id']})")
    print(f"  Export usado:  {r['export_file']}")
    print(f"  Meta usada:    {r['meta_override']}  [{r['meta_origin']}]")
    print(f"  Token:         {r['token']} [{r['token_origin']}]")
    print("  Generando Pulse v2.1 (system + user prompt, con planning_constraints)...")

    gen = v2.generar_pulse_v2(tj, result["activities"], result["weekly"], result["meta"],
                               profile, result["acwr"], api_key, fecha_generacion=fecha_generacion)

    # Fix #3, defensivo: aunque process_entry_v1 ya sincronizó diasPrep con
    # la misma fecha_generacion, lo reconfirmamos con el valor que gen()
    # efectivamente usó (mismo cálculo, cero margen para que diverjan).
    if gen.get("dias_restantes") is not None:
        result["meta"]["metaCarrera"]["diasPrep"] = gen["dias_restantes"]

    print(f"  Intentos:      {gen['attempts']} (1 = sin reparar, 2 = tras reparación)")
    print(f"  Estado:        {gen['status']}")
    if gen["validation"]:
        checks = gen["validation"].get("checks") or []
        n_fail = sum(1 for c in checks if c["status"] == "fail")
        print(f"  Validación:    {'OK' if gen['validation']['ok'] else f'{n_fail} check(s) fallido(s)'} "
              f"({len(checks)} checks evaluados en total)")
        for item in checks:
            if item["status"] == "fail":
                print(f"    - [FALLA] [{item['check']}] {item['message']}")

    # ── escribir los 4 archivos por usuario, siempre (aprobado o no) ──
    user_dir = OUT_DIR / r["token"]
    user_dir.mkdir(parents=True, exist_ok=True)

    with open(user_dir / "input_context.json", "w", encoding="utf-8") as f:
        json.dump(gen["input_context"], f, ensure_ascii=False, indent=2)

    with open(user_dir / "raw_response.txt", "w", encoding="utf-8") as f:
        for i, raw in enumerate(gen["raw_responses"], start=1):
            f.write(f"# ── intento {i} ──\n{raw}\n\n")

    with open(user_dir / "pulse_final.json", "w", encoding="utf-8") as f:
        json.dump(gen["pulse"], f, ensure_ascii=False, indent=2)

    validation_report = {
        "slug": slug, "token": r["token"], "status": gen["status"], "attempts": gen["attempts"],
        "constraints_version": v2.CONSTRAINTS_VERSION, "prompt_version": v2.PROMPT_VERSION,
        "validation": gen["validation"],
        "error": gen.get("error"), "repair_error": gen.get("repair_error"),
    }
    with open(user_dir / "validation_report.json", "w", encoding="utf-8") as f:
        json.dump(validation_report, f, ensure_ascii=False, indent=2)

    # ── candidato "dashboard-shaped" — NUNCA se promueve a public/data/ acá ──
    result["pulse"] = gen["pulse"]
    result["_pilotV2"] = {
        "status": gen["status"],
        "approved": gen["status"] in ("valid", "valid_after_repair"),
        "attempts": gen["attempts"],
        "constraints_version": v2.CONSTRAINTS_VERSION,
        "prompt_version": v2.PROMPT_VERSION,
        "violations": (gen["validation"] or {}).get("violations", []),
        "meta_origin": r["meta_origin"],
    }
    candidate_path = OUT_DIR / f"{r['token']}.json"
    with open(candidate_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n  Archivos escritos en: {user_dir.relative_to(DASHBOARD_ROOT)}/")
    print(f"    input_context.json, raw_response.txt, pulse_final.json, validation_report.json")
    print(f"  Candidato completo:   {candidate_path.relative_to(DASHBOARD_ROOT)}")
    if gen["status"] in ("valid", "valid_after_repair"):
        print("\n  APROBADO por la validación individual. Sigue sin estar en public/data/ "
              "hasta que alguien decida promoverlo explícitamente (fuera del alcance de este runner).")
    else:
        print(f"\n  NO APROBADO (status={gen['status']}). Candidato conservado en pilot_outputs/ "
              "con las razones de arriba. No se promueve a public/data/.")
    return 0 if gen["status"] in ("valid", "valid_after_repair") else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="Lista el roster piloto, tokens y meta resuelta (solo lectura)")
    group.add_argument("--validate-only", action="store_true",
                        help="Corre transformar() v1 + preview de planning_constraints v2.1 para los 11, sin Anthropic")
    group.add_argument("--run", metavar="SLUG", help="Corrida real Pulse v2.1 (con Anthropic) para UN usuario del roster")
    args = parser.parse_args()

    if args.list:
        cmd_list(args)
        return 0
    if args.validate_only:
        return cmd_validate(args)
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
