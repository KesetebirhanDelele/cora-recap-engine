"""Spec/49 step 2 — job flag defaults off, settings present, models + migration chain sane."""
from __future__ import annotations

import pathlib

from app.config.settings import Settings
from app.core.mode_flags import _safe_shadow_defaults
from app.models.explorer_queue import ExplorerQueueContact, ExplorerQueueRun


def test_job_flag_defaults_off_in_settings():
    s = Settings(_env_file=None)
    assert s.explorer_queue_tagging_enabled is False


def test_job_flag_defaults_off_in_safe_defaults():
    assert _safe_shadow_defaults().explorer_queue_tagging_enabled is False
    assert _safe_shadow_defaults().as_dict()["explorer_queue_tagging_enabled"] is False


def test_field_ids_configured():
    s = Settings(_env_file=None)
    assert s.explorer_queue_score_field_id == "UYv8i2BnHkLOJyhc08mR"
    assert s.explorer_queue_ai_summary_field_id == "3yYPrP3zgbG2J5T1jf83"
    assert s.explorer_queue_batch_cap == 200


def test_models_have_expected_tables_and_keys():
    assert ExplorerQueueRun.__tablename__ == "explorer_queue_runs"
    assert ExplorerQueueContact.__tablename__ == "explorer_queue_contacts"
    assert [c.name for c in ExplorerQueueContact.__table__.primary_key.columns] == ["ghl_contact_id"]
    assert "tagged_at" in ExplorerQueueContact.__table__.c
    assert "drained_at" in ExplorerQueueContact.__table__.c


def test_migration_0031_chains_from_0030():
    # Read as text: alembic is not installed in every dev environment.
    path = pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0031_explorer_queue.py"
    text = path.read_text(encoding="utf-8")
    assert 'revision = "0031"' in text and 'down_revision = "0030"' in text
    assert "def upgrade" in text and "def downgrade" in text
    assert "explorer_queue_runs" in text and "explorer_queue_contacts" in text
