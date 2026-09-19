import hashlib
import os
from django.db import models
from django.contrib.auth import get_user_model
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone

from .crypto import encrypt_value, decrypt_value


User = get_user_model()

ACTA_TYPES = (
    ("regional", "Regional"),
    ("provincial_distrital", "Provincial / Distrital"),
)

ENTRY_CATEGORIES = (
    ("organizacion", "Organización Política"),
    ("blanco", "Voto en Blanco"),
    ("nulo", "Voto Nulo"),
    ("impugnado", "Voto Impugnado"),
    ("total", "Total Emitidos"),
    ("consejero", "Consejero Regional"),
    ("gobernador", "Gobernador Regional"),
    ("provincial", "Candidato Provincial"),
    ("distrital", "Candidato Distrital"),
)

ACTA_STATUS = (
    ("pendiente", "Pendiente"),
    ("procesando", "Procesando"),
    ("procesado_ok", "Procesado OK"),
    ("observado", "Observado"),
    ("revisado_ok", "Revisado y Aprobado"),
    ("duplicado", "Duplicado"),
    ("error", "Error de Proceso"),
)

VALIDATION_STATUS = (
    ("pendiente", "Pendiente"),
    ("aprobado", "Validado"),
    ("suma_mal", "Suma No Cuadra"),
    ("catalogo_mal", "Organizaciones Inválidas"),
    ("duplicado", "Duplicado"),
    ("baja_confianza", "Baja Confianza"),
    ("error_ia", "Error en Motor IA"),
)

JOB_STATUS = (
    ("pending", "Pendiente"),
    ("running", "En Ejecución"),
    ("done", "Completado"),
    ("failed", "Fallido"),
    ("cancelled", "Cancelado"),
)

ALERT_SEVERITY = (
    ("info", "Informativo"),
    ("warning", "Advertencia"),
    ("error", "Error"),
    ("critical", "Crítico"),
)

REVIEW_STATUS = (
    ("abierto", "Abierto"),
    ("en_revision", "En Revisión"),
    ("resuelto", "Resuelto"),
    ("cerrado", "Cerrado"),
)


class TimeStampMixin(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class GeoDepartment(TimeStampMixin):
    code = models.CharField(max_length=4, unique=True)
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "vps_geo_departments"
        ordering = ["name"]
        verbose_name = "Departamento"
        verbose_name_plural = "Departamentos"

    def __str__(self):
        return f"{self.code} - {self.name}"


class GeoProvince(TimeStampMixin):
    department = models.ForeignKey(GeoDepartment, on_delete=models.PROTECT, related_name="provinces")
    code = models.CharField(max_length=8)
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "vps_geo_provinces"
        unique_together = (("department", "code"),)
        ordering = ["department__code", "code"]
        verbose_name = "Provincia"
        verbose_name_plural = "Provincias"

    def __str__(self):
        return f"{self.department.code}-{self.code} {self.name}"


class GeoDistrict(TimeStampMixin):
    province = models.ForeignKey(GeoProvince, on_delete=models.PROTECT, related_name="districts")
    ubigeo = models.CharField(max_length=6, unique=True)
    name = models.CharField(max_length=120)
    has_district_election = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "vps_geo_districts"
        ordering = ["ubigeo"]
        verbose_name = "Distrito"
        verbose_name_plural = "Distritos"

    def __str__(self):
        return f"{self.ubigeo} - {self.name}"


class PoliticalOrganization(TimeStampMixin):
    SCOPE_CHOICES = (
        ("regional", "Regional"),
        ("provincial", "Provincial"),
        ("distrital", "Distrital"),
        ("nacional", "Nacional"),
        ("mixto", "Mixto"),
    )
    code = models.CharField(max_length=24, unique=True)
    short_name = models.CharField(max_length=64)
    full_name = models.CharField(max_length=240)
    acronym = models.CharField(max_length=16, blank=True, default="")
    scope = models.CharField(max_length=20, choices=SCOPE_CHOICES, default="mixto")
    color = models.CharField(max_length=9, blank=True, default="")
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    logo_path = models.CharField(max_length=255, blank=True, default="")
    logo_image = models.ImageField(upload_to="org_logos/", blank=True, null=True, max_length=255)

    class Meta:
        db_table = "vps_political_organizations"
        ordering = ["sort_order", "short_name"]
        verbose_name = "Organización Política"
        verbose_name_plural = "Organizaciones Políticas"

    def __str__(self):
        return f"{self.code} - {self.short_name}"

    @property
    def resolved_logo_url(self):
        if self.logo_image and hasattr(self.logo_image, "url") and self.logo_image.name:
            try:
                return self.logo_image.url
            except (ValueError, AttributeError):
                pass
        if self.logo_path:
            return self.logo_path
        return None


class ElectionProcess(TimeStampMixin):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=180)
    process_date = models.DateField()
    has_regional = models.BooleanField(default=True)
    has_provincial = models.BooleanField(default=True)
    has_distrital = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    description = models.TextField(blank=True, default="")

    class Meta:
        db_table = "vps_election_processes"
        ordering = ["-process_date"]
        verbose_name = "Proceso Electoral"
        verbose_name_plural = "Procesos Electorales"

    def __str__(self):
        return f"{self.code} - {self.name}"


