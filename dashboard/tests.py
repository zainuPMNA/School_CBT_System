import io
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook, load_workbook

from accounts.models import Role, StudentProfile, User
from exams.models import Exam, Question, StudentAttempt

PW = "kitemu47"


def csv_file(text, name="students.csv"):
    return SimpleUploadedFile(name, text.encode("utf-8"), content_type="text/csv")


def xlsx_file(rows, name="sheet.xlsx"):
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile(name, buf.getvalue())


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="cbt-test-media-"))
class DashboardTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(username="admin1", password=PW, role=Role.ADMIN)
        cls.teacher = User.objects.create_user(username="teacher1", password=PW, role=Role.TEACHER)
        cls.student = User.objects.create_user(username="S1", password=PW, role=Role.STUDENT)
        StudentProfile.objects.create(user=cls.student, full_name="Stu One", batch_or_class="X-A", roll_number="1")

    def client_for(self, user):
        client = Client(REMOTE_ADDR="10.1.1.1")
        client.post(reverse("accounts:login"), {"username": user.username, "password": PW})
        return client


class PermissionTests(DashboardTestBase):
    def test_anonymous_redirected_to_login(self):
        response = Client().get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_student_forbidden(self):
        self.assertEqual(self.client_for(self.student).get(reverse("dashboard:home")).status_code, 403)

    def test_teacher_can_manage_exams_but_not_students(self):
        c = self.client_for(self.teacher)
        self.assertEqual(c.get(reverse("dashboard:exam_list")).status_code, 200)
        self.assertEqual(c.get(reverse("dashboard:student_list")).status_code, 200)
        self.assertEqual(c.get(reverse("dashboard:student_upload")).status_code, 403)
        self.assertEqual(c.get(reverse("dashboard:login_slips")).status_code, 403)

    def test_all_admin_pages_render(self):
        c = self.client_for(self.admin)
        exam = Exam.objects.create(title="T", subject="S", target_class="X-A", duration_minutes=10)
        q = Question.objects.create(exam=exam, question_text="$x$", option_a="1", option_b="2",
                                    option_c="3", option_d="4", correct_option="A")
        for url in [
            reverse("dashboard:home"),
            reverse("dashboard:student_list") + "?q=S&class=x-a&locked=1",
            reverse("dashboard:student_create"),
            reverse("dashboard:student_upload"),
            reverse("dashboard:login_slips") + "?class=X-A",
            reverse("dashboard:exam_list") + "?status=active",
            reverse("dashboard:exam_create"),
            reverse("dashboard:exam_detail", args=[exam.pk]),
            reverse("dashboard:exam_update", args=[exam.pk]),
            reverse("dashboard:exam_delete", args=[exam.pk]),
            reverse("dashboard:question_create", args=[exam.pk]),
            reverse("dashboard:question_update", args=[q.pk]),
            reverse("dashboard:question_upload", args=[exam.pk]),
        ]:
            with self.subTest(url=url):
                self.assertEqual(c.get(url).status_code, 200)


