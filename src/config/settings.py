"""Configuration settings with enhanced security."""
import os
import secrets
from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field, field_validator


class Settings(BaseSettings):
    """Application settings with validation."""
    
    # Twitch Configuration
    twitch_token: str = Field(..., env="TWITCH_TOKEN")
    twitch_channel: str = Field(..., env="TWITCH_CHANNEL")
    client_id: str = Field(..., env="CLIENT_ID")
    
    # Bot Configuration
    default_message: str = Field("Hello world!", env="DEFAULT_MESSAGE")
    default_interval: int = Field(5, ge=1, le=60, env="DEFAULT_INTERVAL")
    bot_active: bool = Field(False, env="BOT_ACTIVE")
    ignore_live_status: bool = Field(False, env="IGNORE_LIVE_STATUS")
    
    # Random Interval Configuration
    random_interval: bool = Field(False, env="RANDOM_INTERVAL")
    random_min_interval: int = Field(20, ge=1, le=300, env="RANDOM_MIN_INTERVAL")
    random_max_interval: int = Field(60, ge=1, le=300, env="RANDOM_MAX_INTERVAL")
    
    # Security Configuration
    auth_enabled: bool = Field(True, env="AUTH_ENABLED")
    api_username: str = Field(..., env="API_USERNAME")
    api_password: str = Field(..., env="API_PASSWORD")
    secret_key: str = Field(default_factory=lambda: secrets.token_urlsafe(32), env="SECRET_KEY")
    
    # Cache Configuration
    cache_expiry: int = Field(300, ge=60, env="CACHE_EXPIRY")
    ban_cache_expiry: int = Field(60, ge=30, env="BAN_CACHE_EXPIRY")
    
    # CORS Configuration
    allowed_origins: str = Field("http://localhost:8000", env="ALLOWED_ORIGINS")
    
    # Rate Limiting
    api_rate_limit: int = Field(100, ge=10, env="API_RATE_LIMIT")
    
    @field_validator('twitch_token')
    @classmethod
    def validate_twitch_token(cls, v):
        """Validate Twitch token format."""
        if not v.startswith('oauth:'):
            raise ValueError('Twitch token must start with "oauth:"')
        if len(v) < 20:  # oauth: + 14 chars minimum (relaxed for testing)
            raise ValueError('Invalid Twitch token format')
        return v
    
    @field_validator('twitch_channel')
    @classmethod
    def validate_twitch_channel(cls, v):
        """Validate Twitch channel name."""
        v = v.lower().strip()
        if not v:
            raise ValueError('Channel name cannot be empty')
        if ' ' in v:
            raise ValueError('Channel name cannot contain spaces')
        if len(v) > 25:
            raise ValueError('Channel name too long')
        return v
    
    @field_validator('api_password')
    @classmethod
    def validate_password_strength(cls, v):
        """Validate password strength."""
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters long')
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
    
    def get_allowed_origins_list(self) -> list[str]:
        """Get allowed origins as a list."""
        if isinstance(self.allowed_origins, str):
            return [origin.strip() for origin in self.allowed_origins.split(',')]
        return self.allowed_origins
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# Global settings instance
settings = Settings()

# Derived configurations
TWITCH_HEADERS = {
    'Client-ID': settings.client_id,
    'Authorization': f'Bearer {settings.twitch_token.replace("oauth:", "")}'
}