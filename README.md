# CiRCLE Online

> *"キラキラドキドキする——って感じ！"* — 戸山香澄

**CiRCLE Online** is a **multi-agent QQ group chat orchestrator** that brings virtual characters to life. Powered by LLMs, it connects to QQ via NapCatQQ and lets AI characters — currently the 5 members of **Poppin'Party** from *BanG Dream!* — chat, banter, and autonomously interact in group chats like real band members.

Each character runs on an independent WebSocket port, has its own personality loaded from `soul.md`, and can proactively start conversations or chain-reply to other characters. It's not a chatbot. It's a **live concert** where every message is a performance.

Built with **FastAPI** + **Material Design 3** frontend, because even band managers deserve a beautiful dashboard.

---

## Features

- **🎸 Multi-Port WebSocket Server**: Accepts connections from 5 NapCatQQ clients simultaneously — one for each band member.
- **🎤 LLM-Powered Character Roleplay**: Each character has a full `soul.md` personality prompt. Messages are generated via OpenAI-compatible API with Chinese roleplay instruction tuning.
- **🤖 Dual-Dispatcher Architecture**: Two independent AI dispatchers — **Dialogue Dispatcher** decides who speaks, **Emoji Dispatcher** decides when to react with emoji. Both use Function Calling for precise control.
- **⚡ Smart Message Buffering**: Replaces fixed delays with dynamic message aggregation. Messages are buffered per-group and flushed after 1.2s of silence, preventing split conversations.
- **🎭 Emoji Reaction System**: Characters can react to messages with emoji (🐛/🐵/🐳) via `set_msg_emoji_like` tool calls. Emoji dispatch runs in parallel with dialogue dispatch.
- **🔧 Function Calling Tools**: Built-in tools for moderation (`set_group_ban`), emoji reactions, and image analysis (`analyze_image`). Characters can take actions, not just talk.
- **🎯 5 Dispatcher Presets**: From "silent" (almost never speaks) to "enthusiastic" (replies to everything). Each preset controls reply probability and chain behavior.
- **🛡️ Supreme Power Mode**: Override all mechanical constraints (cooldowns, chain limits) when you want characters to be maximally responsive.
- **💬 Dialogue State Machine**: Tracks conversation state (active/winding_down/terminated) per group. Automatically resets when new messages arrive.
- **🧠 Session Memory**: Token-aware, SQLite-backed context management with automatic context compression. The band doesn't forget what happened 5 minutes ago (unlike some guitarists).
- **📊 Material Design 3 Dashboard**: Beautiful MD3 web UI for monitoring connections, assigning characters, tweaking config, and viewing real-time messages.
- **🔌 NapCatQQ Compatible**: Full OneBot protocol support via WebSocket. Drop-in integration with any NapCatQQ client.
- **🎨 Character Avatar Support**: Each character can have an avatar image served alongside their profile.
- **🔧 Hot-Reload Characters**: Add or modify character `soul.md` files and reload via API — no restart required.

---

## Architecture

```
                              ┌─────────────────────────────────────┐
                              │         Message Flow                │
                              └─────────────────────────────────────┘

NapCatQQ ──WS──▶ Port 8081 ──▶ 戸山香澄  (Vocalist/Guitar)
NapCatQQ ──WS──▶ Port 8082 ──▶ 市谷有咲  (Keyboard)
NapCatQQ ──WS──▶ Port 8083 ──▶ 山吹沙绫  (Drummer)
NapCatQQ ──WS──▶ Port 8084 ──▶ 牛込里美  (Bass)
NapCatQQ ──WS──▶ Port 8085 ──▶ 花园多惠  (Lead Guitar)
                        │
                        ▼
              NapCatMessageHandler
                        │
                        ▼
                MessageBuffer (1.2s window)
                  ┌─────┴─────┐
                  ▼           ▼
           Group A Buffer  Group B Buffer  ... (per-group isolation)
                  │           │
                  ▼           ▼
               Dispatcher (Dual-Branch)
          ┌───────┴───────┐
          ▼               ▼
   Dialogue Branch    Emoji Branch
   (Function Call)    (Function Call)
          │               │
          ▼               ▼
   schedule_reply    set_msg_emoji_like
   schedule_chain    skip_emoji_reaction
   skip_response
   terminate_dialogue
          │               │
          ▼               ▼
      Orchestrator ────────────────► NapCat API
       ├── Session Memory (SQLite)
       ├── Context Compression
       ├── Message ID Aliases
       └── Chain Counters
              │
              ▼
        LLMClient (AsyncOpenAI)
         ├── Main Model (character replies)
         ├── Assistant Model (dispatcher decisions)
         └── Vision Model (image analysis)
```

