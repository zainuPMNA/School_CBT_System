from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User


class ThemeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("thm_admin", password="x-Strong-pass-123", role=Role.ADMIN)

    def test_signed_in_user_choice_is_saved(self):
        self.client.force_login(self.user)
        r = self.client.post(reverse("accounts:set_theme"), {"theme": "warm"})
        self.assertEqual(r.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.theme, "warm")

    def test_unknown_theme_rejected(self):
        self.client.force_login(self.user)
        r = self.client.post(reverse("accounts:set_theme"), {"theme": "neon"})
        self.assertEqual(r.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.theme, "light")

    def test_anonymous_visitor_gets_ok_without_saving(self):
        r = self.client.post(reverse("accounts:set_theme"), {"theme": "dark"})
        self.assertEqual(r.status_code, 200)

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get(reverse("accounts:set_theme")).status_code, 405)

    def test_pages_render_saved_theme_and_switcher(self):
        User.objects.filter(pk=self.user.pk).update(theme="dark")
        self.client.force_login(self.user)
        r = self.client.get(reverse("dashboard:home"))
        self.assertContains(r, 'data-theme="dark"')
        self.assertContains(r, 'data-set-theme="warm"')
        self.assertNotContains(r, "\" data-theme-from-browser>")

    def test_login_page_uses_browser_choice(self):
        r = self.client.get(reverse("accounts:login"))
        self.assertContains(r, "\" data-theme-from-browser>")
        self.assertContains(r, 'data-set-theme="dark"')
