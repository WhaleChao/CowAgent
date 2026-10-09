"""A remote file reply must not leave its temp file behind.

``_resolve_media_path`` fetches a URL reply into the agent's managed tmp dir --
the convention every channel follows through ``common.state_dir.tmp_dir()``,
because a bare ``/tmp`` resolves against a different drive depending on where
the process was launched. The voice branch in this same file already removes
what it fetched:

    for path in {file_path, amr_file, *files}:
        try:
            os.remove(path)
        except OSError:
            pass

The file branch did not, and nothing anywhere sweeps that directory. So each
remote document reply left one file of up to ``MAX_FILE_BYTES`` (100 MB)
behind, permanently.

A ``file://`` path, or a bare local path, belongs to the caller -- the agent
wrote it and may still be using it -- so only what this method downloaded is
removed. That is what the ``downloaded`` flag records, and it is the same
shape ``weixin_channel.py`` uses.
"""

import os

import pytest


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Point managed tmp storage at a throwaway workspace."""
    from common import state_dir

    root = tmp_path / "ws" / "tmp"
    root.mkdir(parents=True)
    monkeypatch.setattr(state_dir, "tmp_dir", lambda: root, raising=False)
    return root


@pytest.fixture
def channel(workspace, monkeypatch):
    """A WechatComAppChannel with the network and the WeCom client replaced."""
    from bridge.reply import Reply, ReplyType
    from channel.wechatcom import wechatcomapp_channel as mod

    cls = mod.WechatComAppChannel.__wrapped__
    handle = cls.__new__(cls)
    handle.agent_id = "agent-1"
    handle.sent = []
    handle.uploads = []

    class _Message:
        @staticmethod
        def send_file(agent_id, receiver, media_id):
            handle.sent.append(("file", media_id))

        @staticmethod
        def send_video(agent_id, receiver, media_id):
            handle.sent.append(("video", media_id))

        @staticmethod
        def send_text(agent_id, receiver, text):
            handle.sent.append(("text", text))

    class _Media:
        @staticmethod
        def upload(media_type, payload):
            handle.uploads.append((media_type, payload[0], len(payload[1])))
            return {"media_id": f"media-{len(handle.uploads)}"}

    handle.client = type("_Client", (), {"message": _Message, "media": _Media})()

    def _download(url, local, max_bytes, timeout=None, max_seconds=None):
        with open(local, "wb") as fh:
            fh.write(b"remote-body" * 64)
        return None

    monkeypatch.setattr(mod, "download_to_file", _download)
    return handle, Reply, ReplyType


def _leftovers(workspace):
    return sorted(p.name for p in workspace.iterdir() if p.is_file())


# --- the gap ----------------------------------------------------------------


def test_a_remote_file_reply_leaves_nothing_behind(channel, workspace):
    handle, Reply, ReplyType = channel

    for index in range(3):
        handle._send_file(Reply(ReplyType.FILE, f"https://example.com/r{index}.pdf"),
                          "user-1")

    assert handle.uploads, "the upload still happened"
    assert _leftovers(workspace) == [], "each remote reply leaked its temp file"


def test_a_remote_video_reply_leaves_nothing_behind(channel, workspace):
    handle, Reply, ReplyType = channel

    handle._send_file(Reply(ReplyType.VIDEO, "https://example.com/clip.mp4"), "user-1")

    assert handle.sent == [("video", "media-1")]
    assert _leftovers(workspace) == []


def test_an_upload_failure_still_cleans_up(channel, workspace, monkeypatch):
    handle, Reply, ReplyType = channel
    from channel.wechatcom import wechatcomapp_channel as mod

    def _boom(media_type, payload):
        raise mod.WeChatClientException(-1, "nope")

    handle.client.media.upload = staticmethod(_boom)

    handle._send_file(Reply(ReplyType.FILE, "https://example.com/r.pdf"), "user-1")

    assert _leftovers(workspace) == [], "the exception path leaked too"


def test_a_failed_download_leaves_nothing(channel, workspace, monkeypatch):
    handle, Reply, ReplyType = channel
    from channel.wechatcom import wechatcomapp_channel as mod

    def _boom(*args, **kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr(mod, "download_to_file", _boom)

    handle._send_file(Reply(ReplyType.FILE, "https://example.com/r.pdf"), "user-1")

    assert _leftovers(workspace) == []


# --- the caller's own files must survive ------------------------------------


def test_a_file_url_is_never_deleted(channel, workspace):
    handle, Reply, ReplyType = channel
    mine = workspace / "produced-by-agent.pdf"
    mine.write_bytes(b"agent output" * 16)

    handle._send_file(Reply(ReplyType.FILE, f"file://{mine}"), "user-1")

    assert mine.exists(), "the agent's own file must survive the send"
    assert handle.uploads


def test_a_bare_local_path_is_never_deleted(channel, workspace):
    handle, Reply, ReplyType = channel
    mine = workspace / "plain.pdf"
    mine.write_bytes(b"agent output" * 16)

    handle._send_file(Reply(ReplyType.FILE, str(mine)), "user-1")

    assert mine.exists()
    assert handle.uploads


# --- the resolver's contract -----------------------------------------------


def test_the_resolver_reports_whether_it_downloaded(channel, workspace):
    handle, _Reply, _ReplyType = channel
    _cls = type(handle)

    path, downloaded = handle._resolve_media_path("https://example.com/r.pdf")
    assert downloaded == path, "a fetched file is this method's to remove"

    mine = workspace / "mine.pdf"
    mine.write_bytes(b"x")
    path, downloaded = handle._resolve_media_path(f"file://{mine}")
    assert downloaded == "", "a file:// path belongs to the caller"
    assert path == str(mine)


def test_the_resolver_reports_nothing_for_a_missing_file(channel, workspace):
    handle, _Reply, _ReplyType = channel

    assert handle._resolve_media_path(str(workspace / "absent.pdf")) == ("", "")


def test_remove_media_tmp_tolerates_a_missing_file_and_an_empty_path():
    from channel.wechatcom.wechatcomapp_channel import _remove_media_tmp

    _remove_media_tmp("")
    _remove_media_tmp(os.path.join("nowhere", "never-created.pdf"))


def test_remove_media_tmp_deletes_what_it_can(workspace):
    from channel.wechatcom.wechatcomapp_channel import _remove_media_tmp

    path = workspace / "leftover.pdf"
    path.write_bytes(b"x")
    _remove_media_tmp(str(path))
    assert not path.exists()


def test_the_voice_branch_and_the_file_branch_agree():
    # The contrast this fixes: the voice branch inside send() already cleaned
    # up, which is why the file branch's omission was easy to miss.
    import inspect

    from channel.wechatcom import wechatcomapp_channel as mod

    cls = mod.WechatComAppChannel.__wrapped__
    send_source = inspect.getsource(cls.send)
    assert "os.remove" in send_source, "the voice branch still cleans up"
    assert "_remove_media_tmp" in inspect.getsource(cls._send_file)