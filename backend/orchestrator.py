import asyncio
import random
from typing import Optional
from collections import defaultdict
from datetime import datetime
from backend.models import ChatMessage, CharacterAssignment
from backend.config import config, load_port_assignments, save_port_assignments
from backend.llm_client import llm_client
from backend.character_manager import character_manager
from backend.token_counter import count_message_tokens, count_single_message_tokens, truncate_messages_to_token_budget
from backend.utils import get_logger, parse_message_text, build_text_message, extract_image_urls
from backend.database import db

logger = get_logger("orchestrator")


class SessionMemory:
    """Persistent token-aware session memory backed by SQLite."""

    def __init__(self, session_key: str, max_messages: int = 20, max_tokens: int = 4096):
        self.session_key = session_key
        self.messages: list[ChatMessage] = []
        self._db_ids: list[int] = []
        self.max_messages = max_messages
        self.max_tokens = max_tokens
        self._total_tokens: int = 0
        self._compressing: bool = False

    async def init_from_db(self):
        rows = await db.load_messages(self.session_key)
        for row in rows:
            msg = ChatMessage(
                role=row["role"],
                content=row["content"],
                timestamp=datetime.fromtimestamp(row["timestamp"]),
                character=row["character"],
                qq_id=row["qq_id"],
                raw_content=row["raw_content"],
                is_bot=bool(row["is_bot"]),
                sender_name=row["sender_name"],
            )
            self.messages.append(msg)
            self._db_ids.append(row["id"])
        self._total_tokens = count_message_tokens(self.messages)
        logger.info(f"Loaded {len(self.messages)} msgs for session '{self.session_key}'")

    async def add(self, message: ChatMessage):
        msg_tokens = count_single_message_tokens(message)
        self.messages.append(message)
        self._total_tokens += msg_tokens

        db_id = await db.save_message(
            session_key=self.session_key,
            role=message.role,
            content=message.content,
            raw_content=message.raw_content,
            qq_id=message.qq_id,
            character=message.character,
            is_bot=message.is_bot,
            sender_name=message.sender_name,
            timestamp=message.timestamp.timestamp(),
        )
        self._db_ids.append(db_id)

        if len(self.messages) > self.max_messages:
            removed_ids = self._db_ids[:-self.max_messages]
            self.messages = self.messages[-self.max_messages:]
            self._db_ids = self._db_ids[-self.max_messages:]
            self._total_tokens = count_message_tokens(self.messages)
            await db.delete_messages_by_ids(removed_ids)

        if self._total_tokens > self.max_tokens and not self._compressing:
            self._compressing = True
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._compress())
            except RuntimeError:
                await self._truncate_async()

    async def _compress(self):
        compression_cfg = config.orchestrator.context_compression
        reserve = compression_cfg.reserve_recent

        if len(self.messages) <= reserve:
            self._compressing = False
            return

        old_messages = self.messages[:-reserve]
        recent_messages = self.messages[-reserve:]
        old_db_ids = self._db_ids[:-reserve]
        recent_db_ids = self._db_ids[-reserve:]

        character_name = ""
        for msg in reversed(self.messages):
            if msg.character:
                character_name = msg.character
                break

        summary_msg = await llm_client.compress_context(
            messages=old_messages,
            character_name=character_name,
            target_tokens=compression_cfg.target_tokens,
        )

        if summary_msg:
            await db.delete_messages_by_ids(old_db_ids)

            summary_db_id = await db.save_message(
                session_key=self.session_key,
                role=summary_msg.role,
                content=summary_msg.content,
                raw_content=summary_msg.raw_content,
                timestamp=summary_msg.timestamp.timestamp(),
            )

            self.messages = [summary_msg] + recent_messages
            self._db_ids = [summary_db_id] + recent_db_ids
            self._total_tokens = count_message_tokens(self.messages)
            logger.info(
                f"Context compressed: {len(old_messages)} msgs -> 1 summary, "
                f"total tokens now {self._total_tokens}"
            )
        else:
            await self._truncate_async()

        self._compressing = False

    async def _truncate_async(self):
        kept = truncate_messages_to_token_budget(self.messages, self.max_tokens)
        removed_count = len(self.messages) - len(kept)
        if removed_count > 0:
            removed_ids = self._db_ids[:removed_count]
            await db.delete_messages_by_ids(removed_ids)
            self._db_ids = self._db_ids[removed_count:]
        self.messages = kept
        self._total_tokens = count_message_tokens(self.messages)
        logger.warning(f"Context truncated to {len(self.messages)} msgs, {self._total_tokens} tokens")

    def get_context(self, last_n: int = None) -> list[ChatMessage]:
        if last_n:
            return self.messages[-last_n:]
        return self.messages.copy()

    def get_context_for_character(self, character_name: str, bot_qq_map: dict[str, str]) -> list[ChatMessage]:
        """Transform messages for a specific character's perspective.

        - Own messages (this character's QQ) -> [你]: raw_content
        - Other bot messages -> [Poppin'Party成员] {char_name}: raw_content
        - Human messages -> [sender_name]: raw_content  (unchanged)
        - System messages -> unchanged
        """
        own_qq_id = None
        for qq_id, name in bot_qq_map.items():
            if name == character_name:
                own_qq_id = qq_id
                break

        result = []
        for msg in self.messages:
            if msg.role == "system":
                result.append(msg)
                continue

            if msg.role == "assistant":
                result.append(msg)
                continue

            raw = msg.raw_content if msg.raw_content is not None else msg.content

            if msg.qq_id and msg.qq_id == own_qq_id:
                transformed = ChatMessage(
                    role="user",
                    content=f"[你]: {raw}",
                    timestamp=msg.timestamp,
                    qq_id=msg.qq_id,
                    character=msg.character,
                    raw_content=raw,
                    is_bot=msg.is_bot,
                    sender_name="你",
                )
            elif msg.is_bot and msg.sender_name:
                other_char = bot_qq_map.get(msg.qq_id, msg.sender_name)
                transformed = ChatMessage(
                    role="user",
                    content=f"[Poppin'Party成员] {other_char}: {raw}",
                    timestamp=msg.timestamp,
                    qq_id=msg.qq_id,
                    character=msg.character,
                    raw_content=raw,
                    is_bot=True,
                    sender_name=f"[Poppin'Party成员] {other_char}",
                )
            else:
                result.append(msg)
                continue

            result.append(transformed)

        return result

    def get_token_count(self) -> int:
        return self._total_tokens

    async def clear(self):
        self.messages.clear()
        self._db_ids.clear()
        self._total_tokens = 0
        await db.clear_session(self.session_key)


