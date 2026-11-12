"""Generate site/data.json from data/summary.json and data/records.jsonl."""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SUMMARY = ROOT / "data" / "summary.json"
RECORDS = ROOT / "data" / "records.jsonl"
OUT = ROOT / "site" / "data.json"


def main() -> None:
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    records = []
    for line in RECORDS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    payload = {"summary": summary, "records": records}
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT} with {len(records)} records")


if __name__ == "__main__":
    main()
