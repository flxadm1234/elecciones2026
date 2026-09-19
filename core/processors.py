from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from django.utils import timezone

from .models import (
    Acta,
    ActaImage,
    ActaTranscription,
    ActaVoteEntry,
    ProcessingJob,
    ProcessingAlert,
    ManualReviewQueue,
    AIProviderSettings,
    ElectionProcess,
    GeoDistrict,
    PoliticalOrganization,
)
from .ai import AIResponse, build_provider, get_default_provider_settings
from .pipeline_images import ImagePreprocessor, OCREngine
from .validators import ActaValidator, detect_duplicate_acta, ValidationResult

logger = logging.getLogger("core.processors")


def _emit_debug_event(payload: dict) -> None:
    _p = ".dbg/pipeline-monitor-module.env"
    _u = "http://127.0.0.1:7777/event"
    _s = "pipeline-monitor-module"
    try:
        with open(_p, encoding="utf-8") as f:
            c = f.read()
        _u = next((l.split("=", 1)[1] for l in c.splitlines() if l.startswith("DEBUG_SERVER_URL=")), _u)
        _s = next((l.split("=", 1)[1] for l in c.splitlines() if l.startswith("DEBUG_SESSION_ID=")), _s)
    except Exception:
        pass

    data = {"sessionId": _s, **payload}
    data["runId"] = "post-fix"
    try:
        import json
        import urllib.request

        urllib.request.urlopen(
            urllib.request.Request(
                _u,
                data=json.dumps(data).encode(),
                headers={"Content-Type": "application/json"},
            ),
            timeout=0.35,
        ).read()
    except Exception:
        pass


@dataclass
class ProcessResult:
    ok: bool = False
    acta_id: int | None = None
    transcription_id: int | None = None
    review_item_id: int | None = None
    status: str = ""
    validation: Optional[ValidationResult] = None
    messages: list[str] = field(default_factory=list)
    error: str = ""


