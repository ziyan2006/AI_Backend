from __future__ import annotations

from dataclasses import dataclass

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from tuco_ai_backend.models import ConfigUpdate, PublicConfig


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="TUCO_",
        extra="ignore",
    )

    llm_base_url: str = "https://distribute.wegoo.site/v1"
    llm_model: str = "gpt-5.6-terra"
    llm_api_key: SecretStr | None = None
    llm_timeout_seconds: float = 45.0
    volc_api_key: SecretStr | None = None
    volc_asr_resource_id: str = "volc.bigasr.sauc.duration"
    volc_tts_resource_id: str = "seed-tts-2.0"
    volc_tts_voice_type: str = "zh_female_vv_uranus_bigtts"
    device_token: SecretStr | None = None
    admin_token: SecretStr | None = None
    max_audio_bytes: int = Field(default=512 * 1024, ge=3200, le=2 * 1024 * 1024)
    pipeline_timeout_seconds: float = Field(default=120.0, gt=0, le=300)


@dataclass
class RuntimeConfigStore:
    settings: Settings

    def __post_init__(self) -> None:
        self._llm_base_url = self.settings.llm_base_url.rstrip("/")
        self._llm_model = self.settings.llm_model
        self._llm_api_key = (
            self.settings.llm_api_key.get_secret_value().strip()
            if self.settings.llm_api_key
            else None
        )
        self._llm_timeout_seconds = self.settings.llm_timeout_seconds
        self._volc_api_key = (
            self.settings.volc_api_key.get_secret_value().strip()
            if self.settings.volc_api_key
            else None
        )
        self._volc_asr_resource_id = self.settings.volc_asr_resource_id
        self._volc_tts_resource_id = self.settings.volc_tts_resource_id
        self._volc_tts_voice_type = self.settings.volc_tts_voice_type
        self._device_token = (
            self.settings.device_token.get_secret_value().strip()
            if self.settings.device_token
            else None
        )
        self._admin_token = (
            self.settings.admin_token.get_secret_value().strip()
            if self.settings.admin_token
            else None
        )
        self._max_audio_bytes = self.settings.max_audio_bytes
        self._pipeline_timeout_seconds = self.settings.pipeline_timeout_seconds

    def public_config(self) -> PublicConfig:
        return PublicConfig(
            llm_base_url=self._llm_base_url,
            llm_model=self._llm_model,
            llm_api_key_configured=bool(self._llm_api_key),
            llm_timeout_seconds=self._llm_timeout_seconds,
            volc_api_key_configured=bool(self._volc_api_key),
            volc_asr_resource_id=self._volc_asr_resource_id,
            volc_tts_resource_id=self._volc_tts_resource_id,
            volc_tts_voice_type=self._volc_tts_voice_type,
        )

    def update(self, update: ConfigUpdate) -> PublicConfig:
        if update.llm_base_url is not None:
            self._llm_base_url = update.llm_base_url.rstrip("/")
        if update.llm_model is not None:
            self._llm_model = update.llm_model
        if update.llm_api_key is not None:
            self._llm_api_key = update.llm_api_key.strip() or None
        if update.llm_timeout_seconds is not None:
            self._llm_timeout_seconds = update.llm_timeout_seconds
        if update.volc_api_key is not None:
            self._volc_api_key = update.volc_api_key.strip() or None
        if update.volc_asr_resource_id is not None:
            self._volc_asr_resource_id = update.volc_asr_resource_id
        if update.volc_tts_resource_id is not None:
            self._volc_tts_resource_id = update.volc_tts_resource_id
        if update.volc_tts_voice_type is not None:
            self._volc_tts_voice_type = update.volc_tts_voice_type
        return self.public_config()

    def api_key(self) -> str | None:
        return self._llm_api_key

    def volc_api_key(self) -> str | None:
        return self._volc_api_key

    @property
    def volc_asr_resource_id(self) -> str:
        return self._volc_asr_resource_id

    @property
    def volc_tts_resource_id(self) -> str:
        return self._volc_tts_resource_id

    @property
    def volc_tts_voice_type(self) -> str:
        return self._volc_tts_voice_type

    @property
    def base_url(self) -> str:
        return self._llm_base_url

    @property
    def model(self) -> str:
        return self._llm_model

    @property
    def timeout_seconds(self) -> float:
        return self._llm_timeout_seconds

    def device_token(self) -> str | None:
        return self._device_token

    def admin_token(self) -> str | None:
        return self._admin_token

    @property
    def max_audio_bytes(self) -> int:
        return self._max_audio_bytes

    @property
    def pipeline_timeout_seconds(self) -> float:
        return self._pipeline_timeout_seconds
