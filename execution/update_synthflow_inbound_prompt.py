"""
Update the Synthflow INBOUND agent's prompt from docs/synthflow-inbound-prompt.md.

The inbound agent keeps its prompt inside Synthflow (the cold / warm outbound prompts are injected per call instead), so a change to
the doc has to be pushed there. This script does that safely:

  - dry run by default: shows the agent's name, the size of the current and new prompt, and a diff summary; changes nothing
  - --apply: saves the current prompt to a backup file, sends ONLY `agent.prompt`, reads the agent back, checks the prompt matches
    and that no other agent setting changed (Synthflow's docs do not say how a nested partial update behaves, so this is verified)
  - --restore FILE: puts a saved prompt back
  - --list: read-only list of the assistants (id, name) so the inbound agent's id is easy to find
  - refuses to touch the shared OUTBOUND assistant (SYNTHFLOW_MODEL_ID, if set) or any assistant whose stored prompt is just "{prompt}"
    (that is the outbound shape, where Cora injects the prompt per call) unless --force

The API key is read from the environment / .env by the app settings and is never printed.

Usage (run inside the worker container so the key is available):

    docker compose exec worker-default python execution/update_synthflow_inbound_prompt.py --list                          # find the id
    docker compose exec worker-default python execution/update_synthflow_inbound_prompt.py --assistant-id <ID>            # dry run
    docker compose exec worker-default python execution/update_synthflow_inbound_prompt.py --assistant-id <ID> --apply
    docker compose exec worker-default python execution/update_synthflow_inbound_prompt.py --assistant-id <ID> --restore /tmp/inbound_prompt_backup_<ts>.json

The assistant id is in the Synthflow dashboard URL of the inbound agent (or SYNTHFLOW_INBOUND_MODEL_ID).
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

API = "https://api.synthflow.ai/v2/assistants"
DOC = _ROOT / "docs" / "synthflow-inbound-prompt.md"
OUTBOUND_SHAPE = "{prompt}"


def build_prompt(path: Path = DOC) -> str:
    """The prompt text: the doc from its first `##` section on. The title block and engineering note above it are repo-only, and the
    inbound agent's stored prompt starts at its first section (unlike the outbound loader, which keeps the `#` title)."""
    text = path.read_text(encoding="utf-8")
    idx = text.find("\n## ")
    return (text[idx + 1:] if idx != -1 else text).strip()


def extract_assistant(resp: Any) -> dict[str, Any]:
    """The assistant object from a GET response, whatever envelope Synthflow wraps it in."""
    if isinstance(resp, dict):
        inner = resp.get("response", resp)
        if isinstance(inner, dict):
            if isinstance(inner.get("assistants"), list) and inner["assistants"]:
                return inner["assistants"][0]
            if isinstance(inner.get("assistant"), dict):
                return inner["assistant"]
            if "agent" in inner or "name" in inner:
                return inner
    raise ValueError("could not find the assistant in Synthflow's response")


def current_prompt(assistant: dict[str, Any]) -> str:
    agent = assistant.get("agent")
    return str((agent or {}).get("prompt") or "")


def diff_summary(old: str, new: str, max_lines: int = 40) -> str:
    if old == new:
        return "identical - nothing to change"
    diff = list(difflib.unified_diff(old.splitlines(), new.splitlines(), "current", "new", lineterm="", n=0))
    added = sum(1 for d in diff if d.startswith("+") and not d.startswith("+++"))
    removed = sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))
    shown = "\n".join(diff[:max_lines])
    more = f"\n... ({len(diff) - max_lines} more diff lines)" if len(diff) > max_lines else ""
    return f"{added} line(s) added, {removed} removed\n{shown}{more}"


