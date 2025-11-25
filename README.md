# 🎬 Trakt Watch History to Discord

**Automatically post your Trakt.tv watch history to a Discord channel with rich embeds.**

![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)
![Docker](https://img.shields.io/badge/Docker-Ready-blue.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)

This bot monitors your Trakt.tv profile for new watched movies and episodes and instantly posts them to a Discord webhook. It enriches the notifications with metadata from TMDB (The Movie Database), including posters, ratings, runtimes, and genres.

## ✨ Features

- **Real-time Tracking**: Checks for new watches at a configurable interval.
- **Rich Embeds**: Beautiful Discord cards with:
  - 🖼️ Posters and backdrops
  - ⭐ Ratings and genres
  - ⏱️ Runtime and watch timestamps (converted to IST/Local time)
  - 🔗 Direct links to Trakt pages
- **Smart Deduplication**: Uses a PostgreSQL database to ensure you never get duplicate notifications.
- **Auto-Cleanup**: Automatically removes old history from the database to keep it lightweight.
- **Docker & Railway Ready**: Built to be deployed easily on Railway or any Docker-compatible host.

## 🛠️ Prerequisites

Before you start, you'll need:
1.  **Trakt Account**: [Create an API App](https://trakt.tv/oauth/applications) to get a Client ID.
2.  **Trakt Access Token**: You can generate one using a tool like [Trakt Auth](https://trakt.tv/oauth/authorize) or via curl.
3.  **TMDB API Key**: (Optional but recommended) Get one from [The Movie Database](https://www.themoviedb.org/settings/api).
4.  **Discord Webhook**: Create a webhook in your desired Discord channel.
5.  **PostgreSQL Database**: Required for storing history state.

## 🚀 Deployment

### Option 1: Deploy on Railway (Recommended)

1.  **Fork/Clone** this repository.
2.  **New Project**: Create a new project on [Railway](https://railway.app/).
3.  **Add Database**: Add a PostgreSQL database service to your project.
4.  **Deploy**: Connect your GitHub repo and deploy.
5.  **Variables**: Go to the "Variables" tab and add the following:

| Variable | Description | Required | Default |
| :--- | :--- | :--- | :--- |
| `TRAKT_CLIENT_ID` | Your Trakt API Client ID | Yes | - |
| `TRAKT_ACCESS_TOKEN` | Your Trakt OAuth Access Token | Yes | - |
| `DISCORD_WEBHOOK_URL` | Discord Webhook URL | Yes | - |
| `DATABASE_URL` | Postgres Connection URL (Railway provides this automatically) | Yes | - |
| `TMDB_API_KEY` | TMDB API Key for rich metadata | No | - |
| `CHECK_INTERVAL` | Seconds between checks | No | `3600` (1 hr) |
| `LOOKBACK_HOURS` | Hours to look back for new watches | No | `24` |

### Option 2: Run with Docker

```bash
# Build the image
docker build -t trakt-discord-bot .

# Run the container (pass env vars)
docker run -d \
  -e TRAKT_CLIENT_ID=your_id \
  -e TRAKT_ACCESS_TOKEN=your_token \
  -e DISCORD_WEBHOOK_URL=your_webhook \
  -e DATABASE_URL=postgresql://user:pass@host:5432/db \
  trakt-discord-bot
```

### Option 3: Run Locally

1.  **Clone the repo**:
    ```bash
    git clone https://github.com/yourusername/discord-trakt-notifications.git
    cd discord-trakt-notifications
    ```

2.  **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

3.  **Set Environment Variables**:
    Create a `.env` file or export them in your shell.

4.  **Run**:
    ```bash
    python main.py
    ```

## ⚙️ Configuration Details

- **Timezone**: The bot is currently configured to convert times to **IST (Asia/Kolkata)**. You can change this in `main.py` by modifying the `IST` variable:
  ```python
  IST = pytz.timezone("Your/Timezone")
  ```
- **Embed Colors**: Colors change dynamically based on the rating:
  - 🟢 **8.0+**: Green
  - 🟡 **7.0-7.9**: Yellow
  - 🌸 **6.0-6.9**: Pink
  - 🔴 **<6.0**: Red
  - 🔵 **No Rating**: Blue

## 🤝 Contributing

Feel free to open issues or submit pull requests if you have ideas for improvements!

## 📄 License

This project is open source and available under the [MIT License](LICENSE).
