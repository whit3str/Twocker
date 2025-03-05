import os
import json
import random
import secrets
import asyncio
from queue import Queue
from datetime import datetime
from threading import Thread
from collections import deque
from contextlib import asynccontextmanager

import aiohttp
from fastapi import FastAPI, Request, Depends, HTTPException, status
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sse_starlette.sse import EventSourceResponse
from twitchio.ext import commands
from pydantic import BaseModel, Field, validator
from dotenv import load_dotenv

# ===== MODULE: config.py =====
load_dotenv()

# Configuration Twitch
TWITCH_TOKEN = os.getenv('TWITCH_TOKEN')
TWITCH_CHANNEL = os.getenv('TWITCH_CHANNEL')
DEFAULT_MESSAGE = os.getenv('DEFAULT_MESSAGE', 'Default message')
DEFAULT_INTERVAL = int(os.getenv('DEFAULT_INTERVAL', '5'))
BOT_ACTIVE = os.getenv('BOT_ACTIVE', 'false').lower() == 'true'
CLIENT_ID = os.getenv('CLIENT_ID')

# Configuration de sécurité
AUTH_ENABLED = os.getenv('AUTH_ENABLED', 'false').lower() == 'true'
API_USERNAME = os.getenv('API_USERNAME', 'admin')
API_PASSWORD = os.getenv('API_PASSWORD', 'password')

# Cache configuration
CACHE_EXPIRY = int(os.getenv('CACHE_EXPIRY', '300'))  # 5 minutes en secondes
BAN_CACHE_EXPIRY = int(os.getenv('BAN_CACHE_EXPIRY', '60'))  # 1 minute en secondes (plus court car les bans peuvent être levés)

# Variables globales
current_channel = TWITCH_CHANNEL
current_message = DEFAULT_MESSAGE
current_interval = DEFAULT_INTERVAL
update_queue = Queue()
is_bot_active = BOT_ACTIVE
log_messages = deque(maxlen=50)
log_queue = asyncio.Queue()
bot = None
# ===== FIN MODULE: config.py =====

# ===== MODULE: security.py =====
security = HTTPBasic()

def validate_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    is_username_correct = secrets.compare_digest(credentials.username, API_USERNAME)
    is_password_correct = secrets.compare_digest(credentials.password, API_PASSWORD)
    
    if not (is_username_correct and is_password_correct):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

def get_auth():
    """Fonction d'authentification - active uniquement si AUTH_ENABLED est True"""
    if AUTH_ENABLED:
        return validate_credentials
    else:
        # Si l'authentification est désactivée, on retourne une fonction qui ne fait rien
        def no_auth():
            return None
        return no_auth

# Validation des entrées
class SettingsUpdate(BaseModel):
    channel: str = Field(..., min_length=1, max_length=50)
    message: str = Field(..., min_length=1, max_length=500)
    interval: int = Field(..., ge=1, le=60)
    
    @validator('channel')
    def validate_channel(cls, v):
        if not v.strip():
            raise ValueError('Channel name cannot be empty')
        if ' ' in v:
            raise ValueError('Channel name cannot contain spaces')
        return v.lower().strip()
        
    @validator('message')
    def validate_message(cls, v):
        if not v.strip():
            raise ValueError('Message cannot be empty')
        return v.strip()
# ===== FIN MODULE: security.py =====

# ===== MODULE: cache.py =====
class TwitchCache:
    def __init__(self, expiry_seconds=300):
        self.cache = {}
        self.expiry = expiry_seconds
    
    def get(self, key):
        if key in self.cache:
            timestamp, value = self.cache[key]
            if datetime.utcnow().timestamp() - timestamp < self.expiry:
                return value
            del self.cache[key]
        return None
    
    def set(self, key, value):
        self.cache[key] = (datetime.utcnow().timestamp(), value)
    
    def invalidate(self, key=None):
        if key:
            self.cache.pop(key, None)
        else:
            self.cache.clear()

# Initialisation du cache
twitch_cache = TwitchCache(CACHE_EXPIRY)
# ===== FIN MODULE: cache.py =====

# ===== MODULE: logger.py =====
async def log_event(message: str):
    timestamp = datetime.utcnow().isoformat()
    log_entry = f"{timestamp} {message}"
    log_messages.appendleft(log_entry)
    await log_queue.put(log_entry)
    return log_entry

async def event_generator():
    for msg in reversed(log_messages):
        yield {
            "event": "message",
            "data": msg
        }
    try:
        while True:
            message = await log_queue.get()
            yield {
                "event": "message",
                "data": message
            }
    except asyncio.CancelledError:
        raise
# ===== FIN MODULE: logger.py =====

