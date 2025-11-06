# views_matches.py
from django.shortcuts import render
from django.http import JsonResponse
from django.views import View
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import time
import logging
from urllib.parse import urljoin

logger = logging.getLogger(__name__)


# Optional: a simple manager to reuse the browser across requests
class PlaywrightManager:
    _playwright = None
    _browser = None

    @classmethod
    def start(cls, headless=True, user_agent=None, viewport=None, launch_args=None):
        if cls._playwright is None:
            cls._playwright = sync_playwright().start()
        if cls._browser is None:
            chromium = cls._playwright.chromium
            launch_kwargs = {"headless": headless}
            if launch_args:
                launch_kwargs.setdefault("args", launch_args)
            cls._browser = chromium.launch(**launch_kwargs)

        # store default context options for subsequent pages if provided
        cls.user_agent = user_agent
        cls.viewport = viewport

    @classmethod
    def new_page(cls):
        if cls._browser is None:
            raise RuntimeError(
                "Playwright not started. Call PlaywrightManager.start() first."
            )
        context_kwargs = {}
        if getattr(cls, "user_agent", None):
            context_kwargs["user_agent"] = cls.user_agent
        if getattr(cls, "viewport", None):
            context_kwargs["viewport"] = cls.viewport
        context = cls._browser.new_context(**context_kwargs)
        page = context.new_page()
        # set default navigation timeout (ms)
        page.set_default_navigation_timeout(30_000)
        page.set_default_timeout(30_000)
        return page, context

    @classmethod
    def stop(cls):
        try:
            if cls._browser:
                cls._browser.close()
        finally:
            if cls._playwright:
                cls._playwright.stop()
            cls._browser = None
            cls._playwright = None


def send_safe_request(
    url,
    wait_selector=".comp_matches_list",
    navigation_timeout=60_000,
    selector_timeout=20_000,
    extra_wait=1.5,
    block_resources=True,
):
    """
    Robust page fetch:
    - Uses domcontentloaded for faster navigation
    - Waits for a specific selector
    - Falls back to page.content() on timeout
    - Optionally blocks images/fonts to speed up
    """
    p = sync_playwright().start()
    browser = None
    try:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()

        # Tune timeouts
        page.set_default_navigation_timeout(navigation_timeout)
        page.set_default_timeout(selector_timeout)

        # Optionally block heavy resources (images/fonts/analytics) to speed up load
        if block_resources:

            def route_intercept(route):
                req = route.request
                resource_type = req.resource_type
                # block images, fonts, and analytics scripts
                if resource_type in ("image", "font", "media"):
                    return route.abort()
                # optionally block known third-party hosts here
                return route.continue_()

            page.route("**/*", route_intercept)

        try:
            # Use domcontentloaded (less strict than networkidle)
            page.goto(url, wait_until="domcontentloaded")
        except PlaywrightTimeoutError as e:
            # navigation itself timed out. Try a more forgiving approach below.
            print(f"Warning: page.goto timed out: {e}")

        # Wait for the specific element that contains standings (less brittle than networkidle)
        try:
            page.wait_for_selector(
                wait_selector, state="visible", timeout=selector_timeout
            )
        except PlaywrightTimeoutError:
            # If selector didn't appear in time, we still attempt to grab content
            print(
                f"Warning: selector {wait_selector} not found within {selector_timeout}ms"
            )

        # optional short sleep to let client render
        if extra_wait:
            time.sleep(extra_wait)

        html_content = page.content()
        return html_content

    finally:
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
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        p.stop()


