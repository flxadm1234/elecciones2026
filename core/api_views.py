from __future__ import annotations

import os
import io
import glob
from django.http import HttpResponse
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, IsAdminUser

from .models import (
    GeoDepartment, GeoProvince, GeoDistrict,
    PoliticalOrganization, ElectionProcess,
    ActaImage, Acta, ActaTranscription, ActaVoteEntry,
    ProcessingJob, ProcessingAlert,
    ManualReviewQueue, AIProviderSettings, HumanCorrection, AuditLog,
)
from .serializers import (
    GeoDepartmentSerializer, GeoProvinceSerializer, GeoDistrictSerializer,
    PoliticalOrganizationSerializer, ElectionProcessSerializer,
    ActaImageSerializer, ActaSerializer, ActaTranscriptionSerializer,
    ActaVoteEntrySerializer, ProcessingJobSerializer, ProcessingAlertSerializer,
    ManualReviewQueueSerializer, HumanCorrectionSerializer, AuditLogSerializer,
    AIProviderSettingsSerializer, ImageEnqueueSerializer, ImageScanDirSerializer,
)
from .processors import ActaProcessor
from .exporters import export_actas_xlsx


class GeoDepartmentViewSet(viewsets.ModelViewSet):
    queryset = GeoDepartment.objects.all()
    serializer_class = GeoDepartmentSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["is_active"]
    search_fields = ["code", "name"]


class GeoProvinceViewSet(viewsets.ModelViewSet):
    queryset = GeoProvince.objects.select_related("department").all()
    serializer_class = GeoProvinceSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["is_active", "department"]
    search_fields = ["code", "name"]


class GeoDistrictViewSet(viewsets.ModelViewSet):
    queryset = GeoDistrict.objects.select_related("province", "province__department").all()
    serializer_class = GeoDistrictSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["is_active", "has_district_election", "province"]
    search_fields = ["ubigeo", "name"]


class PoliticalOrganizationViewSet(viewsets.ModelViewSet):
    queryset = PoliticalOrganization.objects.all()
    serializer_class = PoliticalOrganizationSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["is_active", "scope"]
    search_fields = ["code", "short_name", "full_name", "acronym"]
    ordering_fields = ["sort_order", "short_name"]


class ElectionProcessViewSet(viewsets.ModelViewSet):
    queryset = ElectionProcess.objects.all()
    serializer_class = ElectionProcessSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["is_active", "has_regional", "has_provincial", "has_distrital"]
    search_fields = ["code", "name"]


