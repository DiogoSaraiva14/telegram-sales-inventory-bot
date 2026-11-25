import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0010_categoria_dados'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='produto',
            name='categoria',
        ),
        migrations.RenameField(
            model_name='produto',
            old_name='categoria_nova',
            new_name='categoria',
        ),
        migrations.AlterField(
            model_name='produto',
            name='categoria',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='produtos', to='core.categoria'),
        ),
    ]