class StudentImportTests(DashboardTestBase):
    def test_csv_import_creates_students_with_generated_passwords(self):
        c = self.client_for(self.admin)
        data = "admission_no,full_name,class_name,roll_no,password\n2024001,Asha Rao,xii a,1,Riv3rTulip\n2024/002,Ravi K,XII A,2,\n"
        response = c.post(reverse("dashboard:student_upload"), {"file": csv_file(data)})
        self.assertContains(response, "Import complete: 2 created")
        asha = User.objects.get(username="2024001")
        self.assertTrue(asha.check_password("Riv3rTulip"))
        self.assertEqual(asha.student_profile.batch_or_class, "XII A")
        ravi = User.objects.get(username="2024/002")
        self.assertEqual(len(ravi.student_profile.initial_password), 10)
        self.assertTrue(ravi.check_password(ravi.student_profile.initial_password))

    def test_import_is_all_or_nothing(self):
        c = self.client_for(self.admin)
        data = "admission_no,full_name,class_name\nA1,Good Row,X-B\nA1,Duplicate,X-B\n,No Adm,X-B\n"
        response = c.post(reverse("dashboard:student_upload"), {"file": csv_file(data)})
        self.assertContains(response, "nothing saved")
        self.assertContains(response, "duplicate admission_no")
        self.assertFalse(User.objects.filter(username="A1").exists())

    def test_xlsx_import_with_numeric_cells_and_update(self):
        c = self.client_for(self.admin)
        rows = [["Admission No", "Name", "Class", "Roll No", "Password"], [3001, "Num Cell", "X-A", 2, "Mango92kite"]]
        c.post(reverse("dashboard:student_upload"), {"file": xlsx_file(rows)})
        user = User.objects.get(username="3001")
        self.assertTrue(user.check_password("Mango92kite"))
        rows = [["admission_no", "full_name", "class_name", "roll_no", "password"], ["3001", "Renamed", "X-A", "2", ""]]
        response = c.post(reverse("dashboard:student_upload"), {"file": xlsx_file(rows), "update_existing": "on"})
        self.assertContains(response, "1 updated")
        user.refresh_from_db()
        self.assertEqual(user.student_profile.full_name, "Renamed")
        self.assertTrue(user.check_password("Mango92kite"))

    def test_roll_conflict_with_existing_student_rejected(self):
        c = self.client_for(self.admin)
        data = "admission_no,full_name,class_name,roll_no\nNEW1,New Kid,X-A,1\n"
        response = c.post(reverse("dashboard:student_upload"), {"file": csv_file(data)})
        self.assertContains(response, "already belongs to another student")

    def test_missing_column_reported(self):
        c = self.client_for(self.admin)
        response = c.post(reverse("dashboard:student_upload"), {"file": csv_file("name,class\nA,B\n")})
        self.assertContains(response, "Missing required column")

    def test_templates_download(self):
        c = self.client_for(self.teacher)
        for name in ("student_template", "question_template"):
            xlsx = c.get(reverse(f"dashboard:{name}", args=["xlsx"]))
            self.assertEqual(xlsx.status_code, 200)
            wb = load_workbook(io.BytesIO(xlsx.content))
            self.assertEqual(wb.sheetnames[1], "Instructions")
            csv_resp = c.get(reverse(f"dashboard:{name}", args=["csv"]))
            self.assertTrue(csv_resp.content.decode("utf-8").startswith("﻿"))
        self.assertEqual(c.get(reverse("dashboard:student_template", args=["pdf"])).status_code, 404)

    def test_template_round_trip_imports(self):
        c = self.client_for(self.admin)
        xlsx = c.get(reverse("dashboard:student_template", args=["xlsx"]))
        upload = SimpleUploadedFile("t.xlsx", xlsx.content)
        response = c.post(reverse("dashboard:student_upload"), {"file": upload})
        self.assertContains(response, "Import complete: 3 created")


class StudentActionTests(DashboardTestBase):
    def test_slips_show_credentials(self):
        self.student.student_profile.initial_password = "abcdef23"
        self.student.student_profile.save()
        response = self.client_for(self.admin).get(reverse("dashboard:login_slips") + "?class=X-A")
        self.assertContains(response, "abcdef23")
        self.assertContains(response, "Stu One")

    def test_unlock_and_reset_password(self):
        student_pc = self.client_for(self.student)
        self.student.refresh_from_db()
        self.assertTrue(self.student.active_session_key)
        teacher = self.client_for(self.teacher)
        teacher.post(reverse("dashboard:student_unlock", args=[self.student.pk]))
        self.student.refresh_from_db()
        self.assertEqual(self.student.active_session_key, "")
        self.assertEqual(student_pc.get(reverse("accounts:student_home")).status_code, 302)
        self.assertEqual(
            teacher.post(reverse("dashboard:student_reset_password", args=[self.student.pk])).status_code, 403
        )
        self.client_for(self.admin).post(reverse("dashboard:student_reset_password", args=[self.student.pk]))
        self.student.refresh_from_db()
        self.assertTrue(self.student.check_password(self.student.student_profile.initial_password))

    def test_regenerate_class_passwords(self):
        c = self.client_for(self.admin)
        c.post(reverse("dashboard:class_password_regenerate"), {"class_name": "x-a", "confirm": "yes"})
        self.student.refresh_from_db()
        self.assertFalse(self.student.check_password(PW))
        self.assertTrue(self.student.check_password(self.student.student_profile.initial_password))

    def test_create_single_student(self):
        c = self.client_for(self.admin)
        c.post(reverse("dashboard:student_create"), {"admission_no": "NEW9", "full_name": "N N", "class_name": "x-a"})
        self.assertTrue(User.objects.get(username="NEW9").check_password(
            StudentProfile.objects.get(user__username="NEW9").initial_password))


