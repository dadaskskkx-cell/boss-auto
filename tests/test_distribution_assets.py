from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class DistributionAssetsTests(unittest.TestCase):
    def test_build_dist_script_exists_and_packages_script_version(self):
        builder = PROJECT_ROOT / "build_dist.sh"
        self.assertTrue(builder.exists(), "build_dist.sh should exist")

        content = builder.read_text(encoding="utf-8")
        self.assertIn("src/script_runner.py", content)
        self.assertIn("src/profile_builder.py", content)
        self.assertIn("src/profile_editor.py", content)
        self.assertIn("launch.command", content)
        self.assertIn("docs/给同事使用说明.md", content)
        self.assertIn('cp "$ROOT_DIR/config/config.yaml.example" "$DIST_DIR/config/config.yaml"', content)
        self.assertNotIn('cp "$ROOT_DIR/config/config.yaml" "$DIST_DIR/config/config.yaml"', content)
        self.assertNotIn("web.py", content)

    def test_launch_command_exists_and_bootstraps_profile_editor(self):
        launcher = PROJECT_ROOT / "launch.command"
        self.assertTrue(launcher.exists(), "launch.command should exist")

        content = launcher.read_text(encoding="utf-8")
        self.assertIn("python3.12", content)
        self.assertIn("PYTHON_BIN", content)
        self.assertIn('cp "config/config.yaml.example" "config/config.yaml"', content)
        self.assertIn('grep -q "sk-xxx" "config/config.yaml"', content)
        self.assertIn("pip install -r requirements.txt", content)
        self.assertIn("python -m src.profile_editor", content)

    def test_start_sh_runs_direct_script(self):
        starter = PROJECT_ROOT / "start.sh"
        self.assertTrue(starter.exists(), "start.sh should exist")

        content = starter.read_text(encoding="utf-8")
        self.assertIn("python -m src.script_runner", content)
        self.assertNotIn("streamlit run web.py", content)

    def test_requirements_drop_optional_mcp_dependency_for_distribution(self):
        requirements = PROJECT_ROOT / "requirements.txt"
        self.assertTrue(requirements.exists(), "requirements.txt should exist")

        content = requirements.read_text(encoding="utf-8")
        self.assertNotIn("\nmcp", content.lower())


if __name__ == "__main__":
    unittest.main()
