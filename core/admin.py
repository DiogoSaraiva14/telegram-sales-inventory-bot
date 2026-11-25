from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect
import stripe
from . import services
from .views import notificar_estado
from .models import (
    Utilizador, Categoria, Loja, Produto, Encomenda,
    LinhaEncomenda, Pagamento, MovimentoStock, RegistoAuditoria
)  # ← parêntese de fecho em falta no original — BUG #1


@admin.register(Utilizador)
class UtilizadorAdmin(admin.ModelAdmin):
    list_display = ['nome', 'telegram_id', 'username', 'data_registo', 'estado_badge', 'e_admin', 'motivo_bloqueio']
    list_filter = ['ativo', 'e_admin']
    search_fields = ['nome', 'telegram_id', 'username']
    readonly_fields = ['data_registo']
    actions = ['bloquear_utilizadores', 'desbloquear_utilizadores']

    def get_readonly_fields(self, request, obj=None):
        # Only the reseller's superuser grants or removes Telegram admin rights (Stage 4).
        campos = list(super().get_readonly_fields(request, obj))
        return campos if request.user.is_superuser else campos + ['e_admin']

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


@admin.register(Loja)
class LojaAdmin(admin.ModelAdmin):
    """One settings row per shop: no add or delete; the list opens that row."""
    fields = ['nome', 'texto_boas_vindas']

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        return redirect('admin:core_loja_change', services.obter_loja().pk)


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display = ['nome', 'ordem', 'ativo']
    list_editable = ['ordem', 'ativo']
    list_filter = ['ativo']
    search_fields = ['nome']

    def has_delete_permission(self, request, obj=None):
        return False  # deactivate instead: products keep their category and ids are never reused


@admin.register(Produto)
class ProdutoAdmin(admin.ModelAdmin):
    list_display = ['nome', 'categoria', 'preco', 'stock_atual', 'estado_stock', 'ativo', 'data_criacao', 'preview_imagem']
    list_select_related = ['categoria']
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
        atualizados = self._definir_ativo(queryset, True)
        self.message_user(request, f"{atualizados} produto(s) ativado(s).", messages.SUCCESS)

    def save_model(self, request, obj, form, change):
        """Stock edits become an atomic adjustment with a movement and an audit row (SEC-10)."""
        if not (change and 'stock_atual' in form.changed_data):
            return super().save_model(request, obj, form, change)
        delta = obj.stock_atual - form.initial['stock_atual']
        outros = [f for f in form.changed_data if f != 'stock_atual']
        if outros:  # never write stock_atual with the form's possibly stale value
            obj.save(update_fields=outros)
        obj.stock_atual = services.ajustar_stock(
            obj.pk, delta, f"Ajuste manual via Django admin (delta={delta})")
        RegistoAuditoria.objects.create(
            acao='AJUSTE_STOCK', entidade='Produto',
            descricao=f'{obj.nome}: delta={delta}, novo stock={obj.stock_atual} (Django admin: {request.user})',
        )

    @staticmethod
    def _definir_ativo(queryset, ativo):
        # Products are always locked in one query ordered by id, as in checkout (no deadlocks).
        with transaction.atomic():
            ids = list(Produto.objects.select_for_update().filter(pk__in=queryset.values('pk'))
                       .order_by('pk').values_list('pk', flat=True))
            return Produto.objects.filter(pk__in=ids).update(ativo=ativo)

    @admin.action(description='❌ Desativar produtos selecionados')
    def desativar_produtos(self, request, queryset):
        atualizados = self._definir_ativo(queryset, False)
        self.message_user(request, f"{atualizados} produto(s) desativado(s).", messages.WARNING)


