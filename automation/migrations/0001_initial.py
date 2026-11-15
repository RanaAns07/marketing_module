import django.db.models.deletion
import django.utils.timezone
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
            name='Workflow',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=200)),
                ('description', models.TextField(blank=True)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('active', 'Active'), ('paused', 'Paused')], default='draft', max_length=20)),
                ('trigger_type', models.CharField(choices=[('new_lead_created', 'New Lead Created'), ('form_submitted', 'Form Submitted'), ('tag_added', 'Tag Added'), ('tag_removed', 'Tag Removed'), ('email_replied', 'Email Received or Replied To'), ('sms_received', 'SMS Received'), ('whatsapp_received', 'WhatsApp Message Received'), ('appointment_booked', 'Appointment Booked'), ('appointment_cancelled', 'Appointment Cancelled'), ('lead_status_changed', 'Lead Status Changed'), ('lead_inactive', 'Lead Becomes Inactive'), ('external_event', 'External Event')], default='new_lead_created', max_length=40)),
                ('trigger_config', models.JSONField(blank=True, default=dict)),
                ('re_entry', models.CharField(choices=[('once', 'Once'), ('allow', 'Allow')], default='once', max_length=20)),
                ('stop_on_reply', models.BooleanField(default=True)),
                ('is_promotional', models.BooleanField(default=False)),
                ('exit_events', models.JSONField(blank=True, default=list)),
                ('entry_step_key', models.CharField(blank=True, max_length=100)),
                ('version', models.PositiveIntegerField(default=1)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='WorkflowEnrollment',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(choices=[('active', 'Active'), ('paused', 'Paused'), ('completed', 'Completed'), ('stopped', 'Stopped'), ('failed', 'Failed')], default='active', max_length=20)),
                ('current_step_key', models.CharField(blank=True, max_length=100)),
                ('next_run_at', models.DateTimeField(blank=True, null=True)),
                ('paused_reason', models.CharField(blank=True, max_length=100)),
                ('waiting_for_event', models.JSONField(blank=True, default=dict)),
                ('started_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('iteration', models.PositiveIntegerField(default=1)),
                ('locked_at', models.DateTimeField(blank=True, null=True)),
                ('contact', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='workflow_enrollments', to='contacts.contact')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
                ('workflow', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='enrollments', to='automation.workflow')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='StepExecution',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('step_key', models.CharField(max_length=100)),
                ('iteration', models.PositiveIntegerField(default=1)),
                ('status', models.CharField(choices=[('success', 'Success'), ('failed', 'Failed'), ('skipped', 'Skipped')], default='success', max_length=20)),
                ('attempts', models.PositiveIntegerField(default=1)),
                ('error', models.TextField(blank=True)),
                ('result', models.JSONField(blank=True, default=dict)),
                ('executed_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
                ('enrollment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='step_executions', to='automation.workflowenrollment')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='WorkflowStep',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('key', models.CharField(max_length=100)),
                ('type', models.CharField(choices=[('action', 'Action'), ('condition', 'Condition'), ('delay', 'Delay')], max_length=20)),
                ('subtype', models.CharField(choices=[('has_tag', 'Has Tag'), ('not_has_tag', 'Does Not Have Tag'), ('status_is', 'Lead Status Is'), ('source_matches', 'Lead Source Matches'), ('replied', 'Has Replied'), ('not_replied', 'Has Not Replied'), ('custom_field_compare', 'Custom Field Compare'), ('campaign_matches', 'Campaign Matches'), ('appointment_not_cancelled', 'Appointment Not Cancelled'), ('send_email', 'Send Email'), ('send_sms', 'Send SMS'), ('send_whatsapp', 'Send WhatsApp'), ('add_tag', 'Add Tag'), ('remove_tag', 'Remove Tag'), ('update_contact_field', 'Update Contact Field'), ('update_lead_status', 'Update Lead Status'), ('assign_marketer', 'Assign Marketer'), ('create_task', 'Create Task'), ('send_internal_notification', 'Send Internal Notification'), ('send_outbound_webhook', 'Send Outbound Webhook'), ('pause_automation', 'Pause Automation'), ('end_automation', 'End Automation'), ('fixed', 'Fixed Duration'), ('until_datetime', 'Until Specific Datetime'), ('relative_to_field', 'Relative to Contact/Appointment Field'), ('wait_for_event', 'Wait for Event')], max_length=40)),
                ('config', models.JSONField(blank=True, default=dict)),
                ('next_key', models.CharField(blank=True, max_length=100)),
                ('true_key', models.CharField(blank=True, max_length=100)),
                ('false_key', models.CharField(blank=True, max_length=100)),
                ('position', models.JSONField(blank=True, default=dict)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
                ('workflow', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='steps', to='automation.workflow')),
            ],
            options={
                'ordering': ['created_at', 'key'],
            },
        ),
        migrations.AddIndex(
            model_name='workflow',
            index=models.Index(fields=['tenant', 'status', 'trigger_type'], name='idx_workflow_t_st_trig'),
        ),
        migrations.AddIndex(
            model_name='workflow',
            index=models.Index(fields=['tenant', '-created_at'], name='idx_workflow_t_created'),
        ),
        migrations.AddIndex(
            model_name='workflowenrollment',
            index=models.Index(fields=['tenant', 'status', 'next_run_at'], name='idx_wfenroll_t_st_nextrun'),
        ),
        migrations.AddIndex(
            model_name='workflowenrollment',
            index=models.Index(fields=['tenant', 'contact'], name='idx_wfenroll_t_contact'),
        ),
        migrations.AddIndex(
            model_name='workflowenrollment',
            index=models.Index(fields=['tenant', 'workflow', '-created_at'], name='idx_wfenroll_t_wf_created'),
        ),
        migrations.AddConstraint(
            model_name='workflowenrollment',
            constraint=models.UniqueConstraint(condition=models.Q(('status__in', ['active', 'paused'])), fields=('tenant', 'workflow', 'contact'), name='uniq_wfenroll_t_wf_ct_open'),
        ),
        migrations.AddIndex(
            model_name='stepexecution',
            index=models.Index(fields=['tenant', 'enrollment', '-executed_at'], name='idx_stepexec_t_enr_execdt'),
        ),
        migrations.AddIndex(
            model_name='stepexecution',
            index=models.Index(fields=['tenant', '-created_at', '-id'], name='idx_stepexec_t_created_id'),
        ),
        migrations.AddConstraint(
            model_name='stepexecution',
            constraint=models.UniqueConstraint(fields=('tenant', 'enrollment', 'step_key', 'iteration'), name='uniq_stepexec_t_enr_key_iter'),
        ),
        migrations.AddIndex(
            model_name='workflowstep',
            index=models.Index(fields=['tenant', 'workflow'], name='idx_wfstep_tenant_wf'),
        ),
        migrations.AddConstraint(
            model_name='workflowstep',
            constraint=models.UniqueConstraint(fields=('tenant', 'workflow', 'key'), name='uniq_wfstep_tenant_wf_key'),
        ),
    ]
