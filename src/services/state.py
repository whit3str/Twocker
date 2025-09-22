"""Application state management."""
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from collections import deque

from src.config.settings import settings
from src.models.schemas import ChannelStatus, LogEntry


class ApplicationState:
    """Centralized application state manager."""
    
    def __init__(self):
        # Bot configuration
        self.current_channel = settings.twitch_channel
        self.current_message = settings.default_message
        self.current_interval = settings.default_interval
        self.is_bot_active = settings.bot_active
        self.ignore_live_status = settings.ignore_live_status
        
        # Application status
        self.start_time = datetime.utcnow()
        self.bot_instance: Optional[Any] = None
        self.bot_username: Optional[str] = None
        
        # Communication queues
        self.update_queue = asyncio.Queue()
        self.log_queue = asyncio.Queue()
        
        # Logging
        self.log_messages = deque(maxlen=100)  # Increased from 50
        
        # Cache for channel statuses
        self.channel_statuses: Dict[str, ChannelStatus] = {}
        
        # Session management
        self.active_sessions: set = set()
    
    async def update_settings(self, channel: str, message: str, interval: int, ignore_live_status: bool = False,
                            random_interval: bool = False, random_min_interval: int = 20, random_max_interval: int = 60):
        """Update bot settings safely."""
        old_channel = self.current_channel
        
        self.current_channel = channel
        self.current_message = message
        self.current_interval = interval
        self.ignore_live_status = ignore_live_status
        
        # Queue update for bot
        await self.update_queue.put({
            'channel': channel,
            'message': message,
            'interval': interval,
            'ignore_live_status': ignore_live_status,
            'random_interval': random_interval,
            'random_min_interval': random_min_interval,
            'random_max_interval': random_max_interval,
            'old_channel': old_channel
        })
        
        await self.log_event("INFO", f"Settings updated - Channel: {channel}, Interval: {interval}min, Ignore Live: {ignore_live_status}, Random: {random_interval}")
    
    async def toggle_bot(self, active: bool) -> bool:
        """Toggle bot active state with validation."""
        if active and self.bot_instance:
            # Perform pre-activation checks
            channel_status = await self.get_channel_status(self.current_channel)
            if channel_status and channel_status.is_banned:
                await self.log_event("ERROR", f"Cannot activate: banned from {self.current_channel}")
                return False
        
        self.is_bot_active = active
        await self.update_queue.put({'toggle': active})
        
        status = "activated" if active else "deactivated"
        await self.log_event("INFO", f"Bot {status}")
        return True
    
    async def log_event(self, level: str, message: str, source: Optional[str] = None):
        """Add a log entry."""
        timestamp = datetime.utcnow()
        log_entry = LogEntry(
            timestamp=timestamp,
            level=level,
            message=message,
            source=source
        )
        
        # Format for display
        formatted_message = f"{timestamp.isoformat()} [{level}] {message}"
        if source:
            formatted_message += f" ({source})"
        
        self.log_messages.appendleft(formatted_message)
        await self.log_queue.put(formatted_message)
    
    async def get_channel_status(self, channel: str) -> Optional[ChannelStatus]:
        """Get cached channel status."""
        return self.channel_statuses.get(channel)
    
    def set_channel_status(self, channel: str, status: ChannelStatus):
        """Update channel status cache."""
        self.channel_statuses[channel] = status
    
    def get_uptime(self) -> int:
        """Get application uptime in seconds."""
        return int((datetime.utcnow() - self.start_time).total_seconds())
    
    def add_session(self, session_id: str):
        """Add active session."""
        self.active_sessions.add(session_id)
    
    def remove_session(self, session_id: str):
        """Remove active session."""
        self.active_sessions.discard(session_id)
    
    async def initialize_bot_username(self):
        """Initialize the bot username from Twitch API."""
        if not self.bot_username:
            from src.services.twitch_api import twitch_api
            try:
                self.bot_username = await twitch_api.get_bot_username()
                if self.bot_username:
                    await self.log_event("INFO", f"Bot username initialized: {self.bot_username}")
                else:
                    self.bot_username = "TwockerBot"
                    await self.log_event("WARNING", "Could not fetch bot username, using default: TwockerBot")
            except Exception as e:
                self.bot_username = "TwockerBot"
                await self.log_event("ERROR", f"Error initializing bot username: {str(e)}")
    
    def get_bot_username(self) -> str:
        """Get the bot username, with fallback."""
        return self.bot_username or "TwockerBot"
    
    async def cleanup(self):
        """Cleanup resources."""
        if self.bot_instance:
            await self.log_event("INFO", "Cleaning up bot instance")
            # Additional cleanup logic here


# Global state instance
app_state = ApplicationState()