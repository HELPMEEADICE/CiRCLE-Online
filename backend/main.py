import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from pydantic import BaseModel
from fastapi import FastAPI, WebSocket, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from backend.config import config, load_config, save_config, load_port_assignments, save_port_assignments, init_config
from backend.models import CharacterAssignment, OrchestratorState
from backend.websocket_server import MultiPortWebSocketServer
from backend.napcat_handler import NapCatMessageHandler
from backend.orchestrator import init_orchestrator
from backend.character_manager import init_character_manager
from backend.llm_client import init_llm_client
from backend.message_buffer import init_message_buffer
from backend.dispatcher import init_dispatcher
from backend.utils import setup_logging, get_logger
from backend.database import init_db
from backend.auth import create_token, verify_token, revoke_token
from fastapi.middleware.cors import CORSMiddleware

logger = get_logger("main")

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

ws_server: MultiPortWebSocketServer = None
msg_handler: NapCatMessageHandler = None
orchestrator = None
character_manager = None
llm_client = None
message_buffer = None
dispatcher = None


async def on_ws_status_change(event: str, port: int, qq_id: str = None):
    if event == "identified" and qq_id:
        logger.info(f"Port {port} identified as QQ {qq_id}")
        character_name = orchestrator.get_assignment(port)
        if character_name:
            orchestrator.register_bot_qq(qq_id, character_name)
    elif event in ("connected", "disconnected"):
        logger.info(f"Port {port} {event}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global ws_server, msg_handler, orchestrator, character_manager, llm_client, message_buffer, dispatcher

    # Initialize singletons in dependency order
    init_config()
    init_db()
    character_manager = init_character_manager()
    llm_client = init_llm_client()
    orchestrator = init_orchestrator()

    setup_logging(config.logging.level)

    # Initialize database for persistent chat history
    await orchestrator.init_db()

    # Load port configurations
    port_configs = load_port_assignments()

    ws_server = MultiPortWebSocketServer(config.server.base_port, config.server.num_ports, config.server.host)
    msg_handler = NapCatMessageHandler()

    # 初始化分配器
    dispatcher = init_dispatcher()
    # 设置可用角色
    available_characters = list(orchestrator._assignments.values())
    dispatcher.set_available_characters(available_characters)

    # 初始化消息缓冲区
    if config.orchestrator.buffer.enabled:
        message_buffer = init_message_buffer(
            window_ms=config.orchestrator.buffer.window_ms,
            max_size=config.orchestrator.buffer.max_size,
            on_flush=dispatcher.on_flush,
        )
        # 设置到 napcat_handler
        msg_handler.set_message_buffer(message_buffer)
        logger.info(f"Message buffer enabled: window={config.orchestrator.buffer.window_ms}ms, max_size={config.orchestrator.buffer.max_size}")
    else:
        # 如果缓冲区禁用，使用原有回调
        msg_handler.on_group_message = orchestrator.handle_group_message
        logger.info("Message buffer disabled, using direct message handling")

    msg_handler.on_private_message = orchestrator.handle_private_message
    msg_handler.on_any_message = orchestrator.note_message_activity

    ws_server.set_status_callback(on_ws_status_change)
    ws_server.add_event_handler(msg_handler.handle_event)

    # Set port configurations for token validation
    ws_server.set_port_configs(port_configs)

    orchestrator.set_ws_server(ws_server)

    # Start WebSocket servers for each port
    await ws_server.start_servers()

    logger.info("CiRCLE Online started")
    logger.info(f"WebSocket ports: {config.server.base_port}-{config.server.base_port + config.server.num_ports - 1}")
    logger.info(f"Dispatcher enabled: {config.orchestrator.dispatcher.enabled}")

    await orchestrator.start_initiation_task()

    yield

    logger.info("Shutting down...")
    # 清理消息缓冲区
    if message_buffer:
        await message_buffer.flush_all()
        logger.info("Message buffer flushed")
    
    await orchestrator.stop_initiation_task()
    await ws_server.stop_servers()
    from backend.database import db
    await db.close()


app = FastAPI(title="CiRCLE Online", version="1.0.0", lifespan=lifespan)

# CORS middleware - restrict to localhost and LAN for dashboard security
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://localhost:{config.server.management_port}",
        f"http://127.0.0.1:{config.server.management_port}",
    ],
    allow_origin_regex=r"https?://192\.168\.\d{1,3}\.\d{1,3}(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PUBLIC_PATHS = {"/", "/api/auth/login", "/api/auth/check"}

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith("/static"):
        return await call_next(request)
    if path.startswith("/ws/"):
        return await call_next(request)
    if path.startswith("/api/"):
        token = None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        if not token:
            token = request.query_params.get("access_token")
        if not verify_token(token):
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)


