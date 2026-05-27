"""消息分配器：用辅助模型智能决策如何响应群聊消息"""

import asyncio
import json
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from backend.message_buffer import BufferedMessage
from backend.config import config
from backend.utils import get_logger

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


# 辅助模型分析 Prompt
DISPATCHER_PROMPT = """你是一个群聊消息分析助手，负责决定如何响应群聊消息。

## 可用角色及其人设摘要
{characters_info}

## 最近消息（{message_count}条）
{recent_messages}

## 当前对话状态
{chat_state}

## 分析任务

请分析最近的群聊消息，决定最佳响应方式。考虑：
1. 是否有人直接@某个角色或提到角色名
2. 话题是否与某个角色的人设/兴趣相关
3. 对话的氛围和情感
4. 是否有需要管理的违规行为
5. 沉默是否比发言更好

## 输出格式（JSON）

```json
{{
  "action": "reply|tool_only|skip|chain|terminate",
  "character": "角色名（reply/tool_only时必填）",
  "characters": ["角色名1", "角色名2"]（chain时必填）,
  "tool_calls": [
    {{
      "function": "set_group_ban|set_msg_emoji_like",
      "arguments": {{}}
    }}
  ],
  "strategy": "回复策略描述（如：轻松调侃、严肃警告、关心询问等）",
  "reason": "决策理由",
  "chat_state": "active|winding_down|terminated"
}}
```

## 决策规则

### reply - 角色适合回复时
- 必须选择一个最合适的角色
- 可附带 tool_calls（如回复的同时发表情）
- strategy 描述回复的情感/风格

### tool_only - 只需执行操作不需要回复时
- 如：对某条消息发表情回应、对违规用户禁言
- 必须在 tool_calls 中指定具体操作

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

## 重要原则
- 宁可少回复，也不要刷屏
- 当不确定是否应该回复时，选择 skip
- 对话自然结束时，主动 terminate 比被动停止更好

## 可用工具说明
1. set_group_ban - 禁言用户
   - 参数：group_id(群号), user_id(QQ号), duration(秒数，默认600)
   - 只有在用户持续恶意骚扰、发送违规内容时才可使用

2. set_msg_emoji_like - 给消息添加表情回应
   - 参数：message_id(消息ID), emoji_id(表情ID)
   - 可用表情：128027(🐛下头)、128053(🐵无语)、128051(🐳喜欢)
"""


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
                if chat_state == "terminated":
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
            recent_messages.append(f"[{msg.sender_name}]: {msg.raw_message}")
        messages_text = "\n".join(recent_messages)
        
        # 构建完整 prompt
        prompt = DISPATCHER_PROMPT.format(
            characters_info=characters_text,
            message_count=len(messages),
            recent_messages=messages_text,
            chat_state=chat_state,
        )
        
        # 调用辅助模型
        try:
            response = await llm_client.generate_assistant_response(
                system_prompt=prompt,
                messages=[],
                temperature=config.orchestrator.dispatcher.assistant_temperature,
                max_tokens=config.orchestrator.dispatcher.assistant_max_tokens,
            )
            
            if not response:
                logger.warning("Assistant model returned empty response")
                return self._create_skip_decision("辅助模型无响应")
            
            # 解析 JSON
            decision = self._parse_decision(response)
            logger.info(f"Dispatcher decision: action={decision.action}, "
                       f"character={decision.character}, reason={decision.reason}")
            return decision
            
        except Exception as e:
            logger.error(f"Assistant model error: {e}")
            return self._create_skip_decision(f"辅助模型错误: {e}")
    
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
                    character=decision.character,
                    strategy=decision.strategy,
                    trigger_message=messages[-1] if messages else None,
                )
        
        elif decision.action == "chain":
            # 多角色依次回复
            for i, char in enumerate(decision.characters[:3]):  # 最多3个角色
                # 检查对话状态是否已终止
                if self._chat_states.get(group_id) == "terminated":
                    logger.info(f"Chain interrupted: chat terminated for group {group_id}")
                    break
                
                await orchestrator.execute_reply(
                    group_id=group_id,
                    character=char,
                    strategy=decision.strategy,
                    trigger_message=messages[-1] if messages else None,
                )
                
                # 角色间添加延迟
                if i < len(decision.characters) - 1:
                    await asyncio.sleep(2)  # 2秒间隔
    
    async def _fallback_decision(self, group_id: str, messages: list[BufferedMessage]):
        """辅助模型失败时的 fallback"""
        from backend.orchestrator import orchestrator
        
        if not messages:
            return
        
        # 先将消息记录到 session
        await orchestrator.handle_buffered_messages(group_id, messages)
        
        last_msg = messages[-1]
        
        # 检查是否有 @角色名 或关键词
        for char_name in self._available_characters:
            if f"@{char_name}" in last_msg.raw_message or char_name in last_msg.raw_message:
                await orchestrator.execute_reply(
                    group_id=group_id,
                    character=char_name,
                    trigger_message=last_msg,
                )
                return
        
        # 概率判断
        if random.random() < config.orchestrator.dispatcher.fallback_reply_probability:
            if self._available_characters:
                char = random.choice(self._available_characters)
                await orchestrator.execute_reply(
                    group_id=group_id,
                    character=char,
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
