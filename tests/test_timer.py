"""The launchd timer that starts a feed's workflow from a Mac, for when GitHub does not."""

import json
import os
import plistlib
import re
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from ajh import scaffold, timer
from ajh.cli import main

LABEL = "automated-job-hunter.someone.job-feed"
# Stand-in for gh: records its arguments, refuses a GH_TOKEN, and answers from the files beside it.
GH = """#!/bin/sh
here=$(dirname "$0")
echo "$*" >> "$here/calls"
if [ -n "$GH_TOKEN" ]; then echo "GH_TOKEN reached gh" >&2; exit 4; fi
if [ -f "$here/error" ]; then cat "$here/error" >&2; exit 1; fi
cat "$here/state"
"""


def done(code, stderr=""):
    return subprocess.CompletedProcess([], code, "", stderr)


class Launchd:
    """Stand-in for launchctl: records what it is asked and keeps track of what is loaded."""

    def __init__(self):
        self.calls, self.loaded, self.refuse = [], set(), 0

    def __call__(self, *args):
        self.calls.append(args[0])
        if args[0] == "bootstrap":
            if self.refuse:
                self.refuse -= 1
                return done(5, "Bootstrap failed: 5: Input/output error")
            with open(args[-1], "rb") as handle:
                self.loaded.add(plistlib.load(handle)["Label"])
            return done(0)
        name = args[-1].rsplit("/", 1)[1]
        if args[0] == "bootout" and name in self.loaded:
            self.loaded.discard(name)
            return done(0)
        return done(0 if args[0] == "print" and name in self.loaded else 113, "Could not find service")


@pytest.fixture
def mac(tmp_path, monkeypatch):
    """A Mac on US Eastern summer time, with a stand-in launchctl and a stand-in gh on the PATH."""
    home, tools = tmp_path / "home", tmp_path / "bin"
    home.mkdir()
    tools.mkdir()
    (tools / "gh").write_text(GH)
    (tools / "gh").chmod(0o755)
    (tools / "state").write_text("active\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GH_TOKEN", "from-the-shell")  # a launchd agent would not have it
    launchd = Launchd()
    monkeypatch.setattr(timer, "_macos", lambda: True)
    monkeypatch.setattr(timer, "_zone", lambda: ("EDT", -240))
    monkeypatch.setattr(timer, "_launchctl", launchd)
    monkeypatch.setattr(timer.time, "sleep", lambda seconds: None)
    return SimpleNamespace(
        home=home,
        gh=tools / "gh",
        launchd=launchd,
        tools=tools,
        plist=home / "Library" / "LaunchAgents" / f"{LABEL}.plist",
        log=home / "Library" / "Logs" / f"{LABEL}.log",
    )


def feed(tmp_path, cron="5 0,12 * * *", origin="git@github.com:someone/job-feed.git", name="feed"):
    root = tmp_path / name
    scaffold.init(root, "4h")
    workflow = root / scaffold.WORKFLOW
    workflow.write_text(re.sub(r'cron: "[^"]+"', f'cron: "{cron}"', workflow.read_text()))
    if origin:
        subprocess.run(["git", "-C", str(root), "remote", "add", "origin", origin], check=True)
    return root


@pytest.mark.parametrize(
    "cron, offset, starts",
    [
        ("5 0,12 * * *", -240, [{"Hour": 8, "Minute": 5}, {"Hour": 20, "Minute": 5}]),  # US Eastern, summer
        ("17 */4 * * *", 330, [{"Hour": hour, "Minute": 47} for hour in (1, 5, 9, 13, 17, 21)]),  # India
        ("3 23 * * *", 60, [{"Hour": 0, "Minute": 3}]),  # past midnight
        ("9 * * * *", -240, [{"Minute": 9}]),
        ("9 * * * *", 330, [{"Minute": 39}]),
    ],
)
def test_the_timer_starts_at_the_cron_lines_times_on_the_macs_clock(cron, offset, starts):
    assert timer.calendar(timer.utc_times(cron), offset) == starts


