import os
import json
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
CONFIG_FILE = 'config.json'

# Configuration par défaut
DEFAULT_CONFIG = {
    "message": DEFAULT_MESSAGE,
    "interval": DEFAULT_INTERVAL
}

def load_config():
    """Charge la configuration depuis les variables d'environnement ou le fichier JSON"""
    config = DEFAULT_CONFIG.copy()
    
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                config.update(json.load(f))
    except Exception as e:
        print(f"Erreur lors du chargement de la configuration: {e}")
    
    # Les variables d'environnement ont la priorité
    if 'MESSAGE' in os.environ:
        config['message'] = os.getenv('MESSAGE')
    if 'INTERVAL' in os.environ:
        try:
            config['interval'] = int(os.getenv('INTERVAL'))
        except ValueError:
            print(f"Erreur: INTERVAL doit être un nombre")
    
    return config

# Bot Twitch
class Bot(commands.Bot):
    def __init__(self):
        super().__init__(
            token=TWITCH_TOKEN,
            prefix='!',
            initial_channels=[TWITCH_CHANNEL]
        )
        self.message = current_message
        self.interval = current_interval
        self.running = True

    async def event_ready(self):
        print(f'Connecté en tant que | {self.nick}')
        # Démarrer les tâches après la connexion
        await self._start_tasks()

    async def _start_tasks(self):
        """Démarre les tâches du bot"""
        self.loop.create_task(self.send_periodic_message())
        self.loop.create_task(self.check_updates())
        print("Tâches démarrées")

    async def send_periodic_message(self):
        while self.running:
            try:
                # Obtenir une référence fraîche du canal
                channel = self.get_channel(TWITCH_CHANNEL)
                if channel:
                    print(f"Tentative d'envoi du message: {self.message}")
                    await channel.send(self.message)
                    print(f"Message envoyé avec succès: {self.message}")
                else:
                    print(f"Canal non trouvé: {TWITCH_CHANNEL}")
                    # Tenter de rejoindre le canal
                    await self.join_channels([TWITCH_CHANNEL])
            except Exception as e:
                print(f"Erreur lors de l'envoi du message: {str(e)}")
            
            await asyncio.sleep(self.interval * 60)

    async def check_updates(self):
        while self.running:
            try:
                if not update_queue.empty():
                    update = update_queue.get()
                    old_message = self.message
                    old_interval = self.interval
                    self.message = update['message']
                    self.interval = update['interval']
                    print(f"Paramètres mis à jour: message='{self.message}' (ancien='{old_message}'), "
                          f"interval={self.interval} (ancien={old_interval})")
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
            "current_message": current_message,
            "current_interval": current_interval
        }
    )

@app.post("/update_settings")
async def update_settings(message: str = Form(...), interval: int = Form(...)):
    global current_message, current_interval
    
    # Validation de l'intervalle
    if interval < 1:
        interval = 1
    elif interval > 60:
        interval = 60
    
    current_message = message
    current_interval = interval
    
    # Mettre les nouveaux paramètres dans la queue
    update_queue.put({
        'message': message,
        'interval': interval
    })
    
    print(f"Mise en queue des nouveaux paramètres: message='{message}', interval={interval}")
    return {"status": "success", "message": "Paramètres mis à jour"}

# Fonction pour démarrer le bot dans un thread séparé
def run_bot():
    bot.run()

# Démarrage du bot dans un thread séparé
bot_thread = Thread(target=run_bot)
bot_thread.start()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)