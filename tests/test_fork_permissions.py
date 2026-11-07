import re
from pathlib import Path

import pytest

from rconweb.api.fork_permissions import FORK_PERMISSIONS, ensure_fork_permissions

REPO_ROOT = Path(__file__).resolve().parent.parent


class FakePermissionManager:
    def __init__(self, rows):
        self.rows = rows

    def using(self, alias):
        return self

    def get_or_create(self, content_type, codename, defaults):
        key = (content_type, codename)
        if key in self.rows:
            return self.rows[key], False
        self.rows[key] = defaults["name"]
        return self.rows[key], True


class FakeContentTypeManager:
    def db_manager(self, alias):
        return self

    def get_for_model(self, model, for_concrete_model=True):
        return f"content_type:{model.__name__}"


class FakeApps:
    def __init__(self, rows, missing=()):
        self.rows = rows
        self.missing = set(missing)

    def get_model(self, app_label, model_name):
        if (app_label, model_name) in self.missing:
            raise LookupError(model_name)
        if model_name == "Permission":
            return type("Permission", (), {"objects": FakePermissionManager(self.rows)})
        if model_name == "ContentType":
            return type("ContentType", (), {"objects": FakeContentTypeManager()})
        return type(model_name, (), {})


def test_creates_every_fork_permission_once_on_rconuser():
    rows = {}
    apps = FakeApps(rows)

    ensure_fork_permissions(sender=None, apps=apps)
    ensure_fork_permissions(sender=None, apps=apps)

    assert rows == {
        ("content_type:RconUser", codename): name for codename, name in FORK_PERMISSIONS
    }


@pytest.mark.parametrize(
    "apps",
    [None, FakeApps({}, missing={("api", "RconUser")})],
)
def test_does_nothing_without_a_usable_app_registry(apps):
    ensure_fork_permissions(sender=None, apps=apps)

    if apps is not None:
        assert apps.rows == {}


def test_every_api_permission_referenced_by_the_backend_is_declared():
    """A permission used by an endpoint must exist upstream (Meta) or in the fork list.

    Otherwise ``has_perm`` silently returns False and the endpoint is unreachable
    for everyone except superusers.
    """
    models_source = (REPO_ROOT / "rconweb/api/models.py").read_text()
    declared = set(re.findall(r'"(can_[a-z0-9_]+)"', models_source))
    declared |= {codename for codename, _ in FORK_PERMISSIONS}

    referenced = set()
    for path in ("rconweb/api/views.py", "rconweb/api/user_settings.py"):
        source = (REPO_ROOT / path).read_text()
        referenced |= set(re.findall(r'"api\.(can_[a-z0-9_]+)"', source))

    assert referenced - declared == set()