def test_the_timer_follows_every_cron_line_the_engine_writes_and_refuses_others():
    for cadence, count in (("1h", 24), ("4h", 6), ("8h", 3), ("6,10,14,20", 4), ("7", 1)):
        assert len(timer.utc_times(scaffold.cron_for(cadence, 11))) == count
    for cron in ("5 9-17 * * *", "5 0 * * 1", "*/5 * * * *", "60 1 * * *", "5 24 * * *", "5 */0 * * *", "5 1,,2 * * *"):
        with pytest.raises(timer.TimerError, match="upgrade --cadence"):
            timer.utc_times(cron)


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:someone/job-feed.git",
        "https://github.com/someone/job-feed",
        "https://github.com/someone/job-feed.git/",
        "ssh://git@github.com/someone/job-feed.git",
        "https://x-access-token:abc@github.com/someone/job-feed.git",
    ],
)
def test_the_repo_to_start_comes_from_the_github_remote(tmp_path, url):
    assert timer.repo_slug(feed(tmp_path, origin=url)) == "someone/job-feed"


def test_a_feed_without_a_github_remote_has_no_workflow_to_start(tmp_path):
    for name, origin in (("local", None), ("elsewhere", "https://token-123@gitlab.com/someone/job-feed.git")):
        with pytest.raises(timer.TimerError, match="no GitHub remote") as refused:
            timer.repo_slug(feed(tmp_path, origin=origin, name=name))
        assert "token-123" not in str(refused.value)  # the URL is never repeated: it can hold a token


def test_install_writes_a_launch_agent_that_starts_the_workflow_and_loads_it(mac, tmp_path):
    root = feed(tmp_path)
    shown = timer.show(root)
    assert (shown["installed"], shown["starts"], shown["time_zone"]) == (False, ["08:05", "20:05"], "EDT, UTC-04:00")
    assert not mac.plist.exists() and mac.launchd.calls == []  # showing changes nothing

    installed = timer.install(root)
    agent = plistlib.loads(mac.plist.read_bytes())
    assert agent["Label"] == LABEL
    assert agent["ProgramArguments"] == [
        "/bin/sh", "-c", timer.SCRIPT, str(mac.gh), "job-hunter.yml", "someone/job-feed", "10", "30"
    ]  # fmt: skip
    assert agent["StartCalendarInterval"] == [{"Hour": 8, "Minute": 5}, {"Hour": 20, "Minute": 5}]
    assert agent["StandardOutPath"] == agent["StandardErrorPath"] == str(mac.log)
    assert agent["EnvironmentVariables"] == {"HUNTER_CRON": "5 0,12 * * *"}
    assert installed == {
        **shown,
        "installed": True,
        "loaded": True,
        "last_start": None,
    }
    assert installed["plist"] == f"~/Library/LaunchAgents/{LABEL}.plist" and str(mac.home) not in json.dumps(installed)
    # gh was asked about the workflow as launchd will run it: without the shell's GH_TOKEN
    assert (
        mac.tools / "calls"
    ).read_text() == "api repos/someone/job-feed/actions/workflows/job-hunter.yml --jq .state\n"
    if shutil.which("plutil"):
        subprocess.run(["plutil", "-lint", str(mac.plist)], check=True, capture_output=True)

    mac.launchd.calls.clear()
    mac.launchd.refuse = 2  # launchd can take a moment to let go of the copy it unloaded
    assert timer.install(root)["loaded"]
    assert mac.launchd.calls == ["bootout", "bootstrap", "bootstrap", "bootstrap", "print"]


def test_install_refuses_when_the_timer_could_not_start_the_workflow(mac, tmp_path, monkeypatch):
    root = feed(tmp_path)
    (mac.tools / "state").write_text("disabled_manually\n")
    with pytest.raises(
        timer.TimerError, match=r"disabled_manually.*gh workflow enable job-hunter\.yml --repo someone/"
    ):
        timer.install(root)
    (mac.tools / "error").write_text("HTTP 401: Bad credentials (https://api.github.com/repos/someone/job-feed)\n")
    with pytest.raises(timer.TimerError, match=r"Bad credentials.*GH_TOKEN"):
        timer.install(root)
    mac.launchd.refuse = 5
    (mac.tools / "error").unlink()
    (mac.tools / "state").write_text("active\n")
    with pytest.raises(timer.TimerError, match="could not load the timer: Bootstrap failed"):
        timer.install(root)
    assert not mac.plist.exists()  # a timer launchd would not load is not left behind
    monkeypatch.setattr(timer.shutil, "which", lambda name: None)
    with pytest.raises(timer.TimerError, match="gh is not installed"):
        timer.install(root)
    monkeypatch.setattr(timer, "_macos", lambda: False)
    with pytest.raises(timer.TimerError, match="only macOS"):
        timer.install(root)
    assert main(["--repo", str(root), "timer", "install"]) == 2


