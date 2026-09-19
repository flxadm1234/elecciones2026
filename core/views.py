from __future__ import annotations

import json
import logging
import os
import mimetypes
import uuid
from datetime import datetime, timedelta, timezone

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator, InvalidPage, EmptyPage
from django.db import transaction
from django.db.models import Sum, IntegerField, Count, Q, Case, When, FloatField
from django.db.models.functions import Coalesce, TruncHour
from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods
from django.http import Http404, JsonResponse, HttpResponse, FileResponse
from django.utils.encoding import smart_str
from django.urls import reverse

from .models import Acta, ActaVoteEntry, ManualReviewQueue, ActaImage, HumanCorrection, AuditLog, PoliticalOrganization, ActaTranscription, ProcessingJob, ProcessingAlert, ProcessingConfig
from .models_legacy import ActaEscrutinioLegacy, MesaLegacy
from .forms import ActaFilterForm


log = logging.getLogger("core.ui")


PAGINATOR_PER_PAGE = 25
STATUS_PROCESADOS = {"procesado_ok", "revisado_ok", "observado", "error"}
STATUS_PENDIENTES = {"pendiente", "procesando"}


_ACTA_TYPE_MAP = {
    "REGIONAL": "regional",
    "PROVINCIAL_DISTRITAL": "provincial_distrital",
    "PROVINCIAL": "provincial_distrital",
    "DISTRITAL": "provincial_distrital",
    "regional": "regional",
    "provincial_distrital": "provincial_distrital",
}


def _normalize_acta_type(raw: str | None) -> str:
    """Normaliza tipo_acta legacy (uppercase REGIONAL/PROVINCIAL_DISTRITAL) a choices ACTA_TYPES lowercase."""
    if not raw:
        return "regional"
    k = str(raw).strip()
    if k in _ACTA_TYPE_MAP:
        return _ACTA_TYPE_MAP[k]
    # fallback fuzzy
    kl = k.upper()
    if "REGIONAL" in kl:
        return "regional"
    if "PROVINC" in kl or "DISTRI" in kl:
        return "provincial_distrital"
    return "regional"


def _compute_legacy_pending_ids() -> tuple[set[int], set[int], set[int]]:
    """Calcula 3 conjuntos de IDs en actas_escrutinio (managed=False):
       (1) never_synced_ids  = sin fila en vps_actas.acta_escrutinio_legacy_id
       (2) pending_synced_ids = ya vinculados en vps_actas pero con status=pendiente/procesando
           Y SIN transcripción exitosa (ActaTranscription no existe con extracted_ok=True)
       (3) batch_pending_ids  = (1) U (2) → IDs que debe procesar el botón "Procesar Lote".

    No realiza ningún INSERT/UPDATE en BD legacy (solo lecturas).
    """
    try:
        all_legacy_ids = set(
            ActaEscrutinioLegacy.objects
            .order_by()
            .values_list("id", flat=True)
            .distinct()
        )
    except Exception:
        all_legacy_ids = set()

    if not all_legacy_ids:
        return set(), set(), set()

    try:
        synced_qs = Acta.objects.filter(acta_escrutinio_legacy_id__in=all_legacy_ids)
        synced_ids = set(synced_qs.values_list("acta_escrutinio_legacy_id", flat=True).distinct())
    except Exception:
        synced_ids = set()

    never_synced_ids = {lid for lid in all_legacy_ids if lid not in synced_ids}

    try:
        pending_acta_qs = Acta.objects.filter(
            acta_escrutinio_legacy_id__in=synced_ids,
            status__in=STATUS_PENDIENTES,
        )
        pending_synced_all = set(pending_acta_qs.values_list("acta_escrutinio_legacy_id", flat=True).distinct())
    except Exception:
        pending_synced_all = set()

    try:
        ok_transcribed_ids = set(
            ActaTranscription.objects.filter(
                acta__acta_escrutinio_legacy_id__in=pending_synced_all,
                extracted_ok=True,
            ).values_list("acta__acta_escrutinio_legacy_id", flat=True).distinct()
        )
    except Exception:
        ok_transcribed_ids = set()

    pending_synced_ids = {lid for lid in pending_synced_all if lid not in ok_transcribed_ids}
    batch_pending_ids = set(never_synced_ids) | set(pending_synced_ids)
    return never_synced_ids, pending_synced_ids, batch_pending_ids


def _ensure_legacy_pending_synced() -> tuple[int, int]:
    """Sync LAZY: crea registros Acta(status=pendiente) para filas NUEVAS insertadas
    en legacy `actas_escrutinio` que NO tienen row en vps_actas (por `acta_escrutinio_legacy_id`).

    Restricción: NUNCA modifica ni elimina tablas managed=False; SÓLO INSERTA NUEVAS filas en vps_actas.

    IMPORTANTE unique_id: usa formato L{legacy_id}_{mesa}_{tipo} para coincidir 1:1 con
    ActaProcessor.process_legacy_acta_escrutinio() → evita duplicados posterior update_or_create.

    Returns: (total_legacy, newly_synced_count)
    """
    try:
        synced_ids = set(
            Acta.objects
            .exclude(acta_escrutinio_legacy_id__isnull=True)
            .values_list("acta_escrutinio_legacy_id", flat=True)
            .distinct()
        )
    except Exception:
        synced_ids = set()

    try:
        all_legacy = list(
            ActaEscrutinioLegacy.objects
            .only("id", "mesa_numero", "tipo_acta", "created_at")
            .order_by("-id")
        )
    except Exception:
        return (0, 0)

    if not all_legacy:
        return (0, 0)

    missing = [leg for leg in all_legacy if leg.id not in synced_ids]
    if not missing:
        return (len(all_legacy), 0)

    created = 0
    for leg in missing:
        tipo = _normalize_acta_type(getattr(leg, "tipo_acta", None))
        mesa = (getattr(leg, "mesa_numero", "") or "").strip() or "NN"
        unique = f"L{leg.id}_{mesa}_{tipo}"
        defaults = dict(
            unique_id=unique,
            acta_type=tipo,
            table_number=mesa[:32],
            status="pendiente",
            acta_escrutinio_legacy_id=leg.id,
            layout_flags={"_sync_source": "legacy_auto_pending",
                          "_legacy_created": str(getattr(leg, "created_at", "") or "")},
            confidence_score=None,
            notes="Sincronizado automáticamente desde actas_escrutinio legacy (nuevo registro detectado).",
            source_image=None,
            election_process=None,
            department=None,
            province=None,
            district=None,
            duplicate_of=None,
        )
        try:
            obj, is_new = Acta.objects.get_or_create(
                unique_id=unique,
                acta_type=tipo,
                acta_escrutinio_legacy_id=leg.id,
                defaults=defaults,
            )
            if is_new:
                created += 1
        except Exception as exc:
            log.warning("_ensure_legacy_pending_synced: skip leg_id=%s err=%s", getattr(leg, "id", "?"), exc)
            continue

    if created:
        log.info("_ensure_legacy_pending_synced: created %s new Acta rows from legacy (total legacy=%s, missing=%s)",
                 created, len(all_legacy), len(missing))
    return (len(all_legacy), created)


def _sidebar_badge_counts(request):
    """Retorna dict con badge_counts para sidebar.

    Keys: low_conf_review (ManualReviewQueue pending), pending (actas en
    status pendiente/procesando), processed_ok (procesado_ok+revisado_ok),
    error (actas status error), total (total actas).

    Adicionalmente: detecta actas NUEVAS en legacy actas_escrutinio que no existen
    aún en vps_actas y las crea como status=pendiente (lazy sync) — SÓLO INSERTA en vps_*.
    """
    # Hook: sync legacy → vps_actas antes de calcular badges para mantener conteos realtime.
    try:
        _ensure_legacy_pending_synced()
    except Exception:
        log.warning("_sidebar_badge_counts: lazy sync skipped", exc_info=True)
    try:
        review_pending = (
            ManualReviewQueue.objects
            .filter(status="pending")
            .count()
        )
    except Exception:
        review_pending = 0
    try:
        from .models import Acta
        actas_qs = Acta.objects.all()
        pending_count = actas_qs.filter(status__in=["pendiente", "procesando"]).count()
        processed_ok = actas_qs.filter(status__in=["procesado_ok", "revisado_ok"]).count()
        error_count = actas_qs.filter(status="error").count()
        obs_count = actas_qs.filter(status="observado").count()
        total = actas_qs.count()
    except Exception:
        pending_count = 0
        processed_ok = 0
        error_count = 0
        obs_count = 0
        total = 0
    return {
        "badge_counts": {
            "low_conf_review": review_pending,
            "pending": pending_count,
            "processed_ok": processed_ok,
            "processed_total": processed_ok + obs_count,
            "error": error_count,
            "observado": obs_count,
            "total": total,
        }
    }


def _inject_legacy_meta(actas_list):
    """Enriquecer lista de actas con legado (mesa/distrito/local) layout_flags safe.

    VPS schema: NO hay FK id_mesa_id en actas_escrutinio. Join textual:
    ActaEscrutinioLegacy.mesa_numero == MesaLegacy.num_mesa.
    Siempre setea attrs default para evitar VariableDoesNotExist en templates.
    """
    # Paso 1: siempre asignar defaults seguros, incluso si exception.
    for a in actas_list:
        if not isinstance(a.layout_flags, dict):
            a.layout_flags = {}
        a.legacy_mesa_num = getattr(a, "table_number", None) or ""
        a.legacy_distrito = getattr(a, "district", "") or ""
        a.legacy_provincia = getattr(a, "province", "") or ""
        a.legacy_departamento = getattr(a, "department", "") or ""
        a.legacy_nombre_local = ""
        a.legacy_direccion = ""
        a.legacy_obj = None

    try:
        ids_map = {}
        leg_ids = []
        for a in actas_list:
            if a.acta_escrutinio_legacy_id:
                leg_ids.append(a.acta_escrutinio_legacy_id)
                ids_map[a.acta_escrutinio_legacy_id] = a
        if not leg_ids:
            return actas_list

        # Paso 2: legacy actas_escrutinio (solo campos que EXISTEN en VPS)
        leg_rows = {
            row["id"]: row
            for row in ActaEscrutinioLegacy.objects
            .filter(id__in=leg_ids)
            .values("id", "mesa_numero", "tipo_acta", "file_path_disco")
        }

        # Paso 3: extraer mesa_numero únicos para join textual
        mesa_numeros_unicos = list({
            row["mesa_numero"]
            for row in leg_rows.values()
            if row.get("mesa_numero")
        })
        mesa_rows = {}
        if mesa_numeros_unicos:
            # Campos REALES en VPS: direccion_local (NO direccion), codi_ubigeo
            mesa_rows = {
                row["num_mesa"]: row
                for row in MesaLegacy.objects
                .filter(num_mesa__in=mesa_numeros_unicos)
                .values(
                    "num_mesa", "distrito", "nombre_local",
                    "direccion_local", "provincia", "departamento",
                    "codi_ubigeo", "cod_local",
                )
            }

        # Paso 4: merge por leg_id → mesa_numero → mesa_row
        for leg_id, leg in leg_rows.items():
            a = ids_map.get(leg_id)
            if not a:
                continue
            mesa_num = leg.get("mesa_numero") or ""
            if mesa_num and not a.legacy_mesa_num:
                a.legacy_mesa_num = mesa_num
            mesa = mesa_rows.get(mesa_num) or {}
            if mesa:
                if mesa.get("distrito") and not a.legacy_distrito:
                    a.legacy_distrito = mesa["distrito"]
                if mesa.get("provincia") and not a.legacy_provincia:
                    a.legacy_provincia = mesa["provincia"]
                if mesa.get("departamento") and not a.legacy_departamento:
                    a.legacy_departamento = mesa["departamento"]
                if mesa.get("nombre_local"):
                    a.legacy_nombre_local = mesa["nombre_local"]
                if mesa.get("direccion_local"):
                    a.legacy_direccion = mesa["direccion_local"]
                a.legacy_codi_ubigeo = mesa.get("codi_ubigeo") or ""
                a.legacy_cod_local = mesa.get("cod_local") or ""
    except Exception:
        log.warning("_inject_legacy_meta: tablas legacy no disponibles (fallback defaults seguros)", exc_info=True)
    return actas_list


