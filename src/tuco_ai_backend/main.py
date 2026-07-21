from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from tuco_ai_backend.config import RuntimeConfigStore, Settings
from tuco_ai_backend.device_ws import device_websocket
from tuco_ai_backend.models import ConfigUpdate, DecisionRequest, DecisionResponse, PublicConfig
from tuco_ai_backend.providers.openai_compatible import (
    LlmConfigurationError,
    LlmProtocolError,
    OpenAICompatibleClient,
)
from tuco_ai_backend.providers.volcengine_asr import VolcengineAsrClient
from tuco_ai_backend.providers.volcengine_tts import VolcengineTtsClient
from tuco_ai_backend.tools import available_tools
from tuco_ai_backend.voice_pipeline import VoicePipeline

FRONTEND_DIR = Path(__file__).parent / "frontend"


def create_app(
    settings: Settings | None = None,
    *,
    llm_service: Any | None = None,
    voice_pipeline: Any | None = None,
) -> FastAPI:
    app = FastAPI(title="TUCO AI Backend", version="0.1.0")
    config_store = RuntimeConfigStore(settings or Settings())
    app.state.config_store = config_store
    app.state.llm_service = llm_service or OpenAICompatibleClient(config_store)

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        config = config_store.public_config()
        return {
            "status": "ok",
            "version": app.version,
            "capabilities": {
                "llm_tool_decision": True,
                "device_websocket": True,
                "volcengine_asr": config.volc_api_key_configured,
                "volcengine_tts": config.volc_api_key_configured,
            },
        }

    @app.get("/api/config", response_model=PublicConfig)
    async def get_config() -> PublicConfig:
        return config_store.public_config()

    @app.put("/api/config", response_model=PublicConfig)
    async def update_config(update: ConfigUpdate) -> PublicConfig:
        return config_store.update(update)

    @app.get("/api/tools")
    async def get_tools() -> list[dict[str, Any]]:
        return available_tools()

    @app.post("/api/test/decision", response_model=DecisionResponse)
    async def test_decision(request: DecisionRequest) -> DecisionResponse:
        try:
            return await app.state.llm_service.decide(request)
        except LlmConfigurationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (LlmProtocolError, ValidationError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            detail = f"LLM provider returned HTTP {exc.response.status_code}"
            raise HTTPException(status_code=502, detail=detail) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="LLM provider request failed") from exc

    @app.websocket("/ws/device")
    async def ws_device(websocket: WebSocket) -> None:
        pipeline = voice_pipeline or _build_voice_pipeline(config_store, app.state.llm_service)
        await device_websocket(websocket, pipeline)

    if FRONTEND_DIR.exists():
        app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

        @app.get("/", include_in_schema=False)
        async def frontend() -> FileResponse:
            return FileResponse(FRONTEND_DIR / "index.html")

    return app


def _build_voice_pipeline(config: RuntimeConfigStore, llm_service: Any) -> VoicePipeline | None:
    volc_key = config.volc_api_key()
    if not volc_key:
        return None
    return VoicePipeline(
        VolcengineAsrClient(
            api_key=volc_key,
            resource_id=config.volc_asr_resource_id,
        ),
        llm_service,
        VolcengineTtsClient(
            api_key=volc_key,
            resource_id=config.volc_tts_resource_id,
            voice_type=config.volc_tts_voice_type,
        ),
    )


app = create_app()


def run() -> None:
    uvicorn.run("tuco_ai_backend.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    run()
