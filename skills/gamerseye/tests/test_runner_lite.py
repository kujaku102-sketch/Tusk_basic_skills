"""Flash-Lite 経路 (--lite) のモデルの決め方の試験。runner.py の resolve_model だけを取り出して試す (窓や API には触らない)。"""
import ast
import pathlib
import unittest

SRC = pathlib.Path(__file__).resolve().parent.parent / "gamerseye_core" / "runner.py"


def _load():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    keep = [
        n for n in tree.body
        if (isinstance(n, ast.Assign) and any(getattr(t, "id", "") in ("LITE_MODEL", "DEFAULT_MODEL") for t in n.targets))
        or (isinstance(n, ast.FunctionDef) and n.name == "resolve_model")
    ]
    ns = {"Optional": object}
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(SRC), "exec"), ns)
    return ns


class ResolveModelTest(unittest.TestCase):
    def setUp(self):
        self.ns = _load()
        self.r = self.ns["resolve_model"]

    def test_default(self):
        # 所有者 2026-11-16「全て Flash-lite でいこう」
        self.assertEqual(self.r(None, False, {}), "gemini-3.5-flash-lite")

    def test_env_default(self):
        self.assertEqual(self.r(None, False, {"COMPUTER_USE_MODEL": "gemini-3.8-flash-high"}), "gemini-3.8-flash-high")

    def test_lite(self):
        self.assertEqual(self.r(None, True, {}), "gemini-3.5-flash-lite")

    def test_lite_env(self):
        self.assertEqual(self.r(None, True, {"GAMERSEYE_LITE_MODEL": "gemini-flash-lite-latest"}), "gemini-flash-lite-latest")

    def test_lite_ignores_computer_use_model(self):
        self.assertEqual(self.r(None, True, {"COMPUTER_USE_MODEL": "gemini-3.8-flash-high"}), "gemini-3.5-flash-lite")

    def test_explicit_model_wins(self):
        self.assertEqual(self.r("gemini-3.8-flash-low", True, {"GAMERSEYE_LITE_MODEL": "x"}), "gemini-3.8-flash-low")


if __name__ == "__main__":
    unittest.main()
