(function () {
  var statRow = document.querySelector("[data-proof-stats]");
  var testimonialWrap = document.querySelector("[data-proof-testimonials]");
  if (!statRow && !testimonialWrap) return;

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  fetch(window.RPSAS_CONFIG.apiBase + "/public/proof")
    .then(function (res) { if (!res.ok) throw new Error("no data"); return res.json(); })
    .then(function (data) {
      if (statRow && data.stats && data.stats.length) {
        statRow.textContent = "";
        data.stats.forEach(function (s) {
          var stat = el("div", "stat");
          stat.appendChild(el("div", "value", s.value));
          stat.appendChild(el("div", "label", s.label));
          statRow.appendChild(stat);
        });
      }
      if (testimonialWrap && data.testimonials && data.testimonials.length) {
        testimonialWrap.textContent = "";
        data.testimonials.forEach(function (t) {
          var quote = el("blockquote", "testimonial");
          quote.appendChild(document.createTextNode("“" + t.quote + "”"));
          var footer = el("footer", null, t.attribution);
          quote.appendChild(footer);
          testimonialWrap.appendChild(quote);
        });
      }
    })
    .catch(function () {
      // Pre-launch or API unreachable: leave the static fallback content in the page as-is.
    });
})();
