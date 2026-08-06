import logging
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tuco_ai_backend.config import Settings
from tuco_ai_backend.evaluation import LEVEL_EVAL_CASES, build_circuit_coach_v2
from tuco_ai_backend.main import create_app
from tuco_ai_backend.models import CircuitCoachDecisionRequest, DecisionResponse
from tuco_ai_backend.providers.openai_compatible import (
    LlmConfigurationError,
    LlmProtocolError,
)
from tuco_ai_backend.session_store import GLOBAL_SESSION_STORE


class FakeLlmService:
    async def decide(self, request):
        return DecisionResponse(
            assistant_text="测试回答",
            topology_revision=request.circuit.topology_revision,
        )


class HistoryRecordingLlmService:
    def __init__(self) -> None:
        self.histories: list[list[dict[str, str]]] = []

    async def decide(self, request, history=None, trace_id=None):
        self.histories.append(list(history or []))
        return DecisionResponse(
            assistant_text=f"第 {len(self.histories)} 轮回答",
            topology_revision=request.circuit.topology_revision,
        )


class PrecheckLoggingLlmService:
    async def decide(self, request, trace_id=None):
        logging.getLogger("tuco_ai_backend.providers.openai_compatible").warning(
            "[%s] [PRE-CHECK] 触发本地积木缺失拦截: 还差 1 块输入积木",
            trace_id,
        )
        return DecisionResponse(
            assistant_text="请先补齐输入积木。",
            topology_revision=request.circuit.topology_revision,
        )


class FailingLlmService:
    async def decide(self, request, trace_id=None):
        raise LlmConfigurationError("LLM base URL is invalid")


class CircuitCoachHistoryService:
    def __init__(self) -> None:
        self.histories: list[list[dict[str, str]]] = []

    async def decide(self, request, history=None, trace_id=None):
        self.histories.append(list(history or []))
        return DecisionResponse(
            assistant_text=f"第 {len(self.histories)} 轮 v2 回答",
            topology_revision=request.circuit_snapshot.board.topology_revision,
        )


class RaisingCircuitCoachService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def decide(self, request, history=None, trace_id=None):
        raise self.error


def circuit_coach_request_body(
    *, session_id: str = "v2-device-error-101"
) -> dict[str, object]:
    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 101)
    circuit_snapshot = build_circuit_coach_v2(level, "empty").model_dump(by_alias=True)
    return {
        "session_id": session_id,
        "user_text": "这关要做什么？",
        "circuit_snapshot": circuit_snapshot,
    }


def request_validation_error() -> ValidationError:
    try:
        CircuitCoachDecisionRequest.model_validate({})
    except ValidationError as exc:
        return exc
    raise AssertionError("expected request validation to fail")


def test_health_and_redacted_config() -> None:
    app = create_app(
        Settings(llm_api_key="hidden", admin_token="admin-test-token"),
        llm_service=FakeLlmService(),
    )

    with TestClient(app) as client:
        health = client.get("/api/health")
        config = client.get("/api/config", headers={"X-Tuco-Admin-Token": "admin-test-token"})

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert config.status_code == 200
    assert config.json()["llm_api_key_configured"] is True
    assert "llm_api_key" not in config.json()
    assert "hidden" not in config.text


