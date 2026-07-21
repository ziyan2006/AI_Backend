from fastapi.testclient import TestClient

from tuco_ai_backend.config import Settings
from tuco_ai_backend.main import create_app
from tuco_ai_backend.models import DecisionResponse


class FakeLlmService:
    async def decide(self, request):
        return DecisionResponse(
            assistant_text="测试回答",
            topology_revision=request.circuit.topology_revision,
        )


def test_health_and_redacted_config() -> None:
    app = create_app(Settings(llm_api_key="hidden"), llm_service=FakeLlmService())

    with TestClient(app) as client:
        health = client.get("/api/health")
        config = client.get("/api/config")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert config.status_code == 200
    assert config.json()["llm_api_key_configured"] is True
    assert "llm_api_key" not in config.json()
    assert "hidden" not in config.text


def test_config_update_and_decision_endpoint() -> None:
    app = create_app(Settings(llm_api_key=None), llm_service=FakeLlmService())

    with TestClient(app) as client:
        updated = client.put(
            "/api/config",
            json={
                "llm_base_url": "https://relay.example/v1/",
                "llm_model": "new-model",
                "llm_api_key": "temporary",
            },
        )
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

    assert updated.status_code == 200
    assert updated.json()["llm_base_url"] == "https://relay.example/v1"
    assert "temporary" not in updated.text
    assert response.status_code == 200
    assert response.json()["assistant_text"] == "测试回答"


def test_frontend_is_served() -> None:
    app = create_app(Settings())

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "TUCO AI 后端测试台" in response.text
    assert "/static/app.js" in response.text