class ActaImage(TimeStampMixin):
    AVAILABILITY = (
        ("available", "Disponible"),
        ("missing", "Faltante"),
        ("corrupt", "Corrupto"),
        ("moved", "Movido"),
    )
    file_path = models.CharField(max_length=512)
    file_hash = models.CharField(max_length=64, unique=True)
    file_size = models.PositiveBigIntegerField(default=0)
    mime_type = models.CharField(max_length=64, blank=True, default="")
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    availability = models.CharField(max_length=20, choices=AVAILABILITY, default="available")
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    metadata_json = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "vps_acta_images"
        verbose_name = "Imagen de Acta"
        verbose_name_plural = "Imágenes de Actas"

    def __str__(self):
        return f"{self.id} {os.path.basename(self.file_path)}"

    @staticmethod
    def compute_hash(path: str) -> str:
        h = hashlib.sha256()
        if not os.path.exists(path):
            return ""
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()


class Acta(TimeStampMixin):
    unique_id = models.CharField(max_length=96, db_index=True)
    acta_type = models.CharField(max_length=24, choices=ACTA_TYPES)
    election_process = models.ForeignKey(ElectionProcess, on_delete=models.PROTECT, related_name="actas", null=True, blank=True)
    department = models.ForeignKey(GeoDepartment, on_delete=models.PROTECT, null=True, blank=True)
    province = models.ForeignKey(GeoProvince, on_delete=models.PROTECT, null=True, blank=True)
    district = models.ForeignKey(GeoDistrict, on_delete=models.PROTECT, null=True, blank=True)
    table_number = models.CharField(max_length=32, blank=True, default="")
    status = models.CharField(max_length=24, choices=ACTA_STATUS, default="pendiente")
    source_image = models.ForeignKey(ActaImage, on_delete=models.PROTECT, related_name="actas", null=True, blank=True)
    duplicate_of = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    acta_escrutinio_legacy_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    layout_flags = models.JSONField(default=dict, blank=True, null=True)
    confidence_score = models.FloatField(
        default=None, null=True, blank=True, db_index=True,
        help_text="Nivel de confianza consolidado (0.0-1.0). 0.7*transcription + 0.3*avg campo. <0.70 entra a revisión humana.",
    )

    class Meta:
        db_table = "vps_actas"
        unique_together = (
            ("acta_type", "acta_escrutinio_legacy_id", "unique_id"),
        )
        indexes = [
            models.Index(fields=["election_process", "status"]),
            models.Index(fields=["acta_type", "status"]),
            models.Index(fields=["acta_escrutinio_legacy_id"]),
        ]
        verbose_name = "Acta (Procesada)"
        verbose_name_plural = "Actas (Procesadas)"

    def __str__(self):
        return f"Acta {self.unique_id} ({self.get_acta_type_display()})"

    def get_location_label(self) -> str:
        parts = []
        if self.department:
            parts.append(self.department.name)
        if self.province:
            parts.append(self.province.name)
        if self.district:
            parts.append(self.district.name)
        return " / ".join(parts) or "(sin ubicación)"


