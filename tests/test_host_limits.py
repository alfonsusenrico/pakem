"""Host limits measured at run time or set by the owner (ADR 0018), on several host profiles."""

from __future__ import annotations

import json
import sys
import tempfile
import tomllib
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock
from types import SimpleNamespace

from support import DEPLOY_ASSETS, IsolatedTestCase, write
from test_deploy_receive import SMALL_HOST, DiskDocker, free_space

sys.path.insert(0, str(DEPLOY_ASSETS))
import af_limits  # noqa: E402
import af_monitor  # noqa: E402
import af_receive  # noqa: E402

GB = 1000**3
# Several hosts, the incident host among them: limits must follow each one's size.
PROFILES = {
    "small ssd": (af_limits.Host(cpus=4, memory=16 * GB), af_limits.Volume(total=110 * GB, free=14 * GB)),
    "tiny vps": (af_limits.Host(cpus=1, memory=2 * GB), af_limits.Volume(total=25 * GB, free=12 * GB)),
    "large server": (af_limits.Host(cpus=32, memory=128 * GB), af_limits.Volume(total=2000 * GB, free=1500 * GB)),
    "separate data volume": (af_limits.Host(cpus=8, memory=32 * GB), af_limits.Volume(total=1000 * GB, free=700 * GB)),
}
NONE = af_limits.Settings()


def settings(**sections: dict) -> af_limits.Settings:
    return af_limits.Settings.parse(sections)


