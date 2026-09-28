(function () {
  var form = document.querySelector("form.apply-form");
  if (!form) return;
  var msg = document.querySelector(".form-msg");
  var submitBtn = form.querySelector('button[type="submit"]');

  function showMsg(text, kind) {
    msg.textContent = text;
    msg.className = "form-msg show " + kind;
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var data = Object.fromEntries(new FormData(form).entries());
    submitBtn.disabled = true;
    submitBtn.textContent = "Submitting…";

    fetch(window.RPSAS_CONFIG.apiBase + "/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    })
      .then(function (res) {
        if (!res.ok) throw new Error("Request failed");
        return res.json();
      })
      .then(function (result) {
        form.reset();
        form.hidden = true;
        if (result.qualified && result.lead_id) {
          showMsg("You're a fit — taking you to a quick two-minute chat to get you booked…", "ok");
          window.location.href = window.RPSAS_CONFIG.apiBase + "/qualify/" + result.lead_id;
          return;
        }
        showMsg(
          "Application received. If it's a fit, you'll get an email within one business day with a link to book a call.",
          "ok"
        );
      })
      .catch(function () {
        showMsg(
          "Something went wrong submitting your application. Please email us directly and we'll follow up.",
          "err"
        );
      })
      .finally(function () {
        submitBtn.disabled = false;
        submitBtn.textContent = "Submit application";
      });
  });
})();
