from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("comptes/", include("accounts.urls")),
    path("", include("core.urls")),
    path("api/", include("learning.urls")),
]
