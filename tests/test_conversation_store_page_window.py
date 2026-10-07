"""A page window from a query string must not decide how much SQLite reads.

`/api/sessions` and `/api/history` hand `page` / `page_size` straight to
`ConversationStore`, which clamped `page` but never `page_size`:

    page = max(1, page)

so `page_size=-1` reached SQLite as `LIMIT -1` -- which SQLite reads as *no
limit*, dumping the whole table -- and `page_size=0` divided by zero while
locating the `until_seq` page. `HistoryHandler` wraps everything in
`except Exception`, so that ZeroDivisionError surfaced as HTTP 200 with
`{"status": "error"}` and the console's "load earlier messages" simply never
worked.

`MemoryService.list_files` clamps the same pair with the same ceiling; this is
that clamp applied to the conversation store, which every caller (web handlers,
`SessionService.dispatch`, the cloud-protocol history query) goes through.

`web` is stubbed so the suite does not need the optional web.py dependency.
"""

import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

if "web" not in sys.modules:
    web_stub = types.ModuleType("web")
    web_stub.HTTPError = type("HTTPError", (Exception,), {})
    web_stub.cookies = lambda: {}
    web_stub.header = lambda *args, **kwargs: None
    web_stub.data = lambda: b"{}"
    web_stub.input = lambda **kwargs: types.SimpleNamespace(**kwargs)
    web_stub.setcookie = lambda *args, **kwargs: None
    web_stub.seeother = lambda *args, **kwargs: Exception("seeother")
    web_stub.notfound = lambda *args, **kwargs: Exception("notfound")
    web_stub.badrequest = lambda *args, **kwargs: Exception("badrequest")
    web_stub.application = lambda *args, **kwargs: types.SimpleNamespace(
        wsgifunc=lambda: None
    )
    web_stub.httpserver = types.SimpleNamespace(
        LogMiddleware=type("LogMiddleware", (), {"log": lambda *a, **k: None}),
        StaticMiddleware=lambda app: app,
        WSGIServer=lambda *a, **k: types.SimpleNamespace(serve_forever=lambda: None),
    )
    sys.modules["web"] = web_stub


SESSION_COUNT = 5


@pytest.fixture
def store(tmp_path):
    """A real store holding SESSION_COUNT two-turn web sessions."""
    from agent.memory.conversation_store import ConversationStore

    instance = ConversationStore(tmp_path / "history.db")
    for index in range(SESSION_COUNT):
        instance.append_messages(
            session_id=f"s{index}",
            channel_type="web",
            messages=[
                {"role": "user", "content": f"q{index}"},
                {"role": "assistant", "content": f"a{index}"},
            ],
        )
    return instance


@pytest.mark.parametrize("bad_size", [-1, 0, -(10 ** 9)])
def test_a_negative_page_size_cannot_dump_the_whole_table(store, bad_size):
    # RED on unfixed code: LIMIT -1 means "no limit", so the response carried
    # every session instead of a page, and the caller could keep asking.
    result = store.list_sessions(channel_type="web", page=1, page_size=bad_size)

    assert len(result["sessions"]) < SESSION_COUNT
    assert result["page_size"] >= 1


def test_an_absurd_page_size_is_capped(store):
    from agent.memory.conversation_store import MAX_PAGE_SIZE

    result = store.list_sessions(channel_type="web", page=1, page_size=10 ** 9)

    assert result["page_size"] == MAX_PAGE_SIZE


def test_a_non_numeric_page_size_falls_back_to_the_default(store):
    # The handlers used to raise ValueError on int(params.page_size), which the
    # surrounding except turned into a 200 with an error body.
    result = store.list_sessions(channel_type="web", page="1", page_size="abc")

    assert result["page_size"] == 50


def test_a_zero_page_size_does_not_divide_by_zero_on_until_seq(store):
    # RED on unfixed code: idx // page_size with page_size=0 raised
    # ZeroDivisionError, which /api/history reported as status=error.
    result = store.load_history_page(
        session_id="s0", page=1, page_size=0, until_seq=1
    )

    assert result["page_size"] >= 1
    assert result["messages"], "an until_seq page must still return turns"


def test_paging_still_walks_forward_after_clamping(store):
    first = store.list_sessions(channel_type="web", page=1, page_size=2)
    second = store.list_sessions(channel_type="web", page=2, page_size=2)

    ids = [s["session_id"] for s in first["sessions"]]
    assert len(ids) == 2
    assert first["has_more"] is True
    assert not set(ids) & {s["session_id"] for s in second["sessions"]}


