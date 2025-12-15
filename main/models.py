from django.db import models
from slugify import slugify
from django.core.exceptions import ValidationError


def generate_unique_slug(instance, field_name="title", slug_field="slug"):
    """
    Generates a unique slug for any model instance.

    Args:
        instance: The model instance.
        field_name: The name of the field to base the slug on (default 'title').
        slug_field: The name of the slug field (default 'slug').

    Returns:
        A unique slug string.
    """
    base_slug = slugify(getattr(instance, field_name))
    slug = base_slug
    ModelClass = instance.__class__
    counter = 1

    # Check for duplicates
    while ModelClass.objects.filter(**{slug_field: slug}).exists():
        slug = f"{base_slug}-{counter}"
        counter += 1

    return slug


class Continent(models.Model):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=10, blank=True, null=True)

    def __str__(self):
        return self.name


class Country(models.Model):
    name = models.CharField(max_length=100)
    continent = models.ForeignKey(Continent, on_delete=models.CASCADE, related_name='countries')
    code = models.CharField(max_length=10, blank=True, null=True)

    def __str__(self):
        return self.name


class City(models.Model):
    name = models.CharField(max_length=100)
    country = models.ForeignKey(Country, on_delete=models.CASCADE, related_name='cities')

    def __str__(self):
        return self.name


class Team(models.Model):
    name = models.CharField(max_length=100, blank=True, null=True)
    arabic_name = models.CharField(max_length=100)
    country = models.ForeignKey(Country, on_delete=models.SET_NULL, null=True, related_name='teams')
    city = models.ForeignKey(City, on_delete=models.SET_NULL, null=True, related_name='teams')
    logo_url = models.ImageField(upload_to='teams/', blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    code = models.CharField(max_length=10, blank=True, null=True)
    body = models.TextField(blank=True, null=True)
    slug = models.SlugField(max_length=200, unique=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = generate_unique_slug(self, field_name='arabic_name', slug_field='slug')
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Player(models.Model):
    POSITION_CHOICES = [
        ("GK", "حارس مرمى"),
        ("DF", "مدافع"),
        ("MF", "لاعب وسط"),
        ("FW", "مهاجم"),
    ]

    name = models.CharField(max_length=100, blank=True, null=True)
    arabic_name = models.CharField(max_length=100)
    code = models.CharField(max_length=10, blank=True, null=True)
    dob = models.DateField(blank=True, null=True)
    nationality = models.ForeignKey(Country, on_delete=models.SET_NULL, null=True, related_name='players')
    position = models.CharField(max_length=2, choices=POSITION_CHOICES)
    photo_url = models.ImageField(upload_to='players/', blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    body = models.TextField(blank=True, null=True)
    slug = models.SlugField(max_length=200, unique=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = generate_unique_slug(self, field_name='arabic_name', slug_field='slug')
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Contract(models.Model):
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="contracts")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="contracts")
    start_date = models.DateField()
    end_date = models.DateField()
    transfer_fee = models.DecimalField(max_digits=12, decimal_places=2)

    def clean(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValidationError("Contract start_date must be before end_date.")

    def __str__(self):
        return f"{self.player} -> {self.team} ({self.start_date} - {self.end_date})"


class Competition(models.Model):
    COMP_TYPE_CHOICES = [
        ('League', 'دوري'),
        ('Cup', 'كأس'),
        ('International', 'دولي'),
        ('Friendly', 'ودية'),
        ('SuperCup', 'كأس السوبر'),
        ('Qualifier', 'تصفيات'),
        ('Playoff', 'ملحق'),
        ('Tournament', 'بطولة'),
        ('Continental', 'قاري'),
        ('Regional', 'إقليمي'),
    ]
    name = models.CharField(max_length=100)
    title = models.CharField(max_length=100, null=False, blank=True)
    country = models.ForeignKey(Country, on_delete=models.SET_NULL, null=True, blank=True, related_name='competitions')
    type = models.CharField(max_length=20, choices=COMP_TYPE_CHOICES)
    season = models.CharField(max_length=50)
    description = models.TextField(blank=True, null=True)
    code = models.CharField(max_length=10, blank=True, null=True)
    logo = models.ImageField(upload_to="competitions/", blank=True, null=True)
    body = models.TextField(blank=True, null=True)
    slug = models.SlugField(max_length=200, unique=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = generate_unique_slug(self, field_name="name", slug_field="slug")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.season})"


class Season(models.Model):
    name = models.CharField(max_length=100)
    competition = models.ForeignKey(Competition, on_delete=models.CASCADE, related_name='seasons')
    start_date = models.DateField()
    end_date = models.DateField()
    url = models.URLField(max_length=500, blank=True, null=True)

    def clean(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValidationError("Season start_date must be before end_date.")

    def __str__(self):
        return f"{self.competition.name} - {self.name}"

    class Meta:
        ordering = ["-start_date"]
        unique_together = ["name", "competition", "url"]


class Group(models.Model):
    name = models.CharField(max_length=100)
    season = models.ForeignKey(Season, on_delete=models.CASCADE)
    competition = models.ForeignKey(Competition, on_delete=models.CASCADE)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name


class GroupTeam(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE)
    group = models.ForeignKey(Group, on_delete=models.CASCADE)
    played = models.IntegerField(default=0)
    won = models.IntegerField(default=0)
    draw = models.IntegerField(default=0)
    lost = models.IntegerField(default=0)
    against = models.CharField(max_length=10, null=True, blank=True)
    difference = models.IntegerField(default=0)
    points = models.IntegerField(default=0)
    hero = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.team.name} {self.group.name}"


class SeasonTeam(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE)
    season = models.ForeignKey(Season, on_delete=models.CASCADE)
    played = models.IntegerField(default=0)
    won = models.IntegerField(default=0)
    draw = models.IntegerField(default=0)
    lost = models.IntegerField(default=0)
    against = models.CharField(max_length=10, null=True, blank=True)
    difference = models.IntegerField(default=0)
    points = models.IntegerField(default=0)
    hero = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.team.name} - {self.season.name} {'🏆' if self.hero else ''}"


class Match(models.Model):
    competition = models.ForeignKey(Competition, on_delete=models.CASCADE, related_name='matches')
    home_team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='home_matches')
    away_team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='away_matches')
    date_time = models.DateTimeField()
    venue = models.CharField(max_length=255, blank=True, null=True)
    home_score = models.IntegerField(default=0)
    away_score = models.IntegerField(default=0)
    round = models.IntegerField(null=True, blank=True)
    code = models.CharField(max_length=10, blank=True, null=True)

    STATUS_CHOICES = [
        ('upcoming', 'قادم'),
        ('live', 'مباشر'),
        ('finished', 'منتهي'),
    ]
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='upcoming')

    def __str__(self):
        return f"{self.home_team} vs {self.away_team}"


