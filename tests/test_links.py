"""Tests for links.py. Run with:  python tests/test_links.py   (or: python -m pytest tests)

No network: short links are "followed" with a fake function.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # so "import links" works from tests/

from links import BAD_LINK, LinkError, parse_link

# Fake redirects for share links (what the real site would send us to).
FAKE_REDIRECTS = {
    "https://amzn.in/d/abc123": "https://www.amazon.in/WaterScience-CLEO-Filter/dp/B0CJ4T8XK2?ref_=share",
    "https://dl.flipkart.com/s/xyz": "https://www.flipkart.com/water-science-cleo/p/itme09021f5b2d0e?pid=TWFGU3TCDNYQNDXR",
}


def fake_resolve(url):
    return FAKE_REDIRECTS[url]


def check(url, platform, product_id, clean_url=None):
    result = parse_link(url, resolve=fake_resolve)
    assert result["platform"] == platform, result
    assert result["product_id"] == product_id, result
    if clean_url:
        assert result["clean_url"] == clean_url, result


def check_error(url, words=BAD_LINK):
    try:
        parse_link(url, resolve=fake_resolve)
    except LinkError as e:
        assert words in str(e), str(e)
        return
    raise AssertionError(f"expected an error for {url}")


def test_amazon_dp_with_tracking():
    check("https://www.amazon.in/WaterScience-CLEO-Shower-Filter/dp/B0CJ4T8XK2/ref=sr_1_3?keywords=filter&th=1",
          "amazon", "B0CJ4T8XK2", "https://www.amazon.in/dp/B0CJ4T8XK2")


def test_amazon_gp_product():
    check("https://www.amazon.in/gp/product/B0CJ4T8XK2?psc=1", "amazon", "B0CJ4T8XK2")


def test_amazon_product_reviews_lowercase_no_https():
    check("amazon.in/product-reviews/b0cj4t8xk2/ref=cm_cr_dp_d_show_all_btm?reviewerType=all_reviews",
          "amazon", "B0CJ4T8XK2")


def test_amazon_short_link():
    check("https://amzn.in/d/abc123", "amazon", "B0CJ4T8XK2")


def test_flipkart_product_reviews_link():
    check("https://www.flipkart.com/water-science-cleo-tap-filter-hard-municipal-metro-water-mount/"
          "product-reviews/itme09021f5b2d0e?pid=TWFGU3TCDNYQNDXR&lid&marketplace=FLIPKART",
          "flipkart", "TWFGU3TCDNYQNDXR",
          "https://www.flipkart.com/water-science-cleo-tap-filter-hard-municipal-metro-water-mount/"
          "p/itme09021f5b2d0e?pid=TWFGU3TCDNYQNDXR")


def test_flipkart_product_page_link():
    result = parse_link("https://www.flipkart.com/water-science-cleo/p/itme09021f5b2d0e"
                        "?pid=TWFGU3TCDNYQNDXR&lid=LSTTWF&marketplace=FLIPKART&store=search")
    assert result["item_id"] == "itme09021f5b2d0e"
    assert result["product_id"] == "TWFGU3TCDNYQNDXR"


def test_flipkart_short_link():
    check("https://dl.flipkart.com/s/xyz", "flipkart", "TWFGU3TCDNYQNDXR")


def test_flipkart_without_pid_is_rejected():
    check_error("https://www.flipkart.com/water-science-cleo/p/itme09021f5b2d0e", "missing the product id")


def test_amazon_com_is_rejected():
    check_error("https://www.amazon.com/dp/B0CJ4T8XK2", "only amazon.in")


def test_amazon_search_page_is_rejected():
    check_error("https://www.amazon.in/s?k=shower+filter", "no product code")


def test_other_sites_and_empty_are_rejected():
    for url in ["https://www.myntra.com/filter/123", "", "   ", "not a link", "https://fake-amazon.in.evil.com/dp/B0CJ4T8XK2"]:
        check_error(url)


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {name}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    sys.exit(1 if failed else 0)
