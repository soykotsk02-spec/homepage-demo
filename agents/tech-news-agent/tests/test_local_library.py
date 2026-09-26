import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.sax.saxutils import escape
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import local_library as library


RESEARCH = "AI平台的商业模式可以从定价、网络效应与开发者生态三个方面进行分析。云计算产品的收入与成本结构影响平台竞争策略，数据与模型服务的互补关系也影响市场结构。研究时需要区分概念框架、模型假设以及需要进一步检验的实际证据。"


class LocalLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "research.docx"
        self.manifest = self.root / "manifest.json"
        self.run = self.root / "run"
        self.manifest.write_text(json.dumps({"sources": [{"id": "research", "title": "AI平台研究", "path": str(self.source)}]}, ensure_ascii=False), encoding="utf-8")

    def docx(self, paragraphs=None, xml=None):
        if xml is None:
            body = "".join(f"<w:p><w:r><w:t>{escape(p)}</w:t></w:r></w:p>" for p in paragraphs)
            xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + '</w:body></w:document>').encode("utf-8")
        with ZipFile(self.source, "w") as archive:
            archive.writestr("word/document.xml", xml)
            archive.writestr("word/_rels/document.xml.rels", '<Relationships><Relationship Target="https://invalid.example/private" TargetMode="External"/></Relationships>')

    def collect(self, news=None):
        return library.collect_local_context(self.manifest, self.run, news or {})

    def test_real_docx_xml_extraction_preserves_original_paragraph_numbers(self):
        self.docx(["标题", "", RESEARCH])
        result = self.collect()
        self.assertEqual(result["assets"][0]["paragraphCount"], 3)
        self.assertEqual(result["chunks"][0]["chunkId"], "research:p3")
        self.assertEqual(result["chunks"][0]["anchor"], "正文段落 3")
        self.assertEqual(result["chunks"][0]["text"], RESEARCH)
        self.assertEqual(len(result["assets"][0]["sha256"]), 64)
        self.assertNotIn(str(self.source), json.dumps(result))
        evidence = json.loads((self.run / "local-evidence.json").read_text(encoding="utf-8"))
        self.assertEqual(evidence["sources"][0]["path"], str(self.source))
        self.assertTrue(evidence["freshRead"])
        self.assertFalse(evidence["cacheUsed"])

    def test_deleted_source_never_silently_reuses_saved_context(self):
        self.docx([RESEARCH])
        self.collect()
        self.source.unlink()
        with self.assertRaises(library.LocalContextError):
            self.collect()
        saved = json.loads((self.run / "local-context.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["chunks"], [])
        self.assertTrue(any("缺失" in limitation for limitation in saved["limitations"]))

    def test_source_is_reread_and_hash_changes(self):
        self.docx([RESEARCH])
        before = self.collect()
        self.docx([RESEARCH + "本次补充了订阅模式的比较。"])
        after = self.collect()
        self.assertNotEqual(before["assets"][0]["sha256"], after["assets"][0]["sha256"])
        self.assertIn("本次补充", after["chunks"][0]["text"])

    def test_private_identity_path_and_credential_paragraphs_are_excluded(self):
        unsafe = [
            "邮件 demo@example.com", "电话 13800138000", "身份证：000000000000000000",
            "作者姓名：测试姓名", "文件 " + "Z:" + chr(92) + "synthetic-fixture.docx",
            "文件 " + "/" + "home/synthetic-fixture/private.txt", "api_key=TEST_ONLY_INVALID_KEY",
            "password=test-only-value", "授权码：test-only-value",
        ]
        self.docx([RESEARCH + item for item in unsafe] + [RESEARCH + "API接口与密钥管理也是平台服务的研究主题。"])
        result = self.collect()
        self.assertEqual(len(result["chunks"]), 1)
        self.assertIn("API接口与密钥管理", result["chunks"][0]["text"])
        self.assertEqual(result["chunks"][0]["chunkId"], f"research:p{len(unsafe)+1}")

    def test_size_limit_is_explicit(self):
        self.docx([RESEARCH])
        with patch.object(library, "MAX_XML_BYTES", 40), self.assertRaises(library.LocalContextError):
            self.collect()
        context = json.loads((self.run / "local-context.json").read_text(encoding="utf-8"))
        self.assertTrue(any("大小限制" in item for item in context["limitations"]))

    def test_dtd_is_rejected_in_utf8_and_utf16(self):
        text = '<!DOCTYPE document [<!ENTITY secret "unsafe">]><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>&secret;</w:t></w:r></w:p></w:body></w:document>'
        for encoding in ("utf-8", "utf-16"):
            with self.subTest(encoding=encoding):
                self.docx(xml=text.encode(encoding))
                with self.assertRaises(library.LocalContextError):
                    self.collect()
                context = json.loads((self.run / "local-context.json").read_text(encoding="utf-8"))
                self.assertTrue(any("DTD" in item for item in context["limitations"]))

    def test_paragraph_and_character_budgets(self):
        self.docx([(RESEARCH * 5) + f"研究组 {n}。" for n in range(30)])
        result = self.collect()
        self.assertLessEqual(len(result["chunks"]), 12)
        self.assertLessEqual(sum(len(c["text"]) for c in result["chunks"]), 5000)
        self.assertTrue(all(80 <= len(c["text"]) <= 1200 for c in result["chunks"]))

    def test_long_paragraph_retains_original_anchor(self):
        self.docx(["短标题", RESEARCH * 20])
        result = self.collect()
        self.assertEqual(len(result["chunks"]), 1)
        self.assertEqual(result["chunks"][0]["chunkId"], "research:p2")
        self.assertEqual(result["chunks"][0]["anchor"], "正文段落 2")
        self.assertLessEqual(len(result["chunks"][0]["text"]), 1200)

    def test_missing_one_source_is_explicit_but_other_source_still_works(self):
        self.docx([RESEARCH])
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["sources"].append({"id": "missing", "title": "另一研究", "path": str(self.root / "absent.docx")})
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        result = self.collect()
        self.assertEqual(len(result["chunks"]), 1)
        self.assertTrue(any("missing" in item and "缺失" in item for item in result["limitations"]))

    def test_empty_sources_raise_and_write_explicit_empty_context(self):
        self.manifest.write_text('{"sources": []}', encoding="utf-8")
        with self.assertRaises(library.LocalContextError):
            self.collect()
        self.assertEqual(json.loads((self.run / "local-context.json").read_text(encoding="utf-8"))["chunks"], [])


if __name__ == "__main__":
    unittest.main()
