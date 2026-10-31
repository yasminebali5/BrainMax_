from django.urls import path
from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("dashboard/", views.workspace, {"page": "overview"}, name="dashboard"),
    path("profile/", views.workspace, {"page": "profile"}, name="learning_profile_page"),
    path("assessment/", views.workspace, {"page": "assessment"}, name="assessment"),
    path("learning-path/", views.workspace, {"page": "path"}, name="learning_path"),
    path("brain-twin/", views.workspace, {"page": "twin"}, name="brain_twin"),
    path("settings/", views.workspace, {"page": "settings"}, name="settings"),
    path("scoring/", views.workspace, {"page": "overview"}, name="scoring"),
    path("ai-coach/", views.workspace, {"page": "coach"}, name="coach"),
    path("adaptive-learning/", views.workspace, {"page": "path"}, name="adaptive"),
]