class SoLeitura:
    """View-only in Django admin (SEC-10): orders change only through domain operations."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class LinhaEncomendaInline(SoLeitura, admin.TabularInline):
    model = LinhaEncomenda
    extra = 0
    fields = ['produto', 'nome_produto', 'quantidade', 'preco_unitario', 'subtotal']
    readonly_fields = fields


@admin.register(Encomenda)
class EncomendaAdmin(admin.ModelAdmin):
    list_display = ['id', 'utilizador', 'data_encomenda', 'data_atualizacao', 'estado_badge', 'total']
    list_filter = ['estado']
    search_fields = ['utilizador__nome', 'utilizador__telegram_id']
    # Only the internal notes are editable; state changes go through the actions below (SEC-10).
    fields = ['utilizador', 'estado', 'total', 'data_encomenda', 'data_atualizacao', 'confirmacao', 'notas']
    readonly_fields = ['utilizador', 'estado', 'total', 'data_encomenda', 'data_atualizacao', 'confirmacao']
    inlines = [LinhaEncomendaInline]
    actions = ['marcar_enviada', 'marcar_entregue', 'marcar_cancelada']

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Estado', ordering='estado')
    def estado_badge(self, obj):
        cores = {
            'pendente': ('orange', '🕐'), 'confirmada': ('blue', '✅'),
            'enviada': ('purple', '🚚'), 'entregue': ('green', '📬'), 'cancelada': ('red', '❌'),
        }  # ← parêntese de fecho em falta no original — BUG #2
        cor, emoji = cores.get(obj.estado, ('gray', '❓'))
        return format_html('<span style="color:{}">{} {}</span>', cor, emoji, obj.get_estado_display())

    def _avancar(self, request, queryset, novo_estado, rotulo):
        """SEC-10: the same forward step as the Telegram admin, with audit and customer message."""
        feitas, recusadas = 0, []
        for encomenda_id in queryset.values_list('id', flat=True):
            encomenda = services.avancar_estado_encomenda(encomenda_id, novo_estado, f'Django admin: {request.user}')
            if encomenda is None:
                recusadas.append(f"#{encomenda_id}")
                continue
            notificar_estado(encomenda, novo_estado)
            feitas += 1
        self.message_user(request, f"{feitas} encomenda(s) marcada(s) como {rotulo}.", messages.SUCCESS)
        if recusadas:
            anterior = services.ESTADO_ANTERIOR[novo_estado]
            self.message_user(request, f"Não alteradas (só a partir de '{anterior}'): " + ", ".join(recusadas),
                              messages.ERROR)

    @admin.action(description='🚚 Marcar como Enviada', permissions=['change'])
    def marcar_enviada(self, request, queryset):
        self._avancar(request, queryset, 'enviada', 'enviada')

    @admin.action(description='📬 Marcar como Entregue', permissions=['change'])
    def marcar_entregue(self, request, queryset):
        self._avancar(request, queryset, 'entregue', 'entregue')

    def has_cancelar_permission(self, request):
        # Checked by Django when the action is listed and when it is posted (Stage 4).
        return request.user.has_perm('core.cancelar_encomenda')

    @admin.action(description='❌ Marcar como Cancelada', permissions=['cancelar'])
    def marcar_cancelada(self, request, queryset):
        # SEC-06: the same operation as the Telegram admin (Stripe, stock, audit, customer message).
        canceladas, falhas = 0, []
        for encomenda_id in queryset.values_list('id', flat=True):
            try:
                resultado, encomenda = services.cancelar_encomenda(encomenda_id, f'Django admin: {request.user}')
            except services.PagamentoRecebido:
                falhas.append(f"#{encomenda_id} (pagamento acabado de receber)")
                continue
            except stripe.error.StripeError:
                falhas.append(f"#{encomenda_id} (erro da Stripe)")
                continue
            if resultado == 'invalida':
                falhas.append(f"#{encomenda_id} (estado não permite cancelar)")
                continue
            notificar_estado(encomenda, resultado)
            canceladas += 1
        self.message_user(request, f"{canceladas} encomenda(s) cancelada(s).", messages.WARNING)
        if falhas:
            self.message_user(request, "Não canceladas: " + ", ".join(falhas), messages.ERROR)


@admin.register(LinhaEncomenda)
class LinhaEncomendaAdmin(SoLeitura, admin.ModelAdmin):
    list_display = ['encomenda', 'produto', 'quantidade', 'preco_unitario', 'subtotal']
    readonly_fields = ['subtotal']
    search_fields = ['produto__nome', 'encomenda__id']


@admin.register(Pagamento)
class PagamentoAdmin(SoLeitura, admin.ModelAdmin):
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