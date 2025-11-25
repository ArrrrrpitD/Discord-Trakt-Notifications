"""
Trakt Watch History Tracker for Railway
Posts your watch history to Discord
Uses PostgreSQL for persistent storage
"""

import os
import time
import requests
from datetime import datetime, timedelta, timezone
import logging
import pytz
import psycopg2
from psycopg2.extras import execute_values

# Configuration
TRAKT_CLIENT_ID = os.getenv("TRAKT_CLIENT_ID")
TRAKT_ACCESS_TOKEN = os.getenv("TRAKT_ACCESS_TOKEN")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "3600"))  # 1 hour default
LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "24"))  # 24 hours default
DATABASE_URL = os.getenv("DATABASE_URL")  # PostgreSQL connection string

# Timezone configuration
IST = pytz.timezone("Asia/Kolkata")

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("trakt-tracker")


def get_db_connection():
    """Get PostgreSQL database connection"""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        return None


def init_database():
    """Initialize database table"""
    conn = get_db_connection()
    if not conn:
        logger.error("Failed to connect to database!")
        return False

    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS posted_history (
                trakt_id BIGINT PRIMARY KEY,
                posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )
        conn.commit()
        cur.close()
        conn.close()
        logger.info("✅ Database initialized")
        return True
    except Exception as e:
        logger.error(f"Database init error: {e}")
        return False


def is_posted(trakt_id):
    """Check if item has already been posted"""
    conn = get_db_connection()
    if not conn:
        return False

    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM posted_history WHERE trakt_id = %s", (trakt_id,))
        result = cur.fetchone() is not None
        cur.close()
        conn.close()
        return result
    except Exception as e:
        logger.error(f"Error checking posted status: {e}")
        return False


def mark_as_posted(trakt_id):
    """Mark item as posted"""
    conn = get_db_connection()
    if not conn:
        return False

    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO posted_history (trakt_id) VALUES (%s) ON CONFLICT (trakt_id) DO NOTHING",
            (trakt_id,),
        )
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Error marking as posted: {e}")
        return False


def cleanup_old_entries():
    """Clean up entries older than 30 days to keep database lean"""
    conn = get_db_connection()
    if not conn:
        return

    try:
        cur = conn.cursor()
        cur.execute(
            """
            DELETE FROM posted_history 
            WHERE posted_at < NOW() - INTERVAL '30 days'
        """
        )
        deleted = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        if deleted > 0:
            logger.info(f"🧹 Cleaned up {deleted} old entries")
    except Exception as e:
        logger.error(f"Cleanup error: {e}")


def get_trakt_history():
    """Fetch watch history from Trakt"""
    since = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    since_iso = since.isoformat().replace("+00:00", "Z")

    url = f"https://api.trakt.tv/users/me/history?start_at={since_iso}&limit=50"

    headers = {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": TRAKT_CLIENT_ID,
        "Authorization": f"Bearer {TRAKT_ACCESS_TOKEN}",
    }

    logger.info(f"🔍 Fetching Trakt history (last {LOOKBACK_HOURS} hours)")

    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"❌ Trakt API error: {e}")
        return []


def fetch_tmdb_movie(tmdb_id):
    """Fetch movie details from TMDB"""
    if not TMDB_API_KEY:
        return None

    try:
        url = f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={TMDB_API_KEY}"
        response = requests.get(url, timeout=10)
        if response.ok:
            return response.json()
    except Exception as e:
        logger.error(f"TMDB movie error: {e}")
    return None


def fetch_tmdb_episode(show_id, season, episode):
    """Fetch episode details from TMDB"""
    if not TMDB_API_KEY:
        return None

    try:
        show_url = f"https://api.themoviedb.org/3/tv/{show_id}?api_key={TMDB_API_KEY}"
        show_res = requests.get(show_url, timeout=10)
        show_data = show_res.json() if show_res.ok else {}

        ep_url = f"https://api.themoviedb.org/3/tv/{show_id}/season/{season}/episode/{episode}?api_key={TMDB_API_KEY}"
        ep_res = requests.get(ep_url, timeout=10)
        if ep_res.ok:
            ep_data = ep_res.json()
            ep_data["show_poster"] = show_data.get("poster_path")
            return ep_data
    except Exception as e:
        logger.error(f"TMDB episode error: {e}")
    return None