class ProcessingJob(TimeStampMixin):
    job_type = models.CharField(max_length=64, default="process_acta")
    acta_image = models.ForeignKey(ActaImage, on_delete=models.CASCADE, related_name="jobs", null=True, blank=True)
    acta = models.ForeignKey(Acta, on_delete=models.CASCADE, related_name="jobs", null=True, blank=True)
    status = models.CharField(max_length=20, choices=JOB_STATUS, default="pending")
    priority = models.PositiveSmallIntegerField(default=5)
    worker_id = models.CharField(max_length=128, blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")
    retry_count = models.PositiveIntegerField(default=0)
    parameters = models.JSONField(default=dict, blank=True)
    result_summary = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "vps_processing_jobs"
        ordering = ["priority", "created_at"]
        verbose_name = "Job de Procesamiento"
        verbose_name_plural = "Jobs de Procesamiento"

    def mark_start(self, worker_id: str = ""):
        self.status = "running"
        self.started_at = timezone.now()
        self.worker_id = worker_id
        self.save(update_fields=["status", "started_at", "worker_id"])

    def mark_done(self, summary: dict | None = None):
        self.status = "done"
        self.finished_at = timezone.now()
        if summary:
            self.result_summary = summary
        self.save(update_fields=["status", "finished_at", "result_summary"])

    def mark_failed(self, message: str):
        self.status = "failed"
        self.finished_at = timezone.now()
        self.error_message = message
        self.save(update_fields=["status", "finished_at", "error_message"])


class AIProviderSettings(TimeStampMixin):
    PROVIDER_CHOICES = (
        ("gemini", "Google Gemini"),
        ("deepseek", "Deepseek"),
    )

    GEMINI_MODEL_CHOICES = (
        ("gemini-3.6-flash", "Gemini 3.6 Flash (recomendado por Google)"),
        ("gemini-3.5-flash", "Gemini 3.5 Flash"),
        ("gemini-2.0-flash", "Gemini 2.0 Flash"),
        ("gemini-1.5-flash", "Gemini 1.5 Flash"),
        ("gemini-1.5-pro-latest", "Gemini 1.5 Pro"),
    )
    DEEPSEEK_MODEL_CHOICES = (
        ("deepseek-flash", "DeepSeek Flash (recomendado)"),
        ("deepseek-v4-pro", "DeepSeek V4 Pro"),
        ("deepseek-reasoner", "DeepSeek Reasoner (R1)"),
    )

    provider = models.CharField(max_length=32, choices=PROVIDER_CHOICES, unique=True)
    is_active = models.BooleanField(default=False)
    is_default = models.BooleanField(default=False)
    model = models.CharField(
        max_length=120,
        help_text="Slug oficial del modelo. Use dropdown o valores exactos (ej: deepseek-flash / gemini-3.6-flash).",
    )
    temperature = models.FloatField(default=0.0)
    timeout_secs = models.PositiveIntegerField(default=60)
    max_retries = models.PositiveIntegerField(default=3)
    confidence_threshold = models.FloatField(default=0.85)
    fallback_to = models.CharField(max_length=32, blank=True, default="")
    double_validation = models.BooleanField(default=False)
    _api_key_encrypted = models.TextField(db_column="api_key_encrypted", blank=True, default="")
    _extra_encrypted = models.TextField(db_column="extra_encrypted", blank=True, default="")

    class Meta:
        db_table = "vps_ai_provider_settings"
        verbose_name = "Configuración IA"
        verbose_name_plural = "Configuraciones IA"

    def __str__(self):
        return f"{self.provider} ({self.model})"

    @classmethod
    def _slugify_alias(cls, value: str) -> str:
        import re, unicodedata
        if not value:
            return ""
        v = value.strip().lower()
        v = "".join(c for c in unicodedata.normalize("NFD", v) if unicodedata.category(c) != "Mn")
        v = re.sub(r"[^a-z0-9]+", "-", v)
        v = v.strip("-")
        return v

    @classmethod
    def _normalize_model(cls, provider: str, model_raw: str) -> str:
        raw = (model_raw or "").strip()
        if not raw:
            if provider == "deepseek":
                return "deepseek-flash"
            if provider == "gemini":
                return "gemini-3.6-flash"
            return ""
        slug = cls._slugify_alias(raw)
        if provider == "deepseek":
            alias_map = {
                "deepseek-chat": "deepseek-flash",
                "deepseek-flash-preview": "deepseek-flash",
            }
            if slug in alias_map:
                return alias_map[slug]
            official = {c[0] for c in cls.DEEPSEEK_MODEL_CHOICES}
            if slug in official:
                return slug
            return "deepseek-flash"
        if provider == "gemini":
            alias_map = {
                "gemini-2-5-flash": "gemini-3.6-flash",
                "gemini-2-0-pro": "gemini-2.0-flash",
            }
            if slug in alias_map:
                return alias_map[slug]
            official = {c[0] for c in cls.GEMINI_MODEL_CHOICES}
            if slug in official:
                return slug
            return "gemini-3.6-flash"
        return slug

    def get_normalized_model(self) -> str:
        return AIProviderSettings._normalize_model(self.provider, self.model)

    def save(self, *args, **kwargs):
        self.model = self.get_normalized_model()
        super().save(*args, **kwargs)

    @property
    def api_key(self) -> str:
        return decrypt_value(self._api_key_encrypted) or ""

    @api_key.setter
    def api_key(self, value: str):
        self._api_key_encrypted = encrypt_value(value) or ""

    @property
    def extra_config(self) -> dict:
        raw = decrypt_value(self._extra_encrypted) or "{}"
        try:
            import json
            return json.loads(raw)
        except Exception:
            return {}

    @extra_config.setter
    def extra_config(self, value: dict):
        import json
        self._extra_encrypted = encrypt_value(json.dumps(value)) or ""

    @property
    def timeout(self) -> int:
        return int(self.timeout_secs or 60)

    @timeout.setter
    def timeout(self, value):
        try:
            self.timeout_secs = max(5, min(600, int(value)))
        except Exception:
            self.timeout_secs = 60

    def test_connection(self) -> tuple[bool, str]:
        from .ai import build_provider
        provider = build_provider(self)
        if not provider:
            return False, "No se pudo construir el proveedor"
        return provider.ping()


class ActaTranscription(TimeStampMixin):
    acta = models.ForeignKey(Acta, on_delete=models.CASCADE, related_name="transcriptions")
    provider = models.CharField(max_length=32)
    model = models.CharField(max_length=120)
    prompt_version = models.CharField(max_length=32, default="2.0")
    raw_json = models.JSONField(default=dict, blank=True)
    ocr_text = models.TextField(blank=True, default="")
    global_confidence = models.FloatField(default=0.0)
    validation_status = models.CharField(max_length=24, choices=VALIDATION_STATUS, default="pendiente")
    tech_notes = models.TextField(blank=True, default="")
    processing_job = models.ForeignKey(ProcessingJob, on_delete=models.SET_NULL, null=True, blank=True, related_name="transcriptions")
    processed_by = models.CharField(max_length=128, blank=True, default="")

    class Meta:
        db_table = "vps_acta_transcriptions"
        verbose_name = "Transcripción"
        verbose_name_plural = "Transcripciones"

    def __str__(self):
        return f"Trans {self.acta_id} {self.provider} conf={self.global_confidence:.2f}"


class ActaVoteEntry(TimeStampMixin):
    ORIGIN_CHOICES = (
        ("ocr", "OCR"),
        ("ia", "IA Multimodal"),
        ("consolidado", "Consolidado IA+OCR"),
        ("humano", "Corrección Humana"),
    )
    AMBITO_CHOICES = (
        ("muni_provincial", "Municipal Provincial"),
        ("muni_distrital", "Municipal Distrital"),
        ("gob_gobernador_vice", "Gobernador y Vice"),
        ("consejero_regional", "Consejero Regional"),
    )
    acta = models.ForeignKey(Acta, on_delete=models.CASCADE, related_name="vote_entries")
    transcription = models.ForeignKey(ActaTranscription, on_delete=models.SET_NULL, null=True, blank=True, related_name="entries")
    entry_category = models.CharField(max_length=24, choices=ENTRY_CATEGORIES)
    organization = models.ForeignKey(PoliticalOrganization, on_delete=models.PROTECT, null=True, blank=True, related_name="votes")
    candidate_name = models.CharField(max_length=180, blank=True, default="")
    position = models.CharField(max_length=64, blank=True, default="")
    scope = models.CharField(max_length=32, blank=True, default="")
    sort_order = models.PositiveIntegerField(default=0)
    ambito = models.CharField(max_length=32, choices=AMBITO_CHOICES, default="muni_provincial", db_index=True)
    votes = models.IntegerField(default=0, validators=[MinValueValidator(0)])
    votos_provincial = models.IntegerField(default=0, validators=[MinValueValidator(0)], help_text="Col 1 PROV_DISTRITAL o N/A")
    votos_distrital = models.IntegerField(default=0, validators=[MinValueValidator(0)], help_text="Col 2 PROV_DISTRITAL (0 si Iquitos)")
    votos_gob_gobernador_vice = models.IntegerField(default=0, validators=[MinValueValidator(0)], help_text="Col 1 REGIONAL")
    votos_consejero_regional = models.IntegerField(default=0, validators=[MinValueValidator(0)], help_text="Col 2 REGIONAL")
    data_origin = models.CharField(max_length=16, choices=ORIGIN_CHOICES, default="ia")
    field_confidence = models.FloatField(default=0.0)
    is_corrected = models.BooleanField(default=False)
    row_hash = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "vps_acta_vote_entries"
        ordering = ["acta", "ambito", "sort_order"]
        indexes = [
            models.Index(fields=["acta", "entry_category"]),
            models.Index(fields=["acta", "ambito"]),
        ]
        verbose_name = "Registro de Votos"
        verbose_name_plural = "Registros de Votos"

    @property
    def organization_short_name(self):
        if self.organization_id and self.organization.short_name:
            return self.organization.short_name
        if self.candidate_name:
            return self.candidate_name
        # fallback = traducción categorías (sin accents
        return {
            "blanco": "Votos en Blanco",
            "nulo": "Votos Nulos",
            "impugnado": "Votos Impugnados",
            "total_emitidos": "Total Votos Emitidos",
            "organizacion": "Organización Política",
        }.get(self.entry_category, self.entry_category.upper())

    @property
    def field_name(self):
        return self.organization_short_name

    def __str__(self):
        return f"{self.acta_id} | {self.organization_short_name} [{self.ambito}] = {self.votes}"


class ProcessingAlert(TimeStampMixin):
    acta = models.ForeignKey(Acta, on_delete=models.CASCADE, related_name="alerts", null=True, blank=True)
    job = models.ForeignKey(ProcessingJob, on_delete=models.SET_NULL, null=True, blank=True)
    severity = models.CharField(max_length=16, choices=ALERT_SEVERITY, default="warning")
    alert_type = models.CharField(max_length=64)
    message = models.TextField()
    detail = models.JSONField(default=dict, blank=True)
    acknowledged = models.BooleanField(default=False)
    acknowledged_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "vps_processing_alerts"
        ordering = ["-created_at"]
        verbose_name = "Alerta de Proceso"
        verbose_name_plural = "Alertas de Proceso"

    def __str__(self):
        return f"[{self.severity}] {self.alert_type}"


class ManualReviewQueue(TimeStampMixin):
    acta = models.ForeignKey(Acta, on_delete=models.CASCADE, related_name="review_items")
    transcription = models.ForeignKey(ActaTranscription, on_delete=models.SET_NULL, null=True, blank=True)
    reason = models.TextField()
    reason_codes = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=20, choices=REVIEW_STATUS, default="abierto")
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_reviews")
    opened_at = models.DateTimeField(auto_now_add=True)
    started_review_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="closed_reviews")
    resolution_notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "vps_manual_review_queue"
        ordering = ["status", "-created_at"]
        verbose_name = "Cola de Revisión Humana"
        verbose_name_plural = "Colas de Revisión Humana"

    def __str__(self):
        return f"Rev {self.acta_id} [{self.status}]"


