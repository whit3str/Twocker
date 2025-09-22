"""Secure API routes with enhanced error handling."""
import asyncio
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

from src.api.security import security_service
from src.config.settings import settings
from src.models.schemas import SettingsUpdate, BotToggle, ApiResponse, HealthCheck
from src.services.state import app_state
from src.services.twitch_api import twitch_api
from src.services.cache import twitch_cache


# Router setup
router = APIRouter()
templates = Jinja2Templates(directory="templates")


async def log_event_generator():
    """Generate SSE events for logs."""
    # Send existing logs
    for msg in reversed(list(app_state.log_messages)):
        yield {
            "event": "message",
            "data": msg
        }
    
    # Stream new logs
    try:
        while True:
            message = await app_state.log_queue.get()
            yield {
                "event": "message", 
                "data": message
            }
    except asyncio.CancelledError:
        pass


@router.get("/", response_class=HTMLResponse)
async def root(request: Request, username: str = Depends(security_service.get_auth_dependency())):
    """Serve the main page with secure context."""
    # Initialize bot username if not already done
    if not app_state.bot_username:
        await app_state.initialize_bot_username()
    
    return templates.TemplateResponse(
        "index_new.html",
        {
            "request": request,
            "current_channel": app_state.current_channel,
            "current_message": app_state.current_message,
            "current_interval": app_state.current_interval,
            "ignore_live_status": app_state.ignore_live_status,
            "random_interval": settings.random_interval,
            "random_min_interval": settings.random_min_interval,
            "random_max_interval": settings.random_max_interval,
            "is_active": app_state.is_bot_active,
            "bot_username": app_state.get_bot_username(),
            "authenticated": username != "anonymous",
            # Note: NO token exposure here - Security fixed!
        }
    )


@router.get('/logs')
async def logs(request: Request, username: str = Depends(security_service.get_auth_dependency())):
    """Stream logs via Server-Sent Events."""
    return EventSourceResponse(log_event_generator())


@router.post("/update_settings", response_model=ApiResponse)
async def update_settings(
    settings: SettingsUpdate,
    username: str = Depends(security_service.get_auth_dependency())
):
    """Update bot settings with validation."""
    try:
        await app_state.update_settings(
            settings.channel,
            settings.message, 
            settings.interval,
            settings.ignore_live_status,
            settings.random_interval,
            settings.random_min_interval,
            settings.random_max_interval
        )
        
        return ApiResponse(
            status="success",
            message=f"Settings updated for channel {settings.channel}",
            data={
                "channel": settings.channel,
                "message": settings.message,
                "interval": settings.interval,
                "ignore_live_status": settings.ignore_live_status,
                "random_interval": settings.random_interval,
                "random_min_interval": settings.random_min_interval,
                "random_max_interval": settings.random_max_interval
            }
        )
        
    except Exception as e:
        await app_state.log_event("ERROR", f"Failed to update settings: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/toggle_bot", response_model=ApiResponse)
async def toggle_bot(username: str = Depends(security_service.get_auth_dependency())):
    """Toggle bot active state with validation."""
    try:
        new_state = not app_state.is_bot_active
        success = await app_state.toggle_bot(new_state)
        
        if not success:
            return ApiResponse(
                status="error",
                message=f"Cannot activate bot: banned from channel {app_state.current_channel}",
                data={"is_active": app_state.is_bot_active}
            )
        
        status_text = "activated" if app_state.is_bot_active else "deactivated"
        return ApiResponse(
            status="success",
            message=f"Bot {status_text} successfully",
            data={"is_active": app_state.is_bot_active}
        )
        
    except Exception as e:
        await app_state.log_event("ERROR", f"Failed to toggle bot: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/channel_status/{channel}", response_model=dict)
