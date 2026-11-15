from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_remove_tenantapikey_uniq_apikey_tenant_name_and_more'),
        ('contacts', '0001_initial'),
        ('core', '0002_tenant_assignment_strategy_and_more'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='contact',
            index=models.Index(fields=['tenant', '-acquired_at'], name='idx_contact_tenant_acquired'),
        ),
    ]
