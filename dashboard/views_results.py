"""Results, publishing, question-wise analytics and exports (staff side)."""

import logging

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import TemplateView

from exams.analytics import question_analysis, ranked_results, scorecard
from exams.models import Exam

from .exports import pdf_response, results_csv_response, results_workbook_response, scorecards_pdf
from .mixins import AdminRequiredMixin, StaffRequiredMixin

logger = logging.getLogger("cbt")


class ExamResultsView(StaffRequiredMixin, TemplateView):
    template_name = "dashboard/results/exam.html"
    nav_section = "exams"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        exam = get_object_or_404(Exam, pk=self.kwargs["pk"])
        results = ranked_results(exam)
        analysis = question_analysis(exam, results)
        ctx.update(
            exam=exam,
            results=results,
            stats=results.stats,
            hardest=analysis["hardest"],
            topics=analysis["topics"],
            flagged=[i for i in analysis["items"] if i["flags"]],
        )
        return ctx


class QuestionAnalysisView(StaffRequiredMixin, TemplateView):
    template_name = "dashboard/results/questions.html"
    nav_section = "exams"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        exam = get_object_or_404(Exam, pk=self.kwargs["pk"])
        analysis = question_analysis(exam)
        sort = self.request.GET.get("sort", "order")
        items = analysis["items"]
        if sort == "hardest":
            items = sorted(items, key=lambda i: (i["pct_correct"], -i["pct_wrong"]))
        elif sort == "easiest":
            items = sorted(items, key=lambda i: -i["pct_correct"])
        ctx.update(exam=exam, analysis=analysis, items=items, sort=sort)
        return ctx


class PublishResultsView(AdminRequiredMixin, View):
    def post(self, request, pk):
        exam = get_object_or_404(Exam, pk=pk)
        finalized = exam.publish_results(by=request.user)
        logger.info("Results published for exam %s by %s (%d auto-submitted).", exam.pk, request.user.username, finalized)
        extra = f" {finalized} unfinished attempt(s) were auto-submitted." if finalized else ""
        messages.success(request, f"Results published — students can now see their scores.{extra}")
        return redirect("dashboard:exam_results", pk=exam.pk)


class UnpublishResultsView(AdminRequiredMixin, View):
    def post(self, request, pk):
        exam = get_object_or_404(Exam, pk=pk)
        exam.unpublish_results()
        messages.warning(request, "Results hidden from students again.")
        return redirect("dashboard:exam_results", pk=exam.pk)


class ExportResultsView(StaffRequiredMixin, View):
    def get(self, request, pk, fmt):
        exam = get_object_or_404(Exam, pk=pk)
        results = ranked_results(exam)
        if fmt == "xlsx":
            return results_workbook_response(exam, results, question_analysis(exam, results))
        if fmt == "csv":
            return results_csv_response(exam, results)
        raise Http404("Unknown export format")


class ScorecardsPdfView(StaffRequiredMixin, View):
    """All finished students in rank order, or ?attempt=<id> for one student."""

    def get(self, request, pk):
        exam = get_object_or_404(Exam, pk=pk)
        results = ranked_results(exam)
        rows = results.rows
        attempt_id = request.GET.get("attempt")
        if attempt_id:
            rows = [r for r in rows if str(r["attempt"].pk) == attempt_id]
            if not rows:
                raise Http404("No finished attempt with that id")
        if not rows:
            messages.error(request, "No submitted attempts yet — nothing to print.")
            return redirect("dashboard:exam_results", pk=exam.pk)
        cards = [scorecard(r["attempt"], results) for r in rows]
        name = f"{exam.title}_{rows[0]['username']}_scorecard" if attempt_id else f"{exam.title}_scorecards"
        return pdf_response(scorecards_pdf(cards), name, inline=bool(attempt_id))
