import asyncio
import random
from typing import Optional
from collections import defaultdict
from datetime import datetime
from backend.models import ChatMessage, CharacterAssignment
from backend.config import config, load_port_assignments, save_port_assignments
from backend.llm_client import llm_client
from backend.character_manager import character_manager
from backend.utils import get_logger, parse_message_text, build_text_message

logger = get_logger("orchestrator")


class SessionMemory:
    def __init__(self, max_messages: int = 20):
        self.messages: list[ChatMessage] = []
        self.max_messages = max_messages

    def add(self, message: ChatMessage):
        self.messages.append(message)
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages:]

    def get_context(self, last_n: int = None) -> list[ChatMessage]:
        if last_n:
            return self.messages[-last_n:]
        return self.messages.copy()

    def clear(self):
        self.messages.clear()


class Orchestrator:
    def __init__(self):
        self._enabled = config.orchestrator.enabled
        self._assignments: dict[int, str] = {}
        self._sessions: dict[int, SessionMemory] = defaultdict(lambda: SessionMemory(config.orchestrator.max_context_messages))
        self._group_sessions: dict[str, dict[str, SessionMemory]] = defaultdict(lambda: defaultdict(lambda: SessionMemory(config.orchestrator.max_context_messages)))
        self._ws_server = None
        self._load_assignments()
        
        # Auto dialogue tracking
        self._chain_counters: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._last_ai_reply_time: dict[str, dict[str, datetime]] = defaultdict(lambda: defaultdict(datetime.min))
        self._last_initiation_time: dict[str, datetime] = defaultdict(lambda: datetime.min)
        self._initiation_task: Optional[asyncio.Task] = None

    def _load_assignments(self):
        port_configs = load_port_assignments()
        for port, info in port_configs.items():
            char_name = info.get("character", "")
            if char_name:
                self._assignments[port] = char_name
                logger.info(f"Port {port} assigned to character: {char_name}")

    def set_ws_server(self, ws_server):
        self._ws_server = ws_server

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
            port_str = str(port)
            if port_str not in port_configs:
                port_configs[port] = {"name": f"Slot {port - 8080}", "character": char_name}
            else:
                port_configs[port]["character"] = char_name
        save_port_assignments(port_configs)

    def get_all_assignments(self) -> dict[int, str]:
        return self._assignments.copy()

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

        sender = data.get("sender", {})
        sender_name = sender.get("card", "") or sender.get("nickname", "")

        session = self._group_sessions[group_id][character_name]
        session.add(ChatMessage(
            role="user",
            content=f"[{sender_name}]: {raw_message}",
            qq_id=user_id,
            character=character_name,
        ))

        should_reply = self._should_reply(raw_message, character_name)
        if not should_reply:
            return

        await asyncio.sleep(config.orchestrator.reply_delay_ms / 1000)

        system_prompt = character_manager.get_system_prompt(character_name)
        if not system_prompt:
            logger.warning(f"No system prompt for character: {character_name}")
            return

        context = session.get_context()
        response = await llm_client.generate_roleplay_response(
            character_prompt=system_prompt,
            context=context,
            user_message=raw_message,
            character_name=character_name,
        )

        if response:
            session.add(ChatMessage(
                role="assistant",
                content=response,
                character=character_name,
            ))

            await self._send_reply(port, group_id, response)
            
            # Trigger other AI characters to consider replying
            if config.orchestrator.auto_dialogue.enabled:
                await self.handle_ai_reply(port, group_id, character_name, response)

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

        session_key = f"private_{user_id}_{character_name}"
        session = self._sessions[session_key]
        session.add(ChatMessage(
            role="user",
            content=raw_message,
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
            session.add(ChatMessage(
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

    async def handle_ai_reply(self, port: int, group_id: str, responding_character: str, reply_text: str):
        """Trigger other AI characters to consider replying after an AI responds."""
        if not config.orchestrator.auto_dialogue.enabled:
            return

        now = datetime.now()
        auto_config = config.orchestrator.auto_dialogue
        
        # Check cooldown
        last_reply_time = self._last_ai_reply_time[group_id][responding_character]
        if (now - last_reply_time).total_seconds() * 1000 < auto_config.cooldown_ms:
            return
        
        # Update last reply time
        self._last_ai_reply_time[group_id][responding_character] = now
        
        # Get all assigned characters in this group
        assigned_chars = list(self._assignments.values())
        
        # Trigger other characters
        for char_name in assigned_chars:
            if char_name == responding_character:
                continue
            
            # Check chain length limit
            chain_key = f"{group_id}_{char_name}"
            if self._chain_counters[group_id][char_name] >= auto_config.chain_length:
                continue
            
            # Check trigger probability
            if random.random() > auto_config.trigger_probability:
                continue
            
            # Find port for this character
            char_port = None
            for p, c in self._assignments.items():
                if c == char_name:
                    char_port = p
                    break
            
            if not char_port:
                continue
            
            # Generate and send reply
            await asyncio.sleep(config.orchestrator.reply_delay_ms / 1000)
            
            session = self._group_sessions[group_id][char_name]
            system_prompt = character_manager.get_system_prompt(char_name)
            if not system_prompt:
                continue
            
            context = session.get_context()
            response = await llm_client.generate_roleplay_response(
                character_prompt=system_prompt,
                context=context,
                user_message=reply_text,
                character_name=char_name,
            )
            
            if response:
                # Update chain counter
                self._chain_counters[group_id][char_name] += 1
                
                # Add to session
                session.add(ChatMessage(
                    role="assistant",
                    content=response,
                    character=char_name,
                ))
                
                # Send reply
                await self._send_reply(char_port, group_id, response)
                
                # Recursively trigger other AIs
                await self.handle_ai_reply(char_port, group_id, char_name, response)

    def _should_initiate_dialogue(self, group_id: str) -> bool:
        """Check if an AI should initiate a dialogue in the group."""
        if not config.orchestrator.auto_dialogue.enabled:
            return False
        
        now = datetime.now()
        auto_config = config.orchestrator.auto_dialogue
        
        # Check initiation interval
        last_init_time = self._last_initiation_time[group_id]
        if (now - last_init_time).total_seconds() * 1000 < auto_config.initiation_interval_ms:
            return False
        
        # Check initiation probability
        return random.random() < auto_config.initiation_probability

    async def _initiate_auto_dialogue(self, group_id: str):
        """Have an AI initiate a dialogue in the group."""
        if not self._should_initiate_dialogue(group_id):
            return
        
        # Select a random character to initiate
        assigned_chars = list(self._assignments.values())
        if not assigned_chars:
            return
        
        initiating_char = random.choice(assigned_chars)
        
        # Find port for this character
        char_port = None
        for p, c in self._assignments.items():
            if c == initiating_char:
                char_port = p
                break
        
        if not char_port:
            return
        
        # Generate initiation message
        session = self._group_sessions[group_id][initiating_char]
        system_prompt = character_manager.get_system_prompt(initiating_char)
        if not system_prompt:
            return
        
        # Create a context-aware initiation prompt
        context = session.get_context()
        initiation_prompt = f"请以{initiating_char}的身份，根据当前对话上下文，主动发起一个新的对话话题或回应之前的对话。保持角色性格特点，回复要自然、简洁。"
        
        response = await llm_client.generate_roleplay_response(
            character_prompt=system_prompt,
            context=context,
            user_message=initiation_prompt,
            character_name=initiating_char,
        )
        
        if response:
            # Update initiation time
            self._last_initiation_time[group_id] = datetime.now()
            
            # Add to session
            session.add(ChatMessage(
                role="assistant",
                content=response,
                character=initiating_char,
            ))
            
            # Send reply
            await self._send_reply(char_port, group_id, response)
            
            # Trigger other AIs
            await self.handle_ai_reply(char_port, group_id, initiating_char, response)

    def reset_chain_counters(self, group_id: str = None):
        """Reset chain counters for a group or all groups."""
        if group_id:
            self._chain_counters[group_id].clear()
        else:
            self._chain_counters.clear()

    async def start_initiation_task(self):
        """Start the periodic task for AI-initiated dialogues."""
        if not config.orchestrator.auto_dialogue.enabled:
            return
        
        async def _initiation_loop():
            while True:
                try:
                    # Get all groups from sessions
                    for group_id in list(self._group_sessions.keys()):
                        await self._initiate_auto_dialogue(group_id)
                except Exception as e:
                    logger.error(f"Error in initiation loop: {e}")
                
                # Wait before next check
                await asyncio.sleep(60)  # Check every minute
        
        self._initiation_task = asyncio.create_task(_initiation_loop())
        logger.info("Auto dialogue initiation task started")

    async def stop_initiation_task(self):
        """Stop the periodic initiation task."""
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
        if session_key in self._sessions:
            return self._sessions[session_key].get_context(count)
        return []


orchestrator = Orchestrator()