class HumanCorrection(TimeStampMixin):
    review_item = models.ForeignKey(ManualReviewQueue, on_delete=models.CASCADE, related_name="corrections")
    acta = models.ForeignKey(Acta, on_delete=models.CASCADE, related_name="corrections")
    vote_entry = models.ForeignKey(ActaVoteEntry, on_delete=models.SET_NULL, null=True, blank=True, related_name="corrections")
    field_name = models.CharField(max_length=96)
    old_value = models.TextField(blank=True, default="")
    new_value = models.TextField(blank=True, default="")
    ia_value = models.TextField(blank=True, default="")
    corrected_by = models.ForeignKey(User, on_delete=models.PROTECT)
    comment = models.TextField(blank=True, default="")

    class Meta:
        db_table = "vps_human_corrections"
        ordering = ["-created_at"]
        verbose_name = "Corrección Humana"
        verbose_name_plural = "Correcciones Humanas"

    def __str__(self):
        return f"{self.field_name}: {self.old_value} -> {self.new_value}"


class AuditLog(TimeStampMixin):
    ACTION_CHOICES = (
        ("create", "Crear"),
        ("update", "Actualizar"),
        ("delete", "Eliminar"),
        ("approve", "Aprobar"),
        ("reject", "Rechazar"),
        ("reprocess", "Reprocesar"),
        ("login", "Login"),
        ("export", "Exportar"),
    )
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=16, choices=ACTION_CHOICES)
    model_name = models.CharField(max_length=64, blank=True, default="")
    object_id = models.CharField(max_length=96, blank=True, default="")
    field_name = models.CharField(max_length=96, blank=True, default="")
    old_value = models.TextField(blank=True, default="")
    new_value = models.TextField(blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default="")
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "vps_audit_log"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["model_name", "object_id"]),
            models.Index(fields=["actor", "action"]),
        ]
        verbose_name = "Bitácora de Auditoría"
        verbose_name_plural = "Bitácoras de Auditoría"

    def __str__(self):
        return f"{self.action} {self.model_name} {self.object_id}"


