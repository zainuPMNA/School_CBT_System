from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import LoginEvent, Role, StudentProfile, User

PASSWORD = "kitemu47"


def make_student(username="2024001", cls="XII-A", roll="1"):
    user = User.objects.create_user(username=username, password=PASSWORD, role=Role.STUDENT)
    StudentProfile.objects.create(user=user, full_name="Asha Rao", batch_or_class=cls, roll_number=roll)
    return user


def lab_pc(ip):
    return Client(REMOTE_ADDR=ip)


class LoginTests(TestCase):
    def setUp(self):
        self.student = make_student()
        self.login_url = reverse("accounts:login")

    def login(self, client, username="2024001", password=PASSWORD):
        return client.post(self.login_url, {"username": username, "password": password})

    def test_student_login_binds_session_and_redirects_home(self):
        pc = lab_pc("10.0.0.11")
        response = self.login(pc)
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.student.refresh_from_db()
        self.assertEqual(self.student.active_session_key, pc.session.session_key)
        self.assertEqual(self.student.last_login_ip, "10.0.0.11")
        home = pc.get("/")
        self.assertRedirects(home, reverse("accounts:student_home"))

    def test_username_is_case_insensitive_and_trimmed(self):
        user = make_student("ADM-77", roll="77")
        response = self.login(lab_pc("10.0.0.12"), username="  adm-77 ")
        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        self.assertTrue(user.active_session_key)

    def test_wrong_password_logs_failure(self):
        response = self.login(lab_pc("10.0.0.13"), password="nope")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Incorrect username or password")
        self.assertTrue(LoginEvent.objects.filter(event_type="LOGIN_FAILED", user=self.student).exists())

    @override_settings(CBT_SINGLE_SESSION_MODE="BLOCK")
    def test_block_mode_refuses_second_pc_until_unlock(self):
        pc1, pc2 = lab_pc("10.0.0.21"), lab_pc("10.0.0.22")
        self.login(pc1)
        response = self.login(pc2)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already signed in on another computer")
        self.assertTrue(LoginEvent.objects.filter(event_type="LOGIN_BLOCKED").exists())
        # PC1 still works.
        self.assertEqual(pc1.get(reverse("accounts:student_home")).status_code, 200)

        self.student.refresh_from_db()
        self.student.unlock_session()
        # PC1 is now kicked out, PC2 can log in.
        self.assertRedirects(
            pc1.get(reverse("accounts:student_home")),
            f"{self.login_url}?reason=session_ended",
            fetch_redirect_response=False,
        )
        self.assertEqual(self.login(pc2).status_code, 302)

    @override_settings(CBT_SINGLE_SESSION_MODE="BLOCK", CBT_ALLOW_SAME_IP_RELOGIN=True)
    def test_block_mode_allows_same_pc_relogin_after_browser_crash(self):
        self.login(lab_pc("10.0.0.31"))
        fresh_browser_same_pc = lab_pc("10.0.0.31")
        self.assertEqual(self.login(fresh_browser_same_pc).status_code, 302)
        self.assertTrue(LoginEvent.objects.filter(event_type="SESSION_REPLACED").exists())

    @override_settings(CBT_SINGLE_SESSION_MODE="REPLACE")
    def test_replace_mode_kicks_previous_pc(self):
        pc1, pc2 = lab_pc("10.0.0.41"), lab_pc("10.0.0.42")
        self.login(pc1)
        self.assertEqual(self.login(pc2).status_code, 302)
        self.assertEqual(pc2.get(reverse("accounts:student_home")).status_code, 200)
        # AJAX call from the old PC gets a JSON 401.
        response = pc1.get(reverse("accounts:student_home"), HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"], "session_invalid")

    def test_logout_releases_lock(self):
        pc = lab_pc("10.0.0.51")
        self.login(pc)
        pc.post(reverse("accounts:logout"))
        self.student.refresh_from_db()
        self.assertEqual(self.student.active_session_key, "")
        self.assertTrue(LoginEvent.objects.filter(event_type="LOGOUT").exists())

    def test_staff_redirected_to_dashboard_and_not_locked(self):
        User.objects.create_user(username="teacher1", password=PASSWORD, role=Role.TEACHER)
        pc1, pc2 = lab_pc("10.0.0.61"), lab_pc("10.0.0.62")
        self.assertEqual(self.login(pc1, "teacher1").status_code, 302)
        self.assertEqual(self.login(pc2, "teacher1").status_code, 302)
        self.assertRedirects(pc1.get("/"), reverse("dashboard:home"))

    def test_password_hashed_with_lab_hasher(self):
        self.assertTrue(self.student.password.startswith("lab_pbkdf2_sha256$100000$"))
        self.assertTrue(User.objects.filter(username="2024/015").count() == 0)
        slash_user = User(username="2024/015", role=Role.STUDENT)
        slash_user.full_clean(exclude=["password"])


class StaleTokenTests(TestCase):
    """A login page left open while someone signs in elsewhere in the same browser."""

    def setUp(self):
        make_student("2024009", roll="9")
        User.objects.create_user(username="teacher9", password=PASSWORD, role=Role.TEACHER)

    def test_stale_login_page_recovers_instead_of_403(self):
        browser = Client(enforce_csrf_checks=True, REMOTE_ADDR="10.0.9.1")
        old_token = browser.get(reverse("accounts:login")).context["csrf_token"]
        # Another tab of the same browser signs in -> the token rotates.
        browser.post(reverse("accounts:login"), {
            "username": "teacher9", "password": PASSWORD, "csrfmiddlewaretoken": browser.cookies["cbt_csrftoken"].value,
        })
        browser.post(reverse("accounts:logout"), {"csrfmiddlewaretoken": browser.cookies["cbt_csrftoken"].value})
        # The old tab submits with its stale token.
        r = browser.post(reverse("accounts:login"), {"username": "2024009", "password": PASSWORD, "csrfmiddlewaretoken": old_token})
        self.assertRedirects(r, f"{reverse('accounts:login')}?reason=expired", fetch_redirect_response=False)
        page = browser.get(r["Location"])
        self.assertContains(page, "out of date")
        # The fresh page works.
        ok = browser.post(reverse("accounts:login"), {"username": "2024009", "password": PASSWORD,
                                                       "csrfmiddlewaretoken": page.context["csrf_token"]})
        self.assertEqual(ok.status_code, 302)

    def test_stale_login_while_signed_in_elsewhere_explains(self):
        browser = Client(enforce_csrf_checks=True, REMOTE_ADDR="10.0.9.2")
        old_token = browser.get(reverse("accounts:login")).context["csrf_token"]
        browser.post(reverse("accounts:login"), {
            "username": "teacher9", "password": PASSWORD, "csrfmiddlewaretoken": browser.cookies["cbt_csrftoken"].value,
        })
        r = browser.post(reverse("accounts:login"), {"username": "2024009", "password": PASSWORD, "csrfmiddlewaretoken": old_token})
        page = browser.get(r["Location"])
        self.assertContains(page, "This browser is already signed in")
        self.assertContains(page, "teacher9")
        self.assertContains(page, "Sign out, then sign in as someone else")

    def test_stale_token_on_api_is_json(self):
        browser = Client(enforce_csrf_checks=True)
        r = browser.post("/exam/1/api/save/", "{}", content_type="application/json",
                         HTTP_ACCEPT="application/json", HTTP_X_REQUESTED_WITH="XMLHttpRequest", HTTP_X_CSRFTOKEN="stale")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"], "csrf_failed")

    def test_other_forms_show_friendly_page(self):
        browser = Client(enforce_csrf_checks=True)
        r = browser.post(reverse("accounts:logout"), {"csrfmiddlewaretoken": "stale"}, HTTP_REFERER="https://evil.example/")
        self.assertEqual(r.status_code, 403)
        self.assertContains(r, "This page was out of date", status_code=403)
        self.assertNotContains(r, "evil.example", status_code=403)
