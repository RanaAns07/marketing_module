import core.fields
import django.db.models.deletion
import integrations.models
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='ChannelConnection',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('channel', models.CharField(choices=[('email', 'Email'), ('sms', 'SMS'), ('whatsapp', 'WhatsApp')], max_length=20)),
                ('provider', models.CharField(choices=[('sendgrid', 'SendGrid'), ('twilio', 'Twilio')], max_length=50)),
                ('credentials', core.fields.EncryptedJSONField(blank=True, default=dict)),
                ('sender_identity', models.CharField(max_length=255)),
                ('sender_name', models.CharField(blank=True, max_length=255)),
                ('webhook_token', models.CharField(default=integrations.models.generate_webhook_token, max_length=128, unique=True)),
                ('status', models.CharField(choices=[('active', 'Active'), ('disconnected', 'Disconnected'), ('error', 'Error'), ('pending', 'Pending')], default='active', max_length=20)),
                ('last_checked_at', models.DateTimeField(blank=True, null=True)),
                ('last_error', models.TextField(blank=True)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
            ],
            options={
                'ordering': ['channel', '-created_at'],
            },
        ),
        migrations.CreateModel(
            name='InboundWebhookEvent',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('provider', models.CharField(max_length=50)),
                ('channel', models.CharField(max_length=20)),
                ('event_type', models.CharField(choices=[('inbound', 'Inbound'), ('status', 'Status'), ('events', 'Events')], default='inbound', max_length=32)),
                ('raw_payload', models.JSONField(blank=True, default=dict)),
                ('raw_body', models.TextField(blank=True)),
                ('headers', models.JSONField(blank=True, default=dict)),
                ('processed', models.BooleanField(default=False)),
                ('processed_at', models.DateTimeField(blank=True, null=True)),
                ('error', models.TextField(blank=True)),
                ('connection', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='webhook_events', to='integrations.channelconnection')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.AddIndex(
            model_name='channelconnection',
            index=models.Index(fields=['tenant', 'channel'], name='idx_channelconn_tenant_chan'),
        ),
        migrations.AddIndex(
            model_name='channelconnection',
            index=models.Index(fields=['tenant', 'status'], name='idx_channelconn_tenant_status'),
        ),
        migrations.AddConstraint(
            model_name='channelconnection',
            constraint=models.UniqueConstraint(fields=('tenant', 'channel'), name='uniq_channelconn_tenant_channel'),
        ),
        migrations.AddConstraint(
            model_name='channelconnection',
            constraint=models.UniqueConstraint(condition=models.Q(('channel__in', ['sms', 'whatsapp']), models.Q(('sender_identity', ''), _negated=True)), fields=('sender_identity',), name='uniq_channelconn_sender_number'),
        ),
        migrations.AddIndex(
            model_name='inboundwebhookevent',
            index=models.Index(fields=['tenant', 'processed', '-created_at'], name='idx_inwebhook_tenant_proc_dt'),
        ),
    ]
