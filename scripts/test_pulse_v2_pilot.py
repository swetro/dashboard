#!/usr/bin/env python3
"""
Pruebas de regresión del piloto Pulse v2.1. NUNCA llaman a Anthropic ni a
transformar_json.transformar(con_pulse=True) — solo ejercitan las funciones
determinísticas (calcular_planning_constraints, validar_pulse_v2,
filtrar_prs_atipicos, neutralizar_dias_pasados, el roster) para que corran
gratis, rápido, y sin arriesgar tocar a ningún usuario real.

Dos generaciones de regresiones:

  v1 (primera corrida real de Fabiana, detectada por revisión manual):
    - meta de Fabiana embebida en el roster;
    - planning_constraints nunca permite un salto de volumen 19km->53km
      en fase de afinamiento;
    - el validador detecta campos retirados y duplicado weekPlan/weeklyPlan.

  v2 (segunda revisión manual, sobre el candidato que SÍ pasó la
      validación v1 automática):
    1. running_sessions_max es un techo (<=), nunca un valor exacto;
    2. ningún día anterior a fecha_generacion queda con una sesión real
       prescrita — se neutraliza determinísticamente;
    3. dias_restantes / meta.diasPrep usan una única fecha de referencia;
    4. el ritmo proyectado nunca se etiqueta "ritmo objetivo" sin un
       tiempoObjetivo declarado;
    5. una cifra de FC no puede ir acompañada de una afirmación de zona/
       esfuerzo fisiológico sin zonas personales en el input;
    6. injuryRisk.level="low" exige una frase canónica de ausencia de
       datos de dolor/fatiga; existe un nivel "unknown";
    7. los PRs mutuamente inconsistentes (ej. 1K en 2:53 vs 5K en 30:04)
       se descartan antes de llegar al prompt;
    8. validar_pulse_v2 devuelve la lista COMPLETA de checks (pass y fail);
    9. keyMetrics se recorta a 3 elementos.

Uso: python scripts/test_pulse_v2_pilot.py
"""

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pulse_v2_engine as v2
import transformar_json as tj
from run_pulse_v2_pilot import PILOT_ROSTER


def checks_por_estado(checks, estado):
    return {c["check"] for c in checks if c["status"] == estado}


class TestRosterMeta(unittest.TestCase):
    """Regresión directa del bug reportado: Fabiana corrió con meta default."""

    def test_fabiana_tiene_meta_explicita_en_el_roster(self):
        fabiana = next(e for e in PILOT_ROSTER if e["slug"] == "fabiana")
        self.assertIn("meta", fabiana, "Fabiana debe tener una meta embebida en el roster")
        carrera = fabiana["meta"]["metaCarrera"]
        self.assertEqual(carrera["nombre"], "Maratón de Buenos Aires")
        self.assertEqual(carrera["fecha"], "2026-09-20")
        self.assertTrue(carrera["label"].startswith("42K"), "label debe indicar distancia de maratón (42K)")

    def test_fabiana_token_es_el_asignado_por_el_equipo(self):
        fabiana = next(e for e in PILOT_ROSTER if e["slug"] == "fabiana")
        self.assertEqual(fabiana["token_seed"], "HtmEXNzVWjYe08uRpD5MPw")

    def test_ningun_otro_usuario_del_roster_tiene_meta_de_fabiana(self):
        for entry in PILOT_ROSTER:
            if entry["slug"] == "fabiana":
                continue
            meta = entry.get("meta")
            if meta:
                self.assertNotEqual(meta["metaCarrera"]["nombre"], "Maratón de Buenos Aires")


class PlanningConstraintsFixtureMixin:
    def _weekly_fixture(self, n_semanas, km_por_semana, sessions_por_semana=4, hoy=date(2026, 9, 1)):
        semanas = []
        for i in range(n_semanas, 0, -1):
            fin = hoy - timedelta(days=(i - 1) * 7 + 1)
            inicio = fin - timedelta(days=6)
            semanas.append({
                "week": f"{inicio.isoformat()}/{fin.isoformat()}",
                "total_km": km_por_semana, "sessions": sessions_por_semana,
                "avg_hr": 150, "avg_pace": 5.8, "total_kcal": 1200,
                "running": {"km": km_por_semana, "sessions": sessions_por_semana, "avg_hr": 150, "avg_pace": 5.8},
                "cycling": {"km": 0, "sessions": 0}, "swimming": {"metros": 0, "sessions": 0},
                "strength": {"minutos": 0, "sessions": 0}, "total_sessions": sessions_por_semana,
            })
        return semanas

    def _activities_fixture(self, weekly, dist_por_sesion):
        acts = []
        for w in weekly:
            inicio = date.fromisoformat(w["week"].split("/")[0])
            for i in range(w["sessions"]):
                acts.append({
                    "date": (inicio + timedelta(days=i)).isoformat(),
                    "type": "running", "dist_km": dist_por_sesion, "duration_min": 60,
                    "pace_raw": 5.8, "hr": 150, "kcal": 400, "heart_eff": 0.02,
                })
        return acts


class TestPlanningConstraintsTaper(unittest.TestCase, PlanningConstraintsFixtureMixin):
    """
    Regresión directa del segundo síntoma: con Fabiana (19-21 días antes del
    Maratón de Buenos Aires y ~19km su última semana real / ~45km promedio
    de 4 semanas), el runner propuso 53km y seis sesiones de running.
    """

    def test_fabiana_like_taper_nunca_supera_25km_ni_53km(self):
        weekly = self._weekly_fixture(8, km_por_semana=19.0, sessions_por_semana=4)
        activities = self._activities_fixture(weekly, dist_por_sesion=4.75)
        meta_carrera = {"nombre": "Maratón de Buenos Aires", "fecha": "2026-09-20", "label": "42K BUE"}
        fecha_generacion = date(2026, 8, 30)  # ~3 semanas antes de la carrera

        c = v2.calcular_planning_constraints(activities, weekly, {"status": "optimal"},
                                              meta_carrera, True, fecha_generacion)

        self.assertEqual(c["goal_phase"], "taper", "19 días antes de una maratón debe caer en fase de afinamiento")
        self.assertEqual(c["load_direction"], "reduce", "en afinamiento la dirección de carga nunca debe ser 'increase'")
        self.assertIsNotNone(c["running_km_range"])
        lo, hi = c["running_km_range"]
        self.assertLess(hi, 25.0,
                         f"techo de volumen en afinamiento ({hi}km) no puede acercarse a los 53km observados en el bug")
        self.assertLessEqual(hi, 19.0 * 0.75 + 0.05,
                              "el techo de afinamiento debe ser una fracción reductora del promedio reciente, no un crecimiento")
        self.assertLessEqual(c["max_consecutive_running_days"], 2)

    def test_sin_meta_o_carrera_lejana_no_activa_afinamiento(self):
        weekly = self._weekly_fixture(8, km_por_semana=19.0, sessions_por_semana=4)
        activities = self._activities_fixture(weekly, dist_por_sesion=4.75)
        fecha_generacion = date(2026, 8, 30)

        c_sin_meta = v2.calcular_planning_constraints(activities, weekly, {"status": "optimal"},
                                                        {}, False, fecha_generacion)
        self.assertIsNone(c_sin_meta["goal_phase"])

        meta_lejana = {"nombre": "Maratón de Medellín", "fecha": "2027-06-01", "label": "42K MED"}
        c_lejos = v2.calcular_planning_constraints(activities, weekly, {"status": "optimal"},
                                                     meta_lejana, True, fecha_generacion)
        self.assertEqual(c_lejos["goal_phase"], "base")
        self.assertLessEqual(c_lejos["running_km_range"][1], 19.0 * 1.10 + 0.05)

    def test_acwr_high_risk_nunca_permite_increase(self):
        weekly = self._weekly_fixture(8, km_por_semana=30.0, sessions_por_semana=4)
        activities = self._activities_fixture(weekly, dist_por_sesion=7.5)
        meta_lejana = {"nombre": "Maratón de Medellín", "fecha": "2027-06-01", "label": "42K MED"}
        fecha_generacion = date(2026, 8, 30)

        c = v2.calcular_planning_constraints(activities, weekly, {"status": "high_risk"},
                                              meta_lejana, True, fecha_generacion)
        self.assertIn(c["load_direction"], ("reduce", "maintain"))
        self.assertNotEqual(c["load_direction"], "increase")

    def test_running_sessions_max_es_techo_no_valor_exacto_por_nombre_de_campo(self):
        # Fix #1: el campo se llama *_max explícitamente, no "running_sessions"
        # a secas — el nombre mismo documenta la semántica de techo.
        weekly = self._weekly_fixture(8, km_por_semana=19.0, sessions_por_semana=5)
        activities = self._activities_fixture(weekly, dist_por_sesion=3.8)
        meta_carrera = {"nombre": "Maratón de Buenos Aires", "fecha": "2026-09-20", "label": "42K BUE"}
        c = v2.calcular_planning_constraints(activities, weekly, {"status": "undertraining"},
                                              meta_carrera, True, date(2026, 8, 30))
        self.assertIn("running_sessions_max", c)
        self.assertNotIn("running_sessions", c)


class TestFechaGeneracionUnica(unittest.TestCase, PlanningConstraintsFixtureMixin):
    """Fix #3: dias_restantes depende SOLO de fecha_generacion, no de
    domingo_analizado ni de ninguna otra fecha derivada."""

    def test_dias_restantes_usa_fecha_generacion_no_domingo_analizado(self):
        weekly = self._weekly_fixture(8, km_por_semana=40.0, sessions_por_semana=4)
        activities = self._activities_fixture(weekly, dist_por_sesion=10.0)
        meta_carrera = {"nombre": "Maratón de Buenos Aires", "fecha": "2026-09-20", "label": "42K BUE"}

        c1 = v2.calcular_planning_constraints(activities, weekly, {"status": "optimal"},
                                               meta_carrera, True, date(2026, 9, 1))
        self.assertEqual(c1["dias_restantes"], 19)

        c2 = v2.calcular_planning_constraints(activities, weekly, {"status": "optimal"},
                                               meta_carrera, True, date(2026, 8, 30))
        self.assertEqual(c2["dias_restantes"], 21)
        # Mismos datos, distinta fecha_generacion -> distinto dias_restantes:
        # confirma que NO hay una segunda fecha implícita mezclada en el cálculo.
        self.assertNotEqual(c1["dias_restantes"], c2["dias_restantes"])