class MatchDataExtractor(View):
    """
    Django CBV that scrapes matches from html using BeautifulSoup.
    GET: scrapes live site
    POST: accepts posted 'html_content' and parses it (useful for testing)
    """

    def get(self, request):
        try:
            # Example: start the manager once at server start (optional)
            # PlaywrightManager.start(headless=True, user_agent=..., viewport=...)

            html = send_safe_request("https://jdwel.com/today/")
            matches_data = self.extract_matches(html)
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
                json_dumps_params={"ensure_ascii": False},
            )

    def post(self, request):
        html_content = request.POST.get("html_content", "")
        if not html_content:
            return JsonResponse(
                {"success": False, "error": "No HTML content provided"}, status=400
            )
        try:
            matches_data = self.extract_matches(html_content)
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
                json_dumps_params={"ensure_ascii": False},
            )

    def extract_matches(self, html_content, base_url=None):
        """
        Parse the HTML and return a list of match dicts.

        base_url: optional base for joining relative image URLs (e.g. https://jdwel.com)
        """
        soup = BeautifulSoup(html_content, "html.parser")
        matches = []

        comp_lists = soup.find_all("ul", class_="comp_matches_list")
        for comp_list in comp_lists:
            comp_separator = comp_list.find("div", class_="comp_separator")
            if comp_separator:
                comp_title = comp_separator.find("h4", class_="title")
                comp_logo_img = comp_separator.find("img", class_="comp_logo")
                comp_id = comp_list.get("data-comp_id", "") or comp_list.get(
                    "data-compid", ""
                )
                competition = {
                    "id": comp_id,
                    "name": comp_title.text.strip() if comp_title else "",
                    "logo": self._resolve_img_src(comp_logo_img, base_url),
                }
            else:
                competition = None

            match_items = comp_list.find_all("li", class_="single_match")
            for match_el in match_items:
                try:
                    matches.append(
                        self.extract_match_data(match_el, competition, base_url)
                    )
                except Exception as e:
                    logger.exception("Failed to parse single match: %s", e)
                    # skip broken match items but continue
                    continue

        return matches

    def _resolve_img_src(self, img_tag, base_url=None):
        """Return the actual src for an <img>, handling data-src and relative URLs."""
        if not img_tag:
            return ""
        src = (
            img_tag.get("src")
            or img_tag.get("data-src")
            or img_tag.get("data-original")
            or ""
        )
        if base_url and src:
            src = urljoin(base_url, src)
        return src

    def extract_match_data(self, match_element, competition, base_url=None):
        # id and status fields
        match_id = (match_element.get("id") or "").replace("match_", "")
        view_status = match_element.get("data-view_status", "")
        is_live = match_element.get("data-is_live", "0") == "1"

        # status: handle hidden attribute which could be present as empty string or boolean attribute
        status_element = match_element.find("div", class_="match_status")
        status = ""
        match_minute = ""

        if status_element and not status_element.has_attr("hidden"):
            # there could be: <span>....<span dir="ltr">45'</span></span>
            status_span = status_element.find("span")
            if status_span:
                minute_span = status_span.find("span", {"dir": "ltr"})
                if minute_span and minute_span.text.strip():
                    match_minute = minute_span.text.strip()
                    status = "live"
                else:
                    # fallback to the text content of the status span
                    status_text = status_span.get_text(strip=True)
                    status = status_text
        else:
            if view_status == "done":
                status = "انتهت"
            elif view_status == "coming":
                status = "لم تبدأ"
            else:
                status = view_status or ""

        # home team
        home_div = match_element.find("div", class_="hometeam")
        if not home_div:
            raise ValueError("Home team not found for match id: %s" % match_id)

        home_name_el = home_div.find("span", class_="the_team")
        home_name = home_name_el.get_text(strip=True) if home_name_el else ""
        home_logo = self._resolve_img_src(
            home_div.find("img", class_="team_logo"), base_url
        )

        # away team
        away_div = match_element.find("div", class_="awayteam")
        if not away_div:
            raise ValueError("Away team not found for match id: %s" % match_id)

        away_name_el = away_div.find("span", class_="the_team")
        away_name = away_name_el.get_text(strip=True) if away_name_el else ""
        away_logo = self._resolve_img_src(
            away_div.find("img", class_="team_logo"), base_url
        )

        # score: sometimes they reuse classes that conflict with team containers; be defensive
        score_element = match_element.find("span", class_="match_score")
        home_score = None
        away_score = None
        if score_element and not score_element.has_attr("hidden"):
            # look for nested spans that have classes hometeam/awayteam or numeric text
            hs = score_element.find("span", class_="hometeam")
            as_ = score_element.find("span", class_="awayteam")
            if hs and hs.text.strip():
                home_score = hs.text.strip()
            if as_ and as_.text.strip():
                away_score = as_.text.strip()

            # fallback: direct numeric content
            if home_score is None or away_score is None:
                # split by '-' or ':' commonly used
                text = score_element.get_text(" ", strip=True)
                for sep in ("-", ":", "—"):
                    if sep in text:
                        parts = [p.strip() for p in text.split(sep) if p.strip()]
                        if len(parts) >= 2:
                            if home_score is None:
                                home_score = parts[0]
                            if away_score is None:
                                away_score = parts[1]
                            break
        # normalize score to string numbers (fallback to empty string if None)
        home_score = home_score if home_score is not None else ""
        away_score = away_score if away_score is not None else ""

        # match time
        time_element = match_element.find("div", class_="match_time")
        match_time = ""
        if time_element:
            otime = time_element.find("span", class_="the_otime")
            if otime and otime.text.strip():
                match_time = otime.text.strip()
            else:
                # fallback to any text inside time element
                match_time = time_element.get_text(strip=True)

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
