from django.db import models


class UsuarioLegacy(models.Model):
    idusuario = models.AutoField(primary_key=True)
    idpersona = models.IntegerField(null=True, blank=True)
    usuario = models.CharField(max_length=100, unique=True)
    clave = models.CharField(max_length=255)
    rol = models.CharField(max_length=30, blank=True, default="")
    fecha_creado = models.DateTimeField(blank=True, null=True)
    estado = models.BooleanField(default=True)
    id_centro_asignado = models.IntegerField(null=True, blank=True)
    id_mesa_asignada = models.IntegerField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "usuarios"
        verbose_name = "Usuario (Legado)"
        verbose_name_plural = "Usuarios (Legado)"

    def __str__(self):
        return f"{self.usuario} [{self.rol}]"

    @property
    def is_active_legacy(self):
        return bool(self.estado) and self.rol in ("SUPER_ADMIN", "ADMINISTRADOR")


class CentroVotacionLegacy(models.Model):
    id_centro = models.AutoField(primary_key=True)
    codigo_local = models.CharField(max_length=50, blank=True, default="")
    ubigeo = models.CharField(max_length=10, blank=True, default="")
    nombre_local = models.CharField(max_length=255, blank=True, default="")
    direccion_local = models.TextField(blank=True, default="")
    departamento = models.CharField(max_length=120, blank=True, default="")
    provincia = models.CharField(max_length=120, blank=True, default="")
    distrito = models.CharField(max_length=120, blank=True, default="")

    class Meta:
        managed = False
        db_table = "centro_votacion"
        verbose_name = "Centro Votación (Legado)"
        verbose_name_plural = "Centros Votación (Legado)"

    def __str__(self):
        return self.nombre_local or f"CV-{self.id_centro}"


class MesaLegacy(models.Model):
    id_mesa = models.AutoField(primary_key=True)
    num_mesa = models.CharField(max_length=10, unique=True)
    cod_local = models.CharField(max_length=50, blank=True, default="")
    codi_ubigeo = models.CharField(max_length=10, blank=True, default="")
    nombre_local = models.CharField(max_length=255, blank=True, default="")
    direccion_local = models.TextField(blank=True, default="")
    departamento = models.CharField(max_length=120, blank=True, default="")
    provincia = models.CharField(max_length=120, blank=True, default="")
    distrito = models.CharField(max_length=120, blank=True, default="")

    class Meta:
        managed = False
        db_table = "mesa"
        verbose_name = "Mesa (Legado)"
        verbose_name_plural = "Mesas (Legado)"

    def __str__(self):
        return f"Mesa {self.num_mesa} ({self.distrito})"

    @staticmethod
    def normalize_distrito(s: str) -> str:
        if not s:
            return ""
        import unicodedata

        s = s.strip().upper()
        s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
        return s


class ActaEscrutinioLegacyManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().select_related(None)

    def with_mesa(self):
        return self.get_queryset().extra(
            tables=["mesa"],
            where=["mesa.num_mesa = mesa_numero"],
        )


class ActaEscrutinioLegacy(models.Model):
    id = models.BigAutoField(primary_key=True)
    centro_votacion_id = models.IntegerField(null=True, blank=True)
    mesa_numero = models.CharField(max_length=10, blank=True, default="", db_index=True)
    tipo_acta = models.CharField(max_length=50, blank=True, default="")
    file_path_disco = models.TextField(blank=True, default="")
    file_size_bytes = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True, default="")
    uploaded_by_usuario_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(blank=True, null=True)
    metadata_json = models.JSONField(default=dict, blank=True, null=True)

    objects = ActaEscrutinioLegacyManager()

    class Meta:
        managed = False
        db_table = "actas_escrutinio"
        verbose_name = "Acta de Escrutinio (Legado)"
        verbose_name_plural = "Actas de Escrutinio (Legado)"

    def __str__(self):
        return f"Acta {self.id} | Mesa {self.mesa_numero} | {self.tipo_acta}"

    def get_mesa(self):
        try:
            return MesaLegacy.objects.get(num_mesa=(self.mesa_numero or "").strip())
        except MesaLegacy.DoesNotExist:
            return None

    @property
    def distrito(self) -> str:
        mesa = self.get_mesa()
        return mesa.distrito if mesa else ""

    @property
    def is_iquitos_special(self) -> bool:
        mesa = self.get_mesa()
        if not mesa:
            return False
        if (self.tipo_acta or "").upper() != "PROVINCIAL_DISTRITAL":
            return False
        return MesaLegacy.normalize_distrito(mesa.distrito) == "IQUITOS"
