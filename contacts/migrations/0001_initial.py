
import django.contrib.postgres.indexes
import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('accounts', '0001_initial'),
        ('core', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='LeadStatus',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=100)),
                ('order', models.PositiveIntegerField(default=0)),
                ('is_default', models.BooleanField(default=False)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
            ],
            options={
                'ordering': ['order', 'created_at'],
            },
        ),
        migrations.CreateModel(
            name='Contact',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('first_name', models.CharField(blank=True, max_length=150)),
                ('last_name', models.CharField(blank=True, max_length=150)),
                ('full_name', models.CharField(blank=True, max_length=300)),
                ('email', models.EmailField(blank=True, max_length=254, null=True)),
                ('phone', models.CharField(blank=True, max_length=32, null=True)),
                ('city', models.CharField(blank=True, max_length=120)),
                ('country', models.CharField(blank=True, max_length=120)),
                ('timezone', models.CharField(blank=True, default='UTC', max_length=64)),
                ('source_channel', models.CharField(blank=True, max_length=100)),
                ('source_page', models.CharField(blank=True, max_length=255)),
                ('source_form', models.CharField(blank=True, max_length=255)),
                ('campaign', models.CharField(blank=True, max_length=255)),
                ('ad_set', models.CharField(blank=True, max_length=255)),
                ('ad', models.CharField(blank=True, max_length=255)),
                ('acquired_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('converted_at', models.DateTimeField(blank=True, null=True)),
                ('external_id', models.CharField(blank=True, max_length=255)),
                ('last_activity_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('inactive_notified_at', models.DateTimeField(blank=True, null=True)),
                ('email_subscription', models.CharField(choices=[('subscribed', 'Subscribed'), ('unsubscribed', 'Unsubscribed'), ('bounced', 'Bounced')], default='subscribed', max_length=20)),
                ('sms_subscription', models.CharField(choices=[('subscribed', 'Subscribed'), ('unsubscribed', 'Unsubscribed'), ('bounced', 'Bounced')], default='subscribed', max_length=20)),
                ('whatsapp_subscription', models.CharField(choices=[('subscribed', 'Subscribed'), ('unsubscribed', 'Unsubscribed'), ('bounced', 'Bounced')], default='subscribed', max_length=20)),
                ('custom_fields', models.JSONField(blank=True, default=dict)),
                ('assigned_to', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assigned_contacts', to='accounts.membership')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
                ('lead_status', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='contacts', to='contacts.leadstatus')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='Tag',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=100)),
                ('color', models.CharField(default='#3B82F6', max_length=32)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='ContactTag',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('contact', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='contact_tags', to='contacts.contact')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
                ('tag', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='contact_tags', to='contacts.tag')),
            ],
            options={
                'ordering': ['created_at'],
            },
        ),
        migrations.AddField(
            model_name='contact',
            name='tags',
            field=models.ManyToManyField(blank=True, related_name='contacts', through='contacts.ContactTag', to='contacts.tag'),
        ),
        migrations.CreateModel(
            name='Task',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('title', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True)),
                ('due_at', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(choices=[('open', 'Open'), ('in_progress', 'In Progress'), ('completed', 'Completed'), ('cancelled', 'Cancelled')], default='open', max_length=20)),
                ('from_workflow', models.BooleanField(default=False)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('due_soon_notified_at', models.DateTimeField(blank=True, null=True)),
                ('assignee', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assigned_tasks', to='accounts.membership')),
                ('contact', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='tasks', to='contacts.contact')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_tasks', to=settings.AUTH_USER_MODEL)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
            ],
            options={
                'ordering': ['due_at', '-created_at'],
            },
        ),
        migrations.CreateModel(
            name='Appointment',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('starts_at', models.DateTimeField()),
                ('status', models.CharField(choices=[('booked', 'Booked'), ('cancelled', 'Cancelled'), ('completed', 'Completed'), ('rescheduled', 'Rescheduled')], default='booked', max_length=20)),
                ('external_ref', models.CharField(blank=True, max_length=255)),
                ('location', models.CharField(blank=True, max_length=255)),
                ('notes', models.TextField(blank=True)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
                ('contact', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='appointments', to='contacts.contact')),
            ],
            options={
                'ordering': ['-starts_at', '-created_at'],
                'indexes': [models.Index(fields=['tenant', 'contact', '-starts_at'], name='idx_appt_tenant_ct_starts'), models.Index(fields=['tenant', 'status', 'starts_at'], name='idx_appt_tenant_status_start')],
            },
        ),
        migrations.CreateModel(
            name='Activity',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('activity_type', models.CharField(choices=[('form_submission', 'Form Submission'), ('message_in', 'Incoming Message'), ('message_out', 'Outgoing Message'), ('status_change', 'Status Change'), ('tag_change', 'Tag Change'), ('workflow_action', 'Workflow Action'), ('note', 'Internal Note'), ('assignment', 'Assignment'), ('event', 'Event'), ('appointment', 'Appointment')], max_length=32)),
                ('description', models.TextField(blank=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='contact_activities', to=settings.AUTH_USER_MODEL)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
                ('contact', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='activities', to='contacts.contact')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
                'indexes': [models.Index(fields=['tenant', 'contact', '-created_at', '-id'], name='idx_activity_tenant_ct_dt'), models.Index(fields=['tenant', 'activity_type', '-created_at'], name='idx_activity_tenant_type_dt')],
            },
        ),
        migrations.CreateModel(
            name='CustomFieldDefinition',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('key', models.SlugField(max_length=100)),
                ('label', models.CharField(max_length=150)),
                ('field_type', models.CharField(choices=[('text', 'Text'), ('number', 'Number'), ('date', 'Date'), ('select', 'Select'), ('bool', 'Boolean')], default='text', max_length=20)),
                ('options', models.JSONField(blank=True, default=list)),
                ('is_required', models.BooleanField(default=False)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
            ],
            options={
                'ordering': ['label', 'created_at'],
                'indexes': [models.Index(fields=['tenant', 'key'], name='idx_customfield_tenant_key')],
                'constraints': [models.UniqueConstraint(fields=('tenant', 'key'), name='uniq_customfield_tenant_key')],
            },
        ),
        migrations.AddIndex(
            model_name='leadstatus',
            index=models.Index(fields=['tenant', 'order'], name='idx_leadstatus_tenant_order'),
        ),
        migrations.AddIndex(
            model_name='leadstatus',
            index=models.Index(fields=['tenant', 'is_default'], name='idx_leadstatus_tenant_def'),
        ),
        migrations.AddConstraint(
            model_name='leadstatus',
            constraint=models.UniqueConstraint(fields=('tenant', 'name'), name='uniq_leadstatus_tenant_name'),
        ),
        migrations.AddIndex(
            model_name='tag',
            index=models.Index(fields=['tenant', 'name'], name='idx_tag_tenant_name'),
        ),
        migrations.AddConstraint(
            model_name='tag',
            constraint=models.UniqueConstraint(fields=('tenant', 'name'), name='uniq_tag_tenant_name'),
        ),
        migrations.AddIndex(
            model_name='contacttag',
            index=models.Index(fields=['tenant', 'tag', 'contact'], name='idx_contacttag_tenant_tg_ct'),
        ),
        migrations.AddIndex(
            model_name='contacttag',
            index=models.Index(fields=['tenant', 'contact', 'tag'], name='idx_contacttag_tenant_ct_tg'),
        ),
        migrations.AddConstraint(
            model_name='contacttag',
            constraint=models.UniqueConstraint(fields=('tenant', 'contact', 'tag'), name='uniq_contacttag_tenant_ct_tg'),
        ),
        migrations.AddIndex(
            model_name='contact',
            index=models.Index(fields=['tenant', 'lead_status'], name='idx_contact_tenant_status'),
        ),
        migrations.AddIndex(
            model_name='contact',
            index=models.Index(fields=['tenant', 'assigned_to'], name='idx_contact_tenant_assignee'),
        ),
        migrations.AddIndex(
            model_name='contact',
            index=models.Index(fields=['tenant', '-created_at'], name='idx_contact_tenant_created'),
        ),
        migrations.AddIndex(
            model_name='contact',
            index=models.Index(fields=['tenant', '-last_activity_at'], name='idx_contact_tenant_activity'),
        ),
        migrations.AddIndex(
            model_name='contact',
            index=models.Index(fields=['tenant', 'source_channel'], name='idx_contact_tenant_source'),
        ),
        migrations.AddIndex(
            model_name='contact',
            index=models.Index(fields=['tenant', 'campaign'], name='idx_contact_tenant_campaign'),
        ),
        migrations.AddIndex(
            model_name='contact',
            index=django.contrib.postgres.indexes.GinIndex(fields=['custom_fields'], name='idx_contact_custom_fields_gin'),
        ),
        migrations.AddConstraint(
            model_name='contact',
            constraint=models.UniqueConstraint(condition=models.Q(('email__isnull', False), models.Q(('email', ''), _negated=True)), fields=('tenant', 'email'), name='uniq_contact_tenant_email_notnull'),
        ),
        migrations.AddConstraint(
            model_name='contact',
            constraint=models.UniqueConstraint(condition=models.Q(('phone__isnull', False), models.Q(('phone', ''), _negated=True)), fields=('tenant', 'phone'), name='uniq_contact_tenant_phone_notnull'),
        ),
        migrations.AddIndex(
            model_name='task',
            index=models.Index(fields=['tenant', 'assignee', 'status', 'due_at'], name='idx_task_tenant_asgn_st_due'),
        ),
        migrations.AddIndex(
            model_name='task',
            index=models.Index(fields=['tenant', 'contact', '-created_at'], name='idx_task_tenant_ct_dt'),
        ),
    ]