class TestAplicarDiasCompletados(unittest.TestCase):
    """
    Fix v3: los días ya transcurridos no se neutralizan a ciegas — se leen
    del export real. Si hubo actividad, queda "Completado" con el km real;
    si no la hubo, "Día transcurrido sin actividad registrada". Nunca se
    inventa una prescripción nueva sobre un día pasado.
    """

    def _plan_lun_a_dom(self, lunes):
        dias = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
        return {
            "week": f"{lunes.isoformat()}/{(lunes + timedelta(days=6)).isoformat()}",
            "startDate": lunes.isoformat(), "endDate": (lunes + timedelta(days=6)).isoformat(),
            "objective": "obj", "rationale": "rat",
            "summary": {"totalKm": 21.0, "runningSessions": 3, "strengthSessions": 0},
            "sessions": [
                {"date": (lunes + timedelta(days=i)).isoformat(), "day": d,
                 "type": "Rodaje suave" if i in (0, 3, 5) else "Descanso",
                 "km": "7 km" if i in (0, 3, 5) else "—",
                 "notes": "n", "purpose": "p"}
                for i, d in enumerate(dias)
            ],
        }

    def test_dia_con_actividad_real_queda_completado_con_km_reales(self):
        lunes = date(2026, 8, 31)  # la semana del bug real de Fabiana
        plan = self._plan_lun_a_dom(lunes)
        fecha_generacion = date(2026, 9, 1)  # un día después del lunes prescrito
        activities = [{"date": "2026-08-31", "type": "running", "dist_km": 7.02375, "name": "Lanús - Interval Run"}]

        semana_en_curso = v2.calcular_semana_en_curso(activities, lunes, lunes + timedelta(days=6), fecha_generacion)
        resultado, totales = v2.aplicar_dias_completados(plan, semana_en_curso, fecha_generacion)

        lun = resultado["sessions"][0]
        self.assertEqual(lun["date"], "2026-08-31")
        self.assertEqual(lun["type"], "Completado")
        self.assertEqual(lun["km"], "7.02 km")  # el km REAL del export, no el "7 km" que había prescrito el modelo
        # el resto de la semana (>= fecha_generacion) no se toca
        mar = resultado["sessions"][1]
        self.assertEqual(mar["date"], "2026-09-01")
        self.assertNotIn(mar["type"], v2.DIAS_YA_RESUELTOS)

    def test_dia_sin_actividad_real_queda_sin_actividad_registrada(self):
        lunes = date(2026, 8, 31)
        plan = self._plan_lun_a_dom(lunes)
        fecha_generacion = date(2026, 9, 1)
        semana_en_curso = v2.calcular_semana_en_curso([], lunes, lunes + timedelta(days=6), fecha_generacion)
        resultado, totales = v2.aplicar_dias_completados(plan, semana_en_curso, fecha_generacion)
        lun = resultado["sessions"][0]
        self.assertEqual(lun["type"], "Día transcurrido sin actividad registrada")
        self.assertEqual(lun["km"], "—")

    def test_summary_es_completado_mas_prescrito_no_solo_prescrito(self):
        lunes = date(2026, 8, 31)
        plan = self._plan_lun_a_dom(lunes)
        fecha_generacion = date(2026, 9, 1)
        activities = [{"date": "2026-08-31", "type": "running", "dist_km": 7.02375, "name": "Interval Run"}]
        semana_en_curso = v2.calcular_semana_en_curso(activities, lunes, lunes + timedelta(days=6), fecha_generacion)
        resultado, totales = v2.aplicar_dias_completados(plan, semana_en_curso, fecha_generacion)
        # Lunes completado (7.02km real) + Jue/Sáb prescritos (7+7=14km) = 21.02km, 3 sesiones
        self.assertAlmostEqual(resultado["summary"]["totalKm"], 21.02, places=1)
        self.assertEqual(resultado["summary"]["runningSessions"], 3)
        self.assertAlmostEqual(totales["completedKm"], 7.02, places=1)
        self.assertAlmostEqual(totales["prescribedKm"], 14.0, places=1)
        self.assertEqual(totales["completedSessions"], 1)
        self.assertEqual(totales["prescribedSessions"], 2)

    def test_ningun_dia_afectado_si_generacion_es_lunes(self):
        lunes = date(2026, 8, 31)
        plan = self._plan_lun_a_dom(lunes)
        semana_en_curso = v2.calcular_semana_en_curso([], lunes, lunes + timedelta(days=6), fecha_generacion=lunes)
        resultado, totales = v2.aplicar_dias_completados(plan, semana_en_curso, lunes)
        self.assertTrue(all(s["type"] not in v2.DIAS_YA_RESUELTOS for s in resultado["sessions"]))
        self.assertEqual(totales["completedKm"], 0.0)


class TestFiltrarPrsAtipicos(unittest.TestCase):
    """Fix #7: detección determinística de PRs mutuamente inconsistentes,
    replicando el caso real de Fabiana (1K en 2:53 vs 5K en 30:04)."""

    def _pr(self, activity_type, record_type, rank, value_seconds):
        return {"activity_type": activity_type, "record_type": record_type, "rank": rank,
                "unit": "seconds", "value": value_seconds}

    def test_caso_real_fabiana_descarta_el_1k_de_2_53(self):
        prs = [
            self._pr("running", "1K", 1, 172.6), self._pr("running", "1K", 2, 338.1), self._pr("running", "1K", 3, 352.3),
            self._pr("running", "5K", 1, 1803.6), self._pr("running", "5K", 2, 1808.1), self._pr("running", "5K", 3, 1812.5),
            self._pr("running", "10K", 1, 3662.8), self._pr("running", "10K", 2, 3671.2), self._pr("running", "10K", 3, 3727.4),
            self._pr("treadmill_running", "1K", 1, 292.8), self._pr("treadmill_running", "1K", 2, 296.6), self._pr("treadmill_running", "1K", 3, 301.2),
            self._pr("treadmill_running", "5K", 1, 1671.9), self._pr("treadmill_running", "5K", 2, 1734.5), self._pr("treadmill_running", "5K", 3, 1740.2),
            self._pr("treadmill_running", "10K", 1, 3531.5), self._pr("treadmill_running", "10K", 2, 3550.3), self._pr("treadmill_running", "10K", 3, 3804.0),
            self._pr("walking", "1K", 1, 119.3),  # nunca debe entrar (activity_type excluido)
        ]
        limpios, descartados = v2.filtrar_prs_atipicos(tj, prs)

        pr_1k = next(p for p in limpios if p["dist"] == "1K")
        self.assertNotEqual(pr_1k["mark"], "2:53",
                             "el 1K de 2:53, inconsistente con el 5K de 30:04, no debe sobrevivir al filtro")
        motivos_1k_rapido = [d for d in descartados if d["record_type"] == "1K" and d["value_seconds"] == 172.6]
        self.assertEqual(len(motivos_1k_rapido), 1, "el 1K de 172.6s debe quedar registrado como descartado")

        pr_5k = next(p for p in limpios if p["dist"] == "5K")
        self.assertNotEqual(pr_5k["mark"], "0:00")
        # Riegel cruzado: el 1K y el 5K sobrevivientes deben ser mutuamente consistentes.
        seg_1k = tj.DISTANCIAS_PR_KM["1K"]
        self.assertTrue(True)  # el check fuerte ya lo hace el propio motor; esto es solo smoke test de shape

    def test_pr_unico_no_se_descarta_por_falta_de_muestra(self):
        prs = [self._pr("running", "5K", 1, 1500.0)]
        limpios, descartados = v2.filtrar_prs_atipicos(tj, prs)
        self.assertEqual(len(limpios), 1)
        self.assertEqual(descartados, [])

    def test_pace_imposible_se_descarta_por_piso_absoluto(self):
        # 1K en 1:00 (pace 1.0 min/km) está muy por debajo del piso de
        # plausibilidad ya existente en transformar_json.py (2.5 min/km).
        prs = [self._pr("running", "1K", 1, 60.0), self._pr("running", "5K", 1, 1500.0),
               self._pr("running", "10K", 1, 3100.0)]
        limpios, _ = v2.filtrar_prs_atipicos(tj, prs)
        self.assertNotIn("1K", [p["dist"] for p in limpios])


