import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import main


class UrlTests(unittest.TestCase):
    def test_multi_part_domains_are_not_truncated(self):
        for value in (
            "example.co.uk/a",
            "example.co.jp/a",
            "example.com.cn/a",
            "example.org.uk",
        ):
            with self.subTest(value=value):
                self.assertIsNotNone(main.parse_openable_url(value))

    def test_ocr_pattern_keeps_full_multi_part_domain(self):
        text = "访问 example.co.uk/a 和 example.com.cn/path"
        matches = [match.group(0) for match in main.URL_PATTERN.finditer(text)]
        self.assertEqual(
            matches,
            ["example.co.uk/a", "example.com.cn/path"],
        )

    def test_only_http_and_https_are_openable(self):
        self.assertIsNone(main.parse_openable_url("file:///C:/Windows/calc.exe"))
        self.assertIsNone(main.parse_openable_url("javascript:alert(1)"))
        self.assertIsNone(main.parse_openable_url("--gpu-launcher=calc"))
        self.assertIsNone(main.parse_openable_url("user@example.com"))
        self.assertIsNone(main.parse_openable_url("hello world"))
        self.assertEqual(
            main.parse_openable_url("https://example.com/a?b=1"),
            "https://example.com/a?b=1",
        )

    def test_plain_text_payload_is_preserved(self):
        self.assertEqual(main.as_open_url("hello world"), "hello world")


class DedupeTests(unittest.TestCase):
    def test_path_case_is_significant(self):
        boxes = [
            {"url": "https://example.com/AbC", "kind": "qrcode", "openable": True},
            {"url": "https://example.com/abc", "kind": "text", "openable": True},
        ]
        self.assertEqual(len(main.dedupe_boxes(boxes)), 2)

    def test_host_case_is_ignored_and_qr_wins(self):
        boxes = [
            {"url": "http://Example.COM/a", "kind": "text", "openable": True},
            {"url": "http://example.com/a", "kind": "qrcode", "openable": True},
        ]
        result = main.dedupe_boxes(boxes)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["kind"], "qrcode")


class QrFixtureTests(unittest.TestCase):
    def test_text_qr_is_detected_as_non_openable_content(self):
        import cv2

        image_path = Path(__file__).parent / "assets" / "text_qr.png"
        image = cv2.imread(str(image_path))
        self.assertIsNotNone(image)

        boxes = main.detect_qrcodes(image)
        self.assertEqual(len(boxes), 1)
        self.assertEqual(boxes[0]["url"], "这是一条文字内容二维码")
        self.assertFalse(boxes[0]["openable"])


class ConfigTests(unittest.TestCase):
    def test_invalid_types_fall_back_to_defaults(self):
        cfg = main._sanitize_config({
            "browser_path": 123,
            "hotkey": "",
            "autostart": "yes",
            "confirm_open": 0,
        })
        self.assertEqual(cfg, main.DEFAULT_CONFIG)

    def test_atomic_save_and_load(self):
        original_path = main.CONFIG_PATH
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                main.CONFIG_PATH = str(Path(temp_dir) / "config.json")
                expected = {
                    "browser_path": r"C:\Browser\browser.exe",
                    "hotkey": "ctrl+alt+x",
                    "autostart": True,
                    "confirm_open": False,
                }
                self.assertTrue(main.save_config(expected))
                self.assertEqual(main.load_config(), expected)
        finally:
            main.CONFIG_PATH = original_path


class OpenUrlTests(unittest.TestCase):
    def test_non_http_payload_is_rejected(self):
        self.assertFalse(main.open_url("--gpu-launcher=calc", {}))


class ToastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_error_toast_has_retry_button(self):
        from PySide6.QtWidgets import QPushButton

        toast = main.Toast("test error", is_error=True, title="错误")
        texts = [button.text() for button in toast.findChildren(QPushButton)]
        self.assertIn("重新框选", texts)
        self.assertIn("✕", texts)
        toast.close()

    def test_success_toast_has_only_close_button(self):
        from PySide6.QtWidgets import QPushButton

        toast = main.Toast(
            "https://example.com", title="已打开链接", show_retry=False
        )
        texts = [button.text() for button in toast.findChildren(QPushButton)]
        self.assertEqual(texts, ["✕"])
        toast.close()

    def test_toast_starts_auto_close_timer(self):
        toast = main.Toast("test", title="提示", tone="info", show_retry=False)
        toast.show_animated()
        self.assertTrue(toast._auto_timer.isActive())
        toast.close()


if __name__ == "__main__":
    unittest.main()
