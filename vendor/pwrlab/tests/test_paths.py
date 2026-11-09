"""Path handed to LTspice: Wine (macOS / Linux), WSL running the Windows LTspice.exe, native Windows."""
import os
import unittest

from pwrlab import sim


class PathStyle(unittest.TestCase):
    def test_wine_is_the_default_off_windows(self):
        if os.name != "nt" and not sim.is_wsl():
            self.assertEqual(sim.path_style(["/Applications/LTspice.app/x/wine", "LTspice.exe"]), "wine")
        self.assertTrue(sim.win_path("/tmp/a.net", "wine").startswith("Z:/"))

    def test_wsl_paths_become_windows_paths(self):
        self.assertEqual(sim._mnt_to_windows("/mnt/c/Users/me/pwrlab/run.net"), "C:\\Users\\me\\pwrlab\\run.net")
        self.assertEqual(sim._mnt_to_windows("/home/me/run.net"), "/home/me/run.net")   # not on a Windows drive

    def test_override(self):
        os.environ["PWRLAB_PATH_STYLE"] = "native"
        try:
            self.assertEqual(sim.path_style(), "native")
        finally:
            del os.environ["PWRLAB_PATH_STYLE"]


if __name__ == "__main__":
    unittest.main()
