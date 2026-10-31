from django.test import TestCase
from django.urls import reverse
from .models import User


class AuthenticationTests(TestCase):
    def test_signup_creates_user_and_logs_in(self):
        response = self.client.post(reverse("signup"), {
            "first_name": "Nora", "last_name": "Martin", "email": "NORA@example.com",
            "objectives": "Improve my focus", "password1": "Cerveau-Solide-2026",
            "password2": "Cerveau-Solide-2026",
        })
        self.assertRedirects(response, reverse("dashboard"))
        self.assertTrue(User.objects.filter(email="nora@example.com").exists())

    def test_email_login_is_case_insensitive(self):
        User.objects.create_user(email="nora@example.com", password="Cerveau-Solide-2026")
        response = self.client.post(reverse("login"), {"username": "NORA@example.com", "password": "Cerveau-Solide-2026"})
        self.assertRedirects(response, reverse("dashboard"))

    def test_dashboard_requires_authentication(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_duplicate_email_is_rejected(self):
        User.objects.create_user(email="nora@example.com", password="Cerveau-Solide-2026")
        response = self.client.post(reverse("signup"), {
            "first_name": "Nora", "last_name": "Martin", "email": "nora@example.com",
            "password1": "Cerveau-Solide-2026", "password2": "Cerveau-Solide-2026",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "An account already exists with this email address.")
