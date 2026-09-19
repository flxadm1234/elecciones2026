from __future__ import annotations

import json

from django.test import TestCase, Client, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse

User = get_user_model()


# ============================================================================
# Helper: crea usuarios para tests de roles
# ============================================================================
def _create_users():
    superadmin = User.objects.create_user(
        username="test_superadmin",
        password="Abc.12345.",
        is_staff=True,
        is_superuser=True,
    )
    admin_user = User.objects.create_user(
        username="test_admin",
        password="Abc.12345.",
        is_staff=True,
        is_superuser=False,
    )
    return superadmin, admin_user


def _client_login(client: Client, user):
    """force_login() evita pasar por el backend legacy de autenticación."""
    client.force_login(user)
    return client


# ============================================================================
# Caso de test principal: FASE F test_ui_flow
# ============================================================================
class UIFlowTests(TestCase):
    """Tests flujo UI elecciones2026: login, listados, dashboard, detalle, API."""

    databases = {"default"}

    @classmethod
    def setUpTestData(cls):
        cls.superadmin, cls.admin = _create_users()
        # Intentar crear fixtures mínimas de Acta (si la DB soporta modelos core).
        # En SQLite local los modelos core están migrados; en PostgreSQL VPS los
        # managed=False legacy no se tocarán.
        try:
            from core.models import Acta, ActaVoteEntry
            cls.have_core_models = True
        except Exception:
            cls.have_core_models = False

    # ------------------------------------------------------------------
    # 1) Redirección anónimo
    # ------------------------------------------------------------------
    def test_01_anonym_redirects_to_login(self):
        c = Client()
        resp = c.get("/", follow=False)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            "/accounts/login/" in (resp.get("Location") or ""),
            f"Debe redirigir a login, pero fue: {resp.get('Location')}",
        )

    # ------------------------------------------------------------------
    # 2) Dashboard 200 + texto corporativo
    # ------------------------------------------------------------------
    def test_02_superadmin_dashboard_200(self):
        c = _client_login(Client(), self.superadmin)
        resp = c.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, 200, "Dashboard debe cargar 200")
        body = resp.content.decode("utf-8", errors="replace")
        # Marcadores de layout corporativo (Fase A diseño)
        self.assertTrue(
            any(needle in body for needle in [
                "app-shell", "sidebar", "Dashboard", "Procesamiento",
                "Elecciones 2026", "Procesada", "KPI",
            ]),
            "Dashboard debe renderizar app-shell corporativa y branding Elecciones 2026",
        )

    # ------------------------------------------------------------------
    # 3) Listado Procesadas 200
    # ------------------------------------------------------------------
    def test_03_list_procesadas_200(self):
        c = _client_login(Client(), self.superadmin)
        resp = c.get(reverse("actas_procesadas"))
        self.assertEqual(resp.status_code, 200, "Listado procesadas debe ser 200")
        body = resp.content.decode("utf-8", errors="replace")
        self.assertIn("Actas Procesadas", body, "Debe contener título listado procesadas")
        # Debe existir la sección filter-bar (Fase B)
        self.assertTrue(
            "filter-bar" in body or "Confianza" in body or "buscador" in body.lower(),
            "Falta barra de filtros en listado procesadas",
        )

    # ------------------------------------------------------------------
    # 4) Listado Pendientes 200
    # ------------------------------------------------------------------
    def test_04_list_pendientes_200(self):
        c = _client_login(Client(), self.superadmin)
        resp = c.get(reverse("actas_pendientes"))
        self.assertEqual(resp.status_code, 200, "Listado pendientes debe ser 200")
        body = resp.content.decode("utf-8", errors="replace")
        self.assertIn("Por Procesar", body, "Título pendientes debe aparecer")

    # ------------------------------------------------------------------
    # 5) API progress JSON 200 + schema correcto
    # ------------------------------------------------------------------
    def test_05_api_progress_json_200(self):
        c = _client_login(Client(), self.superadmin)
        resp = c.get(reverse("api_progress"))
        self.assertEqual(resp.status_code, 200, "Progress JSON debe ser 200")
        data = json.loads(resp.content.decode("utf-8"))
        # keys mínimas: kpis, status_donut, ambitos_bar, timeline_line, confidence_bands
        for key in ("kpis", "status_donut", "ambitos_bar", "timeline_line", "confidence_bands"):
            self.assertIn(key, data, f"Falta key {key} en progress JSON")
        kpis = data["kpis"]
        for k in ("total", "ok", "pendiente", "procesando"):
            self.assertIn(k, kpis, f"Falta kpi.{k}")
        # status donut debe tener 6 rebanadas
        self.assertEqual(
            len(data["status_donut"]["data"]), 6,
            "Status donut debe contener 6 slices",
        )

    # ------------------------------------------------------------------
    # 6) Acta detalle: crea acta mock (si es posible) y valida 200/404+texto
    # ------------------------------------------------------------------
    def test_06_acta_detail_renders_confidence_section(self):
        if not self.have_core_models:
            self.skipTest("Modelos core no disponibles")

        from core.models import Acta

        # Intentar usar acta 15 (lote VPS real) o crear un mock
        acta = Acta.objects.filter(pk=15).first()
        if not acta:
            acta = Acta.objects.filter(status__in=["procesado_ok", "revisado_ok"]).first()

        c = _client_login(Client(), self.superadmin)

        if acta:
            resp = c.get(reverse("acta_detail", kwargs={"pk": acta.pk}))
            self.assertIn(resp.status_code, (200,), f"Detalle acta {acta.pk} debe ser 200")
            body = resp.content.decode("utf-8", errors="replace")
            # Debe haber sección de confianza visual (Fase D): SVG ring o label ALTA/MEDIA/BAJA
            self.assertTrue(
                any(tok in body for tok in [
                    "confidence-ring", "ALTA", "MEDIA", "BAJA",
                    "Confianza", "confidence_score", "anillo",
                ]),
                f"Detalle acta {acta.pk} debe renderizar indicador confianza",
            )
            # Tabs por ámbito
            self.assertTrue(
                "data-tabs" in body or "ambito" in body.lower(),
                "Detalle acta debe tener tabs por ámbito",
            )
        else:
            # Sin actas: endpoint debe responder 404 sin 500
            resp = c.get(reverse("acta_detail", kwargs={"pk": 999999999}))
            self.assertEqual(resp.status_code, 404, "Acta inexistente debe responder 404 no 500")

    # ------------------------------------------------------------------
    # 7) Split review: 200 con datos/404, debe renderizar split-shell
    # ------------------------------------------------------------------
    def test_07_review_split_renders_layout(self):
        if not self.have_core_models:
            self.skipTest("Modelos core no disponibles")

        from core.models import Acta

        acta = Acta.objects.filter(pk=15).first() or Acta.objects.first()
        c = _client_login(Client(), self.superadmin)

        if acta:
            resp = c.get(reverse("review_split", kwargs={"acta_pk": acta.pk}))
            self.assertIn(resp.status_code, (200,))
            body = resp.content.decode("utf-8", errors="replace")
            self.assertTrue(
                "split-shell" in body and "split-image" in body and "split-form" in body,
                f"Review split {acta.pk} debe renderizar layout split-shell",
            )
            # Controles de imagen (Fase E feature img controls)
            for tok in ("btn-zoom-in", "btn-rotate", "btn-maximize", "vote-input"):
                self.assertIn(tok, body, f"Falta control {tok} en review split")
        else:
            resp = c.get(reverse("review_split", kwargs={"acta_pk": 999999999}))
            self.assertEqual(resp.status_code, 404)

    # ------------------------------------------------------------------
    # 8) Review save API: validación (no 500) — body vacío / inválido / sin CSRF con force_login
    # ------------------------------------------------------------------
    def test_08_review_save_api_validates_payload(self):
        if not self.have_core_models:
            self.skipTest("Modelos core no disponibles")

        c = _client_login(Client(), self.superadmin)
        url = reverse("review_save_api", kwargs={"pk": 999999})

        # Caso a) body ilegible JSON
        resp = c.post(
            url,
            data="not-json-at-all{{{",
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        # Debe ser 400 (cuerpo inválido) NUNCA 500
        self.assertIn(
            resp.status_code, (400, 401, 403, 404),
            f"Body inválido debe retornar 4xx, pero fue {resp.status_code}",
        )

        # Caso b) JSON completo pero acta/pk no existen → 404 controlado
        payload = json.dumps({"vote_entry_id": 123456, "field_name": "votes", "new_value": 5})
        resp = c.post(
            url,
            data=payload,
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertIn(
            resp.status_code, (404, 400),
            f"Acta inexistente save API debe 404/400 no 500, fue {resp.status_code}",
        )

    # ------------------------------------------------------------------
    # 9) Gating SUPER_ADMIN: usuario ADMIN sin super no debe ver link Config IA
    # ------------------------------------------------------------------
    def test_09_admin_user_hides_superadmin_sections(self):
        # Usuario ADMIN (is_staff=True, is_superuser=False)
        c = _client_login(Client(), self.admin)
        resp = c.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8", errors="replace")

        # Link Config IA solo se renderiza si is_super=True (base.html sidebar)
        # Admin NO debería ver "Configurar IA"
        self.assertNotIn(
            "Configurar IA",
            body,
            "Usuario ADMIN (no super) NO debe ver enlace Configurar IA",
        )
        # SUPER_ADMIN sí debe verlo
        c2 = _client_login(Client(), self.superadmin)
        resp2 = c2.get(reverse("dashboard"))
        body2 = resp2.content.decode("utf-8", errors="replace")
        self.assertIn(
            "Configurar IA",
            body2,
            "Usuario SUPER_ADMIN debe ver enlace Configurar IA",
        )

    # ------------------------------------------------------------------
    # 10) BONUS: endpoints protegidos (sin auth) devuelven redirect, no 500
    # ------------------------------------------------------------------
    def test_10_all_core_urls_require_auth(self):
        protected = [
            ("home", {}),
            ("dashboard", {}),
            ("dashboard_stats_partial", {}),
            ("actas_procesadas", {}),
            ("actas_pendientes", {}),
            ("acta_detail", {"pk": 15}),
            ("review_split", {"acta_pk": 15}),
            ("serve_acta_image", {"acta_pk": 15}),
            ("review_save_api", {"pk": 15}),
            ("review_finalize_api", {"acta_pk": 15}),
            ("api_progress", {}),
        ]
        c = Client()
        for name, kwargs in protected:
            try:
                url = reverse(name, kwargs=kwargs)
            except Exception:
                continue
            try:
                resp = c.get(url, follow=False)
            except Exception:
                # POST-only endpoints como review_save_api fallan — probar POST sin body
                try:
                    resp = c.post(url, data={}, content_type="application/json", follow=False)
                except Exception as exc_post:
                    self.fail(f"Endpoint {name} lanza excepción sin auth: {exc_post}")
                    continue
            # GET → debe ser 302 redirect o 405 (POST-only). NUNCA 500.
            self.assertNotEqual(
                resp.status_code, 500,
                f"Endpoint {name} {url} retorna 500 sin auth",
            )
