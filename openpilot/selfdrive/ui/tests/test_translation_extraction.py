import ast
from collections import Counter
from pathlib import Path
import re
import string
import tempfile
import unittest

from openpilot.selfdrive.ui.translations.potools import extract_strings, extract_runtime_alert_strings, parse_po, _static_string

ROOT = Path(__file__).resolve().parents[4]
PO = ROOT / 'openpilot/selfdrive/ui/translations/app_zh-CHT.po'


class TestTranslationExtraction(unittest.TestCase):
  def extract(self, source):
    with tempfile.TemporaryDirectory() as folder:
      path = Path(folder) / 'test.py'
      path.write_text(source, encoding='utf-8')
      return extract_strings(['test.py'], folder)

  def test_static_concatenation(self):
    entries = self.extract('tr("first " + "second"); tr_noop("first " + "second")')
    self.assertEqual([e.msgid for e in entries], ['first second'])

  def test_static_plural_concatenation(self):
    entries = self.extract('trn("{} " + "item", "{} " + "items", count)')
    self.assertEqual((entries[0].msgid, entries[0].msgid_plural), ('{} item', '{} items'))

  def test_dynamic_expressions_not_evaluated(self):
    self.assertEqual(self.extract('tr(f"Value {value}"); tr("Value " + variable); tr(do_not_call())'), [])

  def test_translate_before_format(self):
    entries = self.extract('tr("Backing up {}%").format(progress)')
    self.assertEqual(entries[0].msgid, 'Backing up {}%')

  def test_runtime_alert_extraction_is_static(self):
    with tempfile.TemporaryDirectory() as folder:
      path = Path(folder) / 'openpilot/sunnypilot/selfdrive/selfdrived/events.py'
      path.parent.mkdir(parents=True)
      path.write_text('raise RuntimeError("must not import")\nAlert("Blendful ON", "")\nAlert(dynamic, "Manual " + "Steering")', encoding='utf-8')
      self.assertEqual({e.msgid for e in extract_runtime_alert_strings(folder)}, {'Blendful ON', 'Manual Steering'})

  def test_catalog_has_no_empty_or_duplicate_entries(self):
    _, entries = parse_po(PO)
    self.assertFalse([s for s, n in Counter(e.msgid for e in entries).items() if n > 1])
    self.assertTrue(all(e.msgstr or (e.msgstr_plural and all(e.msgstr_plural.values())) for e in entries))

  def test_placeholders_and_html_preserved(self):
    def placeholders(text):
      return sorted([(name, spec, conv) for _, name, spec, conv in string.Formatter().parse(text) if name is not None])
    _, entries = parse_po(PO)
    for entry in entries:
      for translated in entry.msgstr_plural.values() if entry.is_plural else [entry.msgstr]:
        with self.subTest(text=entry.msgid):
          self.assertEqual(placeholders(entry.msgid), placeholders(translated))
          self.assertEqual(re.findall(r'%(?:n|\d+)', entry.msgid), re.findall(r'%(?:n|\d+)', translated))
          self.assertEqual(re.findall(r'</?[a-zA-Z]+[^>]*>', entry.msgid), re.findall(r'</?[a-zA-Z]+[^>]*>', translated))

  def test_all_static_ui_calls_have_chinese(self):
    _, entries = parse_po(PO)
    translated = {e.msgid for e in entries if e.msgstr or e.msgstr_plural}
    for base in ('openpilot/selfdrive/ui', 'openpilot/system/ui'):
      for path in (ROOT / base).rglob('*.py'):
        if 'tests' in path.parts or 'translations' in path.parts:
          continue
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
          if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ('tr', 'tr_noop', 'trn') and node.args:
            if text := _static_string(node.args[0]):
              self.assertIn(text, translated, (path, node.lineno, text))

  def test_runtime_alert_catalog_is_complete(self):
    _, entries = parse_po(PO)
    translations = {e.msgid: e.msgstr for e in entries}
    for entry in extract_runtime_alert_strings(ROOT):
      self.assertTrue(translations.get(entry.msgid), entry.msgid)
    self.assertEqual(translations['Blendful ON'], 'Blendful 已開啟')
    self.assertEqual(translations['Blendful OFF'], 'Blendful 已關閉')

  def test_readable_dynamic_templates(self):
    _, entries = parse_po(PO)
    translations = {e.msgid: e.msgstr for e in entries}
    self.assertEqual(translations['Backing up {}%'].format(42.5), '正在備份 42.5%')
    self.assertEqual(translations['Restoring {}%'].format(100), '正在還原 100%')
    self.assertIn('30 km/h', translations['Only works above {} {}.'].format(30, 'km/h'))


if __name__ == '__main__':
  unittest.main()
