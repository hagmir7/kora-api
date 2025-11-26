from .views import MatchDataExtractor
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

# Create router
router = DefaultRouter(trailing_slash=False)

router.register("api/continents", views.ContinentViewSet)
router.register("api/countries", views.CountryViewSet)
router.register("api/cities", views.CityViewSet)
router.register("api/teams", views.TeamViewSet)
router.register("api/players", views.PlayerViewSet)
router.register("api/contracts", views.ContractViewSet)
router.register("api/competitions", views.CompetitionViewSet)
router.register("api/seasons", views.SeasonViewSet)
router.register("api/groups", views.GroupViewSet)
router.register("api/matches", views.MatchViewSet)
router.register("api/match-events", views.MatchEventViewSet)
router.register("api/season-teams", views.SeasonTeamViewSet)
router.register("api/season-matches", views.SeasonMatchViewSet)
router.register("api/season-players", views.SeasonPlayerViewSet)
router.register("api/categories", views.CategoryViewSet)
router.register("api/blogs", views.BlogViewSet)
router.register("api/comments", views.NewsCommentViewSet)



urlpatterns = [
    path("api/matches/", MatchDataExtractor.as_view(), name="match_data"),
    path("", include(router.urls)),

    path('api/auth/register', views.RegisterView.as_view(), name='auth-register'),
    path('api/auth/login', views.LoginView.as_view(), name='auth-login'),
    # SimpleJWT built-in token endpoints (recommended)
    path('api/auth/token', views.TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/auth/token/refresh', views.TokenRefreshView.as_view(), name='token_refresh'),

    path('api/auth/logout', views.LogoutView.as_view(), name='auth-logout'),
    path('api/auth/me', views.ProfileView.as_view(), name='auth-me'),
    path('api/auth/change-password', views.ChangePasswordView.as_view(), name='auth-change-password'),
]
