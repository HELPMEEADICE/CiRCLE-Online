# CiRCLE Online

> *"キラキラドキドキする——って感じ！"* — 戸山香澄

**CiRCLE Online** 是一个**多智能体 QQ 群聊编排器**，让虚拟角色真正活起来。基于大语言模型，通过 NapCatQQ 接入 QQ，让 AI 角色——目前是《BanG Dream!》中 **Poppin'Party** 的五位成员——像真正的乐队成员一样在群聊中聊天、拌嘴、自主互动。

每个角色运行在独立的 WebSocket 端口上，拥有从 `soul.md` 加载的完整人格设定，可以主动发起对话或对其他角色进行连锁回复。这不是一个聊天机器人。这是一场**现场演出**——每条消息都是一次演奏。

后端采用 **FastAPI** + **Material Design 3** 前端，因为即使是乐队经理也值得拥有漂亮的仪表盘。

---

## 功能特性

- **🎸 多端口 WebSocket 服务器**：同时接受 5 个 NapCatQQ 客户端连接——每个乐队成员一个端口
- **🎤 LLM 驱动角色扮演**：每个角色拥有完整的 `soul.md` 人格提示词，通过 OpenAI 兼容 API 生成消息，附带中文角色扮演指令微调
- **🤖 自动对话**：角色可主动发起对话并相互连锁回复，仿佛在看 Poppin'Party 排练——只不过是在 QQ 群里
- **🧠 会话记忆**：基于 Token 感知、SQLite 存储的上下文管理，支持自动上下文压缩。乐队不会忘记五分钟前说了什么（不像某位吉他手）
- **📊 Material Design 3 仪表盘**：精美的 MD3 Web 界面，可监控连接状态、分配角色、调整配置、查看实时消息
- **🔌 NapCatQQ 兼容**：通过 WebSocket 完整支持 OneBot 协议，可与任何 NapCatQQ 客户端即插即用
- **🎨 角色头像支持**：每个角色可配置头像图片，随角色信息一并展示
- **🔧 角色热重载**：增删改角色 `soul.md` 文件后可通过 API 热重载，无需重启

---

## 架构

```
NapCatQQ ──WS──▶ Port 8081 ──▶ 戸山香澄  (主唱/吉他)
NapCatQQ ──WS──▶ Port 8082 ──▶ 市谷有咲  (键盘)
NapCatQQ ──WS──▶ Port 8083 ──▶ 山吹沙绫  (鼓手)
NapCatQQ ──WS──▶ Port 8084 ──▶ 牛込里美  (贝斯)
NapCatQQ ──WS──▶ Port 8085 ──▶ 花园多惠  (主音吉他)
                        │
                        ▼
              NapCatMessageHandler
                        │
                        ▼
                  Orchestrator
                   ├── 会话记忆 (SQLite)
                   ├── 回复逻辑 (概率、@提及)
                   ├── 自动对话引擎
                   └── 上下文压缩
                        │
                        ▼
                  LLMClient (AsyncOpenAI)
                   └── 角色 soul.md + 角色扮演提示词
```

后端通过 WebSocket 使用 **OneBot 协议**通信。所有 5 个端口共享同一个编排器，每个群聊和私聊维护独立的会话记忆。收到消息后，编排器根据概率、@提及或角色名称提及决定每个角色是否回复以及如何回复。

---

## 角色

| 端口 | 角色 | 乐队担当 | 简介 |
|------|------|----------|------|
| 8081 | **戸山香澄** (Kasumi Toyama) | 主唱 / 吉他 | "キラキラドキドキ" 的化身。乐观、冲动、永不放弃。Poppin'Party 创始人。 |
| 8082 | **市谷有咲** (Arisa Ichigaya) | 键盘 | 理性的傲娇。其实比谁都关心大家。不会再半途而废了。 |
| 8083 | **山吹沙绫** (Sayo Yamabuki) | 鼓手 | 温柔的大姐姐，扛着节奏和心事。 |
| 8084 | **牛込里美** (Rimi Ushigome) | 贝斯 | 表面超级害羞，内里创作力爆棚。说话结巴。作曲怪物。 |
| 8085 | **花园多惠** (Tae Hanazono) | 主音吉他 | 神游天外的吉他宅。用音乐而不是语言说话。可能会向你推销兔子。 |

