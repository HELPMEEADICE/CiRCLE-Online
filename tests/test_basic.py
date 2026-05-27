import pytest
from types import SimpleNamespace
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


def test_group_context_collapses_duplicate_images_with_different_urls():
    from datetime import datetime
    from backend.orchestrator import SessionMemory

    session = SessionMemory("test_duplicate_image_context")
    timestamp = datetime(2026, 5, 27, 20, 32, 5)
    session.messages = [
        ChatMessage(
            role="user",
            content="[自然常数2.718]: [图片] [图片:abc.image] [message_id=101]",
            raw_content="[图片]",
            qq_id="1183508397",
            sender_name="自然常数2.718",
            image_urls=["http://127.0.0.1:8081/get_image?file=abc.image&token=1"],
            timestamp=timestamp,
        ),
        ChatMessage(
            role="user",
            content="[自然常数2.718]: [图片] [图片:abc.image] [message_id=102]",
            raw_content="[图片]",
            qq_id="1183508397",
            sender_name="自然常数2.718",
            image_urls=["http://127.0.0.1:8082/get_image?file=abc.image&token=2"],
            timestamp=timestamp,
        ),
    ]

    context = session.get_context_for_character("户山香澄", {})

    assert [msg.content for msg in context] == [
        "[自然常数2.718]: [图片] [图片:abc.image] [message_id=101]",
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


def test_dispatcher_supreme_power_ignores_chain_cap():
    from backend.config import config
    from backend.dispatcher import Dispatcher, DispatcherDecision

    old_supreme_power = config.orchestrator.dispatcher.supreme_power
    config.orchestrator.dispatcher.supreme_power = True
    try:
        dispatcher = Dispatcher()
        dispatcher.set_available_characters(["户山香澄", "花园多惠", "市谷有咲", "山吹沙绫"])

        decision = dispatcher._apply_mechanical_constraints(DispatcherDecision(
            action="chain",
            characters=["户山香澄", "不存在", "花园多惠", "市谷有咲", "山吹沙绫"],
        ))

        assert decision.action == "chain"
        assert decision.characters == ["户山香澄", "花园多惠", "市谷有咲", "山吹沙绫"]
    finally:
        config.orchestrator.dispatcher.supreme_power = old_supreme_power


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

        assert dispatcher._explicit_mention_targets([msg]) == ["市谷有咲"]
        assert dispatcher._explicit_mention_target([msg]) == "市谷有咲"
        assert dispatcher._resolve_message_mentions(msg) == "[对市谷有咲说] 来一下"
    finally:
        orchestrator._bot_qq_map = old_bot_qq_map


def test_dispatcher_collects_all_explicit_targets_in_buffer_order():
    from datetime import datetime
    from backend.dispatcher import Dispatcher
    from backend.message_buffer import BufferedMessage
    from backend.orchestrator import orchestrator

    old_bot_qq_map = orchestrator._bot_qq_map.copy()
    try:
        orchestrator._bot_qq_map = {
            "111": "户山香澄",
            "222": "市谷有咲",
            "333": "花园多惠",
        }
        dispatcher = Dispatcher()
        dispatcher.set_available_characters(["户山香澄", "市谷有咲", "花园多惠"])

        msg1 = BufferedMessage(
            port=8081,
            data={},
            timestamp=datetime.now(),
            group_id="1107527508",
            user_id="1183508397",
            raw_message="@111 @222 都来一下",
            sender_name="自然常数2.718",
            message_segments=[
                {"type": "at", "data": {"qq": "111"}},
                {"type": "text", "data": {"text": " "}},
                {"type": "at", "data": {"qq": "222"}},
                {"type": "text", "data": {"text": " 都来一下"}},
            ],
        )
        msg2 = BufferedMessage(
            port=8081,
            data={},
            timestamp=datetime.now(),
            group_id="1107527508",
            user_id="1183508397",
            raw_message="@333 你也来",
            sender_name="自然常数2.718",
            message_segments=[
                {"type": "at", "data": {"qq": "333"}},
                {"type": "text", "data": {"text": " 你也来"}},
            ],
        )

        assert dispatcher._explicit_mention_targets([msg1, msg2]) == ["户山香澄", "市谷有咲", "花园多惠"]
        assert dispatcher._explicit_mention_target([msg1, msg2]) == "花园多惠"
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

    assert len(handled) == 1
    assert handled[0][0] == 8081
    assert handled[0][1]["message_id"] == 101
    assert handled[0][1]["_message_ids_by_port"] == {
        8081: 101,
        8082: 102,
        8083: 103,
        8084: 104,
        8085: 105,
    }


@pytest.mark.asyncio
async def test_orchestrator_translates_emoji_message_id_to_action_port():
    from backend.llm_client import ToolCall
    from backend.orchestrator import Orchestrator

    sent_actions = []

    class FakeConnection:
        async def send_action(self, action, params, timeout=10.0):
            sent_actions.append((action, params, timeout))
            return {"status": "ok", "retcode": 0}

    class FakeWebSocketServer:
        def get_connection(self, port):
            assert port == 8085
            return FakeConnection()

    orchestrator = Orchestrator()
    orchestrator.set_ws_server(FakeWebSocketServer())
    orchestrator._remember_message_id_aliases("1107527508", {
        8081: 101,
        8082: 102,
        8083: 103,
        8084: 104,
        8085: 105,
    })

    results = await orchestrator._execute_tool_calls([
        ToolCall(
            id="call_1",
            function_name="set_msg_emoji_like",
            arguments={"message_id": 101, "emoji_id": "128051"},
        )
    ], "1107527508", 8085)

    assert results == ["[set_msg_emoji_like] 已添加表情回应 🐳"]
    assert sent_actions == [("set_msg_emoji_like", {"message_id": 105, "emoji_id": "128051"}, 30.0)]


def test_orchestrator_sanitizes_emoji_tool_narration():
    from backend.llm_client import ToolCall
    from backend.orchestrator import Orchestrator

    orchestrator = Orchestrator()
    tool_calls = [
        ToolCall(
            id="call_1",
            function_name="set_msg_emoji_like",
            arguments={"message_id": 101, "emoji_id": "128051"},
        )
    ]

    assert orchestrator._sanitize_reply_text("给你点个🐳", tool_calls) == ""
    assert orchestrator._sanitize_reply_text("好耶", tool_calls) == "好耶"


@pytest.mark.asyncio
async def test_execute_reply_generates_text_after_emoji_only_tool_call(monkeypatch):
    from backend.llm_client import RoleplayResponse, ToolCall
    import backend.character_manager as character_manager_module
    import backend.llm_client as llm_client_module
    from backend.orchestrator import Orchestrator

    sent_replies = []
    saved_messages = []
    llm_calls = []

    class FakeSession:
        session_key = "group_1107527508"

        def get_context_for_character(self, character_name, bot_qq_map):
            return []

        async def add(self, message):
            saved_messages.append(message.content)

    orchestrator = Orchestrator()
    orchestrator._assignments = {8081: "户山香澄"}

    async def fake_get_group_session(group_id):
        return FakeSession()

    async def fake_send_reply(port, group_id, reply_text):
        sent_replies.append((port, group_id, reply_text))

    async def fake_execute_tool_calls(tool_calls, group_id, port, is_private=False, context_message_id=0):
        return ["[set_msg_emoji_like] 已添加表情回应 🐳"]

    async def fake_generate_roleplay_response(character_prompt, context, user_message, character_name="", tools=None):
        llm_calls.append(tools)
        if len(llm_calls) == 1:
            return RoleplayResponse(
                content="给你点个🐳",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        function_name="set_msg_emoji_like",
                        arguments={"message_id": 101, "emoji_id": "128051"},
                    )
                ],
            )
        return RoleplayResponse(content="来了", tool_calls=[])

    monkeypatch.setattr(character_manager_module.character_manager, "get_system_prompt", lambda _: "你是户山香澄")
    monkeypatch.setattr(llm_client_module.llm_client, "generate_roleplay_response", fake_generate_roleplay_response)
    monkeypatch.setattr(orchestrator, "_get_group_session", fake_get_group_session)
    monkeypatch.setattr(orchestrator, "_send_reply", fake_send_reply)
    monkeypatch.setattr(orchestrator, "_execute_tool_calls", fake_execute_tool_calls)

    await orchestrator.execute_reply("1107527508", "户山香澄", trigger_message=None)

    assert sent_replies == [(8081, "1107527508", "来了")]
    assert saved_messages == ["来了"]
    assert llm_calls[0] is not None
    assert llm_calls[1] is None


