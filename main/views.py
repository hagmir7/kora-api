from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views import View
from bs4 import BeautifulSoup
import requests
import logging
from urllib.parse import urljoin
from rest_framework.generics import ListAPIView
import re
from functools import lru_cache
from datetime import datetime, timedelta
from django.db import IntegrityError, transaction
from rest_framework import viewsets, status, mixins, generics, permissions
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticatedOrReadOnly, IsAuthenticated, AllowAny
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter, OrderingFilter
from django_filters.rest_framework import DjangoFilterBackend
from . import models, serializers
from django.contrib.auth import get_user_model
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework_simplejwt.tokens import RefreshToken, TokenError
from django.db.models import Prefetch
from .models import Blog, Competition, Group, Season
from rest_framework.decorators import api_view
from .serializers import CompetitionSeasonsSerializer


from .serializers import (
    RegisterSerializer,
    LoginSerializer,
    UserSerializer,
    ChangePasswordSerializer,
    GroupWithTeamsSerializer,
    CompetitionSeasonTeamSerializer,
    CompetitionMatchSerializer,
    CompetitionPlayerSerializer,
    BlogListSerializer,
)


from rest_framework.pagination import PageNumberPagination


class BlogPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


User = get_user_model()


logger = logging.getLogger(__name__)


class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]


