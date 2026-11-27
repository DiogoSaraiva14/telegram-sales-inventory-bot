"""Fill an empty database with a demo catalogue: shop settings, categories and products.

Product photos are plain placeholders drawn here with Pillow, so the demo has no
third-party image rights. Customers and orders come from using the bot. Safe to run
again: it only adds what is missing. It refuses a database that already holds other
products, so it can't mix demo items into a real shop.
    python manage.py criar_demo
"""
from decimal import Decimal
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from PIL import Image, ImageDraw, ImageFont

from core.models import Categoria, Loja, Produto

CATEGORIAS = [("Processadores", (52, 101, 164)), ("Memória RAM", (46, 139, 87)), ("Armazenamento", (178, 102, 34))]
PRODUTOS = [
    ("CPU Demo 6 núcleos", "Processadores", "189.90", 4, "Processador de demonstração, 6 núcleos."),
    ("CPU Demo 8 núcleos", "Processadores", "279.00", 2, "Processador de demonstração, 8 núcleos."),
    ("RAM Demo 16GB", "Memória RAM", "49.99", 10, "Módulo de memória de demonstração, 16 GB."),
    ("RAM Demo 32GB", "Memória RAM", "94.50", 0, "Módulo de memória de demonstração, 32 GB (esgotado)."),
    ("SSD Demo 1TB", "Armazenamento", "69.90", 7, "Disco SSD de demonstração, 1 TB."),
    ("SSD Demo 2TB", "Armazenamento", "129.00", 3, "Disco SSD de demonstração, 2 TB."),
]


def imagem_placeholder(nome, cor):
    """A 600×600 PNG: the category colour with the product name and a 'DEMO' tag."""
    img = Image.new("RGB", (600, 600), cor)
    desenho = ImageDraw.Draw(img)
    titulo, etiqueta = ImageFont.load_default(size=44), ImageFont.load_default(size=28)
    desenho.text((300, 280), nome, fill="white", font=titulo, anchor="mm")
    desenho.text((300, 350), "DEMO", fill=(235, 235, 235), font=etiqueta, anchor="mm")
    saida = BytesIO()
    img.save(saida, format="PNG")
    return saida.getvalue()


class Command(BaseCommand):
    help = "Cria um catálogo de demonstração (categorias e produtos com imagens geradas)."

    def handle(self, *args, **options):
        nomes_demo = {nome for nome, *_ in PRODUTOS}
        if Produto.objects.exclude(nome__in=nomes_demo).exists():
            raise CommandError("Esta base de dados já tem outros produtos; o catálogo de demonstração não é criado.")
        loja = Loja.objects.get_or_create(pk=1)[0]
        if loja.nome == "Loja":  # untouched default: give the demo its own name
            loja.nome = "Loja Demo"
            loja.texto_boas_vindas = "Bem-vindo à Loja Demo! Componentes de teste, sem stock real."
            loja.save()
        cores = dict(CATEGORIAS)
        categorias = {nome: Categoria.objects.get_or_create(nome=nome, defaults={"ordem": i})[0]
                      for i, (nome, _) in enumerate(CATEGORIAS)}
        criados = imagens = 0
        for nome, categoria, preco, stock, descricao in PRODUTOS:
            produto, criado = Produto.objects.get_or_create(nome=nome, defaults=dict(
                categoria=categorias[categoria], preco=Decimal(preco), stock_atual=stock, descricao=descricao))
            criados += criado
            if not produto.imagem:
                produto.imagem = ContentFile(imagem_placeholder(nome, cores[categoria]), name=f"demo-{produto.pk}.png")
                produto.save(update_fields=["imagem"])
                imagens += 1
        self.stdout.write(f"Demo: {criados} produto(s) criado(s), {imagens} imagem(ns) gerada(s).")
