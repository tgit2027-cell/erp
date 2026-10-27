from rest_framework import serializers

from .models import StockExample


class StockExampleSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source="item.item_code", read_only=True)
    item_description = serializers.CharField(source="item.description", read_only=True)
    brand_name = serializers.CharField(source="item.brand.name", read_only=True)
    brand_code = serializers.CharField(source="item.brand.code", read_only=True)
    variant_sku = serializers.CharField(source="variant.sku", read_only=True)
    variant_description = serializers.CharField(source="variant.description", read_only=True)
    location_name = serializers.CharField(source="location.name", read_only=True)
    unit_code = serializers.CharField(source="responsible_business_unit.code", read_only=True)
    unit_name = serializers.CharField(source="responsible_business_unit.name", read_only=True)

    class Meta:
        model = StockExample
        fields = (
            "id",
            "item_code",
            "item_description",
            "brand_name",
            "brand_code",
            "variant_sku",
            "variant_description",
            "location_name",
            "unit_code",
            "unit_name",
            "quantity",
            "is_synthetic",
        )