def kpi_ambito(qs_actas):
    ambitos = [
        "muni_provincial",
        "muni_distrital",
        "gob_gobernador_vice",
        "consejero_regional",
    ]
    qs = ActaVoteEntry.objects.filter(
        acta__in=qs_actas, entry_category="organizacion"
    )
    out = {}
    for amb in ambitos:
        suma = (
            qs.filter(ambito=amb)
            .aggregate(total=Coalesce(Sum("votes"), 0, output_field=IntegerField()))
            ["total"] or 0
        )
        out[amb] = suma
    return out


@login_required
def dashboard_view(request):
    is_super = bool(request.user and request.user.is_superuser)

    try:
        _ensure_legacy_pending_synced()
    except Exception:
        log.warning("dashboard: lazy sync legacy pending skipped", exc_info=True)

    actas_qs = Acta.objects.all()
    total_actas = actas_qs.count()
    ok = actas_qs.filter(status="procesado_ok").count()
    obs = actas_qs.filter(status="observado").count()
    pend = actas_qs.filter(status__in=["pendiente", "procesando"]).count()
    err = actas_qs.filter(status="error").count()
    revisado = actas_qs.filter(status="revisado_ok").count()

    total_legacy = 0
    procesados_legacy_count = 0
    legacy_mode_warning = False
    try:
        total_legacy = ActaEscrutinioLegacy.objects.count()
        procesados_ids = set(
            actas_qs.exclude(acta_escrutinio_legacy_id__isnull=True)
            .values_list("acta_escrutinio_legacy_id", flat=True)
            .distinct()
        )
        procesados_legacy_count = len(procesados_ids)
    except Exception:
        legacy_mode_warning = True
        log.warning("Tablas legadas no disponibles (modo demo local)", exc_info=True)

    try:
        _, _, batch_ids = _compute_legacy_pending_ids()
        legacy_pendientes = len(batch_ids)
    except Exception:
        legacy_pendientes = max(0, total_legacy - procesados_legacy_count)

    tipo_count = {}
    for row in actas_qs.values("acta_type").order_by("acta_type").distinct():
        t = row["acta_type"] or "desconocido"
        tipo_count[t] = actas_qs.filter(acta_type=row["acta_type"]).count()

    totales = kpi_ambito(actas_qs)

    ultimas = list(
        actas_qs.select_related("source_image")
        .order_by("-updated_at", "-id")[:25]
    )
    if not legacy_mode_warning:
        for a in ultimas:
            if not isinstance(a.layout_flags, dict):
                a.layout_flags = {}
            if a.acta_escrutinio_legacy_id:
                try:
                    leg = ActaEscrutinioLegacy.objects.get(pk=a.acta_escrutinio_legacy_id)
                    a.legacy_obj = leg
                except ActaEscrutinioLegacy.DoesNotExist:
                    a.legacy_obj = None

    processing_config, processing_config_error = _get_processing_config_safe()

    v_batch_group = (request.GET.get("batch_group") or "").strip()[:32]
    v_flash_enq = request.GET.get("enq")
    v_flash_skip = request.GET.get("skip")
    v_flash_req = request.GET.get("req")
    v_batch_empty = request.GET.get("batch_empty") == "1"

    _kpi_pendiente = int(pend or 0)
    _kpi_procesando = int(0)  # pending+procesando combined arriba en pend status__in; mantener consistente
    _kpi_legacy_pendientes = int(legacy_pendientes or 0)
    # Compute disabled for dashboard "Procesar Lote" button:
    #   disabled=true si NO hay pendientes (ni nuevos VPS ni legacy pendientes) y NADA procesando.
    batch_button_disabled = (_kpi_pendiente == 0) and (_kpi_procesando == 0) and (_kpi_legacy_pendientes == 0)

    ctx = {
        "is_super": is_super,
        "legacy_mode_warning": legacy_mode_warning,
        "processing_config": processing_config,
        "processing_config_error": processing_config_error,
        "v_batch_group": v_batch_group,
        "v_flash_enq": int(v_flash_enq) if v_flash_enq and v_flash_enq.isdigit() else None,
        "v_flash_skip": int(v_flash_skip) if v_flash_skip and v_flash_skip.isdigit() else None,
        "v_flash_req": int(v_flash_req) if v_flash_req and v_flash_req.isdigit() else None,
        "v_batch_empty": v_batch_empty,
        "batch_button_disabled": bool(batch_button_disabled),
        "kpi": {
            "total": total_actas,
            "ok": ok,
            "observado": obs,
            "pendiente": pend,
            "error": err,
            "revisado": revisado,
            "legacy_total": total_legacy,
            "legacy_procesados": procesados_legacy_count,
            "legacy_pendientes": legacy_pendientes,
        },
        "totales_ambito": totales,
        "por_tipo": tipo_count,
        "ultimas": ultimas,
        "nav_active": "dashboard",
    }
    ctx.update(_sidebar_badge_counts(request))
    return render(request, "core/dashboard.html", ctx)


def _get_processing_config_safe():
    try:
        return ProcessingConfig.get_solo(), None
    except Exception as exc:
        return None, str(exc)


@login_required
@require_http_methods(["POST"])
def procesar_pendientes_view(request):
    """ASYNC: Dispara encolamiento de lote pendiente via Celery. HTTP 302 redirect inmediato.

    No ejecuta NINGÚN procesamiento IA en el hilo request. Solo:
      (1) Lazy sync actas legacy → vps_actas pendientes.
      (2) Calcula IDs candidatos = never_synced ∪ pendientes sin transcripción exitosa.
      (3) Crea ProcessingJob por acta + envia .delay() a Celery.
      (4) HTTP 302 Found → dashboard?batch_group=<hex10>
    """
    from django.http import HttpResponseRedirect

    if not (request.user.is_staff or request.user.is_superuser):
        return HttpResponseRedirect(reverse("dashboard"))

    try:
        _ensure_legacy_pending_synced()
    except Exception:
        log.warning("procesar_pendientes: lazy sync previo skip", exc_info=True)

    try:
        never, pending_synced, batch_ids = _compute_legacy_pending_ids()
        ids_sorted = sorted(batch_ids)
    except Exception:
        log.exception("compute_legacy_pending_ids falló en /procesar-pendientes/")
        ids_sorted = []

    # Obtener singleton config con límites VPS-safe (con fallback si no existe aún)
    try:
        from .models import ProcessingConfig
        config = ProcessingConfig.get_solo()
    except Exception:
        config = None
    max_batch = int(getattr(config, "max_batch_size_per_dispatch", 8) or 8)
    max_concurrent = int(getattr(config, "max_concurrent", 2) or 2)

    if not ids_sorted:
        log.info("procesar_pendientes: sin IDs a procesar → redirect sin lote")
        return HttpResponseRedirect(reverse("dashboard") + "?batch_empty=1")

    ids = ids_sorted[:max_batch]
    batch_group = uuid.uuid4().hex[:10]

    # Import interno para circular import safe (tasks.py importa helpers de views.py)
    enqueued = 0
    skipped = 0
    try:
        from .tasks import _enqueue_legacy_batch
        enqueued, skipped = _enqueue_legacy_batch(
            ids,
            batch_group=batch_group,
            max_concurrent=max_concurrent,
            enqueue_all=True,  # manual: encola TODO el slice, no rate limit por running
        )
    except Exception:
        log.exception("_enqueue_legacy_batch falló en POST /procesar-pendientes/")

    # Audit log
    try:
        AuditLog.objects.create(
            action="create",
            model_name="ProcessingJob",
            object_id="",
            actor=request.user if request.user.is_authenticated else None,
            detail={
                "batch_group": batch_group,
                "requested": len(ids),
                "enqueued": enqueued,
                "skipped": skipped,
                "never_synced_count": len(never),
                "pending_synced_count": len(pending_synced),
                "trigger": "manual_button",
            },
        )
    except Exception:
        log.warning("AuditLog dispatch_batch falló", exc_info=True)

    return HttpResponseRedirect(
        reverse("dashboard")
        + f"?batch_group={batch_group}&enq={enqueued}&skip={skipped}&req={len(ids)}"
    )


def _list_view_common(request, *, list_mode: str, title: str):
    """Lógica compartida entre Procesadas y Pendientes.

    `list_mode` = "processed" | "pending"
    """
    is_super = bool(request.user and request.user.is_superuser)

    if list_mode == "pending":
        # Sync lazy: nuevas actas insertadas en legado actas_escrutinio deben
        # aparecer como status='pendiente' sin que el usuario presione /procesar-pendientes
        try:
            _ensure_legacy_pending_synced()
        except Exception:
            log.warning("_list_view_common pending: lazy sync legacy skipped", exc_info=True)

    if list_mode == "processed":
        base_qs = Acta.objects.filter(status__in=STATUS_PROCESADOS)
        order_default = "-updated_at"
    elif list_mode == "pending":
        base_qs = Acta.objects.filter(status__in=STATUS_PENDIENTES)
        order_default = "-created_at"
    else:
        raise ValueError(f"list_mode desconocido: {list_mode}")

    base_qs = base_qs.select_related("source_image", "election_process")

    form = ActaFilterForm(request.GET or None)
    qs = form.filter_qs(base_qs) if form.is_valid() else base_qs

    orden = request.GET.get("orden", order_default)
    orden_permitidos = {"-updated_at", "updated_at", "-created_at", "created_at", "table_number", "acta_type",
                        "-confidence_score", "confidence_score"}
    if orden not in orden_permitidos:
        orden = order_default
    qs = qs.order_by(orden)

    paginator = Paginator(qs, PAGINATOR_PER_PAGE)
    page = request.GET.get("page", 1)
    try:
        page_obj = paginator.page(page)
    except (EmptyPage, InvalidPage):
        page_obj = paginator.page(1)

    # Hydrate legacy data y layout safe
    page_items = list(page_obj.object_list)
    _inject_legacy_meta(page_items)
    page_obj.object_list = page_items

    # Parámetros de querystring sin `page` para links del paginador
    qs_params = request.GET.copy()
    if "page" in qs_params:
        del qs_params["page"]
    qs_params_no_page = qs_params.urlencode()

    total_count = paginator.count
    ctx = {
        "is_super": is_super,
        "list_mode": list_mode,
        "list_title": title,
        "filter_form": form,
        "page_obj": page_obj,
        "paginator": paginator,
        "total_count": total_count,
        "orden": orden,
        "qs_params": qs_params_no_page,
        "nav_active": "procesadas" if list_mode == "processed" else "pendientes",
    }
    ctx.update(_sidebar_badge_counts(request))
    return render(request, "core/actas_list.html", ctx)


@login_required
def actas_procesadas_list_view(request):
    """Listado de actas con status de procesamiento terminado."""
    return _list_view_common(request, list_mode="processed", title="Actas Procesadas")


@login_required
def actas_pendientes_list_view(request):
    """Listado de actas pendientes o en procesamiento."""
    return _list_view_common(request, list_mode="pending", title="Actas por Procesar")


