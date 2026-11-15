import core.fields
import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('contacts', '0001_initial'),
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='MetaConnection',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user_access_token', core.fields.EncryptedTextField(blank=True)),
                ('token_expires_at', models.DateTimeField(blank=True, null=True)),
                ('meta_user_id', models.CharField(blank=True, max_length=128)),
                ('meta_user_name', models.CharField(blank=True, max_length=255)),
                ('status', models.CharField(choices=[('connected', 'Connected'), ('expired', 'Expired'), ('error', 'Error'), ('disconnected', 'Disconnected')], default='connected', max_length=20)),
                ('last_checked_at', models.DateTimeField(blank=True, null=True)),
                ('last_error', models.TextField(blank=True)),
                ('alert_notified_status', models.CharField(blank=True, max_length=20)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='MetaPage',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('page_id', models.CharField(max_length=128, unique=True)),
                ('name', models.CharField(max_length=255)),
                ('instagram_business_account_id', models.CharField(blank=True, max_length=128)),
                ('page_access_token', core.fields.EncryptedTextField(blank=True)),
                ('selected', models.BooleanField(default=False)),
                ('subscribed', models.BooleanField(default=False)),
                ('connection', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pages', to='leads.metaconnection')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
            ],
            options={
                'ordering': ['name', '-created_at'],
            },
        ),
        migrations.CreateModel(
            name='MetaLeadForm',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('form_id', models.CharField(max_length=128)),
                ('name', models.CharField(max_length=255)),
                ('platform', models.CharField(choices=[('facebook', 'Facebook'), ('instagram', 'Instagram')], default='facebook', max_length=20)),
                ('status', models.CharField(blank=True, default='ACTIVE', max_length=32)),
                ('selected', models.BooleanField(default=False)),
                ('questions', models.JSONField(blank=True, default=list)),
                ('field_mapping', models.JSONField(blank=True, default=dict)),
                ('last_synced_at', models.DateTimeField(blank=True, null=True)),
                ('default_tags', models.ManyToManyField(blank=True, related_name='meta_lead_forms', to='contacts.tag')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
                ('page', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='forms', to='leads.metapage')),
            ],
            options={
                'ordering': ['name', '-created_at'],
            },
        ),
        migrations.CreateModel(
            name='MetaLeadEvent',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('leadgen_id', models.CharField(max_length=128)),
                ('raw_payload', models.JSONField(blank=True, default=dict)),
                ('status', models.CharField(choices=[('received', 'Received'), ('processed', 'Processed'), ('failed', 'Failed'), ('ignored', 'Ignored')], default='received', max_length=20)),
                ('attempts', models.PositiveIntegerField(default=0)),
                ('error', models.TextField(blank=True)),
                ('processed_at', models.DateTimeField(blank=True, null=True)),
                ('contact', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='meta_lead_events', to='contacts.contact')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
                ('form', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='lead_events', to='leads.metaleadform')),
                ('page', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='lead_events', to='leads.metapage')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.AddIndex(
            model_name='metaconnection',
            index=models.Index(fields=['tenant', 'status'], name='idx_metaconn_tenant_status'),
        ),
        migrations.AddConstraint(
            model_name='metaconnection',
            constraint=models.UniqueConstraint(fields=('tenant',), name='uniq_metaconn_tenant'),
        ),
        migrations.AddIndex(
            model_name='metapage',
            index=models.Index(fields=['tenant', 'selected'], name='idx_metapage_tenant_selected'),
        ),
        migrations.AddConstraint(
            model_name='metapage',
            constraint=models.UniqueConstraint(fields=('tenant', 'page_id'), name='uniq_metapage_tenant_page_id'),
        ),
        migrations.AddIndex(
            model_name='metaleadform',
            index=models.Index(fields=['tenant', 'page', 'selected'], name='idx_metaleadform_t_pg_sel'),
        ),
        migrations.AddConstraint(
            model_name='metaleadform',
            constraint=models.UniqueConstraint(fields=('tenant', 'form_id'), name='uniq_metaleadform_tenant_formid'),
        ),
        migrations.AddIndex(
            model_name='metaleadevent',
            index=models.Index(fields=['tenant', 'status', '-created_at'], name='idx_metaleadevent_t_st_created'),
        ),
        migrations.AddConstraint(
            model_name='metaleadevent',
            constraint=models.UniqueConstraint(fields=('tenant', 'leadgen_id'), name='uniq_metaleadevent_tenant_leadid'),
        ),
    ]
