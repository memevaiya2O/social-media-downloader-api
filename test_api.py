"""Quick smoke test for the downloader API. Run: python test_api.py (server must be running)."""
import sys
import requests

BASE = "http://localhost:8000"
# Small public test video (YouTube)
TEST_URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"  # Big Buck Bunny (~10s)

def check(name, fn):
    try:
        fn()
        print(f"✅ {name}")
        return True
    except Exception as e:
        print(f"❌ {name}: {e}")
        return False

def t_health():
    r = requests.get(f"{BASE}/api/health", timeout=10)
    assert r.status_code == 200, r.text
    print("   ", r.json())

def t_supported():
    r = requests.get(f"{BASE}/api/supported", timeout=10)
    assert r.status_code == 200
    assert "popular" in r.json()

def t_info():
    r = requests.get(f"{BASE}/api/info", params={"url": TEST_URL}, timeout=60)
    assert r.status_code == 200, r.text[:300]
    j = r.json()
    assert j.get("title"), "no title"
    print("   ", j["title"], "|", j.get("platform"))

def t_direct():
    r = requests.get(f"{BASE}/api/download",
                     params={"url": TEST_URL, "type": "video", "quality": "360", "direct": "true"},
                     timeout=60)
    assert r.status_code == 200, r.text[:300]
    assert "direct_url" in r.json() or "direct_urls" in r.json()

if __name__ == "__main__":
    ok = all([
        check("health", t_health),
        check("supported", t_supported),
        check("info", t_info),
        check("direct-url", t_direct),
    ])
    print("\nAll tests passed! 🎉" if ok else "\nSome tests failed.")
    sys.exit(0 if ok else 1)
