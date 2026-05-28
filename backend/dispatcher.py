"""消息分配器：用辅助模型智能决策如何响应群聊消息"""

import asyncio
import json
import random
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from backend.message_buffer import BufferedMessage
from backend.config import config
from backend.utils import get_logger, resolve_at_mentions

logger = get_logger("dispatcher")


@dataclass
class ToolCall:
    """工具调用"""
    id: str
    function_name: str
    arguments: dict


@dataclass
class DispatcherDecision:
    """分配器决策结果"""
    action: str  # "reply" | "tool_only" | "skip" | "chain" | "terminate"
    character: Optional[str] = None
    characters: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    strategy: Optional[str] = None
    reason: str = ""
    chat_state: str = "active"  # "active" | "winding_down" | "terminated"


# ── 调度器提示词模板 ──
_DISPATCHER_PROMPT_TEMPLATE = """你是一个群聊调度器，只负责决定是否、由谁、按什么策略发言。

## 可用角色及其人设摘要
{characters_info}

## 最近消息（{message_count}条）
{recent_messages}

## 当前对话状态
{chat_state}

## 机械调度参数（必须作为硬约束参考）
{scheduler_state}

## 分析任务

请分析最近的群聊消息，然后必须通过一次 Function Calling 表达调度结果。考虑：
1. 是否有人直接@某个角色或提到角色名
2. 话题是否与某个角色的人设/兴趣相关
3. 对话的氛围和情感
4. 近期是否已经太吵、同一角色是否刚说过话、链式对话是否已接近上限
5. 沉默是否比发言更好

## Function Calling 规则
- 必须调用且只调用一个调度函数：schedule_reply、schedule_chain、skip_response、terminate_dialogue
- 不要直接输出 JSON 或自然语言决策
- 被@或角色名被明确提及时，优先 schedule_reply 给对应角色
- 多个角色都非常适合参与时才 schedule_chain
- 如果机械参数显示 cooldown_blocked 或 remaining_chain_slots 为 0，优先 skip_response
- 当 supreme_power_enabled=true 时，调度器拥有至高权限：可以无视 terminated、cooldown_blocked、remaining_chain_slots 等机械限制，必要时强行继续对话。
- 如果只是不确定，选择 skip_response

## 决策规则

### reply - 角色适合回复时
- 必须选择一个最合适的角色
- strategy 描述回复的情感/风格

### skip - 不需要任何响应时
- 如：普通闲聊、话题与角色无关、已有其他角色回复

### chain - 多个角色都想参与对话时
- 按参与顺序列出角色名
- 最多3个角色，避免刷屏

### terminate - 终止本轮对话
当出现以下情况时，必须选择 terminate：
1. 多个角色连续互道晚安/告别（如3条以上晚安消息）
2. 对话明显已经结束，继续回复会显得不自然
3. 用户/角色明确表示要结束对话

terminate 时：
- 不再触发任何角色回复
- 设置 chat_state = "terminated"
- 等待新消息触发新一轮对话

### chat_state 状态说明
- **active**: 对话正在进行，可以正常回复
- **winding_down**: 对话接近尾声（如有人说了"先这样"），谨慎回复，避免开启新话题
- **terminated**: 对话已结束，不再回复，等待新消息重置为 active

## 核心回复倾向
{enthusiasm_instruction}

## 可用工具说明
使用 Function Calling，不要在正文里重复工具参数。
"""


_DISPATCHER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "schedule_reply",
            "description": "调度一个最适合的角色回复当前群聊。",
            "parameters": {
                "type": "object",
                "properties": {
                    "character": {"type": "string", "description": "要发言的角色名"},
                    "strategy": {"type": "string", "description": "给角色的简短回复策略"},
                    "reason": {"type": "string", "description": "调度理由"},
                    "chat_state": {
                        "type": "string",
                        "enum": ["active", "winding_down", "terminated"],
                        "description": "执行后的对话状态",
                    },
                },
                "required": ["character", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_chain",
            "description": "按顺序调度多个角色接力发言。只在确实需要多人互动时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "characters": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "按发言顺序排列的角色名",
                    },
                    "strategy": {"type": "string", "description": "给整条对话链的简短策略"},
                    "reason": {"type": "string", "description": "调度理由"},
                    "chat_state": {
                        "type": "string",
                        "enum": ["active", "winding_down", "terminated"],
                    },
                },
                "required": ["characters", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skip_response",
            "description": "不调度任何角色发言。",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "跳过理由"},
                    "chat_state": {
                        "type": "string",
                        "enum": ["active", "winding_down", "terminated"],
                    },
                },
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "terminate_dialogue",
            "description": "终止本轮对话，等待后续新消息重新激活。",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "终止理由"},
                },
                "required": ["reason"],
            },
        },
    },
]


