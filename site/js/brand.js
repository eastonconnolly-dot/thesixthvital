// Single place to rename the brand across the whole static site.
// Every element with [data-brand] gets its text replaced; [data-brand-attr]
// elements get the named attribute rewritten (used for e.g. document title).
window.RPSAS_BRAND = {
  name: "RPSAS",
  tagline: "Physician Communication Training",
};

(function applyBrand() {
  var brand = window.RPSAS_BRAND.name;
  document.querySelectorAll("[data-brand]").forEach(function (el) {
    el.textContent = el.textContent.replace(/RPSAS/g, brand);
  });
  document.querySelectorAll("[data-brand-title]").forEach(function () {
    document.title = document.title.replace(/RPSAS/g, brand);
  });
})();