def get_color_from_rating(rating):
    """Get Discord embed color based on rating"""
    if not rating:
        return 0x5865F2  # Blue
    if rating >= 8:
        return 0x57F287  # Green
    if rating >= 7:
        return 0xFEE75C  # Yellow
    if rating >= 6:
        return 0xEB459E  # Pink
    return 0xED4245  # Red


def post_movie_to_discord(item):
    """Post movie watch to Discord"""
    movie = item["movie"]
    watched_at = datetime.strptime(item["watched_at"], "%Y-%m-%dT%H:%M:%S.%fZ")
    watched_at = watched_at.replace(tzinfo=timezone.utc)
    watched_at_ist = watched_at.astimezone(IST)

    tmdb_data = None
    if movie.get("ids", {}).get("tmdb"):
        tmdb_data = fetch_tmdb_movie(movie["ids"]["tmdb"])

    embed = {
        "title": f"🎬 {movie['title']} ({movie.get('year', 'N/A')})",
        "description": (
            tmdb_data.get("overview", "Just finished watching this movie!")
            if tmdb_data
            else "Just finished watching!"
        ),
        "color": get_color_from_rating(
            tmdb_data.get("vote_average") if tmdb_data else None
        ),
        "url": f"https://trakt.tv/movies/{movie['ids']['slug']}",
        "fields": [
            {
                "name": "🕐 Watched",
                "value": watched_at_ist.strftime("%b %d, %Y at %I:%M %p IST"),
                "inline": True,
            }
        ],
        "footer": {
            "text": "Trakt  •  Infuse",
            "icon_url": "https://i.ibb.co/6JbfjSKn/Trakt-TV.png",
        },
        "timestamp": watched_at.isoformat(),
    }

    if tmdb_data:
        if tmdb_data.get("poster_path"):
            embed["thumbnail"] = {
                "url": f"https://image.tmdb.org/t/p/w500{tmdb_data['poster_path']}"
            }
        if tmdb_data.get("backdrop_path"):
            embed["image"] = {
                "url": f"https://image.tmdb.org/t/p/original{tmdb_data['backdrop_path']}"
            }

        if tmdb_data.get("runtime"):
            hours = tmdb_data["runtime"] // 60
            minutes = tmdb_data["runtime"] % 60
            embed["fields"].append(
                {"name": "⏱️ Runtime", "value": f"{hours}h {minutes}m", "inline": True}
            )

        if tmdb_data.get("vote_average"):
            embed["fields"].append(
                {
                    "name": "⭐ Rating",
                    "value": f"{tmdb_data['vote_average']:.1f}/10",
                    "inline": True,
                }
            )

        if tmdb_data.get("genres"):
            genres = ", ".join([g["name"] for g in tmdb_data["genres"][:4]])
            embed["fields"].append(
                {"name": "🎭 Genres", "value": genres, "inline": False}
            )

    send_to_discord({"embeds": [embed]})


