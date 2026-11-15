
import core.fields
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='User',
            fields=[
                ('password', models.CharField(max_length=128, verbose_name='password')),
                ('last_login', models.DateTimeField(blank=True, null=True, verbose_name='last login')),
                ('is_superuser', models.BooleanField(default=False, help_text='Designates that this user has all permissions without explicitly assigning them.', verbose_name='superuser status')),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('email', models.EmailField(db_index=True, max_length=254, unique=True)),
                ('first_name', models.CharField(blank=True, max_length=150)),
                ('last_name', models.CharField(blank=True, max_length=150)),
                ('phone', models.CharField(blank=True, max_length=32)),
                ('is_active', models.BooleanField(default=True)),
                ('is_staff', models.BooleanField(default=False)),
                ('email_notifications_enabled', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('groups', models.ManyToManyField(blank=True, help_text='The groups this user belongs to. A user will get all permissions granted to each of their groups.', related_name='user_set', related_query_name='user', to='auth.group', verbose_name='groups')),
                ('user_permissions', models.ManyToManyField(blank=True, help_text='Specific permissions for this user.', related_name='user_set', related_query_name='user', to='auth.permission', verbose_name='user permissions')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='Membership',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('role', models.CharField(choices=[('owner', 'Business Owner'), ('admin', 'Business Administrator'), ('marketer', 'Marketer / Sales Representative')], default='marketer', max_length=20)),
                ('is_active', models.BooleanField(default=True)),
                ('is_available', models.BooleanField(default=True)),
                ('email_notifications_enabled', models.BooleanField(default=True)),
                ('notification_preferences', models.JSONField(blank=True, default=dict)),
                ('last_assigned_at', models.DateTimeField(blank=True, null=True)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='memberships', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['created_at'],
            },
        ),
        migrations.CreateModel(
            name='TenantAPIKey',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=120)),
                ('key_id', models.CharField(db_index=True, max_length=64, unique=True)),
                ('secret_encrypted', core.fields.EncryptedTextField()),
                ('is_active', models.BooleanField(default=True)),
                ('last_used_at', models.DateTimeField(blank=True, null=True)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='user',
            index=models.Index(fields=['email'], name='accounts_us_email_74c8d6_idx'),
        ),
        migrations.AddIndex(
            model_name='user',
            index=models.Index(fields=['is_active', '-created_at'], name='accounts_us_is_acti_e28628_idx'),
        ),
        migrations.AddIndex(
            model_name='membership',
            index=models.Index(fields=['tenant', 'role', 'is_active'], name='idx_member_tenant_role_act'),
        ),
        migrations.AddIndex(
            model_name='membership',
            index=models.Index(fields=['tenant', 'is_active', 'is_available', 'last_assigned_at'], name='idx_member_tenant_rr_pool'),
        ),
        migrations.AddConstraint(
            model_name='membership',
            constraint=models.UniqueConstraint(fields=('tenant', 'user'), name='uniq_membership_tenant_user'),
        ),
        migrations.AddIndex(
            model_name='tenantapikey',
            index=models.Index(fields=['tenant', 'is_active'], name='idx_apikey_tenant_active'),
        ),
        migrations.AddIndex(
            model_name='tenantapikey',
            index=models.Index(fields=['tenant', 'key_id'], name='idx_apikey_tenant_keyid'),
        ),
        migrations.AddConstraint(
            model_name='tenantapikey',
            constraint=models.UniqueConstraint(fields=('tenant', 'name'), name='uniq_apikey_tenant_name'),
        ),
    ]
