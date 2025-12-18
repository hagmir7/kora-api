from django.http import JsonResponse
from django.core.files.base import ContentFile
from django.utils import timezone
from bs4 import BeautifulSoup
import requests
import random
import logging
import re
import os
from datetime import datetime
from urllib.parse import urlparse

from main.models import Match, Team, Competition


logger = logging.getLogger(__name__)

SCRAPER_API_KEYS = [
    "d0a075b5a0bcdefcaabdd16757067f0f",
    "2d118deea43615910a5ed5cc6f8f56fa",
    "4c64b63e0a8ebf500aaa60c2514e6e8f",
]


# =========================
# Scraper helpers
# =========================


def fetch_with_scraper_api(url, use_js=True):
    api_url = "http://api.scraperapi.com"
    params = {
        "api_key": random.choice(SCRAPER_API_KEYS),
        "url": url,
        "render": "true" if use_js else "false",
    }
    response = requests.get(api_url, params=params, timeout=60)
    response.raise_for_status()
    return response.content


def download_team_logo(url, team_name):
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        ext = os.path.splitext(urlparse(url).path)[1] or ".png"
        filename = f"{team_name.replace(' ', '_').lower()}{ext}"
        return ContentFile(response.content, name=filename)
    except Exception:
        return None


def parse_match_datetime(match_data):
    """
    Always returns a timezone-aware datetime (NEVER None)
    """
    fallback = timezone.now()

    try:
        if not match_data.get("date") or not match_data.get("time"):
            return fallback

        date_str = match_data["date"].split("،")[-1].strip()
        time_str = match_data["time"].strip()

        dt = datetime.strptime(f"{date_str} {time_str}", "%Y/%m/%d %H:%M")
        return timezone.make_aware(dt)

    except Exception as e:
        logger.warning(f"Datetime parse failed: {e}")
        return fallback


def scrape_match_data(soup):
    data = {}

    frame = soup.find("div", id="match_frame")
    if not frame:
        raise ValueError("match_frame not found")

    data["match_id"] = frame.get("data-match_id")

    # Competition
    header = soup.find("div", id="match_header")
    comp_a = header.find("a") if header else None
    data["competition_name"] = comp_a.text.strip() if comp_a else None

    def parse_team(class_name):
        team = {"name": None, "logo": None, "english_name": None}

        div = soup.find("div", class_=class_name)
        if not div:
            return team

        name_el = div.find("span", class_="the_team")
        team["name"] = name_el.text.strip() if name_el else None

        img = div.find("img", class_="team_logo")
        if img:
            team["logo"] = img.get("src")
            team["english_name"] = img.get("alt")

        return team

    data["home_team"] = parse_team("hometeam")
    data["away_team"] = parse_team("awayteam")

    # Scores
    data["home_score"] = 0
    data["away_score"] = 0
    score = soup.find("span", class_="match_score")
    if score:
        h = score.find("span", class_="hometeam")
        a = score.find("span", class_="awayteam")
        data["home_score"] = int(h.text) if h and h.text.isdigit() else 0
        data["away_score"] = int(a.text) if a and a.text.isdigit() else 0

    # Status
    status = soup.find("div", class_="match_status")
    data["status"] = status.text.strip() if status else "scheduled"

    # Match info
    info = soup.find("div", class_="match_info")
    if info:

        def get_li(cls):
            li = info.find("li", class_=cls)
            return li.text.strip() if li else None

        data["date"] = get_li("match_date")

        time_li = info.find("li", class_="match_time")
        time_span = time_li.find("span", class_="the_time") if time_li else None
        data["time"] = time_span.text.strip() if time_span else None

        stadium = get_li("match_stadium")
        data["stadium"] = stadium.replace("استاد", "").strip() if stadium else None

        stage = get_li("match_stage")
        data["stage"] = stage.replace("المرحلة:", "").strip() if stage else None

    return data


# =========================
# Main View
# =========================


def get_or_create_match(request, match_id=None, match_url=None):
    try:
        if not match_id and not match_url:
            return JsonResponse({"error": "match_id or match_url required"}, status=400)

        # Try DB first
        if match_id:
            match = Match.objects.filter(code=match_id).first()
            if match:
                return JsonResponse(
                    {
                        "success": True,
                        "source": "database",
                        "match": {
                            "id": match.id,
                            "code": match.code,
                            "home_team": match.home_team.name,
                            "away_team": match.away_team.name,
                            "home_score": match.home_score,
                            "away_score": match.away_score,
                            "date_time": match.date_time.isoformat(),
                            "status": match.status,
                            "round": match.round,
                        },
                    }
                )

        if not match_url:
            match_url = f"https://jdwel.com/match/{match_id}/"

        html = fetch_with_scraper_api(match_url)
        soup = BeautifulSoup(html, "html.parser")
        data = scrape_match_data(soup)

        home_team, _ = Team.objects.get_or_create(
            arabic_name=data["home_team"]["name"],
            defaults={
                "name": data["home_team"]["english_name"] or data["home_team"]["name"]
            },
        )

        away_team, _ = Team.objects.get_or_create(
            arabic_name=data["away_team"]["name"],
            defaults={
                "name": data["away_team"]["english_name"] or data["away_team"]["name"]
            },
        )

        for team, key in [(home_team, "home_team"), (away_team, "away_team")]:
            if data[key]["logo"] and not team.logo_url:
                logo = download_team_logo(data[key]["logo"], data[key]["name"])
                if logo:
                    team.logo_url.save(logo.name, logo, save=True)

        competition = None
        if data.get("competition_name"):
            competition, _ = Competition.objects.get_or_create(
                name=data["competition_name"]
            )

        match_datetime = parse_match_datetime(data)
        round_value = 0 if data.get("stage") == "النهائي" else None

        match, created = Match.objects.update_or_create(
            code=data["match_id"],
            defaults={
                "home_team": home_team,
                "away_team": away_team,
                "competition": competition,
                "home_score": data["home_score"],
                "away_score": data["away_score"],
                "date_time": match_datetime,
                "venue": data.get("stadium"),
                "status": data["status"],
                "round": round_value,
            },
        )

        return JsonResponse(
            {
                "success": True,
                "source": "scraped",
                "created": created,
                "match": {
                    "id": match.id,
                    "code": match.code,
                    "home_team": match.home_team.name,
                    "away_team": match.away_team.name,
                    "home_score": match.home_score,
                    "away_score": match.away_score,
                    "date_time": match.date_time.isoformat(),
                    "status": match.status,
                    "round": match.round,
                },
            }
        )

    except Exception as e:
        logger.exception("Match fetch failed")
        return JsonResponse({"error": str(e)}, status=500)


def get_match(request):
    """
    Get match by full URL (?url=...)
    """
    match_url = request.GET.get("url")
    if not match_url:
        return JsonResponse({"error": "url query parameter is required"}, status=400)

    # Try to extract match_id from URL
    match_id = None
    match = re.search(r"/match/(\d+)/", match_url)
    if match:
        match_id = match.group(1)

    return get_or_create_match(request, match_id=match_id, match_url=match_url)