class ActaProcessor:
    def __init__(self, job: ProcessingJob | None = None):
        self.job = job
        self.preprocessor = ImagePreprocessor()
        self.ocr = OCREngine()

    def register_image(self, file_path: str, uploaded_by=None) -> ActaImage:
        if not os.path.exists(file_path):
            raise FileNotFoundError(file_path)
        file_hash = ActaImage.compute_hash(file_path)
        stats = os.stat(file_path)
        obj, created = ActaImage.objects.update_or_create(
            file_hash=file_hash,
            defaults={
                "file_path": file_path,
                "file_size": stats.st_size,
                "availability": "available",
                "uploaded_by": uploaded_by,
            },
        )
        return obj

    def create_job_for_image(self, image: ActaImage, job_type: str = "process_acta",
                             params: dict | None = None) -> ProcessingJob:
        return ProcessingJob.objects.create(
            job_type=job_type,
            acta_image=image,
            parameters=params or {},
        )

    def _resolve_election_process(self, meta: dict) -> Optional[ElectionProcess]:
        process_code = meta.get("process_code") or ""
        if process_code:
            qs = ElectionProcess.objects.filter(code=process_code)
            if qs.exists():
                return qs.first()
        return ElectionProcess.objects.filter(is_active=True).order_by("-process_date").first()

    def _resolve_district(self, ubigeo: str, department: str, province: str, district: str):
        if ubigeo and len(ubigeo) == 6:
            d = GeoDistrict.objects.filter(ubigeo=ubigeo).first()
            if d:
                return d.department, d.province, d
        d = GeoDistrict.objects.filter(name__iexact=district or "").first()
        if d:
            return d.department, d.province, d
        return None, None, None

    def _classify_acta_type(self, template_name: str, structured: dict, default: str) -> str:
        t = (structured.get("acta_metadata") or {}).get("acta_type") or ""
        if t in {"regional", "provincial_distrital"}:
            return t
        if template_name == "regional":
            return "regional"
        return default or "provincial_distrital"

    def _find_org(self, code: str, name: str, create_missing: bool = False) -> Optional[PoliticalOrganization]:
        if code:
            qs = PoliticalOrganization.objects.filter(code__iexact=code)
            if qs.exists():
                return qs.first()
        if name:
            qs = PoliticalOrganization.objects.filter(short_name__iexact=name.strip())
            if qs.exists():
                return qs.first()
            qs = PoliticalOrganization.objects.filter(full_name__icontains=name.strip())
            if qs.exists():
                return qs.first()
        if create_missing and name:
            short = (name or "").strip()[:64] or code
            try:
                return PoliticalOrganization.objects.create(
                    code=code or f"AUTO_{hashlib.sha256(short.encode()).hexdigest()[:8]}",
                    short_name=short,
                    full_name=(name or "").strip()[:240],
                    scope="mixto",
                    is_active=True,
                )
            except Exception as exc:
                logger.warning("No se pudo crear org %s: %s", name, exc)
        return None

    def _expand_entries_by_ambito(self, acta: Acta, raw_entry: dict, sort_order: int) -> list[ActaVoteEntry]:
        """Expande una fila del JSON (4 campos votos) en 1 fila por ámbito."""
        cat = raw_entry.get("entry_category") or "organizacion"
        org = self._find_org(
            raw_entry.get("organization_code") or "",
            raw_entry.get("organization_name") or "",
            create_missing=cat == "organizacion",
        )
        prov = max(0, int(raw_entry.get("votos_provincial") or 0))
        dist = max(0, int(raw_entry.get("votos_distrital") or 0))
        gob = max(0, int(raw_entry.get("votos_gob_gobernador_vice") or 0))
        consej = max(0, int(raw_entry.get("votos_consejero_regional") or 0))
        conf = float(raw_entry.get("field_confidence") or 0.0)
        scope_base = (raw_entry.get("scope") or "")[:32]
        cand = (raw_entry.get("candidate_name") or "")[:180]
        pos = (raw_entry.get("position") or "")[:64]

        row_src_base = f"{acta.id}|{cat}|{raw_entry.get('organization_code')}|{raw_entry.get('organization_name')}|{cand}"
        def mk(ambito: str, votes: int) -> ActaVoteEntry:
            rh = hashlib.sha256(f"{row_src_base}|{ambito}|{votes}|{sort_order}".encode("utf-8")).hexdigest()
            base_votes = votes
            return ActaVoteEntry(
                acta=acta,
                entry_category=cat,
                organization=org,
                candidate_name=cand,
                position=pos,
                scope=scope_base,
                sort_order=sort_order,
                ambito=ambito,
                votes=base_votes,
                votos_provincial=prov,
                votos_distrital=dist,
                votos_gob_gobernador_vice=gob,
                votos_consejero_regional=consej,
                field_confidence=conf,
                row_hash=rh,
                data_origin="ia",
            )
        rows = []
        flags = acta.layout_flags or {}
        iquitos_single = bool(flags.get("single_column_iquitos"))
        acta_type_norm = (acta.acta_type or "").upper()
        if acta_type_norm == "REGIONAL":
            rows.append(mk("gob_gobernador_vice", gob))
            rows.append(mk("consejero_regional", consej))
        elif acta_type_norm == "PROVINCIAL_DISTRITAL":
            rows.append(mk("muni_provincial", prov))
            if not iquitos_single:
                rows.append(mk("muni_distrital", dist))
        elif acta_type_norm == "DISTRITAL":
            rows.append(mk("muni_distrital", dist))
        else:
            if prov:
                rows.append(mk("muni_provincial", prov))
            if dist:
                rows.append(mk("muni_distrital", dist))
            if gob:
                rows.append(mk("gob_gobernador_vice", gob))
            if consej:
                rows.append(mk("consejero_regional", consej))
        return rows

    def _persist_entries(self, acta: Acta, transcription: ActaTranscription,
                         vote_entries: list[dict]) -> dict:
        stats = {"created": 0, "updated": 0, "skipped": 0, "ambitos": set()}
        order = 0
        ActaVoteEntry.objects.filter(acta=acta, transcription=transcription).delete()
        all_rows: list[ActaVoteEntry] = []
        for raw in vote_entries:
            order += 1
            cat = raw.get("entry_category") or "organizacion"
            if cat not in {c[0] for c in ActaVoteEntry._meta.get_field("entry_category").choices}:
                if cat in {"blank"}:
                    cat = "blanco"
                elif cat in {"null"}:
                    cat = "nulo"
                elif cat in {"disputed"}:
                    cat = "impugnado"
                else:
                    cat = "organizacion"
                raw = {**raw, "entry_category": cat}
            sort_order = int(raw.get("sort_order") or order)
            rows = self._expand_entries_by_ambito(acta, raw, sort_order)
            for r in rows:
                r.transcription = transcription
                stats["ambitos"].add(r.ambito)
                all_rows.append(r)
        if all_rows:
            ActaVoteEntry.objects.bulk_create(all_rows, batch_size=200)
            stats["created"] = len(all_rows)
        stats["ambitos"] = sorted(stats["ambitos"])
        return stats

    def process_legacy_acta_escrutinio(self, legacy_acta_id: int,
                                       override_acta_type: str | None = None) -> ProcessResult:
        from .models_legacy import ActaEscrutinioLegacy, MesaLegacy

        result = ProcessResult()
        job = self.job
        try:
            legacy = ActaEscrutinioLegacy.objects.prefetch_related(None).get(id=legacy_acta_id)
        except ActaEscrutinioLegacy.DoesNotExist:
            result.error = f"actas_escrutinio id={legacy_acta_id} no existe"
            if job:
                job.mark_failed(result.error)
            return result

        image_path = legacy.file_path_disco or ""
        if not image_path or not os.path.exists(image_path):
            result.error = f"Imagen faltante en disco: {image_path}"
            if job:
                job.mark_failed(result.error)
            return result

        if job:
            job.mark_start("legacy_vps")

        acta_type = override_acta_type or (legacy.tipo_acta or "PROVINCIAL_DISTRITAL").lower().replace("-", "_")
        if acta_type not in {"regional", "provincial_distrital", "distrital"}:
            acta_type = "provincial_distrital"
        mesa = legacy.get_mesa()
        iquitos_special = bool(mesa and MesaLegacy.normalize_distrito(mesa.distrito) == "IQUITOS"
                               and (legacy.tipo_acta or "").upper() == "PROVINCIAL_DISTRITAL")
        geo = {
            "ubigeo": (mesa.codi_ubigeo if mesa else ""),
            "department": (mesa.departamento if mesa else ""),
            "province": (mesa.provincia if mesa else ""),
            "district": (mesa.distrito if mesa else ""),
            "mesa_numero": legacy.mesa_numero,
        }
        layout_flags = {
            "single_column_iquitos": iquitos_special,
            "distrito_norm": MesaLegacy.normalize_distrito(mesa.distrito) if mesa else "",
        }

        try:
            prep = self.preprocessor.preprocess(image_path)
            for w in prep.warnings:
                result.messages.append(f"[prep] {w}")
        except Exception as e:
            logger.exception("Preprocesamiento falló acta %s", legacy_acta_id)
            result.messages.append(f"Fallo preprocesamiento: {e}")
            prep = None

        ocr_global = ""
        ocr_out = {"regions": {}, "numbers": []}
        if prep is not None:
            ocr_out = self.ocr.extract_regions(prep)
            ocr_global = ocr_out.get("global") or ""
        prep_status = "OK" if prep is not None else "PREP_FAILED"

        image = self.register_image(image_path)
        election = ElectionProcess.objects.filter(is_active=True).order_by("-process_date").first()
        if not election:
            from datetime import date
            election = ElectionProcess.objects.create(
                code="ELECCIONES_2026",
                name="Elecciones Regionales y Municipales 2026",
                process_date=date(2026, 10, 4),
                is_active=True,
            )

        unique_id = f"L{legacy_acta_id}_{legacy.mesa_numero or 'NN'}_{acta_type}"
        acta, _ = Acta.objects.update_or_create(
            unique_id=unique_id,
            acta_type=acta_type,
            acta_escrutinio_legacy_id=legacy.id,
            defaults=dict(
                election_process=election,
                table_number=(legacy.mesa_numero or "")[:32],
                source_image=image,
                status="procesando",
                layout_flags=layout_flags,
                notes="",
            ),
        )
        result.acta_id = acta.id
        if job:
            job.acta = acta
            current_params = job.parameters if isinstance(job.parameters, dict) else {}
            job.parameters = {
                **current_params,
                "legacy_id": legacy.id,
                "mesa_numero": (legacy.mesa_numero or "")[:32],
                "layout_flags": layout_flags,
            }
            job.save(update_fields=["acta", "parameters"])

        provider_settings = get_default_provider_settings()
        if not provider_settings.api_key:
            result.messages.append("Sin clave API IA - modo OCR-only, enviado a revisión")

        ai_response: AIResponse | None = None
        structured: dict = {}
        if provider_settings.api_key:
            provider = build_provider(provider_settings)
            if provider is None:
                acta.status = "error"
                acta.save(update_fields=["status"])
                result.error = "No se pudo cargar proveedor IA"
                if job:
                    job.mark_failed(result.error)
                return result
            ai_response = provider.process_acta(
                image_path=prep.preprocessed_path if prep is not None else image_path,
                ocr_context=ocr_global,
                acta_type=acta_type.upper(),
                geo_context=geo,
                layout_flags=layout_flags,
            )
            if not ai_response.success:
                acta.status = "error"
                acta.save(update_fields=["status"])
                ProcessingAlert.objects.create(
                    acta=acta, job=job, severity="critical",
                    alert_type="AI_ERROR", message=ai_response.error or "Error IA sin detalle",
                    detail={"legacy_id": legacy.id},
                )
                result.error = ai_response.error or "Fallo IA"
                if job:
                    job.mark_failed(result.error)
                return result
            structured = ai_response.structured or {}

        conf_indicators = (structured.get("confidence_indicators") or {}) if ai_response else {}
        transcription = ActaTranscription.objects.create(
            acta=acta,
            provider=ai_response.provider if ai_response else "ocr_only",
            model=ai_response.model if ai_response else "",
            raw_json=(ai_response.raw_json if ai_response else {"ocr_only": True}) or {},
            ocr_text=ocr_global[:200000],
            global_confidence=ai_response.confidence if ai_response else 0.0,
            validation_status="pendiente",
            tech_notes=(
                f"Prep: {prep_status} | "
                f"Layout flags: {layout_flags} | "
                f"Mesa: {legacy.mesa_numero} | "
                f"Distrito: {(mesa.distrito if mesa else '')}"
            )[:500],
            processing_job=job,
        )
        result.transcription_id = transcription.id

        vote_entries_list = structured.get("vote_entries") or []
        if not vote_entries_list:
            vote_entries_list = self._synthesize_entries_from_ocr(ocr_out, acta_type)
        stats_entries = self._persist_entries(acta, transcription, vote_entries_list)
        result.messages.append(f"Entradas de votos: {stats_entries}")

        threshold = provider_settings.confidence_threshold if provider_settings else 0.85
        validation = ActaValidator(acta).validate(threshold_confidence=threshold)
        result.validation = validation
        transcription.validation_status = validation.validation_status
        transcription.save(update_fields=["validation_status"])

        acta.status = "procesado_ok" if validation.passed else "observado"
        acta.save(update_fields=["status"])
        # #region debug-point E:processor-validation-result
        _emit_debug_event({
            "runId": "pre-fix",
            "hypothesisId": "E",
            "location": "core/processors.py:process_legacy_acta_escrutinio:validation",
            "msg": "[DEBUG] validation and status resolved",
            "data": {
                "acta_id": acta.id,
                "legacy_id": legacy.id,
                "transcription_id": transcription.id,
                "status": acta.status,
                "validation_status": validation.validation_status,
                "passed": bool(validation.passed),
                "review_codes": validation.review_codes,
                "review_reasons": validation.review_reasons[:4],
            },
        })
        # #endregion

        review_item = None
        if not validation.passed or acta.status == "observado":
            review_item = ManualReviewQueue.objects.create(
                acta=acta,
                transcription=transcription,
                reason="; ".join(validation.review_reasons) or "Baja confianza / validación incompleta",
                reason_codes=validation.review_codes or ["AUTO_OBSERVED"],
            )
            result.review_item_id = review_item.id
            # #region debug-point E:review-queue-created
            _emit_debug_event({
                "runId": "pre-fix",
                "hypothesisId": "E",
                "location": "core/processors.py:process_legacy_acta_escrutinio:review",
                "msg": "[DEBUG] manual review queue item created",
                "data": {
                    "acta_id": acta.id,
                    "legacy_id": legacy.id,
                    "review_item_id": review_item.id,
                    "review_status": review_item.status,
                    "reason_codes": review_item.reason_codes,
                },
            })
            # #endregion
            for code in validation.review_codes or ["LOW_CONF"]:
                ProcessingAlert.objects.get_or_create(
                    acta=acta,
                    job=job,
                    alert_type=code,
                    defaults={
                        "severity": "warning",
                        "message": f"Código {code}",
                        "detail": {"validation": validation.arithmetic, "reasons": validation.review_reasons},
                    },
                )

        result.ok = True
        result.status = acta.status
        summary = {
            "acta_id": acta.id,
            "transcription_id": transcription.id,
            "legacy_id": legacy.id,
            "status": acta.status,
            "validation_status": validation.validation_status,
            "validation": validation.arithmetic,
            "review_codes": validation.review_codes,
            "entries": stats_entries,
        }
        if job:
            job.mark_done(summary)
        return result

    def process_acta_image(self, image: ActaImage,
                           override_acta_type: str | None = None,
                           process_code: str | None = None) -> ProcessResult:
        result = ProcessResult()
        job = self.job
        if not os.path.exists(image.file_path):
            image.availability = "missing"
            image.save(update_fields=["availability"])
            result.error = f"Imagen faltante {image.file_path}"
            if job:
                job.mark_failed(result.error)
            return result

        if job:
            job.mark_start("local")
            if image.availability != "available":
                image.availability = "available"
                image.save(update_fields=["availability"])

        try:
            prep = self.preprocessor.preprocess(image.file_path)
            for w in prep.warnings:
                result.messages.append(f"[prep] {w}")
        except Exception as e:
            logger.exception("Preprocesamiento falló")
            prep_warnings = [f"Fallo preprocesamiento: {e}"]
            result.messages.extend(prep_warnings)

        ocr_out = self.ocr.extract_regions(prep)
        ocr_global = ocr_out.get("global") or ""
        if prep.warnings:
            prep_status = "; ".join(prep.warnings)
        else:
            prep_status = f"OK regiones={len(prep.regions)} rot={prep.rotation_degrees:.1f}"

        provider_settings = get_default_provider_settings()
        if not provider_settings.api_key:
            # modo sin IA: crear acta pendiente a revisión
            result.messages.append("Sin clave API IA - modo OCR-only, enviado a revisión")

        acta_type = self._classify_acta_type(prep.template_name, {}, override_acta_type or "provincial_distrital")

        election = self._resolve_election_process({"process_code": process_code})
        if not election:
            result.error = "No hay proceso electoral activo. Cree uno en el admin."
            if job:
                job.mark_failed(result.error)
            return result

        unique_id = f"{image.file_hash[:12]}"
        geo = {"ubigeo": "", "department": "", "province": "", "district": ""}
        department, province, district = self._resolve_district("", "", "", "")

        # crear Acta
        acta, _ = Acta.objects.update_or_create(
            unique_id=unique_id,
            acta_type=acta_type,
            election_process=election,
            defaults=dict(
                source_image=image,
                department=department,
                province=province,
                district=district,
                status="procesando",
            ),
        )
        if job:
            job.acta = acta
            job.save(update_fields=["acta"])
        result.acta_id = acta.id

        # duplicados lógicos
        dup = detect_duplicate_acta(acta)
        if dup:
            acta.duplicate_of = dup
            acta.status = "duplicado"
            acta.save(update_fields=["duplicate_of", "status"])
            ProcessingAlert.objects.create(
                acta=acta,
                job=job,
                severity="warning",
                alert_type="DUPLICATE_DETECTED",
                message=f"Acta parece duplicado de acta #{dup.id}",
                detail={"duplicate_of": dup.id, "unique_id": dup.unique_id},
            )
            result.status = "duplicado"
            result.ok = True
            if job:
                job.mark_done({"duplicate_of": dup.id})
            return result

        ai_response: AIResponse | None = None
        structured: dict = {}
        if provider_settings.api_key:
            provider = build_provider(provider_settings)
            if provider is None:
                result.error = "No se pudo cargar proveedor IA"
                if job:
                    job.mark_failed(result.error)
                acta.status = "error"
                acta.save(update_fields=["status"])
                return result
            ai_response = provider.process_acta(
                image_path=prep.preprocessed_path,
                ocr_context=ocr_global,
                acta_type=acta_type,
                geo_context=geo,
            )
            if not ai_response.success:
                acta.status = "error"
                acta.save(update_fields=["status"])
                ProcessingAlert.objects.create(
                    acta=acta, job=job, severity="critical",
                    alert_type="AI_ERROR", message=ai_response.error or "Error IA sin detalle",
                )
                result.error = ai_response.error or "Fallo IA"
                if job:
                    job.mark_failed(result.error)
                return result
            structured = ai_response.structured or {}

            meta = structured.get("acta_metadata") or {}
            if meta.get("acta_type") in {"regional", "provincial_distrital"}:
                acta_type = meta["acta_type"]
            d, p, dd = self._resolve_district(
                meta.get("ubigeo") or "", meta.get("department") or "",
                meta.get("province") or "", meta.get("district") or "",
            )
            if dd:
                department, province, district = d, p, dd
            if meta.get("table_number"):
                acta.table_number = str(meta["table_number"])[:32]
            acta.acta_type = acta_type
            acta.department = department
            acta.province = province
            acta.district = district
            acta.save(update_fields=["acta_type", "department", "province", "district", "table_number"])

        conf_indicators = (structured.get("confidence_indicators") or {}) if ai_response else {}
        transcription = ActaTranscription.objects.create(
            acta=acta,
            provider=ai_response.provider if ai_response else "ocr_only",
            model=ai_response.model if ai_response else "",
            raw_json=ai_response.raw_json if ai_response else {"ocr_only": True, "prep_warnings": prep.warnings},
            ocr_text=ocr_global[:200000],
            global_confidence=ai_response.confidence if ai_response else 0.0,
            validation_status="pendiente",
            tech_notes=f"Prep: {prep_status} | OCR regiones: {list(ocr_out.get('regions', {}).keys())}",
            processing_job=job,
        )
        result.transcription_id = transcription.id

        vote_entries_list = structured.get("vote_entries") or []
        if not vote_entries_list:
            vote_entries_list = self._synthesize_entries_from_ocr(ocr_out, acta_type)
        stats_entries = self._persist_entries(acta, transcription, vote_entries_list)
        result.messages.append(f"Entradas de votos: {stats_entries}")

        threshold = provider_settings.confidence_threshold if provider_settings else 0.85
        validation = ActaValidator(acta).validate(threshold_confidence=threshold)
        result.validation = validation
        transcription.validation_status = validation.validation_status
        transcription.save(update_fields=["validation_status"])

        acta.status = "procesado_ok" if validation.passed else "observado"
        acta.save(update_fields=["status"])

        review_item = None
        if not validation.passed or acta.status == "observado":
            review_item = ManualReviewQueue.objects.create(
                acta=acta,
                transcription=transcription,
                reason="; ".join(validation.review_reasons) or "Baja confianza / validación incompleta",
                reason_codes=validation.review_codes or ["AUTO_OBSERVED"],
            )
            result.review_item_id = review_item.id
            for code in validation.review_codes or ["LOW_CONF"]:
                ProcessingAlert.objects.get_or_create(
                    acta=acta,
                    job=job,
                    alert_type=code,
                    defaults={
                        "severity": "warning",
                        "message": f"Código {code}",
                        "detail": {"validation": validation.arithmetic, "reasons": validation.review_reasons},
                    },
                )

        result.ok = True
        result.status = acta.status
        summary = {
            "acta_id": acta.id,
            "transcription_id": transcription.id,
            "status": acta.status,
            "validation_status": validation.validation_status,
            "validation": validation.arithmetic,
            "review_codes": validation.review_codes,
        }
        if job:
            job.mark_done(summary)
        return result

    def _synthesize_entries_from_ocr(self, ocr_out: dict, acta_type: str) -> list[dict]:
        numbers = ocr_out.get("numbers") or []
        sample_entries = []
        acta_type_norm = (acta_type or "").upper()
        for i, n in enumerate(numbers[:6]):
            base = {
                "entry_category": "organizacion",
                "organization_code": f"SYN_{i:03d}",
                "organization_name": f"Partido Sintetico {i + 1}",
                "candidate_name": "",
                "position": "",
                "scope": acta_type,
                "sort_order": i,
                "field_confidence": 0.3,
                "votos_provincial": 0,
                "votos_distrital": 0,
                "votos_gob_gobernador_vice": 0,
                "votos_consejero_regional": 0,
            }
            val = int(n)
            if acta_type_norm == "REGIONAL":
                base["votos_gob_gobernador_vice"] = val
                base["votos_consejero_regional"] = val
            elif acta_type_norm == "PROVINCIAL_DISTRITAL":
                base["votos_provincial"] = val
                base["votos_distrital"] = val
            elif acta_type_norm == "DISTRITAL":
                base["votos_distrital"] = val
            else:
                base["votos_provincial"] = val
            sample_entries.append(base)
        nlist = list(numbers)

        def fill_totals(cat: str, field: str, idx: int, order: int) -> dict:
            base = {
                "entry_category": cat,
                "organization_code": "",
                "organization_name": cat.title(),
                "candidate_name": "",
                "position": "",
                "scope": "",
                "sort_order": order,
                "field_confidence": 0.25,
                "votos_provincial": 0,
                "votos_distrital": 0,
                "votos_gob_gobernador_vice": 0,
                "votos_consejero_regional": 0,
            }
            if len(nlist) > idx:
                v = int(nlist[idx])
                if field:
                    base[field] = v
            return base

        if acta_type_norm == "REGIONAL":
            sample_entries.append(fill_totals("blanco", "votos_gob_gobernador_vice", -4, 1000))
            sample_entries.append(fill_totals("nulo", "votos_gob_gobernador_vice", -3, 1001))
            sample_entries.append(fill_totals("impugnado", "votos_gob_gobernador_vice", -2, 1002))
            t = fill_totals("total", "", -1, 2000)
            t["votos_gob_gobernador_vice"] = sum(e["votos_gob_gobernador_vice"] for e in sample_entries)
            t["votos_consejero_regional"] = sum(e["votos_consejero_regional"] for e in sample_entries)
            sample_entries.append(t)
        elif acta_type_norm == "PROVINCIAL_DISTRITAL":
            sample_entries.append(fill_totals("blanco", "votos_provincial", -4, 1000))
            sample_entries.append(fill_totals("nulo", "votos_provincial", -3, 1001))
            sample_entries.append(fill_totals("impugnado", "votos_provincial", -2, 1002))
            t = fill_totals("total", "", -1, 2000)
            t["votos_provincial"] = sum(e["votos_provincial"] for e in sample_entries)
            t["votos_distrital"] = sum(e["votos_distrital"] for e in sample_entries)
            sample_entries.append(t)
        else:
            sample_entries.append(fill_totals("blanco", "votos_distrital", -4, 1000))
            sample_entries.append(fill_totals("nulo", "votos_distrital", -3, 1001))
            sample_entries.append(fill_totals("impugnado", "votos_distrital", -2, 1002))
            t = fill_totals("total", "", -1, 2000)
            t["votos_distrital"] = sum(e["votos_distrital"] for e in sample_entries)
            sample_entries.append(t)
        return sample_entries
