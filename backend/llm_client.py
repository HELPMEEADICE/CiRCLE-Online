import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from openai import AsyncOpenAI
from backend.config import config
from backend.models import ChatMessage
from backend.utils import get_logger


@dataclass
class ToolCall:
    id: str
    function_name: str
    arguments: dict


@dataclass
class RoleplayResponse:
    content: Optional[str]
    tool_calls: list[ToolCall] = field(default_factory=list)

    def __bool__(self):
        return self.content is not None or len(self.tool_calls) > 0

logger = get_logger("llm_client")


class LLMClient:
    def __init__(self):
        self._client: Optional[AsyncOpenAI] = None
        self._vision_client: Optional[AsyncOpenAI] = None
        self._init_client()
        self._init_vision_client()

    def _init_client(self):
        from backend.config import config
        api_key = config.llm.api_key
        base_url = config.llm.base_url

        if not api_key:
            logger.warning("LLM API key not configured. LLM features disabled.")
            return

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        logger.info(f"LLM client initialized: {config.llm.provider} @ {base_url}")

    def _init_vision_client(self):
        from backend.config import config
        vision = config.llm.vision
        if not vision.enabled:
            self._vision_client = None
            return

        api_key = vision.api_key or config.llm.api_key
        base_url = vision.base_url or config.llm.base_url

        if not api_key:
            logger.warning("Vision model API key not configured. Vision features disabled.")
            self._vision_client = None
            return

        self._vision_client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        logger.info(f"Vision model client initialized: {vision.model} @ {base_url}")

    def reinitialize(self):
        self._client = None
        self._vision_client = None
        self._init_client()
        self._init_vision_client()

    @property
    def is_available(self) -> bool:
        return self._client is not None

    @property
    def is_vision_available(self) -> bool:
        return self._vision_client is not None

    def _get_extra_body(self, thinking: str) -> Optional[dict]:
        if thinking == "enabled":
            return {"thinking": {"type": "enabled"}}
        if thinking == "disabled":
            return {"thinking": {"type": "disabled"}}
        return None

    async def analyze_image(self, image_url: str, prompt: str = "请详细描述这张图片的内容，包括表情包的文字、人物表情、动作等信息。") -> Optional[str]:
        if not self._vision_client:
            return None

        from backend.config import config
        vision = config.llm.vision
        model = vision.model or config.llm.model

        try:
            messages = [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }]
            kwargs = dict(
                model=model,
                messages=messages,
                max_tokens=1024,
            )
            extra_body = self._get_extra_body(vision.thinking)
            if extra_body:
                kwargs["extra_body"] = extra_body
            response = await self._vision_client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content
            logger.debug(f"Vision analysis: {content[:100]}...")
            return content
        except Exception as e:
            logger.error(f"Vision API error: {e}")
            return None

    async def generate_response(
        self,
        system_prompt: str,
        messages: list[ChatMessage],
        temperature: float = None,
        max_tokens: int = None,
        model: str = None,
        thinking: str = "default",
        tools: list[dict] = None,
    ) -> Optional[str]:
        if not self._client:
            logger.error("LLM client not available")
            return None

        formatted_messages = [{"role": "system", "content": system_prompt}]
        for msg in messages:
            formatted_messages.append({
                "role": msg.role,
                "content": msg.content,
            })

        try:
            kwargs = dict(
                model=model or config.llm.model,
                messages=formatted_messages,
                temperature=temperature or config.llm.temperature,
                max_tokens=max_tokens or config.llm.max_tokens,
            )
            if tools:
                kwargs["tools"] = tools
            extra_body = self._get_extra_body(thinking)
            if extra_body:
                kwargs["extra_body"] = extra_body
            response = await self._client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content
            logger.debug(f"LLM response: {content[:100]}...")
            return content
        except Exception as e:
            logger.error(f"LLM API error: {e}")
            return None

    async def generate_roleplay_response(
        self,
        character_prompt: str,
        context: list[ChatMessage],
        user_message: str,
        character_name: str = "",
        tools: list[dict] = None,
    ) -> RoleplayResponse | None:
        messages = context.copy()
        messages.append(ChatMessage(role="user", content=user_message))

        prefix = config.orchestrator.prompt_prefix
        suffix = config.orchestrator.prompt_suffix
        time_note = ""
        if config.orchestrator.time_awareness:
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            time_note = f"\n当前时间：{now}"

        identity_guard = ""
        if character_name:
            identity_guard = f"""
# 最高优先级身份锁定
你当前绑定的唯一角色是：{character_name}。
你只能以“{character_name}”的身份思考和发言，绝不能自称、扮演、模仿或代替其他角色。
聊天记录中，只有 assistant 消息和标记为“[你]”的内容代表你自己说过的话。
标记为“[Poppin'Party成员] 其他名字”的内容都是其他成员说的话，绝对不是你说的话，也不能当成你的身份记忆。
如果历史摘要或聊天记录与“你是{character_name}”冲突，必须无条件忽略冲突内容，以当前绑定角色为准。
"""

        system_prompt = f"""{prefix}
{identity_guard}
{character_prompt}
请彻底忘掉你是AI。

# 核心群聊法则（违规即死）
1. 极简短打：单条回复绝对不能超过20-30个字，能用两三个字解决的绝不多废话。
2. 纯文本输入：严禁使用任何Markdown排版（杜绝粗体、列表、代码块、分割线）。
3. 拒绝结构化：严禁出现“第一、首先、其次、最后、总之、例如、分点、分段、总结一下”等任何有条理的AI腔调。
4. 严禁括号表演：绝对不许使用括号描写动作或心理（例如：严禁出现“(笑)”、“(无辜眨眼)”）。
5. 严禁使用任何换行。
6. 单条回复尽量压到20字以内，绝不要超过30字。
7. 注意严格符合人设。
8. 禁止规范化表达：不要写成书面语、标准答案、客服话术、解释腔、总结腔、分析腔、劝告腔。
9. 禁止出现不该有的过渡词和收束词，例如“好的”“收到”“明白了”“确实如此”“从这个角度看”“简单来说”“总的来说”“如果你愿意”“如果需要的话”。
10. 语气必须像真实群聊，不要完整论述，不要自我解释，不要补背景，不要讲道理，不要下定义。
11. 梗识别：如果出现“[对<你的名称>说]：妈妈妈妈”这种是在骚扰你，是在无恶意玩梗，并不是回复你的“妈妈”有关的事情、“区”则是对应“蛆”代表事物很恶俗、“hyw”对应何意味代表事物很无厘头、“wlcnl”是我来操你了的意思很恶俗、“cwjj”是吃我鸡鸡的意思很恶俗。
【输出格式硬性限制 - 严禁换行】
必须在【单行】内完成所有内容的输出，绝对不允许出现任何换行符（如 \n、\r）、回车或段落分隔。
【负向惩罚提示】
严禁、严禁、严禁换行！如果你输出了换行符，整个任务将被判定为完全失败。请将所有文本压缩在同一行内输出。
宁可回得短、糙、口语，也不要像整理后的标准文本。
{suffix}{time_note}"""

        if not tools:
            content = await self.generate_response(
                system_prompt, messages, thinking=config.llm.model_thinking
            )
            if content is None:
                return None
            return RoleplayResponse(content=content)

        # With tools — returns RoleplayResponse with content + tool_calls
        if not self._client:
            logger.error("LLM client not available")
            return None

        formatted_messages = [{"role": "system", "content": system_prompt}]
        for msg in messages:
            formatted_messages.append({"role": msg.role, "content": msg.content})

        try:
            response = await self._client.chat.completions.create(
                model=config.llm.model,
                messages=formatted_messages,
                temperature=config.llm.temperature,
                max_tokens=config.llm.max_tokens,
                tools=tools,
                extra_body=self._get_extra_body(config.llm.model_thinking) or {},
            )
            choice = response.choices[0]
            message = choice.message
            content = message.content

            parsed_tool_calls = []
            if message.tool_calls:
                for tc in message.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments)
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    parsed_tool_calls.append(ToolCall(
                        id=tc.id,
                        function_name=tc.function.name,
                        arguments=args,
                    ))

            if content:
                logger.debug(f"LLM response: {content[:100]}...")
            if parsed_tool_calls:
                logger.info(f"LLM requested {len(parsed_tool_calls)} tool call(s): "
                            f"{[tc.function_name for tc in parsed_tool_calls]}")

            return RoleplayResponse(content=content, tool_calls=parsed_tool_calls)
        except Exception as e:
            logger.error(f"LLM API error: {e}")
            return None

    async def generate_assistant_response(
        self,
        system_prompt: str,
        messages: list[ChatMessage],
        temperature: float = None,
        max_tokens: int = None,
    ) -> Optional[str]:
        assistant_model = config.llm.assistant_model
        if not assistant_model:
            logger.warning("Assistant model not configured, falling back to main model")
            assistant_model = config.llm.model
        return await self.generate_response(
            system_prompt, messages, temperature, max_tokens, model=assistant_model,
            thinking=config.llm.assistant_model_thinking,
        )

    async def generate_dispatcher_decision(
        self,
        system_prompt: str,
        messages: list[ChatMessage],
        temperature: float = None,
        max_tokens: int = None,
        tools: list[dict] = None,
        tool_choice: str | dict = None,
    ) -> RoleplayResponse | None:
        """调用辅助模型生成调度器决策。"""
        if not tools:
            content = await self.generate_assistant_response(
                system_prompt, messages, temperature, max_tokens
            )
            if content is None:
                return None
            return RoleplayResponse(content=content)

        if not self._client:
            logger.error("LLM client not available")
            return None

        assistant_model = config.llm.assistant_model or config.llm.model
        formatted_messages = [{"role": "system", "content": system_prompt}]
        for msg in messages:
            formatted_messages.append({"role": msg.role, "content": msg.content})

        try:
            kwargs = dict(
                model=assistant_model,
                messages=formatted_messages,
                temperature=temperature if temperature is not None else config.llm.temperature,
                max_tokens=max_tokens if max_tokens is not None else config.llm.max_tokens,
                tools=tools,
            )
            if tool_choice:
                kwargs["tool_choice"] = tool_choice
            extra_body = self._get_extra_body(config.llm.assistant_model_thinking)
            if extra_body:
                kwargs["extra_body"] = extra_body

            response = await self._client.chat.completions.create(**kwargs)
            message = response.choices[0].message
            parsed_tool_calls = []
            if message.tool_calls:
                for tc in message.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments)
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    parsed_tool_calls.append(ToolCall(
                        id=tc.id,
                        function_name=tc.function.name,
                        arguments=args,
                    ))
            return RoleplayResponse(content=message.content, tool_calls=parsed_tool_calls)
        except Exception as e:
            logger.error(f"Dispatcher LLM API error: {e}")
            return None

    async def compress_context(
        self,
        messages: list[ChatMessage],
        character_name: str = "",
        target_tokens: int = 2048,
    ) -> Optional[ChatMessage]:
        """Compress a list of messages into a single summary message using the assistant model.

        Returns a ChatMessage(role='system') containing the summary, or None on failure.
        """
        if not messages:
            return None

        if not self._client:
            logger.error("LLM client not available for compression")
            return None

        lines: list[str] = []
        for msg in messages:
            if msg.role == "assistant":
                speaker = msg.character or "assistant"
            elif msg.sender_name:
                speaker = msg.sender_name
            elif msg.is_bot and msg.character:
                speaker = msg.character
            else:
                speaker = msg.role
            lines.append(f"[{speaker}]: {msg.content}")
        transcript = "\n".join(lines)

        compression_prompt = f"""你是一个上下文压缩助手。请将以下对话记录压缩为一段简洁的摘要，保留关键信息（人物关系、重要事件、情感状态、未完成的话题等）。
摘要应以第三人称叙述，不超过{target_tokens}字。
角色名: {character_name}

对话记录:
{transcript}

请直接输出摘要内容，不要添加任何前缀或解释。"""

        compress_model = config.orchestrator.context_compression.model or config.llm.assistant_model or config.llm.model
        compress_thinking = config.llm.assistant_model_thinking

        try:
            kwargs = dict(
                model=compress_model,
                messages=[{"role": "user", "content": compression_prompt}],
                temperature=0.3,
                max_tokens=target_tokens,
            )
            extra_body = self._get_extra_body(compress_thinking)
            if extra_body:
                kwargs["extra_body"] = extra_body
            response = await self._client.chat.completions.create(**kwargs)
            summary = response.choices[0].message.content
            if not summary:
                return None
            logger.info(f"Context compressed: {len(transcript)} chars -> {len(summary)} chars")
            return ChatMessage(
                role="system",
                content=f"[对话历史摘要]\n{summary}",
                character=character_name,
            )
        except Exception as e:
            logger.error(f"Context compression failed: {e}")
            return None


_llm_client = None


def init_llm_client():
    global _llm_client
    _llm_client = LLMClient()
    return _llm_client


def __getattr__(name):
    if name == "llm_client":
        if _llm_client is None:
            return init_llm_client()
        return _llm_client
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
