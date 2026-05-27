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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
