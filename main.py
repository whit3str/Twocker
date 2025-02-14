import os
from fastapi import FastAPI, Form, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from twitchio.ext import commands
from dotenv import load_dotenv
import asyncio
from threading import Thread
from queue import Queue

load_dotenv()

# Configuration Twitch
TWITCH_TOKEN = os.getenv('TWITCH_TOKEN')
TWITCH_CHANNEL = os.getenv('TWITCH_CHANNEL')
DEFAULT_MESSAGE = os.getenv('DEFAULT_MESSAGE', 'Message par défaut')
DEFAULT_INTERVAL = int(os.getenv('DEFAULT_INTERVAL', '5'))
# Nouvelle variable d'environnement pour l'état initial du bot
BOT_ACTIVE = os.getenv('BOT_ACTIVE', 'false').lower() == 'true'

# Configuration FastAPI
app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Variables globales
current_channel = TWITCH_CHANNEL
current_message = DEFAULT_MESSAGE
current_interval = DEFAULT_INTERVAL
update_queue = Queue()
is_bot_active = BOT_ACTIVE  # Utilise la valeur de la variable d'environnement

# Bot Twitch
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
        self.is_active = BOT_ACTIVE  # Utilise la valeur de la variable d'environnement

    async def event_ready(self):
        status = "actif" if self.is_active else "inactif"
        print(f'Connecté en tant que | {self.nick} (état initial: {status})')
        await self._start_tasks()

    async def _start_tasks(self):
        """Démarre les tâches du bot"""
        self.loop.create_task(self.send_periodic_message())
        self.loop.create_task(self.check_updates())
        print("Tâches démarrées")

    async def send_periodic_message(self):
        while self.running:
            try:
                if self.is_active:  # Vérifier si le bot est actif
                    channel = self.get_channel(self.channel_name)
                    if channel:
                        print(f"Tentative d'envoi du message sur {self.channel_name}: {self.message}")
                        await channel.send(self.message)
                        print(f"Message envoyé avec succès sur {self.channel_name}: {self.message}")
                    else:
                        print(f"Canal non trouvé: {self.channel_name}")
                        await self.join_channels([self.channel_name])
            except Exception as e:
                print(f"Erreur lors de l'envoi du message: {str(e)}")
            
            await asyncio.sleep(self.interval * 60)

    async def check_updates(self):
        while self.running:
            try:
                if not update_queue.empty():
                    update = update_queue.get()
                    if 'toggle' in update:
                        self.is_active = update['toggle']
                        print(f"État du bot mis à jour: {'actif' if self.is_active else 'inactif'}")
                    else:
                        old_message = self.message
                        old_interval = self.interval
                        old_channel = self.channel_name
                        
                        self.message = update['message']
                        self.interval = update['interval']
                        self.channel_name = update['channel']
                        
                        if old_channel != self.channel_name:
                            await self.part_channels([old_channel])
                            await self.join_channels([self.channel_name])
                        
                        print(f"Paramètres mis à jour: message='{self.message}' (ancien='{old_message}'), "
                              f"interval={self.interval} (ancien={old_interval}), "
                              f"channel={self.channel_name} (ancien={old_channel})")
            except Exception as e:
                print(f"Erreur lors de la mise à jour des paramètres: {str(e)}")
            
            await asyncio.sleep(1)

    async def event_join(self, channel, user):
        """Événement déclenché quand le bot rejoint un canal"""
        if user.name == self.nick:
            print(f"Bot a rejoint le canal: {channel.name}")

# Instance du bot
bot = Bot()

# Routes FastAPI
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

@app.post("/update_settings")
async def update_settings(
    channel: str = Form(...),
    message: str = Form(...),
    interval: int = Form(...)
):
    global current_channel, current_message, current_interval
    
    # Validation de l'intervalle
    if interval < 1:
        interval = 1
    elif interval > 60:
        interval = 60
    
    # Mise à jour des variables globales
    current_channel = channel
    current_message = message
    current_interval = interval
    
    # Mettre les nouveaux paramètres dans la queue
    update_queue.put({
        'channel': channel,
        'message': message,
        'interval': interval
    })
    
    print(f"Mise en queue des nouveaux paramètres: channel='{channel}', message='{message}', interval={interval}")
    return {"status": "success", "message": "Paramètres mis à jour"}

@app.post("/toggle_bot")
async def toggle_bot():
    """Active ou désactive le bot"""
    bot.is_active = not bot.is_active
    status = "activé" if bot.is_active else "désactivé"
    
    # Mettre à jour l'état dans la queue
    update_queue.put({
        'toggle': bot.is_active
    })
    
    return {
        "status": "success",
        "message": f"Bot {status} avec succès",
        "is_active": bot.is_active
    }

# Fonction pour démarrer le bot dans un thread séparé
def run_bot():
    bot.run()

# Démarrage du bot dans un thread séparé
bot_thread = Thread(target=run_bot)
bot_thread.start()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)