try:
    import _isolation  # noqa: F401  (first: keeps tests off the real machine)
except ImportError:  # run as tests.<module> from the repository root
    from tests import _isolation  # noqa: F401

import os
import pathlib
import unittest

TESTS = pathlib.Path(__file__).resolve().parent


class IsolationTests(unittest.TestCase):
    """Every test module must import the isolation module before app code."""

    def test_every_test_module_isolates_itself_first(self) -> None:
        import ast

        for path in sorted(TESTS.glob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            body = [node for node in tree.body if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))]
            body = [node for node in body if not (isinstance(node, ast.ImportFrom) and node.module == "__future__")]
            first = body[0] if body else None
            imports_isolation = isinstance(first, ast.Try) and any(
                isinstance(node, (ast.Import, ast.ImportFrom))
                and any(alias.name == "_isolation" for alias in node.names)
                for node in first.body
            )
            with self.subTest(module=path.name):
                self.assertTrue(imports_isolation, f"{path.name} must import _isolation before anything else")

    def test_the_real_home_and_running_copy_are_out_of_reach(self) -> None:
        from app import main, settings

        root = os.environ["DCF_TEST_ISOLATED"]
        self.assertTrue(str(settings.config_dir()).startswith(root))
        self.assertTrue(str(pathlib.Path.home()).startswith(root))
        self.assertTrue(main.SERVER_NAME.endswith(os.environ["DCF_INSTANCE_SUFFIX"]))


if __name__ == "__main__":
    unittest.main()