# ── 5个积极性预设 ──
DISPATCHER_PRESETS: dict[str, dict] = {
    "silent": {
        "name": "静默",
        "description": "仅响应直接@和明确提到角色名的消息，几乎不主动发言",
        "fallback_reply_probability": 0.01,
        "enthusiasm_instruction": (
            "你极度克制，几乎从不主动回复。\n"
            "- 只有当消息中明确@了某个角色，或直接提到了角色全名时，才选择 reply\n"
            "- 任何其他情况一律选择 skip，即使话题与角色相关\n"
            "- 宁可错过也不要多说\n"
            "- chain 几乎不会用到，只在极端情况下（多人同时@不同角色）才考虑"
        ),
    },
    "conservative": {
        "name": "保守",
        "description": "倾向于沉默，仅在话题明显相关时回复",
        "fallback_reply_probability": 0.15,
        "enthusiasm_instruction": (
            "你偏向沉默，只在确实有必要时才回复。\n"
            "- 被@或角色名被提及时必须回复\n"
            "- 话题与角色人设高度相关时可以回复\n"
            "- 普通闲聊、话题模糊、已有其他角色回复时选择 skip\n"
            "- 当不确定是否应该回复时，选择 skip\n"
            "- chain 最多1-2个角色"
        ),
    },
    "balanced": {
        "name": "平衡",
        "description": "适度参与对话，平衡活跃度与克制",
        "fallback_reply_probability": 0.3,
        "enthusiasm_instruction": (
            "你适度参与群聊。\n"
            "- 被@或角色名被提及时必须回复\n"
            "- 话题与角色相关时积极回复\n"
            "- 有趣的话题也可以适当参与\n"
            "- 但不要每条消息都回复，留出空间\n"
            "- 宁可少回复，也不要刷屏\n"
            "- 当不确定是否应该回复时，选择 skip"
        ),
    },
    "active": {
        "name": "积极",
        "description": "主动参与对话，乐于接话和互动",
        "fallback_reply_probability": 0.5,
        "enthusiasm_instruction": (
            "你乐于参与群聊，喜欢和大家互动。\n"
            "- 被@或角色名被提及时必须回复\n"
            "- 大部分话题都可以参与，尤其是有趣的、有梗的对话\n"
            "- 即使没有被直接提到，也可以自然地接话\n"
            "- 可以适当使用 chain 让多个角色参与讨论\n"
            "- 但仍然要注意不要连续刷屏，给其他人留空间"
        ),
    },
    "enthusiastic": {
        "name": "热情",
        "description": "非常积极活跃，几乎不放过任何对话机会",
        "fallback_reply_probability": 0.8,
        "enthusiasm_instruction": (
            "你非常热情，积极回应每一条消息。\n"
            "- 被@或角色名被提及时必须回复\n"
            "- 几乎所有话题都要参与，展现角色个性\n"
            "- 主动接话、调侃、关心，让群聊充满活力\n"
            "- 积极使用 chain 让多个角色互动\n"
            "- 只有在对话明显结束（晚安、再见等）时才 terminate\n"
            "- 即使话题不太相关，也可以用轻松的方式参与"
        ),
    },
}


def get_dispatcher_prompt(characters_info: str, message_count: int,
                          recent_messages: str, chat_state: str,
                          scheduler_state: str) -> str:
    """根据配置构建调度器提示词"""
    custom_prompt = config.orchestrator.dispatcher.dispatcher_prompt
    preset_key = config.orchestrator.dispatcher.dispatcher_preset

    if custom_prompt:
        # 用户自定义提示词：直接使用（只做变量替换）
        return custom_prompt.format(
            characters_info=characters_info,
            message_count=message_count,
            recent_messages=recent_messages,
            chat_state=chat_state,
            scheduler_state=scheduler_state,
        )

    # 使用预设
    preset = DISPATCHER_PRESETS.get(preset_key, DISPATCHER_PRESETS["balanced"])
    return _DISPATCHER_PROMPT_TEMPLATE.format(
        characters_info=characters_info,
        message_count=message_count,
        recent_messages=recent_messages,
        chat_state=chat_state,
        scheduler_state=scheduler_state,
        enthusiasm_instruction=preset["enthusiasm_instruction"],
    )


