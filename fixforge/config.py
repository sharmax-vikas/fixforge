from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str | None = None
    openai_model: str = "gpt-5.3-codex"
    # Maximum reasoning setting supported by the default coding model.
    openai_reasoning_effort: str = "xhigh"
    github_token: str | None = None
    # A writable fork used only after final PR approval. Leave empty to analyze
    # the issue repository without granting FixForge permission to push.
    fixforge_fork_repository: str | None = None
    workspace_root: Path = Path(".fixforge")
    # Keep a local interactive run responsive. A repository can override this
    # through TEST_TIMEOUT_SECONDS for slower, full-suite verification.
    test_timeout_seconds: int = 180
    max_candidates: int = 3


settings = Settings()