async def get_channel_status(
    channel: str, 
    username: str = Depends(security_service.get_auth_dependency())
):
    """Get comprehensive channel status."""
    try:
        # Input validation
        if not channel or len(channel) > 25:
            raise HTTPException(status_code=400, detail="Invalid channel name")
        
        channel = channel.lower().strip()
        
        # Parallel status checks
        is_live_task = twitch_api.is_channel_live(channel)
        is_following_task = twitch_api.is_following_channel(
            app_state.bot_instance.nick if app_state.bot_instance else "unknown", 
            channel
        )
        
        is_live, is_following = await asyncio.gather(
            is_live_task, 
            is_following_task,
            return_exceptions=True
        )
        
        # Handle potential exceptions
        if isinstance(is_live, Exception):
            is_live = False
        if isinstance(is_following, Exception):
            is_following = True
        
        # Check ban status (if we have a bot instance)
        is_banned = False
        if app_state.bot_instance:
            try:
                is_banned = await app_state.bot_instance._is_banned_from_channel(channel)
            except Exception:
                is_banned = False
        
        return {
            "channel": channel,
            "is_live": is_live,
            "is_following": is_following,
            "is_banned": is_banned,
            "last_checked": datetime.utcnow().isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        await app_state.log_event("ERROR", f"Error getting channel status for {channel}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get channel status")


@router.get("/channel_emotes/{channel}", response_model=dict)
async def get_channel_emotes(
    channel: str,
    username: str = Depends(security_service.get_auth_dependency())
):
    """Get channel emotes."""
    try:
        if not channel or len(channel) > 25:
            raise HTTPException(status_code=400, detail="Invalid channel name")
        
        channel = channel.lower().strip()
        emotes = await twitch_api.get_channel_emotes(channel)
        
        return {"emotes": emotes}
        
    except HTTPException:
        raise
    except Exception as e:
        await app_state.log_event("ERROR", f"Error fetching emotes for {channel}: {str(e)}")
        return {"emotes": []}


@router.get("/health", response_model=HealthCheck)
async def health_check():
    """Health check endpoint."""
    return HealthCheck(
        status="healthy",
        timestamp=datetime.utcnow(),
        bot_active=app_state.is_bot_active,
        uptime_seconds=app_state.get_uptime()
    )


@router.get("/bot_info", response_model=dict)
async def get_bot_info(username: str = Depends(security_service.get_auth_dependency())):
    """Get bot information."""
    try:
        if not app_state.bot_username:
            await app_state.initialize_bot_username()
        
        return {
            "username": app_state.get_bot_username(),
            "active": app_state.is_bot_active,
            "current_channel": app_state.current_channel
        }
    except Exception as e:
        await app_state.log_event("ERROR", f"Error getting bot info: {str(e)}")
        return {
            "username": "TwockerBot",
            "active": app_state.is_bot_active,
            "current_channel": app_state.current_channel
        }


@router.get("/cache_stats", response_model=dict)
async def get_cache_stats(username: str = Depends(security_service.get_auth_dependency())):
    """Get cache statistics."""
    try:
        return {
            "twitch_cache": twitch_cache.get_stats(),
            "active_sessions": len(app_state.active_sessions),
            "uptime_seconds": app_state.get_uptime()
        }
    except Exception as e:
        await app_state.log_event("ERROR", f"Error getting cache stats: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get cache statistics")


# Legacy endpoints for backward compatibility (will be deprecated)
@router.get("/check_live/{channel}")
async def check_live_legacy(channel: str, username: str = Depends(security_service.get_auth_dependency())):
    """Legacy endpoint - use /channel_status instead."""
    status = await get_channel_status(channel, username)
    return {"is_live": status["is_live"]}


@router.get("/check_follow/{channel}")
async def check_follow_legacy(channel: str, username: str = Depends(security_service.get_auth_dependency())):
    """Legacy endpoint - use /channel_status instead."""
    status = await get_channel_status(channel, username)
    return {"is_following": status["is_following"]}


@router.get("/check_ban/{channel}")
async def check_ban_legacy(channel: str, username: str = Depends(security_service.get_auth_dependency())):
    """Legacy endpoint - use /channel_status instead."""
    status = await get_channel_status(channel, username)
    return {"is_banned": status["is_banned"]}