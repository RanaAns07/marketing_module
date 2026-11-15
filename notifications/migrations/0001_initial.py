
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Notification',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('notification_type', models.CharField(choices=[('new_lead', 'New Lead Notification'), ('incoming_message', 'Incoming Message Notification'), ('conversation_assigned', 'Conversation Assignment Notification'), ('conversation_reassigned', 'Conversation Reassignment Notification'), ('internal_task', 'Internal Task Notification'), ('workflow_integration_alert', 'Workflow or Integration Alert')], max_length=40)),
                ('title', models.CharField(max_length=255)),
                ('body', models.TextField(blank=True)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('link', models.CharField(blank=True, max_length=500)),
                ('is_read', models.BooleanField(default=False)),
                ('read_at', models.DateTimeField(blank=True, null=True)),
                ('email_sent', models.BooleanField(default=False)),
                ('recipient', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='notifications', to=settings.AUTH_USER_MODEL)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
                'indexes': [models.Index(fields=['tenant', 'recipient', 'is_read', '-created_at'], name='idx_notif_tenant_rec_read_dt'), models.Index(fields=['tenant', 'notification_type', '-created_at'], name='idx_notif_tenant_type_dt')],
            },
        ),
    ]
