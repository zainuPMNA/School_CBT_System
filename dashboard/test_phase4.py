import io
import json
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from accounts.models import Role, StudentProfile, User
from exams.analytics import question_analysis, ranked_results, scorecard
from exams.models import AnswerStatus, AttemptStatus, Exam, Question, StudentAttempt

PW = "kitemu47"


def make_student(username, roll, cls="XII-A"):
    user = User.objects.create_user(username=username, password=PW, role=Role.STUDENT)
    StudentProfile.objects.create(user=user, full_name=f"Student {username}", batch_or_class=cls, roll_number=str(roll))
    return user


class Phase4Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(username="admin1", password=PW, role=Role.ADMIN)
        cls.teacher = User.objects.create_user(username="teacher1", password=PW, role=Role.TEACHER)
        cls.exam = Exam.objects.create(title="Weekly Chem", subject="Chemistry", target_class="XII-A",
                                       duration_minutes=30, positive_marks=Decimal("4"), negative_marks=Decimal("1"),
                                       is_active=True)
        keys = ["A", "B", "C", "D"]
        cls.questions = [
            Question.objects.create(exam=cls.exam, sort_order=i + 1, question_text=f"Q{i + 1} $\\ce{{H2O}}$",
                                    option_a="a", option_b="b", option_c="c", option_d="d",
                                    correct_option=keys[i], topic_tag="Acids" if i < 2 else "Bases")
            for i in range(4)
        ]
        cls.students = [make_student(f"S{i}", i) for i in range(1, 7)]

    def answer(self, student, picks, submit=True, time_taken=None):
        """picks: list of canonical keys / None in authoring order."""
        attempt, _ = StudentAttempt.start_or_resume(student, self.exam)
        for q, pick in zip(self.questions, picks):
            attempt.answers.filter(question=q).update(
                selected_option=pick, status=AnswerStatus.ANSWERED if pick else AnswerStatus.NOT_ANSWERED)
        if submit:
            attempt.submit()
            if time_taken is not None:
                StudentAttempt.objects.filter(pk=attempt.pk).update(time_taken_seconds=time_taken)
        attempt.refresh_from_db()
        return attempt

    def client_for(self, user, ip="10.9.0.1"):
        client = Client(REMOTE_ADDR=ip)
        client.post(reverse("accounts:login"), {"username": user.username, "password": PW})
        return client


class RankingTests(Phase4Base):
    def test_tie_breaks_and_shared_rank(self):
        s1, s2, s3, s4, s5, s6 = self.students
        self.answer(s1, ["A", "B", "C", "D"], time_taken=900)   # 16, perfect
        self.answer(s2, ["A", "B", "C", None], time_taken=600)  # 12, 100% accuracy
        self.answer(s3, ["A", "B", "C", "A"], time_taken=500)   # 11
        self.answer(s4, ["A", "B", "A", "A"], time_taken=300)   # 6, 2 wrong
        self.answer(s5, ["A", "B", "A", "A"], time_taken=300)   # identical to s4 -> same rank
        self.answer(s6, [None, None, None, None], submit=False)  # still writing

        results = ranked_results(self.exam)
        ranks = {r["username"]: r["rank"] for r in results.rows}
        self.assertEqual(ranks, {"S1": 1, "S2": 2, "S3": 3, "S4": 4, "S5": 4})
        self.assertEqual([r["username"] for r in results.writing], ["S6"])
        self.assertEqual(results.stats["highest"], Decimal("16.00"))
        self.assertEqual(results.stats["average"], Decimal("10.20"))
        self.assertEqual(results.stats["median"], Decimal("11.00"))

    def test_accuracy_beats_time_on_equal_score(self):
        s1, s2 = self.students[:2]
        self.answer(s1, ["A", "B", None, None], time_taken=100)  # 8 marks, 2/2
        self.answer(s2, ["A", "B", "C", "A"], time_taken=50)     # 4*3-1 = 11 -> different; adjust:
        # Make equal scores: s2 gets 3 correct + 4 wrong impossible with 4 Q; use exam marks instead.
        Exam.objects.filter(pk=self.exam.pk).update(negative_marks=Decimal("4"))
        self.exam.refresh_from_db()
        self.exam.recalculate_scores()  # s1 = 8, s2 = 12-4 = 8
        rows = {r["username"]: r for r in ranked_results(self.exam).rows}
        self.assertEqual(rows["S1"]["score"], rows["S2"]["score"])
        self.assertEqual((rows["S1"]["rank"], rows["S2"]["rank"]), (1, 2))  # 100% vs 75% accuracy

    def test_absent_students_listed(self):
        make_student("LATE", 99)
        self.answer(self.students[0], ["A", None, None, None])
        absent = {r["username"] for r in ranked_results(self.exam).absent}
        self.assertIn("LATE", absent)
        self.assertNotIn("S1", absent)


