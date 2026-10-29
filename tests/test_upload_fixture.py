"""tools/upload_fixture.py against an in-memory fake container. Nothing reaches Azure."""
import os

import pytest
from deltalake import DeltaTable

from tools import synthetic_fixture, upload_fixture

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Props:
    def __init__(self, data):
        import hashlib
        self.size = len(data)
        self.content_settings = type("CS", (), {"content_md5": hashlib.md5(data).digest()})()


class Blob:
    def __init__(self, name):
        self.name = name


class FakeContainer:
    def __init__(self, blobs=None, fail_after=None):
        self.blobs = dict(blobs or {})
        self.order = []
        self.fail_after = fail_after

    def list_blobs(self):
        return [Blob(n) for n in self.blobs]

    def upload_blob(self, name, f, overwrite, content_settings):
        assert overwrite is False
        if self.fail_after is not None and len(self.order) >= self.fail_after:
            raise ConnectionError("interrupted")
        if name in self.blobs:
            raise AssertionError("create-only upload would overwrite")
        self.blobs[name] = f.read()
        self.order.append(name)

    def get_blob_client(self, name):
        data = self.blobs[name]
        return type("BC", (), {"get_blob_properties": lambda self: Props(data)})()


@pytest.fixture
def table(tmp_path):
    return synthetic_fixture.write(str(tmp_path), repo_root=REPO)


def test_dry_run_uploads_nothing(table):
    c = FakeContainer()
    uploaded, skipped = upload_fixture.upload(c, table, set(), execute=False)
    assert uploaded and not skipped and c.blobs == {}


def test_data_files_go_before_the_delta_log_and_stay_under_datums(table):
    c = FakeContainer()
    upload_fixture.upload(c, table, set(), execute=True)
    assert all(n.startswith("datums/") for n in c.order)
    first_log = next(i for i, n in enumerate(c.order) if n.startswith("datums/_delta_log/"))
    assert all(n.startswith("datums/_delta_log/") for n in c.order[first_log:])
    assert any(n.endswith(".parquet") for n in c.order[:first_log])


def test_an_interrupted_upload_resumes_without_overwriting(table):
    c = FakeContainer(fail_after=1)
    with pytest.raises(ConnectionError):
        upload_fixture.upload(c, table, set(), execute=True)
    assert not any(n.startswith("datums/_delta_log/") for n in c.blobs)  # no readable partial table
    c.fail_after = None
    uploaded, skipped = upload_fixture.upload(c, table, set(c.blobs), execute=True)
    assert len(skipped) == 1 and uploaded


def test_a_different_existing_file_stops_the_run(table):
    rel, _ = upload_fixture.local_files(table)[0]
    c = FakeContainer({f"datums/{rel}": b"something else"})
    with pytest.raises(upload_fixture.Refused):
        upload_fixture.upload(c, table, set(c.blobs), execute=True)


def test_unrelated_data_in_the_container_refuses(table):
    with pytest.raises(upload_fixture.Refused):
        upload_fixture.check_remote(FakeContainer({"other/thing.parquet": b"x"}), lambda: None)


def test_an_existing_unmarked_table_refuses(table):
    with pytest.raises(upload_fixture.Refused):
        upload_fixture.check_remote(FakeContainer({"datums/_delta_log/00000000000000000000.json": b"{}"}), lambda: {})


def test_a_marked_existing_table_is_allowed(table):
    marker = {synthetic_fixture.FIXTURE_PROPERTY: synthetic_fixture.FIXTURE_VALUE}
    existing = upload_fixture.check_remote(FakeContainer({"datums/_delta_log/00000000000000000000.json": b"{}"}), lambda: marker)
    assert existing


def test_verify_accepts_the_same_table_and_rejects_a_partial_one(table, tmp_path):
    assert upload_fixture.verify(table, DeltaTable(table)) == []
    other = tmp_path / "partial" / "datums"
    other.mkdir(parents=True)
    DeltaTable.create(str(other), synthetic_fixture.SCHEMA, configuration=synthetic_fixture.TABLE_CONFIGURATION, raise_if_key_not_exists=False)
    problems = upload_fixture.verify(table, DeltaTable(str(other)))
    assert any("rows" in p for p in problems)   # marker present, data missing: not complete


def test_the_local_table_must_be_the_fixture(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    DeltaTable.create(str(plain), synthetic_fixture.SCHEMA)
    with pytest.raises(upload_fixture.Refused):
        upload_fixture.check_local(str(plain))
