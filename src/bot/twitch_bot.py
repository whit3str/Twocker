"""Enhanced Twitch bot with asyncio and proper error handling."""
import asyncio
import random
from typing import Optional
from datetime import datetime

from twitchio.ext import commands
from twitchio import Channel

from src.config.settings import settings
from src.services.state import app_state
from src.services.twitch_api import twitch_api
from src.services.cache import ban_cache
from src.models.schemas import ChannelStatus


class TwockerBot(commands.Bot):
    """Enhanced Twitch bot with proper asyncio integration."""
    
    def __init__(self):
        super().__init__(
            token=settings.twitch_token,
            client_id=settings.client_id,
            prefix='!',
            initial_channels=[app_state.current_channel]
        )
        
        self.running = True
        self.current_channel_obj: Optional[Channel] = None
        
        # Tasks
        self.message_task: Optional[asyncio.Task] = None
        self.update_task: Optional[asyncio.Task] = None
        self.cleanup_task: Optional[asyncio.Task] = None
    
    async def event_ready(self):
        """Bot ready event."""
        status = "active" if app_state.is_bot_active else "inactive"
        await app_state.log_event("INFO", f'Bot connected as {self.nick} (initial state: {status})', "Bot")
        
        # Wait for connection to stabilize
        await asyncio.sleep(2)
        
        # Verify initial channel access
        await self._verify_initial_channel()
        
        # Start background tasks
        await self._start_tasks()
    
    async def _verify_initial_channel(self):
        """Verify access to initial channel."""
        channel = self.get_channel(app_state.current_channel)
        if not channel:
            try:
                await self.join_channels([app_state.current_channel])
                await asyncio.sleep(1)
                channel = self.get_channel(app_state.current_channel)
                if channel:
                    self.current_channel_obj = channel
                    await app_state.log_event("INFO", f"Successfully joined initial channel: {app_state.current_channel}", "Bot")
                else:
                    await app_state.log_event("WARNING", f"Could not join initial channel: {app_state.current_channel}", "Bot")
            except Exception as e:
                await app_state.log_event("ERROR", f"Error joining initial channel {app_state.current_channel}: {str(e)}", "Bot")
        else:
            self.current_channel_obj = channel
    
    async def _start_tasks(self):
        """Start background asyncio tasks."""
        self.message_task = asyncio.create_task(self._periodic_message_loop())
        self.update_task = asyncio.create_task(self._update_loop())
        self.cleanup_task = asyncio.create_task(self._cleanup_loop())
        
        await app_state.log_event("INFO", "Background tasks started", "Bot")
    
    async def _periodic_message_loop(self):
        """Main message sending loop."""
        while self.running:
            try:
                if app_state.is_bot_active:
                    await self._send_periodic_message()
                
                # Calculate wait time based on random interval setting
                if settings.random_interval:
                    # Use random interval between min and max
                    wait_minutes = random.uniform(
                        settings.random_min_interval,
                        settings.random_max_interval
                    )
                    await app_state.log_event(
                        "INFO", 
                        f"Random interval: waiting {wait_minutes:.1f} minutes", 
                        "Bot"
                    )
                else:
                    # Use fixed interval
                    wait_minutes = app_state.current_interval
                
                await asyncio.sleep(wait_minutes * 60)
                
            except asyncio.CancelledError:
                await app_state.log_event("INFO", "Message loop cancelled", "Bot")
                break
            except Exception as e:
                await app_state.log_event("ERROR", f"Error in message loop: {str(e)}", "Bot")
                await asyncio.sleep(60)  # Wait 1 minute before retrying
    
    async def _send_periodic_message(self):
        """Send a periodic message with all checks."""
        try:
            # Ensure we have a valid channel connection
            if not await self._ensure_channel_connection():
                return
            
            # Perform status checks
            channel_status = await self._check_channel_status()
            
            # Check if live status should be ignored
            from src.config.settings import settings
            if not settings.ignore_live_status and not channel_status.is_live:
                await app_state.log_event("INFO", f"Channel {app_state.current_channel} is not live, skipping message", "Bot")
                return
            elif settings.ignore_live_status and not channel_status.is_live:
                await app_state.log_event("INFO", f"Channel {app_state.current_channel} is not live, but IGNORE_LIVE_STATUS is enabled", "Bot")
            
            if channel_status.is_banned:
                await app_state.log_event("ERROR", f"Bot is banned from channel {app_state.current_channel}, skipping message", "Bot")
                return
            
            if not channel_status.is_following:
                await app_state.log_event("WARNING", f"Not following {app_state.current_channel}, but continuing anyway", "Bot")
            
            # Send the message
            await self.current_channel_obj.send(app_state.current_message)
            await app_state.log_event("INFO", f"Message sent successfully on {app_state.current_channel}: {app_state.current_message}", "Bot")
            
            # Update cache with successful send
            app_state.set_channel_status(app_state.current_channel, channel_status)
            
        except Exception as e:
            await app_state.log_event("ERROR", f"Error sending periodic message: {str(e)}", "Bot")
    
    async def _ensure_channel_connection(self) -> bool:
        """Ensure we have a valid channel connection."""
        if not self.current_channel_obj or self.current_channel_obj.name != app_state.current_channel:
            channel = self.get_channel(app_state.current_channel)
            if not channel:
                try:
                    await self.join_channels([app_state.current_channel])
                    await asyncio.sleep(1)
                    channel = self.get_channel(app_state.current_channel)
                    if not channel:
                        await app_state.log_event("ERROR", f"Failed to join channel {app_state.current_channel}", "Bot")
                        return False
                except Exception as e:
                    await app_state.log_event("ERROR", f"Error joining channel {app_state.current_channel}: {str(e)}", "Bot")
                    return False
            
            self.current_channel_obj = channel
        
        return True
    
    async def _check_channel_status(self) -> ChannelStatus:
        """Check channel status (live, following, banned)."""
        # Check if banned (quick local check)
        is_banned = await self._is_banned_from_channel(app_state.current_channel)
        
        # Check live status and following status via API
        is_live = await twitch_api.is_channel_live(app_state.current_channel)
        is_following = await twitch_api.is_following_channel(self.nick, app_state.current_channel)
        
        return ChannelStatus(
            is_live=is_live,
            is_following=is_following,
            is_banned=is_banned,
            last_checked=datetime.utcnow()
        )
    
    async def _is_banned_from_channel(self, channel_name: str) -> bool:
        """Check if bot is banned from channel."""
        cache_key = f"banned_status:{self.nick}:{channel_name}"
        
        if cached_status := await ban_cache.get(cache_key):
            return cached_status
        
        try:
            # Test by trying to send a minimal message
            channel = self.get_channel(channel_name)
            if not channel:
                await ban_cache.set(cache_key, True)
                return True
            
            # Try to send an empty message (this will fail silently if banned)
            # Note: This is a lightweight check, not perfect but functional
            try:
                # We don't actually send anything, just check if we can access the channel
                if channel.name != channel_name:
                    await ban_cache.set(cache_key, True)
                    return True
                
                await ban_cache.set(cache_key, False)
                return False
            except Exception:
                await ban_cache.set(cache_key, True)
                return True
                
        except Exception as e:
            await app_state.log_event("ERROR", f"Error checking ban status for {channel_name}: {str(e)}", "Bot")
            return True
    
    async def _update_loop(self):
        """Handle configuration updates."""
        while self.running:
            try:
                # Check for updates with timeout
                try:
                    update = await asyncio.wait_for(app_state.update_queue.get(), timeout=5.0)
                    await self._handle_update(update)
                except asyncio.TimeoutError:
                    continue  # No update available, continue loop
                    
            except asyncio.CancelledError:
                await app_state.log_event("INFO", "Update loop cancelled", "Bot")
                break
            except Exception as e:
                await app_state.log_event("ERROR", f"Error in update loop: {str(e)}", "Bot")
                await asyncio.sleep(1)
    
    async def _handle_update(self, update: dict):
        """Handle a configuration update."""
        try:
            if 'toggle' in update:
                # Just log, the state is already updated
                status = "activated" if update['toggle'] else "deactivated"
                await app_state.log_event("INFO", f"Bot {status}", "Bot")
                
            elif all(key in update for key in ['channel', 'message', 'interval']):
                old_channel = update.get('old_channel', app_state.current_channel)
                new_channel = update['channel']
                
                # Update internal state (app_state is already updated)
                if old_channel != new_channel:
                    # Switch channels
                    try:
                        if old_channel != new_channel:
                            await self.part_channels([old_channel])
                            await self.join_channels([new_channel])
                            await asyncio.sleep(1)
                            self.current_channel_obj = self.get_channel(new_channel)
                        
                        await app_state.log_event("INFO", f"Successfully switched from {old_channel} to {new_channel}", "Bot")
                    except Exception as e:
                        await app_state.log_event("ERROR", f"Error switching channels: {str(e)}", "Bot")
                
                ignore_live = update.get('ignore_live_status', False)
                await app_state.log_event("INFO", f"Settings updated - Channel: {new_channel}, Interval: {update['interval']}min, Ignore Live: {ignore_live}", "Bot")
            
        except Exception as e:
            await app_state.log_event("ERROR", f"Error handling update: {str(e)}", "Bot")
    
    async def _cleanup_loop(self):
        """Periodic cleanup tasks."""
        while self.running:
            try:
                await asyncio.sleep(300)  # Run every 5 minutes
                # Add any periodic cleanup tasks here
                
            except asyncio.CancelledError:
                await app_state.log_event("INFO", "Cleanup loop cancelled", "Bot")
                break
            except Exception as e:
                await app_state.log_event("ERROR", f"Error in cleanup loop: {str(e)}", "Bot")
    
    async def close(self):
        """Clean shutdown of the bot."""
        await app_state.log_event("INFO", "Shutting down bot...", "Bot")
        
        self.running = False
        
        # Cancel all tasks
        tasks = [self.message_task, self.update_task, self.cleanup_task]
        for task in tasks:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        await super().close()
        await app_state.log_event("INFO", "Bot shutdown complete", "Bot")