> *"Poppin'Party齐聚于此，五个人一起演奏的音乐才是最棒的！"* ——现在这句话发生在你的 QQ 群里了。

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 LLM API

编辑 `config/settings.toml`：

```toml
[llm]
api_key = "sk-your-key-here"
base_url = "https://api.openai.com/v1"
model = "gpt-4o-mini"
```

或通过环境变量覆盖（优先级更高）：

```bash
export LLM_API_KEY="sk-your-key"
export LLM_BASE_URL="https://api.openai.com/v1"
```

支持任何 **OpenAI 兼容 API**（OpenAI、Azure、vLLM、Ollama、LocalAI 等）。视觉模型可单独配置用于图片理解。

### 3. 启动服务器

```bash
python run.py
```

仪表盘：**http://localhost:8080**（默认密码：`admin123`）

### 4. 连接 NapCatQQ

将 NapCatQQ 客户端配置为连接到 WebSocket 端点 `ws://your-host:8081/ws/napcat/8081` 至 `ws://your-host:8085/ws/napcat/8085`。每个端口对应一个角色。

*乐队在等你。别让香澄等太久——不然她真的会早上 7 点开始练吉他。*

---

## 配置说明

所有配置都在 `config/settings.toml` 中。配置段说明：

| 配置段 | 说明 |
|--------|------|
| `[server]` | 主机、端口范围、管理端口 |
| `[llm]` | 供应商、API Key、Base URL、模型选择 |
| `[llm.vision]` | 独立的视觉模型配置，用于图片理解 |
| `[orchestrator]` | 回复概率、上下文限制、延迟、提示词定制 |
| `[orchestrator.auto_dialogue]` | 自动对话链长度、冷却时间、触发概率 |
| `[orchestrator.context_compression]` | 基于 Token 的记忆压缩设置 |
| `[chat]` | 管理员 QQ、主群号、仪表盘密码 |

**角色-端口分配** 存储在 `config/ports.toml` 中，支持运行时修改（通过仪表盘或 API 分配/取消分配）。

> **"設定を弄るのは有咲に任せろ！"** —— 说人话：直接用 MD3 仪表盘改就行。

---

## API 端点

### 仪表盘认证

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/auth/login` | 仪表盘登录 |
| `POST` | `/api/auth/logout` | 注销会话 |
| `GET` | `/api/auth/check` | 检查 Token 有效性 |

### 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/status` | 系统全状态 |
| `GET` | `/api/characters` | 列出所有角色 |
| `GET` | `/api/characters/{name}` | 获取角色详情 |
| `POST` | `/api/assign` | 分配角色到端口 |
| `POST` | `/api/port-token` | 更新端口访问令牌 |
| `POST` | `/api/reload-characters` | 从磁盘重载角色 |
| `POST` | `/api/send` | 通过端口发送消息 |

### 编排器控制

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/orchestrator/toggle` | 切换编排器开关 |
| `POST` | `/api/orchestrator/enable` | 启用编排器 |
| `POST` | `/api/orchestrator/disable` | 禁用编排器 |
| `GET` | `/api/orchestrator/auto-dialogue/config` | 获取自动对话配置 |
| `POST` | `/api/orchestrator/auto-dialogue/config` | 更新自动对话配置 |
| `POST` | `/api/orchestrator/auto-dialogue/toggle` | 切换自动对话 |

### 配置与消息

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/config` | 获取完整配置 |
| `POST` | `/api/config` | 更新配置（支持部分更新） |
| `GET` | `/api/messages/recent` | 获取最近消息 |
| `GET` | `/api/chat/config` | 获取聊天配置 |
| `POST` | `/api/chat/config` | 更新聊天配置 |

### WebSocket

| 路径 | 说明 |
|------|------|
| `GET` | `/ws/napcat/{port}` | NapCatQQ WebSocket 端点 |

---

## WebSocket 协议（NapCatQQ）

客户端通过 WebSocket 连接，使用 **OneBot v11** 协议的 JSON 格式通信。每个连接通过端口专属 Token 鉴权（通过 `access_token` 查询参数或 `Authorization` 请求头）。

连接示例：

```
ws://192.168.1.100:8081/ws/napcat/8081?access_token=your-token
```

消息为标准 OneBot 动作（send_msg、get_group_info 等）和事件（message、notice、request、meta_event）。

---

## 项目结构

