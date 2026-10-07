"""Configuração central — lida do .env via pydantic-settings."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    # --- Chaves ---
    anthropic_api_key: str = ""
    deepgram_api_key: str = ""
    elevenlabs_api_key: str = ""

    # --- STT (Deepgram) ---
    deepgram_model: str = "nova-3"
    deepgram_language: str = "pt-BR"
    # Silêncio (ms) para o Deepgram marcar speech_final. Menor = mais rápido, mais risco de cortar o usuário.
    deepgram_endpointing_ms: int = 300
    # Fallback baseado em lacuna entre palavras (imune a ruído). Mínimo recomendado: 1000.
    deepgram_utterance_end_ms: int = 1000

    # --- LLM (Claude) ---
    llm_model: str = "claude-haiku-4-5"
    llm_max_tokens: int = 300
    llm_temperature: float = 0.4
    llm_max_tool_rounds: int = 4

    # --- TTS (ElevenLabs) ---
    elevenlabs_voice_id: str = "EXAVITQu4vr4xnSDxMaL"
    elevenlabs_model: str = "eleven_flash_v2_5"
    # PCM cru = o navegador toca sem decodificar (menos latência que MP3).
    tts_sample_rate: int = 24000

    # --- Barge-in (interromper o agente falando por cima) ---
    # True = full-duplex (recomendado com fone). False = half-duplex: ignora o mic enquanto o agente fala
    # (útil em caixa de som sem bom cancelamento de eco, para o agente não "ouvir a si mesmo").
    barge_in: bool = True
    barge_in_min_chars: int = 3

    # --- Áudio de entrada (o navegador já envia assim) ---
    input_sample_rate: int = 16000

    # --- App ---
    clinic_name: str = "Clínica Vitalis"
    timezone: str = "America/Sao_Paulo"
    db_path: Path = ROOT / "data" / "clinica.db"

    def missing_keys(self) -> list[str]:
        return [k for k in ("anthropic_api_key", "deepgram_api_key", "elevenlabs_api_key") if not getattr(self, k)]


@lru_cache
def get_settings() -> Settings:
    return Settings()
