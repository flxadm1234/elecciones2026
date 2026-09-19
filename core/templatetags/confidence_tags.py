"""Template tags para niveles de confianza visual (umbrales canónicos).

Uso en templates:
    {% load confidence_tags %}

    {{ a.confidence_score|conf_level }}        -> "high" | "medium" | "low"
    {{ a.confidence_score|conf_css_fill }}     -> "high" / "medium" / "low" (clase bar fill)
    {{ a.confidence_score|conf_label_es }}     -> "ALTA" / "MEDIA" / "BAJA"
    {{ a.confidence_score|conf_pct }}          -> "92%"
    {{ a.confidence_score|conf_badge }}        -> badge class (badge.ok / obs / err)
"""
from __future__ import annotations

import unicodedata

from django import template
from django.db import models as dj_models
from django.utils.html import escape, format_html, mark_safe

from ..signals import THRESHOLD_HIGH, THRESHOLD_MEDIUM, confidence_level


register = template.Library()


def _ascii_norm(text: str) -> str:
    """Normaliza Unicode a ASCII para comparaciones difusas (acentos / Ñ)."""
    if not text:
        return ""
    if not isinstance(text, str):
        text = str(text)
    nfkd = unicodedata.normalize("NFKD", text)
    return nfkd.encode("ascii", "ignore").decode("ascii").strip().casefold()


@register.filter(name="conf_level")
def conf_level_filter(score) -> str:
    """Devuelve nivel textual canónico high/medium/low."""
    try:
        if score is None:
            return "low"
        s = float(score)
    except (TypeError, ValueError):
        return "low"
    return confidence_level(s)


@register.filter(name="conf_css_fill")
def conf_css_fill(score) -> str:
    """Devuelve la clase CSS para el fill de la barra de confianza."""
    return conf_level_filter(score)


@register.filter(name="conf_label_es")
def conf_label_es(score) -> str:
    """Etiqueta en español: ALTA / MEDIA / BAJA."""
    lvl = conf_level_filter(score)
    return {"high": "ALTA", "medium": "MEDIA", "low": "BAJA"}.get(lvl, "BAJA")


@register.filter(name="conf_pct")
def conf_pct(score) -> str:
    """Formato porcentual legible (92%). None -> N/D."""
    try:
        if score is None or score == "":
            return "N/D"
        s = float(score)
    except (TypeError, ValueError):
        return "N/D"
    if 0.0 <= s <= 1.0:
        s = s * 100.0
    return f"{round(s)}%"


@register.filter(name="conf_badge_class")
def conf_badge_class(score) -> str:
    """Devuelve clase badge según nivel (ok / obs / err estilos sistema diseño)."""
    lvl = conf_level_filter(score)
    return {"high": "ok", "medium": "obs", "low": "err"}.get(lvl, "err")


@register.simple_tag(name="confidence_bar_html")
def confidence_bar_html(score, show_pct: bool = True, min_width_px: int = 120) -> str:
    """Renderiza HTML inline de una barra visual de confianza.

    Evita repetir markup en múltiples templates.
    """
    try:
        if score is None or score == "":
            num_val = 0.0
        else:
            num_val = float(score)
    except (TypeError, ValueError):
        num_val = 0.0

    if 0.0 <= num_val <= 1.0:
        pct_val = num_val * 100.0
    else:
        pct_val = max(0.0, min(100.0, num_val))

    lvl = confidence_level(num_val if 0.0 <= num_val <= 1.0 else (num_val / 100.0))
    style_width = f"width:{pct_val:.1f}%;"
    pct_text = f"{round(pct_val)}%"

    html = (
        '<span class="confidence-row" style="min-width:0;flex:1">'
        f'<span class="conf-bar-track" style="min-width:{min_width_px}px">'
        f'<span class="conf-bar-fill {lvl}" style="{style_width}"></span>'
        '</span>'
    )
    if show_pct:
        html += f'<span class="conf-pct" data-level="{lvl}">{pct_text}</span>'
    html += "</span>"
    return format_html(html)


@register.filter(name="dict_get")
def dict_get(dictionary, key):
    """Accede a un valor de diccionario usando una clave dinámica.

    Django templates no soportan {{ dict[key] }} para keys dinámicas.
    Uso: {{ ambito_labels|dict_get:amb }}
    """
    try:
        if not isinstance(dictionary, dict):
            return key if key is not None else ""
        val = dictionary.get(key)
        if val is None or val == "":
            return key if key is not None else ""
        return val
    except Exception:
        return key if key is not None else ""


@register.filter(name="dict_len")
def dict_len(dictionary, key):
    """Devuelve longitud de la lista almacenada en diccionario[key].

    Uso: {{ votes_by_ambito|dict_len:amb }}
    """
    try:
        if not isinstance(dictionary, dict):
            return 0
        val = dictionary.get(key)
        if val is None:
            return 0
        return len(val)
    except Exception:
        return 0


@register.filter(name="org_logo_url")
def org_logo_url(short_name):
    """Resuelve URL del logo personalizado de una organización política.

    Busca por short_name/full_name/code (case-insensitive + normalize acentos).
    Retorna URL string o cadena vacía si no encuentra.
    """
    if not short_name:
        return ""
    needle = str(short_name).strip()
    if not needle:
        return ""
    needle_ascii = _ascii_norm(needle)
    try:
        from ..models import PoliticalOrganization as PO

        qs = PO.objects.filter(is_active=True)
        direct = qs.filter(
            dj_models.Q(short_name__iexact=needle)
            | dj_models.Q(full_name__iexact=needle)
            | dj_models.Q(code__iexact=needle)
        ).first()
        if direct:
            return direct.resolved_logo_url or ""
        for org in qs.iterator():
            if (
                _ascii_norm(org.short_name) == needle_ascii
                or _ascii_norm(org.full_name) == needle_ascii
                or _ascii_norm(org.code) == needle_ascii
            ):
                return org.resolved_logo_url or ""
            if (
                org.short_name
                and needle_ascii
                and (needle_ascii in _ascii_norm(org.short_name) or _ascii_norm(org.short_name) in needle_ascii)
            ):
                return org.resolved_logo_url or ""
    except Exception:
        return ""
    return ""


@register.simple_tag(name="org_logo_or_icon")
def org_logo_or_icon(short_name):
    """Renderiza logo <img class=org-logo> si está definido.

    Fallback automático al chip SVG icon-sm de edificio (ORG) 14px sin desalineación.
    Retorna HTML seguro (mark_safe).
    """
    url = org_logo_url(short_name)
    safe_label = escape(str(short_name or "Organización"))
    if url:
        return mark_safe(
            f'<img src="{escape(url)}" class="org-logo" alt="{safe_label}" '
            f'loading="lazy" decoding="async" width="28" height="28">'
        )
    return mark_safe(
        '<span class="cat-chip org" style="flex-shrink:0">'
        '<svg class="icon icon-sm" width="14" height="14" style="width:14px;height:14px;max-width:none">'
        '<use href="#i-building-2"/></svg> ORG</span>'
    )
