"""Turn a pasted Amazon.in / Flipkart link into {platform, product_id, clean_url}.

    parse_link("https://www.amazon.in/dp/B0ABCDEFGH?ref=xyz")
    -> {"platform": "amazon", "product_id": "B0ABCDEFGH", "clean_url": "https://www.amazon.in/dp/B0ABCDEFGH"}
"""

import re
from urllib.parse import parse_qs, urlparse

BAD_LINK = "Paste an Amazon.in or Flipkart product link"

AMAZON_HOSTS = {"amazon.in", "www.amazon.in", "m.amazon.in"}
FLIPKART_HOSTS = {"flipkart.com", "www.flipkart.com", "m.flipkart.com"}
# Share links that redirect to the full product page.
SHORT_HOSTS = {"amzn.in", "amzn.to", "amzn.eu", "a.co", "dl.flipkart.com", "fkrt.it", "fkrt.co"}

# The ASIN is the 10-character product code after /dp/, /gp/product/ or /product-reviews/.
ASIN_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d|product-reviews)/([A-Z0-9]{10})(?:[/?]|$)", re.IGNORECASE)
# Flipkart item id: /p/itm... or /product-reviews/itm...
FLIPKART_ITEM_RE = re.compile(r"/(?:p|product-reviews)/(itm[a-z0-9]+)", re.IGNORECASE)


class LinkError(ValueError):
    """The link is not one we can use; the message is shown to the user as-is."""


def resolve_short_link(url):
    """Follow a share link (amzn.in/..., dl.flipkart.com/...) to the full product URL."""
    import requests  # imported here so parsing full links works without network
    try:
        response = requests.get(url, allow_redirects=True, timeout=10,
                                headers={"User-Agent": "Mozilla/5.0"}, stream=True)
        response.close()  # we only need the final URL, not the page
        return response.url
    except requests.RequestException as e:
        raise LinkError(f"Could not open the short link ({type(e).__name__}). "
                        "Open it in your browser and paste the full product link instead.") from None


def parse_link(url, resolve=resolve_short_link):
    """Return {platform, product_id, clean_url} or raise LinkError with a plain-English message.

    resolve: function used to follow short links (tests pass a fake one, so no network is needed).
    """
    url = (url or "").strip()
    if not url:
        raise LinkError(BAD_LINK)
    if not re.match(r"https?://", url, re.IGNORECASE):
        url = "https://" + url  # people often paste "amazon.in/dp/..." without https://
    host = (urlparse(url).hostname or "").lower()

    if host in SHORT_HOSTS:
        url = resolve(url)
        host = (urlparse(url).hostname or "").lower()
        if host in SHORT_HOSTS:  # still short after following it: give up rather than loop
            raise LinkError(BAD_LINK)

    if host in AMAZON_HOSTS:
        return parse_amazon(url)
    if host in FLIPKART_HOSTS:
        return parse_flipkart(url)
    if host.startswith("amazon.") or ".amazon." in host:
        raise LinkError(f"{BAD_LINK} (only amazon.in is supported, not {host}).")
    raise LinkError(BAD_LINK)


def parse_amazon(url):
    """Amazon.in: the product is identified by its ASIN."""
    match = ASIN_RE.search(urlparse(url).path)
    if not match:
        raise LinkError(f"{BAD_LINK}. This Amazon link has no product code (ASIN) - "
                        "open the product page and copy its link.")
    asin = match.group(1).upper()
    return {"platform": "amazon", "product_id": asin, "clean_url": f"https://www.amazon.in/dp/{asin}"}


def parse_flipkart(url):
    """Flipkart: needs the item id (itm...) from the path and the pid from ?pid=..."""
    parsed = urlparse(url)
    match = FLIPKART_ITEM_RE.search(parsed.path)
    pid = (parse_qs(parsed.query).get("pid") or [""])[0].strip().upper()
    if not match or not pid:
        raise LinkError(f"{BAD_LINK}. This Flipkart link is missing the product id (itm... and pid=...) - "
                        "open the product page and copy the full link.")
    item_id = match.group(1).lower()
    # The readable name part before /p/ (e.g. water-science-cleo-...); some share links have none.
    slug = parsed.path[:match.start()].strip("/").split("/")[0] or "product"
    return {"platform": "flipkart", "product_id": pid, "item_id": item_id,
            "clean_url": f"https://www.flipkart.com/{slug}/p/{item_id}?pid={pid}"}
