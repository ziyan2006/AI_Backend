from simulator.device_simulator import build_sample_snapshot, generate_silence_pcm


def test_simulator_fixture_matches_device_contract() -> None:
    snapshot = build_sample_snapshot("session-test")

    assert snapshot["type"] == "circuit.snapshot"
    assert snapshot["schema_version"] == 1
    assert snapshot["session_id"] == "session-test"
    assert snapshot["topology_revision"] >= 0
    assert any(slot["component"] == "and_gate" for slot in snapshot["slots"])


def test_simulator_pcm_size_matches_s16le_mono_duration() -> None:
    assert len(generate_silence_pcm(duration_ms=100, sample_rate_hz=16000)) == 3200
