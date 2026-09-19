from django.urls import path, include
from rest_framework.routers import DefaultRouter

from . import api_views

router = DefaultRouter()
router.register(r"geo/departments", api_views.GeoDepartmentViewSet)
router.register(r"geo/provinces", api_views.GeoProvinceViewSet)
router.register(r"geo/districts", api_views.GeoDistrictViewSet)
router.register(r"political-organizations", api_views.PoliticalOrganizationViewSet)
router.register(r"election-processes", api_views.ElectionProcessViewSet)
router.register(r"acta-images", api_views.ActaImageViewSet)
router.register(r"actas", api_views.ActaViewSet)
router.register(r"transcriptions", api_views.ActaTranscriptionViewSet)
router.register(r"vote-entries", api_views.ActaVoteEntryViewSet)
router.register(r"jobs", api_views.ProcessingJobViewSet)
router.register(r"alerts", api_views.ProcessingAlertViewSet)
router.register(r"review-queue", api_views.ManualReviewQueueViewSet)
router.register(r"corrections", api_views.HumanCorrectionViewSet)
router.register(r"audit-log", api_views.AuditLogViewSet)
router.register(r"ai-providers", api_views.AIProviderSettingsViewSet)

urlpatterns = [
    path("", include(router.urls)),
]
