import pytest
from ajh.state import COLUMNS, Row, State, StateError, read_rows, rows_text


def filled(root):
    state = State(root)
    state.rows("acme").update(
        {
            "20": Row("20", "A", "2026-10-01", "aaaaaaaaaa", "bbbbbbbbbbbb", "0123456789abcdef", "82", 1, 1),
            "3": Row("3", "X", "2026-09-01", "cccccccccc", "dddddddddddd", note="D@2026-10-10"),
        }
    )
    state.rows("globex")  # touched but empty
    state.titles["account executive"] = 1
    state.sources["acme"] = {"ok_day": "2026-10-13", "count": 2}
    state.profile.update(triage={"hash": "h1", "gen": 1}, assess={"hash": "h2", "gen": 1})
    return state


def test_state_round_trips_and_is_sorted(tmp_path):
    filled(tmp_path / "missing").save(tmp_path / "state", "2026-10-13")
    text = (tmp_path / "state" / "jobs" / "acme.tsv").read_text()
    assert text.split("\n")[0].split("\t") == list(COLUMNS)
    assert [line.split("\t")[0] for line in text.split("\n")[1:3]] == ["20", "3"]  # sorted by id as text
    again = State(tmp_path / "state")
    assert again.rows("acme")["20"] == Row(
        "20", "A", "2026-10-01", "aaaaaaaaaa", "bbbbbbbbbbbb", "0123456789abcdef", "82", 1, 1
    )
    assert again.rows("acme")["3"].note == "D@2026-10-10"
    assert again.titles == {"account executive": 1} and again.sources == {"acme": {"ok_day": "2026-10-13", "count": 2}}
    assert again.meta["as_of"] == "2026-10-13" and again.profile["triage"]["gen"] == 1
    assert not (tmp_path / "state" / "jobs" / "globex.tsv").exists()  # a source with no rows gets no file


def test_saving_the_same_state_twice_is_byte_identical(tmp_path):
    filled(tmp_path / "missing").save(tmp_path / "a", "2026-10-13")
    State(tmp_path / "a").save(tmp_path / "b", "2026-10-13")
    for name in ("jobs/acme.tsv", "titles.tsv", "sources.json", "profile.json", "meta.json"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
    assert not list((tmp_path / "a").rglob("*.tmp"))


@pytest.mark.parametrize(
    "text",
    [
        "id\tstatus\n1\tP\n",
        rows_text({"1": Row("1", "P", "2026-10-13", "g", "t")}).replace("\tP\t", "\tZ\t"),
        rows_text({"1": Row("1", "P", "2026-10-13", "g", "t")})
        + rows_text({"1": Row("1", "P", "2026-10-13", "g", "t")}).split("\n")[1]
        + "\n",
        rows_text({"1": Row("1", "P", "2026-10-13", "g", "t")}).replace("\t0\t0\t0\t0\t", "\t0\tx\t0\t0\t"),
        rows_text({"1": Row("1", "P", "2026-10-13", "g", "t")}).replace("\t-\n", "\n"),
    ],
)
def test_a_damaged_rows_file_is_an_error_not_a_guess(tmp_path, text):
    path = tmp_path / "acme.tsv"
    path.write_text(text)
    with pytest.raises(StateError):
        read_rows(path)


def test_unknown_schema_and_unsafe_source_ids_are_refused(tmp_path):
    (tmp_path / "meta.json").write_text('{"schema_version": 99}')
    with pytest.raises(StateError, match="upgrade"):
        State(tmp_path)
    with pytest.raises(StateError, match="Unsafe"):
        State(tmp_path / "fresh").rows("../escape")
    assert read_rows(tmp_path / "absent.tsv") == {}
