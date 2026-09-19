from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import (
    GeoDepartment, GeoProvince, GeoDistrict,
    PoliticalOrganization, ElectionProcess,
)


DEPARTAMENTOS = [
    ("01", "AMAZONAS"),
    ("02", "ANCASH"),
    ("03", "APURIMAC"),
    ("04", "AREQUIPA"),
    ("05", "AYACUCHO"),
    ("06", "CAJAMARCA"),
    ("07", "CALLAO"),
    ("08", "CUSCO"),
    ("09", "HUANCAVELICA"),
    ("10", "HUANUCO"),
    ("11", "ICA"),
    ("12", "JUNIN"),
    ("13", "LA LIBERTAD"),
    ("14", "LAMBAYEQUE"),
    ("15", "LIMA"),
    ("16", "LORETO"),
    ("17", "MADRE DE DIOS"),
    ("18", "MOQUEGUA"),
    ("19", "PASCO"),
    ("20", "PIURA"),
    ("21", "PUNO"),
    ("22", "SAN MARTIN"),
    ("23", "TACNA"),
    ("24", "TUMBES"),
    ("25", "UCAYALI"),
]

PARTIDOS_DEMO = [
    ("001", "APRA", "Partido Aprista Peruano", "APRA", "nacional", 1, "#1F4E78"),
    ("002", "UNION_POR_PERU", "Unión por el Perú", "UPP", "nacional", 2, "#C00000"),
    ("003", "FUERZA_POPULAR", "Fuerza Popular", "FP", "nacional", 3, "#FF6600"),
    ("004", "ACCION_POPULAR", "Acción Popular", "AP", "nacional", 4, "#0070C0"),
    ("005", "PPC", "Partido Popular Cristiano", "PPC", "nacional", 5, "#00B050"),
    ("006", "VENDICACION_POPULAR", "Partido Vendición Popular", "VP", "mixto", 6, "#7030A0"),
    ("007", "JUNTOS_POR_PERU", "Juntos por el Perú", "JPP", "mixto", 7, "#FF0000"),
    ("008", "BLOQUE_MAGISTERIAL", "Bloque Magisterial", "BM", "regional", 8, "#548235"),
    ("009", "REGIONES_ALTERNATIVAS", "Regiones Alternativas", "RA", "regional", 9, "#BF8F00"),
    ("010", "MOVIMIENTO_REGIONAL", "Movimiento Regional Independiente", "MRI", "regional", 10, "#2E75B6"),
]


class Command(BaseCommand):
    help = "Carga datos de ejemplo: departamentos (25 peruanos), 2 provincias/departamento, distritos, organizaciones, proceso electoral demo"

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Borrar catálogos antes de cargar")

    @transaction.atomic
    def handle(self, *args, **options):
        if options["reset"]:
            GeoDistrict.objects.all().delete()
            GeoProvince.objects.all().delete()
            GeoDepartment.objects.all().delete()
            PoliticalOrganization.objects.all().delete()
            ElectionProcess.objects.all().delete()
            self.stdout.write(self.style.WARNING("Catálogos reseteados"))

        dept_map = {}
        for code, name in DEPARTAMENTOS:
            d, _ = GeoDepartment.objects.get_or_create(code=code, defaults={"name": name})
            dept_map[code] = d

        for d_code, d_obj in dept_map.items():
            for p_idx, p_name in [("01", "Provincia 1"), ("02", "Provincia 2")]:
                p, _ = GeoProvince.objects.get_or_create(
                    department=d_obj,
                    code=f"{d_code}{p_idx}",
                    defaults={"name": f"{d_obj.name} - {p_name}"},
                )
                for dis in range(1, 5):
                    ubigeo = f"{d_code}{p_idx}{dis:02d}"
                    GeoDistrict.objects.get_or_create(
                        province=p,
                        ubigeo=ubigeo,
                        defaults={
                            "name": f"Distrito {ubigeo}",
                            "has_district_election": dis != 4,
                        },
                    )
        self.stdout.write(self.style.SUCCESS(
            f"Cargados {GeoDepartment.objects.count()} departamentos, "
            f"{GeoProvince.objects.count()} provincias, "
            f"{GeoDistrict.objects.count()} distritos."
        ))

        for code, short, full, acronym, scope, order, color in PARTIDOS_DEMO:
            PoliticalOrganization.objects.get_or_create(
                code=code,
                defaults={
                    "short_name": short,
                    "full_name": full,
                    "acronym": acronym,
                    "scope": scope,
                    "sort_order": order,
                    "color": color,
                    "is_active": True,
                },
            )
        self.stdout.write(self.style.SUCCESS(f"Cargadas {PoliticalOrganization.objects.count()} organizaciones políticas demo"))

        import datetime
        ElectionProcess.objects.get_or_create(
            code="ELEC_2026_DEMO",
            defaults={
                "name": "Elecciones Regionales y Municipales 2026 (DEMO)",
                "process_date": datetime.date(2026, 10, 5),
                "has_regional": True,
                "has_provincial": True,
                "has_distrital": True,
                "is_active": True,
                "description": "Conjunto de datos de demostración para pruebas del sistema.",
            },
        )
        self.stdout.write(self.style.SUCCESS("Proceso electoral DEMO creado"))
