"""execution/update_synthflow_inbound_prompt.py: dry run by default, backup, verification, restore, and the outbound safeguards."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

_PATH = Path(__file__).resolve().parents[2] / "execution" / "update_synthflow_inbound_prompt.py"
_spec = importlib.util.spec_from_file_location("update_inbound_prompt", _PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

OLD = "# Old inbound prompt\nline one\nline two"


class FakeSynthflow:
    """An in-memory assistant behind httpx.MockTransport. `drift` makes a PUT also change another agent setting."""

    def __init__(self, prompt=OLD, name="Cora Inbound", drift=False, accept=True):
        self.assistant = {"name": name, "agent": {"prompt": prompt, "voice_id": "v1", "greeting_message": "Hi"}}
        self.drift, self.accept, self.puts = drift, accept, []

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret-key"
        if request.method == "GET":
            return httpx.Response(200, json={"status": "success", "response": {"assistants": [self.assistant]}})
        body = json.loads(request.content)
        self.puts.append(body)
        if not self.accept:
            return httpx.Response(400, json={"detail": "bad"})
        self.assistant["agent"]["prompt"] = body["agent"]["prompt"]
        if self.drift:
            self.assistant["agent"]["greeting_message"] = ""
        return httpx.Response(200, json={"status": "success"})


def _api(fake):
    return M.Synthflow("secret-key", httpx.Client(transport=httpx.MockTransport(fake.handler)))


def _args(tmp_path, **kw):
    base = dict(assistant_id="inb1", apply=False, restore=None, force=False, backup_dir=str(tmp_path))
    return argparse.Namespace(**{**base, **kw})


def _run(args, fake, outbound_id="out1"):
    lines = []
    code = M.run(args, _api(fake), outbound_id, out=lines.append)
    return code, "\n".join(lines)


def test_dry_run_changes_nothing_and_says_so(tmp_path):
    fake = FakeSynthflow()
    code, out = _run(_args(tmp_path), fake)
    assert code == 0 and fake.puts == [] and "DRY RUN" in out and "Cora Inbound" in out
    assert list(tmp_path.iterdir()) == []                                  # no backup written for a dry run


def test_apply_backs_up_sends_only_the_prompt_and_verifies(tmp_path):
    fake = FakeSynthflow()
    code, out = _run(_args(tmp_path, apply=True), fake)
    assert code == 0 and "DONE" in out
    assert fake.puts == [{"agent": {"prompt": M.build_prompt()}}]            # nothing but agent.prompt is sent
    backup = json.loads(next(tmp_path.iterdir()).read_text(encoding="utf-8"))
    assert backup["prompt"] == OLD and backup["assistant_id"] == "inb1"


def test_identical_prompt_is_a_no_op(tmp_path):
    fake = FakeSynthflow(prompt=M.build_prompt())
    code, out = _run(_args(tmp_path, apply=True), fake)
    assert code == 0 and fake.puts == [] and "identical" in out


def test_other_agent_settings_changing_during_the_update_is_reported_as_a_failure(tmp_path):
    fake = FakeSynthflow(drift=True)
    code, out = _run(_args(tmp_path, apply=True), fake)
    assert code == 1 and "agent.greeting_message" in out and "--restore" in out


def test_restore_puts_the_saved_prompt_back(tmp_path):
    fake = FakeSynthflow()
    _run(_args(tmp_path, apply=True), fake)
    backup = next(tmp_path.iterdir())
    code, out = _run(_args(tmp_path, restore=str(backup)), fake)
    assert code == 0 and fake.assistant["agent"]["prompt"] == OLD


def test_restore_refuses_a_backup_from_another_assistant(tmp_path):
    f = tmp_path / "b.json"
    f.write_text(json.dumps({"assistant_id": "someone-else", "prompt": "x"}), encoding="utf-8")
    code, out = _run(_args(tmp_path, restore=str(f)), FakeSynthflow())
    assert code == 2 and "different assistant" in out


def test_the_shared_outbound_assistant_is_never_overwritten(tmp_path):
    fake = FakeSynthflow()
    code, out = _run(_args(tmp_path, assistant_id="out1", apply=True), fake, outbound_id="out1")
    assert code == 2 and "OUTBOUND" in out and fake.puts == []


def test_an_assistant_whose_prompt_is_the_placeholder_is_refused(tmp_path):
    fake = FakeSynthflow(prompt="{prompt}")
    code, out = _run(_args(tmp_path, apply=True), fake)
    assert code == 2 and fake.puts == []
    code, _ = _run(_args(tmp_path, apply=True, force=True), fake)
    assert code == 0 and len(fake.puts) == 1                                # --force is the only way past it


def test_http_error_from_synthflow_raises_without_leaking_the_key(tmp_path):
    fake = FakeSynthflow(accept=False)
    with pytest.raises(RuntimeError) as e:
        _run(_args(tmp_path, apply=True), fake)
    assert "400" in str(e.value) and "secret-key" not in str(e.value)


# -- pure helpers --------------------------------------------------------------------------------------------------
def test_build_prompt_starts_at_the_first_section_and_has_the_failed_transfer_rule():
    p = M.build_prompt()
    assert p.startswith("## 1.1 ROLE & CONTEXT") and "Engineering note" not in p and "Call Type:" not in p
    assert "## 1.2 HARD RULE" in p and "Check office hours first" in p


def test_the_inbound_doc_never_reintroduces_the_banned_word_in_prose():
    """The live inbound agent says "at no cost"; the doc must not push "free" (outside the /start-free link) back into it."""
    import re
    prose = re.sub(r"(?i)training\.colaberry\.com/start-free", "", M.build_prompt()).replace("feel free", "")
    assert not re.search(r"(?i)\bfree\b", prose)


def test_extract_assistant_handles_the_common_envelopes():
    a = {"name": "n", "agent": {"prompt": "p"}}
    for resp in ({"response": {"assistants": [a]}}, {"response": {"assistant": a}}, {"response": a}, a):
        assert M.extract_assistant(resp)["name"] == "n"
    with pytest.raises(ValueError):
        M.extract_assistant({"unexpected": 1})


def test_changed_keys_ignores_the_prompt_and_timestamps():
    a = {"name": "n", "updated_at": "1", "agent": {"prompt": "old", "voice_id": "v"}}
    b = {"name": "n", "updated_at": "2", "agent": {"prompt": "new", "voice_id": "v"}}
    assert M.changed_keys(a, b) == []
    assert M.changed_keys(a, {**b, "agent": {"prompt": "new", "voice_id": "w"}}) == ["agent.voice_id"]


def test_list_shows_id_name_and_flags_the_outbound_placeholder_assistant(tmp_path):
    rows = [{"model_id": "inb1", "name": "Cora Inbound", "agent": {"prompt": "long prompt " * 10}},
            {"model_id": "out1", "name": "Cora Outbound", "agent": {"prompt": "{prompt}"}}]

    def handler(req):
        return httpx.Response(200, json={"status": "success", "response": {"assistants": rows}})
    api = M.Synthflow("secret-key", httpx.Client(transport=httpx.MockTransport(handler)))
    lines = []
    assert M.run(argparse.Namespace(list=True, assistant_id=None), api, None, out=lines.append) == 0
    assert "inb1 | Cora Inbound | 119-char prompt" in lines[0]
    assert "out1 | Cora Outbound" in lines[1] and "OUTBOUND (never update)" in lines[1]
    assert not any("secret-key" in ln for ln in lines)
