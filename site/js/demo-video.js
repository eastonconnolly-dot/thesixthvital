(function () {
  var container = document.querySelector(".hero-video");
  if (!container) return;

  var videoUrl = "/assets/demo.mp4";

  fetch(videoUrl, { method: "HEAD" })
    .then(function (res) {
      if (!res.ok) return;
      var video = document.createElement("video");
      video.src = videoUrl;
      video.controls = true;
      video.preload = "metadata";
      video.setAttribute("playsinline", "");
      video.poster = "/assets/demo-poster.jpg";
      container.textContent = "";
      container.removeAttribute("aria-label");
      container.appendChild(video);
    })
    .catch(function () {
      // No demo video yet (local file:// pages, or a network error) —
      // leave the placeholder text showing.
    });
})();
