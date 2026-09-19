from __future__ import annotations

import io
import json
import logging
from django.contrib import admin, messages
from django.contrib.admin.widgets import AdminFileWidget
from django.db import models
from django import forms
from django.utils.html import format_html, mark_safe
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.http import HttpResponse, HttpResponseRedirect
from django.conf import settings

from .models import (
    GeoDepartment, GeoProvince, GeoDistrict,
    PoliticalOrganization, ElectionProcess,
    ActaImage, Acta, ActaTranscription, ActaVoteEntry,
    ProcessingJob, ProcessingAlert,
    ManualReviewQueue, AIProviderSettings, HumanCorrection, AuditLog,
    OrganizationList, OrganizationListEntry,
)
from .models_legacy import UsuarioLegacy, ActaEscrutinioLegacy, MesaLegacy, CentroVotacionLegacy
from .crypto import encrypt_value


class JsonPrettifiedWidget(admin.widgets.AdminTextareaWidget):
    def render(self, name, value, attrs=None, renderer=None):
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, indent=2)
        return super().render(name, value, attrs, renderer)


class PrettyJsonMixin:
    formfield_overrides = {
        models.JSONField: {"widget": JsonPrettifiedWidget},
    }


@admin.register(GeoDepartment)
class GeoDepartmentAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active", "provinces_count")
    list_filter = ("is_active",)
    search_fields = ("code", "name")
    ordering = ("code",)

    def provinces_count(self, obj):
        return obj.provinces.count()


@admin.register(GeoProvince)
class GeoProvinceAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "department", "is_active", "districts_count")
    list_filter = ("is_active", "department")
    search_fields = ("code", "name")
    raw_id_fields = ("department",)
    autocomplete_fields = ("department",)

    def districts_count(self, obj):
        return obj.districts.count()


@admin.register(GeoDistrict)
class GeoDistrictAdmin(admin.ModelAdmin):
    list_display = ("ubigeo", "name", "province", "department_field", "has_district_election", "is_active")
    list_filter = ("is_active", "has_district_election", "province__department")
    search_fields = ("ubigeo", "name")
    autocomplete_fields = ("province",)

    def department_field(self, obj):
        return obj.province.department.name if obj.province_id else ""


@admin.register(PoliticalOrganization)
class PoliticalOrganizationAdmin(admin.ModelAdmin):
    list_display = ("code", "short_name", "acronym", "scope", "sort_order", "is_active")
    list_filter = ("is_active", "scope")
    search_fields = ("code", "short_name", "full_name", "acronym")
    list_editable = ("sort_order", "is_active", "scope")
    ordering = ("sort_order", "short_name")
    actions = ["activate_selected", "deactivate_selected", "export_json"]

    def activate_selected(self, request, qs):
        n = qs.update(is_active=True)
        self.message_user(request, f"Activadas {n} organizaciones", messages.SUCCESS)

    def deactivate_selected(self, request, qs):
        n = qs.update(is_active=False)
        self.message_user(request, f"Desactivadas {n} organizaciones", messages.WARNING)

    def export_json(self, request, qs):
        data = list(qs.values("code", "short_name", "full_name", "acronym", "scope", "sort_order", "is_active"))
        response = HttpResponse(
            json.dumps(data, ensure_ascii=False, indent=2),
            content_type="application/json",
        )
        response["Content-Disposition"] = "attachment; filename=organizaciones_politicas.json"
        return response


@admin.register(ElectionProcess)
class ElectionProcessAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "process_date", "has_regional", "has_provincial", "has_distrital", "is_active")
    list_filter = ("is_active", "process_date", "has_regional", "has_provincial", "has_distrital")
    search_fields = ("code", "name")
    readonly_fields = ("created_at", "updated_at")


class ActaImageInline(admin.TabularInline):
    model = Acta
    fields = ("id", "acta_type", "table_number", "status", "unique_id")
    readonly_fields = fields
    extra = 0
    show_change_link = True


