from __future__ import annotations

import logging
import socket
from datetime import timedelta

from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from django.db import connection, transaction
from django.utils import timezone

from .models import ProcessingJob, Acta, ProcessingConfig, ActaImage, AuditLog
from .models_legacy import ActaEscrutinioLegacy
from .processors import ActaProcessor
from .views import _ensure_legacy_pending_synced, _compute_legacy_pending_ids

logger = logging.getLogger("core.tasks")


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


def _enqueue_legacy_batch(legacy_ids: list[int], *, batch_group: str,
                         max_concurrent: int | None = None,
                         enqueue_all: bool = False) -> tuple[int, int]:
    """Crea ProcessingJob por acta legacy y lanza process_single_legacy_acta.delay.

    Si `max_concurrent` está seteado y `enqueue_all=False`, solo encola mientras
    `jobs running + queued < max_concurrent` (rate limit VPS). Retorna (enqueued, skipped).
    """
    if not legacy_ids:
        return 0, 0
    # #region debug-point B:enqueue-entry
    _emit_debug_event({
        "runId": "pre-fix",
        "hypothesisId": "B",
        "location": "core/tasks.py:_enqueue_legacy_batch",
        "msg": "[DEBUG] enqueue legacy batch start",
        "data": {
            "legacy_ids": len(legacy_ids),
            "batch_group": batch_group,
            "max_concurrent": max_concurrent,
            "enqueue_all": bool(enqueue_all),
        },
    })
    # #endregion
    proc = ActaProcessor()
    enqueued = 0
    skipped = 0
    for leg_id in legacy_ids:
        try:
            if (not enqueue_all) and max_concurrent and max_concurrent > 0:
                running = ProcessingJob.objects.filter(status__in={"pending", "running"}).count()
                if running >= max_concurrent:
                    skipped += (len(legacy_ids) - legacy_ids.index(leg_id))
                    break
            leg = ActaEscrutinioLegacy.objects.only("id", "file_path_disco", "mesa_numero", "tipo_acta").get(pk=leg_id)
            _ensure_legacy_pending_synced()
            try:
                acta = Acta.objects.get(acta_escrutinio_legacy_id=leg_id)
            except Acta.DoesNotExist:
                # Should not happen; create just in case
                from .views import _normalize_acta_type
                tipo = _normalize_acta_type(getattr(leg, "tipo_acta", None))
                mesa = (getattr(leg, "mesa_numero", "") or "").strip() or "NN"
                acta = Acta.objects.create(
                    unique_id=f"L{leg.id}_{mesa}_{tipo}",
                    acta_type=tipo,
                    table_number=mesa[:32],
                    status="pendiente",
                    acta_escrutinio_legacy_id=leg.id,
                    layout_flags={"_sync_source": "auto_enqueue_task"},
                    notes="Creado al encolar lote automático.",
                )
            img_path = getattr(leg, "file_path_disco", "") or ""
            img: ActaImage | None = None
            if img_path:
                try:
                    img = proc.register_image(img_path)
                except Exception:
                    logger.exception("register_image falló leg_id=%s path=%s", leg_id, img_path)
            params = {"legacy_id": leg_id, "batch_group": batch_group,
                      "mesa_numero": (getattr(leg, "mesa_numero", "") or "")[:32]}
            job: ProcessingJob = ProcessingJob.objects.create(
                acta=acta,
                acta_image=img,
                job_type="process_legacy_acta",
                status="pending",
                priority=5,
                parameters=params,
            )
            process_single_legacy_acta.delay(job.id, leg_id)
            enqueued += 1
            # #region debug-point B:enqueue-job-created
            _emit_debug_event({
                "runId": "pre-fix",
                "hypothesisId": "B",
                "location": "core/tasks.py:_enqueue_legacy_batch:job",
                "msg": "[DEBUG] processing job created",
                "data": {
                    "job_id": job.id,
                    "legacy_id": leg_id,
                    "acta_id": acta.id if acta else None,
                    "batch_group": batch_group,
                    "status": job.status,
                },
            })
            # #endregion
        except Exception:
            logger.exception("error enqueue leg_id=%s", leg_id)
            skipped += 1
            continue
    try:
        connection.close()
    except Exception:
        pass
    return enqueued, skipped


