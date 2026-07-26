from __future__ import annotations

import hmac
import logging
import time
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

import httpx
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from tuco_ai_backend.audio_capture import AudioCaptureStore
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
from tuco_ai_backend.session_store import GLOBAL_SESSION_STORE, SessionTraceLogHandler
from tuco_ai_backend.tools import available_tools
from tuco_ai_backend.voice_pipeline import VoicePipeline

FRONTEND_DIR = Path(__file__).parent / "frontend"


class StartSessionRequest(BaseModel):
    level_id: int
    level_title: str


def create_app(
    settings: Settings | None = None,
    *,
    llm_service: Any | None = None,
    voice_pipeline: Any | None = None,
    config_env_path: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="TUCO AI Backend", version="0.1.0")
    active_settings = settings or Settings()
    config_store = RuntimeConfigStore(active_settings, env_path=config_env_path)
    audio_capture = AudioCaptureStore(
        enabled=active_settings.audio_capture_enabled,
        directory=active_settings.audio_capture_dir,
        retention_days=active_settings.audio_capture_retention_days,
        max_files=active_settings.audio_capture_max_files,
    )
    app.state.config_store = config_store
    app.state.llm_service = llm_service or OpenAICompatibleClient(config_store)
    app.state.audio_capture = audio_capture

    async def require_admin(
        x_tuco_admin_token: Annotated[str | None, Header()] = None,
    ) -> None:
        admin_token = config_store.admin_token()
        if admin_token:
            if not x_tuco_admin_token or not hmac.compare_digest(
                x_tuco_admin_token, admin_token
            ):
                raise HTTPException(status_code=403, detail="administrator authentication failed")

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
                "audio_capture": audio_capture.enabled,
            },
        }

    @app.get("/api/config", response_model=PublicConfig, dependencies=[Depends(require_admin)])
    async def get_config() -> PublicConfig:
        return config_store.public_config()

    @app.put("/api/config", response_model=PublicConfig, dependencies=[Depends(require_admin)])
    async def update_config(update: ConfigUpdate) -> PublicConfig:
        return config_store.update(update)

    @app.get("/api/tools")
    async def get_tools() -> list[dict[str, Any]]:
        return available_tools()

    @app.get("/api/debug/audio-captures", dependencies=[Depends(require_admin)])
    async def list_audio_captures() -> list[dict[str, int | str]]:
        return audio_capture.list_captures()

    @app.get("/api/debug/audio-captures/{capture_name}", dependencies=[Depends(require_admin)])
    async def get_audio_capture(capture_name: str) -> FileResponse:
        capture = audio_capture.resolve_capture(capture_name)
        if capture is None:
            raise HTTPException(status_code=404, detail="audio capture not found")
        return FileResponse(capture, media_type="audio/mpeg", filename=capture.name)

    @app.post("/api/test/decision")
    async def test_decision(request: DecisionRequest) -> DecisionResponse:
        tr_id = f"tr_{int(time.time())}_{uuid4().hex[:6]}"
        GLOBAL_SESSION_STORE.add_log(
            trace_id=tr_id,
            module="INPUT",
            level="INFO",
            message=f"[纯文字 HTTP 测试模式] 用户提问: \"{request.question}\"",
        )
        active_session = GLOBAL_SESSION_STORE.get_active_session()
        trace_handler = SessionTraceLogHandler(
            tr_id,
            session_id=active_session.session_id if active_session else None,
        )
        trace_logger = logging.getLogger("tuco_ai_backend")
        trace_logger.addHandler(trace_handler)

        def record_llm_error(message: str) -> None:
            GLOBAL_SESSION_STORE.add_log(
                trace_id=tr_id,
                module="LLM",
                level="ERROR",
                message=f"[{tr_id}] [LLM-ERROR] {message}",
                session_id=active_session.session_id if active_session else None,
            )

        try:
            try:
                decision = await app.state.llm_service.decide(
                    request,
                    history=GLOBAL_SESSION_STORE.get_conversation_history(
                        active_session.session_id if active_session else None
                    ),
                    trace_id=tr_id,
                )
            except TypeError:
                try:
                    decision = await app.state.llm_service.decide(request, trace_id=tr_id)
                except TypeError:
                    try:
                        decision = await app.state.llm_service.decide(
                            request,
                            history=GLOBAL_SESSION_STORE.get_conversation_history(
                                active_session.session_id if active_session else None
                            ),
                        )
                    except TypeError:
                        decision = await app.state.llm_service.decide(request)
            if active_session and decision.assistant_text:
                GLOBAL_SESSION_STORE.add_conversation_turn(
                    request.question,
                    decision.assistant_text,
                    session_id=active_session.session_id,
                )
            return decision
        except LlmConfigurationError as exc:
            record_llm_error(f"configuration rejected: {exc}")
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (LlmProtocolError, ValidationError) as exc:
            record_llm_error(f"protocol rejected: {exc}")
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            detail = f"LLM provider returned HTTP {exc.response.status_code}"
            record_llm_error(detail)
            raise HTTPException(status_code=502, detail=detail) from exc
        except httpx.HTTPError as exc:
            record_llm_error(f"provider request failed: {type(exc).__name__}")
            raise HTTPException(status_code=502, detail="LLM provider request failed") from exc
        finally:
            trace_logger.removeHandler(trace_handler)
            trace_handler.close()

    @app.get("/api/test/sessions")
    async def list_sessions() -> list[dict[str, Any]]:
        return GLOBAL_SESSION_STORE.list_sessions()

    @app.get("/api/test/sessions/{session_id}")
    async def get_session_detail(session_id: str) -> dict[str, Any]:
        detail = GLOBAL_SESSION_STORE.get_session_detail(session_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return detail

    @app.post("/api/test/sessions/start")
    async def start_session(body: StartSessionRequest) -> dict[str, Any]:
        session = GLOBAL_SESSION_STORE.create_session(body.level_id, body.level_title)
        return session.to_summary()

    @app.post("/api/test/sessions/end")
    async def end_session() -> dict[str, str]:
        GLOBAL_SESSION_STORE.close_session()
        return {"status": "ok"}

    @app.websocket("/ws/device")
    async def ws_device(websocket: WebSocket) -> None:
        def get_pipeline() -> VoicePipeline | None:
            return voice_pipeline or _build_voice_pipeline(
                config_store, app.state.llm_service, audio_capture
            )

        await device_websocket(
            websocket,
            get_pipeline,
            expected_device_token=config_store.device_token(),
            max_audio_bytes=config_store.max_audio_bytes,
            pipeline_timeout_seconds=config_store.pipeline_timeout_seconds,
            audio_capture=audio_capture,
        )

    if FRONTEND_DIR.exists():
        app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

        @app.get("/", include_in_schema=False)
        async def frontend() -> FileResponse:
            return FileResponse(FRONTEND_DIR / "index.html")

    return app


def _build_voice_pipeline(
    config: RuntimeConfigStore,
    llm_service: Any,
    audio_capture: AudioCaptureStore,
) -> VoicePipeline | None:
    volc_key = config.volc_api_key()
    if not volc_key or not config.api_key():
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
        audio_capture=audio_capture,
    )


app = create_app()


def run() -> None:
    uvicorn.run("tuco_ai_backend.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    run()