@admin.register(ActaImage)
class ActaImageAdmin(PrettyJsonMixin, admin.ModelAdmin):
    list_display = ("id", "file_basename", "file_hash_short", "availability", "file_size_mb", "created_at", "actas_linked")
    list_filter = ("availability", "created_at")
    search_fields = ("file_path", "file_hash")
    readonly_fields = ("file_hash", "created_at", "updated_at")
    inlines = [ActaImageInline]
    actions = ["enqueue_process"]

    def file_basename(self, obj):
        import os
        return os.path.basename(obj.file_path)

    def file_hash_short(self, obj):
        return obj.file_hash[:16] + "..." if obj.file_hash else ""

    def file_size_mb(self, obj):
        return f"{(obj.file_size or 0) / 1048576:.2f} MB"

    def actas_linked(self, obj):
        return obj.actas.count()

    def enqueue_process(self, request, qs):
        from .processors import ActaProcessor
        proc = ActaProcessor()
        created = 0
        for img in qs.filter(availability="available"):
            proc.create_job_for_image(img)
            created += 1
        self.message_user(request, f"Creados {created} jobs de procesamiento", messages.SUCCESS)


class VoteEntryInline(admin.TabularInline):
    model = ActaVoteEntry
    fields = ("entry_category", "organization", "candidate_name", "position", "scope", "sort_order", "votes", "data_origin", "field_confidence", "is_corrected")
    readonly_fields = ("is_corrected",)
    extra = 0
    raw_id_fields = ("organization", "transcription")


class TranscriptionInline(admin.TabularInline):
    model = ActaTranscription
    fields = ("id", "provider", "model", "global_confidence", "validation_status", "created_at")
    readonly_fields = fields
    extra = 0
    show_change_link = True


class JobInline(admin.TabularInline):
    model = ProcessingJob
    fields = ("id", "job_type", "status", "priority", "started_at", "finished_at", "error_message")
    readonly_fields = fields
    extra = 0
    show_change_link = True


class AlertInline(admin.TabularInline):
    model = ProcessingAlert
    fields = ("id", "severity", "alert_type", "message", "acknowledged")
    extra = 0


class ReviewInline(admin.StackedInline):
    model = ManualReviewQueue
    fields = ("reason", "reason_codes", "status", "assigned_to", "opened_at", "started_review_at", "closed_at", "closed_by", "resolution_notes")
    readonly_fields = ("opened_at",)
    extra = 0
    raw_id_fields = ("assigned_to", "closed_by", "transcription")
    show_change_link = True


