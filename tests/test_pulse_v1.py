#!/usr/bin/env python3
"""
Pruebas de regresión para congelar PULSE v1 (transformar_json.py).

Objetivo: comprobar que congelar generar_pulse() -> generar_pulse_v1() +
alias de compatibilidad no cambió ningún comportamiento observable. NO
prueba producto (calidad del análisis, contenido del texto) — solo el
pipeline mecánico: qué se le manda a Claude, cómo se parsea, qué se inyecta
después, y qué pasa cuando algo falla.

Nunca llama a la API real de Anthropic: `anthropic.Anthropic` se mockea en
cada test. Corre gratis y rápido.

Uso: python3 tests/test_pulse_v1.py
"""

import json
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import transformar_json as tj


# ── Fixtures ──────────────────────────────────────────────────

def _fake_activity(fecha, tipo="running", dist_km=8.0, duration_min=45.0,
                    hr=150, pace_raw=5.6, name="Salida suave"):
    """Actividad ya en formato interno (post transformar_actividad)."""
    return {
        "date": fecha.isoformat(),
        "name": name,
        "type": tipo,
        "dist_km": dist_km,
        "duration_min": duration_min,
        "pace": tj.pace_a_string(pace_raw),
        "pace_raw": pace_raw,
        "hr": hr,
        "kcal": 500,
        "elevation": 50,
        "points": 0,
        "effort": 1.0,
        "heart_eff": 0.01,
    }


def _fake_raw_activity(fecha, activity_type="Running", dist_m=8000,
                        duration_s=2700, hr=150, name="Salida suave"):
    """Actividad en formato crudo (como llega de la app del socio), para
    probar transformar() de punta a punta."""
    return {
        "name": name,
        "activity_type": activity_type,
        "start_time_utc": f"{fecha.isoformat()}T08:00:00Z",
        "distance_in_meters": dist_m,
        "duration_in_seconds": duration_s,
        "average_pace_in_minutes_per_kilometer": (duration_s / 60) / (dist_m / 1000),
        "average_heart_rate_in_beats_per_minute": hr,
        "active_kilocalories": 500,
        "total_elevation_gain_in_meters": 40,
        "effort_density": 1.0,
        "heart_efficiency": 0.01,
    }


VALID_PULSE_JSON = {
    "semana": "rango",
    "score": 80,
    "headline": "Semana consistente",
    "subheadline": "Carga estable",
    "readiness": 70,
    "aiVerdict": "Alejandro, esta semana mantuviste tu ritmo habitual.",
    "strengths": ["Consistencia"],
    "warnings": [],
    "keyMetrics": [],
    "weeklyPlan": {
        "objective": "Sostener el volumen",
        "rationale": "La carga se mantiene en zona segura.",
        "sessions": [],
    },
    "injuryRisk": {"level": "low", "score": 10, "topRisk": "—", "action": "—"},
    "funFact": None,
    "seoulTip": None,
}


class FakeAnthropicMessage:
    def __init__(self, text):
        self.content = [MagicMock(text=text)]


def _mock_client(response_text=None, side_effect=None):
    client = MagicMock()
    if side_effect is not None:
        client.messages.create.side_effect = side_effect
    else:
        text = response_text or json.dumps(VALID_PULSE_JSON, ensure_ascii=False)
        client.messages.create.return_value = FakeAnthropicMessage(text)
    return client


