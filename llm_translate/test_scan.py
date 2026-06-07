"""Test scan_ftl_files directly in-process to verify it works."""
import sys; sys.path.insert(0, ".")
import threading, time
import llm_translate.app as app

# Reset globals for clean test
app._file_data = {}
app._all_items = {}
app._scan_done = False
app._scan_progress = {"total": 0, "done": 0, "phase": ""}

# Try with the EXISTING lock from the module
errors = []
def wrapped():
    try:
        app.scan_ftl_files(force=True)
    except Exception as e:
        import traceback
        errors.append((e, traceback.format_exc()))

t = threading.Thread(target=wrapped, daemon=True)
t.start()

for i in range(30):
    time.sleep(2)
    print(f"poll {i}: done={app._scan_done} files={len(app._file_data)} items={len(app._all_items)} progress={app._scan_progress.get('done')}/{app._scan_progress.get('total')}")
    if app._scan_done or errors:
        break

if errors:
    for e, tb in errors:
        print(f"ERROR: {e}\n{tb}")
else:
    print(f"\nFinal: files={len(app._file_data)} items={len(app._all_items)}")
    keys = sorted(app._file_data.keys())[:3]
    for k in keys:
        fd = app._file_data[k]
        print(f"  {k}: {len(fd.items)} items, first={fd.items[0].key if fd.items else 'none'}")

# Also check what the API would see
with app._lock:
    print(f"\nUnder lock: files={len(app._file_data)} items={len(app._all_items)}")
