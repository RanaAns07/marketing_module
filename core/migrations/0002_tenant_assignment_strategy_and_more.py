from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='tenant',
            name='assignment_strategy',
            field=models.CharField(choices=[('shared_queue', 'Manual Assignment / Shared Queue'), ('dedicated_owner', 'Dedicated Owner by Source or Form'), ('round_robin', 'Even Team Distribution (Round-Robin)')], default='shared_queue', max_length=32),
        ),
        migrations.AddField(
            model_name='tenant',
            name='handoff_add_interested_tag',
            field=models.BooleanField(default=True),
        ),
    ]
