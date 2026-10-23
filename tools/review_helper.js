// ReturnLens browser helper: download the reviews visible on an Amazon.in review page as a CSV.
//
// How to use (Chrome / Edge):
//   1. Open the product on amazon.in -> "See more reviews" (the page with the list of reviews).
//   2. Press F12 -> "Console" tab, paste this whole file, press Enter.
//   3. A file amazon_<date>.csv downloads (columns rating,title,body,date,verified).
//   4. Click "Next page" on Amazon and repeat; then drop all files into ReturnLens's upload box
//      (duplicates are removed automatically).
// It only reads what is already on your screen; it does not open other pages.
(() => {
  const text = (el, sel) => (el.querySelector(sel)?.innerText || "").trim();
  const rows = [...document.querySelectorAll('[data-hook="review"]')].map((r) => {
    const stars = text(r, '[data-hook="review-star-rating"], [data-hook="cmps-review-star-rating"]');
    const rating = (stars.match(/[1-5]/) || [""])[0];                      // "4.0 out of 5 stars" -> "4"
    const title = text(r, '[data-hook="review-title"]')
      .replace(/^[\d.]+ out of 5 stars\s*/, "");                          // newer layouts repeat the stars
    const body = text(r, '[data-hook="review-body"]').replace(/\s*Read more\s*$/, "");
    const date = text(r, '[data-hook="review-date"]').replace(/^.* on /, ""); // "Reviewed in India on 14 June 2026"
    const verified = r.querySelector('[data-hook="avp-badge"]') ? "true" : "false";
    return [rating, title, body, date, verified];
  }).filter((r) => r[0] && (r[1] || r[2]));

  if (!rows.length) {
    console.warn("ReturnLens helper: no reviews found. Open the product's review list page and try again.");
    return;
  }
  const quote = (v) => `"${String(v).replace(/"/g, '""')}"`;
  const csv = "rating,title,body,date,verified\n" + rows.map((r) => r.map(quote).join(",")).join("\n");
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob(["\ufeff" + csv], { type: "text/csv" })); // BOM: Excel shows Hindi
  link.download = `amazon_${new Date().toISOString().slice(0, 10)}.csv`;
  link.click();
  console.log(`ReturnLens helper: downloaded ${rows.length} reviews.`);
})();
