from pydantic import BaseModel, Field
from typing import Optional, Any
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


class OneBotAction(BaseModel):
    action: str
    params: dict[str, Any] = {}
    echo: Optional[str] = None


class OneBotResponse(BaseModel):
    status: str = "ok"
    retcode: int = 0
    data: Any = None
    message: str = ""
    echo: Optional[str] = None


class OneBotEvent(BaseModel):
    time: int = 0
    self_id: str = ""
    post_type: str = ""
    sub_type: Optional[str] = None
    raw_data: dict[str, Any] = {}


class MessageEvent(OneBotEvent):
    message_id: Optional[int] = None
    user_id: Optional[str] = None
    group_id: Optional[str] = None
    message_type: str = ""
    message: list[dict[str, Any]] = []
    raw_message: str = ""
    font: int = 0


class LifecycleEvent(OneBotEvent):
    pass


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


class OrchestratorState(BaseModel):
    enabled: bool = True
    ports: dict[int, PortInfo] = {}
    characters: list[CharacterInfo] = []
    assignments: dict[int, str] = {}


class DashboardData(BaseModel):
    state: OrchestratorState
    recent_messages: list[ChatMessage] = []
