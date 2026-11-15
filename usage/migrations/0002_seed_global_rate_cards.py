from decimal import Decimal
from django.db import migrations


def seed_rates(apps, schema_editor):
    RateCard = apps.get_model("usage", "RateCard")
    defaults = [
        ("email", "", "", Decimal("0.00100")),
        ("sms", "", "", Decimal("0.01500")),
        ("whatsapp", "", "marketing", Decimal("0.05000")),
        ("whatsapp", "", "utility", Decimal("0.03000")),
        ("whatsapp", "", "authentication", Decimal("0.03000")),
        ("whatsapp", "", "service", Decimal("0.01500")),
    ]
    for ch, country, cat, cost in defaults:
        RateCard.objects.get_or_create(
            tenant=None,
            channel=ch,
            destination_country=country,
            whatsapp_category=cat,
            defaults={
                "unit_cost": cost,
                "currency": "USD",
                "is_active": True,
            },
        )


def unseed_rates(apps, schema_editor):
    RateCard = apps.get_model("usage", "RateCard")
    RateCard.objects.filter(tenant__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("usage", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_rates, unseed_rates),
    ]
