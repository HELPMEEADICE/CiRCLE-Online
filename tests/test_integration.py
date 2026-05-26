import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport


@pytest_asyncio.fixture
async def client():
    from backend.main import app
    from backend.config import init_config, config
    from backend.database import init_db
    from backend.character_manager import init_character_manager
    from backend.llm_client import init_llm_client
    from backend.orchestrator import init_orchestrator
    from backend.websocket_server import MultiPortWebSocketServer
    from backend.napcat_handler import NapCatMessageHandler
    import backend.main as main_module

    init_config()
    init_db()
    cm = init_character_manager()
    lc = init_llm_client()
    orch = init_orchestrator()

    ws = MultiPortWebSocketServer(config.server.base_port, config.server.num_ports, config.server.host)
    handler = NapCatMessageHandler()
    handler.on_group_message = orch.handle_group_message
    handler.on_private_message = orch.handle_private_message
    ws.add_event_handler(handler.handle_event)
    orch.set_ws_server(ws)

    main_module.orchestrator = orch
    main_module.character_manager = cm
    main_module.llm_client = lc
    main_module.ws_server = ws
    main_module.msg_handler = handler

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _login(client: AsyncClient) -> str:
    resp = await client.post("/api/auth/login", json={"password": "admin123"})
    assert resp.status_code == 200
    return resp.json()["token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── Auth ──

@pytest.mark.asyncio
async def test_login_success(client):
    resp = await client.post("/api/auth/login", json={"password": "admin123"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "token" in data


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    resp = await client.post("/api/auth/login", json={"password": "wrong"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_auth_check_valid(client):
    token = await _login(client)
    resp = await client.get("/api/auth/check", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["authenticated"] is True


@pytest.mark.asyncio
async def test_auth_check_invalid(client):
    resp = await client.get("/api/auth/check", headers=_auth("bad-token"))
    assert resp.status_code == 200
    assert resp.json()["authenticated"] is False


@pytest.mark.asyncio
async def test_logout(client):
    token = await _login(client)
    resp = await client.post("/api/auth/logout", headers=_auth(token))
    assert resp.status_code == 200
    resp = await client.get("/api/auth/check", headers=_auth(token))
    assert resp.json()["authenticated"] is False


@pytest.mark.asyncio
async def test_unauthorized_access(client):
    resp = await client.get("/api/status")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_public_routes(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    resp = await client.get("/api/auth/check")
    assert resp.status_code == 200


# ── Config ──

@pytest.mark.asyncio
async def test_get_config(client):
    token = await _login(client)
    resp = await client.get("/api/config", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()
    assert "server" in data
    assert "llm" in data
    assert "orchestrator" in data
    assert "chat" in data
    assert data["chat"]["dashboard_password_set"] is True


@pytest.mark.asyncio
async def test_update_config(client):
    token = await _login(client)
    resp = await client.post("/api/config", headers=_auth(token), json={
        "orchestrator": {"group_reply_probability": 0.5}
    })
    assert resp.status_code == 200
    assert resp.json()["success"] is True


@pytest.mark.asyncio
async def test_get_chat_config(client):
    token = await _login(client)
    resp = await client.get("/api/chat/config", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()
    assert "admin_qq" in data
    assert "dashboard_password_set" in data


# ── Orchestrator ──

@pytest.mark.asyncio
async def test_toggle_orchestrator(client):
    token = await _login(client)
    resp = await client.post("/api/orchestrator/toggle", headers=_auth(token))
    assert resp.status_code == 200
    assert "enabled" in resp.json()


@pytest.mark.asyncio
async def test_enable_disable_orchestrator(client):
    token = await _login(client)
    resp = await client.post("/api/orchestrator/enable", headers=_auth(token))
    assert resp.json()["enabled"] is True
    resp = await client.post("/api/orchestrator/disable", headers=_auth(token))
    assert resp.json()["enabled"] is False


# ── Characters ──

@pytest.mark.asyncio
async def test_get_characters(client):
    token = await _login(client)
    resp = await client.get("/api/characters", headers=_auth(token))
    assert resp.status_code == 200
    chars = resp.json()
    assert len(chars) == 5
    names = [c["name"] for c in chars]
    assert "户山香澄" in names


@pytest.mark.asyncio
async def test_get_single_character(client):
    token = await _login(client)
    resp = await client.get("/api/characters/户山香澄", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["name"] == "户山香澄"


@pytest.mark.asyncio
async def test_get_nonexistent_character(client):
    token = await _login(client)
    resp = await client.get("/api/characters/不存在", headers=_auth(token))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_reload_characters(client):
    token = await _login(client)
    resp = await client.post("/api/reload-characters", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["count"] == 5


# ── Status ──

@pytest.mark.asyncio
async def test_get_status(client):
    token = await _login(client)
    resp = await client.get("/api/status", headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()
    assert "enabled" in data
    assert "ports" in data
    assert "characters" in data
    assert "assignments" in data


# ── Session Memory ──

@pytest.mark.asyncio
async def test_session_memory_compress():
    import asyncio
    from backend.orchestrator import SessionMemory
    from backend.models import ChatMessage
    from backend.config import init_config
    from backend.database import init_db

    init_config()
    db = init_db()
    await db.init_db()

    session = SessionMemory("test_compress", max_messages=10, max_tokens=50)
    await session.init_from_db()

    for i in range(5):
        await session.add(ChatMessage(role="user", content=f"message {i}"))

    assert len(session.messages) == 5

    await session.clear()
    assert len(session.messages) == 0


@pytest.mark.asyncio
async def test_session_memory_max_messages():
    from backend.orchestrator import SessionMemory
    from backend.models import ChatMessage
    from backend.config import init_config
    from backend.database import init_db

    init_config()
    db = init_db()
    await db.init_db()

    session = SessionMemory("test_max", max_messages=3, max_tokens=999999)
    await session.init_from_db()

    for i in range(5):
        await session.add(ChatMessage(role="user", content=f"msg {i}"))

    assert len(session.messages) == 3
    assert session.messages[0].content == "msg 2"

    await session.clear()


# ── Assignment ──

@pytest.mark.asyncio
async def test_assign_character(client):
    token = await _login(client)
    resp = await client.post("/api/assign", headers=_auth(token), json={
        "port": 8081,
        "character_name": "户山香澄"
    })
    assert resp.status_code == 200
    assert resp.json()["success"] is True


@pytest.mark.asyncio
async def test_assign_invalid_port(client):
    token = await _login(client)
    resp = await client.post("/api/assign", headers=_auth(token), json={
        "port": 9999,
        "character_name": "户山香澄"
    })
    assert resp.status_code == 400
