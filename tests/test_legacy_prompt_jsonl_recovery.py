from pathlib import Path

import ai_code_sessions


def test_legacy_events_only_session_can_recover_native_codex_rollout(monkeypatch, tmp_path: Path):
    codex_home = tmp_path / "codex_home"
    sessions_base = codex_home / "sessions" / "2025" / "12" / "21"
    sessions_base.mkdir(parents=True)

    rollout = sessions_base / "rollout-2025-12-21T16-17-58-019b436b-861b-7900-b206-a123d6f11dc3.jsonl"
    prompt = "docs/Future-Enhancements/2025-12-17_Full_Circle_Exchanges_WOI_R_and_D_Files.md"
    rollout.write_text(
        "\n".join(
            [
                '{"type":"session_meta","payload":{"timestamp":"2025-12-21T16:17:58.735959-08:00","cwd":"/tmp","id":"019b436b-861b-7900-b206-a123d6f11dc3"}}',
                f'{{"type":"input","payload":{{"text":"I want you to center yourself on this doc: {prompt}"}}}}',
                '{"timestamp":"2025-12-21T16:18:00.000000-08:00","type":"event"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    legacy_dir = tmp_path / "repo" / ".codex" / "sessions" / "2025-12-21-1617_Retail_Exchange_flow_to_FC_work_3"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "events.jsonl").write_text(
        "\n".join(
            [
                '{"type":"session_start","ts":"2025-12-21T16:17:58.735959-08:00","tool":"codex"}',
                f'{{"type":"user_input","ts":"2025-12-21T16:19:38.203269-08:00","line":"I want you to center yourself on this doc: {prompt}."}}',
                '{"type":"assistant_text","ts":"2025-12-21T16:20:00.000000-08:00","text":"ok"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    copied = ai_code_sessions._maybe_copy_native_jsonl_into_legacy_session_dir(
        tool="codex",
        session_dir=legacy_dir,
        start=None,
        end=None,
        cwd=None,
        codex_resume_id=None,
    )

    assert copied is not None
    assert copied.exists()
    assert copied.parent == legacy_dir
    assert copied.name == rollout.name