### Dual-Dispatcher System

The core innovation is the **dual-dispatcher architecture** that separates dialogue decisions from emoji reactions:

**Dialogue Dispatcher** uses Function Calling to decide:
- `schedule_reply` — One character should respond
- `schedule_chain` — Multiple characters should respond in sequence
- `skip_response` — No response needed
- `terminate_dialogue` — End the conversation

**Emoji Dispatcher** runs in parallel and independently decides:
- `set_msg_emoji_like` — React with 🐛 (cringe), 🐵 (confused), or 🐳 (like)
- `skip_emoji_reaction` — No emoji needed

Both dispatchers use a **secondary LLM model** with Function Calling for structured decision-making, with fallback to simple probability-based logic if the model fails.

### Message Buffering

Messages are buffered per-group with a 1.2s idle window:
- Continuous messages reset the timer, waiting for the conversation to pause
- Different groups process independently without blocking each other
- Prevents the bot from responding to split messages mid-sentence

### Dispatcher Presets

| Preset | Reply Probability | Behavior |
|--------|-------------------|----------|
| `silent` | 1% | Only responds to direct @mentions |
| `conservative` | 15% | Replies when topic clearly relates to character |
| `balanced` | 30% | Moderate participation, balances activity and restraint |
| `active` | 50% | Enjoys participating, readily joins conversations |
| `enthusiastic` | 80% | Very active, rarely misses a chance to interact |

### Supreme Power Mode

When enabled (`supreme_power = true`), the dispatcher can override:
- Cooldown timers between replies
- Chain length limits
- Terminated state restrictions

Use this for maximum character responsiveness at the cost of higher API usage.

---

## Characters

| Port | Character | Band Role | Description |
|------|-----------|-----------|-------------|
| 8081 | **戸山香澄** (Kasumi Toyama) | Vocalist / Guitar | "キラキラドキドキ" incarnate. Optimist, impulsive, never gives up. Founder of Poppin'Party. |
| 8082 | **市谷有咲** (Arisa Ichigaya) | Keyboard | Rational tsundere. Secretly cares deeply. Will never give up halfway again. |
| 8083 | **山吹沙绫** (Sayo Yamabuki) | Drummer | Gentle eldest daughter. Carries the rhythm and the emotional baggage. |
| 8084 | **牛込里美** (Rimi Ushigome) | Bass | Ultra-shy on the surface, creative powerhouse underneath. Stutters. Composes. Slays. |
| 8085 | **花园多惠** (Tae Hanazono) | Lead Guitar | Spacey guitar otaku. Speaks through music, not words. Will sell you a rabbit. |

> *"Poppin'Party齐聚于此，五个人一起演奏的音乐才是最棒的！"* — pretty much what happens in your QQ group now.

---

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure LLM API

Edit `config/settings.toml`:

```toml
[llm]
api_key = "sk-your-key-here"
base_url = "https://api.openai.com/v1"
model = "gpt-4o-mini"
```

Or override via environment variables (these take precedence):

```bash
export LLM_API_KEY="sk-your-key"
export LLM_BASE_URL="https://api.openai.com/v1"
```

The project supports any **OpenAI-compatible API** (OpenAI, Azure, vLLM, Ollama, etc.). Vision model can be configured separately for image understanding.

### 3. Run the Server

```bash
python run.py
```

Dashboard: **http://localhost:8080** (default password: `admin123`)

### 4. Connect NapCatQQ

Configure your NapCatQQ client to connect to WebSocket endpoints at `ws://your-host:8081/ws/napcat/8081` through `ws://your-host:8085/ws/napcat/8085`. Each port corresponds to one character.

*The band is waiting. Don't keep Kasumi waiting or she'll start practicing guitar at 7 AM.*

---

## Configuration

All settings live in `config/settings.toml`. Config sections:

| Section | Description |
|---------|-------------|
| `[server]` | Host, port range, management port |
| `[llm]` | Provider, API key, base URL, model selection |
| `[llm.vision]` | Separate vision model config for image understanding |
| `[orchestrator]` | Reply delay, context limits, prompt customization |
| `[orchestrator.buffer]` | Message buffer window (default 1.2s), max size |
| `[orchestrator.dispatcher]` | Dispatcher preset, supreme power, fallback behavior |
| `[orchestrator.auto_dialogue]` | Chain length, cooldown, trigger/initiation probability |
| `[orchestrator.context_compression]` | Token-aware memory compression settings |
| `[chat]` | Admin QQ, main group ID, dashboard password |

