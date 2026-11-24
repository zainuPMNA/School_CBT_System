"""
Exam-engine operations used by the student views and JSON API.

The browser never sees canonical option keys or the answer key. It works only
with on-screen positions (0-3); the server maps a position to the canonical
key through `StudentAttempt.option_orders`.
"""

from django.conf import settings
from django.utils import timezone

from .models import AnswerStatus, AttemptStatus, OPTION_KEYS, Question, StudentAnswer, StudentAttempt

POSITION_LABELS = ("A", "B", "C", "D")


class AnswerPayloadError(ValueError):
    """The client sent an answer that does not belong to this attempt."""


def finalize_if_expired(attempt: StudentAttempt, now=None) -> bool:
    """Auto-submit if the server deadline (+grace) has passed. Returns True if finished now or before."""
    if attempt.is_finished:
        return True
    if attempt.is_time_over(now):
        attempt.submit(auto=True, now=now)
        return True
    return False


def apply_answer(attempt: StudentAttempt, question_id, selected, requested_status, now=None) -> dict:
    """
    Persist one question's full state with a single UPDATE.
    `selected` is an on-screen position 0-3 or None.
    """
    now = now or timezone.now()
    try:
        qid = int(question_id)
    except (TypeError, ValueError) as exc:
        raise AnswerPayloadError("Invalid question id.") from exc
    if qid not in attempt.question_order:
        raise AnswerPayloadError("Question is not part of this attempt.")

    canonical = None
    if selected is not None:
        try:
            position = int(selected)
        except (TypeError, ValueError) as exc:
            raise AnswerPayloadError("Invalid option.") from exc
        if not 0 <= position < len(OPTION_KEYS):
            raise AnswerPayloadError("Invalid option.")
        canonical = attempt.canonical_key_at(qid, position)

    if requested_status not in AnswerStatus.values:
        raise AnswerPayloadError("Invalid status.")
    status = StudentAnswer.resolve_status(canonical, requested_status)

    updated = StudentAnswer.objects.filter(attempt=attempt, question_id=qid).update(
        selected_option=canonical, status=status, updated_at=now
    )
    if not updated:  # defensive: row missing (should never happen)
        StudentAnswer.objects.create(attempt=attempt, question_id=qid, selected_option=canonical, status=status)
    return {"question_id": qid, "selected": selected if canonical else None, "status": status}


def touch_attempt(attempt: StudentAttempt, now=None, position: int | None = None) -> None:
    """Heartbeat + resume position in one UPDATE."""
    now = now or timezone.now()
    fields = {"last_seen_at": now}
    if position is not None and 0 <= position < len(attempt.question_order):
        fields["last_question_index"] = position
    StudentAttempt.objects.filter(pk=attempt.pk).update(**fields)


def build_exam_page(attempt: StudentAttempt) -> tuple[list[dict], list[dict]]:
    """
    Return (items, client_state) in the student's persisted order.
      items         -> rendered server-side (text, image, options in shuffled order)
      client_state  -> JSON for Alpine: id, status, selected position
    """
    questions = Question.objects.in_bulk(attempt.question_order)
    answers = {a.question_id: a for a in attempt.answers.all()}
    items, state = [], []
    for qid in attempt.question_order:
        question = questions.get(qid)
        if question is None:  # deleted after the attempt began
            continue
        order = attempt.get_option_order(qid)
        options = [
            {"position": pos, "label": POSITION_LABELS[pos], "text": question.get_option_text(key)}
            for pos, key in enumerate(order)
        ]
        answer = answers.get(qid)
        selected = (
            attempt.display_index_of(qid, answer.selected_option)
            if answer is not None and answer.selected_option
            else None
        )
        items.append({"index": len(items), "number": len(items) + 1, "question": question, "options": options})
        state.append(
            {
                "id": qid,
                "status": answer.status if answer else AnswerStatus.NOT_VISITED,
                "selected": selected,
            }
        )
    return items, state


def status_summary(attempt: StudentAttempt) -> dict[str, int]:
    counts = {status: 0 for status in AnswerStatus.values}
    for status in attempt.answers.values_list("status", flat=True):
        counts[status] += 1
    counts["TOTAL"] = sum(counts[s] for s in AnswerStatus.values)
    return counts


def violation_limit_reached(attempt: StudentAttempt) -> bool:
    limit = settings.CBT_MAX_VIOLATIONS
    return bool(limit) and attempt.violation_count >= limit and attempt.status == AttemptStatus.IN_PROGRESS