# ==========================================================
#  FASE D y E stubs (placeholder, se implementan completamente más adelante)
# ==========================================================
@login_required
def acta_detail_view(request, pk: int):
    """FASE D: Detalle del acta con anillo SVG confianza + tabs 4 ámbitos."""
    is_super = bool(request.user and request.user.is_superuser)

    try:
        acta = Acta.objects.select_related("source_image", "election_process").get(pk=pk)
    except Acta.DoesNotExist:
        raise Http404("Acta no encontrada")

    _inject_legacy_meta([acta])

    if not isinstance(acta.layout_flags, dict):
        acta.layout_flags = {}

    # Última transcripción
    try:
        latest_trans = (
            acta.transcriptions.all().order_by("-created_at", "-pk").first()
        )
    except Exception:
        latest_trans = None

    # Vote entries agrupadas por ámbito
    votes_list = list(acta.vote_entries.all().order_by("ambito", "sort_order", "pk"))
    ambitos_order = ["muni_provincial", "muni_distrital", "gob_gobernador_vice", "consejero_regional"]
    ambito_labels = {
        "muni_provincial": "Municipal Provincial",
        "muni_distrital": "Municipal Distrital",
        "gob_gobernador_vice": "Gobernador + Vice",
        "consejero_regional": "Consejero Regional",
    }
    votes_by_ambito = {amb: [] for amb in ambitos_order}
    for entry in votes_list:
        if entry.ambito in votes_by_ambito:
            votes_by_ambito[entry.ambito].append(entry)

    # Validación aritmética por ámbito
    def sum_cat(entries, cat):
        for e in entries:
            if e.entry_category == cat:
                return int(e.votes or 0)
        return 0

    def sum_orgs(entries):
        return sum(int(e.votes or 0) for e in entries if e.entry_category == "organizacion")

    diffs_por_ambito = {}
    for amb in ambitos_order:
        entries = votes_by_ambito[amb]
        if not entries:
            diffs_por_ambito[amb] = None
            continue
        orgs = sum_orgs(entries)
        b = sum_cat(entries, "blanco")
        n = sum_cat(entries, "nulo")
        i = sum_cat(entries, "impugnado")
        t = sum_cat(entries, "total")
        suma = orgs + b + n + i
        diff_val = suma - t
        diffs_por_ambito[amb] = {
            "orgs": orgs, "blanco": b, "nulo": n,
            "impugnado": i, "total": t,
            "suma_computed": suma, "diff": diff_val,
            "ok": bool(t > 0 and diff_val == 0),
        }

    # Alertas
    try:
        alerts = list(acta.alerts.all().order_by("-created_at")[:20])
    except Exception:
        alerts = []

    # Review queue
    try:
        review_item = (
            acta.review_items
            .filter(status__in=("pending", "abierto", "en_revision"))
            .order_by("-created_at")
            .first()
        )
    except Exception:
        review_item = None

    # Confidence
    from .signals import confidence_level as _conf_level_fn
    conf_level = _conf_level_fn(acta.confidence_score)
    conf_pct = None
    if acta.confidence_score is not None:
        conf_pct = round(100.0 * float(acta.confidence_score), 1)

    # Timeline actividad
    events = []
    try:
        status_display = acta.get_status_display()
    except Exception:
        status_display = str(acta.status)
    try:
        events.append({
            "kind": "created",
            "dot": "info",
            "label": "Acta creada en sistema",
            "when": acta.created_at,
            "detail": "Registro inicial del acta en la base de datos",
        })
        if latest_trans:
            events.append({
                "kind": "trans",
                "dot": "ok",
                "label": "Transcripción IA completada",
                "when": latest_trans.created_at,
                "detail": (
                    f"{str(latest_trans.provider or '').upper()} · "
                    f"{latest_trans.model or ''} · "
                    f"conf={float(latest_trans.global_confidence or 0):.0%}"
                ),
            })
        if acta.status in ("procesado_ok", "revisado_ok"):
            status_dot = "ok"
        elif acta.status == "observado":
            status_dot = "warn"
        elif acta.status == "error":
            status_dot = "danger"
        else:
            status_dot = "info"
        events.append({
            "kind": "status",
            "dot": status_dot,
            "label": f"Estado actual: {status_display}",
            "when": acta.updated_at,
            "detail": "Última actualización del registro",
        })
        if review_item:
            events.append({
                "kind": "review",
                "dot": "warn",
                "label": "Ingresada a cola de revisión humana",
                "when": review_item.created_at,
                "detail": str(review_item.reason or "")[:120],
            })
    except Exception:
        pass

    events_sorted = [e for e in events if e.get("when")]
    events_sorted.sort(key=lambda e: e["when"], reverse=True)

    ambito_has_entries = any(vs for vs in votes_by_ambito.values())

    ctx = {
        "is_super": is_super,
        "nav_active": "procesadas",
        "acta": acta,
        "conf_level": conf_level,
        "conf_pct": conf_pct,
        "latest_trans": latest_trans,
        "ambitos_order": ambitos_order,
        "ambito_labels": ambito_labels,
        "votes_by_ambito": votes_by_ambito,
        "diffs_por_ambito": diffs_por_ambito,
        "alerts": alerts,
        "review_item": review_item,
        "timeline_events": events_sorted[:12],
        "ambito_has_entries": ambito_has_entries,
        "review_exists": bool(review_item is not None),
    }
    ctx.update(_sidebar_badge_counts(request))
    return render(request, "core/acta_detail.html", ctx)


@login_required
def serve_acta_image_protected(request, acta_pk: int):
    """FASE E: Sirve la imagen original del acta SOLO a usuarios autenticados.

    Prioriza ActaImage.source_image (FK) y si no existe, hace fallback al
    file_path_disco de la tabla legada ActaEscrutinioLegacy.
    """
    try:
        acta = Acta.objects.select_related("source_image").get(pk=acta_pk)
    except Acta.DoesNotExist:
        raise Http404("Acta no encontrada")

    file_path = None
    if acta.source_image and hasattr(acta.source_image, "file_path") and acta.source_image.file_path:
        file_path = acta.source_image.file_path
    elif acta.acta_escrutinio_legacy_id:
        try:
            leg = ActaEscrutinioLegacy.objects.only("file_path_disco").get(pk=acta.acta_escrutinio_legacy_id)
            file_path = leg.file_path_disco
        except ActaEscrutinioLegacy.DoesNotExist:
            pass

    if not file_path or not os.path.exists(file_path):
        raise Http404("Imagen no disponible en disco")

    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type:
        mime_type = "image/jpeg"
    try:
        fh = open(file_path, "rb")
    except OSError:
        raise Http404("No se puede leer la imagen")
    filename = smart_str(f"acta_{acta_pk}{os.path.splitext(file_path)[1] or '.jpg'}")
    return FileResponse(fh, content_type=mime_type, as_attachment=False, filename=filename)


@login_required
def review_split_view(request, acta_pk: int):
    """FASE E: Vista dividida para revisión humana de actas con baja confianza.

    Layout: mitad IZQ imagen original con zoom/rotate/maximize · mitad DER
    formulario editable por ámbito con guardado AJAX en blur por campo.
    """
    is_super = bool(request.user and request.user.is_superuser)

    try:
        acta = Acta.objects.select_related("source_image", "election_process").get(pk=acta_pk)
    except Acta.DoesNotExist:
        raise Http404("Acta no encontrada")

    _inject_legacy_meta([acta])
    if not isinstance(acta.layout_flags, dict):
        acta.layout_flags = {}

    # Última transcripción
    try:
        latest_trans = acta.transcriptions.all().order_by("-created_at", "-pk").first()
    except Exception:
        latest_trans = None

    # Asegurar que exista un ManualReviewQueue pendiente para esta acta
    try:
        review_item = (
            ManualReviewQueue.objects
            .filter(acta=acta, status__in=("pending", "abierto", "en_revision"))
            .order_by("-created_at")
            .first()
        )
        if not review_item:
            review_item = ManualReviewQueue.objects.create(
                acta=acta,
                status="pending",
                reason="Ingreso manual a revisión humana",
                reason_codes=["manual_entry"],
                transcription=latest_trans,
            )
    except Exception:
        review_item = None

    # Vote entries agrupadas por ámbito (lista ordenada para evitar lookup dinámico en template)
    votes_list = list(acta.vote_entries.all().order_by("ambito", "sort_order", "pk"))
    ambitos_order = ["muni_provincial", "muni_distrital", "gob_gobernador_vice", "consejero_regional"]
    ambito_labels = {
        "muni_provincial": "Municipal Provincial",
        "muni_distrital": "Municipal Distrital",
        "gob_gobernador_vice": "Gobernador + Vice",
        "consejero_regional": "Consejero Regional",
    }
    votes_by_ambito = {amb: [] for amb in ambitos_order}
    for entry in votes_list:
        if entry.ambito in votes_by_ambito:
            votes_by_ambito[entry.ambito].append(entry)

    # Lista ordenada (amb_key, entries, label, count) para iterar directamente en template sin filtros dinámicos
    ambitos_grouped = []
    for amb in ambitos_order:
        entries = votes_by_ambito.get(amb, [])
        ambitos_grouped.append({
            "key": amb,
            "entries": entries,
            "label": ambito_labels.get(amb, amb),
            "count": len(entries),
        })

    # Valores originales para poder comparar (ID entry → dict con votes/conf/organizacion)
    original_values = {}
    for e in votes_list:
        original_values[e.id] = {
            "votes": int(e.votes or 0),
            "field_confidence": float(e.field_confidence or 0.0),
            "entry_category": e.entry_category,
            "is_corrected": bool(e.is_corrected),
        }

    # Confidence level para banner superior
    from .signals import confidence_level as _conf_level_fn
    conf_level = _conf_level_fn(acta.confidence_score)
    conf_pct = None
    if acta.confidence_score is not None:
        conf_pct = round(100.0 * float(acta.confidence_score), 1)

    image_url = reverse("serve_acta_image", kwargs={"acta_pk": acta_pk})

    ctx = {
        "is_super": is_super,
        "nav_active": "procesadas",
        "acta": acta,
        "review_item": review_item,
        "latest_trans": latest_trans,
        "ambitos_order": ambitos_order,
        "ambito_labels": ambito_labels,
        "ambitos_grouped": ambitos_grouped,
        "votes_by_ambito": votes_by_ambito,
        "original_values_json": json.dumps(original_values, ensure_ascii=False),
        "image_url": image_url,
        "conf_level": conf_level,
        "conf_pct": conf_pct,
        "review_save_api_url": reverse("review_save_api", kwargs={"pk": acta_pk}),
        "review_finalize_url": reverse("review_finalize_api", kwargs={"acta_pk": acta_pk}),
    }
    ctx.update(_sidebar_badge_counts(request))
    return render(request, "core/review_split.html", ctx)


