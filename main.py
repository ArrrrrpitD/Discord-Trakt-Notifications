"""
Trakt Watch History Tracker for Railway
Posts your watch history to Discord
Uses PostgreSQL for persistent storage
Auto-refreshes Trakt tokens
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
TRAKT_CLIENT_SECRET = os.getenv("TRAKT_CLIENT_SECRET")
TRAKT_ACCESS_TOKEN = os.getenv("TRAKT_ACCESS_TOKEN")
TRAKT_REFRESH_TOKEN = os.getenv("TRAKT_REFRESH_TOKEN")
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

# Global token variables
current_access_token = TRAKT_ACCESS_TOKEN
current_refresh_token = TRAKT_REFRESH_TOKEN
token_expires_at = None


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

        # Table for posted history
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS posted_history (
                trakt_id BIGINT PRIMARY KEY,
                posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        # Table for storing tokens
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS trakt_tokens (
                id INTEGER PRIMARY KEY DEFAULT 1,
                access_token TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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


def save_tokens_to_db(access_token, refresh_token, expires_in):
    """Save tokens to database"""
    conn = get_db_connection()
    if not conn:
        return False

    try:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        cur = conn.cursor()

        # Insert or update tokens
        cur.execute(
            """
            INSERT INTO trakt_tokens (id, access_token, refresh_token, expires_at, updated_at)
            VALUES (1, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (id) 
            DO UPDATE SET 
                access_token = EXCLUDED.access_token,
                refresh_token = EXCLUDED.refresh_token,
                expires_at = EXCLUDED.expires_at,
                updated_at = CURRENT_TIMESTAMP
        """,
            (access_token, refresh_token, expires_at),
        )

        conn.commit()
        cur.close()
        conn.close()
        logger.info("✅ Tokens saved to database")
        return True
    except Exception as e:
        logger.error(f"Error saving tokens: {e}")
        return False


def load_tokens_from_db():
    """Load tokens from database"""
    conn = get_db_connection()
    if not conn:
        return None

    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT access_token, refresh_token, expires_at 
            FROM trakt_tokens 
            WHERE id = 1
        """
        )
        result = cur.fetchone()
        cur.close()
        conn.close()

        if result:
            # Make sure expires_at is timezone-aware
            expires_at = result[2]
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)

            return {
                "access_token": result[0],
                "refresh_token": result[1],
                "expires_at": expires_at,
            }
        return None
    except Exception as e:
        logger.error(f"Error loading tokens: {e}")
        return None


def refresh_trakt_token():
    """Refresh the Trakt access token"""
    global current_access_token, current_refresh_token, token_expires_at

    if not TRAKT_CLIENT_SECRET:
        logger.error("❌ TRAKT_CLIENT_SECRET not set! Cannot refresh token.")
        return False

    if not current_refresh_token:
        logger.error("❌ No refresh token available!")
        return False

    logger.info("🔄 Refreshing Trakt access token...")

    try:
        response = requests.post(
            "https://api.trakt.tv/oauth/token",
            json={
                "refresh_token": current_refresh_token,
                "client_id": TRAKT_CLIENT_ID,
                "client_secret": TRAKT_CLIENT_SECRET,
                "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
                "grant_type": "refresh_token",
            },
            headers={"Content-Type": "application/json"},
            timeout=15,
        )

        if response.ok:
            token_data = response.json()
            current_access_token = token_data["access_token"]
            current_refresh_token = token_data["refresh_token"]
            token_expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=token_data["expires_in"]
            )

            # Save to database
            save_tokens_to_db(
                current_access_token, current_refresh_token, token_data["expires_in"]
            )

            logger.info(
                f"✅ Token refreshed successfully! Expires in {token_data['expires_in']//86400} days"
            )
            return True
        else:
            logger.error(
                f"❌ Token refresh failed: {response.status_code} - {response.text}"
            )
            return False

    except Exception as e:
        logger.error(f"❌ Error refreshing token: {e}")
        return False


