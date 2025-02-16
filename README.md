# Twocker

Just a silly mixing of Twitch Bot on Docker. Proudly and poorly made in Python with [Claude.ai](https://claude.ai/). 

## Features
- Channel selector
- Message inputer
- Time delayer
- Bot enabler
- Log shower

## Prerequisites
- A Twitch [access token](https://twitchtokengenerator.com/)
- Docker

## Deployment

Clone the repository.

Fill the `.env` file with your needs, helpful table is available below.

### Variables

| Name             | Description/Value          |
|------------------|----------------------------|
| TWITCH_TOKEN     | oauth:<access_token>       |
| TWITCH_CHANNEL   | Default channel to chat to |
| DEFAULT_MESSAGE  | Hello world                |
| DEFAULT_INTERVAL | Between 1 to 60 (min)      |
| BOT_ACTIVE       | false (default) / true     |

### Docker Compose

```
services:
  twocker:
    image: ghcr.io/whit3str/twocker:latest
    ports:
      - "8000:8000"
    env_file: .env
 ```
Run the following command within the cloned repository `docker-compose up -d` and head to http://localhost:8080.
## To Do

* Add a random interval 
* Check if channel is live before posting
* Add a message preview line

