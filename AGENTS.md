# AGENTS.md — CiRCLE Online

## What This Is

Multi-agent QQ group chat orchestrator. FastAPI backend serves a static MD3 frontend and exposes WebSocket endpoints for NapCatQQ clients on ports 8081-8085. LLM-powered characters reply to group/private messages via OpenAI-compatible API.

## Commands

```bash
# Install deps (no lockfile — requirements.txt is canonical)
pip install -r requirements.txt

# Run server (dashboard at :8080, WS at :8081-8085)
python run.py

# Run tests
pytest tests/ -v

# Dev deps include ruff but no lint/format config exists yet
pip install -e ".[dev]"
```

There is no CI, no pre-commit, no type-checking, and no formatter config. `ruff` is a dev dependency but has no configuration file.

## Architecture

```
run.py → backend/main.py:app (FastAPI, uvicorn)
```

- **Config loading**: `config/settings.toml` + `config/ports.toml` + env vars (`LLM_API_KEY`, `LLM_BASE_URL`). Env vars override TOML for LLM settings. Config is loaded once at module import in `backend/config.py:105` — changing TOML at runtime requires restart.
- **WebSocket server**: `MultiPortWebSocketServer` manages one WS endpoint per port. NapCatQQ clients connect to `/ws/napcat/{port}`.
- **Message flow**: WS event → `NapCatMessageHandler` → `Orchestrator.handle_group_message` / `handle_private_message` → LLM call → reply via same WS connection.
- **Orchestrator**: Single global instance. Tracks per-group and per-private session memory. Reply probability is `group_reply_probability` (default 0.3) unless character name is mentioned.
- **LLM client**: `AsyncOpenAI` wrapper. Uses character's `soul.md` as system prompt. Chinese-language roleplay prompt is hardcoded in `llm_client.py:76-80`.
- **Characters**: Each subdirectory in `characters/` must contain `soul.md` (system prompt). Optional `avatar.{png,jpg,jpeg,webp}`. Character name = directory name (Chinese names like `户山香澄`).

## Key Gotchas

- **Port range**: 5 ports starting at 8081 (configurable). Management API on 8080. Port assignment persisted to `config/ports.toml` at runtime.
- **Auth**: API endpoints require token via `Authorization: Bearer <token>` header or `access_token` query param. CORS configured for localhost and 192.168.*.* LAN.
- **Global singletons**: `config`, `orchestrator`, `character_manager`, `llm_client` are module-level singletons. Import side effects are real.
- **Character reload**: Call `POST /api/reload-characters` to reload from disk without restart. Config changes require restart.
- **Test expectations**: `test_character_manager` asserts exactly 5 characters exist. Adding/removing character dirs will break it.
- **No `.env` loading in code**: Despite `python-dotenv` being a dependency, the app does NOT call `load_dotenv()`. Use shell env vars or edit `config/settings.toml`.

## Files That Matter

| Path | Role |
|------|------|
| `run.py` | Entry point — adds repo root to sys.path |
| `backend/main.py` | FastAPI app, lifespan, all API routes |
| `backend/config.py` | TOML + env config loading, singleton at L105 |
| `backend/orchestrator.py` | Message routing, session memory, reply logic |
| `backend/llm_client.py` | OpenAI API wrapper |
| `backend/websocket_server.py` | Multi-port WS server, NapCatConnection |
| `backend/character_manager.py` | Loads characters from `characters/*/soul.md` |
| `config/settings.toml` | Primary config (server, LLM, orchestrator) |
| `config/ports.toml` | Port-to-character assignments (runtime-writable) |
| `characters/*/soul.md` | Character system prompts |
