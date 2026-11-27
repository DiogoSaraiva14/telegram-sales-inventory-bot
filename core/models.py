import os
from io import BytesIO

from django.core.files.base import ContentFile
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
    # Telegram admin rights (cart design, Stage 4); only a superuser can change it in Django admin.
    e_admin       = models.BooleanField(default=False)
    # Bot language: taken from Telegram on /start while empty, then changed only by the customer.
    idioma        = models.CharField(max_length=2, blank=True, default='',
                                     choices=[('pt', 'Português'), ('en', 'English')])

    def __str__(self):
        return f"{self.nome} ({self.telegram_id})"

    class Meta:
        db_table        = 'utilizador'
        verbose_name    = "Utilizador"
        verbose_name_plural = "Utilizadores"
        ordering        = ['nome']


# ── Loja (cart design, Stage 4) ────────────────────────────────────────────

TEXTO_BOAS_VINDAS_PADRAO = "Bem-vindo à nossa loja. Escolhe uma opção para começar."


class Loja(models.Model):
    """The shop's own settings: one row (pk=1) per copy of the bot."""
    nome              = models.CharField(max_length=80, default="Loja")
    texto_boas_vindas = models.CharField(max_length=1000, default=TEXTO_BOAS_VINDAS_PADRAO)

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def __str__(self):
        return self.nome

    class Meta:
        db_table        = 'loja'
        verbose_name    = "Loja"
        verbose_name_plural = "Loja"


# ── Categoria ──────────────────────────────────────────────────────────────

class Categoria(models.Model):
    """Shop data edited in Django admin. Deactivated, never deleted, so ids are never reused."""
    nome  = models.CharField(max_length=60)
    ordem = models.PositiveIntegerField(default=0)
    ativo = models.BooleanField(default=True)

    def __str__(self):
        return self.nome

    class Meta:
        db_table            = 'categoria'
        verbose_name        = "Categoria"
        verbose_name_plural = "Categorias"
        ordering            = ['ordem', 'nome']


# ── Produto ────────────────────────────────────────────────────────────────