@login_required
@require_http_methods(["POST"])
def review_save_corrections_api(request, pk: int):
    """FASE E: Guarda la corrección individual de un campo (votos).

    Endpoint AJAX JSON. Recibe:
        { vote_entry_id: int, field_name: str ("votes"), new_value: int }

    Actualiza ActaVoteEntry.is_corrected=True + field_confidence=1.0
    + data_origin="humano" y crea fila HumanCorrection para auditoría.

    Retorna JSON con ok=True y header X-Toast para feedback instantáneo.
    """
    try:
        acta = Acta.objects.get(pk=pk)
    except Acta.DoesNotExist:
        resp = JsonResponse({"ok": False, "error": "Acta no encontrada"}, status=404)
        resp["X-Toast"] = json.dumps({"m": "Acta no encontrada", "t": "error"}, ensure_ascii=False)
        return resp

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        resp = JsonResponse({"ok": False, "error": "Cuerpo JSON inválido"}, status=400)
        resp["X-Toast"] = json.dumps({"m": "Solicitud inválida", "t": "error"}, ensure_ascii=False)
        return resp

    vote_entry_id = payload.get("vote_entry_id")
    field_name = payload.get("field_name", "votes")
    new_value_raw = payload.get("new_value")

    if not vote_entry_id or field_name not in ("votes",):
        resp = JsonResponse({"ok": False, "error": "Parámetros faltantes"}, status=400)
        resp["X-Toast"] = json.dumps({"m": "Faltan parámetros para guardar", "t": "error"}, ensure_ascii=False)
        return resp

    try:
        new_value_int = int(new_value_raw)
        if new_value_int < 0:
            raise ValueError("negativo")
    except (TypeError, ValueError):
        resp = JsonResponse({"ok": False, "error": "Valor inválido (debe ser entero ≥ 0)"}, status=400)
        resp["X-Toast"] = json.dumps({"m": "Valor inválido, use entero ≥ 0", "t": "error"}, ensure_ascii=False)
        return resp

    try:
        vote_entry = ActaVoteEntry.objects.get(pk=vote_entry_id, acta__pk=pk)
    except ActaVoteEntry.DoesNotExist:
        resp = JsonResponse({"ok": False, "error": "Registro de voto no existe en el acta"}, status=404)
        resp["X-Toast"] = json.dumps({"m": "Registro no encontrado", "t": "error"}, ensure_ascii=False)
        return resp

    old_value = str(getattr(vote_entry, field_name) or 0)
    ia_value = old_value

    if str(new_value_int) == old_value and vote_entry.is_corrected:
        resp = JsonResponse({"ok": True, "saved": 0, "info": "sin_cambios", "new_value": new_value_int})
        resp["X-Toast"] = json.dumps({"m": "Sin cambios", "t": "info"}, ensure_ascii=False)
        return resp

    setattr(vote_entry, field_name, new_value_int)
    vote_entry.is_corrected = True
    vote_entry.field_confidence = 1.0
    vote_entry.data_origin = "humano"
    vote_entry.save(update_fields=[field_name, "is_corrected", "field_confidence", "data_origin", "updated_at"])

    # Crear fila de corrección humana
    try:
        review_item = ManualReviewQueue.objects.filter(
            acta=acta, status__in=("pending", "abierto", "en_revision")
        ).order_by("-created_at").first()
    except Exception:
        review_item = None

    try:
        HumanCorrection.objects.create(
            review_item=review_item,
            acta=acta,
            vote_entry=vote_entry,
            field_name=field_name,
            old_value=old_value,
            new_value=str(new_value_int),
            ia_value=ia_value,
            corrected_by=request.user,
            comment="",
        )
    except Exception as exc:
        log.warning("No se pudo crear HumanCorrection para acta %s entry %s: %s", pk, vote_entry_id, exc)

    resp = JsonResponse({"ok": True, "saved": 1, "new_value": new_value_int, "entry_id": vote_entry_id})
    resp["X-Toast"] = json.dumps(
        {"m": f"Campo guardado: {old_value} → {new_value_int}", "t": "success"},
        ensure_ascii=False,
    )
    return resp


@login_required
@require_http_methods(["POST"])
def review_finalize_api(request, acta_pk: int):
    """FASE E: Cierra la revisión de un acta y la marca como revisado_ok.

    Actualiza ManualReviewQueue.status="revisado" + Acta.status="revisado_ok"
    + crea AuditLog entry action="approve".
    Redirige al detalle del acta con ancla #ok.
    """
    from django.utils import timezone as dj_tz

    try:
        acta = Acta.objects.get(pk=acta_pk)
    except Acta.DoesNotExist:
        raise Http404("Acta no encontrada")

    try:
        review_item = ManualReviewQueue.objects.filter(
            acta=acta, status__in=("pending", "abierto", "en_revision")
        ).order_by("-created_at").first()
    except Exception:
        review_item = None

    now_ts = dj_tz.now()

    if review_item:
        ManualReviewQueue.objects.filter(pk=review_item.pk).update(
            status="revisado",
            closed_at=now_ts,
            closed_by=request.user,
            assigned_to=request.user,
            resolution_notes="Revisión cerrada desde vista split",
        )

    Acta.objects.filter(pk=acta_pk).update(
        status="revisado_ok",
        updated_at=now_ts,
    )

    try:
        AuditLog.objects.create(
            actor=request.user,
            action="approve",
            model_name="Acta",
            object_id=str(acta_pk),
            detail={"message": "Revisión aprobada desde split review"},
            ip_address=request.META.get("REMOTE_ADDR", None) or None,
            user_agent=(request.META.get("HTTP_USER_AGENT", "") or "")[:500],
        )
    except Exception as exc:
        log.warning("AuditLog no creado en review_finalize acta %s: %s", acta_pk, exc)

    return redirect(reverse("acta_detail", kwargs={"pk": acta_pk}) + "?status=reviewed#ok")


# ======================================================================
#  FASE C — Dashboard Tiempo Real (HTMX poll + JSON para Chart.js)
# ======================================================================

def _compute_snapshot():
    """Helper reusable: calcula todos los agregados para dashboard en vivo.

    Retorna dict con:
        kpis, status_donut, ambitos_bar, timeline_line, confidence_bands, totales_ambito.
    """
    actas_qs = Acta.objects.all()

    # 1) KPIs principales
    total_actas = actas_qs.count()
    ok = actas_qs.filter(status="procesado_ok").count()
    revisado = actas_qs.filter(status="revisado_ok").count()
    obs = actas_qs.filter(status="observado").count()
    err = actas_qs.filter(status="error").count()
    pend = actas_qs.filter(status="pendiente").count()
    proc = actas_qs.filter(status="procesando").count()

    total_legacy = 0
    procesados_legacy_count = 0
    try:
        total_legacy = ActaEscrutinioLegacy.objects.count()
        procesados_ids = set(
            actas_qs.exclude(acta_escrutinio_legacy_id__isnull=True)
            .values_list("acta_escrutinio_legacy_id", flat=True)
            .distinct()
        )
        procesados_legacy_count = len(procesados_ids)
    except Exception:
        pass

    try:
        _, _, batch_ids = _compute_legacy_pending_ids()
        legacy_pendientes = len(batch_ids)
    except Exception:
        legacy_pendientes = max(0, total_legacy - procesados_legacy_count)

    kpis = {
        "total": total_actas,
        "ok": ok,
        "revisado": revisado,
        "observado": obs,
        "error": err,
        "pendiente": pend,
        "procesando": proc,
        "legacy_total": total_legacy,
        "legacy_procesados": procesados_legacy_count,
        "legacy_pendientes": legacy_pendientes,
    }

    # 2) Donut: 6 estados
    status_donut = {
        "labels": ["Procesado OK", "Revisado OK", "Observado", "Error", "Pendiente", "Procesando"],
        "data": [ok, revisado, obs, err, pend, proc],
        "colors": ["#16a34a", "#6366f1", "#f59e0b", "#dc2626", "#0ea5e9", "#8b5cf6"],
    }

    # 3) Barras 4 ámbitos (suma organizaciones en actas con status exitosos)
    ambitos_list = [
        ("muni_provincial", "Muni. Provincial"),
        ("muni_distrital", "Muni. Distrital"),
        ("gob_gobernador_vice", "Gobernador + Vice"),
        ("consejero_regional", "Consejero Regional"),
    ]
    amb_ok_qs = ActaVoteEntry.objects.filter(
        acta__status__in=["procesado_ok", "revisado_ok", "observado"],
        entry_category="organizacion",
    )
    amb_labels = []
    amb_data = []
    totales_ambito = {}
    for amb_key, amb_label in ambitos_list:
        suma = (
            amb_ok_qs.filter(ambito=amb_key)
            .aggregate(total=Coalesce(Sum("votes"), 0, output_field=IntegerField()))
            ["total"] or 0
        )
        amb_labels.append(amb_label)
        amb_data.append(int(suma))
        totales_ambito[amb_key] = int(suma)

    ambitos_bar = {"labels": amb_labels, "data": amb_data}

    # 4) Timeline: últimas 5 horas bucket x hora (exitosos vs errores)
    now = datetime.now(timezone.utc)
    tz_now = now.astimezone() if True else now
    # Tomamos la hora actual redondeada al inicio + 4h anteriores = 5 buckets
    base_bucket = tz_now.replace(minute=0, second=0, microsecond=0)
    hours = [base_bucket - timedelta(hours=i) for i in range(4, -1, -1)]
    timeline_labels = [h.strftime("%H:%M") for h in hours]
    timeline_ok = []
    timeline_err = []
    for h_start in hours:
        h_end = h_start + timedelta(hours=1)
        window_q = Q(updated_at__gte=h_start, updated_at__lt=h_end)
        c_ok = actas_qs.filter(window_q, status__in=["procesado_ok", "revisado_ok"]).count()
        c_err = actas_qs.filter(window_q, status__in=["error", "observado"]).count()
        timeline_ok.append(int(c_ok))
        timeline_err.append(int(c_err))

    timeline_line = {
        "labels": timeline_labels,
        "ok": timeline_ok,
        "err": timeline_err,
    }

    # 5) Bandas de confianza (aplica solo a actas procesadas no-error)
    from .signals import THRESHOLD_HIGH, THRESHOLD_MEDIUM  # importar aquí para circular-safe
    conf_qs = actas_qs.filter(status__in=["procesado_ok", "revisado_ok", "observado"])
    c_high = conf_qs.filter(confidence_score__gte=THRESHOLD_HIGH).count()
    c_med = conf_qs.filter(confidence_score__gte=THRESHOLD_MEDIUM, confidence_score__lt=THRESHOLD_HIGH).count()
    c_low = (
        conf_qs.filter(Q(confidence_score__lt=THRESHOLD_MEDIUM) | Q(confidence_score__isnull=True)).count()
    )
    confidence_bands = {
        "labels": ["Alta ≥90%", "Media 70–89%", "Baja <70%"],
        "data": [int(c_high), int(c_med), int(c_low)],
        "colors": ["#16a34a", "#f59e0b", "#dc2626"],
    }

    return {
        "kpis": kpis,
        "status_donut": status_donut,
        "ambitos_bar": ambitos_bar,
        "timeline_line": timeline_line,
        "confidence_bands": confidence_bands,
        "totales_ambito": totales_ambito,
    }


@login_required
def dashboard_stats_partial(request):
    """Fragmento HTMX poll: retorna KPI grid + charts wrappers (sin layout)
    cada ~15s. Usado con hx-poll + hx-swap outerHTML."""
    snap = _compute_snapshot()
    kpis = snap["kpis"]
    totales = snap["totales_ambito"]

    # Tipo count quick (reutilizado en cards)
    actas_qs = Acta.objects.all()
    tipo_count = {}
    for row in actas_qs.values("acta_type").order_by("acta_type").distinct():
        t = row["acta_type"] or "desconocido"
        tipo_count[t] = actas_qs.filter(acta_type=row["acta_type"]).count()

    ctx = {
        "kpi": kpis,
        "totales_ambito": totales,
        "por_tipo": tipo_count,
        "is_super": bool(request.user and request.user.is_superuser),
    }
    ctx.update(_sidebar_badge_counts(request))
    return render(request, "core/partials/dashboard_stats_partial.html", ctx)


