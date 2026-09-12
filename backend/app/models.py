import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, JSON, Boolean, Integer
from sqlalchemy.orm import relationship
from .database import Base

def uid():
    return str(uuid.uuid4())

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=uid)
    email = Column(String, unique=True, index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    conversations = relationship("Conversation", back_populates="user", cascade="all, delete-orphan")
    profile = relationship("UserProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    memories = relationship("Memory", back_populates="user", cascade="all, delete-orphan")
    notes = relationship("Note", back_populates="user", cascade="all, delete-orphan")
    events = relationship("CalendarEvent", back_populates="user", cascade="all, delete-orphan")

class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(String, primary_key=True, default=uid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    title = Column(String, default="New conversation")
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")

class Message(Base):
    __tablename__ = "messages"
    id = Column(String, primary_key=True, default=uid)
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False)
    role = Column(String, nullable=False)  # user | assistant | system
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    conversation = relationship("Conversation", back_populates="messages")

class UserProfile(Base):
    # USER_MODEL_SPECIFICATION.txt — private per-user profile
    # Phase 3: explanation depth, tone, format, teaching style, expertise + signals
    __tablename__ = "user_profiles"
    id = Column(String, primary_key=True, default=uid)
    user_id = Column(String, ForeignKey("users.id"), unique=True, nullable=False)
    answer_style = Column(String, default="detailed")
    tone = Column(String, default="casual")
    learning_style = Column(JSON, default=dict)  # {"visual": true, "examples": true}
    interests = Column(JSON, default=list)
    goals = Column(JSON, default=list)
    skills = Column(JSON, default=list)
    explanation_depth = Column(String, default="balanced")  # concise | balanced | detailed
    communication_format = Column(String, default="chat")  # chat | bullets | tutorial | code-first
    teaching_style = Column(String, default="examples")  # examples | theory | hands-on
    expertise_level = Column(String, default="intermediate")  # beginner | intermediate | advanced
    behaviour_signals = Column(JSON, default=dict)  # counters + observations
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="profile")

class Memory(Base):
    # Prd.txt §2 + Memory_Architecture.txt — facts / preferences / experiences / behaviour
    __tablename__ = "memories"
    id = Column(String, primary_key=True, default=uid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    kind = Column(String, nullable=False)  # fact | preference | experience | behaviour
    content = Column(Text, nullable=False)
    importance = Column(String, default="medium")  # low | medium | high
    memory_type = Column(String, default="long_term")  # short_term | long_term | episodic | behaviour
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="memories")

class Note(Base):
    # Phase 4: Agent tool — user notes (Tasks: Tools/Files/Automation → Notes)
    __tablename__ = "notes"
    id = Column(String, primary_key=True, default=uid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    title = Column(String, default="")
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="notes")

class CalendarEvent(Base):
    # Phase 4: Agent tool — calendar / reminders (automation lite)
    __tablename__ = "calendar_events"
    id = Column(String, primary_key=True, default=uid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    due = Column(String, default="")  # free-text date in Phase 4 ("tomorrow", ISO, ...)
    done = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="events")

class Feedback(Base):
    # Phase 5: Learning System — Conversation → Feedback → Dataset → Fine Tuning
    __tablename__ = "feedback"
    id = Column(String, primary_key=True, default=uid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    message_id = Column(String, default="")
    rating = Column(String, default="")  # "up" | "down" | "1".."5"
    text = Column(Text, default="")  # snapshot of the rated reply (training signal)
    note = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User")

class Goal(Base):
    # Phase 6: goal tracking — dashboard, progress, milestones
    __tablename__ = "goals"
    id = Column(String, primary_key=True, default=uid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    status = Column(String, default="active")  # active | done | paused
    progress = Column(Integer, default=0)  # percent 0..100
    milestones = Column(JSON, default=list)  # [{"title": str, "done": bool}]
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User")
