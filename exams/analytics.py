"""
Result calculation & analytics shared by the dashboard, exports and the
student scorecard, so every screen and file shows identical numbers.

Ranking / tie-break rules (best first):
  1. Higher total score
  2. Higher accuracy  (correct / attempted)
  3. Fewer wrong answers
  4. Less time taken
Students equal on all four share a rank (1, 2, 2, 4 …).
"""

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from statistics import median

from django.db.models import Count, F

from accounts.models import User

from .models import (
    OPTION_KEYS,
    AttemptStatus,
    Exam,
    Question,
    StudentAnswer,
    StudentAttempt,
)

TWO = Decimal("0.01")
POSITION_LABELS = ("A", "B", "C", "D")
UPPER_LOWER_FRACTION = 0.27  # classic item-analysis group size


def format_duration(seconds) -> str:
    if seconds is None:
        return "—"
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _pct(part, whole) -> Decimal:
    if not whole:
        return Decimal("0.00")
    return (Decimal(part) * 100 / Decimal(whole)).quantize(TWO)


def _tie_key(attempt: StudentAttempt):
    accuracy = Decimal(attempt.correct_count) / attempt.attempted_count if attempt.attempted_count else Decimal(0)
    time_taken = attempt.time_taken_seconds if attempt.time_taken_seconds is not None else 10**9
    return (-attempt.total_score, -accuracy, attempt.wrong_count, time_taken)


# ---------------------------------------------------------------------------
# Rank list
# ---------------------------------------------------------------------------
@dataclass
class ExamResults:
    exam: Exam
    total_questions: int
    max_marks: Decimal
    rows: list[dict] = field(default_factory=list)       # finished attempts, ranked
    writing: list[dict] = field(default_factory=list)    # still IN_PROGRESS
    absent: list[dict] = field(default_factory=list)     # in class, never started
    stats: dict = field(default_factory=dict)

    def row_for(self, attempt_id) -> dict | None:
        return next((r for r in self.rows if r["attempt"].pk == attempt_id), None)


def _student_bits(user: User) -> dict:
    profile = getattr(user, "student_profile", None)
    return {
        "student": user,
        "username": user.username,
        "name": profile.full_name if profile else user.username,
        "roll": profile.roll_number if profile else "",
        "class_name": profile.batch_or_class if profile else "",
    }


def _attempt_row(attempt: StudentAttempt, rank, total_questions, max_marks) -> dict:
    row = _student_bits(attempt.student)
    row.update(
        attempt=attempt,
        rank=rank,
        score=attempt.total_score,
        correct=attempt.correct_count,
        wrong=attempt.wrong_count,
        attempted=attempt.attempted_count,
        skipped=max(0, total_questions - attempt.attempted_count),
        accuracy=_pct(attempt.correct_count, attempt.attempted_count),
        percentage=_pct(attempt.total_score, max_marks) if max_marks else Decimal("0.00"),
        time_taken=attempt.time_taken_seconds,
        time_text=format_duration(attempt.time_taken_seconds),
        status=attempt.status,
        status_display=attempt.get_status_display(),
        violations=attempt.violation_count,
        submitted_at=attempt.submitted_at,
    )
    return row


