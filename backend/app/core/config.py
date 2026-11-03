from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND = Path(__file__).resolve().parents[2]
PRODUCT = BACKEND.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND / ".env", extra="ignore")
    data_dir: Path = PRODUCT / "data"
    owner_id: str = "local-researcher"
    model_api_key: SecretStr = SecretStr("")
    model_provider: Literal["deepseek", "bailian"] = "deepseek"
    model_base_url: str = "https://api.deepseek.com"
    model_name: str = "deepseek-flash"
    embedding_provider: Literal["local", "bailian"] = "local"
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_dimensions: int = 512
    model_timeout: float = Field(default=90, gt=0, le=120)
    model_max_output: int = Field(default=8192, ge=1, le=8192)
    model_retries: int = Field(default=3, ge=0, le=3)
    monthly_budget_yuan: float = 200
    report_budget_yuan: float = 10
    max_file_bytes: int = Field(default=100 * 1024 * 1024, ge=1, le=100 * 1024 * 1024)
    lease_seconds: float = 600

    @field_validator("model_base_url")
    @classmethod
    def secure_url(cls, value):
        from urllib.parse import urlparse

        p = urlparse(value)
        if p.scheme != "https" or not p.hostname or p.username or p.password:
            raise ValueError("模型地址须为不含凭据的 HTTPS URL")
        return value.rstrip("/")

    @field_validator("monthly_budget_yuan", "report_budget_yuan", "model_timeout", "lease_seconds")
    @classmethod
    def positive(cls, value):
        if value <= 0:
            raise ValueError("必须大于零")
        return value


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.message, self.status = code, message, status
        super().__init__(code)
