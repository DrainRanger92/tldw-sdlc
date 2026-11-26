from aidevteam.state import (
    State,
    append_journal,
    clear_failure,
    read_failure,
    read_journal,
    write_failure,
)


def test_state_roundtrip(tmp_path):
    st = State.load(tmp_path)
    assert st.cycles == 0 and st.last_processed_commit is None
    st.cycles = 2
    st.last_processed_commit = "abc"
    st.save(tmp_path)
    assert State.load(tmp_path).cycles == 2


def test_journal(tmp_path):
    append_journal(tmp_path, {"cycle": 1, "ok": True})
    append_journal(tmp_path, {"cycle": 2, "ok": False})
    assert [e["cycle"] for e in read_journal(tmp_path, 5)] == [2, 1]


def test_failure_roundtrip(tmp_path):
    write_failure(tmp_path, "EXECUTE", "boom")
    assert "EXECUTE" in read_failure(tmp_path)
    clear_failure(tmp_path)
    assert read_failure(tmp_path) is None