class LoginView(APIView):
    """
    Optional: you can use rest_framework_simplejwt.views.TokenObtainPairView instead.
    This view returns tokens and user info via LoginSerializer.
    """
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data, status=status.HTTP_200_OK)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        """
        Blacklist refresh token. Client should send {"refresh": "<refresh_token>"}.
        """
        refresh_token = request.data.get("refresh")
        if not refresh_token:
            return Response({"detail": "Refresh token required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
        except TokenError as e:
            return Response({"detail": "Invalid token."}, status=status.HTTP_400_BAD_REQUEST)

        return Response(status=status.HTTP_204_NO_CONTENT)


class ProfileView(generics.RetrieveUpdateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user


class ChangePasswordView(generics.UpdateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ChangePasswordSerializer

    def get_object(self):
        return self.request.user

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["user"] = self.request.user
        return context

    def update(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"detail": "Password updated successfully."})


# Simple in-memory cache
_cache = {}
CACHE_DURATION = 60  # seconds
import random

def send_safe_request(url, timeout=15, use_js=False):
    """
    Optimized request - JS rendering only when needed
    """
    SCRAPER_API_KEY = [
        "d0a075b5a0bcdefcaabdd16757067f0f",
        "2d118deea43615910a5ed5cc6f8f56fa",
        "4c64b63e0a8ebf500aaa60c2514e6e8f",
        "d0bae26ea5465f92fbe33e3fa3d2e850",
        "b9573123f02ebb9fdfcd6f4cace5fda5",
        "6c49e45c1661f242378ca489f92b6ede",
    ]

    # Check cache first
    cache_key = f"{url}_{use_js}"
    if cache_key in _cache:
        cached_data, cached_time = _cache[cache_key]
        if (datetime.now() - cached_time).seconds < CACHE_DURATION:
            logger.info(f"Cache hit for {url}")
            return cached_data

    api_url = "http://api.scraperapi.com"
    params = {
        "api_key":  random.choice(SCRAPER_API_KEY),
        "url": url,
        "render": "true" if use_js else "false",
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


# Generic base viewset that sets common filter backends and permission
class BaseModelViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticatedOrReadOnly]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    pagination_class = None 


class ContinentViewSet(BaseModelViewSet):
    queryset = models.Continent.objects.all()
    serializer_class = serializers.ContinentSerializer
    filterset_fields = ["name", "code"]
    search_fields = ["name", "code"]
    ordering_fields = ["name", "id"]


class CountryViewSet(BaseModelViewSet):
    queryset = models.Country.objects.select_related("continent").all()
    serializer_class = serializers.CountrySerializer
    filterset_fields = ["name", "code", "continent"]
    search_fields = ["name", "code"]
    ordering_fields = ["name", "id"]


class CityViewSet(BaseModelViewSet):
    queryset = models.City.objects.select_related("country").all()
    serializer_class = serializers.CitySerializer
    filterset_fields = ["name", "country"]
    search_fields = ["name"]
    ordering_fields = ["name", "id"]


class TeamViewSet(BaseModelViewSet):
    queryset = models.Team.objects.select_related("country", "city").all()
    serializer_class = serializers.TeamSerializer
    filterset_fields = ["country", "city", "code"]
    search_fields = ["name", "code"]
    ordering_fields = ["name", "id"]


class PlayerViewSet(BaseModelViewSet):
    queryset = models.Player.objects.select_related("nationality").all()
    serializer_class = serializers.PlayerSerializer
    filterset_fields = ["nationality", "position", "code"]
    search_fields = ["name", "arabic_name", "code"]
    ordering_fields = ["name", "id"]


class ContractViewSet(BaseModelViewSet):
    queryset = models.Contract.objects.select_related("player", "team").all()
    serializer_class = serializers.ContractSerializer
    filterset_fields = ["player", "team", "start_date", "end_date"]
    search_fields = ["player__name", "team__name"]
    ordering_fields = ["start_date", "end_date"]


class CompetitionViewSet(BaseModelViewSet):
    queryset = models.Competition.objects.select_related("country").all()
    serializer_class = serializers.CompetitionSerializer
    permission_classes = [AllowAny]
    filterset_fields = ["type", "country", "season"]
    search_fields = ["name", "title", "season"]
    ordering_fields = ["name", "season"]
    lookup_field = "slug"


class SeasonViewSet(BaseModelViewSet):
    queryset = models.Season.objects.select_related("competition").all()
    serializer_class = serializers.SeasonSerializer
    filterset_fields = ["competition", "name", "start_date", "end_date"]
    search_fields = ["name", "competition__name"]
    ordering_fields = ["start_date", "end_date"]


class GroupViewSet(BaseModelViewSet):
    queryset = (
        models.Group.objects.select_related("season", "competition") 
        .prefetch_related(
            Prefetch(
                "groupteam_set",
                queryset=models.GroupTeam.objects.select_related("team"),
                to_attr="group_teams",
            )
        )
        .all()
    )

    serializer_class = serializers.GroupSerializer
    filterset_fields = ["season", "competition"]
    search_fields = ["name"]
    ordering_fields = ["name", "id"]


class MatchViewSet(BaseModelViewSet):
    queryset = models.Match.objects.select_related("competition", "home_team", "away_team").all()
    serializer_class = serializers.MatchSerializer
    filterset_fields = ["competition", "home_team", "away_team", "date_time", "status"]
    search_fields = ["home_team__name", "away_team__name"]
    ordering_fields = ["date_time", "status"]

    lookup_field = "code"

    def perform_create(self, serializer):
        # Example: wrap save in a transaction to be safe if many related objects are touched
        with transaction.atomic():
            serializer.save()


class MatchEventViewSet(BaseModelViewSet):
    queryset = models.MatchEvent.objects.select_related("match", "player").all()
    serializer_class = serializers.MatchEventSerializer
    filterset_fields = ["match", "player", "event_type", "minute"]
    search_fields = ["match__home_team__name", "match__away_team__name"]
    ordering_fields = ["minute", "id"]


class SeasonTeamViewSet(BaseModelViewSet):
    queryset = models.SeasonTeam.objects.select_related("season", "team").all()
    serializer_class = serializers.SeasonTeamSerializer
    filterset_fields = ["season", "team"]
    search_fields = ["team__name"]
    ordering_fields = ["points"]


class SeasonMatchViewSet(BaseModelViewSet):
    queryset = models.SeasonMatch.objects.select_related("season", "match").all()
    serializer_class = serializers.SeasonMatchSerializer
    filterset_fields = ["season", "match", "round"]
    search_fields = ["match__home_team__name", "match__away_team__name"]
    ordering_fields = ["round"]


class SeasonPlayerViewSet(BaseModelViewSet):
    queryset = models.SeasonPlayer.objects.select_related("season", "player", "team").all()
    serializer_class = serializers.SeasonPlayerSerializer
    filterset_fields = ["season", "player", "team"]
    search_fields = ["player__name", "team__name"]
    ordering_fields = ["goals", "assists", "rating"]


class CategoryViewSet(BaseModelViewSet):
    queryset = models.Category.objects.all()
    serializer_class = serializers.CategorySerializer
    filterset_fields = ["name"]
    search_fields = ["name"]
    ordering_fields = ["name"]
    lookup_field = 'slug'


class BlogViewSet(BaseModelViewSet):
    queryset = (
        models.Blog.objects.select_related("team", "match", "category")
        .prefetch_related("comments")
        .all()
    )

    filterset_fields = ["team", "match", "category", "created_at"]
    search_fields = ["title", "description", "body", "tags"]
    ordering_fields = ["created_at", "title"]
    ordering = ["-created_at"]
    pagination_class = BlogPagination
    lookup_field = "slug"

    def get_serializer_class(self):
        if self.action == "list":
            return serializers.BlogListSerializer
        return serializers.BlogSerializer

    def perform_create(self, serializer):
        max_tries = 3
        for attempt in range(max_tries):
            try:
                with transaction.atomic():
                    return serializer.save()
            except IntegrityError:
                if attempt + 1 == max_tries:
                    raise
                continue


def popular_blogs(request):
    blogs = Blog.objects.all().order_by('-created_at')[:3]

    blogs_list = list(
        blogs.values("id", "title", "image_url", "description", "created_at", 'slug')
    )

    return JsonResponse(blogs_list, safe=False)


class NewsCommentViewSet(viewsets.ModelViewSet):
    """
    Allow anyone to create comments, but only authenticated users or staff to approve / delete.
    You can adjust permissions as needed (e.g., moderate comments automatically).
    """
    queryset = models.NewsComment.objects.select_related("blog", "parent").all()
    serializer_class = serializers.NewsCommentSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["blog", "user_name", "is_approved", "created_at"]
    search_fields = ["user_name", "content"]
    ordering_fields = ["created_at"]

    def get_permissions(self):
        # allow anonymous users to create comments but require auth for non-safe methods except create
        if self.action == "create":
            return [AllowAny()]
        return [IsAuthenticatedOrReadOnly()]

    def perform_create(self, serializer):
        # create comment (no extra behavior here) -- moderation flag defaults to False
        serializer.save()


@api_view(["GET"])
def competition_seasons(request, slug):
    competition = get_object_or_404(
        Competition.objects.prefetch_related("seasons"), slug=slug
    )
    serializer = CompetitionSeasonsSerializer(competition)
    return Response(serializer.data)


def normalize_season(value: str) -> str:
    """
    Accepts:
    - 2024-2025
    - 2024/2025
    Returns:
    - 2024/2025
    """
    return value.replace("-", "/")

from rest_framework.decorators import api_view, permission_classes

@api_view(["GET"])
@permission_classes([AllowAny])
def competition_groups_teams(request, slug):
    competition = get_object_or_404(Competition, slug=slug)

    season_param = request.GET.get("season")
    if not season_param:
        return Response(
            {"detail": "season query parameter is required"},
            status=400
        )

    season_name = normalize_season(season_param)

    season = get_object_or_404(
        Season,
        competition=competition,
        name=season_name
    )

    groups = (
        Group.objects
        .filter(competition=competition, season=season)
        .select_related("season")
        .prefetch_related("groupteam_set__team")
        .order_by("name")
    )

    serializer = GroupWithTeamsSerializer(groups, many=True)

    return Response({
        "competition": {
            "id": competition.id,
            "name": competition.name,
            "slug": competition.slug,
        },
        "season": {
            "id": season.id,
            "name": season.name,
        },
        "groups": serializer.data
    })


@api_view(["GET"])
def competition_teams(request, slug):
    competition = get_object_or_404(models.Competition, slug=slug)

    season_param = request.GET.get("season")
    if not season_param:
        return Response({"detail": "Season query parameter is required"}, status=400)

    season_name = normalize_season(season_param)

    season = get_object_or_404(models.Season, competition=competition, name=season_name)

    season_teams = (
        models.SeasonTeam.objects.filter(season=season)
        .select_related("team", "season")
        .order_by("-points", "-difference")
    )

    serializer = CompetitionSeasonTeamSerializer(
        season_teams, many=True, context={"request": request}
    )

    return Response(
        {
            "competition": {
                "id": competition.id,
                "name": competition.name,
                "slug": competition.slug,
            },
            "season": {
                "id": season.id,
                "name": season.name,
            },
            "teams": serializer.data,
        }
    )


@api_view(["GET"])
def competition_matches(request, slug):
    competition = get_object_or_404(models.Competition, slug=slug)

    season_param = request.GET.get("season")
    if not season_param:
        return Response({"detail": "Season query parameter is required"}, status=400)

    season_name = normalize_season(season_param)

    season = get_object_or_404(models.Season, competition=competition, name=season_name)

    matches = (
        models.Match.objects.filter(
            competition=competition, season_matches__season=season
        )
        .select_related("home_team", "away_team")
        .prefetch_related("season_matches")
        .order_by("season_matches__round", "date_time")
        .distinct()
    )

    serializer = CompetitionMatchSerializer(
        matches, many=True, context={"request": request}
    )

    return Response(
        {
            "competition": {
                "id": competition.id,
                "name": competition.name,
                "slug": competition.slug,
            },
            "season": {
                "id": season.id,
                "name": season.name,
            },
            "matches": serializer.data,
        }
    )


@api_view(["GET"])
def competition_players(request, slug):
    competition = get_object_or_404(models.Competition, slug=slug)

    season_param = request.GET.get("season")
    if not season_param:
        return Response({"detail": "Season query parameter is required"}, status=400)

    season_name = normalize_season(season_param)

    season = get_object_or_404(models.Season, competition=competition, name=season_name)

    players = (
        models.SeasonPlayer.objects.filter(season=season)
        .select_related("player", "team", "season")
        .order_by("-goals", "-assists", "-rating")
    )

    serializer = CompetitionPlayerSerializer(
        players, many=True, context={"request": request}
    )

    return Response(
        {
            "competition": {
                "id": competition.id,
                "name": competition.name,
                "slug": competition.slug,
            },
            "season": {
                "id": season.id,
                "name": season.name,
            },
            "players": serializer.data,
        }
    )


class CategoryBlogsView(ListAPIView):
    serializer_class = serializers.CategoryBlogSerializer
    pagination_class = BlogPagination

    def get_category(self):
        return get_object_or_404(models.Category, slug=self.kwargs["slug"])

    def get_queryset(self):
        category = self.get_category()
        return (
            models.Blog.objects.filter(category=category)
            .select_related("category")
            .order_by("-created_at")
        )

    def list(self, request, *args, **kwargs):
        category = self.get_category()

        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)

        serializer = self.get_serializer(page, many=True)

        return self.get_paginated_response(
            {
                "category": {
                    "id": category.id,
                    "name": category.name,
                    "slug": category.slug,
                },
                "blogs": serializer.data,
            }
        )

from rest_framework.generics import ListCreateAPIView


class BlogCommentsView(ListAPIView):
    serializer_class = serializers.NewsCommentReadSerializer
    permission_classes = []
    pagination_class = BlogPagination

    def get_queryset(self):
        blog = get_object_or_404(Blog, slug=self.kwargs["slug"])

        return (
            models.NewsComment.objects.filter(blog=blog, parent__isnull=True, is_approved=True)
            .prefetch_related("replies")
            .order_by("-created_at")
        )
