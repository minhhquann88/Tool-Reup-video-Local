import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LinuxX11PackagingTests(unittest.TestCase):
    def test_fontconfig_files_reject_color_fonts(self):
        for relative_path in (
            "linux-fontconfig.conf",
            "packaging/linux-fontconfig-bundled.conf",
        ):
            tree = ET.parse(ROOT / relative_path)
            color_values = tree.findall(
                ".//rejectfont/pattern/patelt[@name='color']/bool"
            )
            self.assertEqual(["true"], [node.text for node in color_values])

    def test_linux_build_requires_patched_xft_and_bundled_fonts(self):
        script = (ROOT / "build_linux.sh").read_text(encoding="utf-8")
        self.assertIn('XFT_VERSION="${XFT_VERSION:-2.3.8}"', script)
        self.assertIn('cp -Lf "$XFT_PREFIX/lib/libXft.so.2"', script)
        self.assertIn("DejaVuSansMono.ttf", script)
        self.assertIn("FONTCONFIG_FILE", script)

    def test_customtkinter_uses_polygon_shapes_on_linux(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('DrawEngine.preferred_drawing_method = "polygon_shapes"', source)
        self.assertIn('else "DejaVu Sans Mono"', source)


if __name__ == "__main__":
    unittest.main()
