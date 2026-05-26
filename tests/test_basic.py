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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