@admin.register(Acta)
class ActaAdmin(PrettyJsonMixin, admin.ModelAdmin):
    list_display = ("id", "unique_id_short", "acta_type", "election_process", "location", "table_number", "status", "vote_total_display", "global_conf", "created_at")
    list_filter = ("acta_type", "status", "election_process", "department")
    search_fields = ("unique_id", "table_number", "district__name", "province__name", "department__name")
    raw_id_fields = ("election_process", "department", "province", "district", "source_image", "duplicate_of")
    readonly_fields = ("created_at", "updated_at")
    inlines = [TranscriptionInline, VoteEntryInline, JobInline, AlertInline, ReviewInline]
    actions = ["reprocess_selected", "send_to_review", "mark_reviewed", "export_detailed_xlsx"]

    def unique_id_short(self, obj):
        return obj.unique_id[:20] + ("..." if len(obj.unique_id) > 20 else "")

    def location(self, obj):
        return obj.get_location_label()

    def vote_total_display(self, obj):
        from django.db.models import Sum
        t = obj.vote_entries.filter(entry_category="total").aggregate(s=Sum("votes"))["s"]
        return t if t is not None else "-"

    def global_conf(self, obj):
        t = obj.transcriptions.order_by("-id").first()
        return f"{t.global_confidence:.2f}" if t else "-"

    def reprocess_selected(self, request, qs):
        from .processors import ActaProcessor
        proc = ActaProcessor()
        for acta in qs:
            if acta.source_image:
                job = proc.create_job_for_image(acta.source_image, params={"acta_id": acta.id})
                job.acta = acta
                job.save(update_fields=["acta"])
            acta.status = "pendiente"
            acta.save(update_fields=["status"])
        self.message_user(request, f"Reprocesando {qs.count()} actas", messages.SUCCESS)

    def send_to_review(self, request, qs):
        n = 0
        for acta in qs:
            if not ManualReviewQueue.objects.filter(acta=acta, status__in={"abierto", "en_revision"}).exists():
                ManualReviewQueue.objects.create(
                    acta=acta,
                    reason="Enviado manualmente a revisión desde admin",
                    reason_codes=["MANUAL_REVIEW"],
                )
                acta.status = "observado"
                acta.save(update_fields=["status"])
                n += 1
        self.message_user(request, f"{n} actas enviadas a revisión", messages.SUCCESS)

    def mark_reviewed(self, request, qs):
        qs.update(status="revisado_ok")
        qs.filter(review_items__status__in={"abierto", "en_revision"}).update(
            review_items__status="cerrado"
        )
        self.message_user(request, f"Marcadas {qs.count()} como revisadas OK", messages.SUCCESS)

    def export_detailed_xlsx(self, request, qs):
        from .exporters import export_actas_xlsx
        wb = export_actas_xlsx(qs)
        bio = io.BytesIO()
        wb.save(bio)
        bio.seek(0)
        response = HttpResponse(
            bio.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = "attachment; filename=actas_detalle.xlsx"
        return response


@admin.register(ActaTranscription)
class ActaTranscriptionAdmin(PrettyJsonMixin, admin.ModelAdmin):
    list_display = ("id", "acta", "provider", "model", "global_confidence", "validation_status", "created_at")
    list_filter = ("provider", "validation_status", "created_at")
    search_fields = ("acta__unique_id", "provider", "model")
    raw_id_fields = ("acta", "processing_job")
    readonly_fields = ("created_at", "updated_at")
    inlines = [VoteEntryInline]


@admin.register(ActaVoteEntry)
class ActaVoteEntryAdmin(admin.ModelAdmin):
    list_display = ("id", "acta", "ambito", "entry_category", "organization_short", "candidate_name", "position", "votes",
                    "votos_provincial", "votos_distrital", "votos_gob_gobernador_vice", "votos_consejero_regional",
                    "data_origin", "field_confidence", "is_corrected")
    list_filter = ("ambito", "entry_category", "data_origin", "is_corrected")
    search_fields = ("acta__unique_id", "candidate_name", "organization__short_name")
    raw_id_fields = ("acta", "transcription", "organization")

    def organization_short(self, obj):
        return obj.organization.short_name if obj.organization_id else "-"


@admin.register(ProcessingJob)
class ProcessingJobAdmin(PrettyJsonMixin, admin.ModelAdmin):
    list_display = ("id", "job_type", "acta_image", "acta", "status", "priority", "retry_count", "created_at", "started_at", "finished_at", "duration_s")
    list_filter = ("status", "job_type", "priority", "created_at")
    search_fields = ("acta_image__file_path", "acta__unique_id", "error_message")
    readonly_fields = ("created_at", "updated_at", "started_at", "finished_at")
    raw_id_fields = ("acta_image", "acta")
    actions = ["cancel_pending", "rerun_failed", "run_now", "run_process_legacy"]

    def duration_s(self, obj):
        if obj.started_at and obj.finished_at:
            return f"{(obj.finished_at - obj.started_at).total_seconds():.1f}s"
        return "-"

    def cancel_pending(self, request, qs):
        n = qs.filter(status="pending").update(status="cancelled")
        self.message_user(request, f"Cancelados {n} jobs", messages.WARNING)

    def rerun_failed(self, request, qs):
        from . import tasks
        n = 0
        for job in qs.filter(status="failed"):
            job.status = "pending"
            job.retry_count += 1
            job.error_message = ""
            job.save(update_fields=["status", "retry_count", "error_message"])
            tasks.run_processing_job.delay(job.id)
            n += 1
        self.message_user(request, f"Re-lanzados {n} jobs fallidos", messages.SUCCESS)

    def run_now(self, request, qs):
        from .processors import ActaProcessor
        n = 0
        for job in qs.filter(status__in={"pending", "failed", "cancelled"}):
            proc = ActaProcessor(job=job)
            if job.acta_image:
                proc.process_acta_image(job.acta_image)
                n += 1
        self.message_user(request, f"Ejecutados {n} jobs en modo sincrono", messages.SUCCESS)

    def run_process_legacy(self, request, qs):
        """Job sincrónico: procesa un acta_escrutinio_legacy (parameter legacy_id) por cada job elegido."""
        from .processors import ActaProcessor
        n_ok = 0
        n_err = 0
        for job in qs.filter(status__in={"pending", "failed", "cancelled", "done"}):
            legacy_id = (job.parameters or {}).get("legacy_id")
            if not legacy_id:
                continue
            proc = ActaProcessor(job=job)
            r = proc.process_legacy_acta_escrutinio(int(legacy_id))
            if r.ok:
                n_ok += 1
            else:
                n_err += 1
        self.message_user(request, f"Procesados {n_ok} OK, {n_err} fallidos", messages.SUCCESS if n_err == 0 else messages.WARNING)


@admin.register(ProcessingAlert)
class ProcessingAlertAdmin(PrettyJsonMixin, admin.ModelAdmin):
    list_display = ("id", "acta", "job", "severity", "alert_type", "message_short", "acknowledged", "created_at")
    list_filter = ("severity", "alert_type", "acknowledged")
    search_fields = ("message", "detail")
    readonly_fields = ("created_at", "updated_at", "acknowledged_at")
    raw_id_fields = ("acta", "job", "acknowledged_by")
    actions = ["acknowledge_selected"]

    def message_short(self, obj):
        return obj.message[:80] + ("..." if len(obj.message) > 80 else "")

    def acknowledge_selected(self, request, qs):
        from django.utils import timezone
        qs.filter(acknowledged=False).update(
            acknowledged=True,
            acknowledged_by=request.user,
            acknowledged_at=timezone.now(),
        )
        self.message_user(request, "Alertas confirmadas", messages.SUCCESS)


class AIProviderForm(forms.ModelForm):
    api_key_raw = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Escribir nueva clave para reemplazar la cifrada existente.",
        label="Nueva API Key (se cifra)",
    )

    class Meta:
        model = AIProviderSettings
        fields = "__all__"


