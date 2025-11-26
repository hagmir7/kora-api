# yourapp/serializers.py
from rest_framework import serializers
from django.utils.text import slugify
from django.core.exceptions import ValidationError
from . import models

# yourapp/serializers_auth.py
from django.contrib.auth import get_user_model, password_validation, authenticate
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    """Public user representation"""

    class Meta:
        model = User
        # adjust fields according to your custom user model
        fields = (
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
        )
        read_only_fields = ("id",)


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True, required=True, style={"input_type": "password"}
    )
    password2 = serializers.CharField(
        write_only=True, required=True, style={"input_type": "password"}
    )

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "password",
            "password2",
            "first_name",
            "last_name",
        )
        extra_kwargs = {"email": {"required": True}}

    def validate(self, attrs):
        if attrs["password"] != attrs["password2"]:
            raise serializers.ValidationError(
                {"password": "Password fields didn't match."}
            )
        # enforce Django password validators
        password_validation.validate_password(attrs["password"], self.instance)
        return attrs

    def create(self, validated_data):
        validated_data.pop("password2", None)
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user

    def to_representation(self, instance):
        # Return created user + tokens
        data = UserSerializer(instance).data
        refresh = RefreshToken.for_user(instance)
        data.update({"access": str(refresh.access_token), "refresh": str(refresh)})
        return data


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(write_only=True)
    password = serializers.CharField(write_only=True, style={"input_type": "password"})
    access = serializers.CharField(read_only=True)
    refresh = serializers.CharField(read_only=True)
    user = UserSerializer(read_only=True)

    def validate(self, attrs):
        username = attrs.get("username")
        password = attrs.get("password")
        user = authenticate(username=username, password=password)
        if not user:
            raise serializers.ValidationError(
                "Unable to log in with provided credentials."
            )
        if not user.is_active:
            raise serializers.ValidationError("User account is disabled.")
        refresh = RefreshToken.for_user(user)
        return {
            "user": user,
            "access": str(refresh.access_token),
            "refresh": str(refresh),
        }


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(
        write_only=True, required=True, style={"input_type": "password"}
    )
    new_password = serializers.CharField(
        write_only=True, required=True, style={"input_type": "password"}
    )

    def validate_new_password(self, value):
        password_validation.validate_password(value, self.context.get("user"))
        return value

    def validate(self, attrs):
        user = self.context.get("user")
        if not user.check_password(attrs.get("old_password")):
            raise serializers.ValidationError({"old_password": "Wrong password."})
        return attrs

    def save(self, **kwargs):
        user = self.context.get("user")
        new_password = self.validated_data["new_password"]
        user.set_password(new_password)
        user.save()
        return user


#
# Helper fields / small utilities
#
class TagListField(serializers.Field):
    """
    Represent tags stored as a single CharField (comma-separated) as a list in the API.
    - to_representation: "a,b,c" -> ["a","b","c"]
    - to_internal_value: ["a","b"] -> "a,b"
    """
    def to_representation(self, value):
        if not value:
            return []
        return [tag.strip() for tag in value.split(",") if tag.strip()]

    def to_internal_value(self, data):
        if data is None:
            return ""
        if isinstance(data, list):
            cleaned = [str(t).strip() for t in data if str(t).strip()]
            return ",".join(cleaned)
        if isinstance(data, str):
            return ",".join([t.strip() for t in data.split(",") if t.strip()])
        raise serializers.ValidationError("Tags must be a list of strings or a comma-separated string.")


#
# Basic location serializers
#
class ContinentSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Continent
        fields = ["id", "name", "code"]


class CountrySerializer(serializers.ModelSerializer):
    continent = ContinentSerializer(read_only=True)
    continent_id = serializers.PrimaryKeyRelatedField(
        queryset=models.Continent.objects.all(), write_only=True, source="continent", required=False, allow_null=True
    )

    class Meta:
        model = models.Country
        fields = ["id", "name", "code", "continent", "continent_id"]


class CitySerializer(serializers.ModelSerializer):
    country = CountrySerializer(read_only=True)
    country_id = serializers.PrimaryKeyRelatedField(
        queryset=models.Country.objects.all(), write_only=True, source="country"
    )

    class Meta:
        model = models.City
        fields = ["id", "name", "country", "country_id"]


#
# Core domain serializers
#
class TeamSerializer(serializers.ModelSerializer):
    country = CountrySerializer(read_only=True)
    country_id = serializers.PrimaryKeyRelatedField(
        queryset=models.Country.objects.all(), write_only=True, source="country", required=False, allow_null=True
    )
    city = CitySerializer(read_only=True)
    city_id = serializers.PrimaryKeyRelatedField(
        queryset=models.City.objects.all(), write_only=True, source="city", required=False, allow_null=True
    )
    slug = serializers.ReadOnlyField()

    class Meta:
        model = models.Team
        fields = [
            "id", "name", "slug", "country", "country_id", "city", "city_id",
            "logo_url", "description", "code", "body"
        ]


