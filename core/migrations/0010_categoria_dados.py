"""Turn the old fixed category codes into Categoria rows (Stage 1 of the cart design)."""
from django.db import migrations
from django.db.migrations.exceptions import IrreversibleError

# A literal copy of the old Produto.CATEGORIA_CHOICES, so later model changes can't alter it.
CATEGORIAS_ANTIGAS = [
    ('cpu',         'Processadores'),
    ('gpu',         'Placas Gráficas'),
    ('ram',         'Memória RAM'),
    ('ssd',         'Armazenamento'),
    ('motherboard', 'Motherboards'),
    ('psu',         'Fontes de Alimentação'),
    ('caixa',       'Caixas'),
    ('cooling',     'Coolers'),
]
ORDEM_DESCONHECIDA = 99


def para_categorias(apps, schema_editor):
    Produto = apps.get_model('core', 'Produto')
    Categoria = apps.get_model('core', 'Categoria')
    conhecidas = {codigo: (posicao, nome) for posicao, (codigo, nome) in enumerate(CATEGORIAS_ANTIGAS)}
    codigos = Produto.objects.order_by().values_list('categoria', flat=True).distinct()
    for codigo in codigos:
        ordem, nome = conhecidas.get(codigo, (ORDEM_DESCONHECIDA, codigo))
        categoria = Categoria.objects.create(nome=nome, ordem=ordem)
        Produto.objects.filter(categoria=codigo).update(categoria_nova=categoria)


def sem_volta(apps, schema_editor):
    raise IrreversibleError(
        "0010_categoria_dados é forward-fix only: restaura a cópia de segurança da base de dados."
    )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0009_categoria'),
    ]

    operations = [
        migrations.RunPython(para_categorias, sem_volta),
    ]