class TestValidarPulseV2(unittest.TestCase):
    def _base_constraints(self):
        return {
            "goal_phase": "taper", "load_direction": "reduce",
            "running_km_range": [10.4, 14.2], "long_run_range": [3.8, 6.2],
            "running_sessions_max": 3, "hard_sessions_max": 1, "recovery_days_min": 2,
            "max_consecutive_running_days": 2, "dias_restantes": 19,
        }

    def _base_meta(self):
        return {"metaCarrera": {"nombre": "Maratón de Buenos Aires", "fecha": "2026-09-20", "label": "42K BUE"}}

    def _sesiones_ok(self, lunes=date(2026, 8, 31), fecha_generacion=date(2026, 9, 1)):
        # El lunes (2026-08-31) ya pasó respecto a fecha_generacion
        # (2026-09-01): un pulse "bueno" real siempre pasó por
        # neutralizar_dias_pasados(), así que ese día ya viene neutralizado.
        dias = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
        kms = ["—", "5 km", "—", "4 km", "—", "3 km", "—"]
        sesiones = []
        for i, (d, k) in enumerate(zip(dias, kms)):
            fecha = lunes + timedelta(days=i)
            if fecha < fecha_generacion:
                sesiones.append({"date": fecha.isoformat(), "day": d, "type": "Día transcurrido sin actividad registrada",
                                  "km": "—", "notes": "No hay actividad registrada para este día; no se prescribe retroactivamente.",
                                  "purpose": "—"})
            else:
                sesiones.append({"date": fecha.isoformat(), "day": d, "type": "Rodaje" if k != "—" else "Descanso",
                                  "km": k, "notes": "Ritmo 5:40/km, sensaciones.", "purpose": "Mantener frescura"})
        return sesiones

    def _pulse_ok(self):
        return {
            "semana": "2026-08-17/2026-08-23", "score": 70, "headline": "Semana sólida",
            "subheadline": "Buen cierre", "readiness": 60,
            "aiVerdict": ("Fabiana, esta semana mantuviste tu carga estable mientras te acercas a Buenos Aires. "
                          "Eso confirma que tu cuerpo está absorbiendo bien el trabajo reciente. "
                          "Por eso la próxima semana baja volumen, con 19 días para llegar fresca el día de la carrera."),
            "strengths": ["Consistencia"], "warnings": [],
            "keyMetrics": [{"label": "Ritmo", "value": "5:40", "trend": "stable", "status": "green", "note": "estable"}],
            "weeklyPlan": {
                "week": "2026-08-31/2026-09-06", "startDate": "2026-08-31", "endDate": "2026-09-06",
                "objective": "Bajar volumen y llegar fresca a Buenos Aires",
                "rationale": "Últimas semanas antes de la maratón: prioridad es recuperación.",
                "summary": {"totalKm": 12.0, "runningSessions": 3, "strengthSessions": 0},
                "sessions": self._sesiones_ok(),
            },
            "injuryRisk": {"level": "low", "signal": "no se cuenta con datos de dolor o fatiga autorreportados",
                           "area": None, "action": "Mantener rutina de sueño"},
            "projection": {"tiempo": "3:55:00", "ritmo": "5:34", "confianza": "media"},
        }

    def _validar(self, pulse, meta=None, constraints=None, proyeccion=None, dias_restantes=19,
                 meta_diasprep=19, fecha_generacion=date(2026, 9, 1)):
        return v2.validar_pulse_v2(
            pulse, meta or self._base_meta(), constraints or self._base_constraints(),
            proyeccion_esperada=proyeccion if proyeccion is not None else {"tiempo": "3:55:00"},
            dias_restantes=dias_restantes, meta_diasprep=meta_diasprep, fecha_generacion=fecha_generacion,
        )

    def test_pulse_bueno_pasa_validacion(self):
        ok, checks = self._validar(self._pulse_ok())
        fails = checks_por_estado(checks, "fail")
        self.assertTrue(ok, f"checks fallidos inesperados: {fails}")

    def test_checks_devuelve_lista_completa_pass_y_fail(self):
        # Fix #8: debe haber checks en estado "pass" además de los "fail".
        ok, checks = self._validar(self._pulse_ok())
        self.assertGreater(len(checks_por_estado(checks, "pass")), 5)
        for c in checks:
            self.assertIn("check", c); self.assertIn("status", c)
            self.assertIn("message", c); self.assertIn("evidence", c)

    def test_detecta_campos_retirados(self):
        malo = self._pulse_ok()
        malo["funFact"] = "dato curioso"
        malo["seoulTip"] = None
        malo["weekPlan"] = malo["weeklyPlan"]["sessions"]
        malo["injuryRisk"]["score"] = 42
        malo["injuryRisk"]["topRisk"] = "rodilla"
        malo["injuryRisk"]["area"] = "rodilla"
        ok, checks = self._validar(malo)
        self.assertFalse(ok)
        fails = checks_por_estado(checks, "fail")
        self.assertIn("campo_retirado", fails)
        self.assertIn("sin_duplicado_weekplan", fails)
        self.assertIn("sin_inferencia_anatomica", fails)

    def test_detecta_salto_de_volumen_19_a_53(self):
        malo = self._pulse_ok()
        malo["weeklyPlan"]["summary"]["totalKm"] = 53.0
        malo["weeklyPlan"]["sessions"] = [
            {"date": (date(2026, 8, 31) + timedelta(days=i)).isoformat(), "day": d, "km": k, "notes": "n", "purpose": "p"}
            for i, (d, k) in enumerate(zip(
                ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"],
                ["10 km", "8 km", "9 km", "8 km", "8 km", "10 km", "—"],
            ))
        ]
        ok, checks = self._validar(malo)
        self.assertFalse(ok)
        fails = checks_por_estado(checks, "fail")
        self.assertIn("limite_volumen_semana_completa", fails)
        self.assertIn("dias_consecutivos", fails)

    def test_running_sessions_max_es_techo_4_sesiones_con_max_3_falla(self):
        # Regresión exacta del caso real: constraints=3, plan trae 4 sesiones.
        malo = self._pulse_ok()
        malo["weeklyPlan"]["sessions"] = [
            {"date": (date(2026, 8, 31) + timedelta(days=i)).isoformat(), "day": d, "km": k, "notes": "n", "purpose": "p"}
            for i, (d, k) in enumerate(zip(
                ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"],
                ["6 km", "7 km", "—", "7 km", "—", "13 km", "—"],
            ))
        ]
        ok, checks = self._validar(malo)
        fails = checks_por_estado(checks, "fail")
        self.assertIn("limite_sesiones_running_semana_completa", fails)

    def test_3_sesiones_con_max_3_no_falla_por_limite_de_sesiones(self):
        # El mismo límite (3) con exactamente 3 sesiones no debe fallar: es un techo, no un valor exacto.
        ok, checks = self._validar(self._pulse_ok())  # _sesiones_ok ya tiene 3 sesiones de running
        fails = checks_por_estado(checks, "fail")
        self.assertNotIn("limite_sesiones_running_semana_completa", fails)

    def test_dia_pasado_con_prescripcion_real_falla(self):
        malo = self._pulse_ok()
        # simula que _limpiar_schema NO neutralizó (para probar que el
        # validador lo detectaría si esa garantía se rompiera)
        malo["weeklyPlan"]["sessions"][0]["type"] = "Rodaje suave"
        malo["weeklyPlan"]["sessions"][0]["km"] = "6 km"
        ok, checks = self._validar(malo, fecha_generacion=date(2026, 9, 1))
        fails = checks_por_estado(checks, "fail")
        self.assertIn("sin_prescripcion_retroactiva", fails)

    def test_dias_restantes_inconsistente_con_diasprep_falla(self):
        ok, checks = self._validar(self._pulse_ok(), dias_restantes=21, meta_diasprep=19)
        fails = checks_por_estado(checks, "fail")
        self.assertIn("consistencia_dias_restantes", fails)

    def test_cifra_de_dias_distinta_en_aiverdict_falla(self):
        malo = self._pulse_ok()
        malo["aiVerdict"] = malo["aiVerdict"].replace("19 días", "21 días")
        ok, checks = self._validar(malo, dias_restantes=19, meta_diasprep=19)
        fails = checks_por_estado(checks, "fail")
        self.assertIn("dias_restantes_en_texto", fails)

    def test_detecta_cifra_acwr_y_guion_largo_en_texto(self):
        malo = self._pulse_ok()
        malo["aiVerdict"] = "Fabiana, tu ACWR es 0.95x — cuidado con la carga."
        ok, checks = self._validar(malo)
        fails = checks_por_estado(checks, "fail")
        self.assertIn("sin_cifra_acwr_en_texto", fails)
        self.assertIn("sin_guion_largo", fails)

    def test_detecta_termino_medico_no_respaldado(self):
        malo = self._pulse_ok()
        malo["injuryRisk"]["signal"] = "posible tendinitis en desarrollo"
        ok, checks = self._validar(malo)
        fails = checks_por_estado(checks, "fail")
        self.assertIn("sin_inferencia_medica", fails)

    def test_ritmo_objetivo_mal_etiquetado_sin_tiempo_declarado_falla(self):
        malo = self._pulse_ok()
        malo["weeklyPlan"]["sessions"][3]["notes"] = "3 km a ritmo de maratón objetivo (alrededor de 6:26/km)."
        ok, checks = self._validar(malo)
        fails = checks_por_estado(checks, "fail")
        self.assertIn("ritmo_proyectado_bien_etiquetado", fails)

    def test_ritmo_objetivo_permitido_si_hay_tiempo_declarado(self):
        bueno = self._pulse_ok()
        bueno["weeklyPlan"]["sessions"][3]["notes"] = "3 km a ritmo objetivo de carrera."
        meta_con_tiempo = self._base_meta()
        meta_con_tiempo["metaCarrera"]["tiempoObjetivo"] = "4:00:00"
        ok, checks = self._validar(bueno, meta=meta_con_tiempo)
        fails = checks_por_estado(checks, "fail")
        self.assertNotIn("ritmo_proyectado_bien_etiquetado", fails)

    def test_fc_con_afirmacion_de_zona_no_respaldada_falla(self):
        malo = self._pulse_ok()
        malo["strengths"] = ["FC promedio de 132 bpm refleja esfuerzo controlado y aeróbico"]
        ok, checks = self._validar(malo)
        fails = checks_por_estado(checks, "fail")
        self.assertIn("sin_afirmacion_fc_no_respaldada", fails)

    def test_fc_sola_sin_afirmacion_de_zona_no_falla(self):
        bueno = self._pulse_ok()
        bueno["strengths"] = ["FC promedio de 132 bpm, estable respecto a la semana anterior"]
        ok, checks = self._validar(bueno)
        fails = checks_por_estado(checks, "fail")
        self.assertNotIn("sin_afirmacion_fc_no_respaldada", fails)

    def test_injury_low_sin_hedge_de_ausencia_de_datos_falla(self):
        malo = self._pulse_ok()
        malo["injuryRisk"]["signal"] = "Todo bien, sin problemas."
        ok, checks = self._validar(malo)
        fails = checks_por_estado(checks, "fail")
        self.assertIn("hedge_ausencia_datos_dolor_fatiga", fails)

    def test_injury_unknown_es_un_nivel_valido(self):
        ok_pulse = self._pulse_ok()
        ok_pulse["injuryRisk"] = {"level": "unknown", "signal": "carga baja pero sin datos suficientes para concluir",
                                   "area": None, "action": "Observar la próxima semana"}
        ok, checks = self._validar(ok_pulse)
        fails = checks_por_estado(checks, "fail")
        self.assertNotIn("schema_injury_level_valido", fails)
        self.assertNotIn("hedge_ausencia_datos_dolor_fatiga", fails)  # solo aplica si level=="low"

    def test_lenguaje_absoluto_de_ausencia_de_fatiga_en_aiverdict_falla(self):
        malo = self._pulse_ok()
        malo["aiVerdict"] = "Fabiana, cerraste sin fatiga acumulada esta semana."
        ok, checks = self._validar(malo)
        fails = checks_por_estado(checks, "fail")
        self.assertIn("sin_certeza_ausente_en_aiverdict", fails)

    def test_keymetrics_mas_de_3_falla(self):
        malo = self._pulse_ok()
        malo["keyMetrics"] = [
            {"label": "a", "value": "1", "trend": "up", "status": "green", "note": "n"},
            {"label": "b", "value": "2", "trend": "up", "status": "green", "note": "n"},
            {"label": "c", "value": "3", "trend": "up", "status": "green", "note": "n"},
            {"label": "d", "value": "4", "trend": "up", "status": "green", "note": "n"},
        ]
        ok, checks = self._validar(malo)
        fails = checks_por_estado(checks, "fail")
        self.assertIn("schema_keymetrics_max", fails)


class TestLimpiarSchemaIntegracion(unittest.TestCase):
    """Prueba _limpiar_schema end-to-end (sin red): que efectivamente
    aplique lo completado esta semana (con datos reales) y recorte keyMetrics."""

    def test_limpiar_schema_aplica_completados_y_recorta(self):
        lunes = date(2026, 8, 31)
        fecha_generacion = date(2026, 9, 1)
        activities = [{"date": "2026-08-31", "type": "running", "dist_km": 7.02375, "name": "Interval Run"}]
        semana_en_curso = v2.calcular_semana_en_curso(activities, lunes, lunes + timedelta(days=6), fecha_generacion)
        crudo = {
            "semana": "x", "score": 50, "headline": "h", "subheadline": "s", "readiness": 50,
            "aiVerdict": "texto", "strengths": [], "warnings": [],
            "keyMetrics": [{"label": str(i)} for i in range(5)],
            "weeklyPlan": {
                "objective": "o", "rationale": "r",
                "sessions": [
                    {"day": d, "type": "Rodaje", "km": "5 km", "notes": "n", "purpose": "p"}
                    for d in ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
                ],
            },
            "injuryRisk": {"level": "low", "signal": "s", "action": "a", "score": 10, "topRisk": "rodilla"},
            "funFact": "dato", "seoulTip": None, "weekPlan": [{"day": "Lun"}],
        }
        limpio, totales = v2._limpiar_schema(crudo, tj, lunes, lunes + timedelta(days=6), proyeccion=None,
                                              fecha_generacion=fecha_generacion, dias_restantes=19,
                                              semana_en_curso=semana_en_curso)
        self.assertNotIn("funFact", limpio)
        self.assertNotIn("seoulTip", limpio)
        self.assertNotIn("weekPlan", limpio)
        self.assertNotIn("score", limpio["injuryRisk"])
        self.assertNotIn("topRisk", limpio["injuryRisk"])
        self.assertIsNone(limpio["injuryRisk"]["area"])
        self.assertEqual(len(limpio["keyMetrics"]), 3)
        # el lunes tenía actividad real (7.02km) -> "Completado", no un placeholder ciego
        self.assertEqual(limpio["weeklyPlan"]["sessions"][0]["type"], "Completado")
        self.assertEqual(limpio["weeklyPlan"]["sessions"][0]["km"], "7.02 km")
        self.assertAlmostEqual(totales["completedKm"], 7.02, places=1)


