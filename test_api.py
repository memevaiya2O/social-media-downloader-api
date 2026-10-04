"""Quick smoke test for the downloader API. Run: python test_api.py (server must be running)."""
import sys
import requests

BASE = "http://localhost:8000"
# Small public test video (YouTube)
TEST_URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"  # Big Buck Bunny (~10s)

failed_required = []


def check(name, fn, required=True):
    try:
        fn()
        print(f"✅ {name}")
    except Exception as e:
        print(f"{'❌' if required else '⚠️ (optional)'} {name}: {str(e)[:200]}")
        if required:
            failed_required.append(name)


def t_health():
    r = requests.get(f"{BASE}/api/health", timeout=10)
    assert r.status_code == 200, r.text
    print("   ", r.json())


def t_supported():
    r = requests.get(f"{BASE}/api/supported", timeout=10)
    assert r.status_code == 200
    assert "popular" in r.json()


def t_cookies_invalid():
    # offline deterministic test: garbage cookies must be rejected
    r = requests.post(f"{BASE}/api/cookies", json={"cookies": "garbage"}, timeout=10)
    assert r.status_code == 400, r.text
    r = requests.delete(f"{BASE}/api/cookies", timeout=10)
    assert r.status_code == 200, r.text


def t_info():
    r = requests.get(f"{BASE}/api/info", params={"url": TEST_URL}, timeout=120)
    assert r.status_code == 200, r.text[:300]
    j = r.json()
    assert j.get("title"), "no title"
    print("   ", j["title"], "|", j.get("platform"))


def t_direct():
    r = requests.get(f"{BASE}/api/download",
                     params={"url": TEST_URL, "type": "video", "quality": "360", "direct": "true"},
                     timeout=120)
    assert r.status_code == 200, r.text[:300]
    assert "direct_url" in r.json() or "direct_urls" in r.json()


def t_fallback_info():
    # cookieless fallback via Piped/Invidious (needs live third-party instances)
    r = requests.get(f"{BASE}/api/info",
                     params={"url": TEST_URL, "force_fallback": "true"}, timeout=120)
    assert r.status_code == 200, r.text[:300]
    j = r.json()
    assert j.get("fallback_source"), "no fallback_source marker"
    print("   ", "via", j["fallback_source"], "|", j.get("title", "")[:60])


if __name__ == "__main__":
    check("health", t_health)
    check("supported", t_supported)
    check("cookies-validation", t_cookies_invalid)
    check("info", t_info, required=False)
    check("direct-url", t_direct, required=False)
    check("fallback-info", t_fallback_info, required=False)
    if failed_required:
        print(f"\n❌ Required tests failed: {failed_required}")
        sys.exit(1)
    print("\nAll required tests passed! 🎉 (optional ones depend on live third parties)")
