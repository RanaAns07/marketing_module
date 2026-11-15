import core.fields
import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('accounts', '0002_remove_tenantapikey_uniq_apikey_tenant_name_and_more'),
        ('contacts', '0001_initial'),
        ('core', '0002_tenant_assignment_strategy_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='WebhookEndpoint',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=150)),
                ('url', models.URLField(max_length=500)),
                ('signing_secret', core.fields.EncryptedTextField()),
                ('subscribed_events', models.JSONField(blank=True, default=list)),
                ('active', models.BooleanField(default=True)),
                ('failure_count', models.PositiveIntegerField(default=0)),
                ('disabled_at', models.DateTimeField(blank=True, null=True)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='WebhookDelivery',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('event_name', models.CharField(max_length=100)),
                ('event_id', models.CharField(max_length=255)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('success', 'Success'), ('failed', 'Failed'), ('dead', 'Dead')], default='pending', max_length=20)),
                ('attempts', models.PositiveIntegerField(default=0)),
                ('response_status', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('response_excerpt', models.TextField(blank=True)),
                ('next_retry_at', models.DateTimeField(blank=True, null=True)),
                ('delivered_at', models.DateTimeField(blank=True, null=True)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
                ('endpoint', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='deliveries', to='webhooks.webhookendpoint')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='ExternalEvent',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('event_id', models.CharField(max_length=255)),
                ('type', models.CharField(max_length=100)),
                ('occurred_at', models.DateTimeField()),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('status', models.CharField(choices=[('received', 'Received'), ('processed', 'Processed'), ('failed', 'Failed'), ('ignored', 'Ignored')], default='received', max_length=20)),
                ('result', models.JSONField(blank=True, default=dict)),
                ('error', models.TextField(blank=True)),
                ('attempts', models.PositiveIntegerField(default=0)),
                ('processed_at', models.DateTimeField(blank=True, null=True)),
                ('api_key', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='external_events', to='accounts.tenantapikey')),
                ('contact', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='external_events', to='contacts.contact')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
                'indexes': [models.Index(fields=['tenant', 'status', '-created_at'], name='idx_extevent_t_status_dt'), models.Index(fields=['tenant', 'type', '-created_at'], name='idx_extevent_t_type_dt')],
                'constraints': [models.UniqueConstraint(fields=('tenant', 'event_id'), name='uniq_extevent_tenant_eventid')],
            },
        ),
        migrations.AddIndex(
            model_name='webhookendpoint',
            index=models.Index(fields=['tenant', 'active'], name='idx_whendpoint_t_active'),
        ),
        migrations.AddIndex(
            model_name='webhookendpoint',
            index=models.Index(fields=['tenant', '-created_at'], name='idx_whendpoint_t_created'),
        ),
        migrations.AddIndex(
            model_name='webhookdelivery',
            index=models.Index(fields=['tenant', 'status', 'next_retry_at'], name='idx_whdeliv_t_st_retry'),
        ),
        migrations.AddIndex(
            model_name='webhookdelivery',
            index=models.Index(fields=['tenant', 'endpoint', '-created_at'], name='idx_whdeliv_t_ep_created'),
        ),
        migrations.AddIndex(
            model_name='webhookdelivery',
            index=models.Index(fields=['tenant', '-created_at', '-id'], name='idx_whdeliv_t_created_id'),
        ),
    ]
