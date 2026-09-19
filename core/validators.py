from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from django.db import models as djmodels

from .models import (
    Acta,
    ActaVoteEntry,
    PoliticalOrganization,
)

logger = logging.getLogger("core.validators")


@dataclass
class ValidationResult:
    passed: bool = False
    validation_status: str = "pendiente"
    reasons: list[str] = field(default_factory=list)
    arithmetic: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    review_reasons: list[str] = field(default_factory=list)
    review_codes: list[str] = field(default_factory=list)
    organization_issues: list[str] = field(default_factory=list)


CATEGORY_VOTABLE = {
    "organizacion", "gobernador", "consejero", "provincial", "distrital",
}


class ActaValidator:
    def __init__(self, acta: Acta):
        self.acta = acta

    def _summarize_ambito(self, entries, ambito: str) -> dict:
        amb_entries = list(entries.filter(ambito=ambito))
        org_entries = [e for e in amb_entries if e.entry_category in CATEGORY_VOTABLE]
        blanco = (
            entries.filter(ambito=ambito, entry_category="blanco").aggregate(v=djmodels.Sum("votes"))["v"] or 0
        )
        nulo = (
            entries.filter(ambito=ambito, entry_category="nulo").aggregate(v=djmodels.Sum("votes"))["v"] or 0
        )
        impugnado = (
            entries.filter(ambito=ambito, entry_category="impugnado").aggregate(v=djmodels.Sum("votes"))["v"] or 0
        )
        total_row = entries.filter(ambito=ambito, entry_category="total").aggregate(v=djmodels.Sum("votes"))["v"] or 0
        sum_votables = sum(e.votes for e in org_entries)
        sum_total = sum_votables + blanco + nulo + impugnado
        return {
            "ambito": ambito,
            "has_rows": len(amb_entries) > 0,
            "sum_votables": sum_votables,
            "blanco": blanco,
            "nulo": nulo,
            "impugnado": impugnado,
            "sum_total_calculado": sum_total,
            "total_consignado": total_row,
            "diff_total": abs(total_row - sum_total) if total_row else None,
        }

    def validate(self, threshold_confidence: float = 0.85) -> ValidationResult:
        result = ValidationResult()
        entries = ActaVoteEntry.objects.filter(acta=self.acta)
        if not entries.exists():
            result.reasons.append("Sin registros de votos")
            result.review_reasons.append("No hay registros de votos para validar")
            result.review_codes.append("V_EMPTY")
            result.validation_status = "suma_mal"
            return result

        # Determinar qué ambitos son requeridos según acta_type + iquitos
        acta_type_norm = (self.acta.acta_type or "").upper()
        flags = self.acta.layout_flags or {}
        iquitos_single = bool(flags.get("single_column_iquitos"))
        ambitos_requeridos: list[str] = []
        if acta_type_norm == "REGIONAL":
            ambitos_requeridos = ["gob_gobernador_vice", "consejero_regional"]
        elif acta_type_norm == "PROVINCIAL_DISTRITAL":
            ambitos_requeridos = ["muni_provincial"]
            if not iquitos_single:
                ambitos_requeridos.append("muni_distrital")
        elif acta_type_norm == "DISTRITAL":
            ambitos_requeridos = ["muni_distrital"]
        else:
            ambitos_requeridos = sorted({e.ambito for e in entries if e.ambito})

        arithmetic: dict = {"ambitos": {}, "global": {}}
        any_arith_failed = False
        for amb in ambitos_requeridos:
            summa = self._summarize_ambito(entries, amb)
            arithmetic["ambitos"][amb] = summa
            if not summa["has_rows"]:
                result.review_reasons.append(f"Sin filas en ámbito {amb}")
                result.review_codes.append("V_EMPTY_" + amb.upper())
                any_arith_failed = True
                continue
            if summa["total_consignado"] and (summa["diff_total"] or 0) > 2:
                any_arith_failed = True
                result.reasons.append(
                    f"{amb}: suma calc {summa['sum_total_calculado']} vs total consignado {summa['total_consignado']} (dif {summa['diff_total']})"
                )
                result.review_reasons.append(f"Aritmética no coincide en ámbito {amb}")
                result.review_codes.append("V_ARITH_" + amb.upper())

        global_org = sum(e.votes for e in entries if e.entry_category in CATEGORY_VOTABLE)
        global_blanco = entries.filter(entry_category="blanco").aggregate(v=djmodels.Sum("votes"))["v"] or 0
        global_nulo = entries.filter(entry_category="nulo").aggregate(v=djmodels.Sum("votes"))["v"] or 0
        global_impugnado = entries.filter(entry_category="impugnado").aggregate(v=djmodels.Sum("votes"))["v"] or 0
        global_total_row = entries.filter(entry_category="total").aggregate(v=djmodels.Sum("votes"))["v"] or 0
        arithmetic["global"] = {
            "sum_votables": global_org,
            "blanco": global_blanco,
            "nulo": global_nulo,
            "impugnado": global_impugnado,
            "sum_total_calculado": global_org + global_blanco + global_nulo + global_impugnado,
            "total_consignado": global_total_row,
        }
        result.arithmetic = arithmetic
        if any_arith_failed:
            result.validation_status = "suma_mal"

        # Catalog checks: solo organizaciones por ámbito.
        repeated_per_ambito: dict[str, set] = {}
        for e in entries.filter(entry_category__in=CATEGORY_VOTABLE).exclude(organization_id__isnull=True):
            key = (e.ambito, e.organization_id)
            if e.organization_id:
                bucket = repeated_per_ambito.setdefault(e.ambito or "all", set())
                if key[1] in bucket:
                    result.review_codes.append("V_DUP_ORG_" + (e.ambito or "ALL").upper())
                    result.review_reasons.append(f"Organización repetida en ámbito {e.ambito or 'n/a'}")
                bucket.add(key[1])

        active_ids = set(
            PoliticalOrganization.objects.filter(is_active=True).values_list("id", flat=True)
        )
        for e in entries.filter(entry_category__in=CATEGORY_VOTABLE):
            if e.organization_id and e.organization_id not in active_ids:
                msg = f"Organización {e.organization} no activa en {e.ambito}"
                result.organization_issues.append(msg)
                result.warnings.append(msg)

        conf_values = [e.field_confidence for e in entries if e.field_confidence > 0]
        avg_conf = sum(conf_values) / len(conf_values) if conf_values else 0.0
        result.arithmetic["avg_field_confidence"] = avg_conf
        if avg_conf < threshold_confidence:
            result.review_reasons.append(f"Confianza promedio baja: {avg_conf:.2f} < {threshold_confidence}")
            result.review_codes.append("V_LOW_CONF")
            result.validation_status = result.validation_status if result.validation_status not in {"", "pendiente"} else "baja_confianza"

        if self.acta.table_number and not self.acta.table_number.strip():
            result.review_reasons.append("Número de mesa vacío")
            result.review_codes.append("V_NO_TABLE")
        if not (self.acta.district or self.acta.province or self.acta.department or self.acta.acta_escrutinio_legacy_id):
            result.review_reasons.append("Ubicación geográfica / acta legada vacía")
            result.review_codes.append("V_NO_GEO")

        result.review_codes = sorted(set(result.review_codes))
        result.review_reasons = list(dict.fromkeys(result.review_reasons))
        result.organization_issues = list(dict.fromkeys(result.organization_issues))
        result.warnings = list(dict.fromkeys(result.warnings))
        result.reasons = list(dict.fromkeys(result.reasons))

        result.passed = (
            len(result.review_codes) == 0
            and result.validation_status not in {"suma_mal", "catalogo_mal"}
        )
        if result.passed:
            result.validation_status = "aprobado"
        elif result.validation_status in {"", "pendiente"}:
            result.validation_status = "baja_confianza"
        return result


def detect_duplicate_acta(acta: Acta) -> Optional[Acta]:
    qs = Acta.objects.filter(
        acta_type=acta.acta_type,
        election_process=acta.election_process,
        table_number=acta.table_number,
    ).exclude(pk=acta.pk)
    if acta.unique_id:
        dup = qs.filter(unique_id=acta.unique_id).first()
        if dup:
            return dup
    if acta.district_id:
        dup = qs.filter(district_id=acta.district_id).first()
        if dup:
            return dup
    return None
