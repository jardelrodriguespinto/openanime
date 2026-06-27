import os
from dataclasses import dataclass

ENVIRONMENT = os.getenv("ENV", "development")

@dataclass
class ModelConfig:
    orchestrator: str
    chat: str
    search: str
    profile: str

def load_model_config() -> ModelConfig:
    return ModelConfig(
        orchestrator=os.getenv("MODEL_ORCHESTRATOR", "meta-llama/llama-3-8b-instruct"),
        chat=os.getenv("MODEL_CHAT", "anthropic/claude-sonnet-4-6"),
        search=os.getenv("MODEL_SEARCH", "meta-llama/llama-3-70b-instruct"),
        profile=os.getenv("MODEL_PROFILE", "meta-llama/llama-3-8b-instruct"),
    )

models = load_model_config()