def start(gh, tries):
    """One start, as launchd runs it."""
    args = ["/bin/sh", "-c", timer.SCRIPT, str(gh), "job-hunter.yml", "someone/job-feed", str(tries), "0"]
    return subprocess.run(args, capture_output=True, text=True, env={"PATH": timer.LAUNCHD_PATH}).stdout


def test_a_failed_start_is_tried_again_and_every_start_logs_one_line(tmp_path):
    gh = tmp_path / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        'n=$(cat "$0.count" 2>/dev/null || echo 0); echo $((n + 1)) > "$0.count"\n'
        'if [ "$n" -ge 2 ]; then echo "$*"; exit 0; fi\n'
        'echo "error connecting to api.github.com"; echo "check your internet connection" >&2; exit 1\n'
    )
    gh.chmod(0o755)
    failed = start(gh, 2)
    assert re.fullmatch(
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ exit 1 tries 2 error connecting to api.github.com check your internet "
        r"connection\n",
        failed,
    )
    (tmp_path / "gh.count").unlink()
    started = start(gh, 10)
    assert started.endswith(" exit 0 tries 3 workflow run job-hunter.yml --repo someone/job-feed\n")
    log = tmp_path / "timer.log"
    log.write_text(failed + "launchd wrote this\n" + started)
    assert timer._last_start(log) == {
        "at": started[:20],
        "exit": 0,
        "tries": 3,
        "said": "workflow run job-hunter.yml --repo someone/job-feed",
    }


def test_show_says_when_the_timer_no_longer_follows_the_schedule_or_could_not_start(mac, tmp_path, monkeypatch):
    root = feed(tmp_path)
    timer.install(root)
    monkeypatch.setattr(timer, "_zone", lambda: ("EST", -300))
    after_clock_change = timer.show(root)
    assert after_clock_change["starts"] == ["08:05", "20:05"] and "warnings" not in after_clock_change  # local times

    mac.log.write_text(
        "2026-10-15T00:05:01Z exit 0 tries 1 \n"
        "2026-10-15T12:09:31Z exit 1 tries 10 HTTP 401: Bad credentials (https://api.github.com/repos/someone/x)\n"
    )
    shown = timer.show(root)
    assert shown["last_start"] == {
        "at": "2026-10-15T12:09:31Z",
        "exit": 1,
        "tries": 10,
        "said": "HTTP 401: Bad credentials (https://api.github.com/repos/someone/x)",
    }
    assert shown["warnings"] == [
        "The last start, at 2026-10-15T12:09:31Z, failed after 10 tries: HTTP 401: Bad credentials "
        "(https://api.github.com/repos/someone/x). `gh auth status` shows whether gh is still logged in"
    ]
    scaffold.upgrade(root, "8h")
    mac.launchd.loaded.clear()
    stale = timer.show(root)["warnings"]
    assert stale[0] == "launchd has not loaded the timer, so it starts nothing; `timer install` loads it again"
    assert stale[1].startswith("The timer was made for the cron line `5 0,12 * * *`, and the workflow now has `")


def test_remove_unloads_the_timer_and_keeps_its_log(mac, tmp_path):
    root = feed(tmp_path)
    timer.install(root)
    mac.log.write_text("2026-10-15T00:05:01Z exit 0 tries 1 \n")
    log = f"~/Library/Logs/{LABEL}.log"
    assert timer.remove(root) == {"removed": True, "label": LABEL, "log": log}
    assert not mac.plist.exists() and mac.log.exists() and not mac.launchd.loaded
    assert timer.remove(root) == {"removed": False, "label": LABEL, "log": log}


def test_status_shows_the_timer_only_on_a_mac_where_this_feed_has_one(mac, tmp_path, monkeypatch, capsys):
    root = feed(tmp_path)

    def status():
        assert main(["--repo", str(root), "status"]) == 0
        return json.loads(capsys.readouterr().out)["timer"]

    assert status() is None
    timer.install(root)
    assert main(["--repo", str(root), "timer"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert status() == shown and shown["starts"] == ["08:05", "20:05"] and shown["loaded"]
    mac.plist.write_text("not a plist")
    assert status()["warnings"][0].startswith("The timer's file cannot be read (")
    monkeypatch.setattr(timer, "_macos", lambda: False)
    assert status() is None