def ranked_results(exam: Exam) -> ExamResults:
    total_questions = exam.questions.count()
    max_marks = (exam.positive_marks * total_questions).quantize(TWO)
    results = ExamResults(exam=exam, total_questions=total_questions, max_marks=max_marks)

    attempts = list(
        exam.attempts.select_related("student", "student__student_profile", "exam")
    )
    finished = [a for a in attempts if a.status != AttemptStatus.IN_PROGRESS]
    finished.sort(key=lambda a: (_tie_key(a), a.student.username))

    rank, previous = 0, None
    for position, attempt in enumerate(finished, start=1):
        key = _tie_key(attempt)
        if key != previous:
            rank, previous = position, key
        results.rows.append(_attempt_row(attempt, rank, total_questions, max_marks))

    for attempt in attempts:
        if attempt.status == AttemptStatus.IN_PROGRESS:
            results.writing.append(_attempt_row(attempt, None, total_questions, max_marks))

    started_ids = {a.student_id for a in attempts}
    for user in (
        User.objects.students()
        .filter(student_profile__batch_or_class__in=exam.target_classes)
        .exclude(pk__in=started_ids)
        .select_related("student_profile")
        .order_by("student_profile__roll_number", "username")
    ):
        results.absent.append(_student_bits(user))

    scores = [r["score"] for r in results.rows]
    results.stats = {
        "appeared": len(results.rows),
        "writing": len(results.writing),
        "absent": len(results.absent),
        "highest": max(scores) if scores else None,
        "lowest": min(scores) if scores else None,
        "average": (sum(scores) / len(scores)).quantize(TWO) if scores else None,
        "median": Decimal(median(scores)).quantize(TWO) if scores else None,
        "avg_accuracy": (sum(r["accuracy"] for r in results.rows) / len(results.rows)).quantize(TWO) if results.rows else None,
        "max_marks": max_marks,
        "total_questions": total_questions,
    }
    return results


# ---------------------------------------------------------------------------
# Question-wise analysis
# ---------------------------------------------------------------------------
def question_analysis(exam: Exam, results: ExamResults | None = None) -> dict:
    results = results or ranked_results(exam)
    attempt_ids = [r["attempt"].pk for r in results.rows]
    n = len(attempt_ids)
    questions = list(exam.questions.order_by("sort_order", "id"))

    distribution: dict[int, dict[str, int]] = defaultdict(lambda: {k: 0 for k in OPTION_KEYS})
    for item in (
        StudentAnswer.objects.filter(attempt_id__in=attempt_ids, selected_option__isnull=False)
        .values("question_id", "selected_option")
        .annotate(c=Count("id"))
        .order_by()
    ):
        distribution[item["question_id"]][item["selected_option"]] = item["c"]

    # Upper / lower 27% groups (by rank) for discrimination and key checks.
    group = int(round(n * UPPER_LOWER_FRACTION)) if n >= 4 else 0
    upper_ids = attempt_ids[:group]
    lower_ids = attempt_ids[-group:] if group else []

    def per_question_counts(ids, correct_only):
        if not ids:
            return {}
        qs = StudentAnswer.objects.filter(attempt_id__in=ids, selected_option__isnull=False)
        if correct_only:
            qs = qs.filter(selected_option=F("question__correct_option"))
        return {
            row["question_id"]: row["c"]
            for row in qs.values("question_id").annotate(c=Count("id")).order_by()
        }

    upper_correct = per_question_counts(upper_ids, True)
    lower_correct = per_question_counts(lower_ids, True)
    upper_choice: dict[int, dict[str, int]] = defaultdict(lambda: {k: 0 for k in OPTION_KEYS})
    if upper_ids:
        for item in (
            StudentAnswer.objects.filter(attempt_id__in=upper_ids, selected_option__isnull=False)
            .values("question_id", "selected_option")
            .annotate(c=Count("id"))
            .order_by()
        ):
            upper_choice[item["question_id"]][item["selected_option"]] = item["c"]

    items = []
    for number, q in enumerate(questions, start=1):
        dist = distribution[q.pk]
        correct = dist[q.correct_option]
        attempted = sum(dist.values())
        wrong = attempted - correct
        skipped = n - attempted
        pct_correct = _pct(correct, n)
        if not n:
            difficulty = "—"
        elif pct_correct >= 70:
            difficulty = "Easy"
        elif pct_correct >= 40:
            difficulty = "Medium"
        else:
            difficulty = "Hard"

        discrimination = None
        flags = []
        if group:
            discrimination = (
                Decimal(upper_correct.get(q.pk, 0) - lower_correct.get(q.pk, 0)) / group
            ).quantize(TWO)
            top_choice = max(OPTION_KEYS, key=lambda k: upper_choice[q.pk][k])
            if (
                group >= 2
                and top_choice != q.correct_option
                and upper_choice[q.pk][top_choice] > upper_correct.get(q.pk, 0)
            ):
                flags.append(f"Check answer key: most top scorers chose {top_choice}")
            if group >= 3 and discrimination < Decimal("0.10"):
                flags.append("Low discrimination")

        items.append(
            {
                "number": number,
                "question": q,
                "topic": q.topic_tag or "Untagged",
                "correct": correct,
                "wrong": wrong,
                "skipped": skipped,
                "attempted": attempted,
                "pct_correct": pct_correct,
                "pct_wrong": _pct(wrong, n),
                "pct_skipped": _pct(skipped, n),
                "distribution": [
                    {"key": k, "count": dist[k], "pct": _pct(dist[k], n), "is_key": k == q.correct_option, "text": q.get_option_text(k)}
                    for k in OPTION_KEYS
                ],
                "difficulty": difficulty,
                "discrimination": discrimination,
                "flags": flags,
            }
        )

    hardest = sorted(items, key=lambda i: (i["pct_correct"], -i["pct_wrong"]))[:5] if n else []

    topics: dict[str, dict] = {}
    for item in items:
        t = topics.setdefault(item["topic"], {"topic": item["topic"], "questions": 0, "correct": 0, "wrong": 0, "skipped": 0})
        t["questions"] += 1
        t["correct"] += item["correct"]
        t["wrong"] += item["wrong"]
        t["skipped"] += item["skipped"]
    topic_rows = []
    for t in topics.values():
        responses = t["questions"] * n
        t["pct_correct"] = _pct(t["correct"], responses)
        topic_rows.append(t)
    topic_rows.sort(key=lambda t: t["pct_correct"])

    return {"items": items, "hardest": hardest, "topics": topic_rows, "appeared": n, "group_size": group}


