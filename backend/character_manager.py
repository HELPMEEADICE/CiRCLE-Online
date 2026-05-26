from pathlib import Path
from typing import Optional
from backend.config import CHARACTERS_DIR
from backend.models import CharacterInfo
from backend.utils import get_logger

logger = get_logger("character_manager")


class CharacterManager:
    def __init__(self):
        self._characters: dict[str, CharacterInfo] = {}
        self._load_characters()

    def _load_characters(self):
        if not CHARACTERS_DIR.exists():
            logger.warning(f"Characters directory not found: {CHARACTERS_DIR}")
            return

        for char_dir in CHARACTERS_DIR.iterdir():
            if char_dir.is_dir():
                self._load_character(char_dir)

    def _load_character(self, char_dir: Path):
        name = char_dir.name
        soul_file = char_dir / "soul.md"
        system_prompt = ""

        if soul_file.exists():
            system_prompt = soul_file.read_text(encoding="utf-8")
            logger.info(f"Loaded character: {name} ({len(system_prompt)} chars)")
        else:
            logger.warning(f"No soul.md found for character: {name}")

        avatar_file = None
        for ext in ["png", "jpg", "jpeg", "webp"]:
            candidate = char_dir / f"avatar.{ext}"
            if candidate.exists():
                avatar_file = str(candidate)
                break

        self._characters[name] = CharacterInfo(
            name=name,
            path=str(char_dir),
            system_prompt=system_prompt,
            avatar=avatar_file,
        )

    def get_character(self, name: str) -> Optional[CharacterInfo]:
        return self._characters.get(name)

    def get_all_characters(self) -> list[CharacterInfo]:
        return list(self._characters.values())

    def get_character_names(self) -> list[str]:
        return list(self._characters.keys())

    def get_system_prompt(self, name: str) -> str:
        char = self.get_character(name)
        return char.system_prompt if char else ""

    def reload(self):
        self._characters.clear()
        self._load_characters()


character_manager = CharacterManager()