# ===== MODULE: twitch_api.py =====
async def retry_api_call(func, *args, max_retries=3, **kwargs):
    for retries in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            wait_time = 2 ** (retries + 1)
            await log_event(f"API call failed, retrying in {wait_time}s ({retries + 1}/{max_retries}): {str(e)}")
            if retries < max_retries - 1:
                await asyncio.sleep(wait_time)
            else:
                await log_event(f"API call failed after {max_retries} retries: {str(e)}")
                raise
# ===== FIN MODULE: twitch_api.py =====

# ===== MODULE: twitch_bot.py =====
class Bot(commands.Bot):
    def __init__(self):
        super().__init__(
            token=TWITCH_TOKEN,
            client_id=CLIENT_ID,
            prefix='!',
            initial_channels=[current_channel]
        )
        self.message = current_message
        self.interval = current_interval
        self.channel_name = current_channel
        self.running = True
        self.is_active = BOT_ACTIVE
        self.channel_ids = {}
        self.headers = {
            'Client-ID': CLIENT_ID,
            'Authorization': f'Bearer {TWITCH_TOKEN.replace("oauth:", "")}'
        }

    async def event_ready(self):
        status = "active" if self.is_active else "inactive"
        await log_event(f'Connected as | {self.nick} (initial state: {status})')
        
        # Attendre un peu pour laisser le temps à TwitchIO de se connecter complètement
        await asyncio.sleep(2)
        
        # Vérifier si le canal initial est accessible
        channel = self.get_channel(self.channel_name)
        if not channel:
            try:
                await self.join_channels([self.channel_name])
                await asyncio.sleep(1)  # Attendre que la connexion soit établie
                channel = self.get_channel(self.channel_name)
                if channel:
                    await log_event(f"Successfully joined initial channel: {self.channel_name}")
                else:
                    await log_event(f"Warning: Could not join initial channel: {self.channel_name}")
            except Exception as e:
                await log_event(f"Error joining initial channel {self.channel_name}: {str(e)}")
        
        await self._start_tasks()

    async def _start_tasks(self):
        self.loop.create_task(self.send_periodic_message())
        self.loop.create_task(self.check_updates())
        await log_event("Tasks started")

    async def get_channel_id(self, channel_name):
        cache_key = f"channel_id:{channel_name}"
        if cached_id := twitch_cache.get(cache_key):
            return cached_id
            
        if channel_name not in self.channel_ids:
            try:
                async def fetch_channel_id():
                    async with aiohttp.ClientSession() as session:
                        async with session.get(
                            f'https://api.twitch.tv/helix/users?login={channel_name}',
                            headers=self.headers,
                            timeout=10
                        ) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                return data['data'][0]['id'] if data['data'] else None
                            return None
                
                if channel_id := await retry_api_call(fetch_channel_id, max_retries=3):
                    self.channel_ids[channel_name] = channel_id
                    twitch_cache.set(cache_key, channel_id)
                    return channel_id
                await log_event(f"Could not find channel ID for {channel_name}")
            except Exception as e:
                await log_event(f"Error getting channel ID: {str(e)}")
            return None
        return self.channel_ids[channel_name]

    async def is_channel_live(self, channel_name):
        cache_key = f"live_status:{channel_name}"
        if cached_status := twitch_cache.get(cache_key):
            return cached_status
            
        try:
            channel_id = await self.get_channel_id(channel_name)
            if not channel_id:
                return False

            async def fetch_live_status():
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                            f'https://api.twitch.tv/helix/streams?user_id={channel_id}',
                            headers=self.headers,
                            timeout=10
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            return len(data['data']) > 0
                        return False
            
            is_live = await retry_api_call(fetch_live_status, max_retries=2)
            twitch_cache.set(cache_key, is_live)
            return is_live
        except Exception as e:
            await log_event(f"Error checking live status: {str(e)}")
            return False

    async def is_channel_followed(self, channel_name):
        cache_key = f"follow_status:{self.nick}:{channel_name}"
        if cached_status := twitch_cache.get(cache_key):
            return cached_status
            
        try:
            channel_id = await self.get_channel_id(channel_name)
            if not channel_id:
                return False

            bot_id = await self.get_channel_id(self.nick)
            if not bot_id:
                return False

            async def fetch_follow_status():
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                            f'https://api.twitch.tv/helix/channels/followed?user_id={bot_id}&broadcaster_id={channel_id}',
                            headers=self.headers,
                            timeout=10
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            is_following = len(data.get('data', [])) > 0
                            return is_following
                        elif resp.status == 401:
                            await log_event(f"Authentication error checking follow status. Make sure your token has the 'user:read:follows' scope.")
                            return True
                        else:
                            return True
            
            try:
                is_following = await retry_api_call(fetch_follow_status, max_retries=2)
            except Exception as e:
                await log_event(f"Error in fetch_follow_status: {str(e)}")
                is_following = True
            
            twitch_cache.set(cache_key, is_following)
            return is_following
        except Exception as e:
            await log_event(f"Error checking follow status: {str(e)}")
            return True

    async def is_banned_from_channel(self, channel_name):
        cache_key = f"banned_status:{self.nick}:{channel_name}"
        ban_cache = TwitchCache(BAN_CACHE_EXPIRY)
        
        if cached_status := ban_cache.get(cache_key):
            return cached_status
            
        try:
            try:
                await self.part_channels([channel_name])
                await asyncio.sleep(1)
                await self.join_channels([channel_name])
                await asyncio.sleep(1)
            except Exception as e:
                ban_cache.set(cache_key, True)
                return True
            
            if not (channel := self.get_channel(channel_name)):
                ban_cache.set(cache_key, True)
                return True
                
            try:
                await channel.send(".")
                ban_cache.set(cache_key, False)
                return False
            except Exception:
                ban_cache.set(cache_key, True)
                return True
                
        except Exception as e:
            await log_event(f"Error checking ban status: {str(e)}")
            return True

    async def send_periodic_message(self):
        while self.running:
            try:
                if self.is_active:
                    if not (channel := self.get_channel(self.channel_name)):
                        await log_event(f"Channel not found: {self.channel_name}")
                        try:
                            await self.join_channels([self.channel_name])
                            if not (channel := self.get_channel(self.channel_name)):
                                raise Exception("Channel still not accessible after join")
                        except Exception as join_error:
                            await log_event(f"Failed to join channel {self.channel_name}: {str(join_error)}")
                            await asyncio.sleep(self.interval * 60)
                            continue

                    # Vérifications de statut
                    checks = {
                        'live': await self.is_channel_live(self.channel_name),
                        'banned': await self.is_banned_from_channel(self.channel_name),
                        'following': await self.is_channel_followed(self.channel_name)
                    }

                    if not checks['live']:
                        await log_event(f"Channel {self.channel_name} is not live, skipping message")
                    elif checks['banned']:
                        await log_event(f"Bot is banned from channel {self.channel_name}, skipping message")
                    else:
                        if not checks['following']:
                            await log_event(f"Not following {self.channel_name}, attempting to follow")
                            await self.follow_channel(self.channel_name)
                        
                        await log_event(f"Attempting to send message on {self.channel_name}: {self.message}")
                        await channel.send(self.message)
                        await log_event(f"Message sent successfully on {self.channel_name}: {self.message}")

            except aiohttp.ClientError as ce:
                await log_event(f"Network error in message routine: {str(ce)}")
                await asyncio.sleep(min(self.interval * 2, 10) * 60)
                continue
            except asyncio.TimeoutError:
                await log_event(f"Timeout error in message routine")
                await asyncio.sleep(min(self.interval * 2, 10) * 60)
                continue
            except Exception as e:
                await log_event(f"Error in message routine: {str(e)}")
                import traceback
                await log_event(f"Error details: {traceback.format_exc()}")

            await asyncio.sleep(self.interval * 60)

    async def check_updates(self):
        while self.running:
            try:
                if not update_queue.empty():
                    update = update_queue.get_nowait()
                    
                    if isinstance(update, dict):
                        if 'toggle' in update:
                            self.is_active = update['toggle']
                            await log_event(f"Bot state updated: {'active' if self.is_active else 'inactive'}")
                        else:
                            required_keys = {'channel', 'message', 'interval'}
                            if all(key in update for key in required_keys):
                                old_channel = self.channel_name
                                self.message = str(update['message'])
                                self.interval = int(update['interval'])
                                self.channel_name = str(update['channel'])
                                
                                if old_channel != self.channel_name:
                                    try:
                                        await self.part_channels([old_channel])
                                        await self.join_channels([self.channel_name])
                                        await log_event(f"Successfully switched from channel {old_channel} to {self.channel_name}")
                                    except Exception as e:
                                        await log_event(f"Error switching channels: {str(e)}")
                                
                                await log_event(f"Settings updated - Channel: {self.channel_name}, Message: {self.message}, Interval: {self.interval}")
                            else:
                                missing_keys = required_keys - set(update.keys())
                                await log_event(f"Error updating settings: Missing required keys {missing_keys}")
                    else:
                        await log_event(f"Error updating settings: Invalid update format")
            except Exception as e:
                await log_event(f"Error in check_updates: {str(e)}")
                import traceback
                await log_event(f"Error details: {traceback.format_exc()}")

            await asyncio.sleep(1)
