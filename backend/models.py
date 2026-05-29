from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum
from datetime import datetime


class ConnectionStatus(str, Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"


class PortInfo(BaseModel):
    port: int
    name: str
    character: str = ""
    qq_id: Optional[str] = None
    qq_name: Optional[str] = None
    status: ConnectionStatus = ConnectionStatus.DISCONNECTED
    connected_at: Optional[datetime] = None
    last_message_at: Optional[datetime] = None


class CharacterInfo(BaseModel):
    name: str
    path: str
    system_prompt: str = ""
    avatar: Optional[str] = None


class ChatMessage(BaseModel):
    role: str
    content: str
    timestamp: datetime = Field(default_factory=datetime.now)
    character: Optional[str] = None
    qq_id: Optional[str] = None
    raw_content: Optional[str] = None
    vision_content: Optional[str] = None
    is_bot: bool = False
    sender_name: Optional[str] = None
    image_urls: Optional[list[str]] = None
    message_id: Optional[int] = None
    message_id_self: Optional[int] = None


class CharacterAssignment(BaseModel):
    port: int
    character_name: str