class ExamManagementTests(DashboardTestBase):
    def setUp(self):
        self.c = self.client_for(self.teacher)

    def create_exam(self):
        self.c.post(reverse("dashboard:exam_create"), {
            "title": "Weekly Physics", "subject": "Physics", "target_class": "x-a", "duration_minutes": 30,
            "positive_marks": "4", "negative_marks": "1", "shuffle_questions": "on", "shuffle_options": "on",
        })
        return Exam.objects.get(title="Weekly Physics")

    def test_create_upload_activate_flow(self):
        exam = self.create_exam()
        self.assertEqual(exam.target_class, "X-A")
        self.assertEqual(exam.created_by, self.teacher)

        # Activation refused with no questions.
        self.c.post(reverse("dashboard:exam_toggle", args=[exam.pk]))
        exam.refresh_from_db()
        self.assertFalse(exam.is_active)

        sheet = self.c.get(reverse("dashboard:question_template", args=["xlsx"])).content
        response = self.c.post(reverse("dashboard:question_upload", args=[exam.pk]),
                               {"file": SimpleUploadedFile("q.xlsx", sheet), "mode": "APPEND"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(exam.questions.count(), 3)
        self.assertEqual(list(exam.questions.values_list("correct_option", flat=True)), ["A", "B", "B"])

        self.c.post(reverse("dashboard:exam_toggle", args=[exam.pk]))
        exam.refresh_from_db()
        self.assertTrue(exam.is_active)
        detail = self.c.get(reverse("dashboard:exam_detail", args=[exam.pk]))
        self.assertContains(detail, r"\ce{Ca(OH)2}")

    def test_question_upload_validation_and_replace(self):
        exam = self.create_exam()
        bad = "question_text,option_a,option_b,option_c,option_d,correct_option\nQ?,1,2,3,,E\n"
        response = self.c.post(reverse("dashboard:question_upload", args=[exam.pk]),
                               {"file": csv_file(bad, "q.csv"), "mode": "APPEND"})
        self.assertContains(response, "option_d is empty")
        self.assertContains(response, "must be A, B, C or D")
        good = "question,a,b,c,d,answer\nQ1,1,2,3,4,2\nQ2,1,2,3,4,d\n"
        self.c.post(reverse("dashboard:question_upload", args=[exam.pk]), {"file": csv_file(good, "q.csv"), "mode": "APPEND"})
        self.c.post(reverse("dashboard:question_upload", args=[exam.pk]), {"file": csv_file(good, "q.csv"), "mode": "REPLACE"})
        self.assertEqual(list(exam.questions.values_list("correct_option", "sort_order")), [("B", 1), ("D", 2)])

    def test_structure_locked_after_attempt_and_key_change_regrades(self):
        exam = self.create_exam()
        q = Question.objects.create(exam=exam, sort_order=1, question_text="Q", option_a="1", option_b="2",
                                    option_c="3", option_d="4", correct_option="A")
        exam.is_active = True
        exam.save()
        attempt, _ = StudentAttempt.start_or_resume(self.student, exam)
        answer = attempt.answers.get()
        answer.selected_option = "B"
        answer.save()
        attempt.submit()
        self.assertEqual(attempt.total_score, -1)

        self.assertRedirects(self.c.get(reverse("dashboard:question_create", args=[exam.pk])),
                             reverse("dashboard:exam_detail", args=[exam.pk]))
        self.c.post(reverse("dashboard:question_delete", args=[q.pk]))
        self.assertTrue(Question.objects.filter(pk=q.pk).exists())

        self.c.post(reverse("dashboard:question_update", args=[q.pk]), {
            "sort_order": 1, "question_text": "Q", "option_a": "1", "option_b": "2", "option_c": "3",
            "option_d": "4", "correct_option": "B", "topic_tag": "", "explanation": "",
        })
        attempt.refresh_from_db()
        self.assertEqual(attempt.total_score, 4)

    def test_question_image_upload(self):
        exam = self.create_exam()
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (40, 20), "white").save(buf, format="PNG")
        png = buf.getvalue()
        self.c.post(reverse("dashboard:question_create", args=[exam.pk]), {
            "sort_order": 1, "question_text": "See figure", "option_a": "1", "option_b": "2", "option_c": "3",
            "option_d": "4", "correct_option": "C", "question_image": SimpleUploadedFile("fig.png", png, "image/png"),
        })
        q = exam.questions.get()
        self.assertTrue(q.question_image.name.startswith(f"questions/exam_{exam.pk}/"))