def test_config_update_and_decision_endpoint(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    app = create_app(
        Settings(llm_api_key=None, admin_token="admin-test-token"),
        llm_service=FakeLlmService(),
        config_env_path=env_path,
    )
    headers = {"X-Tuco-Admin-Token": "admin-test-token"}

    with TestClient(app) as client:
        updated = client.put(
            "/api/config",
            headers=headers,
            json={
                "llm_base_url": "https://relay.example/v1/",
                "llm_model": "new-model",
                "llm_api_key": "temporary",
            },
        )
        response = client.post(
            "/api/test/decision",
            headers=headers,
            json={
                "question": "为什么灯不亮？",
                "circuit": {
                    "schema_version": 1,
                    "topology_revision": 7,
                    "slots": [],
                    "valid_links": [],
                    "invalid_links": [],
                    "scan": {},
                },
            },
        )

    assert updated.status_code == 200
    assert updated.json()["llm_base_url"] == "https://relay.example/v1"
    assert "temporary" not in updated.text
    assert response.status_code == 200
    assert response.json()["assistant_text"] == "测试回答"
    assert "TUCO_LLM_API_KEY=temporary" in env_path.read_text(encoding="utf-8")


def test_circuit_coach_v2_endpoint_uses_device_session_history() -> None:
    service = CircuitCoachHistoryService()
    app = create_app(Settings(llm_api_key="configured"), circuit_coach_service=service)
    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 101)
    circuit_snapshot = build_circuit_coach_v2(level, "empty").model_dump(by_alias=True)
    request_body = {
        "session_id": "v2-device-level-101",
        "circuit_snapshot": circuit_snapshot,
    }

    with TestClient(app) as client:
        first = client.post(
            "/api/device/circuit-coach/decision",
            json={**request_body, "user_text": "我叫小明"},
        )
        second = client.post(
            "/api/device/circuit-coach/decision",
            json={**request_body, "user_text": "你记得我的名字吗？"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["assistant_text"] == "第 2 轮 v2 回答"
    assert service.histories == [
        [],
        [
            {"role": "user", "content": "我叫小明"},
            {"role": "assistant", "content": "第 1 轮 v2 回答"},
        ],
    ]


def test_circuit_coach_v2_endpoint_records_raw_device_exchange() -> None:
    service = CircuitCoachHistoryService()
    app = create_app(Settings(llm_api_key="configured"), circuit_coach_service=service)
    level = next(case for case in LEVEL_EVAL_CASES if case.level_id == 502)
    circuit_snapshot = build_circuit_coach_v2(level, "placed-io").model_dump(by_alias=True)
    request_body = {
        "session_id": "v2-device-raw-502",
        "user_text": "接下来怎么做？",
        "circuit_snapshot": circuit_snapshot,
    }

    with TestClient(app) as client:
        response = client.post("/api/device/circuit-coach/decision", json=request_body)
        detail = client.get("/api/test/sessions/v2-device-raw-502")

    assert response.status_code == 200
    assert detail.status_code == 200
    exchanges = detail.json()["device_exchanges"]
    assert len(exchanges) == 1
    assert exchanges[0]["request"] == request_body
    assert exchanges[0]["response"] == response.json()
    assert exchanges[0]["error"] is None


def test_circuit_coach_v2_success_response_reuses_trace_id_in_session() -> None:
    session_id = "v2-device-success-trace-101"
    app = create_app(
        Settings(llm_api_key="configured"),
        circuit_coach_service=CircuitCoachHistoryService(),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/device/circuit-coach/decision",
            json=circuit_coach_request_body(session_id=session_id),
        )
        detail = client.get(f"/api/test/sessions/{session_id}")

    assert response.status_code == 200
    trace_id = response.json()["trace_id"]
    assert trace_id.startswith("tr_")
    exchange = detail.json()["device_exchanges"][0]
    assert exchange["trace_id"] == trace_id
    assert exchange["response"]["trace_id"] == trace_id


@pytest.mark.parametrize(
    ("error", "status_code", "code", "stage", "retryable", "message"),
    [
        (
            LlmConfigurationError("missing key"),
            503,
            "LLM_UNCONFIGURED",
            "llm_configuration",
            False,
            "模型服务尚未配置",
        ),
        (
            httpx.TimeoutException("timed out"),
            504,
            "LLM_TIMEOUT",
            "llm_provider",
            True,
            "模型服务响应超时",
        ),
        (
            LlmProtocolError("bad response"),
            502,
            "LLM_PROTOCOL_ERROR",
            "llm_protocol",
            False,
            "模型响应格式异常",
        ),
        (
            request_validation_error(),
            502,
            "LLM_PROTOCOL_ERROR",
            "llm_protocol",
            False,
            "模型响应格式异常",
        ),
        (
            httpx.ConnectError("connection failed"),
            502,
            "LLM_UPSTREAM_ERROR",
            "llm_provider",
            True,
            "模型服务连接失败",
        ),
        (
            RuntimeError("unexpected"),
            500,
            "INTERNAL_ERROR",
            "internal",
            False,
            "助教服务内部异常",
        ),
    ],
)
def test_circuit_coach_v2_endpoint_returns_structured_errors(
    error: Exception,
    status_code: int,
    code: str,
    stage: str,
    retryable: bool,
    message: str,
) -> None:
    session_id = f"v2-device-{code.lower()}-{type(error).__name__.lower()}"
    app = create_app(
        Settings(llm_api_key="configured"),
        circuit_coach_service=RaisingCircuitCoachService(error),
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/device/circuit-coach/decision",
            json=circuit_coach_request_body(session_id=session_id),
        )
        detail = client.get(f"/api/test/sessions/{session_id}")

    assert response.status_code == status_code
    payload = response.json()
    assert payload["error"] == {
        "code": code,
        "stage": stage,
        "retryable": retryable,
        "message": message,
    }
    assert payload["trace_id"].startswith("tr_")
    exchange = detail.json()["device_exchanges"][0]
    assert exchange["trace_id"] == payload["trace_id"]
    assert exchange["response"] == payload
    assert exchange["error"] == message


@pytest.mark.parametrize(
    ("provider_status", "retryable"),
    [(401, False), (429, True), (500, True)],
)
def test_circuit_coach_v2_endpoint_maps_provider_http_status(
    provider_status: int, retryable: bool
) -> None:
    request = httpx.Request("POST", "https://provider.example/v1/chat/completions")
    response = httpx.Response(provider_status, request=request)
    error = httpx.HTTPStatusError(
        "provider rejected request",
        request=request,
        response=response,
    )
    app = create_app(
        Settings(llm_api_key="configured"),
        circuit_coach_service=RaisingCircuitCoachService(error),
    )

    with TestClient(app) as client:
        result = client.post(
            "/api/device/circuit-coach/decision",
            json=circuit_coach_request_body(
                session_id=f"v2-device-http-{provider_status}"
            ),
        )

    assert result.status_code == 502
    assert result.json()["error"] == {
        "code": "LLM_UPSTREAM_ERROR",
        "stage": "llm_provider",
        "retryable": retryable,
        "message": f"模型服务返回异常状态（HTTP {provider_status}）",
    }


def test_circuit_coach_v2_request_validation_error_is_structured() -> None:
    app = create_app(Settings(llm_api_key="configured"))
    request_body = circuit_coach_request_body(session_id="v2-device-invalid-request")
    request_body.pop("user_text")

    with TestClient(app) as client:
        response = client.post("/api/device/circuit-coach/decision", json=request_body)

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "REQUEST_INVALID",
        "stage": "request_validation",
        "retryable": False,
        "message": "设备请求格式不正确",
    }
    assert response.json()["trace_id"].startswith("tr_")


def test_text_decision_records_precheck_in_active_session() -> None:
    app = create_app(
        Settings(llm_api_key="configured"),
        llm_service=PrecheckLoggingLlmService(),
    )
    session = GLOBAL_SESSION_STORE.create_session(
        level_id=101,
        level_title="启动飞船",
        session_id="text-precheck",
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/test/decision",
            json={
                "question": "为什么灯不亮？",
                "circuit": {
                    "schema_version": 1,
                    "topology_revision": 7,
                    "slots": [],
                    "valid_links": [],
                    "invalid_links": [],
                    "scan": {},
                },
            },
        )
        detail = client.get(f"/api/test/sessions/{session.session_id}")

    assert response.status_code == 200
    assert detail.status_code == 200
    assert detail.json()["status"] == "INTERCEPT"
    assert any(log["module"] == "PRE-CHECK" for log in detail.json()["logs"])


def test_text_decision_records_llm_error_in_active_session() -> None:
    app = create_app(
        Settings(llm_api_key="configured"),
        llm_service=FailingLlmService(),
    )
    session = GLOBAL_SESSION_STORE.create_session(
        level_id=101,
        level_title="启动飞船",
        session_id="text-llm-error",
    )

    previous_disable_level = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/test/decision",
                json={
                    "question": "这关要做什么？",
                    "circuit": {
                        "schema_version": 2,
                        "topology_revision": 8,
                        "slots": [],
                        "valid_links": [],
                        "invalid_links": [],
                        "scan": {},
                    },
                },
            )
            detail = client.get(f"/api/test/sessions/{session.session_id}")
    finally:
        logging.disable(previous_disable_level)

    assert response.status_code == 503
    assert detail.status_code == 200
    assert any(
        log["module"] == "LLM"
        and log["level"] == "ERROR"
        and "LLM base URL is invalid" in log["message"]
        for log in detail.json()["logs"]
    )