```
CiRCLE-Online/
├── run.py                    # 入口（将项目根目录加入 sys.path）
├── requirements.txt          # Python 依赖（权威来源）
├── pyproject.toml            # 项目元数据 + 开发依赖
│
├── backend/
│   ├── main.py               # FastAPI 应用、路由、生命周期
│   ├── config.py             # TOML + 环境变量配置解析
│   ├── models.py             # Pydantic 数据模型
│   ├── database.py           # aiosqlite 聊天历史
│   ├── auth.py               # Token 认证
│   ├── websocket_server.py   # 多端口 WebSocket 服务器
│   ├── napcat_handler.py     # OneBot 事件分发器
│   ├── orchestrator.py       # 会话记忆、回复逻辑、自动对话
│   ├── llm_client.py         # AsyncOpenAI 封装
│   ├── character_manager.py  # 角色加载器（soul.md + 头像）
│   ├── token_counter.py      # tiktoken Token 计数
│   └── utils.py              # 日志、消息解析、工具函数
│
├── frontend/                 # Material Design 3 Web 界面
│   ├── index.html            # 单页 MD3 仪表盘
│   ├── css/style.css         # MD3 样式
│   └── js/app.js             # 仪表盘逻辑
│
├── characters/               # 角色定义
│   ├── 户山香澄/
│   │   ├── soul.md           # 系统提示词（人格设定）
│   │   └── avatar.png        # 可选角色头像
│   ├── 市谷有咲/
│   ├── 山吹沙绫/
│   ├── 牛込里美/
│   └── 花园多惠/
│
├── config/
│   ├── settings.toml         # 主配置文件
│   └── ports.toml            # 端口-角色分配
│
├── data/
│   └── chat_history.db       # SQLite 数据库（自动创建）
│
└── tests/
    ├── test_basic.py         # 单元测试（配置、角色、模型）
    └── test_integration.py   # 集成测试（API 端点）
```

---

## 开发相关 FAQ

**问：角色为什么从来不回复？**
答：检查 `group_reply_probability` 配置（默认 0.3）。也有可能是有咲在无视你——这很符合人设。

**问：我能添加自己的角色吗？**
答：可以！在 `characters/` 下创建一个目录，里面放一个 `soul.md` 文件。目录名就是角色名。然后调用 `POST /api/reload-characters` 或重启服务。不用献祭拨片给上古之神。

**问：最多能用多少个端口？**
答：通过 `num_ports` 配置，默认 5 个——对应 Poppin'Party 五位成员。想拉 RAS 或 Roselia 进来聊天的话可以加更多。

**问：能用本地 LLM 吗？**
答：当然。把 `base_url` 指向任何 OpenAI 兼容的端点就行——Ollama、vLLM、LM Studio、LocalAI 都可以。只要别慢到让香澄等丢了她的キラキラ就行。

**问：明明依赖里有 `python-dotenv`，为什么代码里不加载 `.env`？**
答：因为某位吉他手（花园多惠）大概又被兔子分散注意力了。直接在 shell 里设置环境变量，或者在启动脚本里 export 吧。以后可能会修。也可能不会。毕竟 Poppin'Party 的精神就是走自己的路。

**问：角色能看懂图片吗？**
答：如果配置了视觉模型就能。多惠终于可以看清那张糊掉的吉他谱照片了。至于她会不会告你答案——那是另一回事。

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.10+, FastAPI, Uvicorn |
| WebSocket | websockets 库（legacy server API） |
| 数据库 | aiosqlite（SQLite） |
| LLM 客户端 | OpenAI SDK（AsyncOpenAI） |
| Token 计数 | tiktoken（cl100k_base） |
| 前端 | HTML + CSS + JS, Material Design 3 |
| 认证 | HMAC Token（24 小时有效期） |
| 协议 | OneBot v11（NapCatQQ） |

---

## 开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest tests/ -v
```

> **贡献指南（市谷有咲 译）：**
> *"别随随便便就放弃啊！如果决定了要做，就坚持到底！"*
>
> 尽管提 Issue，尽管发 PR。让音乐继续。

---

## 开源许可

[GNU General Public License v3.0](LICENSE) —— 因为 Poppin'Party 相信分享快乐。

---

*以キラキラドキドキ之心献给所有热爱 BanG Dream! 和喜欢看 AI 在群里吵架的人。*
