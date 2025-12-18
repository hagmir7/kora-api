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


def scrape_competition_matches(request):
    """
    View to scrape match fixtures data and save to Match and SeasonMatch models
    """
    try:
        # Get competition_id from request
        competition_id = request.GET.get("competition_id")
        if not competition_id:
            return JsonResponse(
                {"error": "competition_id parameter is required"}, status=400
            )

        try:
            competition = Competition.objects.get(id=competition_id)
        except Competition.DoesNotExist:
            return JsonResponse({"error": "Competition not found"}, status=404)

        # Get URL from request
        url = request.GET.get("url")
        if not url:
            return JsonResponse({"error": "url parameter is required"}, status=400)

        # Fetch content using ScraperAPI
        content = fetch_with_scraper_api(url, use_js=True)

        # Parse HTML
        soup = BeautifulSoup(content, "html.parser")

        # Extract season name from page title
        title_element = soup.find("h1", class_="title")
        season_name = "2025/2026"  # default
        start_date = "2025-08-01"  # default
        end_date = "2026-05-31"  # default

        if title_element:
            title_text = title_element.text.strip()
            season_match = re.search(r"(\d{4})/(\d{4})", title_text)
            if season_match:
                season_name = season_match.group(0)
                start_year = season_match.group(1)
                end_year = season_match.group(2)
                start_date = f"{start_year}-08-01"
                end_date = f"{end_year}-05-31"

        # Allow override from request parameters
        season_name = request.GET.get("season_name", season_name)
        start_date = request.GET.get("start_date", start_date)
        end_date = request.GET.get("end_date", end_date)

        # Get or create season
        season, season_created = Season.objects.get_or_create(
            name=season_name,
            competition=competition,
            defaults={
                "url": url,
                "start_date": start_date,
                "end_date": end_date,
            },
        )

        logger.info(
            f"{'Created' if season_created else 'Using existing'} season: {season}"
        )

        stats = {"total_matches": 0, "created": 0, "updated": 0, "errors": []}

        current_round = None
        current_stage = None

        # Find all stage lists (includes both matchday_list and direct stage matches)
        stage_lists = soup.find_all("ul", class_="stage_list")

        for stage_list in stage_lists:
            try:
                # Check if there's a stage header
                stage_header = stage_list.find("div", class_="fixtures_stages")
                if stage_header:
                    stage_title = stage_header.find("h5", class_="title")
                    if stage_title:
                        current_stage = stage_title.text.strip()
                        logger.info(f"Processing Stage: {current_stage}")

                # Find matchday lists within this stage
                matchday_lists = stage_list.find_all("ul", class_="matchday_list")

                for matchday in matchday_lists:
                    # Get round from header
                    header = matchday.find("div", class_="matchday_header")
                    if header:
                        current_round = extract_round(header)
                        logger.info(f"Processing Round {current_round}")

                    # Find all match elements in this matchday
                    match_elements = matchday.find_all("li", class_="single_match")
                    process_matches(
                        match_elements,
                        current_round,
                        current_stage,
                        competition,
                        season,
                        stats,
                    )

                # IMPORTANT: Also process direct matches under stage_list (like finals)
                # These are matches not wrapped in matchday_list
                direct_matches = stage_list.find_all(
                    "li", class_="single_match", recursive=False
                )
                if direct_matches:
                    logger.info(
                        f"Processing {len(direct_matches)} direct matches under stage: {current_stage}"
                    )
                    process_matches(
                        direct_matches,
                        current_round,
                        current_stage,
                        competition,
                        season,
                        stats,
                    )

            except Exception as e:
                logger.error(f"Error processing stage list: {e}")
                continue

        # Return summary
        return JsonResponse(
            {
                "success": True,
                "message": f"Successfully scraped {stats['total_matches']} matches",
                "season": season.name,
                "competition": competition.name,
                "url": url,
                "stats": {
                    "total_matches": stats["total_matches"],
                    "created": stats["created"],
                    "updated": stats["updated"],
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
        return JsonResponse({"error": f"Unexpected error: {str(e)}"}, status=500)


def process_matches(
    match_elements, current_round, current_stage, competition, season, stats
):
    """
    Helper function to process a list of match elements
    """
    for match_elem in match_elements:
        try:
            match_code = extract_match_id(match_elem)
            if not match_code:
                continue

            # Extract teams
            home_team_elem = match_elem.find("div", class_="hometeam")
            away_team_elem = match_elem.find("div", class_="awayteam")

            home_team_name = extract_team_name(home_team_elem)
            away_team_name = extract_team_name(away_team_elem)

            if not home_team_name or not away_team_name:
                logger.warning(f"Skipping match {match_code}: missing team names")
                continue

            # Get or create teams
            home_team = get_or_create_team(home_team_name)
            away_team = get_or_create_team(away_team_name)

            if not home_team or not away_team:
                raise ValueError("Could not create teams")

            # Extract datetime
            match_datetime = extract_datetime(match_elem)
            if not match_datetime:
                # Set a future date for upcoming matches
                match_datetime = timezone.now() + timezone.timedelta(days=30)

            # Extract scores
            home_score, away_score = extract_scores(match_elem)

            # Determine status
            status = determine_status(match_elem)

            # Create or update match
            match, created = Match.objects.update_or_create(
                code=match_code,
                competition=competition,
                defaults={
                    "home_team": home_team,
                    "away_team": away_team,
                    "date_time": match_datetime,
                    "home_score": home_score,
                    "away_score": away_score,
                    "round": current_round,
                    "status": status,
                },
            )

            # Link match to season
            SeasonMatch.objects.get_or_create(
                season=season, match=match, defaults={"round": current_round}
            )

            if created:
                stats["created"] += 1
                logger.info(
                    f"✅ Created match: {home_team_name} vs {away_team_name} ({current_stage})"
                )
            else:
                stats["updated"] += 1
                logger.info(
                    f"🔄 Updated match: {home_team_name} vs {away_team_name} ({current_stage})"
                )

            stats["total_matches"] += 1

        except Exception as e:
            error_msg = f"Error processing match {match_code}: {str(e)}"
            logger.error(error_msg)
            stats["errors"].append(error_msg)
            continue


def clear_matches_cache(request):
    """
    View to clear the scraper cache
    """
    global _cache
    _cache = {}
    return JsonResponse({"success": True, "message": "Matches cache cleared successfully"})