def ensure_valid_token():
    """Ensure we have a valid token, refresh if needed"""
    global current_access_token, current_refresh_token, token_expires_at

    # Try to load tokens from database first
    if not current_access_token or not token_expires_at:
        db_tokens = load_tokens_from_db()
        if db_tokens:
            current_access_token = db_tokens["access_token"]
            current_refresh_token = db_tokens["refresh_token"]
            token_expires_at = db_tokens["expires_at"]
            logger.info("📂 Loaded tokens from database")
        else:
            # Initialize database with env tokens
            if current_access_token and current_refresh_token:
                # Assume token expires in 6 days (default from Trakt)
                save_tokens_to_db(current_access_token, current_refresh_token, 518400)
                token_expires_at = datetime.now(timezone.utc) + timedelta(days=6)
                logger.info("📝 Initialized tokens in database")

    # Check if token needs refresh (refresh 1 day before expiry)
    if token_expires_at:
        time_until_expiry = token_expires_at - datetime.now(timezone.utc)

        if time_until_expiry.total_seconds() < 86400:  # Less than 1 day
            logger.info(
                f"⚠️  Token expires soon ({time_until_expiry.days} days). Refreshing..."
            )
            return refresh_trakt_token()
        else:
            logger.info(f"✅ Token valid for {time_until_expiry.days} more days")
            return True

    return True


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
    global current_access_token

    since = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    since_iso = since.isoformat().replace("+00:00", "Z")

    url = f"https://api.trakt.tv/users/me/history?start_at={since_iso}&limit=50"

    headers = {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": TRAKT_CLIENT_ID,
        "Authorization": f"Bearer {current_access_token}",
    }

    logger.info(f"🔍 Fetching Trakt history (last {LOOKBACK_HOURS} hours)")

    try:
        response = requests.get(url, headers=headers, timeout=15)

        # If we get 401, try refreshing token
        if response.status_code == 401:
            logger.warning("⚠️  Got 401 Unauthorized - attempting token refresh...")
            if refresh_trakt_token():
                # Retry with new token
                headers["Authorization"] = f"Bearer {current_access_token}"
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
        url = f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={TMDB_API_KEY}&append_to_response=credits"
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

        ep_url = f"https://api.themoviedb.org/3/tv/{show_id}/season/{season}/episode/{episode}?api_key={TMDB_API_KEY}&append_to_response=credits"
        ep_res = requests.get(ep_url, timeout=10)
        if ep_res.ok:
            ep_data = ep_res.json()
            ep_data["show_poster"] = show_data.get("poster_path")

            # Add network information
            networks = show_data.get("networks", [])
            if networks:
                ep_data["show_network"] = networks[0]["name"]

            # Add guest stars from credits
            if ep_data.get("credits") and ep_data["credits"].get("guest_stars"):
                ep_data["guest_stars"] = ep_data["credits"]["guest_stars"]

            return ep_data
    except Exception as e:
        logger.error(f"TMDB episode error: {e}")
    return None


def get_color_from_rating(rating):
    """Get Discord embed color based on rating"""
    if not rating:
        return 0x5865F2  # Discord Blurple
    if rating >= 9:
        return 0x00D9FF  # Brilliant Cyan
    if rating >= 8:
        return 0x00FF88  # Vibrant Green
    if rating >= 7.5:
        return 0xFFD700  # Gold
    if rating >= 7:
        return 0xFFA500  # Orange
    if rating >= 6:
        return 0xFF6B9D  # Pink
    if rating >= 5:
        return 0xFF69B4  # Hot Pink
    return 0xFF4444  # Red


def get_movie_color(genres):
    """Get color based on movie genre"""
    if not genres:
        return 0x9B59B6  # Purple

    genre_colors = {
        "Action": 0xFF4444,
        "Adventure": 0xFF8C00,
        "Animation": 0xFF69B4,
        "Comedy": 0xFFD700,
        "Crime": 0x8B0000,
        "Documentary": 0x4682B4,
        "Drama": 0x9370DB,
        "Fantasy": 0x9400D3,
        "Horror": 0x8B0000,
        "Mystery": 0x483D8B,
        "Romance": 0xFF1493,
        "Sci-Fi": 0x00CED1,
        "Thriller": 0xDC143C,
    }

    for genre in genres:
        if genre["name"] in genre_colors:
            return genre_colors[genre["name"]]

    return 0x9B59B6  # Default purple


def get_show_color():
    """Get vibrant color for TV shows"""
    return 0x00D9FF  # Electric cyan for TV shows


