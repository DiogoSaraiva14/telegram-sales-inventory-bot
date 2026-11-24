from django.db import models
from django.utils import timezone
from PIL import Image as PilImage


# ── Utilizador ─────────────────────────────────────────────────────────────

class Utilizador(models.Model):
    telegram_id   = models.CharField(max_length=50, unique=True)
    nome          = models.CharField(max_length=150)
    username      = models.CharField(max_length=150, blank=True, null=True)
    telefone      = models.CharField(max_length=20,  blank=True, null=True)
    data_registo  = models.DateTimeField(auto_now_add=True)  # ← DateTimeField (era DateField)
    ativo         = models.BooleanField(default=True)        # ✅ já existia — usado por @verificar_bloqueio
    motivo_bloqueio = models.TextField(blank=True, null=True)  # ✅ NOVO — registo da razão do bloqueio

    def __str__(self):
        return f"{self.nome} ({self.telegram_id})"

    class Meta:
        db_table        = 'utilizador'
        verbose_name    = "Utilizador"
        verbose_name_plural = "Utilizadores"
        ordering        = ['nome']


# ── Produto ────────────────────────────────────────────────────────────────

class Produto(models.Model):

    CATEGORIA_CHOICES = [
        ('cpu',         'Processadores'),
        ('gpu',         'Placas Gráficas'),
        ('ram',         'Memória RAM'),
        ('ssd',         'Armazenamento'),
        ('motherboard', 'Motherboards'),
        ('psu',         'Fontes de Alimentação'),
        ('caixa',       'Caixas'),
        ('cooling',     'Coolers'),
    ]

    nome        = models.CharField(max_length=200)
    descricao   = models.TextField(blank=True, null=True)
    categoria   = models.CharField(max_length=20, choices=CATEGORIA_CHOICES, default='cpu')
    preco       = models.DecimalField(max_digits=10, decimal_places=2)
    stock_atual = models.IntegerField(default=0)
    ativo       = models.BooleanField(default=True)
    imagem      = models.ImageField(upload_to='produtos/', blank=True, null=True)
    data_criacao = models.DateTimeField(auto_now_add=True)  # ← DateTimeField (era DateField)

    _IMAGEM_TAMANHO = (800, 800)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.imagem:
            self._redimensionar_imagem()

    def _redimensionar_imagem(self):
        img = PilImage.open(self.imagem.path)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        w, h   = img.size
        lado   = min(w, h)
        img    = img.crop(((w - lado) // 2, (h - lado) // 2,
                           (w + lado) // 2, (h + lado) // 2))
        img    = img.resize(self._IMAGEM_TAMANHO, PilImage.LANCZOS)
        img.save(self.imagem.path, 'JPEG', quality=85, optimize=True)

    def __str__(self):
        return self.nome

    class Meta:
        db_table        = 'produto'
        verbose_name    = "Produto"
        verbose_name_plural = "Produtos"
        ordering        = ['nome']


# ── Encomenda ──────────────────────────────────────────────────────────────

class Encomenda(models.Model):

    ESTADO_CHOICES = [
        ('pendente',   'Pendente'),
        ('confirmada', 'Confirmada'),
        ('enviada',    'Enviada'),
        ('entregue',   'Entregue'),
        ('cancelada',  'Cancelada'),
    ]

    utilizador      = models.ForeignKey(
        Utilizador, on_delete=models.PROTECT, related_name='encomendas'
    )
    data_encomenda  = models.DateTimeField(auto_now_add=True)  # ← DateTimeField (era DateField)
    data_atualizacao = models.DateTimeField(auto_now=True)      # ✅ NOVO — timestamp de última alteração de estado
    estado          = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='pendente')
    total           = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    notas           = models.TextField(blank=True, null=True)   # ✅ NOVO — notas internas do admin
    # Nonce do ecrã de confirmação do bot: um ecrã cria no máximo uma encomenda (SEC-13).
    chave_confirmacao = models.CharField(max_length=16, null=True, blank=True, unique=True, editable=False)

    def __str__(self):
        return f"Encomenda #{self.id} — {self.utilizador.nome}"

    class Meta:
        db_table        = 'encomenda'
        verbose_name    = "Encomenda"
        verbose_name_plural = "Encomendas"
        ordering        = ['-data_encomenda']


# ── Linha de Encomenda ─────────────────────────────────────────────────────

class LinhaEncomenda(models.Model):
    encomenda      = models.ForeignKey(Encomenda, on_delete=models.CASCADE, related_name='linhas')
    produto        = models.ForeignKey(Produto,   on_delete=models.PROTECT, related_name='linhas')
    quantidade     = models.IntegerField()
    preco_unitario = models.DecimalField(max_digits=10, decimal_places=2)
    subtotal       = models.DecimalField(max_digits=10, decimal_places=2)
    # Nome do produto no momento da encomenda, para pedidos Stripe repetíveis.
    nome_produto   = models.CharField(max_length=200, null=True, blank=True, editable=False)

    def save(self, *args, **kwargs):
        self.subtotal = self.quantidade * self.preco_unitario
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.quantidade}x {self.produto.nome}"

    class Meta:
        db_table        = 'linha_encomenda'
        verbose_name    = "Linha de Encomenda"
        verbose_name_plural = "Linhas de Encomenda"