# ===== FIN MODULE: twitch_bot.py =====

# ===== MODULE: api.py =====
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await log_event("Application starting...")
    yield
    # Shutdown
    await log_event("Application shutting down...")

# Configuration FastAPI
app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def root(request: Request, username: str = Depends(get_auth())):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "current_channel": current_channel,
            "current_message": current_message,
            "current_interval": current_interval,
            "is_active": bot.is_active,
            "bot": bot,
            "client_id": CLIENT_ID,
            "token": TWITCH_TOKEN.replace("oauth:", "")
        }
    )

@app.get('/logs')
async def logs(request: Request, username: str = Depends(get_auth())):
    return EventSourceResponse(event_generator())

@app.post("/update_settings")
async def update_settings(
    settings: SettingsUpdate,
    username: str = Depends(get_auth())
):
    global current_channel, current_message, current_interval

    current_channel = settings.channel
    current_message = settings.message
    current_interval = settings.interval

    update_queue.put({
        'channel': settings.channel,
        'message': settings.message,
        'interval': settings.interval
    })

    await log_event(f"Settings queued - Channel: {settings.channel}, Message: {settings.message}, Interval: {settings.interval}")
    return {"status": "success", "message": "Settings updated"}

@app.post("/toggle_bot")
async def toggle_bot(username: str = Depends(get_auth())):
    if not bot.is_active:  # Si on active le bot
        # Vérifier d'abord si le bot est banni
        is_banned = await bot.is_banned_from_channel(bot.channel_name)
        if is_banned:
            await log_event(f"Cannot activate bot: banned from channel {bot.channel_name}")
            return {
                "status": "error",
                "message": f"Bot is banned from channel {bot.channel_name}",
                "is_active": bot.is_active
            }
            
        # Vérifier si le bot suit la chaîne (mais ne pas bloquer si ce n'est pas le cas)
        is_following = await bot.is_channel_followed(bot.channel_name)
        if not is_following:
            await log_event(f"Warning: Bot is not following channel {bot.channel_name}, but will continue anyway")
            
        # Vérifier si le canal existe et est accessible
        try:
            channel = bot.get_channel(bot.channel_name)
            if not channel:
                await bot.join_channels([bot.channel_name])
                channel = bot.get_channel(bot.channel_name)
                if not channel:
                    await log_event(f"Cannot activate bot: channel {bot.channel_name} not found or not accessible")
                    return {
                        "status": "error",
                        "message": f"Channel {bot.channel_name} not found or not accessible",
                        "is_active": bot.is_active
                    }
            
            # Vérifier si on peut envoyer un message
            try:
                await channel.send(".")  # Message de test invisible
                await log_event(f"Successfully tested message sending to {bot.channel_name}")
            except Exception as e:
                await log_event(f"Cannot activate bot: error sending test message to {bot.channel_name}: {str(e)}")
                return {
                    "status": "error",
                    "message": f"Error sending test message to {bot.channel_name}",
                    "is_active": bot.is_active
                }
                
        except Exception as e:
            await log_event(f"Cannot activate bot: error checking channel {bot.channel_name}: {str(e)}")
            return {
                "status": "error",
                "message": f"Error checking channel {bot.channel_name}",
                "is_active": bot.is_active
            }
    
    # Si toutes les vérifications sont passées ou si on désactive le bot
    bot.is_active = not bot.is_active
    status = "activated" if bot.is_active else "deactivated"

    update_queue.put({
        'toggle': bot.is_active
    })

    await log_event(f"Bot {status} successfully")
    return {
        "status": "success",
        "message": f"Bot {status} successfully",
        "is_active": bot.is_active
    }

