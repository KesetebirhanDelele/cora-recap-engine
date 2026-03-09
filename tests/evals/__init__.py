"""
Shadow eval harness — Phase 5 stubs.

Full eval fixtures (transcript samples, known-good expected outputs) are
loaded in Phase 10 (integration hardening and evals). This package
documents the eval framework structure so it can be extended incrementally.

Eval philosophy (from spec/05_eval_plan.md):
  - 3-5 representative test cases per recurring logic path
  - At least 1 negative case per major path
  - Known-good expected outputs where possible
  - Regression checks after logic or prompt changes

Eval categories:
  summary  — SummaryOutput for completed calls (consent YES/NO, blank transcript)
  consent  — ConsentOutput for consent detection
  analysis — CallAnalysisOutput for lead stage classification
  vm       — VMContentOutput for voicemail content generation
"""