def get_preset_fallback_probability(preset_key: str) -> float:
    """获取预设的 fallback 回复概率"""
    preset = DISPATCHER_PRESETS.get(preset_key)
    if preset:
        return preset["fallback_reply_probability"]
    return config.orchestrator.dispatcher.fallback_reply_probability


# 终止关键词
TERMINATE_KEYWORDS = ["晚安", "睡了", "拜拜", "再见", "不聊了", "先这样", "撤了", "走了"]
RESET_KEYWORDS = ["早安", "早上好", "在吗", "有人吗", "大家好", "嗨"]


class Dispatcher:
    """消息分配器：用辅助模型智能决策"""
    
    def __init__(self):
        self._chat_states: dict[str, str] = {}  # group_id → chat_state
        self._locks: dict[str, asyncio.Lock] = {}
        self._available_characters: list[str] = []
    
    def set_available_characters(self, characters: list[str]):
        """设置可用角色列表"""
        self._available_characters = characters

    def _get_qq_name_map(self) -> dict[str, str]:
        try:
            from backend.orchestrator import orchestrator
            if not orchestrator._ws_server:
                return {}
            qq_name_map = {}
            for port, conn in orchestrator._ws_server.connections.items():
                if conn and conn.qq_name:
                    char = orchestrator._assignments.get(port)
                    if char:
                        qq_name_map[conn.qq_name] = char
            return qq_name_map
        except Exception as e:
            logger.debug(f"Failed to build QQ name map for dispatcher: {e}")
            return {}

    def _resolve_message_mentions(self, message: BufferedMessage) -> str:
        try:
            from backend.orchestrator import orchestrator
            return resolve_at_mentions(
                message.raw_message,
                self._available_characters,
                orchestrator._bot_qq_map,
                self._get_qq_name_map(),
            )
        except Exception as e:
            logger.debug(f"Failed to resolve mentions for dispatcher: {e}")
            return message.raw_message

    def _explicit_mention_targets(self, messages: list[BufferedMessage]) -> list[str]:
        """Return all explicitly mentioned characters found in buffered messages.

        OneBot at segments carry the QQ id and are more reliable than raw text,
        so they are checked before fallback textual @ matching.
        """
        try:
            from backend.orchestrator import orchestrator
            bot_qq_map = orchestrator._bot_qq_map
        except Exception as e:
            logger.debug(f"Failed to read bot QQ map for dispatcher: {e}")
            bot_qq_map = {}

        qq_name_map = self._get_qq_name_map()
        seen = set()
        targets = []

        def add_target(char_name: Optional[str]):
            if char_name and char_name in self._available_characters and char_name not in seen:
                seen.add(char_name)
                targets.append(char_name)

        for msg in messages:
            for seg in msg.message_segments:
                if seg.get("type") != "at":
                    continue
                qq = str(seg.get("data", {}).get("qq", ""))
                add_target(bot_qq_map.get(qq))

            raw_message = msg.raw_message or ""
            for char_name in self._available_characters:
                aliases = {char_name}
                if len(char_name) > 2:
                    aliases.add(char_name[-2:])
                aliases.update(name for name, mapped in qq_name_map.items() if mapped == char_name)
                aliases.update(qq for qq, mapped in bot_qq_map.items() if mapped == char_name)
                for alias in aliases:
                    if alias and re.search(f"@{re.escape(alias)}(?=[：:，,。.！!？? \\t\\n]|$)", raw_message):
                        add_target(char_name)
                        break

        return targets

    def _explicit_mention_target(self, messages: list[BufferedMessage]) -> Optional[str]:
        """Return the latest explicitly mentioned character, if any."""
        targets = self._explicit_mention_targets(messages)
        return targets[-1] if targets else None
    
    async def on_flush(self, group_id: str, messages: list[BufferedMessage]):
        """消息缓冲区 flush 回调"""
        if not config.orchestrator.dispatcher.enabled:
            # 如果分配器禁用，使用简单逻辑
            await self._fallback_decision(group_id, messages)
            return
        
        # 获取锁
        if group_id not in self._locks:
            self._locks[group_id] = asyncio.Lock()
        
        async with self._locks[group_id]:
            try:
                # 1. 检查对话状态
                chat_state = self._chat_states.get(group_id, "active")
                
                # 如果已终止，检查是否需要重置
                if chat_state == "terminated" and not config.orchestrator.dispatcher.supreme_power:
                    if self._should_reset_chat(messages):
                        self._chat_states[group_id] = "active"
                        chat_state = "active"
                        logger.info(f"Chat state reset to active for group {group_id}")
                    else:
                        logger.debug(f"Chat terminated for group {group_id}, skipping")
                        return
                
                # 2. 调用辅助模型分析
                decision = await self._analyze(messages, chat_state)
                
                # 3. 更新对话状态
                self._chat_states[group_id] = decision.chat_state
                
                # 4. 执行决策
                await self._execute_decision(group_id, decision, messages)
                
            except Exception as e:
                logger.error(f"Dispatcher error for group {group_id}: {e}")
                # Fallback 到简单判断
                if config.orchestrator.dispatcher.fallback_to_simple:
                    await self._fallback_decision(group_id, messages)
    
    async def _analyze(self, messages: list[BufferedMessage], chat_state: str) -> DispatcherDecision:
        """调用辅助模型分析"""
        from backend.llm_client import llm_client
        from backend.character_manager import character_manager
        
        # 构建角色信息
        characters_info = []
        for char_name in self._available_characters:
            prompt = character_manager.get_system_prompt(char_name)
            # 取前200字作为摘要
            summary = prompt[:200] + "..." if len(prompt) > 200 else prompt
            characters_info.append(f"- {char_name}: {summary}")
        characters_text = "\n".join(characters_info)
        
        # 构建消息上下文
        recent_messages = []
        for msg in messages[-20:]:  # 最多取20条
            recent_messages.append(f"[{msg.sender_name}]: {self._resolve_message_mentions(msg)}")
        messages_text = "\n".join(recent_messages)
        scheduler_state = self._build_scheduler_state(messages)
        
        # 构建完整 prompt
        prompt = get_dispatcher_prompt(
            characters_info=characters_text,
            message_count=len(messages),
            recent_messages=messages_text,
            chat_state=chat_state,
            scheduler_state=scheduler_state,
        )
        
        # 调用辅助模型
        try:
            response = await llm_client.generate_dispatcher_decision(
                system_prompt=prompt,
                messages=[],
                temperature=config.orchestrator.dispatcher.assistant_temperature,
                max_tokens=config.orchestrator.dispatcher.assistant_max_tokens,
                tools=_DISPATCHER_TOOLS,
                tool_choice="required",
            )
            
            if not response or not response.tool_calls:
                logger.warning("Assistant model returned no dispatcher tool call")
                return self._create_skip_decision("辅助模型无响应")
            
            group_id = messages[-1].group_id if messages else ""
            decision = self._parse_tool_call(response.tool_calls[0])
            mention_targets = self._explicit_mention_targets(messages)
            if len(mention_targets) == 1:
                decision = DispatcherDecision(
                    action="reply",
                    character=mention_targets[0],
                    strategy=decision.strategy,
                    reason=f"明确@了{mention_targets[0]}",
                    chat_state="active",
                )
            elif len(mention_targets) > 1:
                decision = DispatcherDecision(
                    action="chain",
                    characters=mention_targets,
                    strategy=decision.strategy,
                    reason=f"缓冲区内明确@了多个角色: {'、'.join(mention_targets)}",
                    chat_state="active",
                )
            decision = self._apply_mechanical_constraints(decision, group_id)
            logger.info(f"Dispatcher decision: action={decision.action}, "
                       f"character={decision.character}, reason={decision.reason}")
            return decision
            
        except Exception as e:
            logger.error(f"Assistant model error: {e}")
            return self._create_skip_decision(f"辅助模型错误: {e}")

    def _build_scheduler_state(self, messages: list[BufferedMessage]) -> str:
        """把机械参数和当前可调度状态显式交给调度模型。"""
        auto_cfg = config.orchestrator.auto_dialogue
        now = datetime.now()
        lines = [
            f"group_reply_probability={config.orchestrator.group_reply_probability}",
            f"supreme_power_enabled={config.orchestrator.dispatcher.supreme_power}",
            f"fallback_reply_probability={get_preset_fallback_probability(config.orchestrator.dispatcher.dispatcher_preset)}",
            f"auto_dialogue_enabled={auto_cfg.enabled}",
            f"chain_length={auto_cfg.chain_length}",
            f"cooldown_ms={auto_cfg.cooldown_ms}",
            f"trigger_probability={auto_cfg.trigger_probability}",
            f"initiation_probability={auto_cfg.initiation_probability}",
            f"initiation_interval_ms={auto_cfg.initiation_interval_ms}",
        ]

        group_id = messages[-1].group_id if messages else ""
        try:
            from backend.orchestrator import orchestrator
            for char_name in self._available_characters:
                chain_count = orchestrator._chain_counters[group_id][char_name]
                remaining_slots = max(auto_cfg.chain_length - chain_count, 0)
                last_reply = orchestrator._last_ai_reply_time[group_id][char_name]
                elapsed_ms = (now - last_reply).total_seconds() * 1000
                cooldown_blocked = elapsed_ms < auto_cfg.cooldown_ms
                lines.append(
                    f"character={char_name}, chain_count={chain_count}, "
                    f"remaining_chain_slots={remaining_slots}, cooldown_blocked={cooldown_blocked}"
                )
        except Exception as e:
            logger.debug(f"Failed to build per-character scheduler state: {e}")

        return "\n".join(lines)

    def _parse_tool_call(self, tool_call) -> DispatcherDecision:
        """把调度器专属 Function Calling 转为内部决策对象。"""
        name = tool_call.function_name
        args = tool_call.arguments or {}

        if name == "schedule_reply":
            return DispatcherDecision(
                action="reply",
                character=args.get("character"),
                strategy=args.get("strategy"),
                reason=args.get("reason", ""),
                chat_state=args.get("chat_state", "active"),
            )
        if name == "schedule_chain":
            return DispatcherDecision(
                action="chain",
                characters=args.get("characters", []),
                strategy=args.get("strategy"),
                reason=args.get("reason", ""),
                chat_state=args.get("chat_state", "active"),
            )
        if name == "terminate_dialogue":
            return DispatcherDecision(
                action="terminate",
                reason=args.get("reason", ""),
                chat_state="terminated",
            )
        if name == "skip_response":
            return DispatcherDecision(
                action="skip",
                reason=args.get("reason", ""),
                chat_state=args.get("chat_state", "active"),
            )

        return self._create_skip_decision(f"未知调度函数: {name}")

    def _apply_mechanical_constraints(self, decision: DispatcherDecision, group_id: str = "") -> DispatcherDecision:
        """用链长、冷却和可用角色做硬约束兜底。"""
        if config.orchestrator.dispatcher.supreme_power:
            return self._apply_supreme_constraints(decision)

        if decision.action == "reply":
            if not self._is_schedulable(decision.character, group_id):
                return self._create_skip_decision(f"角色不可调度或处于冷却: {decision.character}")
            return decision

        if decision.action == "chain":
            auto_cfg = config.orchestrator.auto_dialogue
            max_chain = max(1, min(auto_cfg.chain_length, 3))
            seen = set()
            characters = []
            for char_name in decision.characters:
                if char_name in seen or not self._is_schedulable(char_name, group_id):
                    continue
                seen.add(char_name)
                characters.append(char_name)
                if len(characters) >= max_chain:
                    break
            if not characters:
                return self._create_skip_decision("链式调度无可用角色")
            decision.characters = characters
            return decision

        return decision

    def _apply_supreme_constraints(self, decision: DispatcherDecision) -> DispatcherDecision:
        """Supreme mode only validates character existence, not timing or chain limits."""
        if decision.action == "reply":
            if not decision.character or decision.character not in self._available_characters:
                return self._create_skip_decision(f"未知角色: {decision.character}")
            return decision

        if decision.action == "chain":
            seen = set()
            characters = []
            for char_name in decision.characters:
                if char_name in seen or char_name not in self._available_characters:
                    continue
                seen.add(char_name)
                characters.append(char_name)
            if not characters:
                return self._create_skip_decision("链式调度无可用角色")
            decision.characters = characters
            return decision

        return decision

    def _is_schedulable(self, character: Optional[str], group_id: str = "") -> bool:
        if not character or character not in self._available_characters:
            return False
        if not config.orchestrator.auto_dialogue.enabled:
            return True
        try:
            from backend.orchestrator import orchestrator
            auto_cfg = config.orchestrator.auto_dialogue
            if group_id and orchestrator._chain_counters[group_id][character] >= auto_cfg.chain_length:
                return False
            if group_id:
                last_reply = orchestrator._last_ai_reply_time[group_id][character]
                elapsed_ms = (datetime.now() - last_reply).total_seconds() * 1000
                if elapsed_ms < auto_cfg.cooldown_ms:
                    return False
        except Exception as e:
            logger.debug(f"Failed to check schedulable state: {e}")
        return True
    
    def _parse_decision(self, response: str) -> DispatcherDecision:
        """解析辅助模型的 JSON 输出"""
        try:
            # 尝试提取 JSON
            json_str = response
            
            # 如果 response 包含 ```json ... ```，提取其中的 JSON
            if "```json" in response:
                start = response.index("```json") + 7
                end = response.index("```", start)
                json_str = response[start:end].strip()
            elif "```" in response:
                start = response.index("```") + 3
                end = response.index("```", start)
                json_str = response[start:end].strip()
            
            data = json.loads(json_str)
            
            # 解析 tool_calls
            tool_calls = []
            for tc in data.get("tool_calls", []):
                tool_calls.append(ToolCall(
                    id=f"dispatcher_{datetime.now().timestamp()}",
                    function_name=tc.get("function", ""),
                    arguments=tc.get("arguments", {}),
                ))
            
            return DispatcherDecision(
                action=data.get("action", "skip"),
                character=data.get("character"),
                characters=data.get("characters", []),
                tool_calls=tool_calls,
                strategy=data.get("strategy"),
                reason=data.get("reason", ""),
                chat_state=data.get("chat_state", "active"),
            )
            
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"Failed to parse assistant response: {e}\nResponse: {response}")
            return self._create_skip_decision(f"JSON解析失败: {e}")
    
    def _create_skip_decision(self, reason: str) -> DispatcherDecision:
        """创建 skip 决策"""
        return DispatcherDecision(
            action="skip",
            reason=reason,
            chat_state=self._chat_states.get("default", "active"),
        )
    
    async def _execute_decision(self, group_id: str, decision: DispatcherDecision, 
                                 messages: list[BufferedMessage]):
        """执行决策"""
        from backend.orchestrator import orchestrator
        
        # 先将消息记录到 session
        await orchestrator.handle_buffered_messages(group_id, messages)
        
        if decision.action == "skip":
            logger.debug(f"Skipping reply for group {group_id}: {decision.reason}")
            return
        
        elif decision.action == "terminate":
            logger.info(f"Chat terminated for group {group_id}: {decision.reason}")
            return
        
        elif decision.action == "tool_only":
            # 只执行 tool calls
            if decision.tool_calls and decision.character:
                port = orchestrator._find_port_for_character(decision.character)
                if port:
                    await orchestrator._execute_tool_calls(
                        decision.tool_calls, group_id, port
                    )
        
        elif decision.action == "reply":
            # 执行 tool calls（如果有）
            if decision.tool_calls and decision.character:
                port = orchestrator._find_port_for_character(decision.character)
                if port:
                    await orchestrator._execute_tool_calls(
                        decision.tool_calls, group_id, port
                    )
            
            # 触发角色回复
            if decision.character:
                await orchestrator.execute_reply(
                    group_id=group_id,
                    character_name=decision.character,
                    strategy=decision.strategy,
                    trigger_message=messages[-1] if messages else None,
                )
        
        elif decision.action == "chain":
            # 多角色依次回复
            chain_characters = decision.characters if config.orchestrator.dispatcher.supreme_power else decision.characters[:3]
            for i, char in enumerate(chain_characters):
                # 检查对话状态是否已终止
                if self._chat_states.get(group_id) == "terminated" and not config.orchestrator.dispatcher.supreme_power:
                    logger.info(f"Chain interrupted: chat terminated for group {group_id}")
                    break
                
                await orchestrator.execute_reply(
                    group_id=group_id,
                    character_name=char,
                    strategy=decision.strategy,
                    trigger_message=messages[-1] if messages else None,
                )
                
                # 角色间添加延迟
                if i < len(chain_characters) - 1:
                    await asyncio.sleep(2)  # 2秒间隔
    
    async def _fallback_decision(self, group_id: str, messages: list[BufferedMessage]):
        """辅助模型失败时的 fallback"""
        from backend.orchestrator import orchestrator
        
        if not messages:
            return
        
        # 先将消息记录到 session
        await orchestrator.handle_buffered_messages(group_id, messages)

        mention_targets = self._explicit_mention_targets(messages)
        if mention_targets:
            for char_name in mention_targets[:3]:
                await orchestrator.execute_reply(
                    group_id=group_id,
                    character_name=char_name,
                    trigger_message=messages[-1],
                )
            return

        last_msg = messages[-1]
        
        # 检查是否有 @角色名 或关键词
        for char_name in self._available_characters:
            if f"@{char_name}" in last_msg.raw_message or char_name in last_msg.raw_message:
                await orchestrator.execute_reply(
                    group_id=group_id,
                    character_name=char_name,
                    trigger_message=last_msg,
                )
                return
        
        # 概率判断（使用预设的 fallback 概率）
        fallback_prob = get_preset_fallback_probability(
            config.orchestrator.dispatcher.dispatcher_preset
        )
        if random.random() < fallback_prob:
            if self._available_characters:
                char = random.choice(self._available_characters)
                await orchestrator.execute_reply(
                    group_id=group_id,
                    character_name=char,
                    trigger_message=last_msg,
                )
    
    def _should_reset_chat(self, messages: list[BufferedMessage]) -> bool:
        """判断是否应该重置对话状态"""
        if not messages:
            return False
        
        last_msg = messages[-1].raw_message
        
        # 如果新消息包含重置关键词，且不包含终止关键词
        has_reset = any(kw in last_msg for kw in RESET_KEYWORDS)
        has_terminate = any(kw in last_msg for kw in TERMINATE_KEYWORDS)
        
        return has_reset and not has_terminate
    
    def get_chat_state(self, group_id: str) -> str:
        """获取群的对话状态"""
        return self._chat_states.get(group_id, "active")
    
    def set_chat_state(self, group_id: str, state: str):
        """设置群的对话状态"""
        self._chat_states[group_id] = state
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "chat_states": self._chat_states.copy(),
            "available_characters": self._available_characters.copy(),
        }

    async def dispatch_emoji(self, group_id: str, character_name: str,
                             pending_emojis: list[dict], orchestrator,
                             context_message_id: int = 0):
        """选择账号执行表情回应。

        优先用发起角色的账号；如果该账号不可用，回退到任意可用账号。
        """
        if not pending_emojis:
            return

        port = orchestrator._find_port_for_character(character_name)
        if not port or not orchestrator._ws_server or not orchestrator._ws_server.get_connection(port):
            # 回退：找任意已连接的端口
            if orchestrator._ws_server:
                for p, conn in orchestrator._ws_server.connections.items():
                    if conn:
                        port = p
                        break
        if not port:
            logger.warning(f"[EMOJI DISPATCH] No available port for emoji reaction")
            return

        for emoji_call in pending_emojis:
            raw_id = emoji_call.get("message_id_self") or emoji_call["message_id"]
            emoji_id = emoji_call["emoji_id"]
            if emoji_call.get("message_id_self"):
                resolved_id = orchestrator._resolve_message_id_self_for_port(group_id, raw_id, port)
            else:
                resolved_id = orchestrator._resolve_message_id_for_port(group_id, raw_id, port)
            result = await orchestrator._execute_emoji_on_port(port, group_id, resolved_id, emoji_id)
            logger.info(f"[EMOJI DISPATCH] character={character_name} port={port} result={result}")


# 全局实例
_dispatcher: Optional[Dispatcher] = None


def init_dispatcher() -> Dispatcher:
    """初始化全局分配器"""
    global _dispatcher
    _dispatcher = Dispatcher()
    return _dispatcher


def get_dispatcher() -> Optional[Dispatcher]:
    """获取全局分配器"""
    return _dispatcher
