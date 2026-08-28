"""The console.

Two things matter here and the rest is presentation.

First, the graph must not invent a timeline. A run recorded before call timing existed
has every offset at zero, and drawing that as "everything happened at once, in 13 seconds"
would be a confident lie about the one number an audience is told to trust.

Second, and more important: the console must hold no send authority of its own. The
approve endpoint is only ever allowed to reach the operator's `gh` credential, and the
write routes must refuse anything that did not come from the console page itself.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cos.console import gates, graph


def _write_run(runs_dir: Path, run_id: str, rows: list[dict[str, object]]) -> None:
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / f"{run_id}.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


@pytest.fixture
def runs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "runs"
    directory.mkdir()
    monkeypatch.setattr(graph, "RUNS_DIR", directory)
    monkeypatch.setattr(graph, "run_brief_path", lambda run_id: directory / f"{run_id}.brief.json")
    return directory


def _row(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "run_id": "r1",
        "agent": "mail-triage",
        "model": "gpt-5.4-mini",
        "model_version": "2026-03-17",
        "prompt_hash": "abc",
        "input_tokens": 100,
        "output_tokens": 50,
        "latency_ms": 1000,
        "attempt": 1,
        "validation_error": None,
        "stage": "ingest",
        "parent": "chief-of-staff",
        "label": "3 item(s)",
        "started_ms": 0,
    }
    base.update(kw)
    return base


class TestGraph:
    def test_orchestrator_appears_even_though_it_never_calls_a_model(self, runs_dir: Path) -> None:
        _write_run(runs_dir, "r1", [_row()])
        built = graph.build("r1")
        ids = [n["id"] for n in built["nodes"]]
        assert graph.ROOT in ids
        root = next(n for n in built["nodes"] if n["id"] == graph.ROOT)
        assert root["calls"] == 0

    def test_edges_carry_the_delegation(self, runs_dir: Path) -> None:
        _write_run(
            runs_dir,
            "r1",
            [
                _row(agent="mail-triage", started_ms=10),
                _row(agent="chat-triage", started_ms=12),
                _row(
                    agent="consolidator",
                    parent="chief-of-staff",
                    stage="consolidate",
                    started_ms=200,
                ),
                # A split: the consolidator re-asking its own groups.
                _row(
                    agent="consolidator",
                    parent="consolidator",
                    stage="consolidate",
                    started_ms=400,
                ),
            ],
        )
        built = graph.build("r1")
        edges = {(e["source"], e["target"]): e["count"] for e in built["edges"]}
        assert edges[("chief-of-staff", "mail-triage")] == 1
        assert edges[("consolidator", "consolidator")] == 1, "self-delegation must be visible"

    def test_untimed_runs_report_no_wall_clock_rather_than_a_wrong_one(
        self, runs_dir: Path
    ) -> None:
        # Three calls, no offsets — the shape of every run recorded before the console.
        _write_run(
            runs_dir,
            "r1",
            [_row(latency_ms=1000), _row(latency_ms=2000), _row(latency_ms=3000)],
        )
        built = graph.build("r1")
        assert built["has_timing"] is False
        assert built["totals"]["wall_ms"] is None
        assert built["totals"]["sequential_ms"] == 6000

    def test_timed_runs_report_wall_clock_not_the_sum(self, runs_dir: Path) -> None:
        # Three concurrent ingest calls. Summing them would claim 6s for a 3s run.
        _write_run(
            runs_dir,
            "r1",
            [
                _row(agent="mail-triage", started_ms=100, latency_ms=3000),
                _row(agent="chat-triage", started_ms=110, latency_ms=2000),
                _row(agent="calendar-context", started_ms=120, latency_ms=1000),
            ],
        )
        built = graph.build("r1")
        assert built["has_timing"] is True
        assert built["totals"]["wall_ms"] == 3100
        assert built["totals"]["sequential_ms"] == 6000

    def test_a_malformed_line_does_not_blank_the_run(self, runs_dir: Path) -> None:
        path = runs_dir / "r1.jsonl"
        path.write_text(json.dumps(_row()) + "\nnot json at all\n" + json.dumps(_row()) + "\n")
        assert graph.build("r1")["totals"]["calls"] == 2

    def test_retries_are_counted_and_flagged(self, runs_dir: Path) -> None:
        _write_run(
            runs_dir,
            "r1",
            [_row(attempt=1, validation_error="bad"), _row(attempt=2, started_ms=50)],
        )
        built = graph.build("r1")
        assert built["totals"]["retries"] == 1
        assert [c["failed_validation"] for c in built["calls"]] == [True, False]

    def test_missing_run_is_empty_not_an_error(self, runs_dir: Path) -> None:
        built = graph.build("nope")
        assert built["totals"]["calls"] == 0
        assert built["nodes"][0]["id"] == graph.ROOT


class TestNoSendAuthority:
    """The console must never be able to authorise a send on its own."""

    def test_every_gate_action_goes_through_the_operators_gh(self) -> None:
        # If this ever stops being true, the console has grown an authority of its own.
        source = Path(gates.__file__).read_text()
        assert "import requests" not in source
        assert "httpx" not in source
        for forbidden in ("GITHUB_TOKEN", "GH_TOKEN", "Authorization"):
            assert forbidden not in source, (
                f"gates.py references {forbidden}: the console must borrow the operator's "
                "gh credential, never carry one."
            )

    def test_approve_shells_out_as_the_user(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[list[str]] = []

        def fake_gh(*args: str) -> str:
            seen.append(list(args))
            return ""

        monkeypatch.setattr(gates, "_gh", fake_gh)
        gates.approve("owner/repo", 42, 7, "ok")

        assert seen, "approve must call gh"
        call = seen[0]
        assert call[0] == "api" and "--method" in call and "POST" in call
        assert "repos/owner/repo/actions/runs/42/pending_deployments" in call
        assert "state=approved" in call

    def test_gate_failures_are_reported_not_swallowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*args: str) -> str:
            raise gates.GateError("gh: not authenticated")

        monkeypatch.setattr(gates, "_gh", boom)
        monkeypatch.setattr(gates, "_gh_json", boom)
        result = gates.state("owner/repo")
        assert result["error"] == "gh: not authenticated"
        assert result["pulls"] == [] and result["waiting"] == []


class TestServerGuards:
    def test_write_routes_require_the_console_header_and_loopback(self) -> None:
        from cos.console import server

        source = Path(server.__file__).read_text()
        # Both locks must exist on the POST path: a Host check against loopback, which
        # is what stops DNS rebinding, and a header a cross-origin form cannot set.
        assert "X-Console" in source
        assert "ALLOWED_HOSTS" in source
        assert "127.0.0.1" in server.ALLOWED_HOSTS or "localhost" in server.ALLOWED_HOSTS
