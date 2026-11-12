"""Generate site/data.json from the pipeline outputs (never typed by hand)."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def main():
    with open(os.path.join(ROOT, "data", "summary.json"), encoding="utf-8") as fh:
        summary = json.load(fh)
    records = []
    with open(os.path.join(ROOT, "data", "records.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    payload = {"summary": summary, "records": records}
    out = os.path.join(HERE, "data.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=False)
        fh.write("\n")
    print("wrote", out, "records:", len(records))


if __name__ == "__main__":
    main()
