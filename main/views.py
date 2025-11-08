from django.shortcuts import render
from django.http import JsonResponse
from django.views import View
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import time
import logging
from urllib.parse import urljoin
from threading import Lock
import threading

logger = logging.getLogger(__name__)


class PlaywrightManager:
    """Thread-safe singleton browser manager"""

    _lock = Lock()
    _instances = {}  # Store browser per thread

    @classmethod
    def get_browser(cls, headless=True):
        """Get or create browser for current thread"""
        thread_id = threading.get_ident()

        with cls._lock:
            if thread_id not in cls._instances:
                playwright = sync_playwright().start()
                browser = playwright.chromium.launch(
                    headless=headless,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-gpu",
                    ],
                )
                cls._instances[thread_id] = {
                    "playwright": playwright,
                    "browser": browser,
                }

            return cls._instances[thread_id]["browser"]

    @classmethod
    def cleanup(cls):
        """Clean up all browser instances"""
        thread_id = threading.get_ident()
        with cls._lock:
            if thread_id in cls._instances:
                instance = cls._instances[thread_id]
                try:
                    instance["browser"].close()
                except Exception:
                    pass
                try:
                    instance["playwright"].stop()
                except Exception:
                    pass
                del cls._instances[thread_id]


def send_safe_request(
    url,
    wait_selector=".comp_matches_list",
    navigation_timeout=15_000,
    selector_timeout=10_000,
    extra_wait=0.5,
):
    """
    Thread-safe page fetch - creates fresh browser instance per request
    This avoids greenlet threading issues
    """
    playwright = None
    browser = None
    context = None
    page = None

    try:
        # Create fresh instances for this request (avoids threading issues)
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-gpu",
            ],
        )

        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={"width": 1920, "height": 1080},
        )

        page = context.new_page()
        page.set_default_navigation_timeout(navigation_timeout)
        page.set_default_timeout(selector_timeout)

        # Aggressive resource blocking
        def route_intercept(route):
            resource_type = route.request.resource_type
            if resource_type in ("image", "font", "media", "stylesheet"):
                return route.abort()

            url_lower = route.request.url.lower()
            blocked = [
                "google-analytics",
                "googletagmanager",
                "facebook",
                "doubleclick",
            ]
            if any(domain in url_lower for domain in blocked):
                return route.abort()

            return route.continue_()

        page.route("**/*", route_intercept)

        # Fast navigation
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=navigation_timeout)
        except PlaywrightTimeoutError:
            pass

        # Wait for content
        try:
            page.wait_for_selector(
                wait_selector, state="attached", timeout=selector_timeout
            )
        except PlaywrightTimeoutError:
            pass

        if extra_wait:
            time.sleep(extra_wait)

        html_content = page.content()
        return html_content

    finally:
        # Clean up in reverse order
        try:
            if page:
                page.close()
        except Exception:
            pass
        try:
            if context:
                context.close()
        except Exception:
            pass
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        try:
            if playwright:
                playwright.stop()
        except Exception:
            pass


class MatchDataExtractor(View):
    """Optimized Django CBV with fast parsing"""

    def get(self, request):
        try:
            date = request.GET.get('date')
            html = send_safe_request(f"https://jdwel.com/matches/?date={date}")
            matches_data = self.extract_matches_fast(html)
            return JsonResponse(
                {
                    "success": True,
                    "total_matches": len(matches_data),
                    "data": matches_data,
                },
                json_dumps_params={"ensure_ascii": False, "indent": 2},
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
            matches_data = self.extract_matches_fast(html_content)
            return JsonResponse(
                {
                    "success": True,
                    "total_matches": len(matches_data),
                    "data": matches_data,
                },
                json_dumps_params={"ensure_ascii": False, "indent": 2},
            )
        except Exception as e:
            logger.exception("Error in POST MatchDataExtractor")
            return JsonResponse(
                {"success": False, "error": str(e)},
                status=500,
            )

    def extract_matches_fast(self, html_content, base_url=None):
        """Optimized parsing with lxml"""
        soup = BeautifulSoup(html_content, "lxml")
        matches = []

        comp_lists = soup.find_all("ul", class_="comp_matches_list")

        for comp_list in comp_lists:
            comp_separator = comp_list.find("div", class_="comp_separator")
            competition = None

            if comp_separator:
                comp_title = comp_separator.find("h4", class_="title")
                comp_logo_img = comp_separator.find("img", class_="comp_logo")
                comp_id = comp_list.get("data-comp_id") or comp_list.get(
                    "data-compid", ""
                )
                competition = {
                    "id": comp_id,
                    "name": comp_title.get_text(strip=True) if comp_title else "",
                    "logo": self._resolve_img_src(comp_logo_img, base_url),
                }

            match_items = comp_list.find_all("li", class_="single_match")

            for match_el in match_items:
                try:
                    matches.append(
                        self.extract_match_data_fast(match_el, competition, base_url)
                    )
                except Exception as e:
                    logger.warning(f"Failed to parse match: {e}")
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
            or ""
        )
        return urljoin(base_url, src) if base_url and src else src

    def extract_match_data_fast(self, match_element, competition, base_url=None):
        """Optimized match data extraction"""

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
        else:
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

        home_name = home_div.find("span", class_="the_team")
        home_name = home_name.get_text(strip=True) if home_name else ""
        home_logo = self._resolve_img_src(
            home_div.find("img", class_="team_logo"), base_url
        )

        away_name = away_div.find("span", class_="the_team")
        away_name = away_name.get_text(strip=True) if away_name else ""
        away_logo = self._resolve_img_src(
            away_div.find("img", class_="team_logo"), base_url
        )

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