class CorrectionsInline(admin.TabularInline):
    model = HumanCorrection
    fields = ("field_name", "old_value", "new_value", "ia_value", "corrected_by", "created_at", "comment")
    readonly_fields = ("created_at",)
    extra = 0
    raw_id_fields = ("corrected_by", "vote_entry", "review_item")


@admin.register(ManualReviewQueue)
class ManualReviewQueueAdmin(PrettyJsonMixin, admin.ModelAdmin):
    list_display = ("id", "acta", "status", "assigned_to", "reason_short", "opened_at", "started_review_at", "closed_at", "time_open")
    list_filter = ("status", "opened_at")
    search_fields = ("reason", "acta__unique_id")
    readonly_fields = ("opened_at",)
    raw_id_fields = ("acta", "transcription", "assigned_to", "closed_by")
    inlines = [CorrectionsInline]
    actions = ["claim_selected", "close_selected"]

    def reason_short(self, obj):
        return obj.reason[:80] + ("..." if len(obj.reason) > 80 else "")

    def time_open(self, obj):
        from django.utils import timezone
        end = obj.closed_at or timezone.now()
        delta = end - obj.opened_at
        return f"{delta.days}d {delta.seconds // 3600}h"

    def claim_selected(self, request, qs):
        qs.filter(assigned_to__isnull=True, status="abierto").update(
            assigned_to=request.user,
            status="en_revision",
        )
        self.message_user(request, f"Asignado a {request.user}", messages.SUCCESS)

    def close_selected(self, request, qs):
        from django.utils import timezone
        qs.filter(status__in={"abierto", "en_revision"}).update(
            status="cerrado",
            closed_by=request.user,
            closed_at=timezone.now(),
        )
        self.message_user(request, "Cerrados correctamente", messages.SUCCESS)


