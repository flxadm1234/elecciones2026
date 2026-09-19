# -*- coding: utf-8 -*-
"""Django template context processors para UI global.

Funciones disponibles en templates via:
    TEMPLATES[]["OPTIONS"]["context_processors"] = ["core.context_processors.app_version"]

APP_VERSION: string fijo (YYYYMMDD-NNN) para cache-busting manual. Cuando
librería de diseño (app.css/app.js) cambie, incrementar el counter final
forzando invalidación de cache HTTP en browser + nginx + whitenoise.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

_APP_BUILD = "20260918-011"   # YYYYMMDD-NNN; incrementar manual. Build 011: refinamiento integral UI/UX. Contraste AA sidebar/footer y badges warning, sistema visual base en app.css (spacing 8px, radios, sombras, transiciones), icon sprite extendido, review_split fit-to-view y filas de votos responsive. Build 010: FIX sidebar visible desktop y badge_counts.pending.

def _file_hash(p: Path) -> str:
    """Devuelve 10 primeros hexdigitos SHA256 de un archivo (si existe)."""
    try:
        if p.is_file():
            return hashlib.sha256(p.read_bytes()).hexdigest()[:10]
    except Exception:
        pass
    return "0000000000"


def app_version(request):
    """Injecta APP_VERSION / APP_VERSION_HASH / APP_BUILD en context.

    - APP_VERSION:      semántico humano (YYYYMMDD-NNN). Recomendado para ?v= query.
    - APP_VERSION_HASH: composición BUILD + hash(app.css+app.js) 20 chars. Para
                        invalidación automática al cambiar contenido.
    """
    base = Path(__file__).resolve().parent
    css = base / "static" / "app.css"
    js  = base / "static" / "app.js"
    combined = f"{_APP_BUILD}|{_file_hash(css)}|{_file_hash(js)}"
    h = hashlib.sha1(combined.encode("utf-8")).hexdigest()[:12]
    return {
        "APP_VERSION": _APP_BUILD,
        "APP_VERSION_HASH": h,
        "APP_BUILD": _APP_BUILD,
    }
