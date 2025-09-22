"""Enhanced caching service."""
import asyncio
from datetime import datetime, timedelta
from typing import Any, Optional, Dict
from src.config.settings import settings


class TwitchCache:
    """Enhanced cache with TTL and metrics."""
    
    def __init__(self, default_expiry: int = None):
        self.cache: Dict[str, tuple[float, Any]] = {}
        self.default_expiry = default_expiry or settings.cache_expiry
        self.hits = 0
        self.misses = 0
        self.lock = asyncio.Lock()
    
    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache with metrics."""
        async with self.lock:
            if key in self.cache:
                timestamp, value = self.cache[key]
                if datetime.utcnow().timestamp() - timestamp < self.default_expiry:
                    self.hits += 1
                    return value
                # Expired, remove it
                del self.cache[key]
            
            self.misses += 1
            return None
    
    async def set(self, key: str, value: Any, expiry: Optional[int] = None):
        """Set value in cache with optional custom expiry."""
        async with self.lock:
            self.cache[key] = (datetime.utcnow().timestamp(), value)
    
    async def invalidate(self, key: Optional[str] = None):
        """Invalidate cache entry or entire cache."""
        async with self.lock:
            if key:
                self.cache.pop(key, None)
            else:
                self.cache.clear()
    
    async def cleanup_expired(self):
        """Remove expired entries."""
        current_time = datetime.utcnow().timestamp()
        async with self.lock:
            expired_keys = [
                key for key, (timestamp, _) in self.cache.items()
                if current_time - timestamp >= self.default_expiry
            ]
            for key in expired_keys:
                del self.cache[key]
    
    def get_stats(self) -> dict:
        """Get cache statistics."""
        total_requests = self.hits + self.misses
        hit_rate = (self.hits / total_requests * 100) if total_requests > 0 else 0
        
        return {
            'hits': self.hits,
            'misses': self.misses,
            'hit_rate': round(hit_rate, 2),
            'cache_size': len(self.cache)
        }
    
    async def start_cleanup_task(self):
        """Start periodic cleanup task."""
        while True:
            await asyncio.sleep(300)  # Cleanup every 5 minutes
            await self.cleanup_expired()


# Cache instances
twitch_cache = TwitchCache(settings.cache_expiry)
ban_cache = TwitchCache(settings.ban_cache_expiry)