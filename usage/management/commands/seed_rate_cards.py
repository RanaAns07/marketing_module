from decimal import Decimal
from django.conf import settings
from django.core.management.base import BaseCommand
from messaging.models import Channel, WhatsAppCategory
from usage.models import RateCard

GLOBAL_RATE_DEFAULTS = [
    {
        "channel": Channel.EMAIL,
        "destination_country": "",
        "whatsapp_category": "",
        "unit_cost": Decimal("0.00100"),
    },
    {
        "channel": Channel.SMS,
        "destination_country": "",
        "whatsapp_category": "",
        "unit_cost": Decimal("0.01500"),
    },
    {
        "channel": Channel.WHATSAPP,
        "destination_country": "",
        "whatsapp_category": WhatsAppCategory.MARKETING,
        "unit_cost": Decimal("0.05000"),
    },
    {
        "channel": Channel.WHATSAPP,
        "destination_country": "",
        "whatsapp_category": WhatsAppCategory.UTILITY,
        "unit_cost": Decimal("0.03000"),
    },
    {
        "channel": Channel.WHATSAPP,
        "destination_country": "",
        "whatsapp_category": WhatsAppCategory.AUTHENTICATION,
        "unit_cost": Decimal("0.03000"),
    },
    {
        "channel": Channel.WHATSAPP,
        "destination_country": "",
        "whatsapp_category": WhatsAppCategory.SERVICE,
        "unit_cost": Decimal("0.01500"),
    },
]


def seed_global_rate_cards() -> int:
    default_currency = getattr(settings, "DEFAULT_USAGE_CURRENCY", "USD")
    created_count = 0
    for item in GLOBAL_RATE_DEFAULTS:
        obj, created = RateCard.objects.get_or_create(
            tenant=None,
            channel=item["channel"],
            destination_country=item["destination_country"],
            whatsapp_category=item["whatsapp_category"],
            defaults={
                "unit_cost": item["unit_cost"],
                "currency": default_currency,
                "is_active": True,
            },
        )
        if created:
            created_count += 1
    return created_count


class Command(BaseCommand):
    def handle(self, *args, **options):
        count = seed_global_rate_cards()
        self.stdout.write(f"Seeded {count} global rate cards.")
