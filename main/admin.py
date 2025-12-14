from django.contrib import admin
from .models import *

admin.site.register(Blog)


admin.site.register(Category)
admin.site.register(Competition)
admin.site.register(Team)
admin.site.register(Season)
admin.site.register(SeasonTeam)

admin.site.register(Group)

admin.site.register(GroupTeam)
admin.site.register(Match)
admin.site.register(SeasonMatch)
admin.site.register(Player)
admin.site.register(SeasonPlayer)
