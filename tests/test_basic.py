import pytest
from backend.config import load_config
from backend.models import ChatMessage, CharacterInfo
from backend.character_manager import CharacterManager


def test_load_config():
    config = load_config()
    assert config.server.base_port == 8081
    assert config.server.num_ports == 5
    assert config.server.management_port == 8080


def test_chat_message():
    msg = ChatMessage(role="user", content="Hello")
    assert msg.role == "user"
    assert msg.content == "Hello"


def test_character_manager():
    manager = CharacterManager()
    characters = manager.get_all_characters()
    assert len(characters) == 5

    names = manager.get_character_names()
    assert "户山香澄" in names
    assert "花园多惠" in names


def test_character_info():
    manager = CharacterManager()
    char = manager.get_character("户山香澄")
    assert char is not None
    assert char.name == "户山香澄"
    assert len(char.system_prompt) > 0


def test_group_context_keeps_only_own_replies_as_assistant():
    from backend.orchestrator import SessionMemory

    session = SessionMemory("test_identity_context")
    session.messages = [
        ChatMessage(role="assistant", content="我是香澄", character="户山香澄"),
        ChatMessage(role="assistant", content="我是有咲", character="市谷有咲"),
    ]

    context = session.get_context_for_character("户山香澄", {})

    assert context[0].role == "assistant"
    assert context[0].content == "我是香澄"
    assert context[1].role == "user"
    assert "[Poppin'Party成员] 市谷有咲" in context[1].content


def test_group_context_collapses_duplicate_human_messages():
    from datetime import datetime
    from backend.orchestrator import SessionMemory

    session = SessionMemory("test_duplicate_context")
    timestamp = datetime(2026, 5, 27, 20, 32, 5)
    session.messages = [
        ChatMessage(
            role="user",
            content="[自然常数2.718]: 谁来了 [message_id=101]",
            raw_content="谁来了",
            qq_id="1183508397",
            sender_name="自然常数2.718",
            timestamp=timestamp,
        ),
        ChatMessage(
            role="user",
            content="[自然常数2.718]: 谁来了 [message_id=102]",
            raw_content="谁来了",
            qq_id="1183508397",
            sender_name="自然常数2.718",
            timestamp=timestamp,
        ),
        ChatMessage(
            role="user",
            content="[Jjjjkooo]: 🐵 [message_id=201]",
            raw_content="🐵",
            qq_id="2708174131",
            sender_name="Jjjjkooo",
            timestamp=timestamp,
        ),
    ]

    context = session.get_context_for_character("户山香澄", {})

    assert [msg.content for msg in context] == [
        "[自然常数2.718]: 谁来了 [message_id=101]",
        "[Jjjjkooo]: 🐵 [message_id=201]",
    ]


def test_dispatcher_parses_schedule_reply_tool_call():
    from backend.dispatcher import Dispatcher
    from backend.llm_client import ToolCall

    dispatcher = Dispatcher()
    decision = dispatcher._parse_tool_call(ToolCall(
        id="call_1",
        function_name="schedule_reply",
        arguments={
            "character": "户山香澄",
            "strategy": "接住话题",
            "reason": "被直接提到",
        },
    ))

    assert decision.action == "reply"
    assert decision.character == "户山香澄"
    assert decision.strategy == "接住话题"


def test_dispatcher_mechanical_constraints_filter_chain_characters():
    from backend.config import config
    from backend.dispatcher import Dispatcher, DispatcherDecision

    old_enabled = config.orchestrator.auto_dialogue.enabled
    old_chain_length = config.orchestrator.auto_dialogue.chain_length
    config.orchestrator.auto_dialogue.enabled = False
    config.orchestrator.auto_dialogue.chain_length = 2
    try:
        dispatcher = Dispatcher()
        dispatcher.set_available_characters(["户山香澄", "花园多惠"])

        decision = dispatcher._apply_mechanical_constraints(DispatcherDecision(
            action="chain",
            characters=["户山香澄", "不存在", "花园多惠", "户山香澄"],
        ))

        assert decision.action == "chain"
        assert decision.characters == ["户山香澄", "花园多惠"]
    finally:
        config.orchestrator.auto_dialogue.enabled = old_enabled
        config.orchestrator.auto_dialogue.chain_length = old_chain_length


def test_dispatcher_uses_at_segment_as_explicit_target():
    from datetime import datetime
    from backend.dispatcher import Dispatcher
    from backend.message_buffer import BufferedMessage
    from backend.orchestrator import orchestrator

    old_bot_qq_map = orchestrator._bot_qq_map.copy()
    try:
        orchestrator._bot_qq_map = {
            "111": "户山香澄",
            "222": "市谷有咲",
        }
        dispatcher = Dispatcher()
        dispatcher.set_available_characters(["户山香澄", "市谷有咲"])

        msg = BufferedMessage(
            port=8081,
            data={},
            timestamp=datetime.now(),
            group_id="1107527508",
            user_id="1183508397",
            raw_message="@222 来一下",
            sender_name="自然常数2.718",
            message_segments=[
                {"type": "at", "data": {"qq": "222"}},
                {"type": "text", "data": {"text": " 来一下"}},
            ],
        )

        assert dispatcher._explicit_mention_target([msg]) == "市谷有咲"
        assert dispatcher._resolve_message_mentions(msg) == "[对市谷有咲说] 来一下"
    finally:
        orchestrator._bot_qq_map = old_bot_qq_map


@pytest.mark.asyncio
async def test_send_action_registers_echo_before_send():
    from backend.websocket_server import NapCatConnection

    conn = None

    class FastReplyWebSocket:
        async def send_json(self, payload):
            echo = payload["echo"]
            assert echo in conn._pending_echoes
            conn.resolve_echo(echo, {"status": "ok", "retcode": 0})

    conn = NapCatConnection(8081, FastReplyWebSocket())

    resp = await conn.send_action("set_msg_emoji_like", {
        "message_id": 620770633,
        "emoji_id": "128053",
    })

    assert resp == {"status": "ok", "retcode": 0}


@pytest.mark.asyncio
async def test_napcat_handler_deduplicates_same_group_message_across_ports():
    from backend.napcat_handler import NapCatMessageHandler

    handled = []

    async def on_group_message(port, data):
        handled.append((port, data))

    handler = NapCatMessageHandler()
    handler.on_group_message = on_group_message

    base_event = {
        "post_type": "message",
        "message_type": "group",
        "group_id": 1107527508,
        "user_id": 1183508397,
        "time": 1780000000,
        "raw_message": "谁来了",
        "message": [{"type": "text", "data": {"text": "谁来了"}}],
        "sender": {"nickname": "自然常数2.718"},
    }

    for port, message_id in ((8081, 101), (8082, 102), (8083, 103), (8084, 104), (8085, 105)):
        event = {**base_event, "message_id": message_id}
        await handler.handle_event(port, event)

    assert handled == [(8081, {**base_event, "message_id": 101})]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
