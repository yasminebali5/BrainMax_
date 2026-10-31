from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render


@login_required
def workspace(request, page="overview"):
    return render(request, "core/workspace.html", {"active": page, "page": page})


def home(request):
    return redirect("dashboard" if request.user.is_authenticated else "login")
