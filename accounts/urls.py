from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("", views.home_redirect, name="home"),
    path("login/", views.CBTLoginView.as_view(), name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("student/", views.StudentHomeView.as_view(), name="student_home"),
    path("about/", views.AboutView.as_view(), name="about"),
    path("preferences/theme/", views.set_theme, name="set_theme"),
]
