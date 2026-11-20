"""The ledger when more than one process uses it, and the lock that keeps two posters apart."""

import json
import os
import subprocess
import sys
import time

from clipper.state import Ledger, Lock


def test_two_processes_changing_different_clips_both_keep_their_changes(tmp_path):
    path = tmp_path / "state" / "ledger.json"
    first = Ledger(path)
    first.clips.update(a={"id": "a", "status": "held"}, b={"id": "b", "status": "held"}, c={"id": "c", "status": "held"})
    first.sources["s1"] = {"status": "done"}
    first.save()

    run, poster = Ledger(path), Ledger(path)  # a run making clips, and the poster sending one
    held_by_run = run.clips["b"]  # a record the run keeps hold of
    poster.clips["b"].update(status="posted", posts={"youtube": {"state": "posted"}})
    del poster.clips["c"]
    poster.clips["p"] = {"id": "p", "status": "scheduled"}
    poster.log_run({"run": "poster"})
    poster.save()

    run.clips["a"]["status"] = "scheduled"
    run.clips["n"] = {"id": "n", "status": "planned"}
    run.sources["s2"] = {"status": "planned"}
    run.log_run({"run": "maker"})
    run.save()

    for ledger in (run, Ledger(path)):
        assert {k: v["status"] for k, v in ledger.clips.items()} == {"a": "scheduled", "b": "posted", "n": "planned", "p": "scheduled"}
        assert ledger.clips["b"]["posts"] == {"youtube": {"state": "posted"}} and set(ledger.sources) == {"s1", "s2"}
        assert [r["run"] for r in ledger.data["runs"]] == ["poster", "maker"]
    assert held_by_run is run.clips["b"] and held_by_run["status"] == "posted"  # the same record, brought up to date in place

    poster.save()  # the poster picks up the run's changes the same way
    assert poster.clips["a"]["status"] == "scheduled" and "n" in poster.clips and "c" not in poster.clips


def test_when_both_change_the_same_clip_the_later_save_stands(tmp_path):
    path = tmp_path / "ledger.json"
    Ledger(path).save()
    one, two = Ledger(path), Ledger(path)
    one.clips["x"] = {"id": "x", "title": "from one"}
    two.clips["x"] = {"id": "x", "title": "from two"}
    one.save()
    two.save()
    assert Ledger(path).clips["x"]["title"] == "from two"
    one.clips["x"]["title"] = "one again"  # one changes it once more without having seen two's version
    one.save()
    assert Ledger(path).clips["x"]["title"] == "one again"
    assert not path.with_suffix(".lock").exists() and not path.with_suffix(".tmp").exists()
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


def test_a_lock_is_held_by_one_at_a_time_and_an_abandoned_one_is_taken_over(tmp_path):
    path = tmp_path / "state" / "deliver.lock"
    with Lock(path, wait=0) as held:
        assert held and path.read_text().split()[0] == str(os.getpid())
        with Lock(path, wait=0.2) as second:
            assert second is False  # busy
        assert path.exists()  # the second attempt left the first one's lock alone
    assert not path.exists()

    # Left behind by a process that is gone: taken over at once.
    gone = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
    path.write_text(f"{gone.stdout.strip()} {time.time():.0f}")
    with Lock(path, wait=0) as held:
        assert held
    # Held by a live process for longer than it could take: taken over too.
    path.write_text(f"{os.getpid()} {time.time() - 500:.0f}")
    with Lock(path, wait=0, stale=100) as held:
        assert held
    path.write_text(f"{os.getpid()} {time.time():.0f}")
    with Lock(path, wait=0, stale=100) as held:
        assert held is False
    path.unlink()
    path.write_text("garbage")
    os.utime(path, (time.time() - 60, time.time() - 60))
    with Lock(path, wait=0) as held:
        assert held


def test_saves_from_many_processes_at_once_lose_nothing(tmp_path):
    path = tmp_path / "ledger.json"
    Ledger(path).save()
    script = (
        "import sys; sys.path.insert(0, sys.argv[1])\n"
        "from pathlib import Path\n"
        "from clipper.state import Ledger\n"
        "for n in range(15):\n"
        "    ledger = Ledger(Path(sys.argv[2]))\n"
        "    ledger.clips[f'{sys.argv[3]}-{n}'] = {'id': n}\n"
        "    ledger.save()\n"
    )
    root = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
    workers = [subprocess.Popen([sys.executable, "-c", script, root, str(path), name]) for name in ("a", "b", "c", "d")]
    assert [w.wait(timeout=120) for w in workers] == [0, 0, 0, 0]
    assert len(Ledger(path).clips) == 60
