import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from pydantic import BaseModel
from fastapi import FastAPI, WebSocket, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from backend.config import config, load_config, save_config, load_port_assignments, save_port_assignments
from backend.models import CharacterAssignment, OrchestratorState
from backend.websocket_server import MultiPortWebSocketServer
from backend.napcat_handler import NapCatMessageHandler
from backend.orchestrator import orchestrator
from backend.character_manager import character_manager
from backend.utils import setup_logging, get_logger

logger = get_logger("main")

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

ws_server: MultiPortWebSocketServer = None
msg_handler: NapCatMessageHandler = None


async def on_ws_status_change(event: str, port: int, qq_id: str = None):
    if event == "identified" and qq_id:
        logger.info(f"Port {port} identified as QQ {qq_id}")
    elif event in ("connected", "disconnected"):
        logger.info(f"Port {port} {event}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global ws_server, msg_handler

    setup_logging(config.logging.level)

    # Load port configurations
    port_configs = load_port_assignments()
    
    ws_server = MultiPortWebSocketServer(config.server.base_port, config.server.num_ports, config.server.host)
    msg_handler = NapCatMessageHandler()

    msg_handler.on_group_message = orchestrator.handle_group_message
    msg_handler.on_private_message = orchestrator.handle_private_message

    ws_server.set_status_callback(on_ws_status_change)
    ws_server.add_event_handler(msg_handler.handle_event)

    # Set port configurations for token validation
    ws_server.set_port_configs(port_configs)

    orchestrator.set_ws_server(ws_server)

    # Start WebSocket servers on all configured ports
    await ws_server.start_servers()

    logger.info("CiRCLE Online started")
    logger.info(f"WebSocket ports: {config.server.base_port}-{config.server.base_port + config.server.num_ports - 1}")

    yield

    logger.info("Shutting down...")
    await ws_server.stop_servers()


app = FastAPI(title="CiRCLE Online", version="1.0.0", lifespan=lifespan)

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    index_file = FRONTEND_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>CiRCLE Online</h1><p>Frontend not found</p>")


@app.websocket("/ws/napcat/{port}")
async def napcat_websocket(websocket: WebSocket, port: int):
    if port < config.server.base_port or port >= config.server.base_port + config.server.num_ports:
        await websocket.close(code=4001, reason="Invalid port")
        return

    await ws_server.handle_connection(port, websocket)


@app.get("/api/status")
async def get_status():
    port_configs = load_port_assignments()
    ports_info = ws_server.get_all_port_info(port_configs)
    return {
        "enabled": orchestrator.enabled,
        "ports": {str(p): info.model_dump() for p, info in ports_info.items()},
        "characters": [c.model_dump() for c in character_manager.get_all_characters()],
        "assignments": {str(p): a for p, a in orchestrator.get_all_assignments().items()},
        "portConfigs": {str(p): cfg for p, cfg in port_configs.items()},
    }


@app.post("/api/orchestrator/toggle")
async def toggle_orchestrator():
    orchestrator.enabled = not orchestrator.enabled
    return {"enabled": orchestrator.enabled}


@app.post("/api/orchestrator/enable")
async def enable_orchestrator():
    orchestrator.enabled = True
    return {"enabled": True}


@app.post("/api/orchestrator/disable")
async def disable_orchestrator():
    orchestrator.enabled = False
    return {"enabled": False}


@app.post("/api/assign")
async def assign_character(assignment: CharacterAssignment):
    port = assignment.port
    char_name = assignment.character_name

    if port < config.server.base_port or port >= config.server.base_port + config.server.num_ports:
        raise HTTPException(400, "Invalid port number")

    if char_name and not character_manager.get_character(char_name):
        raise HTTPException(404, f"Character not found: {char_name}")

    if char_name:
        orchestrator.set_assignment(port, char_name)
    else:
        orchestrator.remove_assignment(port)

    return {"success": True, "port": port, "character": char_name}


class PortTokenUpdate(BaseModel):
    port: int
    token: str


@app.post("/api/port-token")
async def update_port_token(update: PortTokenUpdate):
    port = update.port
    token = update.token

    if port < config.server.base_port or port >= config.server.base_port + config.server.num_ports:
        raise HTTPException(400, "Invalid port number")

    # Load current port configurations
    port_configs = load_port_assignments()
    
    # Update token for the specified port
    if port not in port_configs:
        port_configs[port] = {"name": f"Slot {port - config.server.base_port + 1}", "character": "", "token": ""}
    
    port_configs[port]["token"] = token
    
    # Save updated configurations
    save_port_assignments(port_configs)
    
    # Update WebSocket server's port configurations
    ws_server.set_port_configs(port_configs)
    
    return {"success": True, "port": port, "message": "Token updated"}


@app.get("/api/characters")
async def get_characters():
    return [c.model_dump() for c in character_manager.get_all_characters()]


@app.get("/api/characters/{name}")
async def get_character(name: str):
    char = character_manager.get_character(name)
    if not char:
        raise HTTPException(404, "Character not found")
    return char.model_dump()


@app.get("/api/messages/recent")
async def get_recent_messages(count: int = 20):
    return [m.model_dump() for m in msg_handler.get_recent_messages(count)]


@app.post("/api/send")
async def send_message(port: int, group_id: str = None, user_id: str = None, message: str = ""):
    if not message:
        raise HTTPException(400, "Message cannot be empty")

    conn = ws_server.get_connection(port)
    if not conn:
        raise HTTPException(404, f"No connection on port {port}")

    from backend.utils import build_text_message
    msg_segments = build_text_message(message)

    try:
        if group_id:
            await conn.send_message(group_id=group_id, message=msg_segments)
        elif user_id:
            await conn.send_message(user_id=user_id, message=msg_segments)
        else:
            raise HTTPException(400, "Either group_id or user_id required")
        return {"success": True}
    except Exception as e:
        raise HTTPException(500, f"Failed to send message: {e}")


@app.post("/api/reload-characters")
async def reload_characters():
    character_manager.reload()
    return {"success": True, "count": len(character_manager.get_all_characters())}


@app.get("/api/config")
async def get_config():
    cfg = load_config()
    return {
        "server": {
            "host": cfg.server.host,
            "base_port": cfg.server.base_port,
            "num_ports": cfg.server.num_ports,
            "management_port": cfg.server.management_port,
        },
        "llm": {
            "provider": cfg.llm.provider,
            "api_key": cfg.llm.api_key,
            "base_url": cfg.llm.base_url,
            "model": cfg.llm.model,
            "assistant_model": cfg.llm.assistant_model,
            "temperature": cfg.llm.temperature,
            "max_tokens": cfg.llm.max_tokens,
        },
        "orchestrator": {
            "enabled": cfg.orchestrator.enabled,
            "reply_delay_ms": cfg.orchestrator.reply_delay_ms,
            "max_context_messages": cfg.orchestrator.max_context_messages,
            "max_context_tokens": cfg.orchestrator.max_context_tokens,
            "group_reply_probability": cfg.orchestrator.group_reply_probability,
            "prompt_prefix": cfg.orchestrator.prompt_prefix,
            "prompt_suffix": cfg.orchestrator.prompt_suffix,
            "time_awareness": cfg.orchestrator.time_awareness,
        },
        "logging": {
            "level": cfg.logging.level,
            "file": cfg.logging.file,
        },
    }


class ServerConfigUpdate(BaseModel):
    host: str = None
    base_port: int = None
    num_ports: int = None
    management_port: int = None


class LLMConfigUpdate(BaseModel):
    provider: str = None
    api_key: str = None
    base_url: str = None
    model: str = None
    assistant_model: str = None
    temperature: float = None
    max_tokens: int = None


class OrchestratorConfigUpdate(BaseModel):
    enabled: bool = None
    reply_delay_ms: int = None
    max_context_messages: int = None
    max_context_tokens: int = None
    group_reply_probability: float = None
    prompt_prefix: str = None
    prompt_suffix: str = None
    time_awareness: bool = None


class ConfigUpdate(BaseModel):
    server: ServerConfigUpdate = None
    llm: LLMConfigUpdate = None
    orchestrator: OrchestratorConfigUpdate = None


@app.post("/api/config")
async def update_config(update: ConfigUpdate):
    global config
    current = load_config()

    if update.server:
        if update.server.host is not None:
            current.server.host = update.server.host
        if update.server.base_port is not None:
            current.server.base_port = update.server.base_port
        if update.server.num_ports is not None:
            current.server.num_ports = update.server.num_ports
        if update.server.management_port is not None:
            current.server.management_port = update.server.management_port

    if update.llm:
        if update.llm.provider is not None:
            current.llm.provider = update.llm.provider
        if update.llm.api_key is not None:
            current.llm.api_key = update.llm.api_key
        if update.llm.base_url is not None:
            current.llm.base_url = update.llm.base_url
        if update.llm.model is not None:
            current.llm.model = update.llm.model
        if update.llm.assistant_model is not None:
            current.llm.assistant_model = update.llm.assistant_model
        if update.llm.temperature is not None:
            current.llm.temperature = update.llm.temperature
        if update.llm.max_tokens is not None:
            current.llm.max_tokens = update.llm.max_tokens

    if update.orchestrator:
        if update.orchestrator.enabled is not None:
            current.orchestrator.enabled = update.orchestrator.enabled
        if update.orchestrator.reply_delay_ms is not None:
            current.orchestrator.reply_delay_ms = update.orchestrator.reply_delay_ms
        if update.orchestrator.max_context_messages is not None:
            current.orchestrator.max_context_messages = update.orchestrator.max_context_messages
        if update.orchestrator.max_context_tokens is not None:
            current.orchestrator.max_context_tokens = update.orchestrator.max_context_tokens
        if update.orchestrator.group_reply_probability is not None:
            current.orchestrator.group_reply_probability = update.orchestrator.group_reply_probability
        if update.orchestrator.prompt_prefix is not None:
            current.orchestrator.prompt_prefix = update.orchestrator.prompt_prefix
        if update.orchestrator.prompt_suffix is not None:
            current.orchestrator.prompt_suffix = update.orchestrator.prompt_suffix
        if update.orchestrator.time_awareness is not None:
            current.orchestrator.time_awareness = update.orchestrator.time_awareness

    save_config(current)
    config = current

    return {"success": True, "message": "配置已保存，重启服务后生效"}


class AutoDialogueConfigUpdate(BaseModel):
    enabled: bool = None
    chain_length: int = None
    cooldown_ms: int = None
    trigger_probability: float = None
    initiation_probability: float = None
    initiation_interval_ms: int = None


@app.get("/api/orchestrator/auto-dialogue/config")
async def get_auto_dialogue_config():
    cfg = load_config()
    return {
        "enabled": cfg.orchestrator.auto_dialogue.enabled,
        "chain_length": cfg.orchestrator.auto_dialogue.chain_length,
        "cooldown_ms": cfg.orchestrator.auto_dialogue.cooldown_ms,
        "trigger_probability": cfg.orchestrator.auto_dialogue.trigger_probability,
        "initiation_probability": cfg.orchestrator.auto_dialogue.initiation_probability,
        "initiation_interval_ms": cfg.orchestrator.auto_dialogue.initiation_interval_ms,
    }


@app.post("/api/orchestrator/auto-dialogue/config")
async def update_auto_dialogue_config(update: AutoDialogueConfigUpdate):
    global config
    current = load_config()

    if update.enabled is not None:
        current.orchestrator.auto_dialogue.enabled = update.enabled
    if update.chain_length is not None:
        current.orchestrator.auto_dialogue.chain_length = update.chain_length
    if update.cooldown_ms is not None:
        current.orchestrator.auto_dialogue.cooldown_ms = update.cooldown_ms
    if update.trigger_probability is not None:
        current.orchestrator.auto_dialogue.trigger_probability = update.trigger_probability
    if update.initiation_probability is not None:
        current.orchestrator.auto_dialogue.initiation_probability = update.initiation_probability
    if update.initiation_interval_ms is not None:
        current.orchestrator.auto_dialogue.initiation_interval_ms = update.initiation_interval_ms

    save_config(current)
    config = current

    return {"success": True, "message": "自动对话配置已保存，重启服务后生效"}


@app.post("/api/orchestrator/auto-dialogue/toggle")
async def toggle_auto_dialogue():
    global config
    current = load_config()
    current.orchestrator.auto_dialogue.enabled = not current.orchestrator.auto_dialogue.enabled
    save_config(current)
    config = current
    return {"enabled": config.orchestrator.auto_dialogue.enabled}


def run():
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host=config.server.host,
        port=config.server.management_port,
        reload=False,
        log_level=config.logging.level.lower(),
    )
