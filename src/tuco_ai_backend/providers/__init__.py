"""External AI provider adapters."""

from tuco_ai_backend.providers.circuit_coach_v2 import CircuitCoachV2Client
from tuco_ai_backend.providers.openai_compatible import OpenAICompatibleClient

__all__ = ["CircuitCoachV2Client", "OpenAICompatibleClient"]

