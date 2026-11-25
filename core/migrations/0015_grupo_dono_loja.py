"""The "Dono da loja" group: a limited Django admin account for the shop owner (Stage 4).

Owners add, edit and deactivate products and categories, edit the shop settings, and
read orders and payments. They get no delete permission anywhere, nothing on customers,
stock movements, audit rows, carts, screens or Django's users and groups, and not the
custom core.cancelar_encomenda permission: cancelling and refunding stay in the Telegram panel.
"""
from django.contrib.auth.management import create_permissions
from django.db import migrations

GRUPO = "Dono da loja"
PERMISSOES = [
    "add_produto", "change_produto", "view_produto",
    "add_categoria", "change_categoria", "view_categoria",
    "change_loja", "view_loja",
    "view_encomenda", "view_linhaencomenda", "view_pagamento",
]


def criar_grupo(apps, schema_editor):
    # Permissions are normally created after all migrations; create them now so the
    # group can be filled in the same migrate run.
    for label in ("auth", "core"):
        app_config = apps.get_app_config(label)
        app_config.models_module = True
        create_permissions(app_config, apps=apps, verbosity=0)
        app_config.models_module = None
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    permissoes = list(Permission.objects.filter(content_type__app_label="core", codename__in=PERMISSOES))
    if len(permissoes) != len(PERMISSOES):
        em_falta = set(PERMISSOES) - {p.codename for p in permissoes}
        raise RuntimeError(f"Permissões em falta para o grupo {GRUPO}: {sorted(em_falta)}")
    grupo, _ = Group.objects.get_or_create(name=GRUPO)
    grupo.permissions.set(permissoes)


def apagar_grupo(apps, schema_editor):
    apps.get_model("auth", "Group").objects.filter(name=GRUPO).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0014_loja_e_admin"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(criar_grupo, apagar_grupo),
    ]
