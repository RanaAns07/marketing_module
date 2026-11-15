from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
        ('core', '0002_tenant_assignment_strategy_and_more'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='tenantapikey',
            name='uniq_apikey_tenant_name',
        ),
        migrations.AddField(
            model_name='tenantapikey',
            name='revoked_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='tenantapikey',
            name='scopes',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddConstraint(
            model_name='tenantapikey',
            constraint=models.UniqueConstraint(condition=models.Q(('is_active', True), ('revoked_at__isnull', True)), fields=('tenant', 'name'), name='uniq_apikey_tenant_name'),
        ),
    ]