class AnalysisTests(Phase4Base):
    def test_question_stats_difficulty_and_key_flag(self):
        # Everyone picks B on Q1 whose key is A -> "check answer key" flag; Q2 easy.
        for s in self.students:
            self.answer(s, ["B", "B", None, "D" if s.username in ("S1", "S2") else "A"])
        analysis = question_analysis(self.exam)
        q1, q2, q3, q4 = analysis["items"]
        self.assertEqual((q1["correct"], q1["wrong"], q1["skipped"]), (0, 6, 0))
        self.assertEqual(q1["difficulty"], "Hard")
        self.assertTrue(any("Check answer key" in f for f in q1["flags"]))
        self.assertEqual(q2["difficulty"], "Easy")
        self.assertEqual(q3["pct_skipped"], Decimal("100.00"))
        self.assertEqual({d["key"]: d["count"] for d in q4["distribution"]}, {"A": 4, "B": 0, "C": 0, "D": 2})
        self.assertEqual(analysis["hardest"][0]["number"], 1)
        topics = {t["topic"]: t for t in analysis["topics"]}
        self.assertEqual(topics["Acids"]["pct_correct"], Decimal("50.00"))

    def test_scorecard_uses_student_display_letters(self):
        attempt = self.answer(self.students[0], ["A", "C", None, "D"])
        card = scorecard(attempt)
        by_q = {i["question"].pk: i for i in card["items"]}
        q1 = by_q[self.questions[0].pk]
        self.assertEqual(q1["outcome"], "correct")
        self.assertEqual(q1["your_label"], "ABCD"[attempt.display_index_of(self.questions[0].pk, "A")])
        self.assertEqual(by_q[self.questions[1].pk]["marks"], Decimal("-1"))
        self.assertEqual(card["rank"], 1)
        self.assertEqual({t["topic"]: t["marks"] for t in card["topics"]}, {"Acids": Decimal("3"), "Bases": Decimal("4")})


class MonitorTests(Phase4Base):
    def test_monitor_page_and_actions(self):
        s1, s2, s3 = self.students[:3]
        writing = self.answer(s1, ["A", "B", None, None], submit=False)
        self.answer(s2, ["A", None, None, None])
        c = self.client_for(self.teacher)
        page = c.get(reverse("dashboard:monitor_exam", args=[self.exam.pk]))
        self.assertEqual(page.status_code, 200)
        summary = page.context["summary"]
        self.assertEqual((summary["writing"], summary["submitted"], summary["not_started"]), (1, 1, 4))
        row = next(r for r in page.context["rows"] if r["student"] == s1)
        self.assertEqual(row["attempt"].answered, 2)

        table = c.get(reverse("dashboard:monitor_table", args=[self.exam.pk]), HTTP_HX_REQUEST="true")
        self.assertContains(table, "Writing")

        r = c.post(reverse("dashboard:monitor_extend", args=[self.exam.pk, writing.pk]), {"minutes": "7"}, HTTP_HX_REQUEST="true")
        self.assertContains(r, "+7 min")
        writing.refresh_from_db()
        self.assertEqual(writing.extra_time_minutes, 7)
        self.assertContains(c.post(reverse("dashboard:monitor_extend", args=[self.exam.pk, writing.pk]), {"minutes": "999"},
                                   HTTP_HX_REQUEST="true"), "Enter 1")

        c.post(reverse("dashboard:monitor_extend_all", args=[self.exam.pk]), {"minutes": "3"}, HTTP_HX_REQUEST="true")
        writing.refresh_from_db()
        self.assertEqual(writing.extra_time_minutes, 10)

        student_pc = self.client_for(s3, ip="10.9.0.33")
        s3.refresh_from_db()
        self.assertTrue(s3.active_session_key)
        c.post(reverse("dashboard:monitor_unlock", args=[self.exam.pk, s3.pk]), HTTP_HX_REQUEST="true")
        s3.refresh_from_db()
        self.assertEqual(s3.active_session_key, "")
        self.assertEqual(student_pc.get(reverse("accounts:student_home")).status_code, 302)

        r = c.post(reverse("dashboard:monitor_force_submit", args=[self.exam.pk, writing.pk]))
        self.assertRedirects(r, reverse("dashboard:monitor_exam", args=[self.exam.pk]))
        writing.refresh_from_db()
        self.assertEqual(writing.status, AttemptStatus.SUBMITTED)
        self.assertEqual(writing.force_submitted_by, self.teacher)

    def test_submit_all_and_violation_list(self):
        a = self.answer(self.students[0], ["A", None, None, None], submit=False)
        from exams.models import ViolationLog
        ViolationLog.record(a, "TAB_SWITCH", "x")
        c = self.client_for(self.teacher)
        self.assertContains(c.get(reverse("dashboard:monitor_violations", args=[self.exam.pk, a.pk])), "Tab switch")
        c.post(reverse("dashboard:monitor_force_submit_all", args=[self.exam.pk]), HTTP_HX_REQUEST="true")
        a.refresh_from_db()
        self.assertTrue(a.is_finished)

    def test_students_cannot_use_monitor(self):
        c = self.client_for(self.students[0])
        self.assertEqual(c.get(reverse("dashboard:monitor_exam", args=[self.exam.pk])).status_code, 403)


