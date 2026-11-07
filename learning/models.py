from django.db import models
from django.conf import settings


class Learner(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    profile = models.JSONField(default=dict)
    preferences = models.JSONField(default=dict)
    goal = models.JSONField(default=dict)


class Assessment(models.Model):
    learner = models.ForeignKey(Learner, on_delete=models.CASCADE)
    subject = models.CharField(max_length=40)
    questions = models.JSONField(default=list)
    started_at = models.DateTimeField(auto_now_add=True)
    question_started_at = models.DateTimeField(null=True)
    answers = models.JSONField(default=list)
    engagement = models.JSONField(default=dict)
    hint_used = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True)
    abandoned_at = models.DateTimeField(null=True)


class LearningPathRun(models.Model):
    learner = models.ForeignKey(Learner, on_delete=models.CASCADE)
    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE)
    steps = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('learner', 'assessment'),
                name='unique_learning_path_run',
            ),
        ]
