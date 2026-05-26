import asyncio
from datetime import datetime
from typing import Optional
from openai import AsyncOpenAI
from backend.config import config
from backend.models import ChatMessage
from backend.utils import get_logger

logger = get_logger("llm_client")


class LLMClient:
    def __init__(self):
        self._client: Optional[AsyncOpenAI] = None
        self._init_client()

    def _init_client(self):
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

    @property
    def is_available(self) -> bool:
        return self._client is not None

    def _get_extra_body(self, thinking: str) -> Optional[dict]:
        if thinking == "enabled":
            return {"thinking": {"type": "enabled"}}
        if thinking == "disabled":
            return {"thinking": {"type": "disabled"}}
        return None

    async def generate_response(
        self,
        system_prompt: str,
        messages: list[ChatMessage],
        temperature: float = None,
        max_tokens: int = None,
        model: str = None,
        thinking: str = "default",
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
    ) -> Optional[str]:
        messages = context.copy()
        messages.append(ChatMessage(role="user", content=user_message))

        prefix = config.orchestrator.prompt_prefix
        suffix = config.orchestrator.prompt_suffix
        time_note = ""
        if config.orchestrator.time_awareness:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            time_note = f"\n当前时间：{now}"

        system_prompt = f"""{prefix}
{character_prompt}
请以角色的身份回复。保持角色的性格特点和说话风格。
回复要自然、简洁，符合群聊场景。不要暴露你是AI。
{suffix}{time_note}"""

        return await self.generate_response(system_prompt, messages, thinking=config.llm.model_thinking)

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

        # Build a transcript from the messages
        lines: list[str] = []
        for msg in messages:
            speaker = msg.character or msg.role
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


llm_client = LLMClient()