class PlayerSerializer(serializers.ModelSerializer):
    nationality = CountrySerializer(read_only=True)
    nationality_id = serializers.PrimaryKeyRelatedField(
        queryset=models.Country.objects.all(), write_only=True, source="nationality", required=False, allow_null=True
    )
    slug = serializers.ReadOnlyField()

    class Meta:
        model = models.Player
        fields = [
            "id", "name", "arabic_name", "slug", "code", "dob",
            "nationality", "nationality_id", "position", "photo_url",
            "description", "body"
        ]


class ContractSerializer(serializers.ModelSerializer):
    player = PlayerSerializer(read_only=True)
    player_id = serializers.PrimaryKeyRelatedField(
        queryset=models.Player.objects.all(), write_only=True, source="player"
    )
    team = TeamSerializer(read_only=True)
    team_id = serializers.PrimaryKeyRelatedField(
        queryset=models.Team.objects.all(), write_only=True, source="team"
    )

    class Meta:
        model = models.Contract
        fields = ["id", "player", "player_id", "team", "team_id", "start_date", "end_date", "transfer_fee"]

    def validate(self, data):
        # data may contain "player" and "team" under their source keys
        start = data.get("start_date") or getattr(self.instance, "start_date", None)
        end = data.get("end_date") or getattr(self.instance, "end_date", None)
        if start and end and start > end:
            raise serializers.ValidationError("start_date must be before end_date.")
        return data


class CompetitionSerializer(serializers.ModelSerializer):
    country = CountrySerializer(read_only=True)
    country_id = serializers.PrimaryKeyRelatedField(
        queryset=models.Country.objects.all(), write_only=True, source="country", required=False, allow_null=True
    )
    slug = serializers.ReadOnlyField()

    class Meta:
        model = models.Competition
        fields = [
            "id", "name", "title", "slug", "country", "country_id",
            "type", "season", "description", "code", "body"
        ]


class SeasonSerializer(serializers.ModelSerializer):
    competition = CompetitionSerializer(read_only=True)
    competition_id = serializers.PrimaryKeyRelatedField(
        queryset=models.Competition.objects.all(), write_only=True, source="competition"
    )

    class Meta:
        model = models.Season
        fields = ["id", "name", "competition", "competition_id", "start_date", "end_date"]

    def validate(self, data):
        start = data.get("start_date") or getattr(self.instance, "start_date", None)
        end = data.get("end_date") or getattr(self.instance, "end_date", None)
        if start and end and start > end:
            raise serializers.ValidationError("Season start_date must be before end_date.")
        return data


class GroupSerializer(serializers.ModelSerializer):
    season = SeasonSerializer(read_only=True)
    season_id = serializers.PrimaryKeyRelatedField(
        queryset=models.Season.objects.all(), write_only=True, source="season"
    )
    competition = CompetitionSerializer(read_only=True)
    competition_id = serializers.PrimaryKeyRelatedField(
        queryset=models.Competition.objects.all(), write_only=True, source="competition"
    )
    teams = TeamSerializer(many=True, read_only=True)
    team_ids = serializers.PrimaryKeyRelatedField(
        queryset=models.Team.objects.all(), many=True, write_only=True, source="teams"
    )

    class Meta:
        model = models.Group
        fields = ["id", "name", "season", "season_id", "competition", "competition_id", "teams", "team_ids", "description"]

    def create(self, validated_data):
        teams = validated_data.pop("teams", [])
        group = super().create(validated_data)
        if teams:
            group.teams.set(teams)
        return group

    def update(self, instance, validated_data):
        teams = validated_data.pop("teams", None)
        instance = super().update(instance, validated_data)
        if teams is not None:
            instance.teams.set(teams)
        return instance


class MatchSerializer(serializers.ModelSerializer):
    competition = CompetitionSerializer(read_only=True)
    competition_id = serializers.PrimaryKeyRelatedField(
        queryset=models.Competition.objects.all(), write_only=True, source="competition"
    )
    home_team = TeamSerializer(read_only=True)
    home_team_id = serializers.PrimaryKeyRelatedField(
        queryset=models.Team.objects.all(), write_only=True, source="home_team"
    )
    away_team = TeamSerializer(read_only=True)
    away_team_id = serializers.PrimaryKeyRelatedField(
        queryset=models.Team.objects.all(), write_only=True, source="away_team"
    )

    class Meta:
        model = models.Match
        fields = [
            "id", "competition", "competition_id", "home_team", "home_team_id",
            "away_team", "away_team_id", "date_time", "venue",
            "home_score", "away_score", "code", "status"
        ]

    def validate(self, data):
        # ensure home and away teams are not the same (if provided)
        home = data.get("home_team") or getattr(self.instance, "home_team", None)
        away = data.get("away_team") or getattr(self.instance, "away_team", None)
        if home and away and home == away:
            raise serializers.ValidationError("home_team and away_team must be different.")
        return data