class Orchestrator:
    def __init__(self):
        self._enabled = config.orchestrator.enabled
        self._assignments: dict[int, str] = {}
        self._group_sessions: dict[str, SessionMemory] = {}
        self._private_sessions: dict[str, SessionMemory] = {}
        self._ws_server = None
        self._load_assignments()

        self._bot_qq_map: dict[str, str] = {}
        self._chain_counters: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._last_ai_reply_time: dict[str, dict[str, datetime]] = defaultdict(lambda: defaultdict(datetime.min))
        self._last_initiation_time: dict[str, datetime] = defaultdict(lambda: datetime.min)
        self._initiation_task: Optional[asyncio.Task] = None

    async def init_db(self):
        await db.init_db()

    def _load_assignments(self):
        port_configs = load_port_assignments()
        for port, info in port_configs.items():
            char_name = info.get("character", "")
            if char_name:
                self._assignments[int(port)] = char_name
                logger.info(f"Port {port} assigned to character: {char_name}")

    def set_ws_server(self, ws_server):
        self._ws_server = ws_server

    def register_bot_qq(self, qq_id: str, character_name: str):
        self._bot_qq_map[qq_id] = character_name
        logger.info(f"Registered bot QQ {qq_id} -> character: {character_name}")

    def get_character_by_qq_id(self, qq_id: str) -> Optional[str]:
        return self._bot_qq_map.get(qq_id)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = value
        logger.info(f"Orchestrator {'enabled' if value else 'disabled'}")

    def get_assignment(self, port: int) -> Optional[str]:
        return self._assignments.get(port)

    def set_assignment(self, port: int, character_name: str):
        self._assignments[port] = character_name
        self._save_assignments()
        logger.info(f"Assigned {character_name} to port {port}")

    def remove_assignment(self, port: int):
        if port in self._assignments:
            del self._assignments[port]
            self._save_assignments()
            logger.info(f"Removed assignment from port {port}")

    def _save_assignments(self):
        port_configs = load_port_assignments()
        for port, char_name in self._assignments.items():
            if port not in port_configs:
                port_configs[port] = {"name": f"Slot {port - 8080}", "character": char_name, "token": ""}
            else:
                port_configs[port]["character"] = char_name
        save_port_assignments(port_configs)

    def get_all_assignments(self) -> dict[int, str]:
        return self._assignments.copy()

    async def _get_group_session(self, group_id: str) -> SessionMemory:
        if group_id not in self._group_sessions:
            session = SessionMemory(
                session_key=f"group_{group_id}",
                max_messages=config.orchestrator.max_context_messages,
                max_tokens=config.orchestrator.max_context_tokens,
            )
            await session.init_from_db()
            self._group_sessions[group_id] = session
        return self._group_sessions[group_id]

    async def _get_private_session(self, user_id: str, character_name: str) -> SessionMemory:
        session_key = f"private_{user_id}_{character_name}"
        if session_key not in self._private_sessions:
            session = SessionMemory(
                session_key=session_key,
                max_messages=config.orchestrator.max_context_messages,
                max_tokens=config.orchestrator.max_context_tokens,
            )
            await session.init_from_db()
            self._private_sessions[session_key] = session
        return self._private_sessions[session_key]

    async def handle_group_message(self, port: int, data: dict):
        if not self._enabled:
            return

        character_name = self._assignments.get(port)
        if not character_name:
            return

        group_id = str(data.get("group_id", ""))
        user_id = str(data.get("user_id", ""))
        message_segments = data.get("message", [])
        raw_message = data.get("raw_message", "")

        if not raw_message:
            raw_message = parse_message_text(message_segments)

        if not raw_message.strip():
            return

        image_urls = extract_image_urls(message_segments)
        if image_urls and config.llm.vision.enabled and llm_client.is_vision_available:
            vision_descriptions = []
            for url in image_urls:
                desc = await llm_client.analyze_image(url)
                if desc:
                    vision_descriptions.append(desc)
            if vision_descriptions:
                raw_message = raw_message.replace("[图片]", "")
                raw_message = raw_message.strip()
                vision_text = " ".join(vision_descriptions)
                raw_message = f"{raw_message} [图片内容: {vision_text}]" if raw_message else f"[图片内容: {vision_text}]"

        sender = data.get("sender", {})
        sender_name = sender.get("card", "") or sender.get("nickname", "")

        bot_character = self.get_character_by_qq_id(user_id)
        is_bot = bot_character is not None
        if is_bot:
            sender_name = bot_character

        session = await self._get_group_session(group_id)

        display_content = f"[{sender_name}]: {raw_message}"
        if is_bot:
            display_content = f"[Poppin'Party成员] {sender_name}: {raw_message}"

        await session.add(ChatMessage(
            role="user",
            content=display_content,
            raw_content=raw_message,
            qq_id=user_id,
            character=character_name,
            is_bot=is_bot,
            sender_name=sender_name,
        ))

        should_reply = self._should_reply(raw_message, character_name)
        if not should_reply:
            return

        await asyncio.sleep(config.orchestrator.reply_delay_ms / 1000)

        system_prompt = character_manager.get_system_prompt(character_name)
        if not system_prompt:
            logger.warning(f"No system prompt for character: {character_name}")
            return

        context = session.get_context_for_character(character_name, self._bot_qq_map)
        response = await llm_client.generate_roleplay_response(
            character_prompt=system_prompt,
            context=context,
            user_message=raw_message,
            character_name=character_name,
        )

        if response:
            await session.add(ChatMessage(
                role="assistant",
                content=response,
                character=character_name,
            ))

            await self._send_reply(port, group_id, response)

            if config.orchestrator.auto_dialogue.enabled:
                self._chain_counters[group_id][character_name] = 0
                await self.handle_ai_reply(port, group_id, character_name, response, depth=0)

    async def handle_private_message(self, port: int, data: dict):
        if not self._enabled:
            return

        character_name = self._assignments.get(port)
        if not character_name:
            return

        user_id = str(data.get("user_id", ""))
        raw_message = data.get("raw_message", "")
        message_segments = data.get("message", [])

        if not raw_message:
            raw_message = parse_message_text(message_segments)

        if not raw_message.strip():
            return

        image_urls = extract_image_urls(message_segments)
        if image_urls and config.llm.vision.enabled and llm_client.is_vision_available:
            vision_descriptions = []
            for url in image_urls:
                desc = await llm_client.analyze_image(url)
                if desc:
                    vision_descriptions.append(desc)
            if vision_descriptions:
                raw_message = raw_message.replace("[图片]", "")
                raw_message = raw_message.strip()
                vision_text = " ".join(vision_descriptions)
                raw_message = f"{raw_message} [图片内容: {vision_text}]" if raw_message else f"[图片内容: {vision_text}]"

        session = await self._get_private_session(user_id, character_name)
        await session.add(ChatMessage(
            role="user",
            content=raw_message,
            raw_content=raw_message,
            qq_id=user_id,
            character=character_name,
        ))

        system_prompt = character_manager.get_system_prompt(character_name)
        if not system_prompt:
            return

        context = session.get_context()
        response = await llm_client.generate_roleplay_response(
            character_prompt=system_prompt,
            context=context,
            user_message=raw_message,
            character_name=character_name,
        )

        if response:
            await session.add(ChatMessage(
                role="assistant",
                content=response,
                character=character_name,
            ))

            await self._send_private_reply(port, user_id, response)

    def _should_reply(self, message: str, character_name: str) -> bool:
        if f"@{character_name}" in message:
            return True

        keywords = [character_name, character_name.replace(" ", "")]
        for kw in keywords:
            if kw in message:
                return True

        return random.random() < config.orchestrator.group_reply_probability

    def _find_port_for_character(self, character_name: str) -> Optional[int]:
        for p, c in self._assignments.items():
            if c == character_name:
                return p
        return None

    async def handle_ai_reply(self, port: int, group_id: str, responding_character: str,
                              reply_text: str, depth: int = 0):
        if not config.orchestrator.auto_dialogue.enabled:
            return

        auto_config = config.orchestrator.auto_dialogue

        if depth >= auto_config.chain_length:
            return

        now = datetime.now()
        last_reply_time = self._last_ai_reply_time[group_id][responding_character]
        if (now - last_reply_time).total_seconds() * 1000 < auto_config.cooldown_ms:
            return

        self._last_ai_reply_time[group_id][responding_character] = now

        assigned_chars = list(self._assignments.values())
        session = await self._get_group_session(group_id)

        for char_name in assigned_chars:
            if char_name == responding_character:
                continue

            if self._chain_counters[group_id][char_name] >= auto_config.chain_length:
                continue

            if random.random() > auto_config.trigger_probability:
                continue

            char_port = self._find_port_for_character(char_name)
            if not char_port:
                continue

            await asyncio.sleep(config.orchestrator.reply_delay_ms / 1000)

            system_prompt = character_manager.get_system_prompt(char_name)
            if not system_prompt:
                continue

            context = session.get_context_for_character(char_name, self._bot_qq_map)
            response = await llm_client.generate_roleplay_response(
                character_prompt=system_prompt,
                context=context,
                user_message=reply_text,
                character_name=char_name,
            )

            if response:
                self._chain_counters[group_id][char_name] += 1

                await session.add(ChatMessage(
                    role="assistant",
                    content=response,
                    character=char_name,
                ))

                await self._send_reply(char_port, group_id, response)

                await self.handle_ai_reply(char_port, group_id, char_name, response, depth=depth + 1)

    def _should_initiate_dialogue(self, group_id: str) -> bool:
        if not config.orchestrator.auto_dialogue.enabled:
            return False

        now = datetime.now()
        auto_config = config.orchestrator.auto_dialogue

        last_init_time = self._last_initiation_time[group_id]
        if (now - last_init_time).total_seconds() * 1000 < auto_config.initiation_interval_ms:
            return False

        return random.random() < auto_config.initiation_probability

    async def _initiate_auto_dialogue(self, group_id: str):
        if not self._should_initiate_dialogue(group_id):
            return

        assigned_chars = list(self._assignments.values())
        if not assigned_chars:
            return

        initiating_char = random.choice(assigned_chars)

        char_port = self._find_port_for_character(initiating_char)
        if not char_port:
            return

        session = await self._get_group_session(group_id)
        system_prompt = character_manager.get_system_prompt(initiating_char)
        if not system_prompt:
            return

        context = session.get_context_for_character(initiating_char, self._bot_qq_map)
        initiation_prompt = f"请以{initiating_char}的身份，根据当前对话上下文，主动发起一个新的对话话题或回应之前的对话。保持角色性格特点，回复要自然、简洁。"

        response = await llm_client.generate_roleplay_response(
            character_prompt=system_prompt,
            context=context,
            user_message=initiation_prompt,
            character_name=initiating_char,
        )

        if response:
            self._last_initiation_time[group_id] = datetime.now()

            await session.add(ChatMessage(
                role="assistant",
                content=response,
                character=initiating_char,
            ))

            await self._send_reply(char_port, group_id, response)

            self._chain_counters[group_id][initiating_char] = 0
            await self.handle_ai_reply(char_port, group_id, initiating_char, response, depth=0)

    def reset_chain_counters(self, group_id: str = None):
        if group_id:
            self._chain_counters[group_id].clear()
        else:
            self._chain_counters.clear()

    async def start_initiation_task(self):
        if not config.orchestrator.auto_dialogue.enabled:
            return

        async def _initiation_loop():
            while True:
                try:
                    for group_id in list(self._group_sessions.keys()):
                        await self._initiate_auto_dialogue(group_id)
                except Exception as e:
                    logger.error(f"Error in initiation loop: {e}")

                await asyncio.sleep(60)

        self._initiation_task = asyncio.create_task(_initiation_loop())
        logger.info("Auto dialogue initiation task started")

    async def stop_initiation_task(self):
        if self._initiation_task:
            self._initiation_task.cancel()
            try:
                await self._initiation_task
            except asyncio.CancelledError:
                pass
            self._initiation_task = None
            logger.info("Auto dialogue initiation task stopped")

    async def _send_reply(self, port: int, group_id: str, text: str):
        if not self._ws_server:
            return

        message = build_text_message(text)
        conn = self._ws_server.get_connection(port)
        if conn:
            try:
                await conn.send_message(group_id=group_id, message=message)
                logger.info(f"[Port {port}] Replied to group {group_id}: {text[:50]}...")
            except Exception as e:
                logger.error(f"Failed to send reply: {e}")

    async def _send_private_reply(self, port: int, user_id: str, text: str):
        if not self._ws_server:
            return

        message = build_text_message(text)
        conn = self._ws_server.get_connection(port)
        if conn:
            try:
                await conn.send_message(user_id=user_id, message=message)
                logger.info(f"[Port {port}] Replied to user {user_id}: {text[:50]}...")
            except Exception as e:
                logger.error(f"Failed to send private reply: {e}")

    def get_session_messages(self, session_key: str, count: int = 20) -> list[ChatMessage]:
        for sessions in (self._group_sessions, self._private_sessions):
            session = sessions.get(session_key)
            if session:
                return session.get_context(count)
        return []


orchestrator = Orchestrator()
