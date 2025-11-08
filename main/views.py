from django.shortcuts import render
from django.http import JsonResponse
from django.views import View
from bs4 import BeautifulSoup
import requests
import logging
from urllib.parse import urljoin
import re
from functools import lru_cache
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Simple in-memory cache
_cache = {}
CACHE_DURATION = 60  # seconds


def send_safe_request(url, timeout=15, use_js=False):
    """
    Optimized request - JS rendering only when needed
    """
    SCRAPER_API_KEY = "6c49e45c1661f242378ca489f92b6ede"

    # Check cache first
    cache_key = f"{url}_{use_js}"
    if cache_key in _cache:
        cached_data, cached_time = _cache[cache_key]
        if (datetime.now() - cached_time).seconds < CACHE_DURATION:
            logger.info(f"Cache hit for {url}")
            return cached_data

    api_url = "http://api.scraperapi.com"
    params = {
        "api_key": SCRAPER_API_KEY,
        "url": url,
        "render": "true" if use_js else "false",  # Control JS rendering
    }

    try:
        response = requests.get(api_url, params=params, timeout=timeout)
        response.raise_for_status()
        html = response.text

        # Cache the result
        _cache[cache_key] = (html, datetime.now())

        # Clean old cache entries
        if len(_cache) > 100:
            _clean_cache()

        return html
    except requests.RequestException as e:
        logger.error(f"ScraperAPI request failed for {url}: {e}")
        raise


def _clean_cache():
    """Remove expired cache entries"""
    global _cache
    now = datetime.now()
    _cache = {k: v for k, v in _cache.items() if (now - v[1]).seconds < CACHE_DURATION}


class MatchDataExtractor(View):
    """Ultra-fast Django CBV with caching and smart JS detection"""

    def get(self, request):
        try:
            date = request.GET.get("date", "")
            debug = request.GET.get("debug", "false").lower() == "true"
            force_js = request.GET.get("js", "auto")  # auto, true, false

            url = (
                f"https://jdwel.com/matches/?date={date}"
                if date
                else "https://jdwel.com/matches/"
            )

            # Try without JS first (much faster)
            use_js = force_js == "true"
            if force_js == "auto":
                use_js = False  # Start with fast mode

            html = send_safe_request(url, use_js=use_js)

            # Quick check if we got content
            has_matches = "single_match" in html or "comp_matches_list" in html

            # If no matches and we didn't use JS, try again with JS
            if not has_matches and force_js == "auto":
                logger.info(
                    "No matches found without JS, retrying with JS rendering..."
                )
                use_js = True
                html = send_safe_request(url, use_js=True)

            # Debug mode
            if debug:
                soup = BeautifulSoup(html, "html.parser")
                all_uls = soup.find_all("ul")
                all_lis = soup.find_all("li")

                return JsonResponse(
                    {
                        "html_length": len(html),
                        "html_preview": html[:2000],
                        "total_uls": len(all_uls),
                        "ul_classes": [ul.get("class") for ul in all_uls[:10]],
                        "total_lis": len(all_lis),
                        "li_classes": [li.get("class") for li in all_lis[:20]],
                        "contains_comp_matches_list": "comp_matches_list" in html,
                        "contains_single_match": "single_match" in html,
                        "js_rendering_used": use_js,
                        "cache_size": len(_cache),
                    }
                )

            matches_data = self.extract_matches_fast(html, base_url="https://jdwel.com")

            return JsonResponse(
                {
                    "success": True,
                    "total_matches": len(matches_data),
                    "data": matches_data,
                    "js_rendering_used": use_js,
                    "from_cache": f"{url}_{use_js}" in _cache,
                },
                json_dumps_params={"ensure_ascii": False},
            )
        except Exception as e:
            logger.exception("Error in GET MatchDataExtractor")
            return JsonResponse(
                {"success": False, "error": str(e), "error_type": type(e).__name__},
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
        """Optimized parsing with html.parser (fastest)"""
        soup = BeautifulSoup(html_content, "html.parser")
        matches = []

        # Primary selector (fastest path)
        comp_lists = soup.find_all("ul", class_="comp_matches_list")

        # Fallback selectors only if needed
        if not comp_lists:
            comp_lists = soup.find_all("ul", class_=re.compile("comp.*match", re.I))

        if not comp_lists:
            all_uls = soup.find_all("ul")
            comp_lists = [
                ul for ul in all_uls if ul.find("li", class_=re.compile("match", re.I))
            ]

        logger.info(f"Found {len(comp_lists)} competition lists")

        for comp_list in comp_lists:
            competition = self._extract_competition(comp_list, base_url)
            match_items = comp_list.find_all("li", class_="single_match")

            if not match_items:
                match_items = comp_list.find_all(
                    "li", class_=re.compile(".*match.*", re.I)
                )

            for match_el in match_items:
                try:
                    matches.append(
                        self.extract_match_data_fast(match_el, competition, base_url)
                    )
                except Exception as e:
                    logger.warning(f"Failed to parse match: {e}")
                    continue

        return matches

    def _extract_competition(self, comp_list, base_url):
        """Extract competition data"""
        comp_separator = comp_list.find("div", class_="comp_separator")
        if not comp_separator:
            return None

        comp_title = comp_separator.find("h4", class_="title")
        comp_logo_img = comp_separator.find("img", class_="comp_logo")
        comp_id = comp_list.get("data-comp_id") or comp_list.get("data-compid", "")

        return {
            "id": comp_id,
            "name": comp_title.get_text(strip=True) if comp_title else "",
            "logo": self._resolve_img_src(comp_logo_img, base_url),
        }

    @staticmethod
    def _resolve_img_src(img_tag, base_url=None):
        """Fast image src resolution"""
        if not img_tag:
            return ""
        src = img_tag.get("src") or img_tag.get("data-src") or ""
        return urljoin(base_url, src) if base_url and src else src

    def extract_match_data_fast(self, match_element, competition, base_url=None):
        """Ultra-optimized match extraction"""

        attrs = match_element.attrs
        match_id = attrs.get("id", "").replace("match_", "")
        view_status = attrs.get("data-view_status", "")
        is_live = attrs.get("data-is_live") == "1"

        # Status (simplified)
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

        # Teams (fast path)
        home_div = match_element.find("div", class_="hometeam")
        away_div = match_element.find("div", class_="awayteam")

        if not home_div or not away_div:
            raise ValueError(f"Teams not found for match: {match_id}")

        home_name_span = home_div.find("span", class_="the_team")
        home_name = home_name_span.get_text(strip=True) if home_name_span else ""
        home_logo = self._resolve_img_src(
            home_div.find("img", class_="team_logo"), base_url
        )

        away_name_span = away_div.find("span", class_="the_team")
        away_name = away_name_span.get_text(strip=True) if away_name_span else ""
        away_logo = self._resolve_img_src(
            away_div.find("img", class_="team_logo"), base_url
        )

        # Score (fast path)
        home_score = away_score = ""
        score_element = match_element.find("span", class_="match_score")

        if score_element and not score_element.has_attr("hidden"):
            hs = score_element.find("span", class_="hometeam")
            as_ = score_element.find("span", class_="awayteam")
            home_score = hs.get_text(strip=True) if hs else ""
            away_score = as_.get_text(strip=True) if as_ else ""

        # Time (fast path)
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
