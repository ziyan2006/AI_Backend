from pathlib import Path

from tuco_ai_backend.config import RuntimeConfigStore, Settings
from tuco_ai_backend.models import ConfigUpdate


def test_default_asr_resource_uses_streaming_model_1() -> None:
    assert Settings().volc_asr_resource_id == "volc.bigasr.sauc.duration"


def test_public_config_redacts_api_key() -> None:
    store = RuntimeConfigStore(
        Settings(
            llm_base_url="https://relay.example/v1",
            llm_model="test-model",
            llm_api_key="super-secret",
            volc_api_key="volc-super-secret",
        )
    )

    public = store.public_config().model_dump()

    assert public["llm_api_key_configured"] is True
    assert public["volc_api_key_configured"] is True
    assert "llm_api_key" not in public
    assert "volc_api_key" not in public
    assert "super-secret" not in str(public)
    assert "volc-super-secret" not in str(public)


def test_runtime_update_keeps_secret_in_memory_only(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    store = RuntimeConfigStore(Settings(llm_api_key=None), env_path=env_path)

    public = store.update(
        ConfigUpdate(
            llm_base_url="https://new.example/v1/",
            llm_model="new-model",
            llm_api_key="temporary-key",
        )
    )

    assert public.llm_base_url == "https://new.example/v1"
    assert public.llm_model == "new-model"
    assert public.llm_api_key_configured is True
    assert store.api_key() == "temporary-key"
    persisted = dict(
        line.split("=", 1)
        for line in env_path.read_text(encoding="utf-8").splitlines()
    )
    assert persisted["TUCO_LLM_BASE_URL"] == "https://new.example/v1"
    assert persisted["TUCO_LLM_MODEL"] == "new-model"
    assert persisted["TUCO_LLM_API_KEY"] == "temporary-key"


def test_runtime_update_strips_secret_whitespace(tmp_path: Path) -> None:
    store = RuntimeConfigStore(Settings(), env_path=tmp_path / ".env")

    store.update(
        ConfigUpdate(
            llm_api_key="  llm-key\r\n",
            volc_api_key="\tvolc-key  ",
        )
    )

    assert store.api_key() == "llm-key"
    assert store.volc_api_key() == "volc-key"