class Produto(models.Model):

    nome        = models.CharField(max_length=200)
    descricao   = models.TextField(blank=True, null=True)
    categoria   = models.ForeignKey(Categoria, on_delete=models.PROTECT, related_name='produtos')
    preco       = models.DecimalField(max_digits=10, decimal_places=2)
    stock_atual = models.IntegerField(default=0)
    ativo       = models.BooleanField(default=True)
    imagem      = models.ImageField(upload_to='produtos/', blank=True, null=True)
    data_criacao = models.DateTimeField(auto_now_add=True)  # ← DateTimeField (era DateField)

    _IMAGEM_TAMANHO = (800, 800)

    def save(self, *args, **kwargs):
        # SEC-11: only a newly assigned file (not yet in storage) is processed, so stock,
        # name or state saves never re-encode the stored image.
        if self.imagem and not self.imagem._committed:
            self._preparar_imagem()
        super().save(*args, **kwargs)

    def _preparar_imagem(self):
        """Crop to a centred square, resize and encode as JPEG once, in memory.

        The result replaces the upload and is written by the configured storage when the
        model saves, without using a local filesystem path.
        """
        ficheiro = self.imagem.file
        ficheiro.seek(0)
        with PilImage.open(ficheiro) as original:
            img = original.convert('RGB')
        w, h   = img.size
        lado   = min(w, h)
        img    = img.crop(((w - lado) // 2, (h - lado) // 2,
                           (w + lado) // 2, (h + lado) // 2))
        img    = img.resize(self._IMAGEM_TAMANHO, PilImage.LANCZOS)
        saida = BytesIO()
        img.save(saida, 'JPEG', quality=85, optimize=True)
        nome = os.path.splitext(os.path.basename(self.imagem.name))[0] + '.jpg'
        self.imagem = ContentFile(saida.getvalue(), name=nome)

    def __str__(self):
        return self.nome

    class Meta:
        db_table        = 'produto'
        verbose_name    = "Produto"
        verbose_name_plural = "Produtos"
        ordering        = ['nome']


# ── Carrinho (cart design, Stage 3) ────────────────────────────────────────

class Carrinho(models.Model):
    """A customer's draft order. Reserves nothing: stock is taken only at Confirm."""
    utilizador    = models.OneToOneField(Utilizador, on_delete=models.CASCADE, related_name='carrinho')
    revisao       = models.IntegerField(default=0)  # +1 on every change; a confirmation screen records it
    atualizado_em = models.DateTimeField(default=timezone.now)  # last change; viewing doesn't count

    def __str__(self):
        return f"Carrinho de {self.utilizador}"

    class Meta:
        db_table        = 'carrinho'
        verbose_name    = "Carrinho"
        verbose_name_plural = "Carrinhos"


class LinhaCarrinho(models.Model):
    carrinho           = models.ForeignKey(Carrinho, on_delete=models.CASCADE, related_name='linhas')
    produto            = models.ForeignKey(Produto, on_delete=models.CASCADE, related_name='+')
    quantidade         = models.PositiveIntegerField()
    # Price when the line was added or last changed, to mark later price changes.
    preco_ao_adicionar = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        db_table        = 'linha_carrinho'
        verbose_name    = "Linha de Carrinho"
        verbose_name_plural = "Linhas de Carrinho"
        constraints     = [
            models.UniqueConstraint(fields=['carrinho', 'produto'], name='uma_linha_por_produto'),
            models.CheckConstraint(condition=models.Q(quantidade__gt=0), name='quantidade_positiva'),
        ]


# ── Ecrã de confirmação (cart design, Stage 2) ─────────────────────────────

class ConfirmacaoCarrinho(models.Model):
    """What a confirmation screen showed: written once when drawn, never changed.

    linhas: [{produto_id, nome, quantidade, preco_unitario_centimos}], ordered by produto_id.
    Prices are integer cents, so JSON holds them exactly.
    """
    nonce            = models.CharField(max_length=16, unique=True, editable=False)
    utilizador       = models.ForeignKey(Utilizador, on_delete=models.CASCADE, related_name='confirmacoes')
    criada_em        = models.DateTimeField(auto_now_add=True)
    # Null only for Stage 2 one-product screens, which don't come from a cart.
    revisao_carrinho = models.IntegerField(null=True, blank=True)
    total_centimos   = models.PositiveIntegerField()
    linhas           = models.JSONField()

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("ConfirmacaoCarrinho é imutável depois de criada")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Confirmação {self.nonce}"

    class Meta:
        db_table        = 'confirmacao_carrinho'
        verbose_name    = "Confirmação"
        verbose_name_plural = "Confirmações"


# ── Encomenda ──────────────────────────────────────────────────────────────

class Encomenda(models.Model):

    ESTADO_CHOICES = [
        ('pendente',   'Pendente'),
        ('confirmada', 'Confirmada'),
        ('enviada',    'Enviada'),
        ('entregue',   'Entregue'),
        ('cancelada',  'Cancelada'),
    ]
    # Every allowed change of estado: new orders start "pendente"; "entregue" and "cancelada" are final.
    # Who makes each one: the Stripe webhook confirms, admins ship and deliver, and
    # cancelar_encomenda / cancelar_por_expiracao cancel (see core/services.py).
    TRANSICOES = {
        'pendente':   {'confirmada', 'cancelada'},
        'confirmada': {'enviada', 'cancelada'},
        'enviada':    {'entregue'},
        'entregue':   set(),
        'cancelada':  set(),
    }

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
    # The screen this order was created from; null for orders from before the cart ("legacy").
    confirmacao = models.OneToOneField(
        ConfirmacaoCarrinho, on_delete=models.PROTECT, null=True, blank=True,
        related_name='encomenda', editable=False,
    )

    def __str__(self):
        return f"Encomenda #{self.id} — {self.utilizador.nome}"

    class Meta:
        db_table        = 'encomenda'
        verbose_name    = "Encomenda"
        verbose_name_plural = "Encomendas"
        ordering        = ['-data_encomenda']
        # Not given to the "Dono da loja" group: cancelling and refunding stay in the Telegram panel.
        permissions     = [('cancelar_encomenda', 'Pode cancelar e reembolsar encomendas')]
        constraints     = [
            # Legacy orders are excluded; order creation also refuses while any pending order exists.
            models.UniqueConstraint(
                fields=['utilizador'], condition=models.Q(estado='pendente', confirmacao__isnull=False),
                name='uma_encomenda_pendente_por_cliente',
            ),
        ]


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
    # Every allowed change of estado_pagamento. "falhado" can still become "pago" or "reembolsado":
    # a session that completes after its order was cancelled is refunded (SEC-06).
    TRANSICOES = {
        'pendente':    {'pago', 'falhado', 'reembolsado'},
        'falhado':     {'pago', 'reembolsado'},
        'pago':        {'reembolsado'},
        'reembolsado': set(),
    }

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

# ── Notificações ao cliente (fila com reenvio) ─────────────────────────────

class Notificacao(models.Model):
    """A customer order message: saved before sending, resent by the cleanup run for 24 hours."""
    ESTADOS = [('pendente', 'Pendente'), ('enviada', 'Enviada'), ('falhada', 'Falhada')]

    encomenda  = models.ForeignKey(Encomenda, on_delete=models.CASCADE, related_name='notificacoes')
    chat_id    = models.CharField(max_length=50)
    texto      = models.TextField()
    estado     = models.CharField(max_length=10, choices=ESTADOS, default='pendente', db_index=True)
    tentativas = models.PositiveIntegerField(default=0)
    criada     = models.DateTimeField(auto_now_add=True)
    enviada_em = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Notificação #{self.pk} — encomenda #{self.encomenda_id} ({self.estado})"

    class Meta:
        db_table = 'notificacao'
        verbose_name = "Notificação"
        verbose_name_plural = "Notificações"