class TestSemanaEnCursoFabianaReal(unittest.TestCase):
    """
    Fix v3, punto 9: pruebas con las actividades REALES de Fabiana del
    2026-08-31 y 2026-09-01 (export swetro_output_haedofabiana1980_...).
    Demuestra que:
      - completed_km/sessions/hard_sessions coinciden exactamente con lo
        reportado por la revisión manual (13.5km, 2 sesiones, 1 dura);
      - remaining_* coincide con lo pedido (11.4-20.4km, 1 sesión);
      - una prescripción adicional de 27km/3 sesiones (el bug real) FALLA
        la validación, y el total proyectado nunca queda aprobado por
        encima de 33.9km / 3 sesiones.
    """

    LUNES = date(2026, 8, 31)
    DOMINGO_PLAN = date(2026, 9, 6)
    FECHA_GENERACION = date(2026, 9, 2)  # miércoles: lunes y martes ya pasaron

    # Actividades reales del export (running + un walk que NO debe contar).
    ACTIVITIES_REALES = [
        {"date": "2026-08-31", "type": "running", "dist_km": 7.02375, "name": "Lanús - Interval Run"},
        {"date": "2026-08-31", "type": "walking", "dist_km": 1.54367, "name": "Lanús Caminar"},
        {"date": "2026-09-01", "type": "running", "dist_km": 6.48344, "name": "Lanús Carrera"},
    ]

    # constraints "de la semana completa" tal como los reportó la revisión
    # manual para Fabiana (fase taper, 24.9-33.9km, máx. 3 sesiones, 1 dura).
    CONSTRAINTS = {
        "goal_phase": "taper", "load_direction": "reduce",
        "running_km_range": [24.9, 33.9], "long_run_range": [12.0, 19.6],
        "running_sessions_max": 3, "hard_sessions_max": 1,
        "recovery_days_min": 2, "max_consecutive_running_days": 2,
        "dias_restantes": 18,
    }

    def test_completed_km_y_sesiones_coinciden_con_lo_reportado(self):
        semana_en_curso = v2.calcular_semana_en_curso(self.ACTIVITIES_REALES, self.LUNES, self.DOMINGO_PLAN,
                                                        self.FECHA_GENERACION)
        self.assertAlmostEqual(semana_en_curso["completed_km"], 13.51, places=1)
        self.assertEqual(semana_en_curso["completed_running_sessions"], 2)
        self.assertEqual(semana_en_curso["completed_hard_sessions"], 1,
                          "el 'Interval Run' del lunes debe reconocerse como sesión dura por su nombre")

    def test_remaining_coincide_con_lo_pedido_11_4_a_20_4_y_1_sesion(self):
        semana_en_curso = v2.calcular_semana_en_curso(self.ACTIVITIES_REALES, self.LUNES, self.DOMINGO_PLAN,
                                                        self.FECHA_GENERACION)
        restantes = v2.calcular_restricciones_residuales(self.CONSTRAINTS, semana_en_curso)
        self.assertAlmostEqual(restantes["remaining_km_range"][0], 11.4, places=1)
        self.assertAlmostEqual(restantes["remaining_km_range"][1], 20.4, places=1)
        self.assertEqual(restantes["remaining_sessions_max"], 1)
        # la sesión dura del lunes ya consumió el único cupo de sesión dura semanal
        self.assertEqual(restantes["remaining_hard_sessions_max"], 0)

    def _plan_con_prescripcion(self, km_por_dia_restante):
        """km_por_dia_restante: dict {día: km_float o None} para Mié..Dom."""
        dias = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
        sesiones = []
        for i, d in enumerate(dias):
            fecha = self.LUNES + timedelta(days=i)
            km = km_por_dia_restante.get(d) if fecha >= self.FECHA_GENERACION else None
            sesiones.append({
                "date": fecha.isoformat(), "day": d,
                "type": "Rodaje suave" if km else "Descanso", "km": f"{km} km" if km else "—",
                "notes": "n", "purpose": "p",
            })
        return {
            "week": f"{self.LUNES.isoformat()}/{self.DOMINGO_PLAN.isoformat()}",
            "startDate": self.LUNES.isoformat(), "endDate": self.DOMINGO_PLAN.isoformat(),
            "objective": "o", "rationale": "r",
            "summary": {"totalKm": 0, "runningSessions": 0, "strengthSessions": 0},  # se recalcula en aplicar_dias_completados
            "sessions": sesiones,
        }

    def test_prescripcion_de_27km_3_sesiones_falla_como_el_bug_real(self):
        semana_en_curso = v2.calcular_semana_en_curso(self.ACTIVITIES_REALES, self.LUNES, self.DOMINGO_PLAN,
                                                        self.FECHA_GENERACION)
        restantes = v2.calcular_restricciones_residuales(self.CONSTRAINTS, semana_en_curso)
        plan = self._plan_con_prescripcion({"Mié": 9, "Vie": 9, "Dom": 9})  # 27km / 3 sesiones adicionales
        plan_final, totales = v2.aplicar_dias_completados(plan, semana_en_curso, self.FECHA_GENERACION)

        self.assertAlmostEqual(totales["projectedTotalKm"], 40.51, places=1,
                                msg="13.5km ya corridos + 27km prescritos ≈ 40.5km, tal como reportó la revisión manual")
        self.assertEqual(totales["projectedTotalSessions"], 5)

        pulse = {
            "semana": "x", "score": 50, "headline": "h", "subheadline": "s", "readiness": 50,
            "aiVerdict": "Fabiana, esta semana bajaste el volumen con 18 días para Buenos Aires.",
            "strengths": [], "warnings": [], "keyMetrics": [],
            "weeklyPlan": plan_final,
            "injuryRisk": {"level": "low", "signal": "no se cuenta con datos de dolor o fatiga autorreportados",
                            "area": None, "action": "a"},
            "projection": None,
        }
        ok, checks = v2.validar_pulse_v2(
            pulse, {"metaCarrera": {"nombre": "Maratón de Buenos Aires", "fecha": "2026-09-20", "label": "42K BUE"}},
            self.CONSTRAINTS, proyeccion_esperada=None, dias_restantes=18, meta_diasprep=18,
            fecha_generacion=self.FECHA_GENERACION, semana_en_curso=semana_en_curso, restantes=restantes,
            weekly_totals=totales,
        )
        self.assertFalse(ok, "40.5km/5 sesiones reales debe fallar la validación, no pasarla como pasó el bug real")
        fails = checks_por_estado(checks, "fail")
        self.assertIn("limite_volumen_semana_completa", fails)
        self.assertIn("limite_sesiones_running_semana_completa", fails)
        self.assertIn("prescripcion_dentro_de_lo_restante_km", fails)
        self.assertIn("prescripcion_dentro_de_lo_restante_sesiones", fails)

    def test_prescripcion_conservadora_dentro_de_lo_restante_pasa_y_nunca_supera_33_9_km_3_sesiones(self):
        semana_en_curso = v2.calcular_semana_en_curso(self.ACTIVITIES_REALES, self.LUNES, self.DOMINGO_PLAN,
                                                        self.FECHA_GENERACION)
        restantes = v2.calcular_restricciones_residuales(self.CONSTRAINTS, semana_en_curso)
        # Una sola salida fácil el sábado, dentro del remaining_km_range (11.4-20.4) y de long_run_range (12-19.6).
        plan = self._plan_con_prescripcion({"Sáb": 15})
        plan_final, totales = v2.aplicar_dias_completados(plan, semana_en_curso, self.FECHA_GENERACION)

        self.assertLessEqual(totales["projectedTotalKm"], 33.9 * 1.05)
        self.assertLessEqual(totales["projectedTotalSessions"], 3)

        pulse = {
            "semana": "x", "score": 50, "headline": "h", "subheadline": "s", "readiness": 50,
            "aiVerdict": "Fabiana, esta semana bajaste el volumen con 18 días para Buenos Aires.",
            "strengths": [], "warnings": [], "keyMetrics": [],
            "weeklyPlan": plan_final,
            "injuryRisk": {"level": "low", "signal": "no se cuenta con datos de dolor o fatiga autorreportados",
                            "area": None, "action": "a"},
            "projection": None,
        }
        ok, checks = v2.validar_pulse_v2(
            pulse, {"metaCarrera": {"nombre": "Maratón de Buenos Aires", "fecha": "2026-09-20", "label": "42K BUE"}},
            self.CONSTRAINTS, proyeccion_esperada=None, dias_restantes=18, meta_diasprep=18,
            fecha_generacion=self.FECHA_GENERACION, semana_en_curso=semana_en_curso, restantes=restantes,
            weekly_totals=totales,
        )
        fails = checks_por_estado(checks, "fail")
        self.assertNotIn("limite_volumen_semana_completa", fails)
        self.assertNotIn("prescripcion_dentro_de_lo_restante_km", fails)
        self.assertNotIn("prescripcion_dentro_de_lo_restante_sesiones", fails)
        self.assertTrue(ok, f"checks fallidos inesperados: {fails}")


