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

    async def generate_response(
        self,
        system_prompt: str,
        messages: list[ChatMessage],
        temperature: float = None,
        max_tokens: int = None,
        model: str = None,
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
            response = await self._client.chat.completions.create(
                model=model or config.llm.model,
                messages=formatted_messages,
                temperature=temperature or config.llm.temperature,
                max_tokens=max_tokens or config.llm.max_tokens,
            )
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

        return await self.generate_response(system_prompt, messages)

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
            system_prompt, messages, temperature, max_tokens, model=assistant_model
        )


llm_client = LLMClient()
