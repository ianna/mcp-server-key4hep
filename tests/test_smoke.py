import json
from types import SimpleNamespace

import pytest

from mcp_server_key4hep.smoke import call, exercise, replay


class Session:
    def __init__(self, states, valid=True):
        self.states = iter(states)
        self.calls = []
        self.valid = valid

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "run_pythia8_generation":
            data = {"job_id": "test-job", "directory": "/test/output"}
        elif name == "get_job_status":
            data = {"status": next(self.states)}
        else:
            data = {"valid": self.valid}
        return SimpleNamespace(isError=False, structuredContent=data, content=[])


@pytest.fixture
def spec():
    return {
        "process_name": "smoke",
        "nevents": 10,
        "random_seed": 42,
        "ecm_gev": 91.2,
        "cvmfs_release": "2026-04-08",
        "steering_path": "examples/pythia.py",
        "cmd_card_path": "examples/ee_mumu.cmd",
    }


async def test_complete_smoke_protocol(spec):
    session = Session(["QUEUED", "RUNNING", "VALIDATING", "SUCCESS"])
    assert await exercise(session, spec, 10, poll_seconds=0) == 0
    names = [name for name, _ in session.calls]
    assert names[:2] == ["validate_steering_config", "run_pythia8_generation"]
    assert names[-2:] == ["verify_provenance", "validate_edm4hep_file"]
    assert "cancel_job" not in names
    assert session.calls[1][1]["random_seed"] == 42


async def test_failed_job_has_nonzero_result(spec):
    session = Session(["FAILED"])
    assert await exercise(session, spec, 10) == 1
    assert "verify_provenance" not in [name for name, _ in session.calls]


async def test_timeout_cancels_job(spec):
    session = Session(["RUNNING"])
    with pytest.raises(TimeoutError):
        await exercise(session, spec, 0.01, poll_seconds=1)
    assert session.calls[-1] == ("cancel_job", {"job_id": "test-job"})


async def test_verification_failure_is_not_success(spec):
    session = Session(["SUCCESS"], valid=False)
    with pytest.raises(RuntimeError, match="Provenance verification failed"):
        await exercise(session, spec, 10)


async def test_tool_error_stops_workflow(spec):
    class ErrorSession:
        async def call_tool(self, name, arguments):
            return SimpleNamespace(
                isError=True, content=[SimpleNamespace(text="Commit input first")]
            )

    with pytest.raises(RuntimeError, match="Commit input first"):
        await exercise(ErrorSession(), spec, 10)


async def test_text_result_compatibility():
    class TextSession:
        async def call_tool(self, name, arguments):
            return SimpleNamespace(
                isError=False,
                structuredContent=None,
                content=[SimpleNamespace(text=json.dumps({"valid": True}))],
            )

    assert await call(TextSession(), "example", {}) == {"valid": True}


@pytest.mark.parametrize("identical", [True, False])
async def test_replay_runs_identical_requests_and_checks_result(spec, identical):
    class ReplaySession(Session):
        def __init__(self):
            super().__init__(["SUCCESS", "SUCCESS"])
            self.number = 0

        async def call_tool(self, name, arguments):
            result = await super().call_tool(name, arguments)
            if name == "run_pythia8_generation":
                self.number += 1
                result.structuredContent["job_id"] = f"job-{self.number}"
            elif name == "compare_event_content":
                result.structuredContent["identical"] = identical
            return result

    session = ReplaySession()
    assert await replay(session, spec, 10, poll_seconds=0) == (0 if identical else 1)
    submissions = [args for name, args in session.calls if name == "run_pythia8_generation"]
    assert len(submissions) == 2 and submissions[0] == submissions[1] == spec
    assert session.calls[-1] == (
        "compare_event_content",
        {"left_job_id": "job-1", "right_job_id": "job-2"},
    )


async def test_failed_first_replay_run_stops(spec):
    session = Session(["FAILED"])
    assert await replay(session, spec, 10) == 1
    assert sum(name == "run_pythia8_generation" for name, _ in session.calls) == 1
