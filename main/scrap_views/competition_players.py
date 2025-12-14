"""
Match Data Extractor for Competition Seasons
Extracts match data from HTML and saves to Django models
"""

from django.shortcuts import render
from django.http import JsonResponse
from bs4 import BeautifulSoup
import requests
import random
from datetime import datetime
from django.utils import timezone
from main.models import Team, Season, Match, SeasonMatch, Competition
import logging
import re

logger = logging.getLogger(__name__)

# Cache for storing scraped data
_cache = {}
CACHE_DURATION = 300  # 5 minutes in seconds

SCRAPER_API_KEY = [
    "d0a075b5a0bcdefcaabdd16757067f0f",
    "2d118deea43615910a5ed5cc6f8f56fa",
    "4c64b63e0a8ebf500aaa60c2514e6e8f",
    "d0bae26ea5465f92fbe33e3fa3d2e850",
    "b9573123f02ebb9fdfcd6f4cace5fda5",
    "6c49e45c1661f242378ca489f92b6ede",
]


def fetch_with_scraper_api(url, use_js=True):
    """
    Fetch webpage content using ScraperAPI
    """
    # Check cache first
    cache_key = f"{url}_{use_js}"
    if cache_key in _cache:
        cached_data, cached_time = _cache[cache_key]
        if (datetime.now() - cached_time).seconds < CACHE_DURATION:
            logger.info(f"Cache hit for {url}")
            return cached_data

    api_url = "http://api.scraperapi.com"
    params = {
        "api_key": random.choice(SCRAPER_API_KEY),
        "url": url,
        "render": "true" if use_js else "false",
    }

    try:
        response = requests.get(api_url, params=params, timeout=60)
        response.raise_for_status()

        # Cache the response
        _cache[cache_key] = (response.content, datetime.now())
        logger.info(f"Successfully fetched {url} via ScraperAPI")

        return response.content

    except requests.RequestException as e:
        logger.error(f"ScraperAPI request failed: {e}")
        raise


def extract_team_name(team_element):
    """Extract team name from team element"""
    team_span = team_element.find('span', class_='the_team')
    return team_span.text.strip() if team_span else None


def extract_datetime(match_element):
    """Extract match datetime from element"""
    time_span = match_element.find('span', class_='the_otime')
    if time_span and not time_span.get('hidden'):
        datetime_str = time_span.text.strip()
        try:
            # Parse datetime: format is "2025-08-15 22:00"
            naive_datetime = datetime.strptime(datetime_str, '%Y-%m-%d %H:%M')
            # Make it timezone aware
            return timezone.make_aware(naive_datetime)
        except ValueError as e:
            logger.error(f"Date parsing error: {e}")
            return None
    return None


def extract_scores(match_element):
    """Extract home and away scores"""
    score_span = match_element.find('span', class_='match_score')
    if score_span and not score_span.get('hidden'):
        home_score_span = score_span.find('span', class_='hometeam')
        away_score_span = score_span.find('span', class_='awayteam')
        
        home_score = int(home_score_span.text.strip()) if home_score_span else 0
        away_score = int(away_score_span.text.strip()) if away_score_span else 0
        return home_score, away_score
    return 0, 0


def extract_round(matchday_header):
    """Extract round number from matchday header"""
    round_div = matchday_header.find('div', class_='round')
    if round_div:
        # Extract number from text like "الجولة 1"
        round_text = round_div.text.strip()
        match = re.search(r'\d+', round_text)
        return int(match.group()) if match else None
    return None


def determine_status(match_element):
    """Determine match status based on element attributes"""
    view_status = match_element.get('data-view_status', 'coming')
    
    if view_status == 'done':
        return 'finished'
    elif view_status == 'live':
        return 'live'
    else:
        return 'upcoming'


def extract_match_id(match_element):
    """Extract unique match ID from element"""
    match_id = match_element.get('id', '')
    # Extract number from "match_196697"
    match = re.search(r'match_(\d+)', match_id)
    return match.group(1) if match else None


def get_or_create_team(team_name):
    """Get or create team by name"""
    if not team_name:
        return None

    team, created = Team.objects.get_or_create(
        arabic_name=team_name,
        defaults={
            'name': team_name,
            'code': team_name[:3].upper()
        }
    )

    if created:
        logger.info(f"Created new team: {team_name}")

    return team


