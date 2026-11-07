from django.urls import path
from . import views

urlpatterns = [
    path('state/', views.state, name='learning_state'),
    path('memory/', views.memory_snapshot, name='learning_memory'),
    path('export/', views.export_data, name='learning_export'),
    path('profile/', views.profile, name='learning_profile'),
    path('assessment/', views.assessment, name='learning_assessment'),
    path('answer/', views.answer, name='learning_answer'),
    path('path/', views.learning_path, name='learning_path_progress'),
    path('definition/', views.definition, name='learning_definition'),
    path('hint/', views.hint, name='learning_hint'),
    path('reset/', views.reset, name='learning_reset'),
    path('preferences/', views.preferences, name='learning_preferences'),
    path('collaboration/', views.collaboration, name='learning_collaboration'),
    path('goal/', views.goal, name='learning_goal'),
    path('coach/', views.coach, name='learning_coach'),
    path('coach/learning-plan/', views.coach_learning_plan, name='coach_learning_plan'),
    path('coach/site-action/', views.coach_site_action, name='coach_site_action'),
    path('engagement/', views.engagement, name='learning_engagement'),
    path('pushback/retest/', views.pushback_retest, name='learning_pushback_retest'),
]
