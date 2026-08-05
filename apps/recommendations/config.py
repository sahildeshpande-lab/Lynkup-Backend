from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RecommendationSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    semantic_scholar_api_key: str | None = Field(default=None, alias="SEMANTIC_SCHOLAR_API_KEY")
    semantic_scholar_base_url: str = Field(
        default="https://api.semanticscholar.org/graph/v1",
        alias="SEMANTIC_SCHOLAR_BASE_URL",
    )
    min_keyword_score: float = Field(default=0.45, alias="MIN_KEYWORD_SCORE", ge=0.0, le=1.0)
    hf_home: str | None = Field(
        default=None,
        alias="HF_HOME",
        description=(
            "Optional Hugging Face cache directory shared across deployments. "
            "When set, SentenceTransformer/KeyBERT reuse cached weights instead "
            "of downloading on every startup."
        ),
    )


settings = RecommendationSettings()
