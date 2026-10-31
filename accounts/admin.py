from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User


@admin.register(User)
class BrainMaxUserAdmin(UserAdmin):
    ordering = ("email",)

    list_display = (
        "email",
        "first_name",
        "last_name",
        "birth_date",
        "is_staff",
    )

    search_fields = (
        "email",
        "first_name",
        "last_name",
    )

    fieldsets = UserAdmin.fieldsets + (
        (
            "BrainMax",
            {
                "fields": (
                    "birth_date",
                    "objectives",
                )
            },
        ),
    )

    add_fieldsets = UserAdmin.add_fieldsets + (
        (
            "BrainMax",
            {
                "fields": (
                    "email",
                    "birth_date",
                    "objectives",
                )
            },
        ),
    )