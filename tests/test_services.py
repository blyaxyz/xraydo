from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from xraydo.cli import parser
from xraydo.health import _service_state
from xraydo.services import (
    CoreServiceError,
    control_service,
    core_version,
    service_logs,
    service_status,
)


def completed(
    command: list[str],
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


class ServiceTests(unittest.TestCase):
    def test_core_health_preserves_systemd_restart_state(self) -> None:
        with (
            mock.patch("xraydo.health.shutil.which", return_value="/usr/bin/systemctl"),
            mock.patch("xraydo.health.Path.is_dir", return_value=True),
            mock.patch(
                "xraydo.health._run",
                return_value=completed([], returncode=3, stdout="activating\n"),
            ),
        ):
            self.assertEqual(_service_state("xray"), "activating")

    def test_local_service_commands_are_exposed_for_both_cores(self) -> None:
        telemt = parser().parse_args(["telemt", "logs", "--lines", "25"])
        hysteria = parser().parse_args(["hysteria", "restart"])
        self.assertEqual((telemt.telemt_command, telemt.lines), ("logs", 25))
        self.assertEqual(hysteria.hysteria_command, "restart")

    def test_systemd_status_is_structured(self) -> None:
        output = "\n".join(
            (
                "LoadState=loaded",
                "ActiveState=active",
                "SubState=running",
                "MainPID=4321",
                "NRestarts=2",
                "ExecMainStartTimestamp=Fri 2026-10-30 10:00:00 +03",
            )
        )
        with (
            mock.patch("xraydo.services.init_system", return_value="systemd"),
            mock.patch(
                "xraydo.services._run",
                return_value=completed([], stdout=output),
            ) as run,
        ):
            status = service_status("telemt")
        self.assertEqual(status["state"], "active")
        self.assertEqual(status["pid"], 4321)
        self.assertEqual(status["restarts"], 2)
        self.assertIn("xraydo-telemt", run.call_args.args[0])

    def test_openrc_control_uses_service_name_and_action(self) -> None:
        with (
            mock.patch("xraydo.services.init_system", return_value="openrc"),
            mock.patch("xraydo.services._run", return_value=completed([])) as run,
        ):
            result = control_service("telemt", "restart")
        self.assertEqual(result["manager"], "openrc")
        run.assert_called_once_with(["rc-service", "xraydo-telemt", "restart"])

    def test_logs_are_bounded_and_use_journal_unit(self) -> None:
        with self.assertRaisesRegex(CoreServiceError, "service_invalid_lines"):
            service_logs("telemt", 0)
        with (
            mock.patch("xraydo.services.init_system", return_value="systemd"),
            mock.patch(
                "xraydo.services._run",
                return_value=completed([], stdout="one\ntwo\n"),
            ) as run,
        ):
            result = service_logs("telemt", 2)
        self.assertEqual(result["logs"], "one\ntwo")
        self.assertEqual(
            run.call_args.args[0],
            ["journalctl", "--unit", "xraydo-telemt", "--no-pager", "--lines", "2"],
        )

    def test_version_uses_official_telemt_flag(self) -> None:
        with (
            mock.patch("xraydo.services.shutil.which", return_value="/usr/local/bin/telemt"),
            mock.patch(
                "xraydo.services._run",
                return_value=completed([], stdout="telemt 3.5.14\n"),
            ) as run,
        ):
            version = core_version("telemt")
        self.assertEqual(version["version"], "telemt 3.5.14")
        run.assert_called_once_with(["/usr/local/bin/telemt", "--version"], timeout=10)

    def test_hysteria_version_skips_ascii_banner(self) -> None:
        output = "ASCII banner line\n\nVersion:\tv2.13.0\nBuildDate:\t2026-10-05\n"
        with (
            mock.patch("xraydo.services.shutil.which", return_value="/usr/local/bin/hysteria"),
            mock.patch(
                "xraydo.services._run",
                return_value=completed([], stdout=output),
            ),
        ):
            version = core_version("hysteria")
        self.assertEqual(version["version"], "v2.13.0")