**Character-to-port assignments** are stored in `config/ports.toml` and are writable at runtime (assign/unassign via the dashboard or API).

### Key Configuration

```toml
[orchestrator.dispatcher]
enabled = true                    # Enable AI dispatcher (vs simple probability)
supreme_power = false             # Override cooldowns and chain limits
fallback_to_simple = true         # Fall back to probability if dispatcher fails
dispatcher_preset = "balanced"    # silent/conservative/balanced/active/enthusiastic

[orchestrator.buffer]
enabled = true                    # Enable message buffering
window_ms = 1200                  # Flush after 1.2s of silence
max_size = 50                     # Max messages per buffer

[orchestrator.auto_dialogue]
enabled = true                    # Enable chain replies
chain_length = 3                  # Max characters in a chain
cooldown_ms = 5000                # Min time between character replies
trigger_probability = 0.5         # Chance another character joins
```

> **"設定を弄るのは有咲に任せろ！"** — actually, just use the MD3 dashboard.

---

## API Endpoints

### Dashboard Auth

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/auth/login` | Login with dashboard password |
| `POST` | `/api/auth/logout` | Revoke session token |
| `GET` | `/api/auth/check` | Check token validity |

### Management

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/status` | Full system status |
| `GET` | `/api/characters` | List all characters |
| `GET` | `/api/characters/{name}` | Get character details |
| `POST` | `/api/assign` | Assign character to a port |
| `POST` | `/api/port-token` | Update port access token |
| `POST` | `/api/reload-characters` | Reload characters from disk |
| `POST` | `/api/send` | Send a message via a port |

### Orchestrator Control

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/orchestrator/toggle` | Toggle orchestrator |
| `POST` | `/api/orchestrator/enable` | Enable orchestrator |
| `POST` | `/api/orchestrator/disable` | Disable orchestrator |
| `GET` | `/api/orchestrator/auto-dialogue/config` | Get auto-dialogue settings |
| `POST` | `/api/orchestrator/auto-dialogue/config` | Update auto-dialogue settings |
| `POST` | `/api/orchestrator/auto-dialogue/toggle` | Toggle auto-dialogue |

### Dispatcher Control

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/dispatcher/config` | Get dispatcher settings |
| `POST` | `/api/dispatcher/config` | Update dispatcher settings |
| `GET` | `/api/dispatcher/presets` | List available presets |
| `GET` | `/api/dispatcher/status` | Get dispatcher state per group |
| `POST` | `/api/dispatcher/chat-state` | Set chat state for a group |

### Config & Messages

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/config` | Get full config |
| `POST` | `/api/config` | Update config (partial updates supported) |
| `GET` | `/api/messages/recent` | Get recent messages |
| `GET` | `/api/chat/config` | Get chat config |
| `POST` | `/api/chat/config` | Update chat config |

### WebSocket

| Path | Description |
|------|-------------|
| `GET` | `/ws/napcat/{port}` | NapCatQQ WebSocket endpoint |

---

## WebSocket Protocol (NapCatQQ)

Clients connect via WebSocket and communicate using the **OneBot v11** protocol over JSON. Each connection is authenticated by port-specific token (via `access_token` query param or `Authorization` header).

Example connection URL:

```
ws://192.168.1.100:8081/ws/napcat/8081?access_token=your-token
```

Messages are standard OneBot actions (send_msg, get_group_info, etc.) and events (message, notice, request, meta_event).

---

## Project Structure

```
CiRCLE-Online/
├── run.py                    # Entry point (adds root to sys.path)
├── requirements.txt          # Python dependencies (canonical)
├── pyproject.toml            # Project metadata + dev dependencies
│
├── backend/
│   ├── main.py               # FastAPI app, routes, lifespan
│   ├── config.py             # TOML + env config parsing
│   ├── models.py             # Pydantic models
│   ├── database.py           # aiosqlite chat history
│   ├── auth.py               # Token-based dashboard auth
│   ├── websocket_server.py   # Multi-port WebSocket server
│   ├── napcat_handler.py     # OneBot event dispatcher
│   ├── orchestrator.py       # Session memory, reply logic, tool execution
│   ├── dispatcher.py         # Dual AI dispatcher (dialogue + emoji)
│   ├── message_buffer.py     # Per-group message aggregation
│   ├── llm_client.py         # AsyncOpenAI wrapper (main + assistant + vision)
│   ├── character_manager.py  # Character loader (soul.md + avatar)
│   ├── token_counter.py      # tiktoken-based token counting
│   └── utils.py              # Logging, message parsing, helpers
│
├── frontend/                 # Material Design 3 Web UI
│   ├── index.html            # Single-page MD3 dashboard
│   ├── css/style.css         # MD3 styles
│   └── js/app.js             # Dashboard logic
│
├── characters/               # Character definitions
│   ├── 户山香澄/
│   │   ├── soul.md           # System prompt (personality)
│   │   └── avatar.png        # Optional character avatar
│   ├── 市谷有咲/
│   ├── 山吹沙绫/
│   ├── 牛込里美/
│   └── 花园多惠/
│
├── config/
│   ├── settings.toml         # Main configuration
│   └── ports.toml            # Port-to-character assignments
│
├── data/
│   ├── chat_history.db       # SQLite (auto-created)
│   └── Context_Compression.md # Compressed context summaries
│
└── tests/
    ├── test_basic.py         # Unit tests (config, chars, models)
    └── test_integration.py   # Integration tests (API endpoints)
