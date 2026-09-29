(function () {
  var form = document.querySelector("form.apply-form");
  if (!form) return;
  var msg = document.querySelector(".form-msg");
  var submitBtn = form.querySelector('button[type="submit"]');

  // Same fields serve all three tracks, but the copy that fits a hospital's
  // procurement process reads oddly for an individual applicant — swap it
  // per track instead of hiding fields the scoring in services/qualify.py
  // still expects (org, in particular, is required there for every track).
  var TRACK_COPY = {
    applicant: {
      orgLabel: "School",
      orgPlaceholder: "e.g. University of Washington School of Medicine",
      orgHint: "The school or program you're applying through.",
      rolePlaceholder: "e.g. MS4, post-bacc, gap-year applicant",
      budgetLegend: "Do you already have funds set aside for this? *",
    },
    physician: {
      orgLabel: "Organization",
      orgPlaceholder: "Practice or hospital name, if any",
      orgHint: "Leave blank if you're independent or between practices.",
      rolePlaceholder: "e.g. Orthopedic Surgeon, Hospitalist",
      budgetLegend: "Is a budget in the relevant range already approved? *",
    },
    program: {
      orgLabel: "Organization",
      orgPlaceholder: "Hospital, practice, or program name",
      orgHint: "The hospital, practice, or program this is for.",
      rolePlaceholder: "e.g. Program Director, Chief Residency Officer",
      budgetLegend: "Is a budget in the relevant range already approved? *",
    },
  };

  var orgLabel = document.getElementById("org-label");
  var orgInput = document.getElementById("org");
  var orgHint = document.getElementById("org-hint");
  var roleInput = document.getElementById("role");
  var budgetLegend = document.getElementById("budget-legend");

  function applyTrackCopy(track) {
    var copy = TRACK_COPY[track];
    if (!copy) return;
    orgLabel.textContent = copy.orgLabel;
    orgInput.placeholder = copy.orgPlaceholder;
    orgHint.textContent = copy.orgHint;
    roleInput.placeholder = copy.rolePlaceholder;
    budgetLegend.textContent = copy.budgetLegend;
  }

  form.querySelectorAll('input[name="track"]').forEach(function (radio) {
    radio.addEventListener("change", function () {
      applyTrackCopy(radio.value);
    });
  });

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
        if (result.qualified && result.qualifier_token) {
          showMsg("You're a fit — taking you to a quick two-minute chat to get you booked…", "ok");
          window.location.href = window.RPSAS_CONFIG.apiBase + "/qualify/" + result.qualifier_token;
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