@shared_task(bind=True, acks_late=True, reject_on_worker_lost=True)
def process_single_legacy_acta(self, job_id: int, legacy_id: int):
    """Procesa UNA acta legacy (por ID de ProcessingJob)."""
    try:
        config = ProcessingConfig.get_solo()
    except Exception:
        config = None
    max_retries = getattr(config, "retry_count_job", 2) or 0
    if getattr(self, "request", None) and self.request.retries > max_retries:
        # Fallback (defensa) si max_retries@decorator != config
        pass
    hard_limit = int(getattr(config, "per_acta_timeout_secs", 90) or 90) + 30
    soft_limit = int(getattr(config, "per_acta_timeout_secs", 90) or 90)
    retry_backoff = int(getattr(config, "retry_backoff_secs", 45) or 45)

    retry_payload = None
    final_result: dict | None = None
    # #region debug-point B:job-execution-entry
    _emit_debug_event({
        "runId": "pre-fix",
        "hypothesisId": "B",
        "location": "core/tasks.py:process_single_legacy_acta:entry",
        "msg": "[DEBUG] process single legacy acta entry",
        "data": {
            "job_id": job_id,
            "legacy_id": legacy_id,
            "retries": getattr(getattr(self, "request", None), "retries", None),
        },
    })
    # #endregion
    try:
        with transaction.atomic():
            job = ProcessingJob.objects.filter(pk=job_id).select_for_update(of=("self",)).first()
            if job is None:
                logger.warning("process_single_legacy_acta: job %s no existe", job_id)
                return
            try:
                self.request.retries = min(self.request.retries, max_retries)
            except Exception:
                pass

            job.mark_start(worker_id=socket.gethostname()[:120])
            if job.acta_id:
                Acta.objects.filter(pk=job.acta_id).update(status="procesando", updated_at=timezone.now())
            try:
                params = job.parameters if isinstance(job.parameters, dict) else {}
                AuditLog.objects.create(
                    action="update",
                    model_name="ProcessingJob",
                    object_id=str(job.id),
                    detail={
                        "trigger": "job_started",
                        "batch_group": params.get("batch_group"),
                        "legacy_id": params.get("legacy_id"),
                        "mesa_numero": params.get("mesa_numero"),
                        "status": "running",
                    },
                )
            except Exception:
                logger.warning("audit job_started skip job_id=%s", job.id, exc_info=True)
            try:
                proc = ActaProcessor(job=job)
                result = proc.process_legacy_acta_escrutinio(legacy_id)
                if not result.ok:
                    raise Exception(result.error or "Proceso fallido sin mensaje")
                params = job.parameters if isinstance(job.parameters, dict) else {}
                job.mark_done({"legacy_id": legacy_id, "ok": True, "acta_id": result.acta_id, "batch_group": params.get("batch_group"), "status": result.status})
                final_result = {"job_id": job_id, "legacy_id": legacy_id, "status": "ok", "acta_id": result.acta_id}
                try:
                    elapsed_secs = None
                    if job.started_at and job.finished_at:
                        elapsed_secs = max(0, int((job.finished_at - job.started_at).total_seconds()))
                    AuditLog.objects.create(
                        action="update",
                        model_name="ProcessingJob",
                        object_id=str(job.id),
                        detail={
                            "trigger": "job_finished",
                            "batch_group": params.get("batch_group"),
                            "legacy_id": params.get("legacy_id"),
                            "mesa_numero": params.get("mesa_numero"),
                            "status": result.status or "done",
                            "duration_secs": elapsed_secs,
                        },
                    )
                except Exception:
                    logger.warning("audit job_finished skip job_id=%s", job.id, exc_info=True)
                # #region debug-point B:job-execution-done
                _emit_debug_event({
                    "runId": "pre-fix",
                    "hypothesisId": "B",
                    "location": "core/tasks.py:process_single_legacy_acta:done",
                    "msg": "[DEBUG] process single legacy acta done",
                    "data": {
                        "job_id": job_id,
                        "legacy_id": legacy_id,
                        "acta_id": result.acta_id,
                        "status": result.status,
                    },
                })
                # #endregion
            except MaxRetriesExceededError:
                job.mark_failed(f"Max retries exceeded ({max_retries})")
                if job.acta_id:
                    Acta.objects.filter(pk=job.acta_id).update(status="error", updated_at=timezone.now())
                final_result = {"job_id": job_id, "legacy_id": legacy_id, "status": "max_retries"}
            except Exception as exc:
                logger.exception("job_id=%s leg_id=%s exception", job_id, legacy_id)
                # #region debug-point B:job-execution-error
                _emit_debug_event({
                    "runId": "pre-fix",
                    "hypothesisId": "B",
                    "location": "core/tasks.py:process_single_legacy_acta:error",
                    "msg": "[DEBUG] process single legacy acta exception",
                    "data": {
                        "job_id": job_id,
                        "legacy_id": legacy_id,
                        "error": str(exc)[:220],
                    },
                })
                # #endregion
                try:
                    job.retry_count = (job.retry_count or 0) + 1
                    job.save(update_fields=["retry_count"])
                    if (job.retry_count or 0) <= max_retries:
                        countdown = retry_backoff * (job.retry_count or 1)
                        retry_payload = (exc, countdown, max_retries)
                    else:
                        job.mark_failed(str(exc))
                        if job.acta_id:
                            Acta.objects.filter(pk=job.acta_id).update(status="error", updated_at=timezone.now())
                        final_result = {"job_id": job_id, "legacy_id": legacy_id, "status": "failed", "error": str(exc)}
                        try:
                            params = job.parameters if isinstance(job.parameters, dict) else {}
                            AuditLog.objects.create(
                                action="update",
                                model_name="ProcessingJob",
                                object_id=str(job.id),
                                detail={
                                    "trigger": "job_failed",
                                    "batch_group": params.get("batch_group"),
                                    "legacy_id": params.get("legacy_id"),
                                    "mesa_numero": params.get("mesa_numero"),
                                    "status": "failed",
                                    "error": str(exc)[:220],
                                },
                            )
                        except Exception:
                            logger.warning("audit job_failed skip job_id=%s", job.id, exc_info=True)
                except MaxRetriesExceededError:
                    job.mark_failed(str(exc) or f"max retries {max_retries}")
                    if job.acta_id:
                        Acta.objects.filter(pk=job.acta_id).update(status="error", updated_at=timezone.now())
                    final_result = {"job_id": job_id, "legacy_id": legacy_id, "status": "max_retries"}
    except MaxRetriesExceededError:
        final_result = final_result or {"job_id": job_id, "legacy_id": legacy_id, "status": "max_retries"}

    if retry_payload is not None:
        try:
            connection.close()
        except Exception:
            pass
        exc, countdown, max_r = retry_payload
        raise self.retry(exc=exc, countdown=countdown, max_retries=max_r)
    return final_result or {"job_id": job_id, "legacy_id": legacy_id, "status": "failed", "error": "sin resultado"}


@shared_task(ignore_result=True)
def auto_dispatch_pending_batch():
    """Celery Beat task: cada N segundos encola nuevos lotes pendientes si auto=True."""
    try:
        config = ProcessingConfig.get_solo()
    except Exception as exc:
        logger.warning("ProcessingConfig no carga: %s", exc)
        return "config-missing"
    if not config.auto_batch_enabled:
        return "auto-disabled"
    try:
        _ensure_legacy_pending_synced()
        never, pend_syn, batch = _compute_legacy_pending_ids()
    except Exception:
        logger.exception("sync/batch falló en auto_dispatch")
        return "sync-error"
    if not batch:
        return "empty"
    ids = sorted(batch)[: int(config.max_batch_size_per_dispatch or 8)]
    bg = "autobg_" + timezone.now().strftime("%Y%m%d%H%M%S") + "_" + str(abs(hash(tuple(ids))) % 10_000)
    enq, skip = _enqueue_legacy_batch(ids, batch_group=bg,
                                      max_concurrent=int(config.max_concurrent or 2),
                                      enqueue_all=False)
    try:
        connection.close()
    except Exception:
        pass
    return f"enqueued={enq} skipped={skip} total_requested={len(ids)}"