class MatchEventSerializer(serializers.ModelSerializer):
    match = MatchSerializer(read_only=True)
    match_id = serializers.PrimaryKeyRelatedField(
        queryset=models.Match.objects.all(), write_only=True, source="match"
    )
    player = PlayerSerializer(read_only=True)
    player_id = serializers.PrimaryKeyRelatedField(
        queryset=models.Player.objects.all(), write_only=True, source="player", required=False, allow_null=True
    )

    class Meta:
        model = models.MatchEvent
        fields = ["id", "match", "match_id", "player", "player_id", "event_type", "minute"]


class SeasonTeamSerializer(serializers.ModelSerializer):
    season = SeasonSerializer(read_only=True)
    season_id = serializers.PrimaryKeyRelatedField(
        queryset=models.Season.objects.all(), write_only=True, source="season"
    )
    team = TeamSerializer(read_only=True)
    team_id = serializers.PrimaryKeyRelatedField(
        queryset=models.Team.objects.all(), write_only=True, source="team"
    )

    class Meta:
        model = models.SeasonTeam
        fields = ["id", "season", "season_id", "team", "team_id", "points", "position"]


class SeasonMatchSerializer(serializers.ModelSerializer):
    season = SeasonSerializer(read_only=True)
    season_id = serializers.PrimaryKeyRelatedField(
        queryset=models.Season.objects.all(), write_only=True, source="season"
    )
    match = MatchSerializer(read_only=True)
    match_id = serializers.PrimaryKeyRelatedField(
        queryset=models.Match.objects.all(), write_only=True, source="match"
    )

    class Meta:
        model = models.SeasonMatch
        fields = ["id", "season", "season_id", "match", "match_id", "round"]


class SeasonPlayerSerializer(serializers.ModelSerializer):
    season = SeasonSerializer(read_only=True)
    season_id = serializers.PrimaryKeyRelatedField(
        queryset=models.Season.objects.all(), write_only=True, source="season"
    )
    player = PlayerSerializer(read_only=True)
    player_id = serializers.PrimaryKeyRelatedField(
        queryset=models.Player.objects.all(), write_only=True, source="player"
    )
    team = TeamSerializer(read_only=True)
    team_id = serializers.PrimaryKeyRelatedField(
        queryset=models.Team.objects.all(), write_only=True, source="team"
    )

    class Meta:
        model = models.SeasonPlayer
        fields = [
            "id", "season", "season_id", "player", "player_id", "team", "team_id",
            "goals", "assists", "yellow_cards", "red_cards", "rating"
        ]


class CategorySerializer(serializers.ModelSerializer):
    slug = serializers.ReadOnlyField()

    class Meta:
        model = models.Category
        fields = ["id", "name", "slug"]


class NewsCommentSerializer(serializers.ModelSerializer):
    parent = serializers.PrimaryKeyRelatedField(queryset=models.NewsComment.objects.all(), required=False, allow_null=True)
    created_at = serializers.ReadOnlyField()
    updated_at = serializers.ReadOnlyField()

    class Meta:
        model = models.NewsComment
        fields = ["id", "blog", "user_name", "user_email", "content", "parent", "is_approved", "created_at", "updated_at"]


class BlogSerializer(serializers.ModelSerializer):
    team = TeamSerializer(read_only=True)
    team_id = serializers.PrimaryKeyRelatedField(
        queryset=models.Team.objects.all(), write_only=True, source="team", required=False, allow_null=True
    )
    match = MatchSerializer(read_only=True)
    match_id = serializers.PrimaryKeyRelatedField(
        queryset=models.Match.objects.all(), write_only=True, source="match", required=False, allow_null=True
    )
    category = CategorySerializer(read_only=True)
    category_id = serializers.PrimaryKeyRelatedField(
        queryset=models.Category.objects.all(), write_only=True, source="category"
    )
    tags = TagListField()
    slug = serializers.ReadOnlyField()
    comments = NewsCommentSerializer(many=True, read_only=True)

    class Meta:
        model = models.Blog
        fields = [
            "id", "title", "slug", "team", "team_id", "match", "match_id",
            "image_url", "description", "tags", "body", "created_at", "category", "category_id", "comments"
        ]
        read_only_fields = ["created_at", "slug", "comments"]

    def create(self, validated_data):
        # tags is handled by TagListField -> will be the CSV string expected by model
        return super().create(validated_data)

    def update(self, instance, validated_data):
        return super().update(instance, validated_data)
