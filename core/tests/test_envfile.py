import os
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from roadrunner.envfile import load_dotenv


class LoadDotenvTests(SimpleTestCase):
    def _write(self, content):
        path = Path(tempfile.mkdtemp()) / ".env"
        path.write_text(content)
        return path

    def test_loads_keys_and_skips_comments_and_blanks(self):
        path = self._write("# a comment\nFOO_ENVTEST=bar\n\nNOEQUALS\n")
        os.environ.pop("FOO_ENVTEST", None)
        load_dotenv(path)
        self.assertEqual(os.environ.get("FOO_ENVTEST"), "bar")
        os.environ.pop("FOO_ENVTEST", None)

    def test_hash_inside_value_is_preserved(self):
        path = self._write("KEY_ENVTEST=ab#cd=ef\n")
        os.environ.pop("KEY_ENVTEST", None)
        load_dotenv(path)
        self.assertEqual(os.environ.get("KEY_ENVTEST"), "ab#cd=ef")
        os.environ.pop("KEY_ENVTEST", None)

    def test_existing_environment_variable_wins(self):
        path = self._write("EXIST_ENVTEST=fromfile\n")
        os.environ["EXIST_ENVTEST"] = "fromenv"
        load_dotenv(path)
        self.assertEqual(os.environ.get("EXIST_ENVTEST"), "fromenv")
        os.environ.pop("EXIST_ENVTEST", None)

    def test_missing_file_is_noop(self):
        load_dotenv(Path("/nonexistent/path/.env"))  # must not raise