# ---------------------------------------------------------------------------
# Individual scorecard
# ---------------------------------------------------------------------------
def scorecard(attempt: StudentAttempt, results: ExamResults | None = None) -> dict:
    exam = attempt.exam
    results = results or ranked_results(exam)
    row = results.row_for(attempt.pk)
    questions = Question.objects.in_bulk(attempt.question_order)
    answers = {a.question_id: a for a in attempt.answers.all()}

    items, topics = [], {}
    for number, qid in enumerate(attempt.question_order, start=1):
        q = questions.get(qid)
        if q is None:
            continue
        answer = answers.get(qid)
        chosen = answer.selected_option if answer else None
        if chosen is None:
            outcome, marks = "skipped", Decimal("0.00")
        elif chosen == q.correct_option:
            outcome, marks = "correct", exam.positive_marks
        else:
            outcome, marks = "wrong", -exam.negative_marks
        order = attempt.get_option_order(qid)
        items.append(
            {
                "number": number,
                "question": q,
                "outcome": outcome,
                "marks": marks,
                "status": answer.status if answer else "NOT_VISITED",
                # Letters as the STUDENT saw them on screen.
                "your_label": POSITION_LABELS[order.index(chosen)] if chosen else None,
                "correct_label": POSITION_LABELS[order.index(q.correct_option)],
                "options": [
                    {"label": POSITION_LABELS[i], "text": q.get_option_text(k), "is_key": k == q.correct_option, "is_yours": k == chosen}
                    for i, k in enumerate(order)
                ],
            }
        )
        t = topics.setdefault(q.topic_tag or "Untagged", {"topic": q.topic_tag or "Untagged", "questions": 0, "correct": 0, "wrong": 0, "skipped": 0, "marks": Decimal("0.00")})
        t["questions"] += 1
        t[outcome] += 1
        t["marks"] += marks

    for t in topics.values():
        t["accuracy"] = _pct(t["correct"], t["correct"] + t["wrong"])

    return {
        "exam": exam,
        "attempt": attempt,
        "row": row,
        "rank": row["rank"] if row else None,
        "appeared": results.stats["appeared"],
        "stats": results.stats,
        "items": items,
        "topics": sorted(topics.values(), key=lambda t: t["topic"]),
    }
