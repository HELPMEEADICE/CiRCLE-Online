# CiRCLE Online

Multi-Agent QQ Group Chat Orchestrator with Material Design 3 Web UI.

## Features

- **Multi-Port WebSocket Server**: Accepts connections from 5 NapCatQQ clients
- **Character Roleplay**: LLM-powered character dialogue generation
- **MD3 Dashboard**: Beautiful Material Design 3 management interface
- **Real-time Monitoring**: Live status updates for all connections

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure LLM API

Edit `config/settings.toml`:

```toml
[llm]
api_key = "your-api-key-here"
base_url = "https://api.openai.com/v1"
model = "gpt-4o-mini"
```

Or use environment variables:

```bash
export LLM_API_KEY="your-api-key"
export LLM_BASE_URL="https://api.openai.com/v1"
```

### 3. Run the Server

```bash
python run.py
```

The dashboard will be available at `http://localhost:8080`

### 4. Connect NapCatQQ

Configure NapCatQQ to connect to WebSocket ports 8081-8085.

## Project Structure

```
CiRCLE-Online/
├── backend/           # Python backend
│   ├── main.py       # FastAPI application
│   ├── config.py     # Configuration management
│   ├── models.py     # Data models
│   ├── websocket_server.py  # WebSocket server
│   ├── napcat_handler.py    # Message handler
│   ├── orchestrator.py      # Multi-agent orchestrator
│   ├── llm_client.py        # LLM API client
│   └── character_manager.py # Character management
├── frontend/          # Web UI
│   ├── index.html    # Main page
│   ├── css/style.css # MD3 styles
│   └── js/app.js     # Application logic
├── characters/        # Character definitions
├── config/           # Configuration files
└── run.py            # Entry point
```

## API Endpoints

- `GET /api/status` - Get system status
- `POST /api/orchestrator/toggle` - Toggle orchestrator
- `POST /api/assign` - Assign character to port
- `GET /api/characters` - List all characters
- `GET /api/messages/recent` - Get recent messages

## WebSocket Protocol

NapCatQQ clients connect to `ws://host:808x/ws/napcat/{port}` where port is 8081-8085.
