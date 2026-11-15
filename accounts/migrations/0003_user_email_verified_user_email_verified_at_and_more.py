import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_remove_tenantapikey_uniq_apikey_tenant_name_and_more'),
        ('core', '0003_tenant_deactivated_at_tenant_deactivation_reason_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='email_verified',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='user',
            name='email_verified_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='TenantInvitation',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('email', models.EmailField(db_index=True, max_length=254)),
                ('role', models.CharField(choices=[('owner', 'Business Owner'), ('admin', 'Business Administrator'), ('marketer', 'Marketer / Sales Representative')], default='marketer', max_length=20)),
                ('token', models.CharField(db_index=True, max_length=255, unique=True)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('accepted', 'Accepted'), ('revoked', 'Revoked'), ('expired', 'Expired')], default='pending', max_length=20)),
                ('expires_at', models.DateTimeField()),
                ('accepted_at', models.DateTimeField(blank=True, null=True)),
                ('invited_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sent_invitations', to=settings.AUTH_USER_MODEL)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['tenant', 'status'], name='idx_invite_tenant_status'), models.Index(fields=['token'], name='idx_invite_token')],
                'constraints': [models.UniqueConstraint(condition=models.Q(('status', 'pending')), fields=('tenant', 'email'), name='uniq_pending_invite_tenant_email')],
            },
        ),
    ]
