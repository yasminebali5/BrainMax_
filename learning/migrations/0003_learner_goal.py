from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('learning', '0002_assessment_questions'),
    ]

    operations = [
        migrations.AddField(
            model_name='learner',
            name='goal',
            field=models.JSONField(default=dict),
        ),
    ]
