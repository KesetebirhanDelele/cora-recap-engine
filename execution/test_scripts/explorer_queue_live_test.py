"""
Explorer call queue — live proof on 5 fake test contacts (spec/49 step 7).

Writes to the PRODUCTION GHL location, but ONLY to 5 contacts this script creates
itself: named fake_one..fake_five, NO phone, NO email, tagged ent-test-delete.
Before any write, every id is re-read from GHL and must still be a fake_* contact
carrying ent-test-delete — otherwise the script stops. Sends nothing; no LLM calls.

State (contact ids, a throwaway SQLite ledger) lives in tmp/explorer_queue_test/ (gitignored).

Usage (from repo root):
    python execution/test_scripts/explorer_queue_live_test.py create     # make the 5 contacts
    python execution/test_scripts/explorer_queue_live_test.py dry        # dry run, writes nothing
    python execution/test_scripts/explorer_queue_live_test.py apply      # tag + Score + AI Summary
    python execution/test_scripts/explorer_queue_live_test.py verify     # read back from GHL
    python execution/test_scripts/explorer_queue_live_test.py fakecall <name>   # fake connected call (local DB only), then re-run apply
    python execution/test_scripts/explorer_queue_live_test.py ids        # list ids for manual cleanup
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.adapters.ghl import GHLClient  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.models.explorer_queue import ExplorerQueueContact, ExplorerQueueRun  # noqa: E402
from app.models.staff_call_quality import StaffCallQuality  # noqa: E402
from app.services.explorer_queue import run_explorer_queue_cycle  # noqa: E402
from app.services.explorer_queue_feed import FixtureFeed  # noqa: E402

WORK = ROOT / "tmp" / "explorer_queue_test"
IDS_FILE = WORK / "contacts.json"
FEED_FILE = WORK / "feed.json"
DB_URL = f"sqlite:///{(WORK / 'ledger.db').as_posix()}"
TEST_TAG = "ent-test-delete"

# (name, primary_state, score, ai_summary) — test-only stage mapping, NOT Ali's.
SPECS = [
    ("fake_one", "new_explorer", 52, "Opened the portal twice this week and finished one tour. Asked about start dates."),
    ("fake_two", "activating", 31, "Signed up 5 days ago, viewed 3 cards, no call yet."),
    ("fake_three", "active_learner", 12, None),
    ("fake_four", "engaged_learner", 44, "Active every day this week; strongest candidate for a call."),
    ("fake_five", "new_explorer", 25, "Joined Monday, one login. Borderline score."),
]
STAGE_MAP = {
    "new_explorer": "ent-stage-new-explorer", "activating": "ent-stage-activating",
    "active_learner": "ent-stage-active-learner", "engaged_learner": "ent-stage-engaged-learner",
}
FLAGS_LIVE = SimpleNamespace(explorer_queue_tagging_enabled=True, ghl_writes_enabled=True)


def _load_ids() -> dict[str, str]:
    return json.loads(IDS_FILE.read_text(encoding="utf-8"))


def _guard(client: GHLClient, ids: dict[str, str]) -> None:
    """Refuse to touch anything that is not one of our fake test contacts."""
    for name, cid in ids.items():
        c = client.get_contact(cid)
        c = c.get("contact", c)
        tags = {str(t).lower() for t in c.get("tags") or []}
        got = (c.get("firstName") or "") + (c.get("lastName") or "")
        if not str(c.get("firstName") or "").startswith("fake_") or TEST_TAG not in tags or c.get("phone") or c.get("email"):
            sys.exit(f"STOP: {name} ({cid}) is not a clean fake test contact (name={got!r}). Nothing written.")


def _settings(location_id: str):
    s = get_settings()
    return s.model_copy(update={
        "explorer_queue_stage_map": json.dumps(STAGE_MAP),
        "explorer_queue_view": "reach_out",
        "explorer_queue_feed_path": str(FEED_FILE),
    }) if hasattr(s, "model_copy") else s


def _session():
    WORK.mkdir(parents=True, exist_ok=True)
    engine = create_engine(DB_URL)
    Base.metadata.create_all(engine, tables=[
        ExplorerQueueRun.__table__, ExplorerQueueContact.__table__, StaffCallQuality.__table__,
    ])
    return sessionmaker(bind=engine)()


def cmd_create() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    if IDS_FILE.exists():
        sys.exit(f"{IDS_FILE} already exists — contacts were already created. Run 'ids' to list them.")
    client = GHLClient()
    ids: dict[str, str] = {}
    for name, *_ in SPECS:
        res = client.create_contact(first_name=name, tags=[TEST_TAG], mode_flags=FLAGS_LIVE)
        cid = (res.get("contact") or res).get("id")
        if not cid:
            sys.exit(f"create failed for {name}: {res}")
        ids[name] = cid
        print(f"created {name} -> {cid}")
        IDS_FILE.write_text(json.dumps(ids, indent=2), encoding="utf-8")
    loc = client.settings.ghl_location_id
    rows = []
    for name, state, score, summary in SPECS:
        row = {"enrollment_id": f"test-{name}", "name": name, "primary_state": state, "score": score,
               "overlays": [], "reasons": [], "rsvps": 0,
               "ghl_url": f"https://app.gohighlevel.com/v2/location/{loc}/contacts/detail/{ids[name]}"}
        if summary:
            row["ai_summary"] = summary
        rows.append(row)
    FEED_FILE.write_text(json.dumps({"view": "reach_out", "tracked_since": "2026-11-04", "rows": rows}, indent=2), encoding="utf-8")
    print(f"feed written: {FEED_FILE}")


def _cycle(mode: str):
    ids = _load_ids()
    client = GHLClient()
    _guard(client, ids)
    settings = _settings(client.settings.ghl_location_id)
    with _session() as session, patch("app.core.mode_flags.get_mode_flags", return_value=FLAGS_LIVE):
        run = run_explorer_queue_cycle(session, settings, mode=mode, feed=FixtureFeed(FEED_FILE), client=client)
        session.commit()
        print(f"mode={mode} status={run.status} dry_run={run.dry_run} fetched={run.rows_fetched} "
              f"tagged={run.contacts_tagged} already={run.contacts_already_tagged} untagged={run.contacts_untagged} "
              f"skip_no_url={run.skipped_no_ghl_url} skip_ai={run.skipped_ai_campaign} "
              f"skip_no_access={run.skipped_no_access} failed={run.contacts_failed} err={run.error_message}")


def cmd_verify() -> None:
    ids = _load_ids()
    client = GHLClient()
    s = get_settings()
    for name, cid in ids.items():
        c = client.get_contact(cid)
        c = c.get("contact", c)
        cf = {f.get("id"): f.get("value") for f in c.get("customFields") or []}
        print(f"{name}: tags={sorted(c.get('tags') or [])}")
        print(f"    Score={cf.get(s.explorer_queue_score_field_id)!r}  AI Summary={str(cf.get(s.explorer_queue_ai_summary_field_id))[:70]!r}")


def cmd_fakecall(name: str) -> None:
    cid = _load_ids()[name]
    with _session() as session:
        session.add(StaffCallQuality(
            id=str(uuid.uuid4()), ghl_message_id=f"fake-{uuid.uuid4()}", ghl_contact_id=cid,
            call_time=datetime.now(timezone.utc), call_connected=True,
        ))
        session.commit()
    print(f"fake connected call recorded in LOCAL ledger for {name} ({cid}). Production call records untouched.")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "create":
        cmd_create()
    elif cmd in ("dry", "apply"):
        _cycle(cmd)
    elif cmd == "verify":
        cmd_verify()
    elif cmd == "fakecall" and len(sys.argv) > 2:
        cmd_fakecall(sys.argv[2])
    elif cmd == "ids":
        print(json.dumps(_load_ids(), indent=2))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
