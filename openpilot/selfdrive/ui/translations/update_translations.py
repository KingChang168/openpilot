#!/usr/bin/env python3
from itertools import chain
import os
from openpilot.common.basedir import BASEDIR
from openpilot.system.ui.lib.multilang import SYSTEM_UI_DIR, UI_DIR, TRANSLATIONS_DIR, multilang
from openpilot.selfdrive.ui.translations.potools import extract_strings, extract_offroad_strings, generate_pot, merge_po, init_po

LANGUAGES_FILE = os.path.join(str(TRANSLATIONS_DIR), "languages.json")
POT_FILE = os.path.join(str(TRANSLATIONS_DIR), "app.pot")


def update_translations():
  files = []
  for root, _, filenames in chain(os.walk(SYSTEM_UI_DIR),
                                  os.walk(os.path.join(str(UI_DIR), "widgets")),
                                  os.walk(os.path.join(str(UI_DIR), "layouts")),
                                  os.walk(os.path.join(str(UI_DIR), "onroad")),
                                  os.walk(os.path.join(str(UI_DIR), "mici")),
                                  os.walk(os.path.join(str(UI_DIR), "sunnypilot"))):
    for filename in filenames:
      if filename.endswith(".py"):
        files.append(os.path.relpath(os.path.join(root, filename), BASEDIR).replace(os.sep, "/"))

  # Extract translatable strings and generate .pot template
  entries = extract_strings(files, BASEDIR)
  seen = {entry.msgid: entry for entry in entries}
  for entry in extract_offroad_strings(BASEDIR):
    if entry.msgid in seen:
      seen[entry.msgid].source_refs.extend(entry.source_refs)
    else:
      entries.append(entry)
      seen[entry.msgid] = entry
  generate_pot(entries, POT_FILE)

  # Generate/update translation files for each language
  for name in multilang.languages.values():
    po_file = os.path.join(str(TRANSLATIONS_DIR), f"app_{name}.po")
    if os.path.exists(po_file):
      merge_po(po_file, POT_FILE)
    else:
      init_po(POT_FILE, po_file, name)


if __name__ == "__main__":
  update_translations()
