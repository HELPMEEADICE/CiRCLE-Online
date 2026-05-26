import tomli
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional
import os


BASE_DIR = Path(__file__).parent.parent
CONFIG_DIR = BASE_DIR / "config"
CHARACTERS_DIR = BASE_DIR / "characters"


class ServerConfig(BaseSettings):
    host: str = "0.0.0.0"
    base_port: int = 8081
    num_ports: int = 5
    management_port: int = 8080


class LLMConfig(BaseSettings):
    provider: str = "openai"
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    assistant_model: str = ""
    temperature: float = 0.8
    max_tokens: int = 1024


class AutoDialogueConfig(BaseSettings):
    enabled: bool = False
    chain_length: int = 5
    cooldown_ms: int = 30000
    trigger_probability: float = 0.5
    initiation_probability: float = 0.3
    initiation_interval_ms: int = 600000


class ContextCompressionConfig(BaseSettings):
    enabled: bool = True
    target_tokens: int = 2048
    reserve_recent: int = 4
    model: str = ""


class OrchestratorConfig(BaseSettings):
    enabled: bool = True
    reply_delay_ms: int = 1000
    max_context_messages: int = 20
    max_context_tokens: int = 4096
    group_reply_probability: float = 0.3
    prompt_prefix: str = ""
    prompt_suffix: str = ""
    time_awareness: bool = False
    auto_dialogue: AutoDialogueConfig = Field(default_factory=AutoDialogueConfig)
    context_compression: ContextCompressionConfig = Field(default_factory=ContextCompressionConfig)


class LoggingConfig(BaseSettings):
    level: str = "INFO"
    file: str = "logs/circle-online.log"


class AppConfig(BaseSettings):
    server: ServerConfig = Field(default_factory=ServerConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    orchestrator: OrchestratorConfig = Field(default_factory=OrchestratorConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


def load_toml_config(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomli.load(f)


def load_config() -> AppConfig:
    settings_path = CONFIG_DIR / "settings.toml"
    raw = load_toml_config(settings_path)

    env_api_key = os.getenv("LLM_API_KEY", "")
    env_base_url = os.getenv("LLM_BASE_URL", "")

    server_cfg = ServerConfig(**raw.get("server", {}))

    llm_raw = raw.get("llm", {})
    if env_api_key:
        llm_raw["api_key"] = env_api_key
    if env_base_url:
        llm_raw["base_url"] = env_base_url
    llm_cfg = LLMConfig(**llm_raw)

    orchestrator_cfg = OrchestratorConfig(**raw.get("orchestrator", {}))
    logging_cfg = LoggingConfig(**raw.get("logging", {}))

    return AppConfig(
        server=server_cfg,
        llm=llm_cfg,
        orchestrator=orchestrator_cfg,
        logging=logging_cfg,
    )


def load_port_assignments() -> dict[int, dict]:
    ports_path = CONFIG_DIR / "ports.toml"
    raw = load_toml_config(ports_path)
    result = {}
    for port_str, info in raw.get("ports", {}).items():
        port = int(port_str)
        result[port] = info
    return result


def save_port_assignments(assignments: dict[int, dict]):
    ports_path = CONFIG_DIR / "ports.toml"
    lines = []
    for port in sorted(assignments.keys()):
        info = assignments[port]
        lines.append(f"[ports.{port}]")
        lines.append(f'name = "{info.get("name", "")}"')
        lines.append(f'character = "{info.get("character", "")}"')
        lines.append(f'token = "{info.get("token", "")}"')
        lines.append("")
    ports_path.write_text("\n".join(lines), encoding="utf-8")


def save_config(app_config: AppConfig):
    settings_path = CONFIG_DIR / "settings.toml"
    lines = []

    lines.append("[server]")
    lines.append(f'host = "{app_config.server.host}"')
    lines.append(f"base_port = {app_config.server.base_port}")
    lines.append(f"num_ports = {app_config.server.num_ports}")
    lines.append(f"management_port = {app_config.server.management_port}")
    lines.append("")

    lines.append("[llm]")
    lines.append(f'provider = "{app_config.llm.provider}"')
    lines.append(f'api_key = "{app_config.llm.api_key}"')
    lines.append(f'base_url = "{app_config.llm.base_url}"')
    lines.append(f'model = "{app_config.llm.model}"')
    lines.append(f'assistant_model = "{app_config.llm.assistant_model}"')
    lines.append(f"temperature = {app_config.llm.temperature}")
    lines.append(f"max_tokens = {app_config.llm.max_tokens}")
    lines.append("")

    lines.append("[orchestrator]")
    lines.append(f"enabled = {'true' if app_config.orchestrator.enabled else 'false'}")
    lines.append(f"reply_delay_ms = {app_config.orchestrator.reply_delay_ms}")
    lines.append(f"max_context_messages = {app_config.orchestrator.max_context_messages}")
    lines.append(f"max_context_tokens = {app_config.orchestrator.max_context_tokens}")
    lines.append(f"group_reply_probability = {app_config.orchestrator.group_reply_probability}")
    lines.append(f'prompt_prefix = """{app_config.orchestrator.prompt_prefix}"""')
    lines.append(f'prompt_suffix = """{app_config.orchestrator.prompt_suffix}"""')
    lines.append(f"time_awareness = {'true' if app_config.orchestrator.time_awareness else 'false'}")
    lines.append("")

    lines.append("[orchestrator.context_compression]")
    lines.append(f"enabled = {'true' if app_config.orchestrator.context_compression.enabled else 'false'}")
    lines.append(f"target_tokens = {app_config.orchestrator.context_compression.target_tokens}")
    lines.append(f"reserve_recent = {app_config.orchestrator.context_compression.reserve_recent}")
    lines.append(f'model = "{app_config.orchestrator.context_compression.model}"')
    lines.append("")

    lines.append("[orchestrator.auto_dialogue]")
    lines.append(f"enabled = {'true' if app_config.orchestrator.auto_dialogue.enabled else 'false'}")
    lines.append(f"chain_length = {app_config.orchestrator.auto_dialogue.chain_length}")
    lines.append(f"cooldown_ms = {app_config.orchestrator.auto_dialogue.cooldown_ms}")
    lines.append(f"trigger_probability = {app_config.orchestrator.auto_dialogue.trigger_probability}")
    lines.append(f"initiation_probability = {app_config.orchestrator.auto_dialogue.initiation_probability}")
    lines.append(f"initiation_interval_ms = {app_config.orchestrator.auto_dialogue.initiation_interval_ms}")
    lines.append("")

    lines.append("[logging]")
    lines.append(f'level = "{app_config.logging.level}"')
    lines.append(f'file = "{app_config.logging.file}"')

    settings_path.write_text("\n".join(lines), encoding="utf-8")


config = load_config()
