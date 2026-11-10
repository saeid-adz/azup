"""Fast offline regression tests (no network, nothing is installed)."""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import azup  # noqa: E402


class CatalogTests(unittest.TestCase):
    def test_keys_are_unique(self):
        keys = [t.key for t in azup.CATALOG]
        self.assertEqual(len(keys), len(set(keys)))

    def test_every_package_has_a_source(self):
        for t in azup.CATALOG:
            if t.kind == "pkg":
                self.assertTrue(t.brew or t.cask or t.winget or t.ghtar or t.snap, t.key)

    def test_commands_build_for_every_supported_tool(self):
        for t in azup.TOOLS:
            for verb in ("install", "update"):
                if t.kind == "pkg" and azup.spec(t)[0] == "tarball":
                    continue                      # installed in-process, not by a command
                self.assertTrue(azup.build_cmd(t, verb), (t.key, verb))

    def test_version_matches_pyproject(self):
        with open(os.path.join(os.path.dirname(azup.__file__), "pyproject.toml"), encoding="utf-8") as f:
            pyproject = f.read()
        self.assertEqual(re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1), azup.VERSION)


class NameResolutionTests(unittest.TestCase):
    """`az` (Azure CLI) and `Az` (PowerShell module) once collided."""
    def test_az_is_the_cli(self):
        self.assertEqual(azup.resolve(["az"])[0].key, "az")

    def test_capital_az_is_the_powershell_module(self):
        self.assertEqual(azup.resolve(["Az"])[0].key, "Az")

    def test_aliases(self):
        self.assertEqual(azup.resolve(["azps"])[0].key, "Az")
        self.assertEqual(azup.resolve(["graph"])[0].key, "Microsoft.Graph")


class HelperTests(unittest.TestCase):
    def test_version_tuple(self):
        self.assertGreater(azup.vtuple("v1.10.0"), azup.vtuple("1.9.9"))
        self.assertEqual(azup.ver_of("azure-dev-cli_1.37.3"), "1.37.3")
        self.assertEqual(azup.ver_of("v16.5.0-November2026"), "16.5.0")

    def test_durations(self):
        self.assertEqual(azup.parse_dur("7d"), 7 * 86400)
        self.assertIsNone(azup.parse_dur("soon"))

    def test_schedule_time_validation(self):
        self.assertEqual(azup.parse_at("09:05"), (9, 5))
        with self.assertRaises(ValueError):
            azup.parse_at("25:00")

    def test_release_notes_rendering_drops_boilerplate(self):
        body = "Install-Module snippet\n```\nInstall-Module Az\n```\n### Docker images\n- mcr.example/x\n#### Notes\n* Fixed a bug [#1](http://x)"
        lines, _ = azup.render_notes(body, 80, 20)
        text = "\n".join(lines)
        self.assertIn("Fixed a bug", text)
        self.assertNotIn("mcr.example", text)

    def test_shell_hook_block_roundtrip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, ".zshrc")
            with open(path, "w") as f:
                f.write("export A=1\n")
            self.assertTrue(azup.add_block(path, azup.SH_BODY))
            self.assertFalse(azup.add_block(path, azup.SH_BODY))      # idempotent
            self.assertTrue(azup.remove_block(path))
            with open(path) as f:
                self.assertEqual(f.read().strip(), "export A=1")


if __name__ == "__main__":
    unittest.main()