def changed_keys(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Settings (top level and inside `agent`, except the prompt) whose value differs between two reads of the assistant."""
    def flat(a: dict[str, Any]) -> dict[str, str]:
        out = {k: json.dumps(v, sort_keys=True, default=str) for k, v in a.items() if k not in ("agent", "updated_at", "modified_at")}
        for k, v in (a.get("agent") or {}).items():
            if k != "prompt":
                out[f"agent.{k}"] = json.dumps(v, sort_keys=True, default=str)
        return out
    b, a = flat(before), flat(after)
    return sorted(k for k in set(b) | set(a) if b.get(k) != a.get(k))


def refusal(assistant: dict[str, Any], assistant_id: str, outbound_id: str | None, force: bool) -> str | None:
    """Why this assistant must not be overwritten, or None."""
    if outbound_id and assistant_id == outbound_id and not force:
        return "this is the shared OUTBOUND assistant (SYNTHFLOW_MODEL_ID); its prompt is injected per call - refusing"
    if current_prompt(assistant).strip() == OUTBOUND_SHAPE and not force:
        return 'its stored prompt is just "{prompt}" (the outbound shape, filled per call by Cora) - refusing'
    return None


class Synthflow:
    def __init__(self, api_key: str, client: httpx.Client | None = None):
        self._c = client or httpx.Client(timeout=30.0)
        self._h = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"}

    def _check(self, r: httpx.Response) -> httpx.Response:
        if r.status_code >= 400:
            raise RuntimeError(f"Synthflow HTTP {r.status_code}: {r.text[:300]}")
        return r

    def get(self, assistant_id: str) -> dict[str, Any]:
        return extract_assistant(self._check(self._c.get(f"{API}/{assistant_id}", headers=self._h)).json())

    def list(self) -> list[dict[str, Any]]:
        inner = self._check(self._c.get(API, headers=self._h, params={"limit": 100})).json()
        inner = inner.get("response", inner) if isinstance(inner, dict) else {}
        rows = inner.get("assistants") if isinstance(inner, dict) else None
        return rows if isinstance(rows, list) else []

    def set_prompt(self, assistant_id: str, prompt: str) -> None:
        self._check(self._c.put(f"{API}/{assistant_id}", headers=self._h, json={"agent": {"prompt": prompt}}))


def describe(a: dict[str, Any]) -> str:
    """One line per assistant for --list: id, name, prompt size, and whether it looks like the outbound shape."""
    p = current_prompt(a).strip()
    shape = 'prompt is just "{prompt}" -> OUTBOUND (never update)' if p == OUTBOUND_SHAPE else f"{len(p)}-char prompt"
    return f"{a.get('model_id') or a.get('id') or '?'} | {a.get('name') or '(unnamed)'} | {shape}"


def run(args: argparse.Namespace, api: Synthflow, outbound_id: str | None, out=print) -> int:
    if getattr(args, "list", False):
        for a in api.list():
            out(describe(a))
        return 0
    assistant = api.get(args.assistant_id)
    name = assistant.get("name") or "(unnamed)"
    old = current_prompt(assistant)
    why = refusal(assistant, args.assistant_id, outbound_id, args.force)
    out(f"Assistant: {name} ({args.assistant_id})")
    if why:
        out(f"REFUSED: {why}")
        return 2

    if args.restore:
        saved = json.loads(Path(args.restore).read_text(encoding="utf-8"))
        if saved.get("assistant_id") != args.assistant_id:
            out("REFUSED: that backup is for a different assistant")
            return 2
        new = saved["prompt"]
    else:
        new = build_prompt()

    out(f"Current prompt: {len(old)} chars | new prompt: {len(new)} chars")
    out(diff_summary(old, new))
    if old == new:
        return 0
    if not (args.apply or args.restore):
        out("\nDRY RUN - nothing was changed. Re-run with --apply to update the agent.")
        return 0

    backup = Path(args.backup_dir) / f"inbound_prompt_backup_{datetime.now(tz=timezone.utc):%Y%m%dT%H%M%SZ}.json"
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_text(json.dumps({"assistant_id": args.assistant_id, "name": name, "prompt": old}), encoding="utf-8")
    out(f"Backed up the current prompt to {backup}")

    api.set_prompt(args.assistant_id, new)
    after = api.get(args.assistant_id)
    if current_prompt(after) != new:
        out(f"FAILED: the agent's prompt does not match what was sent. Restore with --restore {backup}")
        return 1
    others = changed_keys(assistant, after)
    if others:
        out(f"WARNING: other agent settings changed during the update: {', '.join(others)}. Check them in Synthflow, or restore with --restore {backup}")
        return 1
    out("DONE: prompt updated and verified; no other agent setting changed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--assistant-id", default=os.environ.get("SYNTHFLOW_INBOUND_MODEL_ID"), help="the INBOUND agent's id (see --list)")
    ap.add_argument("--list", action="store_true", help="read-only: list the Synthflow assistants (id, name) to find the inbound one")
    ap.add_argument("--apply", action="store_true", help="really update the agent (default is a dry run)")
    ap.add_argument("--restore", metavar="FILE", help="put back a prompt saved by an earlier --apply")
    ap.add_argument("--force", action="store_true", help="skip the outbound-assistant safeguards (not recommended)")
    ap.add_argument("--backup-dir", default="/tmp", help="where --apply saves the current prompt (default /tmp)")
    args = ap.parse_args(argv)
    if not args.assistant_id and not args.list:
        ap.error("--assistant-id is required (or set SYNTHFLOW_INBOUND_MODEL_ID)")

    from app.config import get_settings
    s = get_settings()
    if not s.synthflow_api_key:
        print("SYNTHFLOW_API_KEY is not set")
        return 2
    return run(args, Synthflow(s.synthflow_api_key), s.synthflow_model_id)


if __name__ == "__main__":
    sys.exit(main())