class OrganizationList(TimeStampMixin):
    TIPO_ACTA_CHOICES = (
        ("regional", "Regional"),
        ("provincial_distrital", "Provincial / Distrital"),
        ("ambos", "Ambos"),
    )
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=180)
    tipo_acta = models.CharField(max_length=32, choices=TIPO_ACTA_CHOICES, default="ambos")
    ambito = models.CharField(max_length=32, choices=ActaVoteEntry.AMBITO_CHOICES, default="muni_provincial", db_index=True)
    departamento = models.CharField(max_length=120, blank=True, default="")
    provincia = models.CharField(max_length=120, blank=True, default="")
    distrito = models.CharField(max_length=120, blank=True, default="")
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "vps_organization_lists"
        ordering = ["code"]
        verbose_name = "Lista Ordenable de Organizaciones"
        verbose_name_plural = "Listas Ordenables de Organizaciones"

    def __str__(self):
        return f"{self.code} - {self.name} [{self.get_tipo_acta_display()}]"


class OrganizationListEntry(TimeStampMixin):
    list = models.ForeignKey(OrganizationList, on_delete=models.CASCADE, related_name="entries")
    organization = models.ForeignKey(PoliticalOrganization, on_delete=models.PROTECT, related_name="list_entries")
    sort_order = models.PositiveIntegerField(default=0)
    candidate_name = models.CharField(max_length=220, blank=True, default="")
    scope_label = models.CharField(max_length=120, blank=True, default="")
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "vps_organization_list_entries"
        ordering = ["list", "sort_order"]
        unique_together = (("list", "organization"),)
        verbose_name = "Organización (lista ordenable)"
        verbose_name_plural = "Organizaciones (listas ordenables)"

    def __str__(self):
        org = self.organization.short_name if self.organization else f"org-{self.organization_id or '?'}"
        return f"{self.sort_order:02d}. {org}"


