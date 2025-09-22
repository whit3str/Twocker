"""Enhanced Twitch API service with connection pooling."""
import asyncio
import aiohttp
from typing import Optional, Dict, Any, List
from datetime import datetime

from src.config.settings import settings, TWITCH_HEADERS
from src.services.cache import twitch_cache, ban_cache
from src.services.state import app_state


class TwitchAPIError(Exception):
    """Custom exception for Twitch API errors."""
    pass


class TwitchAPIService:
    """Enhanced Twitch API service with connection pooling and retry logic."""
    
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.connector: Optional[aiohttp.TCPConnector] = None
    
    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
    
    async def start(self):
        """Initialize HTTP session with connection pooling."""
        if not self.session:
            self.connector = aiohttp.TCPConnector(
                limit=100,
                limit_per_host=20,
                ttl_dns_cache=300,
                use_dns_cache=True,
            )
            
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            
            self.session = aiohttp.ClientSession(
                connector=self.connector,
                timeout=timeout,
                headers=TWITCH_HEADERS
            )
    
    async def close(self):
        """Close HTTP session and connector."""
        if self.session:
            await self.session.close()
            self.session = None
        if self.connector:
            await self.connector.close()
            self.connector = None
    
    async def _retry_api_call(self, func, *args, max_retries: int = 3, **kwargs) -> Any:
        """Retry API calls with exponential backoff."""
        last_exception = None
        
        for attempt in range(max_retries):
            try:
                return await func(*args, **kwargs)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_exception = e
                wait_time = 2 ** (attempt + 1)
                
                await app_state.log_event(
                    "WARNING", 
                    f"API call failed, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries}): {str(e)}"
                )
                
                if attempt < max_retries - 1:
                    await asyncio.sleep(wait_time)
                else:
                    await app_state.log_event(
                        "ERROR", 
                        f"API call failed after {max_retries} attempts: {str(e)}"
                    )
                    raise TwitchAPIError(f"API call failed after {max_retries} retries") from e
        
        raise TwitchAPIError("Unexpected error in retry logic") from last_exception
    
    async def get_user_id(self, username: str) -> Optional[str]:
        """Get user ID by username with caching."""
        cache_key = f"user_id:{username.lower()}"
        
        if cached_id := await twitch_cache.get(cache_key):
            return cached_id
        
        async def fetch_user_id():
            if not self.session:
                await self.start()
            
            async with self.session.get(
                f'https://api.twitch.tv/helix/users?login={username.lower()}'
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('data'):
                        return data['data'][0]['id']
                elif resp.status == 401:
                    raise TwitchAPIError("Invalid Twitch token")
                elif resp.status == 429:
                    raise TwitchAPIError("Rate limit exceeded")
                return None
        
        try:
            user_id = await self._retry_api_call(fetch_user_id)
            if user_id:
                await twitch_cache.set(cache_key, user_id)
            return user_id
        except TwitchAPIError:
            raise
        except Exception as e:
            await app_state.log_event("ERROR", f"Error fetching user ID for {username}: {str(e)}")
            return None
    
    async def is_channel_live(self, username: str) -> bool:
        """Check if channel is live with caching."""
        cache_key = f"live_status:{username.lower()}"
        
        if cached_status := await twitch_cache.get(cache_key):
            return cached_status
        
        try:
            user_id = await self.get_user_id(username)
            if not user_id:
                return False
            
            async def fetch_live_status():
                if not self.session:
                    await self.start()
                
                async with self.session.get(
                    f'https://api.twitch.tv/helix/streams?user_id={user_id}'
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return len(data.get('data', [])) > 0
                    return False
            
            is_live = await self._retry_api_call(fetch_live_status)
            await twitch_cache.set(cache_key, is_live)
            return is_live
            
        except Exception as e:
            await app_state.log_event("ERROR", f"Error checking live status for {username}: {str(e)}")
            return False
    
    async def is_following_channel(self, follower_username: str, channel_username: str) -> bool:
        """Check if user is following channel with caching."""
        cache_key = f"follow_status:{follower_username.lower()}:{channel_username.lower()}"
        
        if cached_status := await twitch_cache.get(cache_key):
            return cached_status
        
        try:
            follower_id = await self.get_user_id(follower_username)
            channel_id = await self.get_user_id(channel_username)
            
            if not follower_id or not channel_id:
                return True  # Assume following if we can't verify
            
            async def fetch_follow_status():
                if not self.session:
                    await self.start()
                
                async with self.session.get(
                    f'https://api.twitch.tv/helix/channels/followed?user_id={follower_id}&broadcaster_id={channel_id}'
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return len(data.get('data', [])) > 0
                    elif resp.status == 401:
                        await app_state.log_event(
                            "WARNING", 
                            "Authentication error checking follow status. Token may lack 'user:read:follows' scope."
                        )
                        return True  # Assume following on auth error
                    return True
            
            is_following = await self._retry_api_call(fetch_follow_status)
            await twitch_cache.set(cache_key, is_following)
            return is_following
            
        except Exception as e:
            await app_state.log_event("ERROR", f"Error checking follow status: {str(e)}")
            return True  # Assume following on error
    
    async def get_bot_username(self) -> Optional[str]:
        """Get the bot's username from the token."""
        cache_key = "bot_username"
        
        if cached_username := await twitch_cache.get(cache_key):
            return cached_username
        
        async def fetch_bot_username():
            if not self.session:
                await self.start()
            
            async with self.session.get('https://api.twitch.tv/helix/users') as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('data'):
                        return data['data'][0]['display_name']
                elif resp.status == 401:
                    raise TwitchAPIError("Invalid Twitch token")
                return None
        
        try:
            username = await self._retry_api_call(fetch_bot_username)
            if username:
                await twitch_cache.set(cache_key, username)
            return username
        except TwitchAPIError:
            raise
        except Exception as e:
            await app_state.log_event("ERROR", f"Error fetching bot username: {str(e)}")
            return "TwockerBot"  # Fallback name
    
    async def get_channel_emotes(self, username: str) -> List[Dict[str, Any]]:
        """Get channel emotes."""
        try:
            user_id = await self.get_user_id(username)
            if not user_id:
                return []
            
            async def fetch_emotes():
                if not self.session:
                    await self.start()
                
                async with self.session.get(
                    f'https://api.twitch.tv/helix/chat/emotes?broadcaster_id={user_id}'
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get('data', [])
                    return []
            
            return await self._retry_api_call(fetch_emotes)
            
        except Exception as e:
            await app_state.log_event("ERROR", f"Error fetching emotes for {username}: {str(e)}")
            return []


# Global API service instance
twitch_api = TwitchAPIService()