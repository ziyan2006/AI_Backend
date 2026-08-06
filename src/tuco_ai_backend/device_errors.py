from __future__ import annotations

from dataclasses import dataclass

import httpx
from pydantic import BaseModel, ValidationError

from tuco_ai_backend.providers.openai_compatible import (
    LlmConfigurationError,
    LlmProtocolError,
)


class DeviceError(BaseModel):
    code: str
    stage: str
    retryable: bool
    message: str


class DeviceErrorResponse(BaseModel):
    error: DeviceError
    trace_id: str


@dataclass(frozen=True)
class ClassifiedDeviceError:
    status_code: int
    error: DeviceError

    def response(self, trace_id: str) -> DeviceErrorResponse:
        return DeviceErrorResponse(error=self.error, trace_id=trace_id)


def request_invalid_error() -> ClassifiedDeviceError:
    return ClassifiedDeviceError(
        status_code=422,
        error=DeviceError(
            code="REQUEST_INVALID",
            stage="request_validation",
            retryable=False,
            message="设备请求格式不正确",
        ),
    )


def classify_device_error(exc: Exception) -> ClassifiedDeviceError:
    if isinstance(exc, LlmConfigurationError):
        return ClassifiedDeviceError(
            status_code=503,
            error=DeviceError(
                code="LLM_UNCONFIGURED",
                stage="llm_configuration",
                retryable=False,
                message="模型服务尚未配置",
            ),
        )
    if isinstance(exc, httpx.TimeoutException):
        return ClassifiedDeviceError(
            status_code=504,
            error=DeviceError(
                code="LLM_TIMEOUT",
                stage="llm_provider",
                retryable=True,
                message="模型服务响应超时",
            ),
        )
    if isinstance(exc, (LlmProtocolError, ValidationError)):
        return ClassifiedDeviceError(
            status_code=502,
            error=DeviceError(
                code="LLM_PROTOCOL_ERROR",
                stage="llm_protocol",
                retryable=False,
                message="模型响应格式异常",
            ),
        )
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        retryable = status_code == 429 or status_code >= 500
        return ClassifiedDeviceError(
            status_code=502,
            error=DeviceError(
                code="LLM_UPSTREAM_ERROR",
                stage="llm_provider",
                retryable=retryable,
                message=f"模型服务返回异常状态（HTTP {status_code}）",
            ),
        )
    if isinstance(exc, httpx.HTTPError):
        return ClassifiedDeviceError(
            status_code=502,
            error=DeviceError(
                code="LLM_UPSTREAM_ERROR",
                stage="llm_provider",
                retryable=True,
                message="模型服务连接失败",
            ),
        )
    return ClassifiedDeviceError(
        status_code=500,
        error=DeviceError(
            code="INTERNAL_ERROR",
            stage="internal",
            retryable=False,
            message="助教服务内部异常",
        ),
    )
