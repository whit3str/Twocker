# Twocker

Just a silly mixing of Twitch Bot on Docker. Proudly and poorly made in Python with [Claude.ai](https://claude.ai/). 

## Features
- Channel selector
- Message inputer with Twitch-style preview
- Time delayer
- Bot enabler
- Live status monitoring
- Follow status check
- Ban detection
- Authentication system
- Caching system for API calls
- Dark/Light theme toggle
- Real-time message preview with Twitch chat style
- Status indicators (Live, Follow, Ban)

## Prerequisites
- A Twitch [access token](https://twitchtokengenerator.com/)
- Docker

## Deployment

Clone the repository.

Fill the `.env` file with your needs, helpful table is available below.

### Variables

| Name             | Description/Value                      |
|------------------|----------------------------------------|
| TWITCH_TOKEN     | oauth:<access_token>                   |
| CLIENT_ID        | Your Twitch application client ID      |
| TWITCH_CHANNEL   | Default channel to chat to             |
| DEFAULT_MESSAGE  | Hello world                            |
| DEFAULT_INTERVAL | Between 1 to 60 (min)                  |
| BOT_ACTIVE       | false (default) / true                 |
| API_USERNAME     | Username for web interface (default: admin) |
| API_PASSWORD     | Password for web interface (default: password) |
| CACHE_EXPIRY     | Cache duration in seconds (default: 300) |

### Docker Compose

```
services:
  twocker:
    image: ghcr.io/whit3str/twocker:latest
    ports:
      - "8000:8000"
    env_file: .env
 ```
Run the following command within the cloned repository `docker-compose up -d` and head to http://localhost:8000.

## Security

The application includes basic authentication:
- Username/password authentication for the web interface
- Secure session management
- Rate limiting for API calls