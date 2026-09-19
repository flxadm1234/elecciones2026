from __future__ import annotations

import json
import time
import base64
import logging
from dataclasses import dataclass, field
from typing import Optional

import requests

from django.conf import settings


logger = logging.getLogger("core.ai")


@dataclass
class AIResponse:
    provider: str
    model: str
    raw_json: dict = field(default_factory=dict)
    structured: dict = field(default_factory=dict)
    confidence: float = 0.0
    success: bool = False
    error: str = ""
    latency_ms: int = 0
    ocr_text: str = ""


class BaseAIProvider:
    def __init__(self, settings_obj):
        self.settings_obj = settings_obj
        self.settings = settings_obj
        self.provider = settings_obj.provider
        self.model = settings_obj.get_normalized_model()
        self.api_key = settings_obj.api_key
        self.temperature = settings_obj.temperature
        self.timeout_secs = settings_obj.timeout_secs
        self.max_retries = settings_obj.max_retries

    def ping(self) -> tuple[bool, str]:
        raise NotImplementedError

    def process_acta(self, image_path: str, ocr_context: str | None,
                     acta_type: str, geo_context: dict) -> AIResponse:
        raise NotImplementedError


class GeminiProvider(BaseAIProvider):
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    @property
    def _api_model(self) -> str:
        try:
            return self.settings_obj.get_normalized_model()
        except Exception:
            return "gemini-3.6-flash"

    def ping(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "API key no configurada"
        url = f"{self.BASE_URL}/models?key={self.api_key}"
        try:
            r = requests.get(url, timeout=self.timeout_secs)
            if r.status_code == 200:
                return True, "OK: conexión Gemini exitosa"
            return False, f"HTTP {r.status_code} {r.text[:200]}"
        except Exception as e:
            return False, f"Error: {e}"

    def _encode_image(self, path: str) -> Optional[str]:
        try:
            with open(path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.error("Error codificando imagen %s: %s", path, e)
            return None

    def _build_prompt(self, acta_type: str, ocr_context: str, geo_context: dict,
                       layout_flags: dict | None = None) -> str:
        geo_str = json.dumps(geo_context, ensure_ascii=False) if geo_context else "{}"
        ocr_block = f"\n\nContexto OCR preliminar:\n{ocr_context}\n" if ocr_context else ""
        layout = layout_flags or {}
        layout_str = json.dumps(layout, ensure_ascii=False)
        col_count_iquitos = layout.get("single_column_iquitos") is True
        if acta_type == "REGIONAL":
            layout_hint = """
Acta REGIONAL: 2 columnas.
Columna IZQUIERDA (1): TOTAL VOTOS GOBERNADOR Y VICEGOBERNADOR)
Columna DERECHA (2): TOTAL VOTOS CONSEJERO REGIONAL
""".strip()
        elif acta_type == "PROVINCIAL_DISTRITAL":
            if col_count_iquitos:
                layout_hint = """
Acta PROVINCIAL_DISTRITAL: CASO ESPECIAL IQUITOS: SOLO HAY 1 COLUMNA.
Unica columna: TOTAL VOTOS MUNICIPAL PROVINCIAL (NO HAY COLUMNA MUNICIPAL DISTRITAL).
En vote_entries coloca 0 para muni_distrital EN TODAS LAS FILAS.
""".strip()
            else:
                layout_hint = """
Acta PROVINCIAL_DISTRITAL: 2 columnas.
Columna IZQUIERDA (1): TOTAL VOTOS MUNICIPAL PROVINCIAL
Columna DERECHA (2): TOTAL VOTOS MUNICIPAL DISTRITAL
""".strip()
        else:
            layout_hint = "Acta tipo DISTRITAL: 1 columna: TOTAL VOTOS MUNICIPAL DISTRITAL."

        prompt = f"""Eres un experto auditor electoral especializado en actas de escrutinio peruanas.
Tu tarea es extraer y estructurar la información del acta de tipo: {acta_type}.

Contexto geográfico: {geo_str}
Flags layout: {layout_str}
{layout_hint}
{ocr_block}

REGLAS OBLIGATORIAS:
1. Devuelve SOLO JSON válido sin markdown, sin explicaciones.
2. ESTRUCTURA REQUERIDA:
{{
  "acta_metadata": {{
     "unique_id": "codigo o referencia del acta",
     "table_number": "numero de mesa",
     "acta_type": "{acta_type}",
     "ubigeo": "codigo de 6 digitos o vacio",
     "department": "",
     "province": "",
     "district": ""
  }},
  "vote_entries": [
    {{
       "entry_category": "organizacion|blanco|nulo|impugnado|total",
       "organization_code": "codigo de la organizacion o vacio",
       "organization_name": "nombre o etiqueta",
       "candidate_name": "nombre de candidato o vacio",
       "position": "cargo o vacio",
       "scope": "regional|provincial|distrital o vacio",
       "sort_order": 0,
       "votos_provincial": 0,
       "votos_distrital": 0,
       "votos_gob_gobernador_vice": 0,
       "votos_consejero_regional": 0,
       "field_confidence": 0.95
    }}
  ],
  "confidence_indicators": {{
    "global_confidence": 0.0,
    "legible_percent": 0,
    "has_corrections": false,
    "has_erasures": false,
    "notes": "observaciones",
    "layout_detected": ""
  }}
}}
3. "blanco", "nulo", "impugnado" y "total" son entries obligatorias.
4. Acta REGIONAL: llenar votos_gob_gobernador_vice (col1) y votos_consejero_regional (col2). Los demas campos de ser 0.
5. Acta PROVINCIAL_DISTRITAL normal: votos_provincial (col1) y votos_distrital (col2). Los demas a 0.
6. Caso IQUITOS PROVINCIAL_DISTRITAL de 1 sola columna: solo llenar votos_provincial; votos_distrital DEBE SER 0 en todas las filas.
7. Si algo no se puede leer, confianza baja y anótalo en confidence_indicators.
"""
        return prompt

    def process_acta(self, image_path: str, ocr_context: str | None,
                 acta_type: str, geo_context: dict,
                 layout_flags: dict | None = None) -> AIResponse:
        t0 = time.time()
        response = AIResponse(
            provider=self.provider,
            model=self.model,
            success=False,
            ocr_text=ocr_context or "",
        )
        if not self.api_key:
            response.error = "Gemini API key no configurada"
            return response
        img_b64 = self._encode_image(image_path)
        if not img_b64:
            response.error = "No se pudo codificar la imagen"
            return response
        prompt = self._build_prompt(acta_type, ocr_context or "", geo_context or {}, layout_flags or {})
        url = f"{self.BASE_URL}/models/{self._api_model}:generateContent?key={self.api_key}"
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {"inline_data": {"mime_type": "image/jpeg", "data": img_b64}},
                    ]
                }
            ],
            "generationConfig": {
                "temperature": self.temperature,
                "responseMimeType": "application/json",
            },
        }
        attempt = 0
        last_err = ""
        while attempt < self.max_retries:
            attempt += 1
            try:
                r = requests.post(url, json=payload, timeout=self.timeout_secs)
                if r.status_code != 200:
                    last_err = f"HTTP {r.status_code} {r.text[:400]}"
                    time.sleep(2 * attempt)
                    continue
                data = r.json()
                raw_text = ""
                for c in data.get("candidates", []):
                    for p in c.get("content", {}).get("parts", []):
                        raw_text += p.get("text", "")
                try:
                    structured = json.loads(raw_text)
                except Exception:
                    start = raw_text.find("{")
                    end = raw_text.rfind("}")
                    if start >= 0 and end > start:
                        structured = json.loads(raw_text[start:end + 1])
                    else:
                        structured = {"_raw": raw_text}
                response.raw_json = data
                response.structured = structured
                indicators = structured.get("confidence_indicators", {}) or {}
                response.confidence = float(indicators.get("global_confidence", 0.0) or 0.0)
                response.success = True
                break
            except Exception as e:
                last_err = str(e)
                logger.exception("Intento %s falló en Gemini", attempt)
                time.sleep(2 * attempt)
        if not response.success:
            response.error = last_err or "Falló la llamada a Gemini"
        response.latency_ms = int((time.time() - t0) * 1000)
        return response