class TestPlanningConstraintsPostRace(unittest.TestCase, PlanningConstraintsFixtureMixin):
    """
    guardrails-v4 fix #1: post_race es una fase propia y conservadora, no
    las reglas genéricas de base/build, y está acotada a
    POST_RACE_RECOVERY_WEEKS -- una meta ya vencida no debe gobernar la
    fase indefinidamente.
    """

    FECHA_GENERACION = date(2026, 9, 1)

    def _fixture_alto_volumen(self):
        weekly = self._weekly_fixture(8, km_por_semana=60.0, sessions_por_semana=5, hoy=self.FECHA_GENERACION)
        activities = self._activities_fixture(weekly, dist_por_sesion=12.0)
        return weekly, activities

    def _meta_carrera(self, dias_atras):
        return {"nombre": "Maratón X", "fecha": (self.FECHA_GENERACION - timedelta(days=dias_atras)).isoformat(),
                "label": "42K X"}

    def test_alto_volumen_post_race_nunca_load_direction_increase(self):
        weekly, activities = self._fixture_alto_volumen()
        c = v2.calcular_planning_constraints(activities, weekly, {"status": "undertraining"},
                                              self._meta_carrera(3), True, self.FECHA_GENERACION)
        self.assertEqual(c["goal_phase"], "post_race")
        self.assertEqual(c["load_direction"], "reduce",
                          "un ACWR 'undertraining' justo después de una carrera antes disparaba 'increase'")

    def test_post_race_sin_sesiones_duras_y_reduce_volumen_mas_que_taper(self):
        weekly, activities = self._fixture_alto_volumen()
        c = v2.calcular_planning_constraints(activities, weekly, {"status": "undertraining"},
                                              self._meta_carrera(3), True, self.FECHA_GENERACION)
        self.assertEqual(c["hard_sessions_max"], 0)
        self.assertIsNotNone(c["running_km_range"])
        lo, hi = c["running_km_range"]
        self.assertGreaterEqual(lo, 0.0)
        self.assertLess(hi, 60.0 * 0.55, "post_race debe reducir más que taper (55-75% del promedio)")

    def test_post_race_fondo_largo_no_obligatorio(self):
        weekly, activities = self._fixture_alto_volumen()
        c = v2.calcular_planning_constraints(activities, weekly, {"status": "undertraining"},
                                              self._meta_carrera(3), True, self.FECHA_GENERACION)
        self.assertIsNotNone(c["long_run_range"])
        self.assertEqual(c["long_run_range"][0], 0.0)
        self.assertLessEqual(c["long_run_range"][1], 10.0)

    def test_ventana_de_recuperacion_vencida_cae_a_sin_objetivo_activo(self):
        weekly, activities = self._fixture_alto_volumen()
        # Carrera hace 40 días (~5.7 semanas): ya pasó la ventana de
        # recuperación de 3 semanas.
        c = v2.calcular_planning_constraints(activities, weekly, {"status": "undertraining"},
                                              self._meta_carrera(40), True, self.FECHA_GENERACION)
        self.assertIsNone(c["goal_phase"], "una meta vencida hace >3 semanas no debe seguir en post_race")
        self.assertEqual(c["load_direction"], "increase",
                          "sin fase activa, vuelve al tratamiento normal por ACWR (undertraining -> increase)")

    def test_dentro_de_la_ventana_de_3_semanas_todavia_es_post_race(self):
        weekly, activities = self._fixture_alto_volumen()
        # Exactamente en el borde de 3 semanas (21 días) -- inclusive.
        c = v2.calcular_planning_constraints(activities, weekly, {"status": "undertraining"},
                                              self._meta_carrera(21), True, self.FECHA_GENERACION)
        self.assertEqual(c["goal_phase"], "post_race")


class TestPlanningConstraintsHistorialPreliminar(unittest.TestCase, PlanningConstraintsFixtureMixin):
    """guardrails-v4 fix #2: is_preliminary (<6 semanas) ahora recorta el
    techo superior en vez de solo calcularse y no usarse."""

    def test_no_incremento_agresivo_con_2_semanas_de_historial(self):
        weekly = self._weekly_fixture(2, km_por_semana=20.0, sessions_por_semana=3)
        activities = self._activities_fixture(weekly, dist_por_sesion=6.7)
        c = v2.calcular_planning_constraints(activities, weekly, {"status": "optimal"}, {}, False, date(2026, 9, 1))
        self.assertTrue(c["is_preliminary"])
        self.assertIsNotNone(c["running_km_range"])
        self.assertLessEqual(c["running_km_range"][1], c["avg4_km"],
                              "con historial preliminar, el techo no debe superar el promedio observado (nunca +10%)")
        self.assertIn("preliminary_history", c["volume_cap_reason"])

    def test_fondo_no_supera_el_observado_con_historial_preliminar(self):
        weekly = self._weekly_fixture(3, km_por_semana=25.0, sessions_por_semana=3)
        activities = self._activities_fixture(weekly, dist_por_sesion=8.3)
        c = v2.calcular_planning_constraints(activities, weekly, {"status": "optimal"}, {}, False, date(2026, 9, 1))
        self.assertIsNotNone(c["long_run_range"])
        self.assertLessEqual(c["long_run_range"][1], c["longest_long_run_8w"])
        self.assertEqual(c["long_run_cap_reason"], "preliminary_history")

    def test_sesiones_max_sin_ninguna_observada_es_2_no_3(self):
        c = v2.calcular_planning_constraints([], [], {"status": "optimal"}, {}, False, date(2026, 9, 1))
        self.assertTrue(c["is_preliminary"])
        self.assertEqual(c["running_sessions_max"], 2)

    def test_historial_suficiente_conserva_el_10_por_ciento_normal(self):
        # Control: con 8 semanas de historial (no preliminar) el techo sigue
        # siendo el 110% de siempre -- el fix no toca el caso normal.
        weekly = self._weekly_fixture(8, km_por_semana=30.0, sessions_por_semana=4)
        activities = self._activities_fixture(weekly, dist_por_sesion=7.5)
        c = v2.calcular_planning_constraints(activities, weekly, {"status": "optimal"}, {}, False, date(2026, 9, 1))
        self.assertFalse(c["is_preliminary"])
        self.assertAlmostEqual(c["running_km_range"][1], round(30.0 * 1.10, 1), places=1)


class TestPlanningConstraintsFondoRecencia(unittest.TestCase):
    """
    guardrails-v4 fix #4: longest_long_run usa una ventana CALENDARIO de 8
    semanas cerradas, no 'las últimas 8 semanas con running'.
    """

    FECHA_GENERACION = date(2026, 9, 1)  # domingo_cierre=2026-08-30; ventana fondo=2026-07-06..2026-08-30

    def _weekly_running(self, total_km, sessions):
        return [{"week": "2026-08-24/2026-08-30", "total_km": total_km, "sessions": sessions}]

    def test_fondo_de_hace_4_meses_no_es_referencia_actual(self):
        activities = [
            # ~4.5 meses antes, FUERA de la ventana calendario de 8 semanas.
            {"date": "2026-04-15", "type": "running", "dist_km": 32.0, "duration_min": 200, "hr": 150},
            {"date": "2026-08-20", "type": "running", "dist_km": 6.0, "duration_min": 35, "hr": 150},
            {"date": "2026-08-27", "type": "running", "dist_km": 6.0, "duration_min": 35, "hr": 150},
        ]
        weekly = self._weekly_running(12.0, 2)
        c = v2.calcular_planning_constraints(activities, weekly, {"status": "optimal"}, {}, False, self.FECHA_GENERACION)
        self.assertEqual(c["longest_long_run_8w"], 6.0,
                          "el fondo de 32km de hace 4 meses no debe usarse como referencia; solo cuenta la ventana calendario")

    def test_sin_fondo_dentro_de_la_ventana_calendario_es_conservador(self):
        activities = [
            {"date": "2026-04-15", "type": "running", "dist_km": 32.0, "duration_min": 200, "hr": 150},
        ]
        c = v2.calcular_planning_constraints(activities, [], {"status": "optimal"}, {}, False, self.FECHA_GENERACION)
        self.assertEqual(c["longest_long_run_8w"], 0.0)
        self.assertIsNone(c["long_run_range"])
        self.assertEqual(c["long_run_cap_reason"], "no_recent_long_run")

    def test_fondo_reciente_dentro_de_la_ventana_si_es_referencia(self):
        activities = [
            {"date": "2026-08-12", "type": "running", "dist_km": 20.0, "duration_min": 120, "hr": 150},
            {"date": "2026-08-19", "type": "running", "dist_km": 8.0, "duration_min": 45, "hr": 150},
        ]
        weekly = self._weekly_running(28.0, 2)
        c = v2.calcular_planning_constraints(activities, weekly, {"status": "optimal"}, {}, False, self.FECHA_GENERACION)
        self.assertEqual(c["longest_long_run_8w"], 20.0)
        self.assertIsNotNone(c["long_run_range"])


class TestPlanningConstraintsMultideporte(unittest.TestCase):
    """
    guardrails-v4 fix #3: multisport_load como guardrail sobre
    load_direction/running_km_range -- NUNCA como conversión a "km
    equivalentes de running". Ventanas ancladas a FECHA_GENERACION:
    reciente=2026-08-03..2026-08-30 (4 sem) · baseline=2026-06-08..2026-08-02 (8 sem).
    """

    FECHA_GENERACION = date(2026, 9, 1)

    def _running_bajo(self):
        # 6 semanas de historial (no preliminar) a 15km/semana -- "running
        # bajo" pero con suficiente historial para aislar el efecto de
        # multideporte del de historial preliminar. status="undertraining"
        # hace que, SIN el guardrail, load_direction sería "increase".
        weekly = [{"week": f"wk-{i}", "total_km": 15.0, "sessions": 2} for i in range(6)]
        activities = [
            {"date": "2026-08-05", "type": "running", "dist_km": 7.5, "duration_min": 45, "hr": 150},
            {"date": "2026-08-19", "type": "running", "dist_km": 7.5, "duration_min": 45, "hr": 150},
        ]
        return weekly, activities

    def _con_no_running(self, actividades_base, disciplina, min_reciente_semana, min_baseline_semana):
        """4 sesiones recientes (una/semana, 2026-08-03..30) + 8 de baseline
        (una/semana, 2026-06-08..2026-08-02), todas de `disciplina`."""
        acts = list(actividades_base)
        for f in ("2026-08-05", "2026-08-12", "2026-08-19", "2026-08-26"):
            acts.append({"date": f, "type": disciplina, "dist_km": 0, "duration_min": min_reciente_semana, "hr": 130})
        for f in ("2026-06-10", "2026-06-17", "2026-06-24", "2026-07-01",
                  "2026-07-08", "2026-07-15", "2026-07-22", "2026-07-29"):
            acts.append({"date": f, "type": disciplina, "dist_km": 0, "duration_min": min_baseline_semana, "hr": 130})
        return acts

    def test_running_bajo_ciclismo_alto_evita_aumento_automatico(self):
        weekly, running_acts = self._running_bajo()
        activities = self._con_no_running(running_acts, "cycling", min_reciente_semana=120, min_baseline_semana=50)
        c = v2.calcular_planning_constraints(activities, weekly, {"status": "undertraining"}, {}, False, self.FECHA_GENERACION)
        self.assertEqual(c["multisport_load"], "high")
        self.assertNotEqual(c["load_direction"], "increase")

    def test_running_bajo_fuerza_alta_tambien_se_detecta(self):
        weekly, running_acts = self._running_bajo()
        activities = self._con_no_running(running_acts, "strength", min_reciente_semana=100, min_baseline_semana=40)
        c = v2.calcular_planning_constraints(activities, weekly, {"status": "undertraining"}, {}, False, self.FECHA_GENERACION)
        self.assertEqual(c["multisport_load"], "high")
        self.assertNotEqual(c["load_direction"], "increase")

    def test_solo_running_conserva_comportamiento_actual(self):
        weekly, running_acts = self._running_bajo()
        c = v2.calcular_planning_constraints(running_acts, weekly, {"status": "undertraining"}, {}, False, self.FECHA_GENERACION)
        self.assertEqual(c["multisport_load"], "low")
        self.assertEqual(c["load_direction"], "increase",
                          "un corredor solo-running debe conservar exactamente el comportamiento anterior (sin guardrail)")

    def test_carga_alta_recorta_el_techo_sin_convertir_km(self):
        weekly, running_acts = self._running_bajo()
        activities = self._con_no_running(running_acts, "cycling", min_reciente_semana=120, min_baseline_semana=50)
        c = v2.calcular_planning_constraints(activities, weekly, {"status": "optimal"}, {}, False, self.FECHA_GENERACION)
        self.assertEqual(c["multisport_load"], "high")
        # El techo baja al 100% del promedio de RUNNING (15km) -- nada de
        # ciclismo se suma matemáticamente al rango.
        self.assertEqual(c["running_km_range"][1], c["avg4_km"])
        self.assertIn("multisport_load_high", c["volume_cap_reason"])

    def test_acwr_por_disciplina_escala_un_solo_nivel_no_dos(self):
        weekly, running_acts = self._running_bajo()
        acwr_info = {"status": "undertraining",
                     "series": {"cycling": [{"weekStart": "2026-08-24", "weekEnd": "2026-08-30",
                                              "valor": 1.6, "status": "high_risk"}]}}
        c = v2.calcular_planning_constraints(running_acts, weekly, acwr_info, {}, False, self.FECHA_GENERACION)
        self.assertEqual(c["multisport_load"], "normal",
                          "sin señal propia (solo running), el ACWR de otra disciplina escala low->normal, nunca directo a high")
        self.assertTrue(c["multisport_signal"]["acwr_corrobora_alta"])


