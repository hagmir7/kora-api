from django.shortcuts import render
from django.http import JsonResponse
from django.core.files.base import ContentFile
from bs4 import BeautifulSoup
import requests
import random
from datetime import datetime
from main.models import Team, Season, SeasonTeam, Competition
import logging
from urllib.parse import urlparse
import os

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


def download_team_logo(logo_url, team_name):
    """
    Download team logo from URL and return ContentFile
    """
    try:
        response = requests.get(logo_url, timeout=10)
        response.raise_for_status()

        # Get file extension from URL
        parsed_url = urlparse(logo_url)
        file_ext = os.path.splitext(parsed_url.path)[1] or ".png"

        # Create filename from team name
        filename = f"{team_name.replace(' ', '_').lower()}{file_ext}"

        return ContentFile(response.content, name=filename)

    except Exception as e:
        logger.error(f"Failed to download logo for {team_name}: {e}")
        return None


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


def scrape_team_standings(request):
    """
    View to scrape team standings data and save to SeasonTeam model
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

        # URL of the page to scrape
        url = request.GET.get(
            "url", "https://jdwel.com/2024-2025-england-premier-league/"
        )

        # Fetch content using ScraperAPI
        content = fetch_with_scraper_api(url, use_js=True)

        # Parse HTML
        soup = BeautifulSoup(content, "html.parser")

        # Find the standings table
        table = soup.find("table", class_="standings_jdwel")

        if not table:
            return JsonResponse({"error": "Table not found"}, status=404)

        # Extract season name from page title
        title_element = soup.find("h1", class_="title")
        season_name = "2024/2025"  # default
        start_date = "2024-09-01"  # default
        end_date = "2025-05-31"  # default

        if title_element:
            title_text = title_element.text.strip()
            # Extract season from title like "جدول ترتيب فرق الدوري الإنجليزي 2023/2024"
            import re

            season_match = re.search(r"(\d{4})/(\d{4})", title_text)
            if season_match:
                season_name = season_match.group(0)  # e.g., "2023/2024"
                start_year = season_match.group(1)  # e.g., "2023"
                end_year = season_match.group(2)  # e.g., "2024"

                # Calculate dates based on season years
                start_date = f"{start_year}-08-01"  # Usually starts in August
                end_date = f"{end_year}-05-31"  # Usually ends in May

        # Allow override from request parameters
        season_name = request.GET.get("season_name", season_name)
        start_date = request.GET.get("start_date", start_date)
        end_date = request.GET.get("end_date", end_date)

        # Get or create season using name, competition AND url as unique identifier
        season, season_created = Season.objects.get_or_create(
            name=season_name,
            competition=competition,
            url=url,  # Include URL in the lookup
            defaults={
                "start_date": start_date,
                "end_date": end_date,
            },
        )

        logger.info(
            f"{'Created' if season_created else 'Using existing'} season: {season}"
        )

        # Find all team rows (skip header)
        rows = table.find("tbody").find_all("tr")

        scraped_count = 0

        for row in rows:
            try:
                # Check if team is the champion (hero) by checking row style or "البطل" text
                is_hero = False
                row_style = row.get("style", "")
                if "background-color" in row_style and "rgba" in row_style:
                    is_hero = True
                    logger.info(f"Found hero by background color: {row_style}")

                # Extract team name (Arabic)
                team_name_cell = row.find("td", class_="team")
                arabic_name = team_name_cell.find("div").text.strip()

                # Double-check with "البطل" text - check all divs in team cell
                team_divs = team_name_cell.find_all("div")
                for div in team_divs:
                    if "البطل" in div.get_text():
                        is_hero = True
                        logger.info(f"Found hero by text for team: {arabic_name}")
                        break

                # Extract team logo URL
                team_logo_img = row.find("td", class_="team_logo").find("img")
                logo_url = team_logo_img.get("src") if team_logo_img else None

                # Get alt text as English name (fallback)
                english_name = (
                    team_logo_img.get("alt") if team_logo_img else arabic_name
                )

                # Extract statistics
                played = int(row.find("td", class_="pld").text.strip())
                won = int(row.find("td", class_="won").text.strip())
                draw = int(row.find("td", class_="draw").text.strip())
                lost = int(row.find("td", class_="lost").text.strip())

                # Extract goals for and against
                goal_plus_minus = row.find("td", class_="goal-plus-minus")
                goals_against = goal_plus_minus.find(
                    "span", class_="goal-minus"
                ).text.strip()
                goals_for = goal_plus_minus.find(
                    "span", class_="goal-plus"
                ).text.strip()
                against = f"{goals_against}:{goals_for}"

                # Extract difference and points
                difference = int(row.find("td", class_="diff").text.strip())
                points = int(row.find("td", class_="pts").find("strong").text.strip())

                # Get or create team with all data
                team, created = Team.objects.get_or_create(
                    arabic_name=arabic_name,
                    defaults={
                        "name": english_name,
                        "arabic_name": arabic_name,
                    },
                )

                # Update team data if not created
                if not created:
                    team.name = english_name

                # Download and save logo if available and not already saved
                if logo_url and not team.logo_url:
                    logo_file = download_team_logo(logo_url, arabic_name)
                    if logo_file:
                        team.logo_url.save(logo_file.name, logo_file, save=False)
                        logger.info(f"Downloaded logo for {arabic_name}")

                team.save()

                # Update or create SeasonTeam entry
                team_season, created = SeasonTeam.objects.update_or_create(
                    team=team,
                    season=season,
                    defaults={
                        "played": played,
                        "won": won,
                        "draw": draw,
                        "lost": lost,
                        "against": against,
                        "difference": difference,
                        "points": points,
                        "hero": is_hero,  # Set hero status
                    },
                )

                scraped_count += 1
                logger.info(
                    f"{'Created' if created else 'Updated'} SeasonTeam for {arabic_name} - Hero: {is_hero}"
                )

            except Exception as e:
                logger.error(f"Error processing row: {e}")
                continue

        return JsonResponse(
            {
                "success": True,
                "message": f"Successfully scraped {scraped_count} teams",
                "season": season.name,
                "competition": competition.name,
                "url": url,
            }
        )

    except requests.RequestException as e:
        logger.error(f"Failed to fetch page: {e}")
        return JsonResponse({"error": f"Failed to fetch page: {str(e)}"}, status=500)

    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return JsonResponse({"error": f"Unexpected error: {str(e)}"}, status=500)


def scrape_competition_teams(request, competition_id=None):
    """
    View to scrape team standings for a specific competition
    """
    try:
        # Get competition from parameter or request
        if not competition_id:
            competition_id = request.GET.get("competition_id")

        if not competition_id:
            return JsonResponse(
                {"error": "competition_id parameter is required"}, status=400
            )

        try:
            competition = Competition.objects.get(id=competition_id)
        except Competition.DoesNotExist:
            return JsonResponse({"error": "Competition not found"}, status=404)

        # Get URL from request or use default
        url = request.GET.get(
            "url", "https://jdwel.com/2025-2026-uefa-champions-league/"
        )

        # Fetch content using ScraperAPI
        content = fetch_with_scraper_api(url, use_js=True)

        # Parse HTML
        soup = BeautifulSoup(content, "html.parser")

        # Find the standings table
        table = soup.find("table", class_="standings_jdwel")

        if not table:
            return JsonResponse({"error": "Table not found"}, status=404)

        # Extract season name from page title
        title_element = soup.find("h1", class_="title")
        season_name = "2024/2025"  # default
        start_date = "2024-09-01"  # default
        end_date = "2025-05-31"  # default

        if title_element:
            title_text = title_element.text.strip()
            # Extract season from title like "جدول ترتيب فرق الدوري الإنجليزي 2023/2024"
            import re

            season_match = re.search(r"(\d{4})/(\d{4})", title_text)
            if season_match:
                season_name = season_match.group(0)  # e.g., "2023/2024"
                start_year = season_match.group(1)  # e.g., "2023"
                end_year = season_match.group(2)  # e.g., "2024"

                # Calculate dates based on season years
                start_date = f"{start_year}-08-01"  # Usually starts in August
                end_date = f"{end_year}-05-31"  # Usually ends in May

        # Allow override from request parameters
        season_name = request.GET.get("season_name", season_name)
        start_date = request.GET.get("start_date", start_date)
        end_date = request.GET.get("end_date", end_date)

        # Get or create season using name, competition AND url as unique identifier
        season, season_created = Season.objects.get_or_create(
            name=season_name,
            competition=competition,
            url=url,  # Include URL in the lookup
            defaults={
                "start_date": start_date,
                "end_date": end_date,
            },
        )

        logger.info(
            f"{'Created' if season_created else 'Using existing'} season: {season}"
        )

        # Find all team rows
        rows = table.find("tbody").find_all("tr")

        scraped_count = 0

        for row in rows:
            try:
                # Check if team is the champion (hero) by checking row style or "البطل" text
                is_hero = False
                row_style = row.get("style", "")
                if "background-color" in row_style and "rgba" in row_style:
                    is_hero = True

                # Extract team name (Arabic)
                team_name_cell = row.find("td", class_="team")
                arabic_name = team_name_cell.find("div").text.strip()

                # Double-check with "البطل" text
                hero_div = team_name_cell.find(
                    "div", string=lambda text: text and "البطل" in text
                )
                if hero_div:
                    is_hero = True

                # Extract team logo URL
                team_logo_img = row.find("td", class_="team_logo").find("img")
                logo_url = team_logo_img.get("src") if team_logo_img else None

                # Get alt text as English name (fallback)
                english_name = (
                    team_logo_img.get("alt") if team_logo_img else arabic_name
                )

                # Extract statistics
                played = int(row.find("td", class_="pld").text.strip())
                won = int(row.find("td", class_="won").text.strip())
                draw = int(row.find("td", class_="draw").text.strip())
                lost = int(row.find("td", class_="lost").text.strip())

                goal_plus_minus = row.find("td", class_="goal-plus-minus")
                goals_against = goal_plus_minus.find(
                    "span", class_="goal-minus"
                ).text.strip()
                goals_for = goal_plus_minus.find(
                    "span", class_="goal-plus"
                ).text.strip()
                against = f"{goals_against}:{goals_for}"

                difference = int(row.find("td", class_="diff").text.strip())
                points = int(row.find("td", class_="pts").find("strong").text.strip())

                # Get or create team with all data
                team, created = Team.objects.get_or_create(
                    arabic_name=arabic_name,
                    defaults={
                        "name": english_name,
                        "arabic_name": arabic_name,
                    },
                )

                # Update team data if not created
                if not created:
                    team.name = english_name

                # Download and save logo if available and not already saved
                if logo_url and not team.logo_url:
                    logo_file = download_team_logo(logo_url, arabic_name)
                    if logo_file:
                        team.logo_url.save(logo_file.name, logo_file, save=False)
                        logger.info(f"Downloaded logo for {arabic_name}")

                team.save()

                team_season, created = SeasonTeam.objects.update_or_create(
                    team=team,
                    season=season,
                    defaults={
                        "played": played,
                        "won": won,
                        "draw": draw,
                        "lost": lost,
                        "against": against,
                        "difference": difference,
                        "points": points,
                        "hero": is_hero,  # Set hero status
                    },
                )

                scraped_count += 1
                logger.info(
                    f"{'Created' if created else 'Updated'} SeasonTeam for {arabic_name} - Hero: {is_hero}"
                )

            except Exception as e:
                logger.error(f"Error processing row: {e}")
                continue

        return JsonResponse(
            {
                "success": True,
                "message": f"Successfully scraped {scraped_count} teams",
                "season": season.name,
                "competition": competition.name,
                "url": url,
            }
        )

    except requests.RequestException as e:
        logger.error(f"Failed to fetch page: {e}")
        return JsonResponse({"error": f"Failed to fetch page: {str(e)}"}, status=500)

    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return JsonResponse({"error": f"Unexpected error: {str(e)}"}, status=500)


def clear_scraper_cache(request):
    """
    View to clear the scraper cache
    """
    global _cache
    _cache = {}
    return JsonResponse({"success": True, "message": "Cache cleared successfully"})
