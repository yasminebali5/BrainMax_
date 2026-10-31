from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render
from .forms import EmailLoginForm, ProfileForm, SignUpForm
from datetime import date

class BrainMaxLoginView(LoginView):
    template_name = "registration/login.html"
    authentication_form = EmailLoginForm
    redirect_authenticated_user = True


def signup(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = SignUpForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user, backend="accounts.backends.EmailBackend")
        messages.success(request, "Welcome to BrainMax! Your workspace is ready.")
        return redirect("dashboard")
    return render(request, "registration/signup.html", {"form": form})


@login_required
def profile(request):
    form = ProfileForm(request.POST or None, instance=request.user)

    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Your profile has been updated.")
        return redirect("profile")

    age = None

    if request.user.birth_date:
        today = date.today()
        birth_date = request.user.birth_date

        age = today.year - birth_date.year

        if (today.month, today.day) < (birth_date.month, birth_date.day):
            age -= 1

    return render(
        request,
        "core/profile.html",
        {
            "form": form,
            "age": age,
            "active": "profile",
        }
    )
