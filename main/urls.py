from django .urls import path
from .views import MatchDataExtractor

urlpatterns = [
    path("api/matches/", MatchDataExtractor.as_view(), name="match_data"),
]
