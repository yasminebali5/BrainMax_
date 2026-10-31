from django.contrib.auth.views import LogoutView
from django.urls import path
from .views import BrainMaxLoginView, profile, signup

urlpatterns = [
    path("login/", BrainMaxLoginView.as_view(), name="login"),
    path("signup/", signup, name="signup"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("profile/", profile, name="profile"),
    # Legacy URL aliases for existing browser tabs / bookmarks
    path("connexion/", BrainMaxLoginView.as_view()),
    path("inscription/", signup),
    path("deconnexion/", LogoutView.as_view()),
    path("profil/", profile),
]
