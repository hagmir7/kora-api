from .views import MatchDataExtractor
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views
from .scrap_views.competition import ScrapeCompetitionsView
from .scrap_views.competitions_teams import scrape_competition_teams
from .scrap_views.competitions_teams_group import scrape_groups_competition
from .scrap_views.competitions_matches import scrape_competition_matches
from .scrap_views.competition_players import scrape_competition_players

# Create router
router = DefaultRouter(trailing_slash=False)

router.register("continents", views.ContinentViewSet)
router.register("countries", views.CountryViewSet)
router.register("cities", views.CityViewSet)
router.register("teams", views.TeamViewSet)
router.register("players", views.PlayerViewSet)
router.register("contracts", views.ContractViewSet)
router.register("competitions", views.CompetitionViewSet)
router.register("seasons", views.SeasonViewSet)
router.register("groups", views.GroupViewSet)
router.register("matches", views.MatchViewSet)
router.register("match-events", views.MatchEventViewSet)
router.register("season-teams", views.SeasonTeamViewSet)
router.register("season-matches", views.SeasonMatchViewSet)
router.register("season-players", views.SeasonPlayerViewSet)
router.register("categories", views.CategoryViewSet)
router.register("blogs", views.BlogViewSet)
router.register("comments", views.NewsCommentViewSet)


urlpatterns = [
    path("matches/", MatchDataExtractor.as_view(), name="match_data"),
    path("", include(router.urls)),
    path("auth/register", views.RegisterView.as_view(), name="auth-register"),
    path("auth/login", views.LoginView.as_view(), name="auth-login"),
    # SimpleJWT built-in token endpoints (recommended)
    path("auth/token", views.TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("auth/token/refresh", views.TokenRefreshView.as_view(), name="token_refresh"),
    path("auth/logout", views.LogoutView.as_view(), name="auth-logout"),
    path("auth/me", views.ProfileView.as_view(), name="auth-me"),
    path("popular-blogs", views.popular_blogs),
    path(
        "auth/change-password",
        views.ChangePasswordView.as_view(),
        name="auth-change-password",
    ),
    path(
        "scrape-competitions",
        ScrapeCompetitionsView.as_view(),
        name="scrape_competitions",
    ),
    path("scrape_competition_teams", scrape_competition_teams),
    path("scrape_groups_competition", scrape_groups_competition),
    path("scrape_matches_competition", scrape_competition_matches),
    path("scrape_competition_players", scrape_competition_players),
]
