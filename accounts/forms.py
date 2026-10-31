from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from .models import User


class StyledFormMixin:
    def _style(self):
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")
            field.widget.attrs.setdefault("autocomplete", "off")


class SignUpForm(StyledFormMixin, UserCreationForm):
    first_name = forms.CharField(
        label="First name",
        max_length=150,
        widget=forms.TextInput(
            attrs={"placeholder": "Your first name"}
        )
    )

    last_name = forms.CharField(
        label="Last name",
        max_length=150,
        widget=forms.TextInput(
            attrs={"placeholder": "Your last name"}
        )
    )

    email = forms.EmailField(
        label="Email address",
        widget=forms.EmailInput(
            attrs={
                "placeholder": "you@example.com",
                "autocomplete": "email"
            }
        )
    )

    birth_date = forms.DateField(
        label="Date of birth (optional)",
        required=False,
        widget=forms.DateInput(
            attrs={
                "type": "date"
            }
        )
    )

    objectives = forms.CharField(
        label="Personal goals",
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "placeholder": "e.g. Improve my focus at work"
            }
        )
    )

    class Meta:
        model = User
        fields = (
            "first_name",
            "last_name",
            "email",
            "birth_date",
            "objectives",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["password1"].label = "Password"
        self.fields["password2"].label = "Confirm password"

        self.fields["password1"].widget.attrs.update({
            "placeholder": "8 characters minimum",
            "autocomplete": "new-password"
        })

        self.fields["password2"].widget.attrs.update({
            "placeholder": "Repeat password",
            "autocomplete": "new-password"
        })

        self._style()

    def clean_email(self):
        email = self.cleaned_data["email"].lower().strip()

        if User.objects.filter(email=email).exists():
            raise forms.ValidationError(
                "An account already exists with this email address."
            )

        return email


class EmailLoginForm(StyledFormMixin, AuthenticationForm):
    username = forms.EmailField(
        label="Email address",
        widget=forms.EmailInput(
            attrs={
                "placeholder": "you@example.com",
                "autocomplete": "email",
                "autofocus": True
            }
        )
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["password"].label = "Password"

        self.fields["password"].widget.attrs.update({
            "placeholder": "Your password",
            "autocomplete": "current-password"
        })

        self._style()

        self.error_messages["invalid_login"] = (
            "Incorrect email or password. Please check your credentials."
        )


class ProfileForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = User

        fields = (
            "first_name",
            "last_name",
            "email",
            "birth_date",
            "objectives",
        )

        labels = {
            "first_name": "First name",
            "last_name": "Last name",
            "email": "Email address",
            "birth_date": "Date of birth",
            "objectives": "Personal goals",
        }

        widgets = {
            "objectives": forms.Textarea(
                attrs={"rows": 4}
            )
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()

    def clean_email(self):
        email = self.cleaned_data["email"].lower().strip()

        if User.objects.filter(
            email=email
        ).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(
                "This email address is already in use."
            )

        return email