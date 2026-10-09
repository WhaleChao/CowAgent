"""A downloaded reply must not outlive the send that fetched it.

``_download_remote_media`` puts reply media into managed tmp storage so a bare
name never lands in the process CWD, which a packaged desktop build does not
control. The callback path's base64 loader removes what it downloaded in a
``finally``; the three long-connection senders did not, and neither did the
intermediate files they derive:

    _send_image   download -> _ensure_image_format -> _compress_image
    _send_file    download
    _send_voice   download -> any_to_amr

So every remote media reply left one to three files behind in ``<workspace>/tmp``,
up to ``MAX_FILE_BYTES`` each, and nothing anywhere cleans that directory.
An agent that answers with a remote file once per turn fills the disk.

The same download also had no wall-clock budget. The socket timeout only bounds
the gap between two chunks -- ``common.media_download``'s own docstring says the
socket timeout "never fires on a server that keeps trickling bytes" -- and
``send()`` runs on the shared reply thread, so a stalled download blocked every
later message on that channel. Every other channel that downloads reply media
passes an explicit ``max_seconds``.
"""

import base64
import os
import sys

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Point managed tmp storage at a throwaway workspace."""
    from common import state_dir

    monkeypatch.setattr(state_dir, "tmp_dir", lambda: tmp_path / "tmp",
                        raising=False)
    (tmp_path / "tmp").mkdir(parents=True, exist_ok=True)
    return tmp_path / "tmp"


@pytest.fixture
def channel(workspace, monkeypatch):
    """A WecomBotChannel with the network replaced by a recording stub."""
    from channel.wecom_bot import wecom_bot_channel as mod

    handle = mod.WecomBotChannel.__wrapped__.__new__(mod.WecomBotChannel.__wrapped__)
    handle.uploads = []
    handle.sent = []
    handle.texts = []
    handle._req_id = 0

    def _upload(local_path, media_type):
        # Stand in for the chunked upload: record the file, and fail loudly if
        # the caller cleaned up before the upload could read it.
        with open(local_path, "rb") as handle_:
            data = handle_.read()
        handle.uploads.append((os.path.basename(local_path), media_type, len(data)))
        return f"media-{len(handle.uploads)}"

    handle._upload_media = _upload
    handle._ws_send = lambda payload: handle.sent.append(payload)
    handle._send_text = lambda text, *a, **k: handle.texts.append(text)
    handle._gen_req_id = lambda: "req-1"
    stub = _fake_download(mod, monkeypatch)
    monkeypatch.setattr(mod, "_download_remote_media", stub, raising=False)
    handle.download_stub = stub
    return handle


# A real 1x1 PNG: _ensure_image_format identifies the format by its bytes, so a
# text body would be rejected before the pipeline under test ever runs.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAE"
    "hQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _fake_download(mod, monkeypatch):
    """Build a replacement for _download_remote_media that writes a real file."""
    def _download(url, prefix, ext, max_bytes, read_timeout, max_seconds=None):
        captured["max_seconds"] = max_seconds
        base = mod._media_tmp_path(prefix)
        path = base + (ext or ".bin")
        with open(path, "wb") as fh:
            fh.write(_PNG)
        return path, len(_PNG), "image/png"

    captured = {}
    _download.captured = captured
    return _download


def _tmp_files(workspace):
    return sorted(p.name for p in workspace.iterdir() if p.is_file())


# --- cleanup ----------------------------------------------------------------


def test_a_remote_image_reply_leaves_nothing_behind(channel, workspace):
    channel._send_image("https://example.com/a.png", "chat-1", False)
    assert channel.uploads, "the upload still happened"
    assert _tmp_files(workspace) == []


def test_a_remote_file_reply_leaves_nothing_behind(channel, workspace):
    channel._send_file("https://example.com/a.pdf", "chat-1", False)
    assert channel.uploads
    assert _tmp_files(workspace) == []


def test_a_remote_voice_reply_leaves_nothing_behind(channel, workspace, monkeypatch):
    # No ffmpeg here, so any_to_amr raises -- the download must still be gone.
    import voice.audio_convert as audio

    def _boom(src, dst):
        raise RuntimeError("ffmpeg unavailable (simulated)")

    monkeypatch.setattr(audio, "any_to_amr", _boom, raising=False)

    channel._send_voice("https://example.com/a.mp3", "chat-1", False)

    assert _tmp_files(workspace) == [], "a failed conversion kept the download"


def test_the_image_pipeline_cleans_up_every_derived_file(channel, workspace,
                                                         monkeypatch):
    # _send_image writes up to three: the download, the format conversion and
    # the compression. All three are managed tmp files.
    handle = channel
    handle._ensure_image_format = lambda p: mod_path(workspace, "wecom_fmt_conv.png")
    handle._compress_image = lambda p, limit: mod_path(workspace, "wecom_compressed.jpg")

    handle._send_image("https://example.com/a.png", "chat-1", False)

    assert len(handle.uploads) == 1, "the compressed file was the one uploaded"
    assert _tmp_files(workspace) == [], "the download and the conversion leaked"


def mod_path(workspace, name):
    path = workspace / name
    path.write_bytes(b"derived" * 32)
    return str(path)


def test_an_upload_that_raises_still_cleans_up(channel, workspace):
    def _explode(local_path, media_type):
        raise RuntimeError("upload failed")

    channel._upload_media = _explode

    with pytest.raises(RuntimeError):
        channel._send_image("https://example.com/a.png", "chat-1", False)

    assert _tmp_files(workspace) == []


def test_a_local_file_is_never_deleted(channel, workspace):
    # The file:// branch belongs to the caller: deleting it would destroy the
    # agent's own output.
    local = workspace / "produced-by-agent.png"
    local.write_bytes(_PNG)

    channel._send_image(f"file://{local}", "chat-1", False)

    assert local.exists(), "a local file the agent produced must survive"
    assert channel.uploads


def test_a_local_file_without_the_scheme_is_also_kept(channel, workspace):
    local = workspace / "plain.png"
    local.write_bytes(_PNG)

    channel._send_image(str(local), "chat-1", False)

    assert local.exists()


def test_a_failed_download_leaves_nothing(channel, workspace):
    from channel.wecom_bot import wecom_bot_channel as mod

    def _boom(*args, **kwargs):
        raise ValueError("remote media is empty")

    original = mod._download_remote_media
    mod._download_remote_media = _boom
    try:
        channel._send_image("https://example.com/a.png", "chat-1", False)
    finally:
        mod._download_remote_media = original

    assert _tmp_files(workspace) == []


def test_the_callback_path_is_untouched_by_this_change():
    # It already cleaned up; this pins that the two paths now agree, so the
    # contrast documented above cannot reopen.
    import inspect

    from channel.wecom_bot import wecom_bot_channel as mod

    source = inspect.getsource(mod)
    assert source.count("finally:") >= 4, "every download path cleans up"


# --- the wall-clock budget --------------------------------------------------


def test_the_download_carries_a_total_budget():
    # The channel fixture stubs the download out, so check the real function:
    # a caller that forgets max_seconds still gets the module default.
    import inspect

    from channel.wecom_bot import wecom_bot_channel as mod

    signature = inspect.signature(mod._download_remote_media)
    assert "max_seconds" in signature.parameters, (
        "a socket timeout alone does not bound a trickling server"
    )
    assert signature.parameters["max_seconds"].default == mod._MAX_REMOTE_MEDIA_SECONDS


def test_the_budget_defaults_to_the_module_constant():
    import inspect

    from channel.wecom_bot import wecom_bot_channel as mod

    assert mod._MAX_REMOTE_MEDIA_SECONDS > 0
    source = inspect.getsource(mod._download_remote_media)
    assert "_MAX_REMOTE_MEDIA_SECONDS" in source


def test_remove_media_tmp_tolerates_a_missing_file(workspace):
    from channel.wecom_bot.wecom_bot_channel import _remove_media_tmp

    _remove_media_tmp(str(workspace / "never-created.png"))
    _remove_media_tmp("")


def test_remove_media_tmp_deletes_what_it_can(workspace):
    from channel.wecom_bot.wecom_bot_channel import _remove_media_tmp

    path = workspace / "leftover.png"
    path.write_bytes(b"x")
    _remove_media_tmp(str(path))
    assert not path.exists()