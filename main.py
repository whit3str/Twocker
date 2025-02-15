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

load_dotenv()

# Twitch Configuration
TWITCH_TOKEN = os.getenv('TWITCH_TOKEN')
TWITCH_CHANNEL = os.getenv('TWITCH_CHANNEL')
DEFAULT_MESSAGE = os.getenv('DEFAULT_MESSAGE', 'Default message')
DEFAULT_INTERVAL = int(os.getenv('DEFAULT_INTERVAL', '5'))
BOT_ACTIVE = os.getenv('BOT_ACTIVE', 'false').lower() == 'true'

# FastAPI Configuration
app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Global variables
current_channel = TWITCH_CHANNEL
current_message = DEFAULT_MESSAGE
current_interval = DEFAULT_INTERVAL
update_queue = Queue()
is_bot_active = BOT_ACTIVE
log_messages = deque(maxlen=50)  # Store last 50 log messages
log_queue = asyncio.Queue()  # Queue for real-time log updates

async def log_event(message: str):
    """Add timestamped log message to the log queue"""
    timestamp = datetime.utcnow().isoformat()
    log_entry = f"{timestamp} {message}"
    log_messages.appendleft(log_entry)
    await log_queue.put(log_entry)
    return log_entry

async def event_generator():
    """Generate events for SSE"""
    # First, send all existing logs
    for msg in reversed(log_messages):
        yield {
            "event": "message",
            "data": msg
        }

    # Then listen for new logs
    try:
        while True:
            message = await log_queue.get()
            yield {
                "event": "message",
                "data": message
            }
    except asyncio.CancelledError:
        raise

# Twitch Bot
class Bot(commands.Bot):
    def __init__(self):
        super().__init__(
            token=TWITCH_TOKEN,
            prefix='!',
            initial_channels=[current_channel]
        )
        self.message = current_message
        self.interval = current_interval
        self.channel_name = current_channel
        self.running = True
        self.is_active = BOT_ACTIVE

    async def event_ready(self):
        status = "active" if self.is_active else "inactive"
        await log_event(f'Connected as | {self.nick} (initial state: {status})')
        await self._start_tasks()

    async def _start_tasks(self):
        """Start bot tasks"""
        self.loop.create_task(self.send_periodic_message())
        self.loop.create_task(self.check_updates())
        await log_event("Tasks started")

    async def send_periodic_message(self):
        while self.running:
            try:
                if self.is_active:
                    channel = self.get_channel(self.channel_name)
                    if channel:
                        await log_event(f"Attempting to send message on {self.channel_name}: {self.message}")
                        await channel.send(self.message)
                        await log_event(f"Message sent successfully on {self.channel_name}: {self.message}")
                    else:
                        await log_event(f"Channel not found: {self.channel_name}")
                        await self.join_channels([self.channel_name])
            except Exception as e:
                await log_event(f"Error sending message: {str(e)}")

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

    async def event_join(self, channel, user):
        """Event triggered when bot joins a channel"""
        if user.name == self.nick:
            await log_event(f"Bot joined channel: {channel.name}")

# Bot instance
bot = Bot()

# FastAPI routes
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
    """SSE endpoint for log updates"""
    return EventSourceResponse(event_generator())

@app.post("/update_settings")
async def update_settings(
        channel: str = Form(...),
        message: str = Form(...),
        interval: int = Form(...)
):
    global current_channel, current_message, current_interval

    # Validate interval
    if interval < 1:
        interval = 1
    elif interval > 60:
        interval = 60

    # Update global variables
    current_channel = channel
    current_message = message
    current_interval = interval

    # Queue new settings
    update_queue.put({
        'channel': channel,
        'message': message,
        'interval': interval
    })

    await log_event(f"Settings queued - Channel: {channel}, Message: {message}, Interval: {interval}")
    return {"status": "success", "message": "Settings updated"}

@app.post("/toggle_bot")
async def toggle_bot():
    """Activate or deactivate the bot"""
    bot.is_active = not bot.is_active
    status = "activated" if bot.is_active else "deactivated"

    # Update state in queue
    update_queue.put({
        'toggle': bot.is_active
    })

    await log_event(f"Bot {status} successfully")
    return {
        "status": "success",
        "message": f"Bot {status} successfully",
        "is_active": bot.is_active
    }

# Function to start bot in separate thread
def run_bot():
    bot.run()

# Start bot in separate thread
bot_thread = Thread(target=run_bot)
bot_thread.start()

# Startup event to log application start
@app.on_event("startup")
async def startup_event():
    await log_event("Application started")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)