@app.get("/check_live/{channel}")
async def check_live(channel: str, username: str = Depends(get_auth())):
    is_live = await bot.is_channel_live(channel)
    return {"is_live": is_live}

@app.get("/check_follow/{channel}")
async def check_follow(channel: str, username: str = Depends(get_auth())):
    is_following = await bot.is_channel_followed(channel)
    return {"is_following": is_following}

@app.get("/check_ban/{channel}")
async def check_ban(channel: str, username: str = Depends(get_auth())):
    is_banned = await bot.is_banned_from_channel(channel)
    return {"is_banned": is_banned}

@app.get("/channel_emotes/{channel}")
async def get_channel_emotes(channel: str, username: str = Depends(get_auth())):
    try:
        channel_id = await bot.get_channel_id(channel)
        if not channel_id:
            return {"emotes": []}
            
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f'https://api.twitch.tv/helix/chat/emotes?broadcaster_id={channel_id}',
                headers=bot.headers,
                timeout=10
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {"emotes": data.get("data", [])}
                return {"emotes": []}
    except Exception as e:
        await log_event(f"Error fetching channel emotes: {str(e)}")
        return {"emotes": []}
# ===== FIN MODULE: api.py =====

# ===== MODULE: main.py (après restructuration) =====
def init_bot():
    global bot
    bot = Bot()
    return bot

def run_bot():
    global bot
    if bot is None:
        bot = init_bot()
    bot.run()

# Initialize bot and start thread
bot = init_bot()
bot_thread = Thread(target=run_bot)
bot_thread.start()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
# ===== FIN MODULE: main.py =====