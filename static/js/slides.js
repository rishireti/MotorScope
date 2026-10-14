/*
  Arrow-driven slide deck for the "Why Movement Matters" page.

  The markup ships as a plain, stacked list of panels so it works with JavaScript
  disabled. This script upgrades each [data-slides] block into a horizontal track: it
  pins the track to one panel at a time, wires the previous/next buttons and dots, keeps
  the state exposed to assistive technology, and honours a deep link such as
  /movement#about by opening straight to that panel.
*/
(function () {
  "use strict";

  function toArray(nodeList) {
    return Array.prototype.slice.call(nodeList);
  }

  function init(deck) {
    var track = deck.querySelector("[data-slide-track]");
    if (!track) return;

    var slides = toArray(track.querySelectorAll("[data-slide]"));
    if (slides.length < 2) return;

    var prev = deck.querySelector("[data-slide-prev]");
    var next = deck.querySelector("[data-slide-next]");
    var dotHost = deck.querySelector("[data-slide-dots]");
    var dots = [];
    var index = 0;

    deck.classList.add("slides--ready");
    deck.setAttribute("role", "region");
    deck.setAttribute("aria-roledescription", "carousel");

    slides.forEach(function (slide, i) {
      slide.setAttribute("role", "group");
      slide.setAttribute("aria-roledescription", "slide");
      var label = slide.getAttribute("data-label");
      slide.setAttribute("aria-label", (i + 1) + " of " + slides.length + (label ? ": " + label : ""));
    });

    if (dotHost) {
      slides.forEach(function (slide, i) {
        var dot = document.createElement("button");
        dot.type = "button";
        dot.className = "slides__dot";
        dot.setAttribute("aria-label", "Go to " + (slide.getAttribute("data-label") || "section " + (i + 1)));
        dot.addEventListener("click", function () {
          go(i);
        });
        dotHost.appendChild(dot);
        dots.push(dot);
      });
    }

    function render() {
      track.style.transform = "translateX(" + (-index * 100) + "%)";

      slides.forEach(function (slide, i) {
        if (i === index) {
          slide.removeAttribute("aria-hidden");
        } else {
          slide.setAttribute("aria-hidden", "true");
        }
      });

      dots.forEach(function (dot, i) {
        dot.setAttribute("aria-current", i === index ? "true" : "false");
      });

      if (prev) prev.disabled = index === 0;
      if (next) next.disabled = index === slides.length - 1;
    }

    function go(target) {
      var clamped = Math.max(0, Math.min(slides.length - 1, target));
      if (clamped === index) {
        render();
        return;
      }
      index = clamped;
      render();
    }

    function step(delta) {
      go(index + delta);
    }

    if (prev) prev.addEventListener("click", function () { step(-1); });
    if (next) next.addEventListener("click", function () { step(1); });

    deck.addEventListener("keydown", function (event) {
      if (event.target && /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName)) return;
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        step(-1);
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        step(1);
      }
    });

    /* Basic horizontal swipe for touch devices. */
    var startX = null;
    deck.addEventListener("touchstart", function (event) {
      startX = event.touches.length === 1 ? event.touches[0].clientX : null;
    }, { passive: true });

    deck.addEventListener("touchend", function (event) {
      if (startX === null) return;
      var endX = event.changedTouches[0].clientX;
      var delta = endX - startX;
      startX = null;
      if (Math.abs(delta) < 45) return;
      step(delta < 0 ? 1 : -1);
    }, { passive: true });

    /* Open to the panel named in the URL fragment, e.g. /movement#about. */
    var hash = window.location.hash.replace(/^#/, "");
    if (hash) {
      var target = null;
      try {
        target = deck.querySelector("#" + window.CSS.escape(hash));
      } catch (err) {
        target = null;
      }
      if (target) {
        var found = slides.indexOf(target);
        if (found > -1) index = found;
      }
    }

    render();
  }

  function boot() {
    toArray(document.querySelectorAll("[data-slides]")).forEach(init);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