class ActaImageViewSet(viewsets.ModelViewSet):
    queryset = ActaImage.objects.all()
    serializer_class = ActaImageSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["availability"]
    search_fields = ["file_path", "file_hash"]

    @action(detail=False, methods=["post"], url_path="enqueue")
    def enqueue_image(self, request):
        ser = ImageEnqueueSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        try:
            proc = ActaProcessor()
            img = proc.register_image(d["file_path"], uploaded_by=request.user)
            job = proc.create_job_for_image(
                img,
                job_type=d.get("job_type") or "process_acta",
                params={"process_code": d.get("process_code") or ""},
            )
        except FileNotFoundError as e:
            return Response({"error": f"Archivo no encontrado: {e}"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            from .tasks import run_processing_job
            run_processing_job.delay(job.id)
        except Exception:
            pass
        return Response({"image": img.id, "job": job.id}, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path="scan-dir")
    def scan_directory(self, request):
        ser = ImageScanDirSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        directory = d["directory"]
        patterns = [p.strip() for p in d["pattern"].split(",") if p.strip()]
        files = []
        if os.path.isdir(directory):
            for pat in patterns:
                files.extend(glob.glob(os.path.join(directory, "**", pat), recursive=True))
        files = sorted(set(files))
        proc = ActaProcessor()
        created = []
        skipped = []
        jobs = []
        for fp in files:
            try:
                img = proc.register_image(fp, uploaded_by=request.user)
                created.append(img.id)
                if d["enqueue"]:
                    job = proc.create_job_for_image(img, params={"process_code": d.get("process_code") or ""})
                    jobs.append(job.id)
                    try:
                        from .tasks import run_processing_job
                        run_processing_job.delay(job.id)
                    except Exception:
                        pass
            except Exception as e:
                skipped.append({"path": fp, "reason": str(e)})
        return Response({
            "scanned": len(files),
            "images_created": len(created),
            "jobs_created": len(jobs),
            "job_ids": jobs,
            "skipped": skipped,
        })


class ActaViewSet(viewsets.ModelViewSet):
    queryset = Acta.objects.select_related(
        "election_process", "department", "province", "district", "source_image",
    ).all()
    serializer_class = ActaSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = [
        "acta_type", "status", "election_process", "department", "province", "district",
    ]
    search_fields = ["unique_id", "table_number"]
    ordering_fields = ["created_at", "id"]

    @action(detail=True, methods=["post"], url_path="reprocess")
    def reprocess(self, request, pk=None):
        acta = self.get_object()
        if not acta.source_image:
            return Response({"error": "Acta sin imagen asociada"}, status=status.HTTP_400_BAD_REQUEST)
        proc = ActaProcessor()
        job = proc.create_job_for_image(acta.source_image, params={"acta_id": acta.id})
        job.acta = acta
        job.save(update_fields=["acta"])
        acta.status = "pendiente"
        acta.save(update_fields=["status"])
        try:
            from .tasks import run_processing_job
            run_processing_job.delay(job.id)
        except Exception:
            pass
        return Response({"job": job.id, "status": "enqueued"})

    @action(detail=False, methods=["get"], url_path="export/xlsx")
    def export_xlsx(self, request):
        ids = request.query_params.get("ids") or ""
        if ids:
            qs = self.filter_queryset(self.queryset.filter(id__in=[int(x) for x in ids.split(",") if x.isdigit()]))
        else:
            qs = self.filter_queryset(self.queryset)
        qs = qs[:5000]
        wb = export_actas_xlsx(qs)
        bio = io.BytesIO()
        wb.save(bio)
        bio.seek(0)
        response = HttpResponse(
            bio.read(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = "attachment; filename=actas_export.xlsx"
        return response


class ActaTranscriptionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ActaTranscription.objects.select_related("acta", "processing_job").all()
    serializer_class = ActaTranscriptionSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["provider", "validation_status", "acta"]
    search_fields = ["provider", "model", "acta__unique_id"]


class ActaVoteEntryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ActaVoteEntry.objects.select_related("acta", "organization", "transcription").all()
    serializer_class = ActaVoteEntrySerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["acta", "entry_category", "data_origin", "organization", "is_corrected"]
    search_fields = ["candidate_name", "position"]


class ProcessingJobViewSet(viewsets.ModelViewSet):
    queryset = ProcessingJob.objects.select_related("acta_image", "acta").all()
    serializer_class = ProcessingJobSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["status", "job_type", "priority", "acta", "acta_image"]
    search_fields = ["error_message"]

    @action(detail=True, methods=["post"], url_path="run")
    def run_job(self, request, pk=None):
        job = self.get_object()
        proc = ActaProcessor(job=job)
        result = {"ok": False}
        if job.acta_image:
            r = proc.process_acta_image(job.acta_image)
            result = {
                "ok": r.ok,
                "acta_id": r.acta_id,
                "status": r.status,
                "error": r.error,
            }
        return Response(result)


class ProcessingAlertViewSet(viewsets.ModelViewSet):
    queryset = ProcessingAlert.objects.select_related("acta", "job", "acknowledged_by").all()
    serializer_class = ProcessingAlertSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["severity", "alert_type", "acknowledged", "acta"]


class ManualReviewQueueViewSet(viewsets.ModelViewSet):
    queryset = ManualReviewQueue.objects.select_related("acta", "assigned_to", "closed_by", "transcription").all()
    serializer_class = ManualReviewQueueSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["status", "assigned_to", "acta"]

    @action(detail=True, methods=["post"], url_path="claim")
    def claim(self, request, pk=None):
        item = self.get_object()
        item.assigned_to = request.user
        item.status = "en_revision"
        from django.utils import timezone
        item.started_review_at = item.started_review_at or timezone.now()
        item.save()
        return Response({"status": item.status, "assigned_to": request.user.username})

    @action(detail=True, methods=["post"], url_path="close")
    def close(self, request, pk=None):
        item = self.get_object()
        from django.utils import timezone
        item.status = "cerrado"
        item.closed_by = request.user
        item.closed_at = timezone.now()
        item.resolution_notes = request.data.get("notes") or item.resolution_notes
        item.save()
        return Response({"status": "closed"})


class HumanCorrectionViewSet(viewsets.ModelViewSet):
    queryset = HumanCorrection.objects.select_related("review_item", "acta", "vote_entry", "corrected_by").all()
    serializer_class = HumanCorrectionSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["acta", "review_item", "corrected_by", "field_name"]


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = AuditLog.objects.select_related("actor").all()
    serializer_class = AuditLogSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ["action", "model_name", "actor"]
    search_fields = ["object_id", "old_value", "new_value"]


class AIProviderSettingsViewSet(viewsets.ModelViewSet):
    queryset = AIProviderSettings.objects.all()
    serializer_class = AIProviderSettingsSerializer
    permission_classes = [IsAdminUser]

    @action(detail=True, methods=["post"], url_path="ping")
    def ping_provider(self, request, pk=None):
        obj = self.get_object()
        ok, msg = obj.test_connection()
        return Response({"ok": ok, "message": msg}, status=status.HTTP_200_OK if ok else status.HTTP_503_SERVICE_UNAVAILABLE)