@admin.register(HumanCorrection)
class HumanCorrectionAdmin(admin.ModelAdmin):
    list_display = ("id", "acta", "field_name", "diff_display", "corrected_by", "created_at")
    list_filter = ("field_name", "created_at")
    search_fields = ("field_name", "old_value", "new_value", "comment")
    raw_id_fields = ("review_item", "acta", "vote_entry", "corrected_by")
    readonly_fields = ("created_at", "updated_at")

    def diff_display(self, obj):
        return format_html(
            '<span style="color:#b00">{}</span> &rarr; <span style="color:#080">{}</span>',
            obj.old_value[:40] or "(vacio)",
            obj.new_value[:40] or "(vacio)",
        )


@admin.register(AuditLog)
class AuditLogAdmin(PrettyJsonMixin, admin.ModelAdmin):
    list_display = ("id", "created_at", "actor", "action", "model_name", "object_id", "field_name", "summary")
    list_filter = ("action", "model_name", "created_at")
    search_fields = ("actor__username", "model_name", "object_id", "old_value", "new_value")
    readonly_fields = ("created_at", "updated_at")

    def summary(self, obj):
        if obj.field_name:
            return f"{obj.old_value[:20]} -> {obj.new_value[:20]}"
        return obj.new_value[:80]

    def has_view_permission(self, request, obj=None):
        return bool(request.user.is_superuser)
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
    def has_add_permission(self, request): return False


@admin.register(AIProviderSettings)
class AIProviderSettingsAdmin(admin.ModelAdmin):
    form = AIProviderForm
    list_display = ("provider", "model", "is_active", "is_default", "temperature", "timeout_secs", "confidence_threshold", "double_validation", "fallback_to", "ping_status")
    list_editable = ("is_active", "is_default", "temperature", "timeout_secs", "confidence_threshold", "double_validation")
    readonly_fields = ("_api_key_encrypted", "_extra_encrypted")
    exclude = ("_api_key_encrypted", "_extra_encrypted")

    def has_view_permission(self, request, obj=None): return bool(request.user.is_superuser)
    def has_change_permission(self, request, obj=None): return bool(request.user.is_superuser)
    def has_delete_permission(self, request, obj=None): return bool(request.user.is_superuser)
    def has_add_permission(self, request): return bool(request.user.is_superuser)

    def ping_status(self, obj):
        return format_html(
            '<a class="button" href="{}">Probar conexión</a>',
            reverse("admin:test_provider", args=[obj.pk]),
        )
    ping_status.short_description = "Conexión"

    def save_model(self, request, obj, form, change):
        raw = form.cleaned_data.get("api_key_raw") or ""
        if raw:
            obj.api_key = raw
        elif not change and not obj.api_key:
            obj.api_key = ""
        if obj.is_default:
            AIProviderSettings.objects.exclude(pk=obj.pk).update(is_default=False)
        super().save_model(request, obj, form, change)

    def get_urls(self):
        urls = super().get_urls()
        return [
            path("<int:pk>/test/", self.admin_site.admin_view(self.test_provider), name="test_provider"),
        ] + urls

    def test_provider(self, request, pk):
        if not request.user.is_superuser:
            self.message_user(request, "Sin permiso", messages.ERROR)
            return HttpResponseRedirect(reverse("admin:core_aiprovidersettings_changelist"))
        obj = self.get_object(request, pk)
        ok, msg = obj.test_connection()
        level = messages.SUCCESS if ok else messages.ERROR
        self.message_user(request, msg, level)
        return HttpResponseRedirect(reverse("admin:core_aiprovidersettings_changelist"))


# --- Resto admin ROLE-AWARE: SUPER_ADMIN -> todo; ADMINISTRADOR -> lectura limitada ---

class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return bool(request.user.is_superuser)
    def has_delete_permission(self, request, obj=None): return bool(request.user.is_superuser)


class OrganizationListEntryInline(admin.TabularInline):
    model = OrganizationListEntry
    fields = ("sort_order", "organization", "candidate_name", "scope_label", "notes")
    raw_id_fields = ("organization",)
    extra = 0


@admin.register(OrganizationList)
class OrganizationListAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "tipo_acta", "ambito", "departamento", "provincia", "distrito", "is_active")
    list_filter = ("tipo_acta", "ambito", "is_active", "departamento", "provincia")
    search_fields = ("code", "name")
    list_editable = ("is_active",)
    inlines = [OrganizationListEntryInline]

    def has_add_permission(self, request): return True
    def has_change_permission(self, request, obj=None): return True
    def has_delete_permission(self, request, obj=None): return bool(request.user.is_superuser)


