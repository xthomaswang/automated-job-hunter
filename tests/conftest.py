import json

import pytest
from helpers import ACME, CRITERIA


@pytest.fixture
def repo(tmp_path):
    def make(sources=(ACME,), criteria=None, profile="Five years of mixed-methods research."):
        (tmp_path / "config").mkdir(exist_ok=True)
        (tmp_path / "profile").mkdir(exist_ok=True)
        (tmp_path / "config" / "sources.json").write_text(json.dumps(list(sources)))
        (tmp_path / "profile" / "criteria.json").write_text(json.dumps(dict(CRITERIA, **(criteria or {}))))
        (tmp_path / "profile" / "profile.md").write_text(profile)
        return tmp_path
    return make