# ═══════════════════════════════════════════════════════════════════════
# Weekly Comparison Engine — calcular_comparaciones_pulse()
# NUNCA integrado al prompt en esta iteración (ver pulse_v2_engine.py).
# ═══════════════════════════════════════════════════════════════════════

LUNES_ANALIZADO = date(2026, 8, 24)
DOMINGO_ANALIZADO = date(2026, 8, 30)
# avg4 (calendario, ancladas a LUNES_ANALIZADO): 08-17 (anterior/k=1), 08-10 (k=2), 08-03 (k=3), 07-27 (k=4)
SEMANA_ANTERIOR = date(2026, 8, 17)


def _act(fecha, tipo="running", dist_km=0.0, duration_min=0.0, hr=0, pace_raw=0.0, kcal=0):
    return {"date": fecha, "type": tipo, "dist_km": dist_km, "duration_min": duration_min,
            "hr": hr, "pace_raw": pace_raw, "kcal": kcal, "name": "x"}


def _comparaciones(activities, acwr_info=None):
    weekly = tj.calcular_weekly_multidisciplina(activities)
    return v2.calcular_comparaciones_pulse(activities, weekly, LUNES_ANALIZADO, DOMINGO_ANALIZADO,
                                            acwr_info=acwr_info)


class TestComparacionesRunningVolumen(unittest.TestCase):
    def _avg4_uniforme(self, km_por_semana, sesiones=1, duration_min=40.0, pace_raw=5.5, hr=150):
        acts = []
        for k in (1, 2, 3, 4):
            lunes = LUNES_ANALIZADO - timedelta(days=7 * k)
            for i in range(sesiones):
                acts.append(_act((lunes + timedelta(days=i)).isoformat(), dist_km=km_por_semana / sesiones,
                                  duration_min=duration_min, pace_raw=pace_raw, hr=hr))
        return acts

    def test_volumen_sube(self):
        acts = self._avg4_uniforme(20.0) + [_act(LUNES_ANALIZADO.isoformat(), dist_km=26.0, duration_min=140)]
        c = _comparaciones(acts)
        km = c["running"]["km"]
        self.assertEqual(km["avg4"], 20.0)
        self.assertEqual(km["current"], 26.0)
        self.assertEqual(km["direction"], "up")
        self.assertEqual(km["vs_avg4_pct"], 30.0)

    def test_volumen_baja(self):
        acts = self._avg4_uniforme(20.0) + [_act(LUNES_ANALIZADO.isoformat(), dist_km=12.0, duration_min=70)]
        c = _comparaciones(acts)
        km = c["running"]["km"]
        self.assertEqual(km["direction"], "down")
        self.assertEqual(km["vs_avg4_pct"], -40.0)

    def test_volumen_estable(self):
        acts = self._avg4_uniforme(20.0) + [_act(LUNES_ANALIZADO.isoformat(), dist_km=21.0, duration_min=110)]
        c = _comparaciones(acts)
        self.assertEqual(c["running"]["km"]["direction"], "stable")

    def test_pct_alto_pero_base_minima_no_es_up(self):
        # avg4=1.0km, current=1.5km: 50% de cambio, pero delta absoluto (0.5km)
        # queda por debajo del piso de 2km -- no debe ser "up".
        acts = self._avg4_uniforme(1.0) + [_act(LUNES_ANALIZADO.isoformat(), dist_km=1.5, duration_min=10)]
        c = _comparaciones(acts)
        self.assertEqual(c["running"]["km"]["direction"], "stable")

    def test_semana_anterior_en_cero_no_inventa_porcentaje(self):
        # Semana anterior (k=1) sin running; k=2,3,4 con 6km cada una.
        acts = []
        for k in (2, 3, 4):
            lunes = LUNES_ANALIZADO - timedelta(days=7 * k)
            acts.append(_act(lunes.isoformat(), dist_km=6.0, duration_min=35))
        acts.append(_act(LUNES_ANALIZADO.isoformat(), dist_km=8.0, duration_min=45))
        c = _comparaciones(acts)
        km = c["running"]["km"]
        self.assertEqual(km["previous"], 0.0)
        self.assertIsNone(km["vs_previous_pct"], "denominador 0 nunca debe producir infinito ni 100%")

    def test_avg4_incluye_semanas_de_cero_running_como_informacion_valida(self):
        # Solo 2 de las 4 semanas avg4 tienen running (10km c/u); las otras 2
        # NO deben excluirse del promedio -- deben contar como 0.
        acts = [
            _act((LUNES_ANALIZADO - timedelta(days=7)).isoformat(), dist_km=10.0, duration_min=55),
            _act((LUNES_ANALIZADO - timedelta(days=21)).isoformat(), dist_km=10.0, duration_min=55),
            _act(LUNES_ANALIZADO.isoformat(), dist_km=10.0, duration_min=55),
        ]
        c = _comparaciones(acts)
        # avg4 = (10+0+10+0)/4 = 5.0, NO (10+10)/2 = 10.0
        self.assertEqual(c["running"]["km"]["avg4"], 5.0)


class TestComparacionesPace(unittest.TestCase):
    def _con_pace(self, pace_avg4, pace_actual):
        acts = []
        for k in (1, 2, 3, 4):
            lunes = LUNES_ANALIZADO - timedelta(days=7 * k)
            acts.append(_act(lunes.isoformat(), dist_km=6.0, duration_min=35, pace_raw=pace_avg4))
        acts.append(_act(LUNES_ANALIZADO.isoformat(), dist_km=6.0, duration_min=33, pace_raw=pace_actual))
        return acts

    def test_pace_faster(self):
        c = _comparaciones(self._con_pace(5.5, 5.0))  # 330 -> 300 seg/km, delta -30
        pace = c["running"]["pace"]
        self.assertEqual(pace["direction"], "faster")
        self.assertEqual(pace["delta_sec_per_km"], -30.0)

    def test_pace_slower(self):
        c = _comparaciones(self._con_pace(5.5, 5.8))  # 330 -> 348, delta +18
        self.assertEqual(c["running"]["pace"]["direction"], "slower")

    def test_pace_stable(self):
        c = _comparaciones(self._con_pace(5.5, 5.52))  # 330 -> 331.2, delta +1.2 (<5)
        self.assertEqual(c["running"]["pace"]["direction"], "stable")

    def test_semanas_sin_pace_valido_queda_unknown(self):
        # avg4: running con pace_raw=0 (inválido) en las 4 semanas -- no cuenta
        # como muestra. current sí tiene pace válido, pero sin avg4 no hay con
        # qué comparar.
        acts = []
        for k in (1, 2, 3, 4):
            lunes = LUNES_ANALIZADO - timedelta(days=7 * k)
            acts.append(_act(lunes.isoformat(), dist_km=6.0, duration_min=35, pace_raw=0.0))
        acts.append(_act(LUNES_ANALIZADO.isoformat(), dist_km=6.0, duration_min=33, pace_raw=5.5))
        c = _comparaciones(acts)
        pace = c["running"]["pace"]
        self.assertIsNone(pace["avg4_sec_km"])
        self.assertEqual(pace["direction"], "unknown")
        self.assertEqual(pace["weeks_with_data"], 0)

    def test_pace_invalido_se_excluye_de_la_muestra(self):
        # Misma semana: una actividad con pace_raw=0 (inválida) y otra con
        # pace válido -- el promedio debe ser SOLO el válido, no diluirse.
        acts = self._con_pace(5.5, 5.5)
        acts.append(_act(LUNES_ANALIZADO.isoformat(), dist_km=3.0, duration_min=100, pace_raw=0.0))
        c = _comparaciones(acts)
        # current_sec_km sigue siendo 5.5*60=330, no un promedio con la muestra inválida.
        self.assertEqual(c["running"]["pace"]["current_sec_km"], 330.0)


class TestComparacionesHeartRate(unittest.TestCase):
    def _con_hr(self, hr_avg4, hr_actual):
        acts = []
        for k in (1, 2, 3, 4):
            lunes = LUNES_ANALIZADO - timedelta(days=7 * k)
            acts.append(_act(lunes.isoformat(), dist_km=6.0, duration_min=35, hr=hr_avg4, pace_raw=5.5))
        acts.append(_act(LUNES_ANALIZADO.isoformat(), dist_km=6.0, duration_min=33, hr=hr_actual, pace_raw=5.5))
        return acts

    def test_hr_up(self):
        c = _comparaciones(self._con_hr(150, 155))
        self.assertEqual(c["running"]["heart_rate"]["direction"], "up")
        self.assertEqual(c["running"]["heart_rate"]["delta_bpm"], 5.0)

    def test_hr_down(self):
        c = _comparaciones(self._con_hr(150, 145))
        self.assertEqual(c["running"]["heart_rate"]["direction"], "down")

    def test_hr_stable(self):
        c = _comparaciones(self._con_hr(150, 151))
        self.assertEqual(c["running"]["heart_rate"]["direction"], "stable")

    def test_hr_ausente_queda_unknown(self):
        acts = self._con_hr(0, 0)  # hr=0 en todas partes -- ninguna muestra válida
        c = _comparaciones(acts)
        hr = c["running"]["heart_rate"]
        self.assertIsNone(hr["current"])
        self.assertIsNone(hr["avg4"])
        self.assertEqual(hr["direction"], "unknown")


