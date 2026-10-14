"""`publish` and `audit` against real git repositories in temporary directories."""

import json
import subprocess

import pytest
from ajh import audit, publish
from ajh.cli import main
from helpers import Boards, full_tick, posting

IDENTITY = ["-c", "user.name=Tester", "-c", "user.email=tester@users.noreply.github.com"]


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def feed_repo(repo, tmp_path_factory, monkeypatch):
    """A feed repo with one committed tick's inputs and a bare remote to push to."""
    for name in (*audit.SECRET_ENV, "AJH_DENYLIST"):
        monkeypatch.delenv(name, raising=False)
    root = repo()
    remote = tmp_path_factory.mktemp("remote") / "feed.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Tester")
    git(root, "config", "user.email", "tester@users.noreply.github.com")
    (root / ".gitignore").write_text(".hunter/work/\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "Set up the feed repo")
    git(root, "remote", "add", "origin", str(remote))
    git(root, "push", "-q", "origin", "main")
    return root, remote


def tick(root):
    return full_tick(root, Boards(acme=[posting(1), posting(2)]))


def test_publish_commits_only_generated_paths_and_pushes(feed_repo):
    root, remote = feed_repo
    tick(root)
    result = publish.run(root)
    assert result["state"] == "published" and result["pushed"] is True and result["files"] >= 8
    changed = git(root, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert {path.split("/")[0] for path in changed} == {"README.md", "positions.jsonl", "jobs", "feed", "state"}
    message = git(root, "log", "-1", "--format=%B")
    assert (
        message.startswith("Update job feed: 2 open matches, 0 waiting to be judged")
        and "Job-Hunter-Publish: auto" in message
    )
    assert (
        git(root, "rev-parse", "HEAD") == git(remote, "rev-parse", "main") and git(root, "status", "--porcelain") == ""
    )
    assert "extraheader" not in (root / ".git" / "config").read_text()


def test_a_tick_with_nothing_new_makes_no_commit(feed_repo):
    root, _ = feed_repo
    tick(root)
    publish.run(root)
    head = git(root, "rev-parse", "HEAD")
    tick(root)
    assert publish.run(root) == {"state": "unchanged", "commit": None, "pushed": True, "files": 0}
    assert git(root, "rev-parse", "HEAD") == head


def test_a_dry_run_reports_and_changes_nothing(feed_repo):
    root, _ = feed_repo
    tick(root)
    head = git(root, "rev-parse", "HEAD")
    result = publish.run(root, dry_run=True)
    assert result["state"] == "dry_run" and result["files"] >= 8 and git(root, "rev-parse", "HEAD") == head
    assert git(root, "diff", "--cached", "--name-only") == ""


def test_other_staged_work_blocks_publishing(feed_repo):
    root, _ = feed_repo
    tick(root)
    (root / "notes.txt").write_text("my own file")
    git(root, "add", "notes.txt")
    with pytest.raises(publish.PublishBlocked, match=r"notes\.txt"):
        publish.run(root)


def test_a_moved_remote_is_left_alone_never_forced(feed_repo, tmp_path_factory):
    root, remote = feed_repo
    other = tmp_path_factory.mktemp("other")
    subprocess.run(["git", "clone", "-q", str(remote), str(other)], check=True)
    (other / "elsewhere.txt").write_text("a commit made elsewhere")
    git(other, "add", "-A")
    git(other, *IDENTITY, "commit", "-q", "-m", "Elsewhere")
    git(other, "push", "-q", "origin", "main")
    ahead = git(remote, "rev-parse", "main")
    tick(root)
    result = publish.run(root)
    assert result["state"] == "stale_base" and result["pushed"] is False and git(remote, "rev-parse", "main") == ahead
    assert main(["--repo", str(root), "publish"]) == 0  # the next tick starts from a fresh clone and redoes the work


def push_from_elsewhere(remote, tmp_path_factory, path, text):
    """A person's commit that reaches the remote while a run is working, as a merged PR would."""
    other = tmp_path_factory.mktemp("other")
    subprocess.run(["git", "clone", "-q", str(remote), str(other)], check=True)
    (other / path).parent.mkdir(parents=True, exist_ok=True)
    (other / path).write_text(text)
    git(other, "add", "-A")
    git(other, *IDENTITY, "commit", "-q", "-m", f"Edit {path}")
    git(other, "push", "-q", "origin", "main")
    return git(remote, "rev-parse", "main")


def test_a_run_lands_on_top_of_a_persons_edit_merged_while_it_ran(feed_repo, tmp_path_factory):
    root, remote = feed_repo
    edited = push_from_elsewhere(
        remote, tmp_path_factory, "profile/profile.md", "Five years of research, and now teaching."
    )
    tick(root)
    result = publish.run(root)
    assert (result["state"], result["pushed"], result["replayed"]) == ("published", True, ["profile/profile.md"])
    head = git(remote, "rev-parse", "main")
    assert git(remote, "rev-parse", "main^") == edited and head.startswith(result["commit"])  # on top, nothing lost
    assert git(remote, "show", "main:profile/profile.md") == "Five years of research, and now teaching."
    assert "positions.jsonl" in git(remote, "show", "--name-only", "--format=", "main").splitlines()
    assert git(remote, "log", "-1", "--format=%B", "main").rstrip().endswith(publish.TRAILER)
    assert (root / "profile/profile.md").read_text() == "Five years of research, and now teaching."


def test_no_replay_over_generated_files_or_past_other_unpushed_work(feed_repo, tmp_path_factory, monkeypatch, capsys):
    root, remote = feed_repo
    ahead = push_from_elsewhere(remote, tmp_path_factory, "README.md", "edited by hand")
    tick(root)
    result = publish.run(root)
    assert (result["state"], result["pushed"]) == ("stale_base", False) and "README.md" in result["detail"]
    assert git(remote, "rev-parse", "main") == ahead
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert main(["--repo", str(root), "publish"]) == 0  # a warning on the run, not a failure
    assert "::warning::Nothing was pushed: the remote moved, with changes outside profile/ and config/" in (
        capsys.readouterr().err
    )


def test_no_replay_when_this_repository_has_other_commits_the_remote_lacks(feed_repo, tmp_path_factory):
    root, remote = feed_repo
    (root / "config/hunter.json").write_text("{}\n")  # a person's own commit, not pushed yet
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "Local edit")
    mine = git(root, "rev-parse", "HEAD")
    ahead = push_from_elsewhere(remote, tmp_path_factory, "profile/profile.md", "Edited elsewhere.")
    tick(root)
    result = publish.run(root)
    assert result["state"] == "stale_base" and "other commits" in result["detail"]
    assert git(remote, "rev-parse", "main") == ahead and git(root, "rev-parse", "HEAD^") == mine  # nothing reordered


def test_publishing_without_a_remote_or_with_no_push_commits_locally(feed_repo):
    root, remote = feed_repo
    before = git(remote, "rev-parse", "main")
    tick(root)
    assert publish.run(root, push=False)["state"] == "committed" and git(remote, "rev-parse", "main") == before
    git(root, "remote", "remove", "origin")
    assert publish.run(root)["state"] == "unchanged"


def test_a_detached_head_or_a_merge_in_progress_blocks_publishing(feed_repo):
    root, _ = feed_repo
    tick(root)
    (root / ".git" / "MERGE_HEAD").write_text(git(root, "rev-parse", "HEAD") + "\n")
    with pytest.raises(publish.PublishBlocked, match="in progress"):
        publish.run(root)
    (root / ".git" / "MERGE_HEAD").unlink()
    git(root, "checkout", "-q", "--detach")
    with pytest.raises(publish.PublishBlocked, match="detached"):
        publish.run(root)


@pytest.mark.parametrize(
    "text,problem",
    [
        ("Reach me at jane.doe@example.org for details.", "an email address"),
        ("Phone: +1 (415) 555-0100", "a phone number"),
        ("See linkedin.com/in/jane-doe-123", "a personal profile link"),
        ("key sk-ant-api03-" + "a" * 40, "an Anthropic key"),
        ("token ghp_" + "b" * 36, "a GitHub token"),
    ],
)
def test_the_profile_must_be_contact_free_and_hold_no_credentials(feed_repo, capsys, text, problem):
    root, _ = feed_repo
    (root / "profile" / "profile.md").write_text("Five years of research.\n" + text)
    assert audit.check(root) == [f"profile/profile.md contains {problem}"]
    assert main(["--repo", str(root), "audit"]) == 2 and problem in json.loads(capsys.readouterr().out)["detail"]
    with pytest.raises(publish.PublishBlocked, match="Audit failed"):
        publish.run(root)


def test_a_clean_profile_passes_and_years_or_dates_are_not_phone_numbers(feed_repo):
    root, _ = feed_repo
    (root / "profile" / "profile.md").write_text(
        "Research lead 2019-2024. Ran 120+ interviews; 3.5 years in B2B. Team of 12."
    )
    assert audit.check(root) == []


def test_a_secret_this_process_can_see_never_reaches_a_commit(feed_repo, monkeypatch):
    root, _ = feed_repo
    tick(root)
    monkeypatch.setenv("GH_TOKEN", "tok-" + "z9" * 12)
    assert audit.check(root) == []
    with (root / "jobs" / "acme" / "1.md").open("a") as brief:
        brief.write("leaked tok-" + "z9" * 12 + "\n")
    assert audit.check(root) == ["jobs/acme/1.md contains the value of $GH_TOKEN"]
    with pytest.raises(publish.PublishBlocked):
        publish.run(root)


def test_denied_terms_block_hand_written_files_but_not_posting_text(feed_repo, monkeypatch):
    root, _ = feed_repo
    tick(root)
    monkeypatch.setenv("AJH_DENYLIST", "Jane Doe\nxy\nOld Employer Ltd")
    with (root / "jobs" / "acme" / "1.md").open("a") as brief:
        brief.write("> This role reports to Jane Doe.\n")  # a name in someone else's posting is not ours to block
    assert audit.check(root) == []
    (root / "profile" / "profile.md").write_text("Previously at old employer ltd.")
    assert audit.check(root) == ["profile/profile.md contains a denied term (entry 3)"]


def test_the_commit_message_counts_postings_waiting_to_be_assessed_again(feed_repo):
    root, _ = feed_repo
    tick(root)
    meta_path = root / "feed" / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["counts"].update(pending_triage=5, pending_assess=7, pending_reassess=11)
    meta_path.write_text(json.dumps(meta))
    publish.run(root, push=False)
    assert git(root, "log", "-1", "--format=%s") == "Update job feed: 2 open matches, 23 waiting to be judged"
