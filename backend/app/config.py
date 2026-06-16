"""Runtime configuration loaded from .env."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str
    supabase_jwt_secret: str

    redis_url: str = "redis://localhost:6379"
    cors_origins: str = "http://localhost:5173"

    # Job delivery backend for admin/taxon jobs (the `jobs` table).
    #   "postgres" → enqueue = just insert the row; a Postgres-polling worker
    #                (app.job_worker) claims it. No Redis required.
    #   "redis"    → enqueue via arq into Redis; app.worker (arq) runs it.
    # The job *records* always live in the `jobs` table either way — Redis only
    # ever acted as the delivery queue, so flipping this needs no schema change.
    job_backend: str = "postgres"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
