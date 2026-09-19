from __future__ import annotations

from rest_framework import serializers

from .models import (
    GeoDepartment, GeoProvince, GeoDistrict,
    PoliticalOrganization, ElectionProcess,
    ActaImage, Acta, ActaTranscription, ActaVoteEntry,
    ProcessingJob, ProcessingAlert,
    ManualReviewQueue, AIProviderSettings, HumanCorrection, AuditLog,
)


class GeoDepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = GeoDepartment
        fields = "__all__"


class GeoProvinceSerializer(serializers.ModelSerializer):
    class Meta:
        model = GeoProvince
        fields = "__all__"


class GeoDistrictSerializer(serializers.ModelSerializer):
    class Meta:
        model = GeoDistrict
        fields = "__all__"


class PoliticalOrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = PoliticalOrganization
        fields = "__all__"


class ElectionProcessSerializer(serializers.ModelSerializer):
    class Meta:
        model = ElectionProcess
        fields = "__all__"


class ActaImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActaImage
        fields = "__all__"
        read_only_fields = ("file_hash", "created_at", "updated_at")


class ActaVoteEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = ActaVoteEntry
        fields = "__all__"


class ActaTranscriptionSerializer(serializers.ModelSerializer):
    entries = ActaVoteEntrySerializer(source="entries", many=True, read_only=True)

    class Meta:
        model = ActaTranscription
        fields = "__all__"


class ActaSerializer(serializers.ModelSerializer):
    transcriptions = ActaTranscriptionSerializer(many=True, read_only=True)
    vote_entries = ActaVoteEntrySerializer(many=True, read_only=True)
    location_label = serializers.CharField(read_only=True)

    class Meta:
        model = Acta
        fields = "__all__"


class ProcessingJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProcessingJob
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at", "started_at", "finished_at")


class ProcessingAlertSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProcessingAlert
        fields = "__all__"


class ManualReviewQueueSerializer(serializers.ModelSerializer):
    class Meta:
        model = ManualReviewQueue
        fields = "__all__"


class HumanCorrectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = HumanCorrection
        fields = "__all__"


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = "__all__"


class AIProviderSettingsSerializer(serializers.ModelSerializer):
    api_key = serializers.CharField(write_only=True, required=False, allow_blank=True, default="")
    ping_result = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = AIProviderSettings
        fields = "__all__"
        read_only_fields = ("_api_key_encrypted", "_extra_encrypted")

    def get_ping_result(self, obj) -> str:
        request = self.context.get("request")
        if not request or request.method != "GET":
            return ""
        ok, msg = obj.test_connection()
        return f"{'OK' if ok else 'FAIL'}: {msg}"

    def update(self, instance, validated_data):
        if "api_key" in validated_data:
            instance.api_key = validated_data.pop("api_key") or ""
        if validated_data.get("is_default"):
            AIProviderSettings.objects.exclude(pk=instance.pk).update(is_default=False)
        return super().update(instance, validated_data)

    def create(self, validated_data):
        api_key = validated_data.pop("api_key", "") or ""
        obj = super().create(validated_data)
        obj.api_key = api_key
        obj.save()
        if validated_data.get("is_default"):
            AIProviderSettings.objects.exclude(pk=obj.pk).update(is_default=False)
        return obj


class ImageEnqueueSerializer(serializers.Serializer):
    file_path = serializers.CharField(max_length=512)
    job_type = serializers.CharField(default="process_acta", required=False)
    process_code = serializers.CharField(default="", required=False, allow_blank=True)


class ImageScanDirSerializer(serializers.Serializer):
    directory = serializers.CharField(max_length=512)
    pattern = serializers.CharField(default="*.jpg,*.jpeg,*.png,*.pdf", required=False)
    enqueue = serializers.BooleanField(default=True)
    process_code = serializers.CharField(default="", required=False, allow_blank=True)
