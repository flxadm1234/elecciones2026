from django.urls import path, include
from django.contrib.auth import views as auth_views
from django.views.generic.base import RedirectView

from . import views


urlpatterns = [
    path("", views.dashboard_view, name="home"),
    path("accounts/", include("django.contrib.auth.urls")),
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path("monitoreo/", views.pipeline_monitor_view, name="pipeline_monitor"),
    path("dashboard/stats-partial/", views.dashboard_stats_partial, name="dashboard_stats_partial"),
    path("resultados/", views.resultados_view, name="resultados"),
    path("configuracion/", views.config_view, name="config_view"),
    path("procesar-pendientes/", views.procesar_pendientes_view, name="procesar_pendientes"),
    path("actas/procesadas/", views.actas_procesadas_list_view, name="actas_procesadas"),
    path("actas/pendientes/", views.actas_pendientes_list_view, name="actas_pendientes"),
    path("actas/<int:pk>/", views.acta_detail_view, name="acta_detail"),
    path("acta/<int:pk>/", views.acta_detail_view, name="acta_detail_alias"),
    path("review/<int:acta_pk>/", views.review_split_view, name="review_split"),
    path("acta-imagen/<int:acta_pk>/original.jpg", views.serve_acta_image_protected, name="serve_acta_image"),
    path("media/actas/<int:acta_pk>/original.jpg", views.serve_acta_image_protected, name="serve_acta_image_legacy_alias"),
    path("api/v1/review/<int:pk>/save/", views.review_save_corrections_api, name="review_save_api"),
    path("review/<int:acta_pk>/finalizar/", views.review_finalize_api, name="review_finalize_api"),
    path("api/v1/progress.json", views.api_progress_snapshot, name="api_progress"),
    path("api/progress/snapshot/", views.api_progress_snapshot, name="api_progress_snapshot"),
    path("api/toggle-auto-batch/", views.config_toggle_autobatch_api, name="toggle_auto_batch"),
    path("api/pipeline-monitor/", views.pipeline_monitor_api_json, name="pipeline_monitor_json"),
    path("api/ai-provider/save/", views.ai_provider_save_api, name="ai_provider_save"),
    path("api/ai-provider/<str:provider>/test/", views.ai_provider_test_api, name="ai_provider_test"),
    path("api/batch/<str:batch_group>/status/", views.batch_status_api_json, name="batch_status_json"),
    path("organizaciones/", views.org_manage_list, name="org_manage_list"),
    path("organizaciones/<int:pk>/editar/", views.org_edit_api, name="org_edit_api"),
    path("organizaciones/<int:pk>/subir-logo/", views.org_upload_logo_api, name="org_upload_logo_api"),
    path("organizaciones/reordenar/", views.org_reorder_api, name="org_reorder_api"),
    path("organizaciones/<int:pk>/eliminar-logo/", views.org_delete_logo_api, name="org_delete_logo_api"),
    path("gestion-actas/", views.gestion_actas_list, name="gestion_actas"),
    path("purgar-todo-procesados/", views.purgar_todo_procesados_api, name="purgar_todo_api"),
    path("eliminar-acta/<int:pk>/", views.eliminar_acta_api, name="eliminar_acta_api"),
    path("login/", auth_views.LoginView.as_view(), name="login_fallback"),
    path("admin_redirect/", RedirectView.as_view(url="/admin/"), name="admin_redirect"),
]
