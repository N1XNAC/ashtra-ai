from typing import Optional, List, Dict, Any
from pydantic import BaseModel, field_validator

# --- Chat ---
class ChatRequest(BaseModel):
    user_id: str
    conversation_id: Optional[str] = None
    message: str
    # Vision pipeline: text the vision model wrote about an attached image.
    # Fed to the text brain as context; never requires the image itself.
    image_context: Optional[str] = None

    @field_validator("user_id")
    @classmethod
    def _user_id_sane(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) > 128:
            raise ValueError("user_id must be 1..128 chars")
        return v

    @field_validator("message")
    @classmethod
    def _message_sane(cls, v: str) -> str:
        from .config import settings
        v = v.strip()
        if not v:
            raise ValueError("message must not be empty")
        if len(v) > settings.max_message_length:
            raise ValueError(f"message too long (max {settings.max_message_length} chars)")
        return v

    @field_validator("image_context")
    @classmethod
    def _image_context_sane(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if len(v) > 4000:
            raise ValueError("image_context too long (max 4000 chars)")
        return v or None

class MemoryHit(BaseModel):
    memory_id: Optional[str] = None
    content: str
    kind: str = ""
    score: float = 0.0

class ChatResponse(BaseModel):
    conversation_id: str
    reply: str
    sources: List[MemoryHit] = []
    adaptation: Dict[str, Any] = {}
    adaptations_made: List[str] = []
    tool_calls: List[Dict[str, Any]] = []

# --- Conversations (sidebar) ---
class ConversationOut(BaseModel):
    id: str
    title: str
    created_at: Any = None
    class Config:
        from_attributes = True

class ChatMessageOut(BaseModel):
    role: str
    content: str
    created_at: Any = None
    class Config:
        from_attributes = True

class ConversationDetail(BaseModel):
    id: str
    title: str
    messages: List[ChatMessageOut] = []

# --- Memory (Prd §5: view/edit/delete/export/disable) ---
class MemoryCreate(BaseModel):
    user_id: str
    kind: str = "fact"
    content: str
    importance: str = "medium"
    memory_type: str = "long_term"

class MemoryOut(BaseModel):
    id: str
    kind: str
    content: str
    importance: str
    memory_type: str
    is_active: bool
    class Config:
        from_attributes = True

# --- Profile (USER_MODEL_SPECIFICATION.txt + Phase 3) ---
class ProfileUpdate(BaseModel):
    answer_style: Optional[str] = None
    tone: Optional[str] = None
    learning_style: Optional[Dict[str, Any]] = None
    interests: Optional[List[str]] = None
    goals: Optional[List[str]] = None
    skills: Optional[List[str]] = None
    explanation_depth: Optional[str] = None
    communication_format: Optional[str] = None
    teaching_style: Optional[str] = None
    expertise_level: Optional[str] = None

class ProfileOut(BaseModel):
    answer_style: str
    tone: str
    learning_style: Dict[str, Any]
    interests: List[str]
    goals: List[str]
    skills: List[str]
    explanation_depth: str = "balanced"
    communication_format: str = "chat"
    teaching_style: str = "examples"
    expertise_level: str = "intermediate"
    behaviour_signals: Dict[str, Any] = {}
    class Config:
        from_attributes = True

# --- Phase 2: semantic search ---
class MemorySearchRequest(BaseModel):
    user_id: str
    query: str
    top_k: int = 5

# --- Phase 4: agent ---
class AgentRequest(BaseModel):
    user_id: str
    message: str

class ToolCallOut(BaseModel):
    tool: str
    args: Dict[str, Any] = {}
    ok: bool = True
    result: Any = None

class AgentResponse(BaseModel):
    reply: str
    steps: List[str] = []
    tool_calls: List[ToolCallOut] = []

class NoteOut(BaseModel):
    id: str
    title: str
    content: str
    class Config:
        from_attributes = True

class EventCreate(BaseModel):
    title: str
    due: str = ""

class EventOut(BaseModel):
    id: str
    title: str
    due: str
    done: bool
    class Config:
        from_attributes = True

# --- Phase 6: goals ---
class GoalOut(BaseModel):
    id: str
    title: str
    status: str
    progress: int
    milestones: list = []
    class Config:
        from_attributes = True
