"""
merge.py — Combine all session_*.json or session_*.csv into one file.
Run after scraping is complete.
"""

import os, json, csv, glob

OUTPUT_DIR = "outputs"

def merge():
    json_files = sorted(glob.glob(os.path.join(OUTPUT_DIR, "session_*.json")))
    csv_files  = sorted(glob.glob(os.path.join(OUTPUT_DIR, "session_*.csv")))

    if json_files:
        all_data = []
        for f in json_files:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
            all_data.extend(data)
            print(f"  ✅ {f}: {len(data)} records")

        # Deduplicate
        seen, unique = set(), []
        for item in all_data:
            k = json.dumps(item, sort_keys=True)
            if k not in seen:
                seen.add(k)
                unique.append(item)

        out = os.path.join(OUTPUT_DIR, "merged_output.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(unique, f, indent=2, ensure_ascii=False)
        print(f"\n🎉 Merged {len(json_files)} files → {out}")
        print(f"   Total: {len(all_data)} records, {len(unique)} unique")

    elif csv_files:
        all_rows, all_keys = [], []
        for f in csv_files:
            with open(f, newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                rows = list(reader)
                all_rows.extend(rows)
                all_keys.extend(reader.fieldnames or [])
                print(f"  ✅ {f}: {len(rows)} records")

        keys = list(dict.fromkeys(all_keys))
        out  = os.path.join(OUTPUT_DIR, "merged_output.csv")
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            w.writerows(all_rows)
        print(f"\n🎉 Merged {len(csv_files)} files → {out}")
        print(f"   Total: {len(all_rows)} records")
    else:
        print("❌ No session files found in outputs/")

if __name__ == "__main__":
    merge()