import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def main():
    with open(os.path.join(ROOT, "data", "summary.json"), "r", encoding="utf-8") as f:
        summary = json.load(f)
    records = []
    with open(os.path.join(ROOT, "data", "records.jsonl"), "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    payload = {"summary": summary, "records": records}
    out = os.path.join(HERE, "data.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("wrote", out, "records:", len(records))


if __name__ == "__main__":
    main()
