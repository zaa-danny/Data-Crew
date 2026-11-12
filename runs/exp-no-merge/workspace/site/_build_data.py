"""Generate site/data.json from data/summary.json and data/records.jsonl."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SUMMARY = os.path.join(ROOT, "data", "summary.json")
RECORDS = os.path.join(ROOT, "data", "records.jsonl")
OUT = os.path.join(HERE, "data.json")


def main():
    with open(SUMMARY, "r", encoding="utf-8") as fh:
        summary = json.load(fh)
    records = []
    with open(RECORDS, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    payload = {"summary": summary, "records": records}
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    print("wrote", OUT, "records:", len(records))


if __name__ == "__main__":
    main()
