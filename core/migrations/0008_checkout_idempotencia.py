from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0007_alter_encomenda_options_alter_movimentostock_options_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='encomenda',
            name='chave_confirmacao',
            field=models.CharField(blank=True, editable=False, max_length=16, null=True, unique=True),
        ),
        migrations.AddField(
            model_name='linhaencomenda',
            name='nome_produto',
            field=models.CharField(blank=True, editable=False, max_length=200, null=True),
        ),
    ]
