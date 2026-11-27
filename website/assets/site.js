(function () {
  "use strict";
  // Shield simulator from the design: shows how a late check becomes "unverified", never a failure.
  var sim = document.getElementById("sim");
  if (sim) {
    var g3 = document.getElementById("g3"), tot = document.getElementById("tot"), dl = document.getElementById("dl"),
        p3 = document.getElementById("p3"), d3 = document.getElementById("d3"), note = document.getElementById("note"), isTimeout = false;
    sim.addEventListener("click", function () {
      isTimeout = !isTimeout;
      g3.classList.toggle("un", isTimeout);
      if (isTimeout) {
        tot.textContent = "49+"; dl.textContent = "No alert: run incomplete"; dl.style.color = "#8e8e8e"; dl.style.background = "rgba(142, 142, 142, 0.1)";
        p3.textContent = "Unverified"; d3.textContent = "AI answer engines timed out. Not counted as a failure.";
        note.textContent = "The run still returned. Total is a minimum floor."; sim.textContent = "Restore complete run";
      } else {
        tot.textContent = "53"; dl.textContent = "\u2193 4 since last run"; dl.style.color = "#ef4444"; dl.style.background = "rgba(239, 68, 68, 0.1)";
        p3.textContent = "4 / 25"; d3.textContent = "Named in 1 of 30 AI answers.";
        note.textContent = "Each bar represents group weight. Complete run."; sim.textContent = "Simulate a check timeout";
      }
    });
  }
  // Hero bar: carry the URL into the full form (the real submit is handled by assets/advisorgro-api.js).
  var hero = document.getElementById("hero-form");
  if (hero) hero.addEventListener("submit", function (e) {
    e.preventDefault();
    var site = document.getElementById("form-site");
    if (site) site.value = hero.elements.u.value;
    var t = document.getElementById("audit");
    if (t) t.scrollIntoView({ behavior: "smooth" });
    if (site) site.focus({ preventScroll: true });
  });
})();
