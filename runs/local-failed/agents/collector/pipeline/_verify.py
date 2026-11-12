import json

def verify_records(records):
    if not records:
        print("No records collected.")
        return False
    if not all(isinstance(record, dict) for record in records):
        print("Records are not in the expected format.")
        return False
    if not all('site' in record and 'device' in record and 'day' in record and 'value' in record for record in records):
        print("Records are missing required fields.")
        return False
    return True

if __name__ == "__main__":
    with open('data/records.jsonl', 'r') as f:
        records = [json.loads(line) for line in f]
    if verify_records(records):
        print(f"Collected {len(records)} records.")
    else:
        print("Verification failed.")