class DerivationTests(unittest.TestCase):
    def test_limits_follow_each_host(self) -> None:
        reserves, memories, runners = {}, {}, {}
        for name, (host, disk) in PROFILES.items():
            reserves[name] = af_limits.reserve(disk, [], NONE).value
            memories[name] = af_limits.builder_memory(host, NONE).value
            runners[name] = af_limits.runners_per_repo(host, NONE).value
            with self.subTest(host=name):
                self.assertLess(memories[name], host.memory, "a build never gets the whole machine")
                self.assertLessEqual(af_limits.runner_cpus(host, NONE).value, max(1.0, host.cpus))
                self.assertGreaterEqual(runners[name], 1)
        self.assertLess(reserves["tiny vps"], reserves["small ssd"])
        self.assertLess(reserves["small ssd"], reserves["large server"])
        self.assertLess(memories["tiny vps"], memories["small ssd"])
        self.assertLess(memories["small ssd"], memories["large server"])
        self.assertLess(runners["small ssd"], runners["large server"])

    def test_invariants_hold_on_every_host(self) -> None:
        for name, (_, disk) in PROFILES.items():
            for builds in ([], [3 * GB], [1 * GB, 8 * GB, 2 * GB]):
                with self.subTest(host=name, builds=builds):
                    kept = int(af_limits.reserve(disk, builds, NONE).value)
                    alert = af_limits.alert_free(disk, kept, NONE).value
                    cache = af_limits.build_cache(disk, 5 * GB, kept, NONE).value
                    self.assertGreater(alert, kept, "the alert fires before builds are refused")
                    self.assertLess(cache + kept, disk.total, "cache cap plus reserve stay below capacity")
                    self.assertGreaterEqual(cache, 0)

    def test_limits_recompute_when_the_volume_changes(self) -> None:
        before = af_limits.Volume(total=110 * GB, free=14 * GB)
        after = af_limits.Volume(total=1000 * GB, free=880 * GB)
        self.assertLess(af_limits.reserve(before, [], NONE).value, af_limits.reserve(after, [], NONE).value)
        kept = int(af_limits.reserve(before, [], NONE).value)
        self.assertLess(af_limits.build_cache(before, 0, kept, NONE).value,
                        af_limits.build_cache(after, 0, kept, NONE).value)

    def test_the_cache_cap_shrinks_as_the_disk_fills(self) -> None:
        roomy = af_limits.Volume(total=110 * GB, free=60 * GB)
        tight = af_limits.Volume(total=110 * GB, free=14 * GB)
        kept = int(af_limits.reserve(tight, [], NONE).value)
        self.assertLess(af_limits.build_cache(tight, 9 * GB, kept, NONE).value,
                        af_limits.build_cache(roomy, 9 * GB, kept, NONE).value)
        full = af_limits.Volume(total=110 * GB, free=1 * GB)
        self.assertEqual(af_limits.build_cache(full, 0, kept, NONE).value, 0, "below the reserve: no cache kept")

    def test_the_reserve_follows_measured_builds(self) -> None:
        disk = af_limits.Volume(total=110 * GB, free=50 * GB)
        measured = af_limits.reserve(disk, [2 * GB, 9 * GB, 3 * GB], NONE)
        self.assertEqual(measured.value, int(9 * GB * af_limits.BUILD_MARGIN))
        self.assertIn("largest of the last 3 builds", measured.source)
        small = af_limits.reserve(disk, [100 * 1000**2], NONE)
        self.assertEqual(small.value, int(disk.total * af_limits.RESERVE_FLOOR_SHARE), "never below the floor")
        old_large = [50 * GB] + [1 * GB] * af_limits.RECENT_BUILDS
        self.assertLess(af_limits.reserve(disk, old_large, NONE).value, 50 * GB, "only recent builds count")

    def test_the_owner_value_wins(self) -> None:
        host, disk = PROFILES["small ssd"]
        chosen = settings(disk={"reserve": "20GB", "build_cache": "5%"}, builder={"memory": "4GiB", "cpus": 3},
                          runner={"per_repo": 5})
        kept = af_limits.reserve(disk, [50 * GB], chosen)
        self.assertEqual((kept.value, kept.source), (20 * GB, af_limits.SET))
        self.assertEqual(af_limits.build_cache(disk, 0, kept.value, chosen).value, int(disk.total * 0.05))
        self.assertEqual(af_limits.builder_memory(host, chosen).value, 4 * 1024**3)
        self.assertEqual(af_limits.builder_options(host, chosen),
                         ["memory=4096m", "cpu-period=100000", "cpu-quota=300000"])
        self.assertEqual(af_limits.runners_per_repo(host, chosen).value, 5)

    def test_settings_that_break_an_invariant_or_the_schema_are_refused(self) -> None:
        for bad in ({"disk": {"reserve": "30GB", "alert_free": "20GB"}}, {"disk": {"reserve": "95%"}},
                    {"disk": {"unknown": 1}}, {"cache": {}}, {"runner": {"per_repo": 0}},
                    {"builder": {"memory": "lots"}}, {"builder": {"cpus": True}}):
            with self.subTest(settings=bad), self.assertRaises(af_limits.LimitError):
                af_limits.Settings.parse(bad)
        disk = af_limits.Volume(total=110 * GB, free=14 * GB)
        with self.assertRaisesRegex(af_limits.LimitError, "not above the reserve"):
            af_limits.alert_free(disk, 30 * GB, settings(disk={"alert_free": "20GB"}))

    def test_sizes_read_as_docker_prints_them(self) -> None:
        for text, size in (("9.72GB", 9_720_000_000), ("13.58MB", 13_580_000), ("0B", 0), ("4.096kB", 4096),
                           ("512MiB", 512 * 1024**2), ("20GB", 20 * GB), ("800M", 800_000_000)):
            self.assertEqual(af_limits.parse_size(text), size, text)
        self.assertEqual(af_limits.total_line("ID x\nReclaimable:\t97.51MB\nTotal:\t\t97.51MB\n"), 97_510_000)
        self.assertEqual(af_limits.total_line(""), 0, "a builder that is not running holds nothing")
        self.assertEqual(af_limits.build_cache_line("Images\t3GB\nBuild Cache\t1.5GB\n"), 1_500_000_000)

    def test_time_to_full_follows_the_trend(self) -> None:
        falling = [(hour * 3600.0, (40 - hour) * GB) for hour in range(6)]
        self.assertAlmostEqual(af_limits.hours_until(falling, 10 * GB), 25.0, places=3)
        rising = [(hour * 3600.0, (30 + hour) * GB) for hour in range(6)]
        self.assertIsNone(af_limits.hours_until(rising, 10 * GB))
        self.assertIsNone(af_limits.hours_until(falling[:2], 10 * GB), "too little history")

    def test_build_history_keeps_the_latest_builds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            for size in range(af_limits.RECENT_BUILDS + 3):
                af_limits.record_build(size * GB, state)
            self.assertEqual(af_limits.load_builds(state), [size * GB for size in range(3, af_limits.RECENT_BUILDS + 3)])
            (state / af_limits.BUILDS_FILE).write_text("not json")
            self.assertEqual(af_limits.load_builds(state), [])

    def test_the_prune_falls_back_to_the_flag_this_docker_knows(self) -> None:
        calls = []

        def run(argv):
            calls.append(argv)
            return SimpleNamespace(returncode=0 if "--keep-storage" in argv else 1)

        self.assertEqual(af_limits.prune_cache(run, ["docker", "builder", "prune"], 123), "--keep-storage")
        self.assertEqual([argv[-2] for argv in calls], list(af_limits.CACHE_LIMIT_FLAGS))
        self.assertIsNone(af_limits.prune_cache(lambda argv: SimpleNamespace(returncode=1), ["docker"], 1))

    def test_runner_caps_print_for_the_runner_script(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "host.toml"
            path.write_text('[runner]\ncpus = 3\nmemory = "6GiB"\nper_repo = 4\n')
            output = StringIO()
            with redirect_stdout(output), mock.patch.object(af_limits.Host, "measure", return_value=SMALL_HOST):
                self.assertEqual(af_limits.main(["--settings", str(path), "runner-caps"]), 0)
            self.assertEqual(output.getvalue().split(), ["300%", "6144M", "4"])


class ReceiverLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.config = {"images": [{"name": "web"}]}
        self.source = self.root / "source"
        self.source.mkdir()

    def builder(self, docker: DiskDocker, settings: af_limits.Settings = NONE, **gigabytes: float):
        return af_receive.Builder(self.root / "build.lock", run=docker, disk_usage=free_space(**gigabytes),
                                  settings=settings, host=SMALL_HOST, state_dir=self.root)

    def test_a_failed_build_still_bounds_the_cache(self) -> None:
        docker = DiskDocker(build_fails=True)
        with self.assertRaisesRegex(af_receive.ReceiveError, "building web failed"):
            self.builder(docker, root=50, base=50).build(self.source, self.config, "example", "a" * 40)
        prunes = docker.prunes()
        self.assertEqual(len(prunes), 1)
        self.assertIn("--max-used-space", prunes[0], "bounded by size, not only by age")

    def test_the_cache_bound_follows_free_space(self) -> None:
        docker = DiskDocker()
        self.builder(docker, root=50, base=50).build(self.source, self.config, "example", "a" * 40)
        cap = int(docker.prunes()[0][-1])
        disk = af_limits.Volume(total=100 * GB, free=50 * GB)
        kept = int(af_limits.reserve(disk, af_limits.load_builds(self.root), NONE).value)
        self.assertEqual(cap, af_limits.build_cache(disk, docker.cache, kept, NONE).value)

    def test_each_build_records_its_disk_use(self) -> None:
        docker = DiskDocker()
        self.builder(docker, root=50, base=50).build(self.source, self.config, "example", "a" * 40)
        self.assertEqual(af_limits.load_builds(self.root), [docker.growth + 1_000_000_000],
                         "cache growth plus the loaded image")

    def test_the_reserve_comes_from_measured_builds(self) -> None:
        af_limits.record_build(20 * GB, self.root)
        docker = DiskDocker()
        with self.assertRaises(af_receive.ReceiveError) as caught:
            self.builder(docker, root=25, base=80).build(self.source, self.config, "example", "a" * 40)
        self.assertIn("keeps at least 30.0GB free (derived from the largest of the last 1 builds)", str(caught.exception))
        self.assertEqual(docker.builds(), [])

    def test_an_owner_reserve_wins_and_a_project_value_wins_over_it(self) -> None:
        owner = settings(disk={"reserve": "40GB"})
        with self.assertRaisesRegex(af_receive.ReceiveError, r"at least 40\.0GB free \(set in the host settings\)"):
            self.builder(DiskDocker(), owner, root=30, base=80).check_space(None, None, owner)
        self.builder(DiskDocker(), owner, root=30, base=80).check_space(20, None, owner)

    def test_the_builder_is_recreated_only_when_its_limits_change(self) -> None:
        wanted = " ".join(f'{key}="{value}"' for key, value in
                          (option.split("=", 1) for option in af_limits.builder_options(SMALL_HOST, NONE)))
        cases = {wanted: False, 'cpu-period="100000" cpu-quota="200000" memory="4g"': True}
        for options, recreated in cases.items():
            with self.subTest(options=options):
                docker = DiskDocker(driver_options=options)
                self.builder(docker, root=50, base=50)._ensure_builder(NONE, SMALL_HOST)
                commands = [argv[2] for argv in docker.calls if argv[1] == "buildx"]
                self.assertEqual("rm" in commands and "create" in commands, recreated, commands)
        docker = DiskDocker()
        self.builder(docker)._ensure_builder(NONE, SMALL_HOST)
        self.assertNotIn("rm", [argv[2] for argv in docker.calls], "limits it cannot read keep the cache")


class DiskWatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.sent: list[tuple[str, str]] = []
        self.free = 60 * GB
        self.clock = 0.0

    def watch(self, chosen: af_limits.Settings = NONE) -> af_monitor.DiskWatch:
        notifier = SimpleNamespace(send=lambda event, text: self.sent.append((event, text)))
        return af_monitor.DiskWatch(
            notifier=lambda hosts: notifier,
            docker_root=lambda: self.state,
            disk_usage=lambda path: SimpleNamespace(total=110 * GB, free=self.free),
            settings=lambda: chosen,
            state_dir=self.state,
            now=lambda: self.clock,
        )

    def test_low_space_alerts_once_and_recovers(self) -> None:
        watch = self.watch()
        self.assertTrue(watch.check([]))
        self.free = 15 * GB
        self.clock += 3600
        self.assertFalse(watch.check([]))
        self.assertFalse(watch.check([]))
        self.assertEqual([event for event, _ in self.sent], ["disk_low"])
        self.assertIn("below the alert level", self.sent[0][1])
        self.free = 60 * GB
        self.clock += 3600
        self.assertTrue(watch.check([]))
        self.assertEqual([event for event, _ in self.sent], ["disk_low", "disk_recovered"])

    def test_the_alert_fires_while_builds_still_pass(self) -> None:
        disk = af_limits.Volume(total=110 * GB, free=0)
        kept = af_limits.reserve(disk, [], NONE).value
        self.free = int(kept) + GB
        self.assertFalse(self.watch().check([]), "above the reserve, below the alert level")
        self.assertEqual(self.sent[0][0], "disk_low")

    def test_a_falling_trend_alerts_before_the_level_is_reached(self) -> None:
        watch = self.watch()
        for hour in range(8):
            self.free = (80 - hour * 2) * GB
            self.clock = hour * 3600.0
            watch.check([])
        self.assertEqual([event for event, _ in self.sent], ["disk_low"])
        self.assertIn("reaches the build reserve", self.sent[0][1])

    def test_unusable_settings_fall_back_to_derived_levels(self) -> None:
        self.free = 15 * GB
        bad = settings(disk={"alert_free": "5GB"})
        self.assertFalse(self.watch(bad).check([]))
        self.assertEqual(self.sent[0][0], "disk_low")


class HostLimitCommandTests(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        write(self.framework / "skills" / "release-delivery" / "assets" / "deploy" / "af_limits.py",
              (DEPLOY_ASSETS / "af_limits.py").read_text())

    def hosts(self) -> dict:
        return tomllib.loads((self.home / ".config" / "pakem" / "hosts.toml").read_text())["hosts"]

    def test_the_owner_sets_lists_and_clears_a_limit(self) -> None:
        self.assert_ok(self.pakem("host", "add", "prod", "--target", "admin@203.0.113.7"))
        set_reserve = self.pakem("host", "limit", "prod", "disk.reserve", "20GB")
        self.assert_ok(set_reserve)
        self.assertIn("pakem setup host prod", set_reserve.stdout)
        self.assert_ok(self.pakem("host", "limit", "prod", "runner.cpus", "3"))
        self.assertEqual(self.hosts()["prod"]["limits"], {"disk.reserve": "20GB", "runner.cpus": 3.0})
        listed = self.pakem("host", "limit", "prod", "--json")
        self.assert_ok(listed)
        self.assertEqual(json.loads(listed.stdout)["limits"]["disk.reserve"], "20GB")
        plain = self.pakem("host", "limit", "prod").stdout
        self.assertIn("disk.reserve: 20GB", plain)
        self.assertIn("builder.memory: derived on the host", plain)
        self.assert_ok(self.pakem("host", "limit", "prod", "disk.reserve", "--unset"))
        self.assertEqual(self.hosts()["prod"]["limits"], {"runner.cpus": 3.0})
        self.assert_ok(self.pakem("host", "add", "prod", "--no-proxy"))
        self.assertEqual(self.hosts()["prod"]["limits"], {"runner.cpus": 3.0}, "other host changes keep the limits")

    def test_invalid_values_are_refused_and_nothing_is_recorded(self) -> None:
        self.assert_ok(self.pakem("host", "add", "prod", "--target", "admin@203.0.113.7"))
        for arguments in (("disk.reserve", "lots"), ("disk.unknown", "1"), ("runner.per_repo", "two"),
                          ("disk.reserve", "95%")):
            with self.subTest(arguments=arguments):
                self.assert_fails(self.pakem("host", "limit", "prod", *arguments), code=2)
        self.assert_ok(self.pakem("host", "limit", "prod", "disk.reserve", "30GB"))
        refused = self.pakem("host", "limit", "prod", "disk.alert_free", "20GB")
        self.assert_fails(refused, code=2)
        self.assertIn("alert_free must be above disk.reserve", refused.stderr)
        self.assertEqual(self.hosts()["prod"]["limits"], {"disk.reserve": "30GB"})


if __name__ == "__main__":
    unittest.main()