@login_required
@require_http_methods(["GET"])
def api_progress_snapshot(request):
    """Endpoint JSON para Chart.js. Retorna 4 datasets + KPIs."""
    snap = _compute_snapshot()
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "kpis": snap["kpis"],
        "status_donut": snap["status_donut"],
        "ambitos_bar": snap["ambitos_bar"],
        "timeline_line": snap["timeline_line"],
        "confidence_bands": snap["confidence_bands"],
    }
    resp = JsonResponse(data, json_dumps_params={"ensure_ascii": False})
    resp["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


@login_required
@require_http_methods(["GET"])
def resultados_view(request):
    """Vista de resultados electorales (estilo ONPE).

    Filtros encadenados: Departamento → Provincia → Distrito con recarga
    server-side. Sidebar 4 cargos: Gobernador, Consejero, Provincial, Distrital.
    KPI 4 tarjetas: procesadas, contabilizadas, electores, participación.
    Chart.js: barras verticales por organización política + tabla resumen.
    """
    depto_sel = request.GET.get("depto", "").strip()
    prov_sel = request.GET.get("prov", "").strip()
    dist_sel = request.GET.get("dist", "").strip()
    cargo_sel = request.GET.get("cargo", "gob_gobernador_vice").strip()

    ambitos_order = ["muni_provincial", "muni_distrital", "gob_gobernador_vice", "consejero_regional"]
    if cargo_sel not in ambitos_order:
        cargo_sel = "gob_gobernador_vice"
    ambito_labels = {
        "muni_provincial": "Municipal Provincial",
        "muni_distrital": "Municipal Distrital",
        "gob_gobernador_vice": "Gobernador y Vicegobernador",
        "consejero_regional": "Consejero Regional",
    }

    # 1) Valores únicos para 3 selects encadenados (legacy mesa)
    try:
        all_ubi = list(
            MesaLegacy.objects
            .values("departamento", "provincia", "distrito")
            .distinct()
            .order_by("departamento", "provincia", "distrito")
        )
    except Exception:
        all_ubi = []

    deptos = sorted({r["departamento"] for r in all_ubi if r.get("departamento")})

    if depto_sel:
        provincias = sorted({r["provincia"] for r in all_ubi if r.get("departamento") == depto_sel and r.get("provincia")})
    else:
        provincias = sorted({r["provincia"] for r in all_ubi if r.get("provincia")})

    if prov_sel:
        distritos = sorted({r["distrito"] for r in all_ubi if r.get("provincia") == prov_sel and r.get("distrito")})
    else:
        distritos = sorted({r["distrito"] for r in all_ubi if r.get("distrito")})

    # 2) Filtrar MesaLegacy → num_mesa → IDs actas_escrutinio_legacy
    mesa_nums_filter = []
    try:
        qs_mesas = MesaLegacy.objects.all()
        if depto_sel:
            qs_mesas = qs_mesas.filter(departamento=depto_sel)
        if prov_sel:
            qs_mesas = qs_mesas.filter(provincia=prov_sel)
        if dist_sel:
            qs_mesas = qs_mesas.filter(distrito=dist_sel)
        mesa_nums_filter = list(qs_mesas.values_list("num_mesa", flat=True))
    except Exception:
        mesa_nums_filter = []

    legacy_ids_actas = []
    if mesa_nums_filter:
        try:
            legacy_ids_actas = list(
                ActaEscrutinioLegacy.objects
                .filter(mesa_numero__in=mesa_nums_filter)
                .values_list("id", flat=True)
            )
        except Exception:
            legacy_ids_actas = []

    # 3) Base queries: actas VPS + entries
    try:
        actas_qs = Acta.objects.all()
        if legacy_ids_actas:
            actas_qs = actas_qs.filter(acta_escrutinio_legacy_id__in=legacy_ids_actas)
    except Exception:
        actas_qs = Acta.objects.none()

    actas_procesadas_qs = actas_qs.filter(status__in={"procesado_ok", "revisado_ok", "observado"})

    # KPIs
    try:
        if mesa_nums_filter:
            total_actas_legacy = ActaEscrutinioLegacy.objects.filter(mesa_numero__in=mesa_nums_filter).count()
        else:
            total_actas_legacy = ActaEscrutinioLegacy.objects.count()
    except Exception:
        total_actas_legacy = 0

    kpi_actas_procesadas = actas_qs.filter(status__in={"procesado_ok", "revisado_ok"}).count()
    kpi_actas_contabilizadas = actas_procesadas_qs.count()
    try:
        num_mesas = len(mesa_nums_filter) if mesa_nums_filter else MesaLegacy.objects.count()
    except Exception:
        num_mesas = 0
    kpi_electores = num_mesas * 300

    chart_labels = []
    chart_data = []
    chart_colors = []
    chart_logo_urls = []
    table_org_rows = []
    sum_validos = 0
    sum_blancos = 0
    sum_nulos = 0
    sum_impugnados = 0
    sum_total = 0
    ganador_nombre = ""
    ganador_votos = 0

    # ---------- Resolver logotipos de organizaciones (fuzzy match NFKD) ----------
    from .templatetags.confidence_tags import _ascii_norm
    _po_name_to_logo = {}
    _po_norm_names = []
    try:
        for po in PoliticalOrganization.objects.filter(is_active=True).only(
            "short_name", "full_name", "acronym", "code", "logo_image", "logo_path"
        ):
            candidates = [po.short_name, po.full_name, po.acronym, po.code]
            url = po.resolved_logo_url if hasattr(po, "resolved_logo_url") else None
            if not url and po.logo_image:
                try:
                    url = po.logo_image.url
                except Exception:
                    url = None
            if not url and po.logo_path:
                url = po.logo_path
            for cn in candidates:
                if cn:
                    n = _ascii_norm(cn)
                    if n:
                        _po_name_to_logo[n] = url
                        _po_norm_names.append(n)
    except Exception:
        _po_norm_names = []

    def _resolve_logo(org_name: str):
        """Fuzzy match nombre organización → URL logo (misma técnica confidence_tags)."""
        if not org_name:
            return None
        q_norm = _ascii_norm(org_name)
        if not q_norm:
            return None
        if q_norm in _po_name_to_logo:
            return _po_name_to_logo[q_norm]
        # contains either way
        for n in _po_norm_names:
            if (n and q_norm in n) or (q_norm and n in q_norm):
                return _po_name_to_logo.get(n)
        return None

    try:
        acta_pks = list(actas_procesadas_qs.values_list("pk", flat=True))
        if acta_pks:
            entries_qs = (
                ActaVoteEntry.objects
                .filter(acta_id__in=acta_pks, ambito=cargo_sel)
                .select_related("organization")
            )

            org_votes = {}
            for e in entries_qs:
                cat = e.entry_category
                if cat == "organizacion":
                    oid = e.organization_id or f"sin_org_{e.pk}"
                    if oid not in org_votes:
                        e_logo = None
                        try:
                            if e.organization and hasattr(e.organization, "resolved_logo_url"):
                                e_logo = e.organization.resolved_logo_url
                            if not e_logo and e.organization and getattr(e.organization, "logo_image", None):
                                e_logo = e.organization.logo_image.url
                        except Exception:
                            e_logo = None
                        try:
                            oname = e.organization_short_name or ""
                        except Exception:
                            oname = ""
                        if not oname:
                            try:
                                oname = e.organization.short_name if e.organization else ""
                            except Exception:
                                oname = ""
                        if not oname:
                            oname = e.candidate_name or "N/A"
                        try:
                            ofull = e.organization.name if e.organization else oname
                        except Exception:
                            ofull = oname
                        inicial = (oname.strip() or "?")[:1].upper()
                        if not e_logo:
                            e_logo = _resolve_logo(oname) or _resolve_logo(ofull)
                        org_votes[oid] = {
                            "name": oname,
                            "full": ofull,
                            "votes": 0,
                            "inicial": inicial,
                            "logo_url": e_logo or "",
                        }
                    org_votes[oid]["votes"] += int(e.votes or 0)
                elif cat == "blanco":
                    sum_blancos += int(e.votes or 0)
                elif cat == "nulo":
                    sum_nulos += int(e.votes or 0)
                elif cat == "impugnado":
                    sum_impugnados += int(e.votes or 0)
                elif cat == "total":
                    sum_total += int(e.votes or 0)

            palette = ["#005eb8", "#0ea5e9", "#6366f1", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#14b8a6", "#84cc16", "#f97316"]
            org_sorted = sorted(org_votes.values(), key=lambda r: r["votes"], reverse=True)
            for idx, row in enumerate(org_sorted):
                chart_labels.append(row["name"])
                chart_data.append(row["votes"])
                chart_colors.append(palette[idx % len(palette)])
                logo_url = row.get("logo_url") or ""
                if not logo_url:
                    logo_url = _resolve_logo(row["name"]) or _resolve_logo(row["full"]) or ""
                chart_logo_urls.append(logo_url or None)
                sum_validos += row["votes"]
                if idx == 0:
                    ganador_nombre = row["name"]
                    ganador_votos = row["votes"]
                table_org_rows.append({
                    "inicial": row["inicial"],
                    "name": row["full"],
                    "votos": row["votes"],
                    "logo_url": logo_url,
                    "short_name": row["name"],
                })
    except Exception as ex:
        log.warning("resultados_view aggregate error: %s", ex)

    total_emitidos_filtro = sum_validos + sum_blancos + sum_nulos + sum_impugnados
    kpi_participacion = round((total_emitidos_filtro / kpi_electores) * 100, 2) if kpi_electores > 0 else 0

    # Fecha actualización en zona horaria Perú (America/Lima = UTC-5)
    try:
        from datetime import timezone as tz
        lima_tz = tz(timedelta(hours=-5))
        fecha_actualizacion = datetime.now(lima_tz).strftime("%d/%m/%Y %H:%M h")
    except Exception:
        fecha_actualizacion = datetime.now().strftime("%d/%m/%Y %H:%M h")

    if dist_sel:
        ubica_label = dist_sel
        tipo_region = "distritales"
    elif prov_sel:
        ubica_label = prov_sel
        tipo_region = "provinciales"
    elif depto_sel:
        ubica_label = depto_sel
        tipo_region = "regionales"
    else:
        ubica_label = "Todo el ámbito"
        tipo_region = "regionales"

    context = _sidebar_badge_counts(request)
    y_axis_max = None
    if chart_data:
        try:
            y_axis_max = max(1, int(max(chart_data) * 1.22))
        except Exception:
            y_axis_max = None
    context.update({
        "nav_active": "resultados",
        "is_super": getattr(request.user, "is_superuser", False),
        "depto_sel": depto_sel,
        "prov_sel": prov_sel,
        "dist_sel": dist_sel,
        "cargo_sel": cargo_sel,
        "deptos": deptos,
        "provincias": provincias,
        "distritos": distritos,
        "ambitos_order": ambitos_order,
        "ambito_labels": ambito_labels,
        "kpi_actas_procesadas": kpi_actas_procesadas,
        "kpi_actas_procesadas_pct": round((kpi_actas_procesadas / total_actas_legacy) * 100, 3) if total_actas_legacy > 0 else 0,
        "kpi_actas_contabilizadas": kpi_actas_contabilizadas,
        "kpi_actas_contabilizadas_pct": round((kpi_actas_contabilizadas / total_actas_legacy) * 100, 3) if total_actas_legacy > 0 else 0,
        "kpi_electores": kpi_electores,
        "kpi_participacion": kpi_participacion,
        "fecha_actualizacion": fecha_actualizacion,
        "tipo_region": tipo_region,
        "ubica_label": ubica_label,
        "cargo_label": ambito_labels.get(cargo_sel, cargo_sel),
        "ganador_nombre": ganador_nombre,
        "ganador_votos": ganador_votos,
        "chart_labels_json": json.dumps(chart_labels, ensure_ascii=False),
        "chart_data_json": json.dumps(chart_data, ensure_ascii=False),
        "chart_colors_json": json.dumps(chart_colors, ensure_ascii=False),
        "chart_logo_urls_json": json.dumps(chart_logo_urls, ensure_ascii=False),
        "chart_y_max": y_axis_max,
        "table_org_rows": table_org_rows,
        "sum_validos": sum_validos,
        "sum_blancos": sum_blancos,
        "sum_nulos": sum_nulos,
        "sum_impugnados": sum_impugnados,
        "sum_total_emitidos": max(sum_total, total_emitidos_filtro),
        "total_actas_legacy": total_actas_legacy,
    })
    return render(request, "core/resultados.html", context)


# ==========================================================================
# MÓDULO GESTIÓN ORGANIZACIONES POLÍTICAS (SUPERADMIN-ONLY)
# 5 endpoints: list_view / edit_api / upload_logo / reorder / delete_logo
# ==========================================================================

def _superadmin_gate_or_403(request):
    """Retorna (ok_bool, response_or_None)."""
    if not request.user.is_authenticated:
        return False, JsonResponse({"error": "Autenticación requerida"}, status=401)
    if not getattr(request.user, "is_superuser", False):
        return False, JsonResponse({"error": "Permiso denegado: rol SUPER_ADMIN requerido"}, status=403)
    return True, None


@login_required
def org_manage_list(request):
    """Vista principal del módulo (SUPERADMIN only).

    Renderiza plantilla con listado arrastrable SortableJS, KPIs, acciones
    de edición inline, subida de logo y botón guardar-orden.
    """
    is_super = bool(request.user and request.user.is_superuser)
    if not is_super:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("Acceso restringido a SUPER_ADMIN")

    orgs = list(PoliticalOrganization.objects.all().order_by("sort_order", "short_name"))
    total_activas = sum(1 for o in orgs if o.is_active)
    con_logo = sum(1 for o in orgs if o.resolved_logo_url)

    context = _sidebar_badge_counts(request)
    context.update({
        "nav_active": "organizaciones",
        "is_super": is_super,
        "orgs": orgs,
        "kpi_total": len(orgs),
        "kpi_activas": total_activas,
        "kpi_con_logo": con_logo,
        "kpi_sin_logo": len(orgs) - con_logo,
    })
    return render(request, "core/org_manage.html", context)


@login_required
@require_http_methods(["POST"])
def org_edit_api(request, pk):
    """Editar campos inline de una organización: short_name, full_name, code,
    acronym, scope, color, is_active. Responde JSON."""
    ok, err_resp = _superadmin_gate_or_403(request)
    if not ok:
        return err_resp
    try:
        org = PoliticalOrganization.objects.get(pk=pk)
    except PoliticalOrganization.DoesNotExist:
        return JsonResponse({"error": f"Organización #{pk} no existe"}, status=404)

    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON inválido"}, status=400)

    allowed_fields = {"short_name", "full_name", "code", "acronym", "scope", "color", "is_active"}
    updated = []
    for field, val in data.items():
        if field not in allowed_fields:
            continue
        if field == "is_active":
            val = bool(val)
        elif field in {"scope"}:
            val = str(val or "mixto")[:20]
        elif field == "color":
            val = (str(val or "").strip())[:9]
        elif field == "code":
            val = (str(val or "").strip())[:24]
        elif field == "acronym":
            val = (str(val or "").strip())[:16]
        elif field == "short_name":
            val = (str(val or "").strip())[:64]
        elif field == "full_name":
            val = (str(val or "").strip())[:240]
        setattr(org, field, val)
        updated.append(field)
    try:
        org.save(update_fields=updated + ["updated_at"] if updated else None)
    except Exception as exc:
        log.exception("org_edit_api save error")
        return JsonResponse({"error": f"Error al guardar: {exc}"}, status=500)

    return JsonResponse({
        "ok": True,
        "updated": updated,
        "resolved_logo_url": org.resolved_logo_url or "",
        "sort_order": org.sort_order,
    })


@login_required
@require_http_methods(["POST"])
def org_upload_logo_api(request, pk):
    """Subir logo personalizado (PNG/JPEG/WEBP ≤ 2MB). Verifica con PIL.

    Request: multipart/form-data con campo 'file'. Devuelve JSON con URL final.
    """
    ok, err_resp = _superadmin_gate_or_403(request)
    if not ok:
        return err_resp
    try:
        org = PoliticalOrganization.objects.get(pk=pk)
    except PoliticalOrganization.DoesNotExist:
        return JsonResponse({"error": f"Organización #{pk} no existe"}, status=404)

    if "file" not in request.FILES:
        return JsonResponse({"error": "Falta archivo 'file'"}, status=400)
    f = request.FILES["file"]

    MAX_BYTES = 2 * 1024 * 1024
    if f.size > MAX_BYTES:
        return JsonResponse({"error": "Archivo excede 2MB"}, status=413)

    ALLOWED_MIME = {"image/png", "image/jpeg", "image/webp"}
    mime = getattr(f, "content_type", "") or ""
    if mime.lower() not in ALLOWED_MIME:
        return JsonResponse({"error": f"Formato no permitido: {mime}. Use PNG/JPEG/WEBP"}, status=415)

    try:
        from PIL import Image
        img = Image.open(f)
        img.verify()
        f.seek(0)
    except Exception as exc:
        log.warning("org_upload_logo PIL verify fail pk=%s: %s", pk, exc)
        return JsonResponse({"error": "Imagen corrupta o formato inválido"}, status=400)

    old_name = org.logo_image.name if org.logo_image else None
    safe_name = f"org_{pk}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    try:
        org.logo_image.save(safe_name, f, save=True)
    except Exception as exc:
        log.exception("org_upload_logo storage save pk=%s", pk)
        return JsonResponse({"error": f"Error almacenando archivo: {exc}"}, status=500)

    if old_name:
        try:
            from django.core.files.storage import default_storage
            if default_storage.exists(old_name):
                default_storage.delete(old_name)
        except Exception:
            pass

    return JsonResponse({
        "ok": True,
        "url": org.resolved_logo_url or "",
    })


@login_required
@require_http_methods(["POST"])
def org_reorder_api(request):
    """Guarda el orden de la lista por arrastre. Body JSON: {"order": [pk1,pk2,...]}.

    Realiza bulk_update sort_order con enumerate en una sola transacción.
    """
    ok, err_resp = _superadmin_gate_or_403(request)
    if not ok:
        return err_resp

    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON inválido"}, status=400)

    order = data.get("order") or []
    if not isinstance(order, list) or not order:
        return JsonResponse({"error": "Parámetro 'order' (lista PKs) requerido"}, status=400)
    try:
        order = [int(x) for x in order]
    except (TypeError, ValueError):
        return JsonResponse({"error": "'order' debe contener enteros PK"}, status=400)

    pk_set = set(order)
    objs = list(PoliticalOrganization.objects.filter(pk__in=pk_set))
    obj_by_pk = {o.pk: o for o in objs}

    missing = [pk for pk in order if pk not in obj_by_pk]
    if missing:
        return JsonResponse({"error": f"PKs no encontrados: {missing}"}, status=404)

    for idx, pk in enumerate(order):
        obj_by_pk[pk].sort_order = idx

    try:
        PoliticalOrganization.objects.bulk_update(objs, ["sort_order", "updated_at"])
    except Exception as exc:
        log.exception("org_reorder_api bulk_update")
        return JsonResponse({"error": f"Error al guardar orden: {exc}"}, status=500)

    return JsonResponse({"ok": True, "reordered": len(order)})


@login_required
@require_http_methods(["POST"])
def org_delete_logo_api(request, pk):
    """Elimina logo subido y campo logo_image=null. No toca logo_path legacy."""
    ok, err_resp = _superadmin_gate_or_403(request)
    if not ok:
        return err_resp
    try:
        org = PoliticalOrganization.objects.get(pk=pk)
    except PoliticalOrganization.DoesNotExist:
        return JsonResponse({"error": f"Organización #{pk} no existe"}, status=404)

    if org.logo_image:
        old_name = org.logo_image.name
        try:
            org.logo_image.delete(save=True)
        except Exception as exc:
            log.exception("org_delete_logo_api pk=%s", pk)
            return JsonResponse({"error": f"Error al eliminar: {exc}"}, status=500)
        _ = old_name
    return JsonResponse({"ok": True, "resolved_logo_url": org.resolved_logo_url or ""})


# ==========================================================================
# MÓDULO GESTIÓN ACTAS PROCESADAS (SUPERADMIN-ONLY)
# 3 endpoints: list_view / purgar_todo_api / eliminar_acta_api
# RESTRICCIÓN FUNDACIONAL: Solo se permite modificar tablas vps_* nuevas.
# PROHIBIDO TOCAR tablas legacy: usuarios, mesa, actas_escrutinio (managed=False)
# ==========================================================================

@login_required
@require_http_methods(["GET"])
def gestion_actas_list(request):
    """Vista principal listado buscador parametrizable actas procesadas (SUPERADMIN).

    Filtros: id acta (pk), mesa_numero, rango fechas procesamiento, confianza.
    Datatable server-side con paginador 25 filas. Botones eliminar individual.
    Botón global sticky PURGAR TODO (confirmación 2 pasos en modal).
    """
    if not getattr(request.user, "is_superuser", False):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("Acceso exclusivo para SUPER_ADMIN.")

    pk_q = request.GET.get("pk", "").strip()
    mesa_q = request.GET.get("mesa", "").strip()
    fecha_desde = request.GET.get("fecha_desde", "").strip()
    fecha_hasta = request.GET.get("fecha_hasta", "").strip()
    conf_q = request.GET.get("confianza", "").strip()

    qs = Acta.objects.select_related("source_image", "department", "province", "district").order_by("-created_at", "-pk")

    if pk_q and pk_q.isdigit():
        qs = qs.filter(pk=int(pk_q))
    if mesa_q:
        mesa_q_filters = Q(table_number__icontains=mesa_q)
        try:
            leg_matches = ActaEscrutinioLegacy.objects.filter(
                mesa_numero__icontains=mesa_q
            ).values_list("id", flat=True)
            leg_ids = list(leg_matches)
            if leg_ids:
                mesa_q_filters = mesa_q_filters | Q(acta_escrutinio_legacy_id__in=leg_ids)
        except Exception:
            pass
        qs = qs.filter(mesa_q_filters)
    if fecha_desde:
        try:
            dt_desde = datetime.strptime(fecha_desde, "%Y-%m-%d")
            qs = qs.filter(created_at__gte=dt_desde)
        except ValueError:
            pass
    if fecha_hasta:
        try:
            dt_hasta = datetime.strptime(fecha_hasta, "%Y-%m-%d") + timedelta(days=1)
            qs = qs.filter(created_at__lt=dt_hasta)
        except ValueError:
            pass
    if conf_q == "high":
        qs = qs.filter(confidence_score__gte=0.85)
    elif conf_q == "medium":
        qs = qs.filter(confidence_score__gte=0.60, confidence_score__lt=0.85)
    elif conf_q == "low":
        qs = qs.filter(Q(confidence_score__lt=0.60) | Q(confidence_score__isnull=True))

    total_qs = qs.count()

    try:
        legacy_join_map = {}
        leg_ids = [a.acta_escrutinio_legacy_id for a in qs if a.acta_escrutinio_legacy_id]
        if leg_ids:
            leg_rows = ActaEscrutinioLegacy.objects.filter(id__in=leg_ids).values("id", "mesa_numero")
            mesa_nums_leg = [r["mesa_numero"] for r in leg_rows if r.get("mesa_numero")]
            leg_map = {r["id"]: r["mesa_numero"] for r in leg_rows}
            if mesa_nums_leg:
                mesa_rows = MesaLegacy.objects.filter(num_mesa__in=mesa_nums_leg).values(
                    "num_mesa", "departamento", "provincia", "distrito", "nombre_local"
                )
                mesa_map = {r["num_mesa"]: r for r in mesa_rows}
                for lid, mnum in leg_map.items():
                    legacy_join_map[lid] = {"mesa": mnum, **mesa_map.get(mnum, {})}
    except Exception:
        legacy_join_map = {}

    for a in qs:
        lj = legacy_join_map.get(a.acta_escrutinio_legacy_id or -1, {})
        a.v_mesa = lj.get("mesa") or a.table_number or "-"
        a.v_departamento = lj.get("departamento") or (a.department.name if hasattr(a, 'department') and a.department else getattr(a, 'department', None) or "-")
        a.v_provincia = lj.get("provincia") or (a.province.name if hasattr(a, 'province') and a.province else getattr(a, 'province', None) or "-")
        a.v_distrito = lj.get("distrito") or (a.district.name if hasattr(a, 'district') and a.district else getattr(a, 'district', None) or "-")
        a.v_local = lj.get("nombre_local") or "-"
        try:
            a.v_has_review = ManualReviewQueue.objects.filter(acta_id=a.pk).exists()
        except Exception:
            a.v_has_review = False

    paginator = Paginator(qs, PAGINATOR_PER_PAGE)
    try:
        page = int(request.GET.get("page", 1))
    except ValueError:
        page = 1
    try:
        page_obj = paginator.page(page)
    except (EmptyPage, InvalidPage):
        page_obj = paginator.page(paginator.num_pages)

    try:
        kpi_total = Acta.objects.count()
        kpi_images = ActaImage.objects.count()
        kpi_transcr = ActaTranscription.objects.count()
        kpi_votes = ActaVoteEntry.objects.count()
        kpi_review_q = ManualReviewQueue.objects.count()
        kpi_corrections = HumanCorrection.objects.count()
    except Exception:
        kpi_total = kpi_images = kpi_transcr = kpi_votes = kpi_review_q = kpi_corrections = 0

    context = _sidebar_badge_counts(request)
    context.update({
        "nav_active": "gestion_actas",
        "is_super": True,
        "pk_q": pk_q,
        "mesa_q": mesa_q,
        "fecha_desde": fecha_desde,
        "fecha_hasta": fecha_hasta,
        "conf_q": conf_q,
        "page_obj": page_obj,
        "total_qs": total_qs,
        "kpi_total": kpi_total,
        "kpi_images": kpi_images,
        "kpi_transcr": kpi_transcr,
        "kpi_votes": kpi_votes,
        "kpi_review_q": kpi_review_q,
        "kpi_corrections": kpi_corrections,
    })
    return render(request, "core/gestion_actas.html", context)


@login_required
@require_http_methods(["POST"])
def purgar_todo_procesados_api(request):
    """Elimina TODOS los registros de las 6 tablas vps_* de procesamiento.

    Body JSON requerido: {confirmar: true, declaracion_no_legacy: true}
    2 checks obligatorios + flag de que ningún otro sistema depende de estas tablas.

    ORDEN deletes FK-safe (hijos primero, padres después):
      1. HumanCorrection (depende de acta vía FK directa/indirecta)
      2. ManualReviewQueue (FK a Acta)
      3. ActaVoteEntry (FK a Acta + Transcription)
      4. ActaTranscription (FK a Acta)
      5. ActaImage (FK a Acta)
      6. Acta (padre)

    NUNCA se tocan tablas legacy managed=False: usuarios, mesa, actas_escrutinio.
    """
    ok, err_resp = _superadmin_gate_or_403(request)
    if not ok:
        return err_resp

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON inválido"}, status=400)

    if not payload.get("confirmar") or not payload.get("declaracion_no_legacy"):
        return JsonResponse({
            "error": "Se requieren ambas confirmaciones: confirmar + declaracion_no_legacy."
        }, status=400)

    models_order = [
        ("HumanCorrection", HumanCorrection),
        ("ManualReviewQueue", ManualReviewQueue),
        ("ActaVoteEntry", ActaVoteEntry),
        ("ActaTranscription", ActaTranscription),
        ("ActaImage", ActaImage),
        ("Acta", Acta),
    ]

    counts_before = {}
    try:
        for mname, M in models_order:
            try:
                counts_before[mname] = M.objects.count()
            except Exception:
                counts_before[mname] = 0
    except Exception:
        pass

    deleted_counts = {}
    total_rows = 0
    try:
        with transaction.atomic():
            for mname, M in models_order:
                try:
                    n, _ = M.objects.all().delete()
                except Exception as exc:
                    log.error("purgar_todo error en %s: %s", mname, exc)
                    raise
                deleted_counts[mname] = n
                total_rows += n
            try:
                AuditLog.objects.create(
                    action="delete",
                    actor=request.user if request.user.is_authenticated else None,
                    model_name="VpsAllProcesados",
                    object_id="",
                    ip_address=request.META.get("REMOTE_ADDR", None) or None,
                    user_agent=(request.META.get("HTTP_USER_AGENT", "") or "")[:500],
                    detail={
                        "operation": "PURGAR_TODO_PROCESADOS",
                        "deleted_counts": deleted_counts,
                        "total_rows": total_rows,
                        "confirmar": True,
                        "declaracion_no_legacy": True,
                    },
                )
            except Exception:
                log.warning("AuditLog PURGAR_TODO_PROCESADOS skip", exc_info=True)
    except Exception as exc:
        log.exception("purgar_todo_procesados_api transacción fallida")
        return JsonResponse({
            "error": f"Error durante purga (rollback ejecutado): {exc}",
            "deleted_counts": deleted_counts,
        }, status=500)

    return JsonResponse({
        "ok": True,
        "deleted_counts": deleted_counts,
        "counts_before": counts_before,
        "total_rows": total_rows,
        "msg": f"Purga completada. {total_rows} filas eliminadas en 6 tablas vps_* (tablas legacy intactas).",
    })


@login_required
@require_http_methods(["POST"])
def eliminar_acta_api(request, pk: int):
    """Elimina UN SOLO registro Acta y su cascada de hijos FK.

    Body JSON: {confirmar: true}
    Django ORM on_delete=CASCADE se encarga de eliminar hijos:
      ActaImage, ActaTranscription, ActaVoteEntry, ManualReviewQueue, HumanCorrection.
    """
    ok, err_resp = _superadmin_gate_or_403(request)
    if not ok:
        return err_resp

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON inválido"}, status=400)

    if not payload.get("confirmar"):
        return JsonResponse({"error": "Confirmación requerida"}, status=400)

    try:
        acta = Acta.objects.get(pk=pk)
    except Acta.DoesNotExist:
        return JsonResponse({"error": f"Acta #{pk} no existe"}, status=404)

    deleted_counts = {}
    total_rows = 0
    child_checks = [
        ("ActaImage", lambda: ActaImage.objects.filter(acta=acta).count()),
        ("ActaTranscription", lambda: ActaTranscription.objects.filter(acta=acta).count()),
        ("ActaVoteEntry", lambda: ActaVoteEntry.objects.filter(acta=acta).count()),
        ("ManualReviewQueue", lambda: ManualReviewQueue.objects.filter(acta=acta).count()),
        ("HumanCorrection", lambda: HumanCorrection.objects.filter(
            review_queue__acta=acta
        ).count() if hasattr(HumanCorrection, "review_queue") else 0),
    ]
    for mname, fn in child_checks:
        try:
            deleted_counts[mname] = fn()
        except Exception:
            deleted_counts[mname] = 0
        total_rows += deleted_counts[mname]

    try:
        with transaction.atomic():
            n, per_model = acta.delete()
            total_rows = n or total_rows
            try:
                AuditLog.objects.create(
                    action="delete",
                    actor=request.user if request.user.is_authenticated else None,
                    model_name="Acta",
                    object_id=str(pk),
                    ip_address=request.META.get("REMOTE_ADDR", None) or None,
                    user_agent=(request.META.get("HTTP_USER_AGENT", "") or "")[:500],
                    detail={
                        "operation": "ELIMINAR_ACTA_INDIVIDUAL",
                        "acta_pk": pk,
                        "deleted_counts": deleted_counts,
                        "per_model_summary": {k: v for k, v in (per_model or {}).items()},
                        "total_rows": total_rows,
                    },
                )
            except Exception:
                log.warning("AuditLog ELIMINAR_ACTA_INDIVIDUAL skip", exc_info=True)
    except Exception as exc:
        log.exception("eliminar_acta_api pk=%s falló", pk)
        return JsonResponse({"error": f"Error: {exc}"}, status=500)

    return JsonResponse({
        "ok": True,
        "acta_pk": pk,
        "deleted_counts": deleted_counts,
        "total_rows": total_rows,
        "msg": f"Acta #{pk} y {total_rows - 1} registros relacionados eliminados (cascade).",
    })


@login_required
@require_http_methods(["POST"])
def ai_provider_save_api(request):
    from .models import AIProviderSettings
    ok, err_resp = _superadmin_gate_or_403(request)
    if not ok:
        return err_resp
    provider = (request.POST.get("provider") or "").strip().lower()
    if provider not in {"gemini", "deepseek"}:
        return JsonResponse({"ok": False, "msg": "Proveedor inválido (gemini/deepseek)."}, status=400)
    try:
        s = AIProviderSettings.objects.get(provider=provider)
    except AIProviderSettings.DoesNotExist:
        s = AIProviderSettings(provider=provider, is_default=(provider == "gemini"))
    raw_key = request.POST.get("api_key_raw") or ""
    if raw_key:
        s.api_key = raw_key
    model_sel = (request.POST.get("model") or "").strip()
    if model_sel:
        s.model = model_sel
    try:
        s.temperature = float(request.POST.get("temperature") or 0.0)
    except (TypeError, ValueError):
        s.temperature = 0.0
    try:
        s.timeout_secs = max(10, min(600, int(request.POST.get("timeout_secs") or 60)))
    except (TypeError, ValueError):
        s.timeout_secs = 60
    try:
        s.max_retries = max(0, min(10, int(request.POST.get("max_retries") or 3)))
    except (TypeError, ValueError):
        s.max_retries = 3
    s.is_active = bool(request.POST.get("is_active") in {"on", "1", "true", "True"}) or bool(raw_key and len(raw_key) >= 10)
    s.updated_by = request.user if request.user.is_authenticated else None
    try:
        with transaction.atomic():
            s.save()
    except Exception as exc:
        log.exception("ai_provider_save_api guardar falló")
        return JsonResponse({"ok": False, "msg": f"No guardar: {exc}"}, status=500)
    try:
        AuditLog.objects.create(
            action="update",
            model_name="AIProviderSettings",
            object_id=provider,
            actor=request.user if request.user.is_authenticated else None,
            ip_address=request.META.get("REMOTE_ADDR", None) or None,
            user_agent=(request.META.get("HTTP_USER_AGENT", "") or "")[:500],
            detail={"provider": provider, "active": s.is_active, "model": s.model, "temp": s.temperature, "timeout": s.timeout_secs},
        )
    except Exception:
        log.warning("AuditLog AIProvider save skip", exc_info=True)
    return JsonResponse({
        "ok": True,
        "provider": provider,
        "active": s.is_active,
        "model": s.model,
        "api_key_len": len(s.api_key or ""),
    })


@login_required
@require_http_methods(["POST"])
def ai_provider_test_api(request, provider: str):
    from .ai import get_provider_for_settings
    from .models import AIProviderSettings
    ok, err_resp = _superadmin_gate_or_403(request)
    if not ok:
        return err_resp
    provider = (provider or "").strip().lower()
    if provider not in {"gemini", "deepseek"}:
        return JsonResponse({"ok": False, "msg": "Proveedor inválido."}, status=400)
    try:
        s = AIProviderSettings.objects.get(provider=provider)
    except AIProviderSettings.DoesNotExist:
        return JsonResponse({"ok": False, "msg": "Proveedor no existe en DB."}, status=404)
    if not s.api_key:
        return JsonResponse({"ok": False, "msg": "API key NO configurada (longitud 0)."})
    try:
        inst = get_provider_for_settings(s)
    except Exception as exc:
        return JsonResponse({"ok": False, "msg": f"No instanciar provider: {exc}"})
    head_ok, head_ms = inst.ping() if hasattr(inst, "ping") and callable(getattr(inst, "ping", None)) else (True, "ping not implemented - key stored")
    return JsonResponse({
        "ok": bool(head_ok),
        "msg": head_ms or ("Proveedor responde OK." if head_ok else "Fallo ping proveedor (revisa key)."),
        "provider": provider,
        "active": s.is_active,
        "model": s.model,
    })


# ==========================================================================
# MÓDULO CONFIGURACIÓN (SUPERADMIN ONLY)
# ==========================================================================

@login_required
def config_view(request):
    """Vista de configuración singleton ProcessingConfig. Solo SUPER_ADMIN.

    GET: Muestra formulario con valores actuales de get_solo().
    POST: Guarda cambios, actualiza updated_by=request.user, crea AuditLog,
          messages.success y redirect a la misma vista (GET-after-POST).
    """
    from django.contrib import messages
    from django.http import HttpResponseRedirect
    from .models import ProcessingConfig
    from .forms import ProcessingConfigForm

    ok, err_resp = _superadmin_gate_or_403(request)
    if not ok:
        return err_resp

    instance = ProcessingConfig.get_solo()
    if request.method == "POST":
        form = ProcessingConfigForm(request.POST, instance=instance)
        if form.is_valid():
            changed = []
            for fname in form.changed_data:
                old_v = getattr(instance, fname, None)
                new_v = form.cleaned_data.get(fname)
                changed.append({"field": fname, "old": old_v, "new": new_v})
            form.instance.updated_by = request.user
            form.save()
            try:
                AuditLog.objects.create(
                    action="update",
                    model_name="ProcessingConfig",
                    object_id=instance.pk,
                    actor=request.user,
                    detail={"changes": changed},
                )
            except Exception:
                log.warning("AuditLog UPDATE_PROCESSING_CONFIG skip", exc_info=True)
            messages.success(request, "Configuración de procesamiento guardada correctamente.")
            return HttpResponseRedirect(reverse("config_view"))
    else:
        form = ProcessingConfigForm(instance=instance)

    from .models import AIProviderSettings
    providers = list(AIProviderSettings.objects.all().order_by("-is_default", "id"))
    if not providers:
        from .apps import CoreConfig
        try:
            CoreConfig()._ensure_ai_provider_defaults()
        except Exception:
            pass
        providers = list(AIProviderSettings.objects.all().order_by("-is_default", "id"))
    GEMINI_MODEL_CHOICES = [
        ("gemini-3.6-flash", "Gemini 3.6 Flash (recomendado)"),
        ("gemini-3.5-flash", "Gemini 3.5 Flash"),
        ("gemini-2.0-flash", "Gemini 2.0 Flash"),
        ("gemini-1.5-flash", "Gemini 1.5 Flash"),
        ("gemini-1.5-pro-latest", "Gemini 1.5 Pro"),
    ]
    DEEPSEEK_MODEL_CHOICES = [
        ("deepseek-flash", "DeepSeek Flash (recomendado)"),
        ("deepseek-v4-pro", "DeepSeek V4 Pro"),
        ("deepseek-reasoner", "DeepSeek Reasoner (R1)"),
    ]
    ctx = {
        "nav_active": "config",
        "is_super": True,
        "form": form,
        "config": instance,
        "ai_providers": providers,
        "api_key_len": {str(p.provider): len(p.api_key or "") for p in providers},
        "GEMINI_MODEL_CHOICES": GEMINI_MODEL_CHOICES,
        "DEEPSEEK_MODEL_CHOICES": DEEPSEEK_MODEL_CHOICES,
        "active_provider_exists": any(p.is_active and len(p.api_key or "") >= 10 for p in providers),
    }
    ctx.update(_sidebar_badge_counts(request))
    return render(request, "core/configuracion_new.html", ctx)


@login_required
@require_http_methods(["POST"])
def config_toggle_autobatch_api(request):
    """Flip (toggle) del flag auto_batch_enabled. Responde JSON. SUPER_ADMIN only."""
    ok, err_resp = _superadmin_gate_or_403(request)
    if not ok:
        return JsonResponse({"ok": False, "error": "Permiso denegado"}, status=403)

    cfg, cfg_error = _get_processing_config_safe()
    if cfg is None:
        return JsonResponse({"ok": False, "error": f"No carga config: {cfg_error}", "requires_action": "migrate_processing_config"}, status=500)

    cfg.auto_batch_enabled = not bool(cfg.auto_batch_enabled)
    cfg.updated_by = request.user
    new_val = bool(cfg.auto_batch_enabled)
    try:
        with transaction.atomic():
            cfg.save(update_fields=["auto_batch_enabled", "updated_by", "updated_at"])
    except Exception as exc:
        log.exception("TOGGLE_AUTO_BATCH save falló")
        return JsonResponse({"ok": False, "error": f"No guardar config: {exc}"}, status=500)
    try:
        AuditLog.objects.create(
            action="update",
            model_name="ProcessingConfig",
            object_id=cfg.pk,
            actor=request.user if request.user.is_authenticated else None,
            ip_address=request.META.get("REMOTE_ADDR", None) or None,
            user_agent=(request.META.get("HTTP_USER_AGENT", "") or "")[:500],
            detail={"auto_batch_enabled": cfg.auto_batch_enabled, "trigger": "dashboard_widget_switch"},
        )
    except Exception:
        log.warning("AuditLog TOGGLE_AUTO_BATCH skip", exc_info=True)

    return JsonResponse({
        "ok": True,
        "auto_batch_enabled": cfg.auto_batch_enabled,
        "label": "Activo" if cfg.auto_batch_enabled else "Inactivo",
    })


def _serialize_batch_snapshot(batch_group: str):
    bg = (batch_group or "").strip()[:64]
    if not bg:
        return {
            "batch_group": "",
            "total": 0,
            "done": 0,
            "failed": 0,
            "running": 0,
            "pending": 0,
            "cancelled": 0,
            "finished": 0,
            "progress_pct": 0,
            "is_complete": False,
            "elapsed_total_label": "-",
            "eta_label": "-",
            "rows": [],
        }

    jobs_qs = ProcessingJob.objects.select_related("acta").filter(parameters__batch_group=bg).order_by("id")
    jobs = list(jobs_qs)
    total = len(jobs)
    done = sum(1 for j in jobs if j.status == "done")
    failed = sum(1 for j in jobs if j.status == "failed")
    running = sum(1 for j in jobs if j.status == "running")
    pending = sum(1 for j in jobs if j.status == "pending")
    cancelled = sum(1 for j in jobs if j.status == "cancelled")
    finished = done + failed + cancelled
    progress_pct = 0 if total == 0 else int(round(finished * 100.0 / total))
    is_complete = total > 0 and finished == total

    now = datetime.now(timezone.utc)
    started_any = [j.started_at for j in jobs if j.started_at]
    earliest = min(started_any) if started_any else None
    elapsed_total_secs = max(0, int((now - earliest).total_seconds())) if earliest else None
    avg_per_job_secs = max(1, int(elapsed_total_secs / max(1, done))) if (earliest and done > 0 and elapsed_total_secs is not None) else None
    eta_secs_remaining = (max(0, total - finished) * avg_per_job_secs) if avg_per_job_secs else None

    status_label_map = {
        "pending": "Pendiente",
        "running": "En proceso",
        "done": "Completado",
        "failed": "Fallido",
        "cancelled": "Cancelado",
    }
    rows = []
    for j in jobs[:20]:
        params = j.parameters if isinstance(j.parameters, dict) else {}
        leg_id = params.get("legacy_id") or (getattr(j.acta, "acta_escrutinio_legacy_id", None) if j.acta_id else None)
        mesa = params.get("mesa_numero") or (getattr(j.acta, "table_number", "") if j.acta_id else "") or "-"
        elapsed = None
        if j.started_at:
            ref = j.finished_at or now
            elapsed = int((ref - j.started_at).total_seconds())
        rows.append({
            "job_id": j.id,
            "legacy_id": leg_id,
            "acta_id": j.acta_id,
            "mesa_numero": str(mesa),
            "status": j.status,
            "status_label": status_label_map.get(j.status, j.status),
            "retry_count": getattr(j, "retry_count", 0) or 0,
            "elapsed_label": _fmt_mmss(elapsed),
            "error": (j.error_message or "")[:220],
        })

    return {
        "batch_group": bg,
        "total": total,
        "done": done,
        "failed": failed,
        "running": running,
        "pending": pending,
        "cancelled": cancelled,
        "finished": finished,
        "progress_pct": progress_pct,
        "is_complete": is_complete,
        "elapsed_total_label": _fmt_mmss(elapsed_total_secs),
        "eta_label": _fmt_mmss(eta_secs_remaining),
        "rows": rows,
    }


@login_required
@require_http_methods(["GET"])
def batch_status_api_json(request, batch_group: str):
    """Retorna JSON con progreso en tiempo real de un lote batch_group.

    Usa short-poll HTMX. Total, done/failed/running/pending + progress_pct +
    filas detalle por job (legacy_id, mesa_numero, status, elapsed, ETA).
    SUPER_ADMIN or is_staff gating.
    """
    is_ok = (getattr(request.user, "is_authenticated", False)
             and (getattr(request.user, "is_superuser", False) or getattr(request.user, "is_staff", False)))
    if not is_ok:
        return JsonResponse({"ok": False, "error": "Autenticación requerida"}, status=403)

    bg = (batch_group or "").strip()[:64]
    if not bg:
        return JsonResponse({"ok": False, "error": "batch_group faltante"}, status=400)
    payload = _serialize_batch_snapshot(bg)
    payload["ok"] = True
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    return JsonResponse(payload)


@login_required
@require_http_methods(["GET"])
def pipeline_monitor_api_json(request):
    is_ok = (getattr(request.user, "is_authenticated", False)
             and (getattr(request.user, "is_superuser", False) or getattr(request.user, "is_staff", False)))
    if not is_ok:
        return JsonResponse({"ok": False, "error": "Autenticación requerida"}, status=403)

    cfg, cfg_error = _get_processing_config_safe()
    requested_bg = (request.GET.get("batch_group") or "").strip()[:64]

    latest_jobs = list(ProcessingJob.objects.select_related("acta").order_by("-created_at")[:25])
    current_bg = requested_bg
    if not current_bg:
        for job in latest_jobs:
            params = job.parameters if isinstance(job.parameters, dict) else {}
            bg = (params.get("batch_group") or "").strip()
            if bg and job.status in {"pending", "running", "failed"}:
                current_bg = bg
                break
        if not current_bg:
            for job in latest_jobs:
                params = job.parameters if isinstance(job.parameters, dict) else {}
                bg = (params.get("batch_group") or "").strip()
                if bg:
                    current_bg = bg
                    break

    batch_payload = _serialize_batch_snapshot(current_bg) if current_bg else _serialize_batch_snapshot("")

    alerts_qs = ProcessingAlert.objects.select_related("acta", "job").filter(acknowledged=False).order_by("-created_at")[:8]
    alerts = [{
        "severity": a.severity,
        "alert_type": a.alert_type,
        "message": a.message[:180],
        "acta_id": a.acta_id,
        "job_id": a.job_id,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    } for a in alerts_qs]

    audit_qs = AuditLog.objects.order_by("-created_at")[:12]
    audit_rows = [{
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "action": a.action,
        "model_name": a.model_name,
        "actor": getattr(a.actor, "username", None),
        "detail": a.detail if isinstance(a.detail, dict) else {},
    } for a in audit_qs]

    job_rows = []
    for job in latest_jobs[:10]:
        params = job.parameters if isinstance(job.parameters, dict) else {}
        job_rows.append({
            "job_id": job.id,
            "status": job.status,
            "legacy_id": params.get("legacy_id"),
            "batch_group": params.get("batch_group"),
            "mesa_numero": params.get("mesa_numero") or (getattr(job.acta, "table_number", "") if job.acta_id else ""),
            "retry_count": job.retry_count or 0,
            "error": (job.error_message or "")[:160],
        })

    anomalies = []
    if cfg_error:
        anomalies.append({"severity": "critical", "message": f"Configuración batch no disponible: {cfg_error}"})
    if batch_payload.get("failed", 0) > 0:
        anomalies.append({"severity": "warning", "message": f"El lote actual registra {batch_payload['failed']} job(s) fallidos."})
    if alerts:
        anomalies.append({"severity": alerts[0]["severity"], "message": alerts[0]["message"]})

    return JsonResponse({
        "ok": True,
        "config": {
            "available": cfg is not None,
            "error": cfg_error,
            "auto_batch_enabled": bool(getattr(cfg, "auto_batch_enabled", False)) if cfg else False,
            "poll_progress_interval_secs": int(getattr(cfg, "poll_progress_interval_secs", 3) or 3) if cfg else 3,
            "max_batch_size_per_dispatch": int(getattr(cfg, "max_batch_size_per_dispatch", 8) or 8) if cfg else None,
        },
        "current_batch": batch_payload,
        "recent_jobs": job_rows,
        "recent_events": audit_rows,
        "alerts": alerts,
        "anomalies": anomalies,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })


def _fmt_mmss(total_secs) -> str:
    if total_secs is None:
        return "-"
    try:
        s = int(max(0, total_secs))
    except (TypeError, ValueError):
        return "-"
    h, rem = divmod(s, 3600)
    m, se = divmod(rem, 60)
    if h > 0:
        return f"{h:d}h {m:02d}m {se:02d}s"
    return f"{m:02d}m {se:02d}s"