@admin.register(OrganizationListEntry)
class OrganizationListEntryAdmin(admin.ModelAdmin):
    list_display = ("id", "list", "sort_order", "organization", "candidate_name", "scope_label")
    list_editable = ("sort_order",)
    list_filter = ("list__tipo_acta", "list__ambito")
    search_fields = ("list__code", "list__name", "candidate_name", "organization__short_name")
    raw_id_fields = ("list", "organization")
    ordering = ("list", "sort_order")


# Tablas legadas: SOLO LECTURA, SOLO SUPER_ADMIN las ve en admin.

@admin.register(UsuarioLegacy)
class UsuarioLegacyAdmin(ReadOnlyAdmin):
    list_display = ("idusuario", "usuario", "rol", "estado", "id_centro_asignado", "id_mesa_asignada")
    list_filter = ("rol", "estado")
    search_fields = ("usuario", "rol")

    def has_view_permission(self, request, obj=None): return bool(request.user.is_superuser)


@admin.register(CentroVotacionLegacy)
class CentroVotacionLegacyAdmin(ReadOnlyAdmin):
    list_display = ("id_centro", "codigo_local", "ubigeo", "nombre_local", "departamento", "provincia", "distrito")
    list_filter = ("departamento", "provincia")
    search_fields = ("nombre_local", "codigo_local", "ubigeo", "distrito")

    def has_view_permission(self, request, obj=None): return bool(request.user.is_staff)


@admin.register(MesaLegacy)
class MesaLegacyAdmin(ReadOnlyAdmin):
    list_display = ("id_mesa", "num_mesa", "cod_local", "departamento", "provincia", "distrito", "nombre_local")
    list_filter = ("departamento", "provincia", "distrito")
    search_fields = ("num_mesa", "distrito", "codi_ubigeo", "nombre_local")

    def has_view_permission(self, request, obj=None): return bool(request.user.is_staff)


@admin.register(ActaEscrutinioLegacy)
class ActaEscrutinioLegacyAdmin(ReadOnlyAdmin):
    list_display = ("id", "mesa_numero", "tipo_acta", "distrito_legacy", "iquitos_especial", "archivo_nombre", "created_at")
    list_filter = ("tipo_acta",)
    search_fields = ("mesa_numero", "file_path_disco")
    actions = ["enqueue_legacy"]

    def has_view_permission(self, request, obj=None): return bool(request.user.is_staff)
    def has_change_permission(self, request, obj=None): return False
    def has_add_permission(self, request): return False
    def has_delete_permission(self, request, obj=None): return False

    def distrito_legacy(self, obj):
        return obj.distrito
    def archivo_nombre(self, obj):
        import os
        return os.path.basename(obj.file_path_disco or "")
    def iquitos_especial(self, obj):
        return "SÍ (1-col)" if obj.is_iquitos_special else "2-col"
    iquitos_especial.short_description = "Layout"

    def enqueue_legacy(self, request, qs):
        from .processors import ActaProcessor
        from .models import ProcessingJob
        n_ok = 0
        for leg in qs.iterator():
            try:
                params = {"legacy_id": leg.id, "acta_escrutinio_legacy_id": leg.id}
                job = ProcessingJob.objects.create(
                    job_type="process_legacy_acta",
                    parameters=params,
                )
                proc = ActaProcessor(job=job)
                r = proc.process_legacy_acta_escrutinio(leg.id)
                if r.ok:
                    n_ok += 1
            except Exception as exc:
                logging.exception("Error enqueing legacy %s", leg.id)
                messages.error(request, f"Acta legada {leg.id}: {exc}")
        messages.success(request, f"Lanzadas a procesar OK: {n_ok}/{qs.count()}")


admin.site.site_header = "Procesamiento Electoral · Elecciones 2026 (Whadox)"
admin.site.site_title = "Elecciones 2026 · Auditoría"
admin.site.index_title = "Panel de Control"