def post_movie_to_discord(item):
    """Post movie watch to Discord"""
    movie = item["movie"]
    watched_at = datetime.strptime(item["watched_at"], "%Y-%m-%dT%H:%M:%S.%fZ")
    watched_at = watched_at.replace(tzinfo=timezone.utc)
    watched_at_ist = watched_at.astimezone(IST)

    tmdb_data = None
    if movie.get("ids", {}).get("tmdb"):
        tmdb_data = fetch_tmdb_movie(movie["ids"]["tmdb"])

    # Build description
    description = ""
    if tmdb_data and tmdb_data.get("tagline"):
        description = f"*\"{tmdb_data['tagline']}\"*\n\n"

    if tmdb_data and tmdb_data.get("overview"):
        overview = tmdb_data["overview"]
        # Trim if too long
        if len(overview) > 400:
            overview = overview[:397] + "..."
        description += overview
    else:
        description += "Just finished watching this movie! 🍿"

    # Determine color
    embed_color = get_color_from_rating(
        tmdb_data.get("vote_average") if tmdb_data else None
    )

    # Use genre-based color if rating color is default
    if tmdb_data and embed_color == 0x5865F2 and tmdb_data.get("genres"):
        embed_color = get_movie_color(tmdb_data["genres"])

    embed = {
        "author": {
            "name": "Movie 🎬",
            "icon_url": "https://i.ibb.co/6JbfjSKn/Trakt-TV.png",
        },
        "title": f"{movie['title']}",
        "description": description,
        "color": embed_color,
        "url": f"https://trakt.tv/movies/{movie['ids']['slug']}",
        "fields": [],
        "footer": {
            "text": "Trakt  •  Infuse",
            "icon_url": "https://i.ibb.co/6JbfjSKn/Trakt-TV.png",
        },
        "timestamp": watched_at.isoformat(),
    }

    # Add poster and backdrop
    if tmdb_data:
        if tmdb_data.get("poster_path"):
            embed["thumbnail"] = {
                "url": f"https://image.tmdb.org/t/p/w500{tmdb_data['poster_path']}"
            }
        if tmdb_data.get("backdrop_path"):
            embed["image"] = {
                "url": f"https://image.tmdb.org/t/p/original{tmdb_data['backdrop_path']}"
            }

    # Build fields
    # Row 1: Watch time
    embed["fields"].append(
        {
            "name": "🕐 Watched",
            "value": watched_at_ist.strftime("%b %d, %Y at %I:%M %p IST"),
            "inline": False,
        }
    )

    # Row 2: Year, Runtime, Rating
    if movie.get("year"):
        embed["fields"].append(
            {"name": "📅 Year", "value": str(movie["year"]), "inline": True}
        )

    if tmdb_data and tmdb_data.get("runtime"):
        hours = tmdb_data["runtime"] // 60
        minutes = tmdb_data["runtime"] % 60
        runtime_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
        embed["fields"].append(
            {"name": "⏱️ Runtime", "value": runtime_str, "inline": True}
        )

    if tmdb_data and tmdb_data.get("vote_average"):
        rating = tmdb_data["vote_average"]
        stars = "⭐" * max(1, min(5, int(rating / 2)))
        embed["fields"].append(
            {"name": "⭐ Rating", "value": f"{rating:.1f}/10 {stars}", "inline": True}
        )

    # Row 2: Genres (full width for better display)
    if tmdb_data and tmdb_data.get("genres"):
        genres = " • ".join([g["name"] for g in tmdb_data["genres"][:5]])
        embed["fields"].append({"name": "🎭 Genres", "value": genres, "inline": False})

    # Row 3: Director and Cast
    if tmdb_data and tmdb_data.get("credits"):
        credits = tmdb_data["credits"]

        # Get director
        directors = [
            c["name"] for c in credits.get("crew", []) if c.get("job") == "Director"
        ]
        if directors:
            embed["fields"].append(
                {
                    "name": "🎬 Director",
                    "value": ", ".join(directors[:2]),
                    "inline": True,
                }
            )

        # Get top cast
        cast = credits.get("cast", [])[:3]
        if cast:
            cast_names = ", ".join([c["name"] for c in cast])
            embed["fields"].append(
                {"name": "🎭 Cast", "value": cast_names, "inline": True}
            )

    # Add budget/revenue if available
    if tmdb_data and tmdb_data.get("budget") and tmdb_data["budget"] > 0:
        budget = tmdb_data["budget"]
        if budget >= 1_000_000:
            budget_str = f"${budget / 1_000_000:.1f}M"
        else:
            budget_str = f"${budget:,}"

        embed["fields"].append(
            {"name": "💰 Budget", "value": budget_str, "inline": True}
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

    # Build description with episode name
    description = f"**S{episode['season']:02d}E{episode['number']:02d}"
    if episode.get("title"):
        description += f" • {episode['title']}**"
    else:
        description += "**"
    description += "\n\n"

    if tmdb_data and tmdb_data.get("overview"):
        overview = tmdb_data["overview"]
        # Trim if too long
        if len(overview) > 350:
            overview = overview[:347] + "..."
        description += overview
    else:
        description += "Just finished watching this episode! 📺"

    # Use vibrant cyan color for TV shows
    embed_color = get_show_color()

    # Override with rating color if available
    if tmdb_data and tmdb_data.get("vote_average"):
        embed_color = get_color_from_rating(tmdb_data["vote_average"])

    embed = {
        "author": {
            "name": f"{show['title']} 📺",
        },
        "title": episode.get("title", f"Episode {episode['number']}"),
        "description": description,
        "color": embed_color,
        "url": f"https://trakt.tv/shows/{show['ids']['slug']}/seasons/{episode['season']}/episodes/{episode['number']}",
        "fields": [],
        "footer": {
            "text": "Trakt  •  Infuse",
            "icon_url": "https://i.ibb.co/6JbfjSKn/Trakt-TV.png",
        },
        "timestamp": watched_at.isoformat(),
    }

    # Add images
    if tmdb_data:
        if tmdb_data.get("show_poster"):
            embed["thumbnail"] = {
                "url": f"https://image.tmdb.org/t/p/w500{tmdb_data['show_poster']}"
            }
        if tmdb_data.get("still_path"):
            embed["image"] = {
                "url": f"https://image.tmdb.org/t/p/original{tmdb_data['still_path']}"
            }

    # Build fields
    # Row 1: Watch time
    embed["fields"].append(
        {
            "name": "🕐 Watched",
            "value": watched_at_ist.strftime("%b %d, %Y at %I:%M %p IST"),
            "inline": False,
        }
    )

    # Row 2: Season/Episode, Runtime, Rating
    embed["fields"].append(
        {
            "name": "📺 Episode",
            "value": f"Season {episode['season']}, Ep. {episode['number']}",
            "inline": True,
        }
    )

    if tmdb_data and tmdb_data.get("runtime"):
        embed["fields"].append(
            {
                "name": "⏱️ Runtime",
                "value": f"{tmdb_data['runtime']} min",
                "inline": True,
            }
        )

    if tmdb_data and tmdb_data.get("vote_average"):
        rating = tmdb_data["vote_average"]
        stars = "⭐" * max(1, min(5, int(rating / 2)))
        embed["fields"].append(
            {"name": "⭐ Rating", "value": f"{rating:.1f}/10 {stars}", "inline": True}
        )

    # Row 2: Air date
    if tmdb_data and tmdb_data.get("air_date"):
        try:
            air_date = datetime.strptime(tmdb_data["air_date"], "%Y-%m-%d")
            embed["fields"].append(
                {
                    "name": "📅 Air Date",
                    "value": air_date.strftime("%b %d, %Y"),
                    "inline": True,
                }
            )
        except:
            pass

    # Network information from show data
    if tmdb_data and tmdb_data.get("show_network"):
        embed["fields"].append(
            {"name": "📡 Network", "value": tmdb_data["show_network"], "inline": True}
        )

    # Guest stars if available
    if tmdb_data and tmdb_data.get("guest_stars"):
        guests = tmdb_data["guest_stars"][:3]
        if guests:
            guest_names = ", ".join([g["name"] for g in guests])
            embed["fields"].append(
                {"name": "⭐ Guest Stars", "value": guest_names, "inline": False}
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

    if not TRAKT_CLIENT_ID or not current_access_token:
        logger.error("❌ Trakt credentials not configured!")
        return

    if not DISCORD_WEBHOOK_URL:
        logger.error("❌ Discord webhook not configured!")
        return

    if not DATABASE_URL:
        logger.error("❌ Database not configured!")
        return

    # Ensure token is valid before making API calls
    ensure_valid_token()

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

    # Ensure we have valid tokens
    ensure_valid_token()

    while True:
        try:
            check_and_post()
        except Exception as e:
            logger.exception(f"❌ Unexpected error: {e}")

        logger.info(f"⏰ Next check in {CHECK_INTERVAL} seconds...")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