@app.post("/api/auth/login")
async def auth_login(request: Request):
    body = await request.json()
    password = body.get("password", "")
    current = load_config()
    token = create_token(password, current.chat.dashboard_password)
    if not token:
        raise HTTPException(401, "密码错误")
    return {"success": True, "token": token}


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    auth_header = request.headers.get("Authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else None
    revoke_token(token)
    return {"success": True}


@app.get("/api/auth/check")
async def auth_check(request: Request):
    auth_header = request.headers.get("Authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else None
    return {"authenticated": verify_token(token)}

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
            "api_key_set": bool(cfg.llm.api_key),
            "base_url": cfg.llm.base_url,
            "model": cfg.llm.model,
            "model_thinking": cfg.llm.model_thinking,
            "assistant_model": cfg.llm.assistant_model,
            "assistant_model_thinking": cfg.llm.assistant_model_thinking,
            "temperature": cfg.llm.temperature,
            "max_tokens": cfg.llm.max_tokens,
            "vision": {
                "enabled": cfg.llm.vision.enabled,
                "api_key_set": bool(cfg.llm.vision.api_key),
                "base_url": cfg.llm.vision.base_url,
                "model": cfg.llm.vision.model,
                "thinking": cfg.llm.vision.thinking,
            },
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
            "tools": {
                "emoji_reaction": cfg.orchestrator.tools.emoji_reaction,
                "ban": cfg.orchestrator.tools.ban,
                "image_analysis": cfg.orchestrator.tools.image_analysis,
            },
            "auto_dialogue": {
                "enabled": cfg.orchestrator.auto_dialogue.enabled,
                "chain_length": cfg.orchestrator.auto_dialogue.chain_length,
                "cooldown_ms": cfg.orchestrator.auto_dialogue.cooldown_ms,
                "trigger_probability": cfg.orchestrator.auto_dialogue.trigger_probability,
                "initiation_probability": cfg.orchestrator.auto_dialogue.initiation_probability,
                "initiation_interval_ms": cfg.orchestrator.auto_dialogue.initiation_interval_ms,
            },
            "context_compression": {
                "enabled": cfg.orchestrator.context_compression.enabled,
                "target_tokens": cfg.orchestrator.context_compression.target_tokens,
                "reserve_recent": cfg.orchestrator.context_compression.reserve_recent,
                "model": cfg.orchestrator.context_compression.model,
            },
            "dispatcher": {
                "enabled": cfg.orchestrator.dispatcher.enabled,
                "supreme_power": cfg.orchestrator.dispatcher.supreme_power,
                "fallback_to_simple": cfg.orchestrator.dispatcher.fallback_to_simple,
                "fallback_reply_probability": cfg.orchestrator.dispatcher.fallback_reply_probability,
                "assistant_max_tokens": cfg.orchestrator.dispatcher.assistant_max_tokens,
                "assistant_temperature": cfg.orchestrator.dispatcher.assistant_temperature,
                "dispatcher_preset": cfg.orchestrator.dispatcher.dispatcher_preset,
                "dispatcher_prompt": cfg.orchestrator.dispatcher.dispatcher_prompt,
            },
        },
        "chat": {
            "admin_qq": cfg.chat.admin_qq,
            "main_group_id": cfg.chat.main_group_id,
            "private_message_enabled": cfg.chat.private_message_enabled,
            "dashboard_password_set": bool(cfg.chat.dashboard_password),
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
    model_thinking: str = None
    assistant_model: str = None
    assistant_model_thinking: str = None
    temperature: float = None
    max_tokens: int = None


class VisionConfigUpdate(BaseModel):
    enabled: bool = None
    api_key: str = None
    base_url: str = None
    model: str = None
    thinking: str = None


class ToolsConfigUpdate(BaseModel):
    emoji_reaction: bool = None
    ban: bool = None
    image_analysis: bool = None


class OrchestratorConfigUpdate(BaseModel):
    enabled: bool = None
    reply_delay_ms: int = None
    max_context_messages: int = None
    max_context_tokens: int = None
    group_reply_probability: float = None
    prompt_prefix: str = None
    prompt_suffix: str = None
    time_awareness: bool = None
    tools: ToolsConfigUpdate = None


class ChatConfigUpdate(BaseModel):
    admin_qq: str = None
    main_group_id: str = None
    private_message_enabled: bool = None
    dashboard_password: str = None


class ConfigUpdate(BaseModel):
    server: ServerConfigUpdate = None
    llm: LLMConfigUpdate = None
    vision: VisionConfigUpdate = None
    orchestrator: OrchestratorConfigUpdate = None
    chat: ChatConfigUpdate = None


@app.post("/api/config")
async def update_config(update: ConfigUpdate):
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

    llm_changed = False
    if update.llm:
        if update.llm.provider is not None:
            current.llm.provider = update.llm.provider
        if update.llm.api_key:
            current.llm.api_key = update.llm.api_key
            llm_changed = True
        if update.llm.base_url is not None:
            current.llm.base_url = update.llm.base_url
            llm_changed = True
        if update.llm.model is not None:
            current.llm.model = update.llm.model
        if update.llm.model_thinking is not None:
            current.llm.model_thinking = update.llm.model_thinking
        if update.llm.assistant_model is not None:
            current.llm.assistant_model = update.llm.assistant_model
        if update.llm.assistant_model_thinking is not None:
            current.llm.assistant_model_thinking = update.llm.assistant_model_thinking
        if update.llm.temperature is not None:
            current.llm.temperature = update.llm.temperature
        if update.llm.max_tokens is not None:
            current.llm.max_tokens = update.llm.max_tokens

    if update.vision:
        if update.vision.enabled is not None:
            current.llm.vision.enabled = update.vision.enabled
            llm_changed = True
        if update.vision.api_key:
            current.llm.vision.api_key = update.vision.api_key
            llm_changed = True
        if update.vision.base_url is not None:
            current.llm.vision.base_url = update.vision.base_url
            llm_changed = True
        if update.vision.model is not None:
            current.llm.vision.model = update.vision.model
            llm_changed = True
        if update.vision.thinking is not None:
            current.llm.vision.thinking = update.vision.thinking
            llm_changed = True

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
        if update.orchestrator.tools:
            if update.orchestrator.tools.emoji_reaction is not None:
                current.orchestrator.tools.emoji_reaction = update.orchestrator.tools.emoji_reaction
            if update.orchestrator.tools.ban is not None:
                current.orchestrator.tools.ban = update.orchestrator.tools.ban
            if update.orchestrator.tools.image_analysis is not None:
                current.orchestrator.tools.image_analysis = update.orchestrator.tools.image_analysis

    if update.chat:
        if update.chat.admin_qq is not None:
            current.chat.admin_qq = update.chat.admin_qq
        if update.chat.main_group_id is not None:
            current.chat.main_group_id = update.chat.main_group_id
        if update.chat.private_message_enabled is not None:
            current.chat.private_message_enabled = update.chat.private_message_enabled
        if update.chat.dashboard_password is not None:
            if update.chat.dashboard_password:
                current.chat.dashboard_password = update.chat.dashboard_password

    save_config(current)
    # Update each sub-config in-place so all cached references stay valid
    config.server.__dict__.update(current.server.__dict__)
    config.llm.__dict__.update(current.llm.__dict__)
    config.llm.vision.__dict__.update(current.llm.vision.__dict__)
    config.orchestrator.__dict__.update(current.orchestrator.__dict__)
    config.orchestrator.tools.__dict__.update(current.orchestrator.tools.__dict__)
    config.orchestrator.auto_dialogue.__dict__.update(current.orchestrator.auto_dialogue.__dict__)
    config.orchestrator.context_compression.__dict__.update(current.orchestrator.context_compression.__dict__)
    config.orchestrator.buffer.__dict__.update(current.orchestrator.buffer.__dict__)
    config.orchestrator.dispatcher.__dict__.update(current.orchestrator.dispatcher.__dict__)
    config.chat.__dict__.update(current.chat.__dict__)
    config.logging.__dict__.update(current.logging.__dict__)

    # Reinitialize LLM client if API key or base URL changed
    if llm_changed:
        llm_client.reinitialize()

    return {"success": True, "message": "配置已保存"}


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
    config.server.__dict__.update(current.server.__dict__)
    config.llm.__dict__.update(current.llm.__dict__)
    config.llm.vision.__dict__.update(current.llm.vision.__dict__)
    config.orchestrator.__dict__.update(current.orchestrator.__dict__)
    config.orchestrator.tools.__dict__.update(current.orchestrator.tools.__dict__)
    config.orchestrator.auto_dialogue.__dict__.update(current.orchestrator.auto_dialogue.__dict__)
    config.orchestrator.context_compression.__dict__.update(current.orchestrator.context_compression.__dict__)
    config.chat.__dict__.update(current.chat.__dict__)
    config.logging.__dict__.update(current.logging.__dict__)

    return {"success": True, "message": "自动对话配置已保存"}


@app.post("/api/orchestrator/auto-dialogue/toggle")
async def toggle_auto_dialogue():
    current = load_config()
    current.orchestrator.auto_dialogue.enabled = not current.orchestrator.auto_dialogue.enabled
    save_config(current)
    config.orchestrator.auto_dialogue.__dict__.update(current.orchestrator.auto_dialogue.__dict__)
    return {"enabled": config.orchestrator.auto_dialogue.enabled}


@app.get("/api/chat/config")
async def get_chat_config():
    cfg = load_config()
    return {
        "admin_qq": cfg.chat.admin_qq,
        "main_group_id": cfg.chat.main_group_id,
        "private_message_enabled": cfg.chat.private_message_enabled,
        "dashboard_password_set": bool(cfg.chat.dashboard_password),
    }


@app.post("/api/chat/config")
async def update_chat_config(update: ChatConfigUpdate):
    current = load_config()

    if update.admin_qq is not None:
        current.chat.admin_qq = update.admin_qq
    if update.main_group_id is not None:
        current.chat.main_group_id = update.main_group_id
    if update.private_message_enabled is not None:
        current.chat.private_message_enabled = update.private_message_enabled
    if update.dashboard_password is not None:
        if update.dashboard_password:
            current.chat.dashboard_password = update.dashboard_password

    save_config(current)
    config.server.__dict__.update(current.server.__dict__)
    config.llm.__dict__.update(current.llm.__dict__)
    config.llm.vision.__dict__.update(current.llm.vision.__dict__)
    config.orchestrator.__dict__.update(current.orchestrator.__dict__)
    config.orchestrator.tools.__dict__.update(current.orchestrator.tools.__dict__)
    config.orchestrator.auto_dialogue.__dict__.update(current.orchestrator.auto_dialogue.__dict__)
    config.orchestrator.context_compression.__dict__.update(current.orchestrator.context_compression.__dict__)
    config.orchestrator.buffer.__dict__.update(current.orchestrator.buffer.__dict__)
    config.orchestrator.dispatcher.__dict__.update(current.orchestrator.dispatcher.__dict__)
    config.chat.__dict__.update(current.chat.__dict__)
    config.logging.__dict__.update(current.logging.__dict__)

    return {"success": True, "message": "聊天配置已保存"}


class DispatcherConfigUpdate(BaseModel):
    """分配器配置更新"""
    enabled: bool = None
    supreme_power: bool = None
    fallback_to_simple: bool = None
    fallback_reply_probability: float = None
    assistant_max_tokens: int = None
    assistant_temperature: float = None
    dispatcher_preset: str = None
    dispatcher_prompt: str = None


@app.get("/api/dispatcher/config")
async def get_dispatcher_config():
    """获取分配器配置"""
    cfg = load_config()
    from backend.dispatcher import DISPATCHER_PRESETS
    return {
        "enabled": cfg.orchestrator.dispatcher.enabled,
        "supreme_power": cfg.orchestrator.dispatcher.supreme_power,
        "fallback_to_simple": cfg.orchestrator.dispatcher.fallback_to_simple,
        "fallback_reply_probability": cfg.orchestrator.dispatcher.fallback_reply_probability,
        "assistant_max_tokens": cfg.orchestrator.dispatcher.assistant_max_tokens,
        "assistant_temperature": cfg.orchestrator.dispatcher.assistant_temperature,
        "dispatcher_preset": cfg.orchestrator.dispatcher.dispatcher_preset,
        "dispatcher_prompt": cfg.orchestrator.dispatcher.dispatcher_prompt,
        "presets": {
            k: {"name": v["name"], "description": v["description"]}
            for k, v in DISPATCHER_PRESETS.items()
        },
    }


@app.post("/api/dispatcher/config")
async def update_dispatcher_config(update: DispatcherConfigUpdate):
    """更新分配器配置"""
    current = load_config()

    if update.enabled is not None:
        current.orchestrator.dispatcher.enabled = update.enabled
    if update.supreme_power is not None:
        current.orchestrator.dispatcher.supreme_power = update.supreme_power
    if update.fallback_to_simple is not None:
        current.orchestrator.dispatcher.fallback_to_simple = update.fallback_to_simple
    if update.fallback_reply_probability is not None:
        current.orchestrator.dispatcher.fallback_reply_probability = update.fallback_reply_probability
    if update.assistant_max_tokens is not None:
        current.orchestrator.dispatcher.assistant_max_tokens = update.assistant_max_tokens
    if update.assistant_temperature is not None:
        current.orchestrator.dispatcher.assistant_temperature = update.assistant_temperature
    if update.dispatcher_preset is not None:
        current.orchestrator.dispatcher.dispatcher_preset = update.dispatcher_preset
    if update.dispatcher_prompt is not None:
        current.orchestrator.dispatcher.dispatcher_prompt = update.dispatcher_prompt

    save_config(current)
    config.orchestrator.dispatcher.__dict__.update(current.orchestrator.dispatcher.__dict__)

    return {"success": True, "message": "调度器配置已保存"}


@app.get("/api/dispatcher/presets")
async def get_dispatcher_presets():
    """获取所有调度器预设"""
    from backend.dispatcher import DISPATCHER_PRESETS
    return {
        "presets": {
            k: {
                "name": v["name"],
                "description": v["description"],
                "fallback_reply_probability": v["fallback_reply_probability"],
            }
            for k, v in DISPATCHER_PRESETS.items()
        },
        "current_preset": config.orchestrator.dispatcher.dispatcher_preset,
    }


@app.get("/api/dispatcher/status")
async def get_dispatcher_status():
    """获取分配器和消息缓冲区状态"""
    result = {
        "dispatcher_enabled": config.orchestrator.dispatcher.enabled,
        "buffer_enabled": config.orchestrator.buffer.enabled,
    }
    
    if message_buffer:
        result["buffer"] = message_buffer.get_stats()
    
    if dispatcher:
        result["dispatcher"] = dispatcher.get_stats()
    
    return result


@app.post("/api/dispatcher/chat-state")
async def set_chat_state(group_id: str, state: str):
    """手动设置群的对话状态"""
    if state not in ("active", "winding_down", "terminated"):
        raise HTTPException(400, "Invalid state. Must be: active, winding_down, terminated")
    
    if dispatcher:
        dispatcher.set_chat_state(group_id, state)
        return {"success": True, "group_id": group_id, "state": state}
    
    raise HTTPException(500, "Dispatcher not initialized")


def run():
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host=config.server.host,
        port=config.server.management_port,
        reload=False,
        log_level=config.logging.level.lower(),
    )
