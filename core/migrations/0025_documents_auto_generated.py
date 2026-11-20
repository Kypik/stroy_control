from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0024_certificate_sheet_count_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='documents',
            name='auto_generated',
            field=models.BooleanField(default=False, verbose_name='Создан автоматически'),
        ),
    ]
