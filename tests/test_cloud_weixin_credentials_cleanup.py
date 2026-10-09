# encoding: utf-8
"""Deleting a Weixin instance/channel must clear its per-instance token file.

Weixin credentials are stored per instance (``weixin_credentials.{instance_id}.json``)
so several accounts in one process never overwrite one file. The cloud control
plane removes instances through three paths -- ``instance_delete``,
``instance_update`` with ``enabled=N``, and ``channel_sync``. Until the fix none
of them removed the per-instance credential file, so deleting and re-adding an
instance silently re-logged it into the old account from the token left on disk.
``_remove_weixin_credentials`` now takes the instance id and the deletion paths
pass it through.
"""

import sys
import types
from unittest.mock import patch

# The remote client module imports an optional runtime SDK (linkai) that is not
# present in the test environment. Stub it so we can unit-test our own logic.
if "linkai" not in sys.modules:
    _linkai = types.ModuleType("linkai")
    _linkai.LinkAIClient = type("LinkAIClient", (), {"__init__": lambda self, *a, **k: None})
    _linkai.PushMsg = type("PushMsg", (), {})
    sys.modules["linkai"] = _linkai

import common.cloud_client as cloud_client  # noqa: E402
from common.cloud_client import CloudClient  # noqa: E402


def _path_resolver(tmp_path):
    def _resolve(instance_id=""):
        return str(tmp_path / f"weixin_credentials.{instance_id or 'default'}.json")

    return _resolve


# ---------------------------------------------------------------------------
# the helper itself: per-instance vs legacy path
# ---------------------------------------------------------------------------


def test_remove_weixin_credentials_clears_per_instance_file(tmp_path):
    inst_file = tmp_path / "weixin_credentials.abc.json"
    inst_file.write_text("{}")

    with patch.object(cloud_client, "get_weixin_credentials_path",
                      side_effect=_path_resolver(tmp_path)):
        CloudClient._remove_weixin_credentials("abc")

    assert not inst_file.exists()


def test_remove_weixin_credentials_keeps_default_when_instance_given(tmp_path):
    default_file = tmp_path / "weixin_credentials.default.json"
    default_file.write_text("{}")
    inst_file = tmp_path / "weixin_credentials.abc.json"
    inst_file.write_text("{}")

    with patch.object(cloud_client, "get_weixin_credentials_path",
                      side_effect=_path_resolver(tmp_path)):
        CloudClient._remove_weixin_credentials("abc")

    # A per-instance delete must not wipe the legacy single-login file.
    assert default_file.exists()
    assert not inst_file.exists()


def test_remove_weixin_credentials_clears_default_when_no_instance(tmp_path):
    default_file = tmp_path / "weixin_credentials.default.json"
    default_file.write_text("{}")

    with patch.object(cloud_client, "get_weixin_credentials_path",
                      side_effect=_path_resolver(tmp_path)):
        CloudClient._remove_weixin_credentials()

    assert not default_file.exists()


# ---------------------------------------------------------------------------
# the deletion paths actually wire the cleanup through
# ---------------------------------------------------------------------------


def _client():
    client = CloudClient.__new__(CloudClient)
    client.channel_mgr = None  # skip the threaded channel removal
    return client


def test_handle_instance_delete_clears_weixin_credentials(tmp_path):
    inst_file = tmp_path / "weixin_credentials.abc.json"
    inst_file.write_text("{}")
    client = _client()

    with patch("channel.channel_instances.remove_instance", lambda *a, **k: None), \
            patch.object(cloud_client, "get_weixin_credentials_path",
                         side_effect=_path_resolver(tmp_path)):
        client._handle_instance_delete("abc", "weixin", {})

    assert not inst_file.exists()


def test_handle_instance_update_disabled_clears_weixin_credentials(tmp_path):
    inst_file = tmp_path / "weixin_credentials.abc.json"
    inst_file.write_text("{}")
    client = _client()

    with patch("channel.channel_instances.remove_instance", lambda *a, **k: None), \
            patch.object(cloud_client, "get_weixin_credentials_path",
                         side_effect=_path_resolver(tmp_path)):
        client._handle_instance_update("abc", "weixin", {"enabled": "N"})

    assert not inst_file.exists()


def test_handle_instance_delete_skips_non_weixin(tmp_path):
    # A feishu instance deletion must not touch a weixin credential file.
    inst_file = tmp_path / "weixin_credentials.abc.json"
    inst_file.write_text("{}")
    client = _client()

    with patch("channel.channel_instances.remove_instance", lambda *a, **k: None), \
            patch.object(cloud_client, "get_weixin_credentials_path",
                         side_effect=_path_resolver(tmp_path)):
        client._handle_instance_delete("abc", "feishu", {})

    assert inst_file.exists()
