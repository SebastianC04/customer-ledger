"""
Bulk-loads customers_seed.json into the running Customer Ledger app.

Usage:
    1. Start the app first:  uvicorn main:app --reload
    2. In another terminal, from the customer_ledger/ folder, run:
         python seed_customers.py
"""
import json
import urllib.request
import urllib.error

API_URL = "http://127.0.0.1:8000/api/customers"
SEED_FILE = "customers_seed.json"


def create_customer(payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_URL, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def main():
    with open(SEED_FILE, "r") as f:
        customers = json.load(f)

    created = 0
    for c in customers:
        try:
            result = create_customer(c)
            print(f"Created: {result['name']} (id {result['id']})")
            created += 1
        except urllib.error.URLError as e:
            print(f"Failed to reach app at {API_URL} — is it running? ({e})")
            return
        except Exception as e:
            print(f"Skipped {c['name']}: {e}")

    print(f"\nDone. {created}/{len(customers)} customers created.")


if __name__ == "__main__":
    main()