def test_the_sessions_endpoint_clamps_before_merging_agents(store, tmp_path):
    # scope=all multiplies page * page_size and slices the merged list itself,
    # so the handler has to clamp before that arithmetic, not just at the store.
    from channel.web.api import sessions as sessions_api

    params = types.SimpleNamespace(
        page="1", page_size="-1", agent_id="", agent="", scope="all"
    )

    with patch.object(sessions_api, "_require_auth"), \
         patch.object(sessions_api.web, "header", lambda *a, **k: None), \
         patch.object(sessions_api.web, "input", lambda **kwargs: params), \
         patch("agent.memory.get_conversation_store", lambda *a, **k: store), \
         patch("agent.registry.get_agent_registry") as registry:
        registry.return_value.list.return_value = []
        response = json.loads(sessions_api.SessionsHandler().GET())

    assert response["status"] == "success"
    assert response["page_size"] >= 1
    assert len(response["sessions"]) < SESSION_COUNT


def test_the_history_endpoint_survives_a_zero_page_size(store):
    from channel.web.api import sessions as sessions_api

    params = types.SimpleNamespace(
        session_id="s0", page="1", page_size="0", agent_id="", until_seq="1"
    )

    class _NoLiveStream:
        def resumable_stream(self, session_id, agent_id=None):
            return None

    with patch.object(sessions_api, "_require_auth"), \
         patch.object(sessions_api.web, "header", lambda *a, **k: None), \
         patch.object(sessions_api.web, "input", lambda **kwargs: params), \
         patch.object(sessions_api, "WebChannel", _NoLiveStream), \
         patch("agent.memory.get_conversation_store", lambda *a, **k: store), \
         patch("agent.workspace.project_store.get_project_dir", return_value=None):
        response = json.loads(sessions_api.HistoryHandler().GET())

    assert response["status"] == "success"
    assert response["messages"]


def test_the_cap_matches_the_memory_service_ceiling():
    # One ceiling for both paginated stores, so the console cannot be handed a
    # larger window through one endpoint than through the other.
    from agent.memory.conversation_store import MAX_PAGE_SIZE as store_cap
    from agent.memory.service import MAX_PAGE_SIZE as service_cap

    assert store_cap == service_cap


def test_a_valid_window_is_passed_through_untouched(store):
    # The clamp is a guard, not a rewrite: a normal request is unchanged.
    result = store.list_sessions(channel_type="web", page=2, page_size=3)

    assert result["page"] == 2
    assert result["page_size"] == 3


def test_the_page_is_still_clamped_upwards(store):
    result = store.list_sessions(channel_type="web", page=0, page_size=2)

    assert result["page"] == 1
    assert len(result["sessions"]) == 2


def test_a_non_numeric_page_falls_back_to_the_first_page(store):
    result = store.list_sessions(channel_type="web", page="abc", page_size=2)

    assert result["page"] == 1


def test_the_helper_is_importable_from_one_place():
    # Both handlers and the store share it, so the rule is stated once.
    from agent.memory.conversation_store import _page_window

    assert _page_window(0, 0, 50) == (1, 1)
    assert _page_window(3, 5, 50) == (3, 5)
    assert _page_window("x", "y", 20) == (1, 20)


def test_the_helper_keeps_the_default_page_size_when_the_value_is_junk():
    from agent.memory.conversation_store import _page_window

    assert _page_window(2, None, 20) == (2, 20)
    assert _page_window(2, [1], 20) == (2, 20)


def test_a_negative_page_size_cannot_walk_past_the_end_of_the_history(store):
    # The store also slices newest_first[offset:end] directly; page_size=-1
    # used to make end negative and silently drop the oldest turn.
    result = store.load_history_page(session_id="s0", page=1, page_size=-1)

    assert result["page_size"] >= 1
    assert result["messages"], "history must not come back empty for a page"


def test_the_module_docstring_records_the_clamp():
    # The two paginated methods document their bounds; pin that they still do.
    from agent.memory.conversation_store import ConversationStore

    for method in (ConversationStore.list_sessions, ConversationStore.load_history_page):
        assert "clamped" in (method.__doc__ or "")


def test_page_size_zero_never_reaches_the_sql(store):
    # Pin the mechanism: whatever the caller passes, the SQL layer only ever
    # sees a positive LIMIT.
    seen = []
    real_connect = store._connect

    class _ConnProxy:
        """sqlite3.Connection.execute is read-only, so wrap instead of patch."""

        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, params=()):
            if "LIMIT ?" in sql:
                # The SQL layers params as LIMIT ? OFFSET ?, so page_size is
                # the second-to-last bound value whichever way it is passed.
                bound = list(params)
                seen.append(bound[-2] if len(bound) >= 2 else None)
            return self._conn.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    store._connect = lambda: _ConnProxy(real_connect())
    try:
        store.list_sessions(channel_type="web", page=1, page_size=0)
        store.list_sessions(channel_type="web", page=1, page_size=-1)
    finally:
        store._connect = real_connect

    assert seen, "the LIMIT probe never ran"
    assert all(value is not None and value > 0 for value in seen), seen


def test_the_store_still_opens_the_database_it_was_given(tmp_path):
    # Guards the fixture itself: a store pointed at the wrong path would make
    # every assertion above vacuously true.
    from agent.memory.conversation_store import ConversationStore

    instance = ConversationStore(Path(tmp_path) / "explicit.db")
    assert Path(instance._db_path).exists()
