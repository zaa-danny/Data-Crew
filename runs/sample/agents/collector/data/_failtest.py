import sys
sys.path.insert(0, ".")
from pipeline.collect import collect, CollectError

# 1) fetch failure -> CollectError
try:
    collect({"pages": ["http://127.0.0.1:8000/sites/nonexistent.html"]})
    print("FAIL: no error on bad fetch")
except CollectError as e:
    print("OK fetch error:", str(e)[:60])

# 2) parse failure (no records) -> CollectError
try:
    collect({"pages": ["http://127.0.0.1:8000/"]})
    print("FAIL: no error on empty parse")
except CollectError as e:
    print("OK parse error:", str(e)[:60])

# 3) empty pages -> CollectError
try:
    collect({"pages": []})
    print("FAIL: no error on empty pages")
except CollectError as e:
    print("OK empty pages:", str(e)[:60])
