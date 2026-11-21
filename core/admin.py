from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.contrib import messages
from .models import (
    Utilizador, Produto, Encomenda,
    LinhaEncomenda, Pagamento, MovimentoStock, RegistoAuditoria
)  # ← parêntese de fecho em falta no original — BUG #1


@admin.register(Utilizador)
class UtilizadorAdmin(admin.ModelAdmin):
    list_display = ['nome', 'telegram_id', 'username', 'data_registo', 'estado_badge', 'motivo_bloqueio']
    list_filter = ['ativo']
    search_fields = ['nome', 'telegram_id', 'username']
    readonly_fields = ['data_registo']
    actions = ['bloquear_utilizadores', 'desbloquear_utilizadores']

    @admin.display(description='Estado', ordering='ativo')
    def estado_badge(self, obj):
        if obj.ativo:
            return mark_safe('<span style="color:green">✅ Ativo</span>')
        return mark_safe('<span style="color:red">🚫 Bloqueado</span>')

    @admin.action(description='🚫 Bloquear utilizadores selecionados')
    def bloquear_utilizadores(self, request, queryset):
        atualizados = queryset.update(ativo=False)
        self.message_user(request, f"{atualizados} utilizador(es) bloqueado(s).", messages.WARNING)

    @admin.action(description='✅ Desbloquear utilizadores selecionados')
    def desbloquear_utilizadores(self, request, queryset):
        atualizados = queryset.update(ativo=True)
        self.message_user(request, f"{atualizados} utilizador(es) desbloqueado(s).", messages.SUCCESS)


@admin.register(Produto)
class ProdutoAdmin(admin.ModelAdmin):
    list_display = ['nome', 'categoria', 'preco', 'stock_atual', 'estado_stock', 'ativo', 'data_criacao', 'preview_imagem']
    list_filter = ['ativo', 'categoria']
    search_fields = ['nome']
    readonly_fields = ['data_criacao', 'preview_imagem']
    fields = ['nome', 'descricao', 'categoria', 'preco', 'stock_atual', 'ativo', 'imagem', 'preview_imagem', 'data_criacao']
    actions = ['ativar_produtos', 'desativar_produtos']

    @admin.display(description='Stock', ordering='stock_atual')
    def estado_stock(self, obj):
        if obj.stock_atual == 0:
            return mark_safe('❌ Esgotado')
        if obj.stock_atual <= 3:
            return format_html('⚠️ Baixo ({})', obj.stock_atual)
        return format_html('✅ {}', obj.stock_atual)

    @admin.display(description='Imagem')
    def preview_imagem(self, obj):
        if obj.imagem:
            return format_html('<img src="{}" style="max-height:60px;border-radius:4px;">', obj.imagem.url)
        return "—"

    @admin.action(description='✅ Ativar produtos selecionados')
    def ativar_produtos(self, request, queryset):
        atualizados = queryset.update(ativo=True)
        self.message_user(request, f"{atualizados} produto(s) ativado(s).", messages.SUCCESS)

    @admin.action(description='❌ Desativar produtos selecionados')
    def desativar_produtos(self, request, queryset):
        atualizados = queryset.update(ativo=False)
        self.message_user(request, f"{atualizados} produto(s) desativado(s).", messages.WARNING)


class LinhaEncomendaInline(admin.TabularInline):
    model = LinhaEncomenda
    extra = 0
    readonly_fields = ['subtotal']
    fields = ['produto', 'quantidade', 'preco_unitario', 'subtotal']


