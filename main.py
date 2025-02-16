import os
from fastapi import FastAPI, Form, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse
from twitchio.ext import commands
from dotenv import load_dotenv
import asyncio
from threading import Thread
from queue import Queue
from datetime import datetime
from collections import deque
import json
import aiohttp
from contextlib import asynccontextmanager

load_dotenv()

# Configuration Twitch
TWITCH_TOKEN = os.getenv('TWITCH_TOKEN')
TWITCH_CHANNEL = os.getenv('TWITCH_CHANNEL')
DEFAULT_MESSAGE = os.getenv('DEFAULT_MESSAGE', 'Default message')
DEFAULT_INTERVAL = int(os.getenv('DEFAULT_INTERVAL', '5'))
BOT_ACTIVE = os.getenv('BOT_ACTIVE', 'false').lower() == 'true'
CLIENT_ID = os.getenv('CLIENT_ID')

# Variables globales
current_channel = TWITCH_CHANNEL
current_message = DEFAULT_MESSAGE
current_interval = DEFAULT_INTERVAL
update_queue = Queue()
is_bot_active = BOT_ACTIVE
log_messages = deque(maxlen=50)
log_queue = asyncio.Queue()
bot = None

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
        await self._start_tasks()

    async def _start_tasks(self):
        self.loop.create_task(self.send_periodic_message())
        self.loop.create_task(self.check_updates())
        await log_event("Tasks started")

    async def get_channel_id(self, channel_name):
        if channel_name not in self.channel_ids:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                            f'https://api.twitch.tv/helix/users?login={channel_name}',
                            headers=self.headers
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data['data']:
                                self.channel_ids[channel_name] = data['data'][0]['id']
                            else:
                                await log_event(f"Could not find channel ID for {channel_name}")
                                return None
            except Exception as e:
                await log_event(f"Error getting channel ID: {str(e)}")
                return None
        return self.channel_ids[channel_name]

    async def is_channel_live(self, channel_name):
        try:
            channel_id = await self.get_channel_id(channel_name)
            if not channel_id:
                return False

            async with aiohttp.ClientSession() as session:
                async with session.get(
                        f'https://api.twitch.tv/helix/streams?user_id={channel_id}',
                        headers=self.headers
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        is_live = len(data['data']) > 0
                        await log_event(f"Channel {channel_name} live status: {is_live}")
                        return is_live
                    return False
        except Exception as e:
            await log_event(f"Error checking live status: {str(e)}")
            return False

    async def is_channel_followed(self, channel_name):
        try:
            channel_id = await self.get_channel_id(channel_name)
            if not channel_id:
                return False

            bot_id = await self.get_channel_id(self.nick)
            if not bot_id:
                return False

            async with aiohttp.ClientSession() as session:
                async with session.get(
                        f'https://api.twitch.tv/helix/users/follows?from_id={bot_id}&to_id={channel_id}',
                        headers=self.headers
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        is_following = len(data['data']) > 0
                        await log_event(f"Following status for {channel_name}: {is_following}")
                        return is_following
                    return False
        except Exception as e:
            await log_event(f"Error checking follow status: {str(e)}")
            return False

    async def follow_channel(self, channel_name):
        try:
            channel_id = await self.get_channel_id(channel_name)
            if not channel_id:
                return False

            bot_id = await self.get_channel_id(self.nick)
            if not bot_id:
                return False

            async with aiohttp.ClientSession() as session:
                async with session.post(
                        'https://api.twitch.tv/helix/users/follows',
                        headers=self.headers,
                        json={
                            'from_id': bot_id,
                            'to_id': channel_id
                        }
                ) as resp:
                    if resp.status == 200:
                        await log_event(f"Successfully followed channel {channel_name}")
                        return True
                    else:
                        await log_event(f"Failed to follow channel {channel_name}: Status {resp.status}")
                        return False
        except Exception as e:
            await log_event(f"Error following channel: {str(e)}")
            return False

    async def send_periodic_message(self):
        while self.running:
            try:
                if self.is_active:
                    channel = self.get_channel(self.channel_name)
                    if channel:
                        # Vérification du statut live
                        is_live = await self.is_channel_live(self.channel_name)
                        if not is_live:
                            await log_event(f"Channel {self.channel_name} is not live, skipping message")
                            await asyncio.sleep(self.interval * 60)
                            continue

                        # Vérification du statut de suivi
                        is_following = await self.is_channel_followed(self.channel_name)
                        if not is_following:
                            await log_event(f"Not following {self.channel_name}, attempting to follow")
                            follow_success = await self.follow_channel(self.channel_name)
                            if not follow_success:
                                await log_event(f"Unable to follow {self.channel_name}, skipping message")
                                await asyncio.sleep(self.interval * 60)
                                continue

                        await log_event(f"Attempting to send message on {self.channel_name}: {self.message}")
                        await channel.send(self.message)
                        await log_event(f"Message sent successfully on {self.channel_name}: {self.message}")
                    else:
                        await log_event(f"Channel not found: {self.channel_name}")
                        await self.join_channels([self.channel_name])
            except Exception as e:
                await log_event(f"Error in message routine: {str(e)}")

            await asyncio.sleep(self.interval * 60)

    async def check_updates(self):
        while self.running:
            try:
                if not update_queue.empty():
                    update = update_queue.get()
                    if 'toggle' in update:
                        self.is_active = update['toggle']
                        await log_event(f"Bot state updated: {'active' if self.is_active else 'inactive'}")
                    else:
                        old_channel = self.channel_name
                        self.message = update['message']
                        self.interval = update['interval']
                        self.channel_name = update['channel']

                        if old_channel != self.channel_name:
                            await self.part_channels([old_channel])
                            await self.join_channels([self.channel_name])

                        await log_event(f"Settings updated - Channel: {self.channel_name}, Message: {self.message}, Interval: {self.interval}")
            except Exception as e:
                await log_event(f"Error updating settings: {str(e)}")

            await asyncio.sleep(1)

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
async def root(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "current_channel": current_channel,
            "current_message": current_message,
            "current_interval": current_interval,
            "is_active": bot.is_active
        }
    )

@app.get('/logs')
async def logs(request: Request):
    return EventSourceResponse(event_generator())

@app.post("/update_settings")
async def update_settings(
        channel: str = Form(...),
        message: str = Form(...),
        interval: int = Form(...)
):
    global current_channel, current_message, current_interval

    if interval < 1:
        interval = 1
    elif interval > 60:
        interval = 60

    current_channel = channel
    current_message = message
    current_interval = interval

    update_queue.put({
        'channel': channel,
        'message': message,
        'interval': interval
    })

    await log_event(f"Settings queued - Channel: {channel}, Message: {message}, Interval: {interval}")
    return {"status": "success", "message": "Settings updated"}

@app.post("/toggle_bot")
async def toggle_bot():
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