```

---

## FAQ (Featuring the Band)

**Q: Why does my character never reply?**
A: Check `dispatcher_preset` in config. Use "enthusiastic" for maximum responsiveness, or enable `supreme_power` to override all cooldowns. Or maybe Arisa is just ignoring you — it's in-character.

**Q: Can I add my own characters?**
A: Yes! Create a directory under `characters/` with a `soul.md` file. The directory name becomes the character name. Then call `POST /api/reload-characters` or restart. No need to sacrifice a guitar pick to the elder gods.

**Q: How many ports can I use?**
A: As many as you configure via `num_ports` in settings. Default is 5 — one for each Poppin'Party member. Add more if you want to invite RAS or Roselia to the chat.

**Q: Can I use a local LLM?**
A: Absolutely. Point `base_url` at any OpenAI-compatible endpoint — Ollama, vLLM, LM Studio, LocalAI, etc. Just make sure it's fast enough that Kasumi doesn't lose her キラキラ waiting for a response.

**Q: Why is there no `.env` loading even though `python-dotenv` is a dependency?**
A: Because someone (花园多惠) was probably distracted by a rabbit. Set env vars directly in your shell, or export them in your startup script. We might fix this someday. We might not. The spirit of Poppin'Party is about doing things your own way.

**Q: Can the characters see images?**
A: If vision model is configured, yes. Tae can finally understand what that blurry guitar tab photo is. Whether she'll *tell* you is another matter.

**Q: What's the difference between the main model and assistant model?**
A: The **main model** generates character replies (roleplay responses). The **assistant model** powers the dispatcher — it decides who should speak and when, using Function Calling. You can use different models for each (e.g., a fast model for dispatch, a creative model for replies).

**Q: Why do characters sometimes react with emoji instead of replying?**
A: The emoji dispatcher runs in parallel with the dialogue dispatcher. Sometimes a 🐛 (cringe), 🐵 (confused), or 🐳 (like) is more appropriate than a full reply. Characters can also add emoji alongside their text replies via the `set_msg_emoji_like` tool.

**Q: What does "supreme_power" do?**
A: It's like giving Kasumi unlimited energy drinks. When enabled, the dispatcher can ignore cooldown timers, chain length limits, and terminated states. Use it for events or when you want maximum chaos.

**Q: How does message buffering work?**
A: Instead of responding immediately to each message, the system buffers messages per-group. After 1.2 seconds of silence (configurable), the buffered messages are flushed to the dispatcher. This prevents the bot from interrupting mid-conversation and allows it to consider multiple messages at once.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.10+, FastAPI, Uvicorn |
| WebSocket | websockets library (legacy server API) |
| Database | aiosqlite (SQLite) |
| LLM Client | OpenAI SDK (AsyncOpenAI) — main + assistant + vision models |
| Token Counting | tiktoken (cl100k_base) |
| Frontend | HTML + CSS + JS, Material Design 3 |
| Auth | HMAC token-based (24h TTL) |
| Protocol | OneBot v11 (NapCatQQ) |
| Dispatcher | Function Calling-based AI decision engine |

---

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v
```

> **Contribution philosophy (as explained by 市谷有咲):**
> *"别随随便便就放弃啊！如果决定了要做，就坚持到底！"*
>
> (Translation: "Don't you dare give up halfway! If you're gonna do something, see it through!")
>
> Open issues. Send PRs. Keep the music playing.

---

## License

[GNU General Public License v3.0](LICENSE) — because Poppin'Party believes in sharing the joy.

---

*Made with キラキラドキドキ by people who love BanG Dream! and watching AI argue in group chats.*