class ProcessingConfig(TimeStampMixin):
    """Singleton global configuration for async batch processing pipeline.

    Managed=True vps_processing_config table (NEW, no legacy touch).
    Should exist exactly ONE row (pk=1). Use ProcessingConfig.get_solo() anywhere.
    """
    max_concurrent = models.PositiveSmallIntegerField(
        default=2,
        validators=[MinValueValidator(1), MaxValueValidator(4)],
        help_text="Número máximo de actas procesando en paralelo en Celery. Limitado a 4 para no saturar el VPS.",
    )
    auto_batch_enabled = models.BooleanField(
        default=False,
        help_text="Si está activado, el sistema automáticamente encola lotes pendientes sin presionar Procesar Lote.",
    )
    auto_batch_interval_secs = models.PositiveIntegerField(
        default=30,
        validators=[MinValueValidator(10), MaxValueValidator(600)],
        help_text="Cada cuántos segundos verifica nuevos lotes pendientes para auto-procesar.",
    )
    poll_progress_interval_secs = models.PositiveSmallIntegerField(
        default=3,
        validators=[MinValueValidator(1), MaxValueValidator(15)],
        help_text="Intervalo de actualización en UI (HTMX poll) para progreso en tiempo real.",
    )
    max_batch_size_per_dispatch = models.PositiveSmallIntegerField(
        default=8,
        validators=[MinValueValidator(1), MaxValueValidator(30)],
        help_text="Máximo número de actas encoladas por cada Procesar Lote / auto-dispatch.",
    )
    per_acta_timeout_secs = models.PositiveSmallIntegerField(
        default=90,
        validators=[MinValueValidator(30), MaxValueValidator(180)],
        help_text="Timeout máximo (segundos) por acta individual en Celery worker (hard-kill beyond).",
    )
    retry_count_job = models.PositiveSmallIntegerField(
        default=2,
        validators=[MinValueValidator(0), MaxValueValidator(5)],
        help_text="Reintentos automáticos si falla el job de Celery antes de marcarlo como error.",
    )
    retry_backoff_secs = models.PositiveSmallIntegerField(
        default=45,
        validators=[MinValueValidator(10), MaxValueValidator(600)],
        help_text="Base para backoff exponencial entre reintentos (segundos).",
    )
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        db_table = "vps_processing_config"
        verbose_name = "Configuración de Procesamiento Batch"
        verbose_name_plural = "Configuraciones de Procesamiento Batch"

    def __str__(self):
        return (
            f"ProcessingConfig(id={self.pk}, max_concurrent={self.max_concurrent}, "
            f"auto={self.auto_batch_enabled}, poll={self.poll_progress_interval_secs}s)"
        )

    @classmethod
    def get_solo(cls) -> "ProcessingConfig":
        obj, _ = cls.objects.get_or_create(pk=1, defaults={
            "max_concurrent": 2,
            "auto_batch_enabled": False,
            "auto_batch_interval_secs": 30,
            "poll_progress_interval_secs": 3,
            "max_batch_size_per_dispatch": 8,
            "per_acta_timeout_secs": 90,
            "retry_count_job": 2,
            "retry_backoff_secs": 45,
            "updated_by": None,
        })
        return obj