class PulseV1TestCase(unittest.TestCase):
    def setUp(self):
        self.lunes_analizado, self.domingo_analizado = tj.ultima_semana_completa()
        self.lunes_semana_actual = self.lunes_analizado + timedelta(days=7)

        # Una sesión dentro de la semana cerrada (debe llegar al prompt) y
        # una en la semana en curso (NUNCA debe llegar al prompt).
        self.activity_cerrada = _fake_activity(self.domingo_analizado, name="Salida suave")
        self.activity_en_curso = _fake_activity(self.lunes_semana_actual, name="NoDebeAparecerEnElPrompt")
        self.activities = [self.activity_cerrada, self.activity_en_curso]
        self.weekly = tj.calcular_weekly_multidisciplina(self.activities)

        self.meta = {
            "nombre": "ALEJANDRO",
            "metaCarrera": {"nombre": "¿Cuál es tu próxima carrera?", "fecha": "2027-01-01", "label": "META"},
            "prs": [],
        }
        self.profile = {"personal_records": []}
        self.acwr_info = {"status": "optimal", "series": {}}

    # 1. Construcción del prompt + exclusión de la semana en curso
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "fake-key"})
    @patch("anthropic.Anthropic")
    def test_prompt_excluye_actividades_de_la_semana_en_curso(self, mock_anthropic_cls):
        client = _mock_client()
        mock_anthropic_cls.return_value = client

        tj.generar_pulse_v1(self.activities, self.weekly, self.meta, self.profile, self.acwr_info)

        self.assertTrue(client.messages.create.called)
        _, kwargs = client.messages.create.call_args
        system_prompt = kwargs["system"]
        user_prompt = kwargs["messages"][0]["content"]

        self.assertNotIn("NoDebeAparecerEnElPrompt", system_prompt)
        self.assertNotIn("NoDebeAparecerEnElPrompt", user_prompt)
        # La semana analizada (cerrada) sí debe estar presente.
        self.assertIn(self.domingo_analizado.isoformat()[:4], user_prompt)
        # Los parámetros de la llamada no deben cambiar al congelar v1.
        self.assertEqual(kwargs["model"], "claude-sonnet-4-6")
        self.assertEqual(kwargs["max_tokens"], 2500)
        self.assertEqual(kwargs["temperature"], 0.3)

    # 2. Parsing del JSON (incluye limpieza de fences ```json ... ```)
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "fake-key"})
    @patch("anthropic.Anthropic")
    def test_parsing_extrae_json_con_fences(self, mock_anthropic_cls):
        texto_con_fences = "```json\n" + json.dumps(VALID_PULSE_JSON, ensure_ascii=False) + "\n```"
        client = _mock_client(response_text=texto_con_fences)
        mock_anthropic_cls.return_value = client

        resultado = tj.generar_pulse_v1(self.activities, self.weekly, self.meta, self.profile, self.acwr_info)

        self.assertIsNotNone(resultado)
        self.assertEqual(resultado["headline"], "Semana consistente")
        self.assertEqual(resultado["score"], 80)

    # 3. Inyección de projection (nunca la genera el LLM)
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "fake-key"})
    @patch("anthropic.Anthropic")
    def test_projection_es_none_sin_meta_de_carrera(self, mock_anthropic_cls):
        mock_anthropic_cls.return_value = _mock_client()
        resultado = tj.generar_pulse_v1(self.activities, self.weekly, self.meta, self.profile, self.acwr_info)
        self.assertIn("projection", resultado)
        self.assertIsNone(resultado["projection"])

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "fake-key"})
    @patch("anthropic.Anthropic")
    def test_projection_se_inyecta_cuando_hay_referencia_valida(self, mock_anthropic_cls):
        mock_anthropic_cls.return_value = _mock_client()

        # Un fondo de 20km hace de referencia (grupo 21K) para proyectar una
        # meta de maratón (42.195km) — mismo criterio que
        # proyectar_tiempo_carrera() documenta.
        referencia = _fake_activity(
            self.domingo_analizado - timedelta(days=30),
            dist_km=20.0, duration_min=110.0, pace_raw=5.5, hr=150,
        )
        activities = self.activities + [referencia]
        weekly = tj.calcular_weekly_multidisciplina(activities)
        meta = dict(self.meta)
        meta["metaCarrera"] = {"nombre": "Maratón Test", "fecha": "2027-06-01", "label": "42K MAR"}

        resultado = tj.generar_pulse_v1(activities, weekly, meta, self.profile, self.acwr_info)

        # Debe coincidir exactamente con lo que calcula el motor determinístico.
        esperado = tj.proyectar_tiempo_carrera(
            self.profile.get("personal_records", []), 42.195, activities, "Maratón Test", self.domingo_analizado,
        )
        self.assertEqual(resultado["projection"], esperado)
        self.assertIsNotNone(resultado["projection"])

    # 4. Comportamiento cuando la API falla
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "fake-key"})
    @patch("anthropic.Anthropic")
    def test_api_error_retorna_none_sin_lanzar(self, mock_anthropic_cls):
        mock_anthropic_cls.return_value = _mock_client(side_effect=RuntimeError("timeout simulado"))
        resultado = tj.generar_pulse_v1(self.activities, self.weekly, self.meta, self.profile, self.acwr_info)
        self.assertIsNone(resultado)

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "fake-key"})
    @patch("anthropic.Anthropic")
    def test_json_invalido_retorna_none_sin_lanzar(self, mock_anthropic_cls):
        mock_anthropic_cls.return_value = _mock_client(response_text="esto no es json")
        resultado = tj.generar_pulse_v1(self.activities, self.weekly, self.meta, self.profile, self.acwr_info)
        self.assertIsNone(resultado)

    def test_sin_api_key_retorna_none(self):
        with patch.dict("os.environ", {}, clear=True):
            resultado = tj.generar_pulse_v1(self.activities, self.weekly, self.meta, self.profile, self.acwr_info)
        self.assertIsNone(resultado)

    # 5. Marca de versión (metadata interna, no rompe el contrato existente)
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "fake-key"})
    @patch("anthropic.Anthropic")
    def test_pulse_version_v1_presente(self, mock_anthropic_cls):
        mock_anthropic_cls.return_value = _mock_client()
        resultado = tj.generar_pulse_v1(self.activities, self.weekly, self.meta, self.profile, self.acwr_info)
        self.assertEqual(resultado["pulse_version"], "v1")

    # 6. generar_pulse() (alias) se comporta idéntico a generar_pulse_v1()
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "fake-key"})
    @patch("anthropic.Anthropic")
    def test_alias_generar_pulse_es_identico_a_v1(self, mock_anthropic_cls):
        mock_anthropic_cls.return_value = _mock_client()
        resultado_alias = tj.generar_pulse(self.activities, self.weekly, self.meta, self.profile, self.acwr_info)

        mock_anthropic_cls.return_value = _mock_client()
        resultado_v1 = tj.generar_pulse_v1(self.activities, self.weekly, self.meta, self.profile, self.acwr_info)

        self.assertIsNot(tj.generar_pulse, tj.generar_pulse_v1)
        self.assertEqual(resultado_alias, resultado_v1)


