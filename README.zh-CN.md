# CiRCLE Online

> *"キラキラドキドキする——って感じ！"* — 戸山香澄

**CiRCLE Online** 是一个**多智能体 QQ 群聊编排器**，让虚拟角色真正活起来。基于大语言模型，通过 NapCatQQ 接入 QQ，让 AI 角色——目前是《BanG Dream!》中 **Poppin'Party** 的五位成员——像真正的乐队成员一样在群聊中聊天、拌嘴、自主互动。

每个角色运行在独立的 WebSocket 端口上，拥有从 `soul.md` 加载的完整人格设定，可以主动发起对话或对其他角色进行连锁回复。这不是一个聊天机器人。这是一场**现场演出**——每条消息都是一次演奏。

后端采用 **FastAPI** + **Material Design 3** 前端，因为即使是乐队经理也值得拥有漂亮的仪表盘。

---

## 功能特性

- **🎸 多端口 WebSocket 服务器**：同时接受 5 个 NapCatQQ 客户端连接——每个乐队成员一个端口
- **🎤 LLM 驱动角色扮演**：每个角色拥有完整的 `soul.md` 人格提示词，通过 OpenAI 兼容 API 生成消息，附带中文角色扮演指令微调
- **🤖 双调度器架构**：两个独立的 AI 调度器——**对话调度器**决定谁说话，**表情调度器**决定何时用表情回应，均使用 Function Calling 实现精准控制
- **⚡ 智能消息缓冲**：替代固定延迟的动态消息聚合。消息按群分组缓冲，1.2 秒无新消息后批量处理，避免打断正在说话的人
- **🎭 表情回应系统**：角色可通过 `set_msg_emoji_like` 工具调用对消息添加表情回应（🐛/🐵/🐳），表情调度与对话调度并行运行
- **🔧 Function Calling 工具**：内置管理工具（`set_group_ban` 禁言）、表情回应、图片分析（`analyze_image`）。角色不只是聊天，还能采取行动
- **🎯 5 种调度预设**：从"静默"（几乎不说话）到"热情"（回复一切），每个预设控制回复概率和连锁行为
- **🛡️ 至高权限模式**：当需要角色最大程度响应时，可覆盖所有机械约束（冷却、链长限制等）
- **💬 对话状态机**：按群追踪对话状态（active/winding_down/terminated），新消息到达时自动重置
- **🧠 会话记忆**：基于 Token 感知、SQLite 存储的上下文管理，支持自动上下文压缩。乐队不会忘记五分钟前说了什么（不像某位吉他手）
- **📊 Material Design 3 仪表盘**：精美的 MD3 Web 界面，可监控连接状态、分配角色、调整配置、查看实时消息
- **🔌 NapCatQQ 兼容**：通过 WebSocket 完整支持 OneBot 协议，可与任何 NapCatQQ 客户端即插即用
- **🎨 角色头像支持**：每个角色可配置头像图片，随角色信息一并展示
- **🔧 角色热重载**：增删改角色 `soul.md` 文件后可通过 API 热重载，无需重启

---

## 架构

```
                              ┌─────────────────────────────────────┐
                              │            消息流                   │
                              └─────────────────────────────────────┘

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
                MessageBuffer (1.2秒窗口)
                  ┌─────┴─────┐
                  ▼           ▼
           群 A 缓冲区    群 B 缓冲区    ... (按群隔离)
                  │           │
                  ▼           ▼
               Dispatcher (双分支)
          ┌───────┴───────┐
          ▼               ▼
    对话调度分支       表情调度分支
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
       ├── 会话记忆 (SQLite)
       ├── 上下文压缩
       ├── 消息 ID 别名
       └── 链计数器
              │
              ▼
        LLMClient (AsyncOpenAI)
         ├── 主模型 (角色回复)
         ├── 辅助模型 (调度器决策)
         └── 视觉模型 (图片分析)
```

### 双调度器系统

核心创新是将对话决策与表情回应分离的**双调度器架构**：

**对话调度器**使用 Function Calling 决定：
- `schedule_reply` — 一个角色应该回复
- `schedule_chain` — 多个角色按顺序接力回复
- `skip_response` — 不需要回复
- `terminate_dialogue` — 终止本轮对话

**表情调度器**独立并行运行：
- `set_msg_emoji_like` — 用 🐛（下头）、🐵（无语）或 🐳（喜欢）回应
- `skip_emoji_reaction` — 不贴表情

两个调度器都使用**辅助 LLM 模型**通过 Function Calling 进行结构化决策，如果模型失败则回退到简单的概率逻辑。

### 消息缓冲

消息按群分组缓冲，1.2 秒无新消息后触发 flush：
- 连续消息会重置定时器，等待对话暂停
- 不同群的消息独立处理，互不阻塞
- 避免打断正在说话的人，允许一次考虑多条消息

### 调度预设

