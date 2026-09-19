from datetime import datetime

from django import forms
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db.models import Q

from .models import ProcessingConfig, Acta, ACTA_STATUS, ACTA_TYPES, ActaVoteEntry


CONFIANZA_CHOICES = (
    ("", "Todos"),
    ("alta", "Alta (>= 0.70)"),
    ("baja", "Baja (< 0.70)"),
    ("sin_dato", "Sin calificar"),
)

AMBITO_FILTER_CHOICES = (("", "Todos"),) + ActaVoteEntry.AMBITO_CHOICES
STATUS_FILTER_CHOICES = (("", "Todos"),) + ACTA_STATUS
TIPO_FILTER_CHOICES = (("", "Todos"),) + ACTA_TYPES


class ActaFilterForm(forms.Form):
    q = forms.CharField(required=False, label="Búsqueda", max_length=200)
    status = forms.ChoiceField(required=False, choices=STATUS_FILTER_CHOICES, label="Estado")
    tipo_acta = forms.ChoiceField(required=False, choices=TIPO_FILTER_CHOICES, label="Tipo Acta")
    ambito = forms.ChoiceField(required=False, choices=AMBITO_FILTER_CHOICES, label="Ámbito")
    confianza = forms.ChoiceField(required=False, choices=CONFIANZA_CHOICES, label="Confianza")
    fecha_desde = forms.DateField(required=False, label="Desde", widget=forms.DateInput)
    fecha_hasta = forms.DateField(required=False, label="Hasta", widget=forms.DateInput)

    class Media:
        pass

    def filter_qs(self, qs):
        d = self.cleaned_data
        q = (d.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(table_number__icontains=q)
                | Q(unique_id__icontains=q)
                | Q(notes__icontains=q)
                | Q(district__name__icontains=q)
                | Q(province__name__icontains=q)
                | Q(department__name__icontains=q)
            ).distinct()
        if d.get("status"):
            qs = qs.filter(status=d["status"])
        if d.get("tipo_acta"):
            qs = qs.filter(acta_type=d["tipo_acta"])
        if d.get("ambito"):
            qs = qs.filter(vote_entries__ambito=d["ambito"]).distinct()
        if d.get("confianza") == "alta":
            qs = qs.filter(confidence_score__gte=0.70)
        elif d.get("confianza") == "baja":
            qs = qs.filter(confidence_score__lt=0.70, confidence_score__isnull=False)
        elif d.get("confianza") == "sin_dato":
            qs = qs.filter(confidence_score__isnull=True)
        if d.get("fecha_desde"):
            fd = datetime.combine(d["fecha_desde"], datetime.min.time())
            qs = qs.filter(created_at__gte=fd)
        if d.get("fecha_hasta"):
            fh = datetime.combine(d["fecha_hasta"], datetime.max.time())
            qs = qs.filter(created_at__lte=fh)
        return qs


class ProcessingConfigForm(forms.ModelForm):
    """Superadmin-only form: guardar settings singleton ProcessingConfig."""

    class Meta:
        model = ProcessingConfig
        fields = (
            "max_concurrent",
            "auto_batch_enabled",
            "auto_batch_interval_secs",
            "poll_progress_interval_secs",
            "max_batch_size_per_dispatch",
            "per_acta_timeout_secs",
            "retry_count_job",
            "retry_backoff_secs",
        )
        widgets = {
            "max_concurrent": forms.NumberInput(attrs={"min": 1, "max": 4, "class": "input", "step": 1, "inputmode": "numeric"}),
            "auto_batch_interval_secs": forms.NumberInput(attrs={"min": 10, "max": 600, "class": "input", "step": 1, "inputmode": "numeric"}),
            "poll_progress_interval_secs": forms.NumberInput(attrs={"min": 1, "max": 15, "class": "input", "step": 1, "inputmode": "numeric"}),
            "max_batch_size_per_dispatch": forms.NumberInput(attrs={"min": 1, "max": 30, "class": "input", "step": 1, "inputmode": "numeric"}),
            "per_acta_timeout_secs": forms.NumberInput(attrs={"min": 30, "max": 180, "class": "input", "step": 1, "inputmode": "numeric"}),
            "retry_count_job": forms.NumberInput(attrs={"min": 0, "max": 5, "class": "input", "step": 1, "inputmode": "numeric"}),
            "retry_backoff_secs": forms.NumberInput(attrs={"min": 10, "max": 600, "class": "input", "step": 1, "inputmode": "numeric"}),
            "auto_batch_enabled": forms.CheckboxInput(attrs={"class": "switch-input"}),
        }

    def clean_max_concurrent(self):
        v = int(self.cleaned_data.get("max_concurrent") or 1)
        if v > 4:
            raise forms.ValidationError("Por restricción del VPS, max 4 workers concurrentes.")
        return max(1, min(4, v))

    def clean_max_batch_size_per_dispatch(self):
        v = int(self.cleaned_data.get("max_batch_size_per_dispatch") or 1)
        return max(1, min(30, v))
