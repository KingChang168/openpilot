import ast
import json
import re
import string
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from openpilot.selfdrive.ui.translations.potools import parse_po
from openpilot.system.ui.lib import multilang as lang


ROOT = Path(__file__).resolve().parents[4]
PO = ROOT / 'openpilot/selfdrive/ui/translations/app_zh-CHT.po'


class TestTraditionalChineseSync(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    _, cls.entries = parse_po(PO)
    cls.translations, _ = lang.load_translations(PO)

  def test_no_duplicate_or_empty_translations(self):
    assert all(count == 1 for count in Counter(e.msgid for e in self.entries).values())
    assert all(e.msgstr or e.msgstr_plural for e in self.entries)

  def test_c4_confirmation_and_onboarding_labels_translated(self):
    translations = {**lang.C4_ZH_CHT_TRANSLATIONS, **self.translations}
    layout_dir = ROOT / 'openpilot/selfdrive/ui/mici/layouts'
    for relative in ('onboarding.py', 'settings/developer.py', 'settings/toggles.py'):
      tree = ast.parse((layout_dir / relative).read_text(encoding='utf-8'))
      for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
          continue
        if node.func.id not in ('BigButton', 'GreyBigButton', 'BigConfirmationCircleButton'):
          continue
        for arg in node.args[:2]:
          if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value:
            assert translations.get(arg.value), (relative, node.lineno, arg.value)

  def test_c4_compatibility_table_has_no_duplicate_keys(self):
    tree = ast.parse((ROOT / 'openpilot/system/ui/lib/multilang.py').read_text(encoding='utf-8'))
    for node in tree.body:
      if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'C4_ZH_CHT_TRANSLATIONS' for t in node.targets):
        keys = [ast.literal_eval(key) for key in node.value.keys]
        assert len(keys) == len(set(keys))

  def test_c4_model_and_camera_labels(self):
    translations = {**lang.C4_ZH_CHT_TRANSLATIONS, **self.translations}
    for text in ('record & upload cabin camera', 'small models', 'big models', 'small model', 'big model',
                 'active', 'Default', 'unavailable', 'getting ready', 'queued'):
      assert translations.get(text) and translations[text] != text, text
    assert translations['active model'] == '目前模型'

  def test_placeholders_preserved(self):
    def placeholders(text):
      fields = [field for _, field, _, _ in string.Formatter().parse(text) if field is not None]
      return sorted(fields + re.findall(r'%(?:n|\d+)', text))

    for entry in self.entries:
      for translated in ([entry.msgstr] if not entry.is_plural else entry.msgstr_plural.values()):
        assert placeholders(entry.msgid) == placeholders(translated), entry.msgid

  def test_new_chestnut_alerts_are_translated(self):
    alerts = json.loads((ROOT / 'openpilot/selfdrive/selfdrived/alerts_offroad.json').read_text())
    for key, alert in alerts.items():
      if key.startswith('Offroad_Chestnut') and alert['text'] != '%1':
        assert self.translations.get(alert['text']), key
    for text in ('install now', 'Big Model Ready',
                 "Failed to get available branches. Ensure you're connected to the internet and try again."):
      assert self.translations.get(text), text

  def test_offroad_alert_translates_before_substitution(self):
    with patch.object(lang, 'tr', side_effect=lambda text: self.translations.get(text, text)):
      text = lang.translate_offroad_alert(
        'Chestnut overheated. Ensure good airflow. Current GPU temperature is %1.', '85 °C')
      assert '過熱' in text and '85 °C' in text and '%1' not in text
      extra = 'Chestnut power restored. 12V is stable again, cycle ignition.'
      assert lang.translate_offroad_alert('%1', extra) == self.translations[extra]

  def test_untranslated_alert_is_unchanged(self):
    with patch.object(lang, 'tr', side_effect=lambda text: text):
      assert lang.translate_offroad_alert('GPU temperature %1', '85 °C') == 'GPU temperature 85 °C'


if __name__ == '__main__':
  unittest.main()
