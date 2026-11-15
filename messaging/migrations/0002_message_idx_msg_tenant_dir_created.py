from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0002_contact_idx_contact_tenant_acquired'),
        ('core', '0002_tenant_assignment_strategy_and_more'),
        ('messaging', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddIndex(
            model_name='message',
            index=models.Index(fields=['tenant', 'direction', '-created_at'], name='idx_msg_tenant_dir_created'),
        ),
    ]
