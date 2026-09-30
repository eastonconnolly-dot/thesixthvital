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

  function reveal(id) {
    var node = document.getElementById(id);
    if (node) node.hidden = false;
  }

  fetch(window.RPSAS_CONFIG.apiBase + "/public/proof")
    .then(function (res) { if (!res.ok) throw new Error("no data"); return res.json(); })
    .then(function (data) {
      var hasStats = statRow && data.stats && data.stats.length;
      var hasTestimonials = testimonialWrap && data.testimonials && data.testimonials.length;

      if (hasStats) {
        data.stats.forEach(function (s) {
          var stat = el("div", "stat");
          stat.appendChild(el("div", "value", s.value));
          stat.appendChild(el("div", "label", s.label));
          statRow.appendChild(stat);
        });
        reveal("proof-stats-section");
      }
      if (hasTestimonials) {
        data.testimonials.forEach(function (t) {
          var quote = el("blockquote", "testimonial");
          quote.appendChild(document.createTextNode("“" + t.quote + "”"));
          var footer = el("footer", null, t.attribution);
          quote.appendChild(footer);
          testimonialWrap.appendChild(quote);
        });
        reveal("proof-testimonials-section");
      }
      if (hasStats || hasTestimonials) {
        var pending = document.getElementById("proof-pending");
        if (pending) pending.hidden = true;
      }
    })
    .catch(function () {
      // Pre-launch or API unreachable: leave the "not yet" note showing.
    });
})();
