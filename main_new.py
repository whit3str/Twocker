"""Main application entry point with enhanced FastAPI setup."""
import asyncio
import signal
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
import uvicorn

from src.config.settings import settings
from src.services.state import app_state
from src.services.twitch_api import twitch_api
from src.services.cache import twitch_cache
from src.bot.twitch_bot import TwockerBot
from src.api.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    await app_state.log_event("INFO", "Application starting...")
    
    try:
        # Initialize Twitch API service
        await twitch_api.start()
        await app_state.log_event("INFO", "Twitch API service initialized")
        
        # Initialize bot username
        await app_state.initialize_bot_username()
        
        # Start cache cleanup task
        asyncio.create_task(twitch_cache.start_cleanup_task())
        
        # Initialize and start bot
        bot = TwockerBot()
        app_state.bot_instance = bot
        
        # Start bot in background task
        bot_task = asyncio.create_task(bot.start())
        await app_state.log_event("INFO", "Bot started")
        
        yield
        
    except Exception as e:
        await app_state.log_event("ERROR", f"Startup error: {str(e)}")
        raise
    
    finally:
        # Shutdown
        await app_state.log_event("INFO", "Application shutting down...")
        
        try:
            # Close bot
            if app_state.bot_instance:
                await app_state.bot_instance.close()
            
            # Close Twitch API service
            await twitch_api.close()
            
            # Cleanup state
            await app_state.cleanup()
            
            await app_state.log_event("INFO", "Application shutdown complete")
            
        except Exception as e:
            print(f"Error during shutdown: {e}")


# FastAPI application with enhanced configuration
app = FastAPI(
    title="Twocker",
    description="Enhanced Twitch Bot with Web Interface",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/api/docs" if not settings.auth_enabled else None,
    redoc_url="/api/redoc" if not settings.auth_enabled else None,
)

# Security middleware
app.add_middleware(
    TrustedHostMiddleware, 
    allowed_hosts=["localhost", "127.0.0.1", "0.0.0.0", "*"]
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_allowed_origins_list(),
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Static files
app.mount("/assets", StaticFiles(directory="assets"), name="assets")

# Include API routes
app.include_router(router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler."""
    await app_state.log_event("ERROR", f"Unhandled exception: {str(exc)}")
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": "Internal server error"}
    )


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all requests."""
    start_time = asyncio.get_event_loop().time()
    
    response = await call_next(request)
    
    process_time = asyncio.get_event_loop().time() - start_time
    
    # Only log non-static requests
    if not request.url.path.startswith("/assets"):
        await app_state.log_event(
            "INFO", 
            f"{request.method} {request.url.path} - {response.status_code} ({process_time:.3f}s)"
        )
    
    return response


def shutdown_handler(signum, frame):
    """Handle shutdown signals."""
    print(f"Received signal {signum}, shutting down...")
    # FastAPI will handle the shutdown via lifespan


def main():
    """Main entry point."""
    # Set up signal handlers
    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)
    
    # Run the application
    uvicorn.run(
        "main_new:app",
        host="0.0.0.0",
        port=8000,
        log_level="info",
        access_log=False,  # We handle logging ourselves
        server_header=False,
        date_header=False,
    )


if __name__ == "__main__":
    main()