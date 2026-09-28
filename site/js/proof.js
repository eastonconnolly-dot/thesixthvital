(function () {
  var statRow = document.querySelector("[data-proof-stats]");
  var testimonialWrap = document.querySelector("[data-proof-testimonials]");
  if (!statRow && !testimonialWrap) return;

  fetch(window.RPSAS_CONFIG.apiBase + "/public/proof")
    .then(function (res) { if (!res.ok) throw new Error("no data"); return res.json(); })
    .then(function (data) {
      if (statRow && data.stats) {
        statRow.innerHTML = data.stats.map(function (s) {
          return '<div class="stat"><div class="value">' + s.value + '</div><div class="label">' + s.label + "</div></div>";
        }).join("");
      }
      if (testimonialWrap && data.testimonials && data.testimonials.length) {
        testimonialWrap.innerHTML = data.testimonials.map(function (t) {
          return '<blockquote class="testimonial">&ldquo;' + t.quote + '&rdquo;<footer>' + t.attribution + "</footer></blockquote>";
        }).join("");
      }
    })
    .catch(function () {
      // Pre-launch or API unreachable: leave the static fallback content in the page as-is.
    });
})();
