
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Tenant',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=255)),
                ('slug', models.SlugField(max_length=255, unique=True)),
                ('timezone', models.CharField(default='UTC', max_length=64)),
                ('is_active', models.BooleanField(default=True)),
                ('marketer_can_view_all_contacts', models.BooleanField(default=True)),
                ('notify_admins_on_new_lead', models.BooleanField(default=True)),
                ('assignment_mode', models.CharField(choices=[('manual', 'Manual / Shared Queue'), ('round_robin', 'Round-Robin'), ('by_source', 'Dedicated Owner by Source or Form')], default='manual', max_length=32)),
                ('settings', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['slug'], name='core_tenant_slug_bb8545_idx'), models.Index(fields=['is_active', '-created_at'], name='core_tenant_is_acti_062958_idx')],
            },
        ),
    ]
