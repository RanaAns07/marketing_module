import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('contacts', '0001_initial'),
        ('core', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Broadcast',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(blank=True, max_length=200)),
                ('audience_filter', models.JSONField(blank=True, default=dict)),
                ('scheduled_at', models.DateTimeField(blank=True, null=True)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('scheduled', 'Scheduled'), ('running', 'Running'), ('completed', 'Completed'), ('cancelled', 'Cancelled')], default='draft', max_length=20)),
                ('total_recipients', models.PositiveIntegerField(default=0)),
                ('sent_count', models.PositiveIntegerField(default=0)),
                ('delivered_count', models.PositiveIntegerField(default=0)),
                ('failed_count', models.PositiveIntegerField(default=0)),
                ('skipped_count', models.PositiveIntegerField(default=0)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_broadcasts', to=settings.AUTH_USER_MODEL)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='MessageTemplate',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('channel', models.CharField(choices=[('email', 'Email'), ('sms', 'SMS'), ('whatsapp', 'WhatsApp')], max_length=20)),
                ('name', models.CharField(max_length=200)),
                ('subject', models.CharField(blank=True, max_length=255)),
                ('body', models.TextField()),
                ('variables', models.JSONField(blank=True, default=list)),
                ('whatsapp_content_sid', models.CharField(blank=True, max_length=128)),
                ('whatsapp_category', models.CharField(blank=True, choices=[('marketing', 'Marketing'), ('utility', 'Utility'), ('authentication', 'Authentication'), ('service', 'Service')], default='utility', max_length=32)),
                ('approval_status', models.CharField(choices=[('approved', 'Approved'), ('pending', 'Pending'), ('rejected', 'Rejected'), ('not_required', 'Not Required')], default='approved', max_length=32)),
                ('active', models.BooleanField(default=True)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
            ],
            options={
                'ordering': ['channel', 'name', '-created_at'],
            },
        ),
        migrations.CreateModel(
            name='Message',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('channel', models.CharField(choices=[('email', 'Email'), ('sms', 'SMS'), ('whatsapp', 'WhatsApp')], max_length=20)),
                ('direction', models.CharField(choices=[('in', 'Inbound'), ('out', 'Outbound')], max_length=10)),
                ('status', models.CharField(choices=[('queued', 'Queued'), ('sent', 'Sent'), ('delivered', 'Delivered'), ('read', 'Read'), ('undelivered', 'Undelivered'), ('failed', 'Failed'), ('bounced', 'Bounced')], default='queued', max_length=20)),
                ('body', models.TextField(blank=True)),
                ('subject', models.CharField(blank=True, max_length=255)),
                ('provider', models.CharField(blank=True, max_length=50)),
                ('provider_message_id', models.CharField(blank=True, max_length=255, null=True)),
                ('segments', models.PositiveIntegerField(default=1)),
                ('destination_country', models.CharField(blank=True, max_length=16)),
                ('whatsapp_category', models.CharField(blank=True, choices=[('marketing', 'Marketing'), ('utility', 'Utility'), ('authentication', 'Authentication'), ('service', 'Service')], max_length=32)),
                ('source', models.CharField(choices=[('workflow', 'Workflow'), ('broadcast', 'Broadcast'), ('manual', 'Manual')], default='manual', max_length=20)),
                ('error', models.TextField(blank=True)),
                ('idempotency_key', models.CharField(blank=True, max_length=255, null=True)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('delivered_at', models.DateTimeField(blank=True, null=True)),
                ('read_at', models.DateTimeField(blank=True, null=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('broadcast', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='messages', to='messaging.broadcast')),
                ('contact', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='messages', to='contacts.contact')),
                ('sent_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sent_messages', to=settings.AUTH_USER_MODEL)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
                ('template', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='messages', to='messaging.messagetemplate')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.AddField(
            model_name='broadcast',
            name='template',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='broadcasts', to='messaging.messagetemplate'),
        ),
        migrations.AddIndex(
            model_name='messagetemplate',
            index=models.Index(fields=['tenant', 'channel', 'active'], name='idx_msgtemplate_tenant_ch_act'),
        ),
        migrations.AddConstraint(
            model_name='messagetemplate',
            constraint=models.UniqueConstraint(fields=('tenant', 'channel', 'name'), name='uniq_msgtemplate_tenant_ch_name'),
        ),
        migrations.AddConstraint(
            model_name='messagetemplate',
            constraint=models.UniqueConstraint(condition=models.Q(('whatsapp_content_sid__isnull', False), models.Q(('whatsapp_content_sid', ''), _negated=True)), fields=('tenant', 'whatsapp_content_sid'), name='uniq_msgtemplate_tenant_wasid'),
        ),
        migrations.AddIndex(
            model_name='message',
            index=models.Index(fields=['tenant', 'contact', '-created_at'], name='idx_msg_tenant_contact_created'),
        ),
        migrations.AddIndex(
            model_name='message',
            index=models.Index(fields=['tenant', 'status', '-created_at'], name='idx_msg_tenant_status_created'),
        ),
        migrations.AddIndex(
            model_name='message',
            index=models.Index(fields=['tenant', 'contact', 'channel', 'direction', '-created_at'], name='idx_msg_tenant_ct_ch_dir_dt'),
        ),
        migrations.AddConstraint(
            model_name='message',
            constraint=models.UniqueConstraint(condition=models.Q(('provider_message_id__isnull', False), models.Q(('provider_message_id', ''), _negated=True)), fields=('tenant', 'provider', 'provider_message_id'), name='uniq_msg_tenant_prov_msgid'),
        ),
        migrations.AddConstraint(
            model_name='message',
            constraint=models.UniqueConstraint(condition=models.Q(('idempotency_key__isnull', False), models.Q(('idempotency_key', ''), _negated=True)), fields=('tenant', 'idempotency_key'), name='uniq_msg_tenant_idemp_key'),
        ),
        migrations.AddIndex(
            model_name='broadcast',
            index=models.Index(fields=['tenant', 'status', 'scheduled_at'], name='idx_broadcast_tenant_st_sched'),
        ),
        migrations.AddIndex(
            model_name='broadcast',
            index=models.Index(fields=['tenant', '-created_at'], name='idx_broadcast_tenant_created'),
        ),
    ]