def scrape_competition_players(request):
    """
    Complete player stats scraper - extracts goals, assists, and all player data
    Automatically fetches both scorers and assists pages
    """
    try:
        # Get season_id from request (preferred method)
        season_id = request.GET.get("season_id")

        if season_id:
            # Direct season ID provided - use this!
            try:
                season = Season.objects.get(id=season_id)
                competition = season.competition
                season_created = False
                logger.info(
                    f"Using season ID {season_id}: {season.name} - {competition.name}"
                )
            except Season.DoesNotExist:
                return JsonResponse(
                    {"error": f"Season with ID {season_id} not found"}, status=404
                )
        else:
            # Fallback: use competition_id (old method)
            competition_id = request.GET.get("competition_id")
            if not competition_id:
                return JsonResponse(
                    {
                        "error": "Either season_id or competition_id parameter is required"
                    },
                    status=400,
                )

            try:
                competition = Competition.objects.get(id=competition_id)
            except Competition.DoesNotExist:
                return JsonResponse({"error": "Competition not found"}, status=404)

        # Get base URL (scorers page)
        scorers_url = request.GET.get("url")
        if not scorers_url:
            return JsonResponse(
                {"error": "url parameter is required (scorers page URL)"}, status=400
            )

        # Generate assists URL from scorers URL
        assists_url = scorers_url.replace("-scorers/", "-assists/")

        logger.info(f"Scorers URL: {scorers_url}")
        logger.info(f"Assists URL: {assists_url}")

        # Extract season info from scorers page
        content = fetch_with_scraper_api(scorers_url, use_js=True)
        soup = BeautifulSoup(content, "html.parser")

        # Extract season name
        title_element = soup.find("h1", class_="title")
        season_name = "2025/2026"
        start_date = "2025-08-01"
        end_date = "2026-05-31"

        if title_element:
            title_text = title_element.text.strip()
            season_match = re.search(r"(\d{4})/(\d{4})", title_text)
            if season_match:
                season_name = season_match.group(0)
                start_year = season_match.group(1)
                end_year = season_match.group(2)
                start_date = f"{start_year}-08-01"
                end_date = f"{end_year}-05-31"

        # Allow override from request
        season_name = request.GET.get("season_name", season_name)
        start_date = request.GET.get("start_date", start_date)
        end_date = request.GET.get("end_date", end_date)

        # Get or create season (handle duplicates)
        try:
            season = Season.objects.get(name=season_name, competition=competition)
            season_created = False
            logger.info(f"Found existing season: {season_name}")
        except Season.DoesNotExist:
            season = Season.objects.create(
                name=season_name,
                competition=competition,
                url=scorers_url,
                start_date=start_date,
                end_date=end_date,
            )
            season_created = True
            logger.info(f"Created new season: {season_name}")
        except Season.MultipleObjectsReturned:
            # Handle duplicate seasons - use the first one
            season = Season.objects.filter(
                name=season_name, competition=competition
            ).first()
            season_created = False
            logger.warning(
                f"Found multiple seasons for {season_name}! Using season ID: {season.id}"
            )
            logger.warning("You should clean up duplicate seasons in your database!")

        logger.info(f"{'Created' if season_created else 'Using'} season: {season_name}")

        # Import models
        from main.models import Player, SeasonPlayer

        stats = {
            "total_players": 0,
            "created_players": 0,
            "updated_players": 0,
            "with_goals": 0,
            "with_assists": 0,
            "with_both": 0,
            "errors": [],
        }

        # Dictionary to store all player data: {arabic_name: {goals, assists, team, ...}}
        players_data = {}

        # ==================== STEP 1: SCRAPE SCORERS ====================
        logger.info("=" * 60)
        logger.info("STEP 1: Scraping scorers (goals)")
        logger.info("=" * 60)

        # Fetch scorers page if we haven't already
        if season_id:
            content = fetch_with_scraper_api(scorers_url, use_js=True)
            soup = BeautifulSoup(content, "html.parser")

        scorers_frame = soup.find("div", class_="scorers_jdwel")
        if not scorers_frame:
            scorers_frame = soup.find("div", class_="jdwel_frame")

        if scorers_frame:
            rows = scorers_frame.find_all("div", class_="brow")
            logger.info(f"Found {len(rows)} players with goals")

            for idx, row in enumerate(rows):
                try:
                    # Extract player name
                    name_cell = row.find("div", class_="name")
                    if not name_cell:
                        continue

                    arabic_name_elem = name_cell.find("div", class_="main_name")
                    english_name_elem = name_cell.find("div", class_="second_name")

                    arabic_name = (
                        arabic_name_elem.text.strip() if arabic_name_elem else None
                    )
                    english_name = (
                        english_name_elem.text.strip()
                        if english_name_elem
                        else arabic_name
                    )

                    if not arabic_name:
                        continue

                    # Extract team
                    team_badge = name_cell.find("div", class_="badge")
                    team_name_span = (
                        team_badge.find("span", class_="team_name")
                        if team_badge
                        else None
                    )
                    team_name = team_name_span.text.strip() if team_name_span else None

                    # Extract photo
                    photo_cell = row.find("div", class_="photo")
                    player_photo_img = (
                        photo_cell.find("img", class_="player_photo")
                        if photo_cell
                        else None
                    )
                    photo_url = (
                        player_photo_img.get("src") if player_photo_img else None
                    )

                    # Extract goals
                    goals_cell = row.find("div", class_="goals_count")
                    goals = 0
                    if goals_cell:
                        goals_span = goals_cell.find("span")
                        goals = int(goals_span.text.strip()) if goals_span else 0

                    # Extract rank
                    rank_cell = row.find("div", class_="rank")
                    rank = int(rank_cell.text.strip()) if rank_cell else None

                    # Store player data
                    players_data[arabic_name] = {
                        "arabic_name": arabic_name,
                        "english_name": english_name,
                        "team_name": team_name,
                        "photo_url": photo_url,
                        "goals": goals,
                        "assists": 0,  # Will be updated in step 2
                        "rank": rank,
                    }

                    logger.info(
                        f"⚽ Scorer #{rank}: {arabic_name} - {goals} goals - {team_name}"
                    )

                except Exception as e:
                    error_msg = f"Error processing scorer row {idx}: {str(e)}"
                    logger.error(error_msg)
                    stats["errors"].append(error_msg)
                    continue
        else:
            logger.warning("Scorers frame not found!")

        # ==================== STEP 2: SCRAPE ASSISTS ====================
        logger.info("=" * 60)
        logger.info("STEP 2: Scraping assists")
        logger.info("=" * 60)

        try:
            assists_content = fetch_with_scraper_api(assists_url, use_js=True)
            assists_soup = BeautifulSoup(assists_content, "html.parser")

            assists_frame = assists_soup.find("div", class_="scorers_jdwel")
            if not assists_frame:
                assists_frame = assists_soup.find("div", class_="jdwel_frame")

            if assists_frame:
                rows = assists_frame.find_all("div", class_="brow")
                logger.info(f"Found {len(rows)} players with assists")

                for idx, row in enumerate(rows):
                    try:
                        # Extract player name
                        name_cell = row.find("div", class_="name")
                        if not name_cell:
                            continue

                        arabic_name_elem = name_cell.find("div", class_="main_name")
                        english_name_elem = name_cell.find("div", class_="second_name")

                        arabic_name = (
                            arabic_name_elem.text.strip() if arabic_name_elem else None
                        )
                        english_name = (
                            english_name_elem.text.strip()
                            if english_name_elem
                            else arabic_name
                        )

                        if not arabic_name:
                            continue

                        # Extract team
                        team_badge = name_cell.find("div", class_="badge")
                        team_name_span = (
                            team_badge.find("span", class_="team_name")
                            if team_badge
                            else None
                        )
                        team_name = (
                            team_name_span.text.strip() if team_name_span else None
                        )

                        # Extract assists
                        assists_cell = row.find("div", class_="goals_count")
                        assists = 0
                        if assists_cell:
                            assists_span = assists_cell.find("span")
                            assists = (
                                int(assists_span.text.strip()) if assists_span else 0
                            )

                        # Update existing player or create new entry
                        if arabic_name in players_data:
                            players_data[arabic_name]["assists"] = assists
                            logger.info(
                                f"🎯 Updated {arabic_name}: {players_data[arabic_name]['goals']} goals + {assists} assists"
                            )
                        else:
                            # Player has assists but no goals
                            photo_cell = row.find("div", class_="photo")
                            player_photo_img = (
                                photo_cell.find("img", class_="player_photo")
                                if photo_cell
                                else None
                            )
                            photo_url = (
                                player_photo_img.get("src")
                                if player_photo_img
                                else None
                            )

                            players_data[arabic_name] = {
                                "arabic_name": arabic_name,
                                "english_name": english_name,
                                "team_name": team_name,
                                "photo_url": photo_url,
                                "goals": 0,
                                "assists": assists,
                                "rank": None,
                            }
                            logger.info(
                                f"🎯 New player: {arabic_name} - {assists} assists - {team_name}"
                            )

                    except Exception as e:
                        error_msg = f"Error processing assists row {idx}: {str(e)}"
                        logger.error(error_msg)
                        stats["errors"].append(error_msg)
                        continue
            else:
                logger.warning("Assists frame not found!")

        except Exception as e:
            logger.warning(f"Could not fetch assists page: {e}")
            logger.info("Continuing with goals data only...")

        # ==================== STEP 3: SAVE TO DATABASE ====================
        logger.info("=" * 60)
        logger.info("STEP 3: Saving to database")
        logger.info("=" * 60)

        for arabic_name, player_info in players_data.items():
            try:
                # Get or create team
                team = None
                if player_info["team_name"]:
                    team = get_or_create_team(player_info["team_name"])

                # Get or create player
                player, player_created = Player.objects.get_or_create(
                    arabic_name=player_info["arabic_name"],
                    defaults={
                        "name": player_info["english_name"],
                        "code": player_info["arabic_name"][:3].upper(),
                        "position": (
                            "FW"
                            if player_info["goals"] > player_info["assists"]
                            else "MF"
                        ),
                    },
                )

                if player_created:
                    stats["created_players"] += 1
                    logger.info(f"✅ Created player: {arabic_name}")
                else:
                    # Update English name if needed
                    if not player.name or player.name == player.arabic_name:
                        player.name = player_info["english_name"]
                        player.save()
                    stats["updated_players"] += 1

                # Download and save photo if available
                if player_info["photo_url"] and not player.photo_url:
                    try:
                        from django.core.files.base import ContentFile
                        from urllib.parse import urlparse
                        import os

                        response = requests.get(player_info["photo_url"], timeout=10)
                        response.raise_for_status()

                        parsed_url = urlparse(player_info["photo_url"])
                        file_ext = os.path.splitext(parsed_url.path)[1] or ".png"
                        filename = f"{arabic_name.replace(' ', '_').lower()}{file_ext}"

                        player.photo_url.save(
                            filename, ContentFile(response.content), save=False
                        )
                        player.save()
                        logger.info(f"📸 Downloaded photo for {arabic_name}")
                    except Exception as e:
                        logger.warning(
                            f"Failed to download photo for {arabic_name}: {e}"
                        )

                # Create or update SeasonPlayer
                season_player, sp_created = SeasonPlayer.objects.update_or_create(
                    season=season,
                    player=player,
                    defaults={
                        "team": team,
                        "goals": player_info["goals"],
                        "assists": player_info["assists"],
                    },
                )

                # Update statistics
                if player_info["goals"] > 0:
                    stats["with_goals"] += 1
                if player_info["assists"] > 0:
                    stats["with_assists"] += 1
                if player_info["goals"] > 0 and player_info["assists"] > 0:
                    stats["with_both"] += 1

                stats["total_players"] += 1

                logger.info(
                    f"💾 {'Created' if sp_created else 'Updated'} SeasonPlayer: "
                    f"{arabic_name} - {player_info['goals']}G {player_info['assists']}A"
                )

            except Exception as e:
                error_msg = f"Error saving player {arabic_name}: {str(e)}"
                logger.error(error_msg)
                stats["errors"].append(error_msg)
                continue

        # ==================== RETURN SUMMARY ====================
        logger.info("=" * 60)
        logger.info("COMPLETED!")
        logger.info("=" * 60)

        return JsonResponse(
            {
                "success": True,
                "message": f"Successfully scraped {stats['total_players']} players with complete stats",
                "season": season.name,
                "competition": competition.name,
                "scorers_url": scorers_url,
                "assists_url": assists_url,
                "stats": {
                    "total_players": stats["total_players"],
                    "created_players": stats["created_players"],
                    "updated_players": stats["updated_players"],
                    "players_with_goals": stats["with_goals"],
                    "players_with_assists": stats["with_assists"],
                    "players_with_both": stats["with_both"],
                    "errors": len(stats["errors"]),
                    "error_details": stats["errors"][:5] if stats["errors"] else [],
                },
            }
        )

    except requests.RequestException as e:
        logger.error(f"Failed to fetch page: {e}")
        return JsonResponse({"error": f"Failed to fetch page: {str(e)}"}, status=500)

    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        import traceback

        logger.error(traceback.format_exc())
        return JsonResponse({"error": f"Unexpected error: {str(e)}"}, status=500)
