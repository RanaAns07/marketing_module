import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '0002_tenant_assignment_strategy_and_more'),
        ('messaging', '0002_message_idx_msg_tenant_dir_created'),
    ]

    operations = [
        migrations.CreateModel(
            name='RateCard',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('channel', models.CharField(choices=[('email', 'Email'), ('sms', 'SMS'), ('whatsapp', 'WhatsApp')], max_length=20)),
                ('destination_country', models.CharField(blank=True, default='', max_length=2)),
                ('whatsapp_category', models.CharField(blank=True, choices=[('marketing', 'Marketing'), ('utility', 'Utility'), ('authentication', 'Authentication'), ('service', 'Service')], default='', max_length=20)),
                ('unit_cost', models.DecimalField(decimal_places=5, max_digits=10)),
                ('currency', models.CharField(default='USD', max_length=3)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('tenant', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='rate_cards', to='core.tenant')),
            ],
            options={
                'ordering': ['channel', 'destination_country', 'whatsapp_category'],
                'indexes': [models.Index(fields=['tenant', 'channel', 'destination_country'], name='idx_ratecard_t_ch_country'), models.Index(fields=['channel', 'destination_country'], name='idx_ratecard_ch_country')],
                'constraints': [models.UniqueConstraint(condition=models.Q(('tenant__isnull', False)), fields=('tenant', 'channel', 'destination_country', 'whatsapp_category'), name='uniq_ratecard_tenant_scope'), models.UniqueConstraint(condition=models.Q(('tenant__isnull', True)), fields=('channel', 'destination_country', 'whatsapp_category'), name='uniq_ratecard_global_scope')],
            },
        ),
        migrations.CreateModel(
            name='UsageRecord',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('channel', models.CharField(choices=[('email', 'Email'), ('sms', 'SMS'), ('whatsapp', 'WhatsApp')], max_length=20)),
                ('quantity', models.PositiveIntegerField(default=1)),
                ('destination_country', models.CharField(blank=True, default='', max_length=2)),
                ('whatsapp_category', models.CharField(blank=True, choices=[('marketing', 'Marketing'), ('utility', 'Utility'), ('authentication', 'Authentication'), ('service', 'Service')], default='', max_length=20)),
                ('unit_cost', models.DecimalField(decimal_places=5, max_digits=10)),
                ('total_cost', models.DecimalField(decimal_places=5, max_digits=12)),
                ('currency', models.CharField(default='USD', max_length=3)),
                ('status', models.CharField(blank=True, default='', max_length=20)),
                ('occurred_at', models.DateTimeField()),
                ('message', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='usage_record', to='messaging.message')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
            ],
            options={
                'ordering': ['-occurred_at', '-id'],
                'indexes': [models.Index(fields=['tenant', 'occurred_at'], name='idx_usagerec_t_occurred'), models.Index(fields=['tenant', 'channel', 'occurred_at'], name='idx_usagerec_t_ch_occurred'), models.Index(fields=['tenant', 'status', 'occurred_at'], name='idx_usagerec_t_st_occurred')],
            },
        ),
    ]
