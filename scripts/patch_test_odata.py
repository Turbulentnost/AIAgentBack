from pathlib import Path

p = Path("tests/test_odata_attached_file.py")
text = p.read_text(encoding="utf-8")
needle = 'entity="Document_'
idx = text.find(needle, text.find("test_odata_integration_attach_files_delegates_to_client"))
if idx == -1:
    raise SystemExit("entity line not found")
line_end = text.find("\n", idx)
line = text[idx:line_end]
if "file_author_key" not in text[idx:idx + 200]:
    text = text[:line_end] + ",\n        file_author_key=AUTHOR_KEY" + text[line_end:]
    p.write_text(text, encoding="utf-8")
    print("patched file_author_key")
