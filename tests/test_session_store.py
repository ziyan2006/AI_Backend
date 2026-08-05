from tuco_ai_backend.session_store import GLOBAL_SESSION_STORE, SessionLogStore


def test_session_store_create_and_add_log() -> None:
    store = SessionLogStore(max_history=5)
    sess = store.create_session(level_id=101, level_title="启动飞船", session_id="s1")

    assert sess.session_id == "s1"
    assert sess.level_id == 101
    assert sess.is_active is True

    store.add_log(
        trace_id="tr1",
        module="PRE-CHECK",
        level="WARNING",
        message="[tr1] 触发本地积木缺失拦截",
        session_id="s1",
    )

    detail = store.get_session_detail("s1")
    assert detail is not None
    assert detail["status"] == "INTERCEPT"
    assert len(detail["logs"]) == 1
    assert detail["logs"][0]["module"] == "PRE-CHECK"

    store.close_session("s1")
    assert store.get_active_session() is None


def test_session_store_api_integration() -> None:
    GLOBAL_SESSION_STORE.create_session(
        level_id=102, level_title="点亮小灯", session_id="test_s2"
    )
    GLOBAL_SESSION_STORE.add_log("tr2", "LLM", "INFO", "模型纯文本回复", session_id="test_s2")

    summaries = GLOBAL_SESSION_STORE.list_sessions()
    assert any(s["session_id"] == "test_s2" for s in summaries)

    detail = GLOBAL_SESSION_STORE.get_session_detail("test_s2")
    assert detail is not None
    assert detail["level_title"] == "点亮小灯"
    GLOBAL_SESSION_STORE.close_session("test_s2")


def test_closing_session_clears_conversation_history() -> None:
    store = SessionLogStore(max_history=5)
    store.create_session(level_id=101, level_title="启动飞船", session_id="s1")
    store.add_conversation_turn("我叫小明", "你好，小明", session_id="s1")

    assert store.get_conversation_history("s1") == [
        {"role": "user", "content": "我叫小明"},
        {"role": "assistant", "content": "你好，小明"},
    ]

    store.close_session("s1")

    assert store.get_conversation_history("s1") == []


def test_session_detail_keeps_complete_device_exchange() -> None:
    store = SessionLogStore(max_history=5)
    store.create_session(level_id=502, level_title="三路进位", session_id="device-502")
    request = {
        "session_id": "device-502",
        "user_text": "接下来怎么做？",
        "circuit_snapshot": {"board": {"topology_revision": 7, "slots": []}},
    }
    response = {
        "assistant_text": "先看看已经放好的积木。",
        "tool_call": None,
        "topology_revision": 7,
    }

    store.add_device_exchange(
        trace_id="tr-device-502",
        request=request,
        response=response,
        session_id="device-502",
    )

    detail = store.get_session_detail("device-502")

    assert detail is not None
    assert detail["device_exchanges"] == [
        {
            "trace_id": "tr-device-502",
            "request": request,
            "response": response,
            "error": None,
        }
    ]