def post_episode_to_discord(item):
    """Post episode watch to Discord"""
    show = item["show"]
    episode = item["episode"]
    watched_at = datetime.strptime(item["watched_at"], "%Y-%m-%dT%H:%M:%S.%fZ")
    watched_at = watched_at.replace(tzinfo=timezone.utc)
    watched_at_ist = watched_at.astimezone(IST)

    tmdb_data = None
    if show.get("ids", {}).get("tmdb"):
        tmdb_data = fetch_tmdb_episode(
            show["ids"]["tmdb"], episode["season"], episode["number"]
        )

    description = f"**Season {episode['season']}, Episode {episode['number']}**"
    if episode.get("title"):
        description += f" - {episode['title']}"
    description += "\n\n"

    if tmdb_data and tmdb_data.get("overview"):
        description += tmdb_data["overview"]
    else:
        description += "Just finished watching this episode!"

    embed = {
        "title": f"📺 {show['title']}",
        "description": description,
        "color": 0x5865F2,
        "url": f"https://trakt.tv/shows/{show['ids']['slug']}/seasons/{episode['season']}/episodes/{episode['number']}",
        "fields": [
            {
                "name": "🕐 Watched",
                "value": watched_at_ist.strftime("%b %d, %Y at %I:%M %p IST"),
                "inline": True,
            }
        ],
        "footer": {
            "text": "Trakt  •  Infuse",
            "icon_url": "https://i.ibb.co/6JbfjSKn/Trakt-TV.png",
        },
        "timestamp": watched_at.isoformat(),
    }

    if tmdb_data:
        if tmdb_data.get("show_poster"):
            embed["thumbnail"] = {
                "url": f"https://image.tmdb.org/t/p/w500{tmdb_data['show_poster']}"
            }
        if tmdb_data.get("still_path"):
            embed["image"] = {
                "url": f"https://image.tmdb.org/t/p/w500{tmdb_data['still_path']}"
            }

        if tmdb_data.get("runtime"):
            embed["fields"].append(
                {
                    "name": "⏱️ Runtime",
                    "value": f"{tmdb_data['runtime']} min",
                    "inline": True,
                }
            )

        if tmdb_data.get("vote_average"):
            embed["fields"].append(
                {
                    "name": "⭐ Rating",
                    "value": f"{tmdb_data['vote_average']:.1f}/10",
                    "inline": True,
                }
            )

    send_to_discord({"embeds": [embed]})


def send_to_discord(payload):
    """Send embed to Discord"""
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if response.ok:
            logger.info("✅ Posted to Discord")
            return True
        else:
            logger.error(f"❌ Discord webhook failed: {response.status_code}")
            return False
    except Exception as e:
        logger.error(f"❌ Discord error: {e}")
        return False


def check_and_post():
    """Main check function"""
    logger.info("🎬 Starting Trakt check...")

    if not TRAKT_CLIENT_ID or not TRAKT_ACCESS_TOKEN:
        logger.error("❌ Trakt credentials not configured!")
        return

    if not DISCORD_WEBHOOK_URL:
        logger.error("❌ Discord webhook not configured!")
        return

    if not DATABASE_URL:
        logger.error("❌ Database not configured!")
        return

    history = get_trakt_history()
    logger.info(f"📊 Found {len(history)} recent watch events")

    if not history:
        logger.info("No new watches found")
        return

    new_count = 0

    # Process history (reverse to post oldest first)
    for item in reversed(history):
        trakt_id = item["id"]

        if is_posted(trakt_id):
            continue

        try:
            if item["type"] == "movie":
                logger.info(
                    f"🎬 New: {item['movie']['title']} ({item['movie'].get('year')})"
                )
                post_movie_to_discord(item)
                new_count += 1
            elif item["type"] == "episode":
                logger.info(
                    f"📺 New: {item['show']['title']} S{item['episode']['season']}E{item['episode']['number']}"
                )
                post_episode_to_discord(item)
                new_count += 1

            mark_as_posted(trakt_id)
            time.sleep(0.5)

        except Exception as e:
            logger.error(f"Error processing item: {e}")

    logger.info(f"✨ Posted {new_count} new item(s)!")


def main():
    """Main loop"""
    logger.info("🚀 Trakt Watch History Tracker started!")
    logger.info(f"📅 Checking every {CHECK_INTERVAL} seconds")
    logger.info(f"🕐 Looking back {LOOKBACK_HOURS} hours for new watches")

    # Initialize database
    if not init_database():
        logger.error("Failed to initialize database. Exiting.")
        return

    # Run cleanup on startup
    cleanup_old_entries()

    while True:
        try:
            check_and_post()
        except Exception as e:
            logger.exception(f"❌ Unexpected error: {e}")

        logger.info(f"⏰ Next check in {CHECK_INTERVAL} seconds...")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
