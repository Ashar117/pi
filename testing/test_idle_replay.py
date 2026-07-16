"""T-136 — IdleReplayManager unit tests (mocked clock + injected callables)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.idle_replay import IdleReplayManager


class Clock:
    """Controllable monotonic clock."""
    def __init__(self, t=1000.0):
        self.t = t
    def __call__(self):
        return self.t
    def advance(self, secs):
        self.t += secs


def _mgr(clock, **over):
    state = {"replayed": [], "meta": []}
    defaults = dict(
        fetch_episodes=lambda: [{"id": "e1"}, {"id": "e2"}, {"id": "e3"}, {"id": "e4"}],
        replay_episode=lambda ep: state["replayed"].append(ep["id"]),
        detect_patterns=lambda: [],
        write_meta_fact=lambda p: state["meta"].append(p),
        clock=clock,
        enabled=True,
        idle_threshold_s=300,
    )
    defaults.update(over)
    m = IdleReplayManager(**defaults)
    m._state = state
    return m


def test_idle_threshold_triggers_replay():
    clk = Clock()
    m = _mgr(clk)
    assert m.run_once() is False           # just active → no replay
    clk.advance(301)                        # 5 min idle
    assert m.run_once() is True
    assert m._state["replayed"] == ["e1", "e2", "e3"]  # caps at episodes_per_replay


def test_env_flag_off_disables():
    clk = Clock()
    m = _mgr(clk, enabled=False)
    clk.advance(1000)
    assert m.run_once() is False
    assert m._state["replayed"] == []


def test_user_input_halts_replay():
    clk = Clock()
    state = {"replayed": []}

    def replay(ep):
        state["replayed"].append(ep["id"])
        if ep["id"] == "e1":
            m.notify_activity()  # user input mid-replay

    m = _mgr(clk, replay_episode=replay)
    m._state = state
    clk.advance(301)
    m.run_once()
    assert state["replayed"] == ["e1"], "replay did not halt on activity"


def test_per_hour_cap_enforced():
    clk = Clock()
    m = _mgr(clk, per_hour_cap=1, per_day_cap=10)
    clk.advance(301)
    assert m.run_once() is True
    clk.advance(301)            # still idle, but within the hour
    assert m.run_once() is False  # hour cap = 1


def test_per_day_cap_enforced():
    clk = Clock()
    m = _mgr(clk, per_hour_cap=100, per_day_cap=2)
    for _ in range(2):
        clk.advance(301)
        assert m.run_once() is True
    clk.advance(301)
    assert m.run_once() is False  # day cap = 2


def test_tpd_budget_low_skips_replay():
    clk = Clock()
    m = _mgr(clk, tpd_remaining=lambda: 0.10, min_tpd_fraction=0.20)
    clk.advance(301)
    assert m.run_once() is False
    m2 = _mgr(clk, tpd_remaining=lambda: 0.50, min_tpd_fraction=0.20)
    clk.advance(301)
    assert m2.run_once() is True


def test_pattern_detection_writes_meta_fact():
    clk = Clock()
    pattern = {"entity": "GNN", "count": 4,
               "content": "Pattern detected: GNN appears in 4 sessions over 7d",
               "category": "pattern_observation", "source": "replay"}
    m = _mgr(clk, detect_patterns=lambda: [pattern])
    clk.advance(301)
    m.run_once()
    assert m._state["meta"] == [pattern]
    assert m._state["meta"][0]["source"] == "replay"


def test_replay_resumes_after_activity_window():
    clk = Clock()
    m = _mgr(clk, per_hour_cap=1)
    clk.advance(301)
    assert m.run_once() is True
    clk.advance(3601)          # an hour later, still idle
    assert m.run_once() is True  # hour-old replay pruned from cap window