def test_session_start_creates_active_session_from_json_body() -> None:
    app = create_app(Settings())

    with TestClient(app) as client:
        response = client.post(
            "/api/test/sessions/start",
            json={"level_id": 101, "level_title": "启动飞船"},
        )

    assert response.status_code == 200
    assert response.json()["level_id"] == 101
    assert response.json()["level_title"] == "启动飞船"
    assert response.json()["is_active"] is True


def test_text_decision_reuses_active_session_conversation_history() -> None:
    llm_service = HistoryRecordingLlmService()
    app = create_app(Settings(llm_api_key="configured"), llm_service=llm_service)

    request_body = {
        "circuit": {
            "schema_version": 3,
            "topology_revision": 7,
            "slots": [],
            "valid_links": [],
            "invalid_links": [],
            "scan": {},
        }
    }
    with TestClient(app) as client:
        started = client.post(
            "/api/test/sessions/start",
            json={"level_id": 101, "level_title": "启动飞船"},
        )
        first = client.post(
            "/api/test/decision",
            json={**request_body, "question": "我叫小明"},
        )
        second = client.post(
            "/api/test/decision",
            json={**request_body, "question": "你记得我的名字吗？"},
        )
        ended = client.post("/api/test/sessions/end")

    assert started.status_code == 200
    assert first.status_code == 200
    assert second.status_code == 200
    assert ended.status_code == 200
    assert llm_service.histories == [
        [],
        [
            {"role": "user", "content": "我叫小明"},
            {"role": "assistant", "content": "第 1 轮回答"},
        ],
    ]


def test_admin_api_rejects_missing_token() -> None:
    app = create_app(Settings(admin_token="admin-test-token"))

    with TestClient(app) as client:
        response = client.get("/api/config")

    assert response.status_code == 403


def test_frontend_is_served() -> None:
    app = create_app(Settings())

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "TUCO AI 后端测试台" in response.text
    assert "/static/app.js" in response.text