# ── Pagamento ──────────────────────────────────────────────────────────────

class Pagamento(models.Model):

    METODO_CHOICES = [
        ('stripe',        'Stripe'),
        ('transferencia', 'Transferência'),
        ('outro',         'Outro'),
    ]
    ESTADO_CHOICES = [
        ('pendente',    'Pendente'),
        ('pago',        'Pago'),
        ('falhado',     'Falhado'),
        ('reembolsado', 'Reembolsado'),
    ]

    encomenda          = models.OneToOneField(
        Encomenda, on_delete=models.CASCADE, related_name='pagamento'
    )
    metodo             = models.CharField(max_length=20, choices=METODO_CHOICES)
    valor              = models.DecimalField(max_digits=10, decimal_places=2)
    estado_pagamento   = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='pendente')
    data_pagamento     = models.DateTimeField(blank=True, null=True)  # ← DateTimeField (era DateField)
    referencia_externa = models.CharField(max_length=200, blank=True, null=True)
    stripe_session_id  = models.CharField(max_length=255, blank=True, null=True)  # ✅ NOVO — ID de sessão Stripe

    def __str__(self):
        return f"Pagamento #{self.id} — {self.encomenda}"

    class Meta:
        db_table        = 'pagamento'
        verbose_name    = "Pagamento"
        verbose_name_plural = "Pagamentos"


# ── Movimento de Stock ─────────────────────────────────────────────────────

class MovimentoStock(models.Model):

    TIPO_CHOICES = [
        ('entrada', 'Entrada'),
        ('saida',   'Saída'),
        ('ajuste',  'Ajuste'),
    ]

    produto         = models.ForeignKey(Produto, on_delete=models.CASCADE, related_name='movimentos')
    tipo_movimento  = models.CharField(max_length=20, choices=TIPO_CHOICES)
    quantidade      = models.IntegerField()
    data_movimento  = models.DateTimeField(auto_now_add=True)  # ← DateTimeField (era DateField)
    observacoes     = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.tipo_movimento} — {self.produto.nome} ({self.quantidade})"

    class Meta:
        db_table        = 'movimento_stock'
        verbose_name    = "Movimento de Stock"
        verbose_name_plural = "Movimentos de Stock"
        ordering        = ['-data_movimento']


# ── Registo de Auditoria ───────────────────────────────────────────────────

class RegistoAuditoria(models.Model):
    utilizador = models.ForeignKey(
        Utilizador, on_delete=models.SET_NULL, null=True, related_name='registos'
    )
    acao       = models.CharField(max_length=100)
    entidade   = models.CharField(max_length=100)
    descricao  = models.TextField(blank=True, null=True)
    data_hora  = models.DateTimeField(auto_now_add=True)
    ip_origem  = models.CharField(max_length=50, blank=True, null=True)  # ✅ NOVO — para rastreio futuro

    def __str__(self):
        return f"{self.acao} — {self.entidade} ({self.data_hora:%d/%m/%Y %H:%M})"

    class Meta:
        db_table        = 'registo_auditoria'
        verbose_name    = "Registo de Auditoria"
        verbose_name_plural = "Registos de Auditoria"
        ordering        = ['-data_hora']