EVENT_CHOICES = [
    ('goal', 'هدف'),
    ('assist', 'تمريرة حاسمة'),
    ('yellow_card', 'بطاقة صفراء'),
    ('red_card', 'بطاقة حمراء'),
    ('sub_in', 'تبديل دخول'),
    ('sub_out', 'تبديل خروج'),
    ('penalty_scored', 'ركلة جزاء مسجلة'),
    ('penalty_missed', 'ركلة جزاء ضائعة'),
    ('penalty_saved', 'ركلة جزاء تصدى لها'),
    ('own_goal', 'هدف عكسي'),
    ('free_kick_goal', 'هدف من ركلة حرة'),
    ('var_check', 'مراجعة VAR'),
    ('var_decision', 'قرار VAR'),
    ('foul', 'خطأ'),
    ('offside', 'تسلل'),
    ('injury', 'إصابة'),
    ('foul_drawn', 'الحصول على خطأ'),
    ('shot_on_target', 'تسديدة على المرمى'),
    ('shot_off_target', 'تسديدة خارج المرمى'),
    ('corner', 'ركلة ركنية'),
    ('goal_kick', 'ركلة مرمى'),
    ('save', 'تصدي'),
    ('tackle', 'افتكاك كرة'),
    ('clearance', 'إبعاد كرة'),
    ('block', 'تصدي للتسديدة'),
]


class MatchEvent(models.Model):
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="events")
    player = models.ForeignKey(Player, on_delete=models.SET_NULL, null=True, blank=True, related_name='events')
    event_type = models.CharField(max_length=30, choices=EVENT_CHOICES)
    minute = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.get_event_type_display()} - {self.match} ({self.minute}')"


class SeasonMatch(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name='season_matches')
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name='season_matches')
    round = models.IntegerField(blank=True, null=True)

    class Meta:
        unique_together = ('season', 'match')

    def __str__(self):
        return f"{self.match} in {self.season}"


class SeasonPlayer(models.Model):
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name='season_players')
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name='season_players')
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='season_players')
    goals = models.IntegerField(default=0)
    assists = models.IntegerField(default=0)
    yellow_cards = models.IntegerField(default=0)
    red_cards = models.IntegerField(default=0)
    rating = models.FloatField(default=0.0)

    def __str__(self):
        return f"{self.player} in {self.season}"


class Category(models.Model):
    name = models.CharField(max_length=50, unique=True)
    slug = models.SlugField(max_length=200, unique=True, blank=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = generate_unique_slug(self, field_name='name', slug_field='slug')
        super().save(*args, **kwargs)


class Blog(models.Model):
    title = models.CharField(max_length=250)
    team = models.ForeignKey(Team,  on_delete=models.SET_NULL, null=True, blank=True, related_name='blogs')
    match = models.ForeignKey(Match,  on_delete=models.SET_NULL, null=True, blank=True, related_name='blogs')
    image_url = models.ImageField(upload_to='blogs/', blank=True, null=True)
    description = models.TextField()
    tags = models.CharField(max_length=200)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    slug = models.SlugField(max_length=200, unique=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = generate_unique_slug(self, field_name='title', slug_field='slug')
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title


class NewsComment(models.Model):
    blog = models.ForeignKey("Blog", on_delete=models.CASCADE, related_name="comments")
    user_name = models.CharField(max_length=100)
    user_email = models.EmailField(blank=True, null=True)
    content = models.TextField() 
    parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True, related_name="replies")
    is_approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        if self.parent:
            return f"Reply by {self.user_name} on {self.blog.title}"
        return f"Comment by {self.user_name} on {self.blog.title}"