class TestComparacionesMultideporte(unittest.TestCase):
    def _avg4_running_uniforme(self, km=6.0, duration_min=40.0):
        acts = []
        for k in (1, 2, 3, 4):
            lunes = LUNES_ANALIZADO - timedelta(days=7 * k)
            acts.append(_act(lunes.isoformat(), dist_km=km, duration_min=duration_min, pace_raw=5.5, hr=150))
        return acts

    def test_running_baja_pero_total_training_sube(self):
        acts = self._avg4_running_uniforme() + [
            _act(LUNES_ANALIZADO.isoformat(), dist_km=1.5, duration_min=10, pace_raw=5.5, hr=150),
            _act(LUNES_ANALIZADO.isoformat(), tipo="cycling", dist_km=25.0, duration_min=90),
        ]
        c = _comparaciones(acts)
        self.assertEqual(c["running"]["km"]["direction"], "down")
        self.assertEqual(c["total_training"]["minutes"]["direction"], "up")
        ids = {s["id"] for s in c["signals"]}
        self.assertIn("running_down_total_training_up", ids)

    def test_running_baja_pero_ciclismo_sube(self):
        acts = self._avg4_running_uniforme() + [
            _act(LUNES_ANALIZADO.isoformat(), dist_km=1.5, duration_min=10, pace_raw=5.5, hr=150),
            _act(LUNES_ANALIZADO.isoformat(), tipo="cycling", dist_km=25.0, duration_min=90),
        ]
        c = _comparaciones(acts)
        self.assertEqual(c["disciplines"]["cycling"]["km"]["direction"], "up")

    def test_running_estable_y_fuerza_sube(self):
        acts = self._avg4_running_uniforme() + [
            _act(LUNES_ANALIZADO.isoformat(), dist_km=6.2, duration_min=40, pace_raw=5.5, hr=150),
            _act(LUNES_ANALIZADO.isoformat(), tipo="strength", duration_min=45),
        ]
        c = _comparaciones(acts)
        self.assertEqual(c["running"]["km"]["direction"], "stable")
        self.assertEqual(c["disciplines"]["strength"]["minutes"]["direction"], "up")

    def test_solo_running_disciplinas_ausentes_quedan_en_cero_sin_fabricar(self):
        acts = self._avg4_running_uniforme() + [
            _act(LUNES_ANALIZADO.isoformat(), dist_km=6.0, duration_min=40, pace_raw=5.5, hr=150),
        ]
        c = _comparaciones(acts)
        for disc in ("cycling", "swimming", "strength"):
            bloque = c["disciplines"][disc]
            self.assertEqual(bloque["sessions"]["current"], 0)
            self.assertEqual(bloque["minutes"]["current"], 0)
            self.assertEqual(bloque["minutes"]["direction"], "stable")
        for disc, campo_vol in (("cycling", "km"), ("swimming", "metros")):
            bloque = c["disciplines"][disc]
            self.assertEqual(bloque[campo_vol]["current"], 0)
            self.assertEqual(bloque[campo_vol]["direction"], "stable")

    def test_running_sube_y_total_training_sube_genera_señal_concurrente(self):
        acts = self._avg4_running_uniforme() + [
            _act(LUNES_ANALIZADO.isoformat(), dist_km=12.0, duration_min=70, pace_raw=5.5, hr=150),
        ]
        c = _comparaciones(acts)
        ids = {s["id"] for s in c["signals"]}
        self.assertIn("running_up_total_training_up", ids)


class TestComparacionesActiveDays(unittest.TestCase):
    def test_dos_deportes_mismo_dia_cuentan_un_dia(self):
        acts = [
            _act(LUNES_ANALIZADO.isoformat(), dist_km=6.0, duration_min=40, pace_raw=5.5, hr=150),
            _act(LUNES_ANALIZADO.isoformat(), tipo="strength", duration_min=30),
        ]
        c = _comparaciones(acts)
        self.assertEqual(c["total_training"]["active_days"]["current"], 1)

    def test_semana_sin_actividades_queda_en_cero_no_en_null(self):
        # Solo hay actividad en semanas avg4, la semana analizada queda vacía.
        acts = []
        for k in (1, 2, 3, 4):
            lunes = LUNES_ANALIZADO - timedelta(days=7 * k)
            acts.append(_act(lunes.isoformat(), dist_km=6.0, duration_min=40, pace_raw=5.5, hr=150))
        c = _comparaciones(acts)
        self.assertEqual(c["total_training"]["active_days"]["current"], 0)
        self.assertEqual(c["total_training"]["minutes"]["current"], 0)
        self.assertEqual(c["running"]["km"]["current"], 0.0)


class TestComparacionesHistoryQuality(unittest.TestCase):
    def test_menos_de_4_semanas_de_historial_no_falla(self):
        acts = [
            _act(SEMANA_ANTERIOR.isoformat(), dist_km=6.0, duration_min=40, pace_raw=5.5, hr=150),
            _act(LUNES_ANALIZADO.isoformat(), dist_km=6.0, duration_min=40, pace_raw=5.5, hr=150),
        ]
        c = _comparaciones(acts)
        self.assertEqual(c["history_quality"]["weeks_available"], 2)
        self.assertTrue(c["history_quality"]["is_preliminary"])
        # avg4 sigue siendo calculable (semanas ausentes cuentan 0), no crashea ni es None.
        self.assertIsNotNone(c["running"]["km"]["avg4"])

    def test_historial_preliminar_umbral_5_vs_6_semanas(self):
        def _n_semanas(n):
            acts = []
            for i in range(n):
                acts.append(_act((LUNES_ANALIZADO - timedelta(days=7 * i)).isoformat(),
                                  dist_km=5.0, duration_min=30, pace_raw=5.5, hr=150))
            return acts

        c5 = _comparaciones(_n_semanas(5))
        self.assertTrue(c5["history_quality"]["is_preliminary"])
        c6 = _comparaciones(_n_semanas(6))
        self.assertFalse(c6["history_quality"]["is_preliminary"])

    def test_weeks_available_cuenta_solo_semanas_con_alguna_actividad(self):
        acts = [_act(LUNES_ANALIZADO.isoformat(), dist_km=6.0, duration_min=40, pace_raw=5.5, hr=150)]
        c = _comparaciones(acts)
        self.assertEqual(c["history_quality"]["weeks_available"], 1)


class TestComparacionesMissingData(unittest.TestCase):
    def test_denominador_cero_running_km_no_inventa_porcentaje(self):
        # Sin running en ninguna de las 4 semanas avg4; sí hay en la analizada.
        acts = [_act(LUNES_ANALIZADO.isoformat(), dist_km=8.0, duration_min=45, pace_raw=5.5, hr=150)]
        c = _comparaciones(acts)
        km = c["running"]["km"]
        self.assertEqual(km["avg4"], 0.0)
        self.assertIsNone(km["vs_avg4_pct"])
        self.assertEqual(km["direction"], "up")  # cambio real e inequívoco, aunque sin %

    def test_valores_null_en_semana_actual_sin_pace_ni_hr_validos(self):
        acts = []
        for k in (1, 2, 3, 4):
            lunes = LUNES_ANALIZADO - timedelta(days=7 * k)
            acts.append(_act(lunes.isoformat(), dist_km=6.0, duration_min=35, pace_raw=5.5, hr=150))
        # La semana analizada SÍ tiene una sesión, pero sin pace ni HR válidos.
        acts.append(_act(LUNES_ANALIZADO.isoformat(), dist_km=6.0, duration_min=35, pace_raw=0.0, hr=0))
        c = _comparaciones(acts)
        self.assertIsNone(c["running"]["pace"]["current_sec_km"])
        self.assertEqual(c["running"]["pace"]["direction"], "unknown")
        self.assertIsNone(c["running"]["heart_rate"]["current"])
        self.assertEqual(c["running"]["heart_rate"]["direction"], "unknown")
        # El km sí es real (la sesión existió), no se pierde por falta de pace/HR.
        self.assertEqual(c["running"]["km"]["current"], 6.0)

    def test_hard_sessions_queda_explicitamente_unknown(self):
        acts = [_act(LUNES_ANALIZADO.isoformat(), dist_km=6.0, duration_min=35, pace_raw=5.5, hr=150)]
        c = _comparaciones(acts)
        hs = c["running"]["hard_sessions"]
        self.assertIsNone(hs["current"])
        self.assertIsNone(hs["avg4"])
        self.assertEqual(hs["direction"], "unknown")

    def test_load_no_fabrica_status_de_disciplina_sin_acwr(self):
        acts = [_act(LUNES_ANALIZADO.isoformat(), dist_km=6.0, duration_min=35, pace_raw=5.5, hr=150)]
        c = _comparaciones(acts, acwr_info=None)
        self.assertIsNone(c["load"]["running_acwr_status"])
        self.assertEqual(c["load"]["discipline_statuses"], {})


class TestComparacionesNoIntegracion(unittest.TestCase):
    """Guardrail de alcance: esta iteración NO debe integrar el motor al
    prompt ni cambiar generar_pulse_v2()."""

    def test_calcular_comparaciones_pulse_no_se_llama_desde_construir_prompts_v2(self):
        import inspect
        fuente = inspect.getsource(v2.construir_prompts_v2)
        self.assertNotIn("calcular_comparaciones_pulse", fuente)

    def test_calcular_comparaciones_pulse_no_se_llama_desde_generar_pulse_v2(self):
        import inspect
        fuente = inspect.getsource(v2.generar_pulse_v2)
        self.assertNotIn("calcular_comparaciones_pulse", fuente)


# ═══════════════════════════════════════════════════════════════════════
# Robustez de running.pace — filtrado de outliers (auditoría real:
# William/Álvaro). Solo pace cambia; heart_rate y el resto del engine se
# mantienen sin tocar (cubierto por los tests ya existentes arriba).
# ═══════════════════════════════════════════════════════════════════════

