from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_tenant_assignment_strategy_and_more'),
        ('messaging', '0002_message_idx_msg_tenant_dir_created'),
        ('usage', '0002_seed_global_rate_cards'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='ratecard',
            options={'ordering': ['channel', 'direction', 'destination_country', 'whatsapp_category']},
        ),
        migrations.RemoveConstraint(
            model_name='ratecard',
            name='uniq_ratecard_tenant_scope',
        ),
        migrations.RemoveConstraint(
            model_name='ratecard',
            name='uniq_ratecard_global_scope',
        ),
        migrations.RemoveIndex(
            model_name='ratecard',
            name='idx_ratecard_t_ch_country',
        ),
        migrations.RemoveIndex(
            model_name='ratecard',
            name='idx_ratecard_ch_country',
        ),
        migrations.AddField(
            model_name='ratecard',
            name='direction',
            field=models.CharField(choices=[('outbound', 'Outbound'), ('inbound', 'Inbound')], default='outbound', max_length=10),
        ),
        migrations.AddField(
            model_name='usagerecord',
            name='direction',
            field=models.CharField(choices=[('outbound', 'Outbound'), ('inbound', 'Inbound')], default='outbound', max_length=10),
        ),
        migrations.AddIndex(
            model_name='ratecard',
            index=models.Index(fields=['tenant', 'channel', 'direction', 'destination_country'], name='idx_ratecard_t_ch_d_ctry'),
        ),
        migrations.AddIndex(
            model_name='ratecard',
            index=models.Index(fields=['channel', 'direction', 'destination_country'], name='idx_ratecard_ch_d_ctry'),
        ),
        migrations.AddIndex(
            model_name='usagerecord',
            index=models.Index(fields=['tenant', 'direction', 'occurred_at'], name='idx_usagerec_t_dir_occ'),
        ),
        migrations.AddIndex(
            model_name='usagerecord',
            index=models.Index(fields=['tenant', 'channel', 'direction', 'occurred_at'], name='idx_usagerec_t_ch_d_occ'),
        ),
        migrations.AddConstraint(
            model_name='ratecard',
            constraint=models.UniqueConstraint(condition=models.Q(('tenant__isnull', False)), fields=('tenant', 'channel', 'direction', 'destination_country', 'whatsapp_category'), name='uniq_ratecard_tenant_scope'),
        ),
        migrations.AddConstraint(
            model_name='ratecard',
            constraint=models.UniqueConstraint(condition=models.Q(('tenant__isnull', True)), fields=('channel', 'direction', 'destination_country', 'whatsapp_category'), name='uniq_ratecard_global_scope'),
        ),
    ]
