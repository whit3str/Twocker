FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Créer le fichier de configuration par défaut
RUN echo '{"message": "Message par défaut", "interval": 5}' > config.json

# Définir un volume pour la persistance
VOLUME ["/app/config.json"]

CMD ["python", "main.py"]