"""Reconcile pending orders older than N hours (SEC-03, cart design G12).

Each one is cancelled with its stock returned (and any open Stripe session expired),
or confirmed if its stored Stripe session was paid. A Stripe or database error leaves
the order unchanged for the next run. The same run deletes unused confirmation
screens after 45 minutes and idle carts after 30 days, and resends queued customer messages
(given up after 24 hours, with an audit row). Run it from cron every 5 minutes:
    python manage.py cancelar_encomendas_abandonadas --horas 1
"""
from django.core.management.base import BaseCommand, CommandError

from core import services
from core.notificacoes import notificar_estado, reenviar_pendentes


class Command(BaseCommand):
    help = ("Reconcilia encomendas pendentes com mais de N horas: cancela e devolve o stock, "
            "ou confirma as que já foram pagas.")

    def add_arguments(self, parser):
        parser.add_argument("--horas", type=int, default=24,
                            help="Idade mínima da encomenda, em horas (por omissão 24).")

    def handle(self, *args, horas, **options):
        if horas < 1:  # zero or negative moves the cutoff to now or later: every unpaid order would qualify
            raise CommandError("--horas tem de ser pelo menos 1.")
        contagem = {"confirmada": 0, "cancelada": 0, "erro": 0}
        for encomenda_id in services.encomendas_abandonadas(horas):
            resultado, encomenda = services.reconciliar_encomenda(encomenda_id, horas)
            if resultado in ("confirmada", "cancelada"):  # the transaction committed; tell the customer
                notificar_estado(encomenda, resultado)
            if resultado in contagem:
                contagem[resultado] += 1
        ecras, carrinhos = services.limpar_rascunhos()
        enviadas, falhadas = reenviar_pendentes()
        self.stdout.write(
            f"Limpeza: {contagem['confirmada']} confirmada(s), {contagem['cancelada']} cancelada(s), "
            f"{contagem['erro']} erro(s); {ecras} ecrã(s) e {carrinhos} carrinho(s) apagados; "
            f"notificações: {enviadas} reenviada(s), {falhadas} abandonada(s)."
        )