| 预设 | 回复概率 | 行为 |
|------|----------|------|
| `silent` (静默) | 1% | 仅响应直接 @提及 |
| `conservative` (保守) | 15% | 话题明显相关时才回复 |
| `balanced` (平衡) | 30% | 适度参与，平衡活跃与克制 |
| `active` (积极) | 50% | 乐于参与，积极接话 |
| `enthusiastic` (热情) | 80% | 非常活跃，几乎不放过任何互动机会 |

### 至高权限模式

启用后（`supreme_power = true`），调度器可覆盖：
- 回复间的冷却计时器
- 链长度限制
- 已终止状态限制

用于需要角色最大程度响应的场景，代价是更高的 API 调用量。

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
| `[orchestrator]` | 回复延迟、上下文限制、提示词定制 |
| `[orchestrator.buffer]` | 消息缓冲窗口（默认 1.2 秒）、最大容量 |
| `[orchestrator.dispatcher]` | 调度器预设、至高权限、回退行为 |
| `[orchestrator.auto_dialogue]` | 链长度、冷却时间、触发/主动发起概率 |
| `[orchestrator.context_compression]` | 基于 Token 的记忆压缩设置 |
| `[chat]` | 管理员 QQ、主群号、仪表盘密码 |

**角色-端口分配** 存储在 `config/ports.toml` 中，支持运行时修改（通过仪表盘或 API 分配/取消分配）。

### 关键配置

```toml
[orchestrator.dispatcher]
enabled = true                    # 启用 AI 调度器（vs 简单概率）
supreme_power = false             # 覆盖冷却和链长限制
fallback_to_simple = true         # 调度器失败时回退到概率逻辑
dispatcher_preset = "balanced"    # silent/conservative/balanced/active/enthusiastic

[orchestrator.buffer]
enabled = true                    # 启用消息缓冲
window_ms = 1200                  # 1.2 秒无新消息后 flush
max_size = 50                     # 每个缓冲区最大消息数

[orchestrator.auto_dialogue]
enabled = true                    # 启用连锁回复
chain_length = 3                  # 连锁中最大角色数
cooldown_ms = 5000                # 角色回复间最小间隔
trigger_probability = 0.5         # 其他角色加入连锁的概率
```

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

### 调度器控制

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/dispatcher/config` | 获取调度器配置 |
| `POST` | `/api/dispatcher/config` | 更新调度器配置 |
| `GET` | `/api/dispatcher/presets` | 列出可用预设 |
| `GET` | `/api/dispatcher/status` | 获取各群调度器状态 |
| `POST` | `/api/dispatcher/chat-state` | 设置群对话状态 |

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
│   ├── orchestrator.py       # 会话记忆、回复逻辑、工具执行
│   ├── dispatcher.py         # 双 AI 调度器（对话 + 表情）
│   ├── message_buffer.py     # 按群消息聚合
│   ├── llm_client.py         # AsyncOpenAI 封装（主模型 + 辅助模型 + 视觉模型）
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
│   ├── chat_history.db       # SQLite 数据库（自动创建）
│   └── Context_Compression.md # 压缩的上下文摘要
│
└── tests/
    ├── test_basic.py         # 单元测试（配置、角色、模型）
    └── test_integration.py   # 集成测试（API 端点）
```

---

## 开发相关 FAQ

**问：角色为什么从来不回复？**
答：检查 `dispatcher_preset` 配置。用 "enthusiastic" 可以获得最大响应率，或者启用 `supreme_power` 来覆盖所有冷却限制。也有可能是有咲在无视你——这很符合人设。

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

**问：主模型和辅助模型有什么区别？**
答：**主模型**负责生成角色回复（角色扮演响应）。**辅助模型**驱动调度器——使用 Function Calling 决定谁该说话、什么时候说话。你可以为两者使用不同的模型（例如调度用快速模型，反应用创意模型）。

**问：为什么角色有时用表情回应而不是说话？**
答：表情调度器与对话调度器并行运行。有时候一个 🐛（下头）、🐵（无语）或 🐳（喜欢）比完整回复更合适。角色也可以通过 `set_msg_emoji_like` 工具在文字回复的同时添加表情。

**问："supreme_power"（至高权限）是做什么的？**
答：就像给香澄灌了无限能量饮料。启用后，调度器可以无视冷却计时器、链长限制和已终止状态。适合活动场景或需要最大程度混乱的时候使用。

**问：消息缓冲机制是怎么工作的？**
答：系统不会立即响应每条消息，而是按群分组缓冲。1.2 秒无新消息（可配置）后，缓冲的消息会被批量发送给调度器。这样可以避免打断正在说话的人，并允许一次考虑多条消息。

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.10+, FastAPI, Uvicorn |
| WebSocket | websockets 库（legacy server API） |
| 数据库 | aiosqlite（SQLite） |
| LLM 客户端 | OpenAI SDK（AsyncOpenAI）— 主模型 + 辅助模型 + 视觉模型 |
| Token 计数 | tiktoken（cl100k_base） |
| 前端 | HTML + CSS + JS, Material Design 3 |
| 认证 | HMAC Token（24 小时有效期） |
| 协议 | OneBot v11（NapCatQQ） |
| 调度器 | 基于 Function Calling 的 AI 决策引擎 |

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