@pytest.mark.asyncio
async def test_generate_roleplay_response_inserts_tool_guidance_between_history_and_current_message():
    from backend.llm_client import LLMClient

    captured_messages = None

    class FakeCompletions:
        async def create(self, **kwargs):
            nonlocal captured_messages
            captured_messages = kwargs["messages"]
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="收到", tool_calls=[])
                    )
                ]
            )

    llm = LLMClient()
    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

    response = await llm.generate_roleplay_response(
        character_prompt="你是户山香澄",
        context=[ChatMessage(role="user", content="历史消息")],
        user_message="当前消息",
        character_name="户山香澄",
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "set_msg_emoji_like",
                    "description": "给指定消息添加表情回应",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "message_id": {"type": "integer", "description": "目标消息ID"},
                            "emoji_id": {"type": "string", "description": "表情ID"},
                        },
                        "required": ["message_id", "emoji_id"],
                    },
                },
            }
        ],
    )

    assert response.content == "收到"
    assert captured_messages is not None
    assert captured_messages[1] == {"role": "user", "content": "历史消息"}
    assert captured_messages[2]["role"] == "system"
    assert "set_msg_emoji_like" in captured_messages[2]["content"]
    assert "给指定消息添加表情回应" in captured_messages[2]["content"]
    assert captured_messages[3] == {"role": "user", "content": "当前消息"}