@admin.register(Encomenda)
class EncomendaAdmin(admin.ModelAdmin):
    list_display = ['id', 'utilizador', 'data_encomenda', 'data_atualizacao', 'estado_badge', 'total']
    list_filter = ['estado']
    search_fields = ['utilizador__nome', 'utilizador__telegram_id']
    readonly_fields = ['data_encomenda', 'data_atualizacao', 'total']
    inlines = [LinhaEncomendaInline]
    actions = ['marcar_confirmada', 'marcar_enviada', 'marcar_entregue', 'marcar_cancelada']

    @admin.display(description='Estado', ordering='estado')
    def estado_badge(self, obj):
        cores = {
            'pendente': ('orange', '🕐'), 'confirmada': ('blue', '✅'),
            'enviada': ('purple', '🚚'), 'entregue': ('green', '📬'), 'cancelada': ('red', '❌'),
        }  # ← parêntese de fecho em falta no original — BUG #2
        cor, emoji = cores.get(obj.estado, ('gray', '❓'))
        return format_html('<span style="color:{}">{} {}</span>', cor, emoji, obj.get_estado_display())

    @admin.action(description='✅ Marcar como Confirmada')
    def marcar_confirmada(self, request, queryset):
        atualizados = queryset.update(estado='confirmada')
        self.message_user(request, f"{atualizados} encomenda(s) confirmada(s).", messages.SUCCESS)

    @admin.action(description='🚚 Marcar como Enviada')
    def marcar_enviada(self, request, queryset):
        atualizados = queryset.update(estado='enviada')
        self.message_user(request, f"{atualizados} encomenda(s) marcada(s) como enviada.", messages.SUCCESS)

    @admin.action(description='📬 Marcar como Entregue')
    def marcar_entregue(self, request, queryset):
        atualizados = queryset.update(estado='entregue')
        self.message_user(request, f"{atualizados} encomenda(s) marcada(s) como entregue.", messages.SUCCESS)

    @admin.action(description='❌ Marcar como Cancelada')
    def marcar_cancelada(self, request, queryset):
        atualizados = queryset.update(estado='cancelada')
        self.message_user(request, f"{atualizados} encomenda(s) cancelada(s).", messages.WARNING)


@admin.register(LinhaEncomenda)
class LinhaEncomendaAdmin(admin.ModelAdmin):
    list_display = ['encomenda', 'produto', 'quantidade', 'preco_unitario', 'subtotal']
    readonly_fields = ['subtotal']
    search_fields = ['produto__nome', 'encomenda__id']


@admin.register(Pagamento)
class PagamentoAdmin(admin.ModelAdmin):
    list_display = ['id', 'encomenda', 'metodo', 'valor', 'estado_pagamento_badge', 'data_pagamento', 'stripe_session_id']
    list_filter = ['metodo', 'estado_pagamento']
    search_fields = ['encomenda__id', 'stripe_session_id', 'referencia_externa']
    readonly_fields = ['data_pagamento']

    @admin.display(description='Estado Pagamento', ordering='estado_pagamento')
    def estado_pagamento_badge(self, obj):
        cores = {
            'pendente': ('orange', '🕐'), 'pago': ('green', '✅'),
            'falhado': ('red', '❌'), 'reembolsado': ('gray', '↩️'),
        }  # ← parêntese de fecho em falta no original — BUG #3
        cor, emoji = cores.get(obj.estado_pagamento, ('gray', '❓'))
        return format_html('<span style="color:{}">{} {}</span>', cor, emoji, obj.estado_pagamento.capitalize())


@admin.register(MovimentoStock)
class MovimentoStockAdmin(admin.ModelAdmin):
    list_display = ['produto', 'tipo_movimento', 'quantidade', 'data_movimento', 'observacoes']
    list_filter = ['tipo_movimento']
    search_fields = ['produto__nome']
    readonly_fields = ['data_movimento']


@admin.register(RegistoAuditoria)
class RegistoAuditoriaAdmin(admin.ModelAdmin):
    list_display = ['data_hora', 'acao', 'entidade', 'utilizador', 'descricao_curta']
    list_filter = ['acao', 'entidade']
    search_fields = ['acao', 'entidade', 'utilizador__nome', 'descricao']
    readonly_fields = ['data_hora', 'utilizador', 'acao', 'entidade', 'descricao', 'ip_origem']

    @admin.display(description='Descrição')
    def descricao_curta(self, obj):
        if obj.descricao and len(obj.descricao) > 60:
            return obj.descricao[:60] + '…'
        return obj.descricao or '—'