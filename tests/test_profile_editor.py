from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import yaml

from src import profile_editor


class ProfileEditorTests(unittest.TestCase):
    def test_main_saves_profile_and_invokes_script_runner(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            profile_path = root / "config" / "profile.yaml"

            answers = iter(
                [
                    "法务总监",
                    "本科及以上学历",
                    "5年以上法务经验",
                    "/done",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "y",
                ]
            )

            with patch.object(profile_editor, "PROJECT_ROOT", root), \
                patch.object(profile_editor, "PROFILE_PATH", profile_path), \
                patch("builtins.input", side_effect=lambda prompt="": next(answers)), \
                patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                result = profile_editor.main()

            self.assertEqual(result, 0)
            self.assertTrue(profile_path.exists())

            saved = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["job_title"], "法务总监")
            self.assertIn("法务", saved["rules"]["required_keywords_any"])
            mock_run.assert_called_once_with(
                [profile_editor.sys.executable, "-m", "src.script_runner"],
                cwd=root,
            )


if __name__ == "__main__":
    unittest.main()
