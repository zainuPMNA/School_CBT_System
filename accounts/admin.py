from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.translation import ngettext

from .models import LoginEvent, Role, StudentProfile, User


class StudentProfileInline(admin.StackedInline):
    model = StudentProfile
    can_delete = False
    extra = 0
    max_num = 1
    fields = ("full_name", "batch_or_class", "roll_number", "initial_password")
    verbose_name_plural = "Student profile"


@admin.register(User)
class CBTUserAdmin(DjangoUserAdmin):
    list_display = (
        "username",
        "name_col",
        "role",
        "class_col",
        "roll_col",
        "is_active",
        "session_locked",
        "last_login",
        "last_login_ip",
    )
    list_filter = ("role", "is_active", "student_profile__batch_or_class")
    list_select_related = ("student_profile",)
    search_fields = (
        "username",
        "first_name",
        "last_name",
        "student_profile__full_name",
        "student_profile__roll_number",
    )
    ordering = ("role", "username")
    list_per_page = 100
    readonly_fields = (
        "active_session_key",
        "session_started_at",
        "last_login_ip",
        "last_login",
        "date_joined",
    )
    fieldsets = (
        (None, {"fields": ("username", "password", "role")}),
        ("Personal info", {"fields": ("first_name", "last_name", "email")}),
        (
            "Single-device session lock",
            {"fields": ("active_session_key", "session_started_at", "last_login_ip")},
        ),
        (
            "Permissions",
            {
                "classes": ("collapse",),
                "fields": ("is_active", "is_superuser", "groups", "user_permissions"),
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("username", "role", "usable_password", "password1", "password2"),
            },
        ),
    )
    actions = ["unlock_sessions", "activate_users", "deactivate_users"]

    def get_inlines(self, request, obj):
        if obj is not None and obj.role == Role.STUDENT:
            return [StudentProfileInline]
        return []

    @admin.display(description="Name", ordering="student_profile__full_name")
    def name_col(self, obj):
        return obj.display_name

    @admin.display(description="Class", ordering="student_profile__batch_or_class")
    def class_col(self, obj):
        profile = getattr(obj, "student_profile", None)
        return profile.batch_or_class if profile else "—"

    @admin.display(description="Roll", ordering="student_profile__roll_number")
    def roll_col(self, obj):
        profile = getattr(obj, "student_profile", None)
        return profile.roll_number if profile and profile.roll_number else "—"

    @admin.display(description="Session lock", boolean=True)
    def session_locked(self, obj):
        return bool(obj.active_session_key)

    @admin.action(description="Reset / unlock session (allow login on another PC)")
    def unlock_sessions(self, request, queryset):
        count = 0
        for user in queryset:
            user.unlock_session(performed_by=request.user, reason="Unlocked from Django admin.")
            count += 1
        self.message_user(
            request,
            ngettext("%d session unlocked.", "%d sessions unlocked.", count) % count,
            messages.SUCCESS,
        )

    @admin.action(description="Activate selected users")
    def activate_users(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f"{updated} user(s) activated.", messages.SUCCESS)

    @admin.action(description="Deactivate selected users (and end their sessions)")
    def deactivate_users(self, request, queryset):
        count = 0
        for user in queryset.exclude(pk=request.user.pk):
            user.clear_session(delete_session_row=True)
            user.is_active = False
            user.save(update_fields=["is_active"])
            count += 1
        self.message_user(request, f"{count} user(s) deactivated.", messages.WARNING)


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display = ("full_name", "admission_col", "batch_or_class", "roll_number", "initial_password", "updated_at")
    list_filter = ("batch_or_class",)
    search_fields = ("full_name", "user__username", "roll_number")
    list_select_related = ("user",)
    autocomplete_fields = ("user",)
    ordering = ("batch_or_class", "roll_number", "full_name")
    list_per_page = 100

    @admin.display(description="Admission no.", ordering="user__username")
    def admission_col(self, obj):
        return obj.user.username


@admin.register(LoginEvent)
class LoginEventAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "event_type", "user", "username_attempted", "ip_address", "performed_by", "details")
    list_filter = ("event_type", "timestamp")
    search_fields = ("user__username", "username_attempted", "ip_address", "details")
    list_select_related = ("user", "performed_by")
    date_hierarchy = "timestamp"
    list_per_page = 200

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