class TransformarIntegracionTestCase(unittest.TestCase):
    """transformar() de punta a punta: activities crudas -> JSON final con pulse."""

    def setUp(self):
        lunes_analizado, domingo_analizado = tj.ultima_semana_completa()
        self.input_data = {
            "profile": {
                "user_id": "u_test",
                "full_name": "Alejandro Test",
                "gender": "M",
                "age": 30,
                "country": "COL",
                "personal_records": [],
            },
            "activities": [
                _fake_raw_activity(domingo_analizado, name="Salida cerrada"),
                _fake_raw_activity(lunes_analizado + timedelta(days=7), name="NoDebeAparecerEnElPrompt"),
            ],
        }

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "fake-key"})
    @patch("anthropic.Anthropic")
    def test_pulse_se_incorpora_al_json_final_con_con_pulse(self, mock_anthropic_cls):
        mock_anthropic_cls.return_value = _mock_client()
        resultado = tj.transformar(self.input_data, con_pulse=True)

        self.assertIn("pulse", resultado)
        self.assertIsNotNone(resultado["pulse"])
        self.assertEqual(resultado["pulse"]["pulse_version"], "v1")
        self.assertIn("projection", resultado["pulse"])
        # El resto del contrato del JSON final no debe verse afectado.
        for clave in ("meta", "activities", "weekly", "acwr", "semanaAnalizada", "taper", "retos"):
            self.assertIn(clave, resultado)

    def test_pulse_es_none_sin_con_pulse(self):
        resultado = tj.transformar(self.input_data, con_pulse=False)
        self.assertIsNone(resultado["pulse"])

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "fake-key"})
    @patch("anthropic.Anthropic")
    def test_transformar_no_lanza_si_pulse_falla(self, mock_anthropic_cls):
        mock_anthropic_cls.return_value = _mock_client(side_effect=RuntimeError("boom"))
        resultado = tj.transformar(self.input_data, con_pulse=True)
        self.assertIsNone(resultado["pulse"])
        # El resto del JSON se sigue generando aunque Pulse falle.
        self.assertTrue(len(resultado["activities"]) > 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