@pytest.mark.asyncio
async def test_napcat_handler_deduplicates_same_image_with_different_urls():
    from backend.napcat_handler import NapCatMessageHandler

    handled = []

    async def on_group_message(port, data):
        handled.append((port, data["message_id"]))

    handler = NapCatMessageHandler()
    handler.on_group_message = on_group_message

    base_event = {
        "post_type": "message",
        "message_type": "group",
        "group_id": 1107527508,
        "user_id": 1183508397,
        "time": 1780000000,
        "sender": {"nickname": "自然常数2.718"},
    }

    for port, message_id in ((8081, 101), (8082, 102), (8083, 103), (8084, 104), (8085, 105)):
        event = {
            **base_event,
            "message_id": message_id,
            "raw_message": f"[CQ:image,file=abc.image,url=http://127.0.0.1:{port}/get_image?token={port}]",
            "message": [{
                "type": "image",
                "data": {
                    "file": "abc.image",
                    "url": f"http://127.0.0.1:{port}/get_image?token={port}",
                },
            }],
        }
        await handler.handle_event(port, event)

    assert handled == [(8081, 101)]


@pytest.mark.asyncio
async def test_napcat_handler_deduplicates_same_sticker_with_different_urls():
    from backend.napcat_handler import NapCatMessageHandler

    handled = []

    async def on_group_message(port, data):
        handled.append((port, data["message_id"]))

    handler = NapCatMessageHandler()
    handler.on_group_message = on_group_message

    base_event = {
        "post_type": "message",
        "message_type": "group",
        "group_id": 1107527508,
        "user_id": 1183508397,
        "time": 1780000000,
        "sender": {"nickname": "自然常数2.718"},
    }

    for port, message_id in ((8081, 201), (8082, 202), (8083, 203), (8084, 204), (8085, 205)):
        event = {
            **base_event,
            "message_id": message_id,
            "raw_message": f"[CQ:mface,emoji_id=114514,url=http://127.0.0.1:{port}/mface?token={port}]",
            "message": [{
                "type": "mface",
                "data": {
                    "emoji_id": "114514",
                    "url": f"http://127.0.0.1:{port}/mface?token={port}",
                },
            }],
        }
        await handler.handle_event(port, event)

    assert handled == [(8081, 201)]


@pytest.mark.asyncio
async def test_napcat_handler_deduplicates_raw_reply_at_with_different_reply_ids():
    from backend.napcat_handler import NapCatMessageHandler

    handled = []

    async def on_group_message(port, data):
        handled.append((port, data["message_id"]))

    handler = NapCatMessageHandler()
    handler.on_group_message = on_group_message

    base_event = {
        "post_type": "message",
        "message_type": "group",
        "group_id": 1107527508,
        "user_id": 2708174131,
        "time": 1780000000,
        "sender": {"nickname": "Jjjjkooo"},
    }

    for port, message_id, reply_id in (
        (8084, 301, 1236921153),
        (8081, 302, 1871555620),
        (8083, 303, 191742165),
        (8082, 304, 870123998),
    ):
        event = {
            **base_event,
            "message_id": message_id,
            "raw_message": f"[CQ:reply,id={reply_id}][CQ:at,qq=3677613276] 我错了",
            "message": [],
        }
        await handler.handle_event(port, event)

    assert handled == [(8084, 301)]


@pytest.mark.asyncio
async def test_napcat_handler_deduplicates_segment_reply_at_with_different_reply_ids():
    from backend.napcat_handler import NapCatMessageHandler

    handled = []

    async def on_group_message(port, data):
        handled.append((port, data["message_id"]))

    handler = NapCatMessageHandler()
    handler.on_group_message = on_group_message

    base_event = {
        "post_type": "message",
        "message_type": "group",
        "group_id": 1107527508,
        "user_id": 2708174131,
        "time": 1780000000,
        "sender": {"nickname": "Jjjjkooo"},
    }

    for port, message_id, reply_id in ((8084, 401, 1), (8081, 402, 2), (8083, 403, 3)):
        event = {
            **base_event,
            "message_id": message_id,
            "raw_message": "",
            "message": [
                {"type": "reply", "data": {"id": str(reply_id)}},
                {"type": "at", "data": {"qq": "3677613276"}},
                {"type": "text", "data": {"text": " 我错了"}},
            ],
        }
        await handler.handle_event(port, event)

    assert handled == [(8084, 401)]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
