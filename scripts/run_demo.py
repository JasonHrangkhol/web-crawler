"""
Demo runner — crawls all 5 assignment test URLs and saves JSON output.

Use this to:
  1. Verify the crawler works end-to-end before deploying
  2. Generate the sample_outputs/ files for the assignment submission
  3. Quickly eyeball what each test URL returns

Usage:
    python scripts/run_demo.py

Output is saved to sample_outputs/<name>.json
"""

import json
import sys
import time
from pathlib import Path

# Add the project root to sys.path so the crawler package is importable
# when this script is run from the project root or the scripts/ folder
sys.path.insert(0, str(Path(__file__).parent.parent))

from crawler import crawl

# The 5 URLs provided in the assignment
TEST_URLS = [
    (
        "amazon_toaster",
        "http://www.amazon.com/Cuisinart-CPT-122-Compact-2-Slice-Toaster/dp/B009GQ034C/"
        "ref=sr_1_1?s=kitchen&ie=UTF8&qid=1431620315&sr=1-1&keywords=toaster",
    ),
    (
        "rei_blog",
        "http://blog.rei.com/camp/how-to-introduce-your-indoorsy-friend-to-the-outdoors/",
    ),
    (
        "cnn_article",
        "https://www.cnn.com/2025/09/23/tech/google-study-90-percent-tech-jobs-ai",
    ),
    (
        "walmart_home",
        "https://www.walmart.com",
    ),
    (
        "bestbuy_home",
        "https://www.bestbuy.com",
    ),
]


def main():
    output_dir = Path(__file__).parent.parent / "sample_outputs"
    output_dir.mkdir(exist_ok=True)

    print(f"Crawling {len(TEST_URLS)} test URLs...\n")
    print(f"{'Name':<20} {'Status':<8} {'Category':<28} {'Topics (first 3)'}")
    print("-" * 90)

    results = []

    for name, url in TEST_URLS:
        result = crawl(url)
        data   = result.to_dict()

        status     = str(data["status_code"]) if not data["error"] else f"ERR"
        category   = data["page_category"] or "(none)"
        top_topics = ", ".join(data["topics"][:3]) if data["topics"] else "(none)"

        print(f"{name:<20} {status:<8} {category:<28} {top_topics}")

        # Save full JSON
        output_path = output_dir / f"{name}.json"
        output_path.write_text(json.dumps(data, indent=2))

        results.append((name, data))

    print("-" * 90)
    print(f"\nSaved {len(results)} files to {output_dir}/\n")

    # Print any errors for quick debugging
    errors = [(name, d["error"]) for name, d in results if d["error"]]
    if errors:
        print("Errors:")
        for name, err in errors:
            print(f"  [{name}] {err}")
    else:
        print("All crawls completed without errors.")


if __name__ == "__main__":
    main()