class PublishingAndExportTests(Phase4Base):
    def test_scores_hidden_until_published_then_visible(self):
        s1 = self.students[0]
        self.answer(s1, ["A", "B", "C", "A"])
        writing = self.answer(self.students[1], ["A", None, None, None], submit=False)
        student = self.client_for(s1, ip="10.9.0.41")
        self.assertRedirects(student.get(reverse("cbt:result", args=[self.exam.pk])), reverse("accounts:student_home"))
        self.assertNotContains(student.get(reverse("accounts:student_home")), "View scorecard")

        teacher = self.client_for(self.teacher)
        self.assertEqual(teacher.post(reverse("dashboard:publish_results", args=[self.exam.pk])).status_code, 403)
        admin = self.client_for(self.admin)
        admin.post(reverse("dashboard:publish_results", args=[self.exam.pk]))
        self.exam.refresh_from_db()
        writing.refresh_from_db()
        self.assertTrue(self.exam.results_published)
        self.assertFalse(self.exam.is_active)
        self.assertEqual(writing.status, AttemptStatus.AUTO_SUBMITTED)

        home = student.get(reverse("accounts:student_home"))
        self.assertContains(home, "View scorecard")
        page = student.get(reverse("cbt:result", args=[self.exam.pk]))
        self.assertContains(page, "11")  # 3 correct, 1 wrong
        self.assertNotContains(page, "Answer review")  # review disabled by default
        pdf = student.get(reverse("cbt:result_pdf", args=[self.exam.pk]))
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))

        Exam.objects.filter(pk=self.exam.pk).update(allow_review_after_publish=True)
        review = student.get(reverse("cbt:result", args=[self.exam.pk]))
        self.assertContains(review, "Answer review")
        self.assertContains(review, "Correct answer")

        admin.post(reverse("dashboard:unpublish_results", args=[self.exam.pk]))
        self.assertEqual(student.get(reverse("cbt:result", args=[self.exam.pk])).status_code, 302)

    def test_results_pages_and_exports(self):
        for s, picks in zip(self.students[:3], (["A", "B", "C", "D"], ["A", "C", None, None], [None, None, None, "D"])):
            self.answer(s, picks)
        c = self.client_for(self.teacher)
        self.assertContains(c.get(reverse("dashboard:exam_results", args=[self.exam.pk])), "Rank list")
        analysis_page = c.get(reverse("dashboard:question_analysis", args=[self.exam.pk]) + "?sort=hardest")
        self.assertContains(analysis_page, "Question-wise analysis")
        self.assertNotContains(analysis_page, "Discrimination compares")  # needs >= 4 submissions

        xlsx = c.get(reverse("dashboard:export_results", args=[self.exam.pk, "xlsx"]))
        wb = load_workbook(io.BytesIO(xlsx.content))
        self.assertEqual(wb.sheetnames, ["Rank List", "Question Analysis", "Topics", "Responses"])
        rank_sheet = wb["Rank List"]
        self.assertEqual(rank_sheet.cell(row=6, column=2).value, "S1")
        self.assertEqual(rank_sheet.cell(row=6, column=6).value, 16)
        self.assertEqual(wb["Responses"].cell(row=2, column=5).value, "A")  # answer key row

        csv_text = c.get(reverse("dashboard:export_results", args=[self.exam.pk, "csv"])).content.decode("utf-8-sig")
        self.assertIn("ABSENT", csv_text)
        self.assertEqual(csv_text.splitlines()[1].split(",")[1], "S1")

        all_pdf = c.get(reverse("dashboard:scorecards_pdf", args=[self.exam.pk]))
        self.assertTrue(all_pdf.content.startswith(b"%PDF"))
        one = StudentAttempt.objects.get(student=self.students[1])
        single = c.get(reverse("dashboard:scorecards_pdf", args=[self.exam.pk]) + f"?attempt={one.pk}")
        self.assertIn("inline", single["Content-Disposition"])
        self.assertEqual(c.get(reverse("dashboard:export_results", args=[self.exam.pk, "pdf"])).status_code, 404)
