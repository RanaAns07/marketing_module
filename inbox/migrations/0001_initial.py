import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('accounts', '0001_initial'),
        ('contacts', '0001_initial'),
        ('core', '0002_tenant_assignment_strategy_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Conversation',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(choices=[('open', 'Open'), ('pending', 'Pending'), ('resolved', 'Resolved')], default='open', max_length=20)),
                ('last_message_at', models.DateTimeField(blank=True, default=django.utils.timezone.now, null=True)),
                ('last_inbound_at', models.DateTimeField(blank=True, null=True)),
                ('last_channel', models.CharField(blank=True, choices=[('email', 'Email'), ('sms', 'SMS'), ('whatsapp', 'WhatsApp')], default='', max_length=20)),
                ('last_message_preview', models.CharField(blank=True, default='', max_length=500)),
                ('unread_count', models.PositiveIntegerField(default=0)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('assigned_to', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assigned_conversations', to='accounts.membership')),
                ('contact', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='conversations', to='contacts.contact')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
            ],
            options={
                'ordering': ['-last_message_at', '-created_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='ConversationNote',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('body', models.TextField()),
                ('author', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='conversation_notes', to=settings.AUTH_USER_MODEL)),
                ('conversation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='notes', to='inbox.conversation')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='AssignmentRule',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(blank=True, default='', max_length=150)),
                ('priority', models.PositiveIntegerField(default=1)),
                ('match_page', models.CharField(blank=True, default='', max_length=255)),
                ('match_form', models.CharField(blank=True, default='', max_length=255)),
                ('match_tag_name', models.CharField(blank=True, default='', max_length=100)),
                ('is_active', models.BooleanField(default=True)),
                ('assignee', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='assignment_rules', to='accounts.membership')),
                ('match_tag', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assignment_rules', to='contacts.tag')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_set', to='core.tenant')),
            ],
            options={
                'ordering': ['priority', 'created_at'],
                'indexes': [models.Index(fields=['tenant', 'is_active', 'priority'], name='idx_assignrule_t_act_prio')],
                'constraints': [models.UniqueConstraint(fields=('tenant', 'priority'), name='uniq_assignrule_tenant_prio')],
            },
        ),
        migrations.AddIndex(
            model_name='conversation',
            index=models.Index(fields=['tenant', 'status', '-last_message_at'], name='idx_conv_t_st_lastmsg'),
        ),
        migrations.AddIndex(
            model_name='conversation',
            index=models.Index(fields=['tenant', 'assigned_to', 'status', '-last_message_at'], name='idx_conv_t_asgn_st_lastmsg'),
        ),
        migrations.AddIndex(
            model_name='conversation',
            index=models.Index(fields=['tenant', 'unread_count'], name='idx_conv_t_unread'),
        ),
        migrations.AddConstraint(
            model_name='conversation',
            constraint=models.UniqueConstraint(fields=('tenant', 'contact'), name='uniq_conv_tenant_contact'),
        ),
        migrations.AddIndex(
            model_name='conversationnote',
            index=models.Index(fields=['tenant', 'conversation', '-created_at', '-id'], name='idx_convnote_t_conv_dt'),
        ),
    ]