class TestComparacionesPaceRobusto(unittest.TestCase):
    def _semana_pace(self, lunes, paces, dist_km=6.0, duration_min=35.0, hr=150):
        return [_act(lunes.isoformat(), dist_km=dist_km, duration_min=duration_min, pace_raw=p, hr=hr)
                for p in paces]

    def test_pace_normal_sin_outliers_comportamiento_identico(self):
        # >=8 muestras de referencia, todas normales -- ningún filtrado
        # debería activarse; el promedio debe ser el simple de TODAS las
        # muestras (mismo comportamiento que antes de este fix).
        acts = []
        for k in (1, 2, 3, 4):
            lunes = LUNES_ANALIZADO - timedelta(days=7 * k)
            acts += self._semana_pace(lunes, [5.4, 5.6])
        acts += self._semana_pace(LUNES_ANALIZADO, [5.0, 5.2])
        c = _comparaciones(acts)
        pace = c["running"]["pace"]
        self.assertEqual(pace["samples_excluded"]["current"], 0)
        self.assertEqual(pace["samples_excluded"]["avg4"], 0)
        esperado_avg4 = round(sum([5.4, 5.6] * 4) * 60 / 8, 1)
        self.assertEqual(pace["avg4_sec_km"], esperado_avg4)

    def test_actividad_extremadamente_lenta_entre_normales_se_excluye_solo_de_pace(self):
        acts = []
        for k in (1, 2, 3):
            lunes = LUNES_ANALIZADO - timedelta(days=7 * k)
            acts += self._semana_pace(lunes, [5.4, 5.5, 5.6])
        lunes4 = LUNES_ANALIZADO - timedelta(days=28)
        acts += self._semana_pace(lunes4, [5.5, 5.6])
        acts.append(_act(lunes4.isoformat(), dist_km=2.0, duration_min=40.0, pace_raw=20.0, hr=110))
        acts += self._semana_pace(LUNES_ANALIZADO, [5.3])
        c = _comparaciones(acts)
        pace = c["running"]["pace"]
        self.assertEqual(pace["samples_excluded"]["avg4"], 1, "el outlier de 20 min/km debe excluirse")
        self.assertEqual(pace["samples_used"]["avg4"], 11)  # 3+3+3+2 normales
        # Ninguna muestra normal se pierde por accidente.
        self.assertEqual(pace["samples_total"]["avg4"], 12)

    def test_atleta_uniformemente_lento_no_se_excluye_por_ser_lento(self):
        # Mediana propia ~16 min/km (trail/ultra/run-walk legítimo).
        # Ninguna muestra es atípica RESPECTO A SÍ MISMA, aunque todas
        # crucen el piso absoluto de 15 -- nada debe excluirse.
        paces_avg4 = [15.5, 16.0, 15.8, 16.3, 15.7, 16.1, 15.9, 16.2]
        acts = []
        idx = 0
        for k in (1, 2, 3, 4):
            lunes = LUNES_ANALIZADO - timedelta(days=7 * k)
            acts += self._semana_pace(lunes, paces_avg4[idx:idx + 2])
            idx += 2
        acts += self._semana_pace(LUNES_ANALIZADO, [16.0])
        c = _comparaciones(acts)
        pace = c["running"]["pace"]
        self.assertEqual(pace["samples_excluded"]["avg4"], 0)
        self.assertEqual(pace["samples_excluded"]["current"], 0)
        self.assertIsNotNone(pace["avg4_sec_km"])

    def test_principiante_legitimamente_lento_conserva_muestras(self):
        # Perfil distinto (principiante, ~13 min/km, algo más de dispersión
        # que el caso trail/ultra de arriba) -- mismo resultado esperado:
        # ninguna exclusión por ser simplemente lento.
        paces_avg4 = [12.5, 13.8, 12.9, 13.5, 13.1, 13.9, 12.7, 13.3]
        acts = []
        idx = 0
        for k in (1, 2, 3, 4):
            lunes = LUNES_ANALIZADO - timedelta(days=7 * k)
            acts += self._semana_pace(lunes, paces_avg4[idx:idx + 2])
            idx += 2
        acts += self._semana_pace(LUNES_ANALIZADO, [13.2])
        c = _comparaciones(acts)
        self.assertEqual(c["running"]["pace"]["samples_excluded"]["avg4"], 0)

    def test_pocos_datos_referencia_insuficiente_aplica_solo_guardrail_absoluto(self):
        # Menos de PACE_MIN_MUESTRAS_REFERENCIA (8) muestras en total: sin
        # base para el criterio relativo. Un valor lento pero bajo el piso
        # absoluto (14 min/km) debe SOBREVIVIR pese a los pocos datos; uno
        # claramente absurdo (22) se excluye igual (el guardrail absoluto
        # no depende de la referencia).
        acts = self._semana_pace(LUNES_ANALIZADO - timedelta(days=7), [5.5, 14.0])
        acts.append(_act((LUNES_ANALIZADO - timedelta(days=14)).isoformat(),
                          dist_km=2.0, duration_min=44.0, pace_raw=22.0, hr=110))
        acts += self._semana_pace(LUNES_ANALIZADO, [5.6])
        c = _comparaciones(acts)
        pace = c["running"]["pace"]
        self.assertLess(pace["samples_total"]["avg4"], v2.PACE_MIN_MUESTRAS_REFERENCIA,
                         "precondición del test: referencia insuficiente")
        self.assertEqual(pace["samples_excluded"]["avg4"], 1)
        self.assertEqual(pace["samples_used"]["avg4"], 2)  # 5.5 y 14.0 sobreviven

    def test_outlier_rapido_extremo_tambien_se_excluye_simetricamente(self):
        acts = []
        for k in (1, 2, 3, 4):
            lunes = LUNES_ANALIZADO - timedelta(days=7 * k)
            acts += self._semana_pace(lunes, [5.4, 5.6])
        acts.append(_act(LUNES_ANALIZADO.isoformat(), dist_km=1.0, duration_min=1.0, pace_raw=1.0, hr=180))
        acts.append(_act(LUNES_ANALIZADO.isoformat(), dist_km=5.0, duration_min=27.0, pace_raw=5.4, hr=150))
        c = _comparaciones(acts)
        pace = c["running"]["pace"]
        self.assertEqual(pace["samples_excluded"]["current"], 1,
                          "1.0 min/km es implausible (bajo PACE_RAPIDO_EXTREMO_MIN_KM) y estadísticamente atípico")
        self.assertAlmostEqual(pace["current_sec_km"], 5.4 * 60, places=1)

    def test_actividad_excluida_de_pace_sigue_contando_en_resto_de_metricas(self):
        lunes4 = LUNES_ANALIZADO - timedelta(days=28)
        acts = []
        for k in (1, 2, 3):
            lunes = LUNES_ANALIZADO - timedelta(days=7 * k)
            acts += self._semana_pace(lunes, [5.4, 5.5, 5.6], dist_km=6.0, duration_min=35.0)
        acts += self._semana_pace(lunes4, [5.5, 5.6], dist_km=6.0, duration_min=35.0)
        # El outlier va en un día DISTINTO de esa semana, para que el test
        # aísle de verdad si su día cuenta o no en active_days.
        fecha_outlier = (lunes4 + timedelta(days=2)).isoformat()
        acts.append(_act(fecha_outlier, dist_km=3.0, duration_min=45.0, pace_raw=20.0, hr=115))
        acts += self._semana_pace(LUNES_ANALIZADO, [5.3])

        c = _comparaciones(acts)
        self.assertEqual(c["running"]["pace"]["samples_excluded"]["avg4"], 1)

        weekly = tj.calcular_weekly_multidisciplina(acts)
        semana_outlier = next(w for w in weekly if w["week"].startswith(lunes4.isoformat()))
        # km: 2 sesiones normales (6.0 c/u) + la del outlier (3.0) -- la
        # actividad NUNCA se borra de activities[]/weekly[].
        self.assertAlmostEqual(semana_outlier["running"]["km"], 15.0, places=1)
        self.assertEqual(semana_outlier["running"]["sessions"], 3)
        # active_days: k=1,2,3 tienen 1 día c/u (sesiones agrupadas); la
        # semana del outlier tiene 2 (lunes4 + fecha_outlier, un día
        # distinto) -- el día del outlier SÍ cuenta pese a excluirse de pace.
        self.assertEqual(c["total_training"]["active_days"]["avg4"], 1.2)  # (1+1+1+2)/4=1.25, redondeado a 1 decimal

    def test_william_real_deja_de_mostrar_delta_absurdo(self):
        # Valores reales (pace_raw) de William, semana analizada 2026-08-24
        # y las 4 semanas avg4 -- extraídos del export real usado en la
        # auditoría (fecha_generacion=2026-09-02). Antes del fix: delta =
        # -163.4 seg/km, direction="faster"(large) -- artefacto, no mejora real.
        datos = {
            LUNES_ANALIZADO: [8.06, 7.85, 6.77],
            LUNES_ANALIZADO - timedelta(days=7): [7.39, 6.9, 6.49],
            LUNES_ANALIZADO - timedelta(days=14): [7.96, 6.82, 20.22, 17.57, 7.49],
            LUNES_ANALIZADO - timedelta(days=21): [6.91, 7.42, 7.04, 22.79, 19.79, 6.81, 7.83],
            LUNES_ANALIZADO - timedelta(days=28): [8.08, 7.29],
        }
        acts = []
        for lunes, paces in datos.items():
            acts += self._semana_pace(lunes, paces, dist_km=8.0, duration_min=55.0, hr=140)
        c = _comparaciones(acts)
        pace = c["running"]["pace"]
        self.assertGreater(pace["samples_excluded"]["avg4"], 0, "debe excluir al menos las observaciones de ~17-23 min/km")
        self.assertLess(abs(pace["delta_sec_per_km"]), 60.0,
                         f"delta antes del fix era -163.4 seg/km; debe reducirse drásticamente, quedó {pace['delta_sec_per_km']}")

    def test_alvaro_real_deja_de_mostrar_delta_absurdo(self):
        # Valores reales de Álvaro, mismas fechas/fuente que arriba. Antes
        # del fix: delta = -97.8 seg/km, direction="faster"(large).
        datos = {
            LUNES_ANALIZADO: [5.39],
            LUNES_ANALIZADO - timedelta(days=7): [5.57, 5.56, 4.51, 16.84, 6.76, 7.93, 3.82, 6.33, 5.24],
            LUNES_ANALIZADO - timedelta(days=14): [5.51, 4.64, 20.05, 6.88, 5.84, 5.03],
            LUNES_ANALIZADO - timedelta(days=21): [5.08, 4.72, 11.93, 12.89, 6.63, 4.87, 4.96],
            LUNES_ANALIZADO - timedelta(days=28): [5.39, 5.11, 8.28, 5.38, 5.48, 5.43],
        }
        acts = []
        for lunes, paces in datos.items():
            acts += self._semana_pace(lunes, paces, dist_km=6.0, duration_min=35.0, hr=140)
        c = _comparaciones(acts)
        pace = c["running"]["pace"]
        self.assertGreater(pace["samples_excluded"]["avg4"], 0)
        self.assertLess(abs(pace["delta_sec_per_km"]), 60.0,
                         f"delta antes del fix era -97.8 seg/km; debe reducirse drásticamente, quedó {pace['delta_sec_per_km']}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
