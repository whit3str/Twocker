"""Pydantic models for data validation."""
import re
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field, field_validator


class SettingsUpdate(BaseModel):
    """Model for updating bot settings."""
    channel: str = Field(..., min_length=1, max_length=25)
    message: str = Field(..., min_length=1, max_length=500)
    interval: int = Field(..., ge=1, le=60)
    ignore_live_status: bool = Field(False)
    random_interval: bool = Field(False)
    random_min_interval: int = Field(20, ge=1, le=300)
    random_max_interval: int = Field(60, ge=1, le=300)
    
    @field_validator('channel')
    @classmethod
    def validate_channel(cls, v):
        """Validate channel name format."""
        v = v.lower().strip()
        if not v:
            raise ValueError('Channel name cannot be empty')
        if not re.match(r'^[a-zA-Z0-9_]{1,25}$', v):
            raise ValueError('Channel name can only contain letters, numbers, and underscores')
        return v
        
    @field_validator('message')
    @classmethod
    def validate_message(cls, v):
        """Validate message content."""
        v = v.strip()
        if not v:
            raise ValueError('Message cannot be empty')
        if len(v.encode('utf-8')) > 500:
            raise ValueError('Message too long (max 500 bytes)')
        return v
    
    @field_validator('random_max_interval')
    @classmethod
    def validate_random_intervals(cls, v, info):
        """Validate that max interval is greater than min interval."""
        if hasattr(info.data, 'random_min_interval') and info.data.get('random_min_interval'):
            min_val = info.data.get('random_min_interval')
            if v <= min_val:
                raise ValueError('random_max_interval must be greater than random_min_interval')
        return v


class BotToggle(BaseModel):
    """Model for toggling bot state."""
    active: bool


class ChannelStatus(BaseModel):
    """Model for channel status information."""
    is_live: bool
    is_following: bool
    is_banned: bool
    last_checked: datetime


class LogEntry(BaseModel):
    """Model for log entries."""
    timestamp: datetime
    level: str = Field(..., pattern=r'^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$')
    message: str
    source: Optional[str] = None


class ApiResponse(BaseModel):
    """Standard API response model."""
    status: str = Field(..., pattern=r'^(success|error)$')
    message: str
    data: Optional[dict] = None


class HealthCheck(BaseModel):
    """Health check response model."""
    status: str
    timestamp: datetime
    bot_active: bool
    uptime_seconds: int