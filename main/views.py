from django.shortcuts import render
from django.http import JsonResponse
from django.views import View
from bs4 import BeautifulSoup
import requests
import logging
from urllib.parse import urljoin

logger = logging.getLogger(__name__)


def send_safe_request(url, timeout=30):
    """
    Optimized request with reduced timeout
    """
    SCRAPER_API_KEY = "6c49e45c1661f242378ca489f92b6ede"
    
    api_url = "http://api.scraperapi.com"
    params = {
        "api_key": SCRAPER_API_KEY,
        "url": url,
        "render": "true",  # Keep JS rendering for now
    }

    try:
        response = requests.get(api_url, params=params, timeout=timeout)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        logger.error(f"ScraperAPI request failed for {url}: {e}")
        raise


class MatchDataExtractor(View):
    """Optimized Django CBV with fast parsing"""

    def get(self, request):
        try:
            date = request.GET.get("date", "")
            url = (
                f"https://jdwel.com/matches/?date={date}"
                if date
                else "https://jdwel.com/matches/"
            )

            html = send_safe_request(url)
            
            # Add debug mode
            debug = request.GET.get("debug", "false").lower() == "true"
            if debug:
                return JsonResponse({
                    "html_length": len(html),
                    "html_preview": html[:1000],
                    "contains_comp_matches": "comp_matches_list" in html,
                    "contains_single_match": "single_match" in html,
                })
            
            matches_data = self.extract_matches_fast(html, base_url="https://jdwel.com")

            return JsonResponse(
                {
                    "success": True,
                    "total_matches": len(matches_data),
                    "data": matches_data,
                },
                json_dumps_params={"ensure_ascii": False},
            )
        except Exception as e:
            logger.exception("Error in GET MatchDataExtractor")
            return JsonResponse(
                {"success": False, "error": str(e)},
                status=500,
            )

    def post(self, request):
        html_content = request.POST.get("html_content", "")
        if not html_content:
            return JsonResponse(
                {"success": False, "error": "No HTML content provided"}, status=400
            )
        try:
            matches_data = self.extract_matches_fast(
                html_content, base_url="https://jdwel.com"
            )
            return JsonResponse(
                {
                    "success": True,
                    "total_matches": len(matches_data),
                    "data": matches_data,
                },
                json_dumps_params={"ensure_ascii": False},
            )
        except Exception as e:
            logger.exception("Error in POST MatchDataExtractor")
            return JsonResponse(
                {"success": False, "error": str(e)},
                status=500,
            )

    def extract_matches_fast(self, html_content, base_url=None):
        """Optimized parsing - using html.parser (faster than lxml for small docs)"""
        soup = BeautifulSoup(html_content, "html.parser")
        matches = []

        # Try multiple possible selectors
        comp_lists = soup.find_all("ul", class_="comp_matches_list")
        
        if not comp_lists:
            # Try alternative selectors
            comp_lists = soup.find_all("ul", attrs={"class": lambda x: x and "comp_matches" in x})
        
        logger.info(f"Found {len(comp_lists)} competition lists")

        for comp_list in comp_lists:
            competition = None
            
            # Extract competition info
            comp_separator = comp_list.find("div", class_="comp_separator")
            if comp_separator:
                comp_title = comp_separator.find("h4", class_="title")
                comp_logo_img = comp_separator.find("img", class_="comp_logo")
                comp_id = comp_list.get("data-comp_id") or comp_list.get("data-compid", "")
                
                competition = {
                    "id": comp_id,
                    "name": comp_title.get_text(strip=True) if comp_title else "",
                    "logo": self._resolve_img_src(comp_logo_img, base_url),
                }

            # Find matches - try multiple selectors
            match_items = comp_list.find_all("li", class_="single_match")
            if not match_items:
                match_items = comp_list.find_all("li", attrs={"class": lambda x: x and "match" in x.lower()})
            
            logger.info(f"Found {len(match_items)} matches in competition {competition.get('name') if competition else 'Unknown'}")

            for match_el in match_items:
                try:
                    match_data = self.extract_match_data_fast(match_el, competition, base_url)
                    matches.append(match_data)
                except Exception as e:
                    logger.warning(f"Failed to parse match: {e}", exc_info=True)
                    continue

        return matches

    @staticmethod
    def _resolve_img_src(img_tag, base_url=None):
        """Optimized image src resolution"""
        if not img_tag:
            return ""
        src = (
            img_tag.get("src")
            or img_tag.get("data-src")
            or img_tag.get("data-original")
            or img_tag.get("data-lazy-src")
            or ""
        )
        return urljoin(base_url, src) if base_url and src else src

    def extract_match_data_fast(self, match_element, competition, base_url=None):
        """Optimized match data extraction"""
        
        # Get attributes efficiently
        attrs = match_element.attrs
        match_id = attrs.get("id", "").replace("match_", "")
        view_status = attrs.get("data-view_status", "")
        is_live = attrs.get("data-is_live") == "1"

        # Status
        status = ""
        match_minute = ""
        status_element = match_element.find("div", class_="match_status")

        if status_element and not status_element.has_attr("hidden"):
            status_span = status_element.find("span")
            if status_span:
                minute_span = status_span.find("span", {"dir": "ltr"})
                if minute_span:
                    match_minute = minute_span.get_text(strip=True)
                    status = "live"
                else:
                    status = status_span.get_text(strip=True)
        
        if not status:
            status = (
                "انتهت"
                if view_status == "done"
                else "لم تبدأ" if view_status == "coming" else view_status
            )

        # Teams
        home_div = match_element.find("div", class_="hometeam")
        away_div = match_element.find("div", class_="awayteam")

        if not home_div or not away_div:
            raise ValueError(f"Teams not found for match: {match_id}")

        home_name_span = home_div.find("span", class_="the_team")
        home_name = home_name_span.get_text(strip=True) if home_name_span else ""
        home_logo = self._resolve_img_src(home_div.find("img", class_="team_logo"), base_url)

        away_name_span = away_div.find("span", class_="the_team")
        away_name = away_name_span.get_text(strip=True) if away_name_span else ""
        away_logo = self._resolve_img_src(away_div.find("img", class_="team_logo"), base_url)

        # Score
        home_score = away_score = ""
        score_element = match_element.find("span", class_="match_score")

        if score_element and not score_element.has_attr("hidden"):
            hs = score_element.find("span", class_="hometeam")
            as_ = score_element.find("span", class_="awayteam")

            home_score = hs.get_text(strip=True) if hs else ""
            away_score = as_.get_text(strip=True) if as_ else ""

            if not home_score or not away_score:
                text = score_element.get_text(strip=True)
                for sep in ("-", ":", "—"):
                    if sep in text:
                        parts = text.split(sep, 1)
                        if len(parts) == 2:
                            home_score = home_score or parts[0].strip()
                            away_score = away_score or parts[1].strip()
                            break

        # Time
        match_time = ""
        time_element = match_element.find("div", class_="match_time")
        if time_element:
            otime = time_element.find("span", class_="the_otime")
            match_time = (
                otime.get_text(strip=True)
                if otime
                else time_element.get_text(strip=True)
            )

        return {
            "match_id": match_id,
            "competition": competition,
            "status": status,
            "view_status": view_status,
            "is_live": is_live,
            "match_minute": match_minute,
            "home_team": {"name": home_name, "logo": home_logo, "score": home_score},
            "away_team": {"name": away_name, "logo": away_logo, "score": away_score},
            "match_time": match_time,
        }