from django.core.management.base import BaseCommand
from accounts.models import User


class Command(BaseCommand):
    help = "Creates the BrainMax demo account if it does not exist."

    def handle(self, *args, **options):
        user, created = User.objects.get_or_create(
            email="demo@brainmax.app",
            defaults={"first_name": "Nora", "last_name": "Martin", "objectives": "Improve focus and maintain a consistent cognitive routine."},
        )
        if created:
            user.set_password("BrainMax2026!")
            user.save()
        self.stdout.write(self.style.SUCCESS("Demo account ready." if created else "Demo account already exists."))
