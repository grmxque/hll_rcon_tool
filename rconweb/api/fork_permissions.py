"""Permissions added by this fork on top of upstream CRCON.

Upstream declares its permissions in ``RconUser.Meta.permissions`` and ships a
numbered Django migration for each change. Doing the same here would create a
second ``api`` migration leaf every time upstream adds its own migration with
the same number, and ``migrate`` would then refuse to run in the maintenance
container. Fork permissions are therefore created by a ``post_migrate`` handler
instead, which keeps ``rconweb/api/models.py`` and the migration chain
identical to upstream.

Django's own ``create_permissions`` never deletes permissions that are missing
from ``Meta``, so rows created here are stable. ``ModelBackend.has_perm`` reads
the ``auth_permission`` table, so ``"api.<codename>"`` checks behave exactly
like upstream permissions.
"""

from django.db import DEFAULT_DB_ALIAS

FORK_PERMISSIONS: tuple[tuple[str, str], ...] = (
    ("can_view_auto_mod_antisteamroll_config", "Can view Anti-Steamroll config"),
    ("can_change_auto_mod_antisteamroll_config", "Can change Anti-Steamroll config"),
    ("can_view_welcome_stats_config", "Can view Welcome Stats config"),
    ("can_change_welcome_stats_config", "Can change Welcome Stats config"),
    ("can_view_end_of_game_rewards_config", "Can view End of Game Rewards config"),
    ("can_change_end_of_game_rewards_config", "Can change End of Game Rewards config"),
)


def ensure_fork_permissions(sender, apps=None, using=DEFAULT_DB_ALIAS, **kwargs):
    """Create missing fork permissions on ``RconUser``; idempotent.

    Connected to ``post_migrate`` for the ``api`` app. ``apps`` is the
    historical app registry passed by the signal; it is ``None`` or lacks the
    models when migrations are being unapplied, in which case nothing is done.
    """
    if apps is None:
        return
    try:
        Permission = apps.get_model("auth", "Permission")
        ContentType = apps.get_model("contenttypes", "ContentType")
        RconUser = apps.get_model("api", "RconUser")
    except LookupError:
        return

    content_type = ContentType.objects.db_manager(using).get_for_model(
        RconUser, for_concrete_model=False
    )
    for codename, name in FORK_PERMISSIONS:
        Permission.objects.using(using).get_or_create(
            content_type=content_type, codename=codename, defaults={"name": name}
        )
