"""Cancel pending orders that never got a Stripe payment session (SEC-03).

Run it from cron or Task Scheduler, for example hourly:
    python manage.py cancelar_encomendas_abandonadas
"""
from django.core.management.base import BaseCommand, CommandError

from core import services
from core.views import notificar_estado


class Command(BaseCommand):
    help = "Cancela encomendas pendentes sem sessão de pagamento com mais de N horas e devolve o stock."

    def add_arguments(self, parser):
        parser.add_argument("--horas", type=int, default=24,
                            help="Idade mínima da encomenda, em horas (por omissão 24).")

    def handle(self, *args, horas, **options):
        if horas < 1:  # zero or negative moves the cutoff to now or later: every unpaid order would qualify
            raise CommandError("--horas tem de ser pelo menos 1.")
        canceladas = 0
        for encomenda_id in services.encomendas_abandonadas(horas):
            encomenda = services.cancelar_se_abandonada(encomenda_id, horas)
            if encomenda is not None:  # the transaction committed; tell the customer
                notificar_estado(encomenda, 'cancelada')
                canceladas += 1
        self.stdout.write(f"{canceladas} encomenda(s) abandonada(s) cancelada(s).")
