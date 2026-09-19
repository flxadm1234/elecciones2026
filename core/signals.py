"""Django signals para propagar confianza y colas de revisión automáticamente.

Umbrales canónicos (ver plan_mejoras_ui_profesional.md):
    THRESHOLD_HIGH   = 0.90  -> ALTA confianza (badge verde OK)
    THRESHOLD_MEDIUM = 0.70  -> MEDIA confianza (badge amarillo warning)
    score < 0.70        -> BAJA confianza, auto-ingresa a ManualReviewQueue
"""
from __future__ import annotations

import logging
from django.db.models.signals import post_save
from django.db.models import Avg, FloatField
from django.db.models.functions import Coalesce
from django.dispatch import receiver

from .models import Acta, ActaTranscription, ActaVoteEntry, ManualReviewQueue


log = logging.getLogger("core.signals")

THRESHOLD_HIGH: float = 0.90
THRESHOLD_MEDIUM: float = 0.70


def confidence_level(score: float | None) -> str:
    """Devuelve 'high' | 'medium' | 'low' según umbrales canónicos."""
    if score is None:
        return "low"
    if score >= THRESHOLD_HIGH:
        return "high"
    if score >= THRESHOLD_MEDIUM:
        return "medium"
    return "low"


@receiver(post_save, sender=ActaTranscription, dispatch_uid="acta_transcr_propagate_confidence")
def propagate_transcription_confidence(sender, instance: ActaTranscription, created: bool, **kwargs) -> None:
    """Cuando hay una nueva transcripción (o actualización), recalcular
    Acta.confidence_score consolidado y auto-ingresar a ManualReviewQueue
    si la confianza está por debajo del umbral bajo.

    Fórmula ponderada del plan:
        confidence_score = (0.7 * transcription.global_confidence)
                         + (0.3 * AVG(ActaVoteEntry.field_confidence por acta))
    """
    try:
        acta: Acta | None = Acta.objects.filter(pk=instance.acta_id).first()
        if acta is None:
            return

        t_conf = float(instance.global_confidence or 0.0)

        avg_field = (
            ActaVoteEntry.objects.filter(acta=acta)
            .aggregate(avg=Coalesce(Avg("field_confidence"), 0.0, output_field=FloatField()))
            ["avg"]
            or 0.0
        )
        score = round((0.7 * t_conf) + (0.3 * float(avg_field)), 4)

        # Actualizar solo si cambió, para evitar loops
        if acta.confidence_score != score:
            Acta.objects.filter(pk=acta.pk).update(confidence_score=score)
            log.info("Acta %s confidence_score actualizado a %.4f (t=%.3f avg_campo=%.3f)",
                     acta.pk, score, t_conf, avg_field)

        # Auto-ingresar a cola de revisión baja confianza
        if score < THRESHOLD_MEDIUM:
            obj, created_q = ManualReviewQueue.objects.get_or_create(
                acta=acta,
                defaults={
                    "status": "pending",
                    "reason": f"Confianza baja consolidada {score:.2%} < umbral {THRESHOLD_MEDIUM:.0%}",
                    "priority": 10 if score < 0.50 else 5,
                    "created_by": "system:low_confidence",
                },
            )
            if created_q:
                log.info("Acta %s agregada a ManualReviewQueue (score=%.4f)", acta.pk, score)
            elif obj.status == "pending":
                # Ya existía pendiente; asegurarse prioridad y razón
                update_kw = {}
                if obj.priority is None or obj.priority < (10 if score < 0.50 else 5):
                    update_kw["priority"] = 10 if score < 0.50 else 5
                if update_kw:
                    ManualReviewQueue.objects.filter(pk=obj.pk).update(**update_kw)
    except Exception:
        log.exception("Error propagando confianza para transcripción %s (acta=%s)",
                      instance.pk, getattr(instance, "acta_id", None))
