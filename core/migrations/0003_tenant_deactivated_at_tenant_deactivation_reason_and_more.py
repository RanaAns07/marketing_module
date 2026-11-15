from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_tenant_assignment_strategy_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='tenant',
            name='deactivated_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='tenant',
            name='deactivation_reason',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='tenant',
            name='grace_period_ends_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
