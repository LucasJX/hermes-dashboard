#!/usr/bin/env python3
"""Pre-fetch release body and save to cache file."""
import json, os, time

# The release body will be saved here
CACHE_FILE = os.path.join(os.path.dirname(__file__), ".release_cache.json")

# Read the body from stdin or file
import sys
body = sys.stdin.read() if not sys.stdin.isatty() else ""

if body:
    data = {
        "version": "0.13.0",
        "tag": "v2026.5.7",
        "name": "Hermes Agent v0.13.0 (2026.5.7) — The Tenacity Release",
        "body": body,
        "published_at": "2026-05-07T16:23:08Z",
        "html_url": "https://github.com/NousResearch/hermes-agent/releases/tag/v2026.5.7",
        "cached_at": int(time.time()),
    }
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f)
    print(f"Saved {len(body)} chars to {CACHE_FILE}")
else:
    print("No body provided")