class DeepseekProvider(BaseAIProvider):
    BASE_URL = "https://api.deepseek.com/v1/chat/completions"

    @property
    def _api_model(self) -> str:
        try:
            return self.settings_obj.get_normalized_model()
        except Exception:
            return "deepseek-flash"

    def ping(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "API key no configurada"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {
            "model": self._api_model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 4,
        }
        try:
            r = requests.post(self.BASE_URL, json=payload, headers=headers, timeout=self.timeout_secs)
            if r.status_code == 200:
                return True, "OK: conexión Deepseek exitosa"
            return False, f"HTTP {r.status_code} {r.text[:200]}"
        except Exception as e:
            return False, f"Error: {e}"

    def _encode_image(self, path: str) -> Optional[str]:
        try:
            with open(path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.error("Error codificando imagen %s: %s", path, e)
            return None

    def _build_prompt(self, acta_type: str, ocr_context: str, geo_context: dict,
                       layout_flags: dict | None = None) -> str:
        geo_str = json.dumps(geo_context, ensure_ascii=False) if geo_context else "{}"
        ocr_block = f"\n\nContexto OCR preliminar:\n{ocr_context}\n" if ocr_context else ""
        layout = layout_flags or {}
        layout_str = json.dumps(layout, ensure_ascii=False)
        col_count_iquitos = layout.get("single_column_iquitos") is True
        if acta_type == "REGIONAL":
            layout_hint = """
Acta REGIONAL: 2 columnas.
Columna IZQUIERDA (1): TOTAL VOTOS GOBERNADOR Y VICEGOBERNADOR)
Columna DERECHA (2): TOTAL VOTOS CONSEJERO REGIONAL
""".strip()
        elif acta_type == "PROVINCIAL_DISTRITAL":
            if col_count_iquitos:
                layout_hint = """
Acta PROVINCIAL_DISTRITAL: CASO ESPECIAL IQUITOS: SOLO HAY 1 COLUMNA.
Unica columna: TOTAL VOTOS MUNICIPAL PROVINCIAL (NO HAY COLUMNA MUNICIPAL DISTRITAL).
En vote_entries coloca 0 para muni_distrital EN TODAS LAS FILAS.
""".strip()
            else:
                layout_hint = """
Acta PROVINCIAL_DISTRITAL: 2 columnas.
Columna IZQUIERDA (1): TOTAL VOTOS MUNICIPAL PROVINCIAL
Columna DERECHA (2): TOTAL VOTOS MUNICIPAL DISTRITAL
""".strip()
        else:
            layout_hint = "Acta tipo DISTRITAL: 1 columna: TOTAL VOTOS MUNICIPAL DISTRITAL."

        return f"""Eres auditor electoral experto en actas peruanas. Tipo acta: {acta_type}.
Geo: {geo_str}
Flags layout: {layout_str}
{layout_hint}{ocr_block}

Devuelve SOLO JSON válido:
{{
  "acta_metadata": {{"unique_id": "", "table_number": "", "acta_type": "{acta_type}", "ubigeo": "", "department": "", "province": "", "district": ""}},
  "vote_entries": [
    {{
      "entry_category": "organizacion|blanco|nulo|impugnado|total",
      "organization_code": "",
      "organization_name": "",
      "candidate_name": "",
      "position": "",
      "scope": "",
      "sort_order": 0,
      "votos_provincial": 0,
      "votos_distrital": 0,
      "votos_gob_gobernador_vice": 0,
      "votos_consejero_regional": 0,
      "field_confidence": 0.9
    }}
  ],
  "confidence_indicators": {{ "global_confidence": 0.0, "legible_percent": 0, "has_corrections": false, "notes": "", "layout_detected": "" }}
}}

Reglas: blanco,nulo,impugnado,total SIEMPRE.
REGIONAL: llenar gob + consejero (otros 0).
PROVINCIAL_DISTRITAL normal: llenar provincial + distrital.
PROVINCIAL_DISTRITAL IQUITOS (1 sola columna): SOLO llenar provincial. votos_distrital=0 EN TODAS LAS FILAS.
"""

    def process_acta(self, image_path: str, ocr_context: str | None,
                     acta_type: str, geo_context: dict,
                     layout_flags: dict | None = None) -> AIResponse:
        t0 = time.time()
        response = AIResponse(
            provider=self.provider,
            model=self.model,
            success=False,
            ocr_text=ocr_context or "",
        )
        if not self.api_key:
            response.error = "Deepseek API key no configurada"
            return response
        img_b64 = self._encode_image(image_path)
        prompt = self._build_prompt(acta_type, ocr_context or "", geo_context or {}, layout_flags or {})
        messages = [
            {"role": "system", "content": "Extrae solo JSON estructurado del acta electoral."},
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
            ]},
        ]
        if img_b64:
            messages[1]["content"].append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
            })
        payload = {
            "model": self._api_model,
            "messages": messages,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        attempt = 0
        last_err = ""
        while attempt < self.max_retries:
            attempt += 1
            try:
                r = requests.post(self.BASE_URL, json=payload, headers=headers, timeout=self.timeout_secs)
                if r.status_code != 200:
                    last_err = f"HTTP {r.status_code} {r.text[:400]}"
                    time.sleep(2 * attempt)
                    continue
                data = r.json()
                raw_text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                try:
                    structured = json.loads(raw_text)
                except Exception:
                    start = raw_text.find("{")
                    end = raw_text.rfind("}")
                    structured = json.loads(raw_text[start:end + 1]) if (start >= 0 and end > start) else {"_raw": raw_text}
                response.raw_json = data
                response.structured = structured
                response.confidence = float(
                    (structured.get("confidence_indicators") or {}).get("global_confidence", 0.0) or 0.0
                )
                response.success = True
                break
            except Exception as e:
                last_err = str(e)
                logger.exception("Intento %s falló Deepseek", attempt)
                time.sleep(2 * attempt)
        if not response.success:
            response.error = last_err or "Falló llamada a Deepseek"
        response.latency_ms = int((time.time() - t0) * 1000)
        return response


def get_default_provider_settings():
    from .models import AIProviderSettings
    try:
        s = AIProviderSettings.objects.filter(is_default=True, is_active=True).first()
        if s and s.api_key:
            return s
    except Exception:
        pass
    try:
        for s in AIProviderSettings.objects.filter(is_active=True).order_by("-is_default", "id"):
            if s.api_key:
                return s
    except Exception:
        pass
    default_provider = getattr(settings, "DEFAULT_AI_PROVIDER", "gemini")
    api_key = getattr(settings, f"{default_provider.upper()}_API_KEY", "") or ""
    model = getattr(settings, f"{default_provider.upper()}_MODEL", "") or (
        "gemini-3.6-flash" if default_provider == "gemini" else "deepseek-flash"
    )
    s = AIProviderSettings(
        provider=default_provider,
        model=model,
        is_active=bool(api_key),
        is_default=True,
    )
    s.api_key = api_key
    return s


def build_provider(settings_obj):
    if settings_obj.provider == "gemini":
        return GeminiProvider(settings_obj)
    if settings_obj.provider == "deepseek":
        return DeepseekProvider(settings_obj)
    return None


get_provider_for_settings = build_provider
