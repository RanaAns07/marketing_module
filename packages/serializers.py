from rest_framework import serializers
from core.serializers import TenantScopedSerializer
from packages.models import Package


class PackageSerializer(TenantScopedSerializer):
    class Meta:
        model = Package
        fields = [
            "id",
            "external_id",
            "name",
            "description",
            "price",
            "currency",
            "billing_interval",
            "status",
            "external_url",
            "features",
            "metadata",
            "synced_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "synced_at", "created_at", "updated_at"]


class PackageSyncItemSerializer(serializers.Serializer):
    external_id = serializers.CharField(max_length=255)
    name = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    price = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, default=0.00)
    currency = serializers.CharField(max_length=10, required=False, default="USD")
    billing_interval = serializers.ChoiceField(
        choices=Package.BillingInterval.choices,
        required=False,
        default=Package.BillingInterval.ONE_TIME,
    )
    status = serializers.ChoiceField(
        choices=Package.Status.choices,
        required=False,
        default=Package.Status.ACTIVE,
    )
    external_url = serializers.URLField(max_length=500, required=False, allow_blank=True, default="")
    features = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    metadata = serializers.DictField(required=False, default=dict)


class BulkPackageSyncSerializer(serializers.Serializer):
    packages = serializers.ListField(child=PackageSyncItemSerializer(), allow_empty=False)


class PackageSyncResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    total = serializers.IntegerField()
    created = serializers.IntegerField()
    updated = serializers.IntegerField()
    packages = PackageSerializer(many=True)
