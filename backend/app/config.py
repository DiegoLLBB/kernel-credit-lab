"""Application settings, loaded from environment variables (see .env.example)."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://kernel:kernel@localhost:5432/kernel"
    redis_url: str = "redis://localhost:6379/0"

    # Meta Model API (hosted). Verify base URL and model IDs against the official model list.
    model_api_key: str = Field(default="", repr=False)
    meta_model_api_base_url: str = ""
    default_model: str = ""
    deep_research_model: str = ""
    vision_segmentation_model: str = ""

    # Open-weight models via Baseten (OpenAI-compatible).
    baseten_api_key: str = Field(default="", repr=False)
    baseten_base_url: str = "https://inference.baseten.co/v1"
    open_model: str = "meta-models/muse-spark-open-1026"

    # Run guardrails
    llm_timeout_seconds: float = 120.0
    llm_max_retries: int = 3
    max_run_cost_usd: float = 2.0
    # USD per 1M tokens, as JSON: {"model-id": [input_price, output_price]}. Used for cost tracking.
    model_pricing_json: str = "{}"

    # SEC EDGAR requires a descriptive User-Agent with contact info.
    sec_user_agent: str = "KernelCreditLab research@example.com"
    fred_api_key: str = Field(default="", repr=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
