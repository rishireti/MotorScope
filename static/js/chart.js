/*
  Zoom and hover readout for the MotorScope trend charts.

  The charts are plain server-rendered SVG, so there is no charting library to lean on.
  This script upgrades any <svg data-zoomable> in place:

    * zoom in / out / reset buttons scale the SVG's viewBox around its centre, and
      dragging pans the window, so a long history packed with readings can be opened up
      until individual points separate,
    * on charts carrying data-tooltip-prefix, hovering a point shows its date and value
      in a small floating readout instead of relying on the browser's native <title>.

  Everything is additive: without JavaScript the SVG renders exactly as sent, the native
  <title> still announces each point, and the buttons simply never appear.
*/
(function () {
  "use strict";

  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var MIN_ZOOM = 10; // how many times smaller than full width the view may get
  var ZOOM_STEP = 0.7; // width multiplier when zooming in

  // "2026-10-04" -> "4 Oct 2026", without letting the browser shift it a day for UTC.
  function formatDate(iso) {
    var parts = (iso || "").split("-");
    if (parts.length < 3) return iso || "";
    var month = MONTHS[parseInt(parts[1], 10) - 1] || parts[1];
    return parseInt(parts[2], 10) + " " + month + " " + parts[0];
  }

  function svgPoint(svg, clientX, clientY) {
    var rect = svg.getBoundingClientRect();
    var box = svg.viewBox.baseVal;
    return {
      x: box.x + (clientX - rect.left) * (box.width / rect.width),
      y: box.y + (clientY - rect.top) * (box.height / rect.height),
    };
  }

  function makeZoomable(svg) {
    var full = {
      x: svg.viewBox.baseVal.x,
      y: svg.viewBox.baseVal.y,
      w: svg.viewBox.baseVal.width,
      h: svg.viewBox.baseVal.height,
    };
    var view = { x: full.x, y: full.y, w: full.w, h: full.h };

    function clamp() {
      view.w = Math.min(Math.max(view.w, full.w / MIN_ZOOM), full.w);
      view.h = full.h * (view.w / full.w);
      view.x = Math.min(Math.max(view.x, full.x), full.x + full.w - view.w);
      view.y = Math.min(Math.max(view.y, full.y), full.y + full.h - view.h);
    }

    function apply() {
      clamp();
      svg.setAttribute(
        "viewBox",
        view.x.toFixed(2) + " " + view.y.toFixed(2) + " " + view.w.toFixed(2) + " " + view.h.toFixed(2)
      );
    }

    function zoom(factor, cx, cy) {
      if (typeof cx !== "number") {
        cx = view.x + view.w / 2;
        cy = view.y + view.h / 2;
      }
      var next = Math.min(Math.max(view.w * factor, full.w / MIN_ZOOM), full.w);
      var scale = next / view.w;
      view.x = cx - (cx - view.x) * scale;
      view.y = cy - (cy - view.y) * scale;
      view.w = next;
      view.h = full.h * (view.w / full.w);
      apply();
    }

    // --- controls -------------------------------------------------------
    var bar = document.createElement("div");
    bar.className = "chart__controls";

    function button(label, title, onClick) {
      var el = document.createElement("button");
      el.type = "button";
      el.className = "chart__control";
      el.textContent = label;
      el.title = title;
      el.setAttribute("aria-label", title);
      el.addEventListener("click", onClick);
      bar.appendChild(el);
      return el;
    }

    button("\u2212", "Zoom out", function () {
      zoom(1 / ZOOM_STEP);
    });
    button("+", "Zoom in", function () {
      zoom(ZOOM_STEP);
    });
    button("\u21ba", "Reset zoom", function () {
      view.x = full.x;
      view.y = full.y;
      view.w = full.w;
      view.h = full.h;
      apply();
    });

    svg.parentNode.insertBefore(bar, svg.nextSibling);

    // --- drag to pan ----------------------------------------------------
    var dragging = null;
    svg.addEventListener("pointerdown", function (event) {
      if (event.button !== 0) return;
      dragging = { x: event.clientX, y: event.clientY };
      svg.setPointerCapture(event.pointerId);
      svg.classList.add("is-panning");
    });
    svg.addEventListener("pointermove", function (event) {
      if (!dragging) return;
      var rect = svg.getBoundingClientRect();
      view.x -= (event.clientX - dragging.x) * (view.w / rect.width);
      view.y -= (event.clientY - dragging.y) * (view.h / rect.height);
      dragging.x = event.clientX;
      dragging.y = event.clientY;
      apply();
    });
    function endDrag(event) {
      if (!dragging) return;
      dragging = null;
      svg.classList.remove("is-panning");
      if (svg.hasPointerCapture && svg.hasPointerCapture(event.pointerId)) {
        svg.releasePointerCapture(event.pointerId);
      }
    }
    svg.addEventListener("pointerup", endDrag);
    svg.addEventListener("pointercancel", endDrag);
  }

  function makeTooltip(svg) {
    var prefix = svg.getAttribute("data-tooltip-prefix") || "";
    var unit = svg.getAttribute("data-tooltip-unit") || "";
    var tip = document.createElement("div");
    tip.className = "chart__tooltip";
    tip.setAttribute("role", "status");
    tip.hidden = true;
    svg.parentNode.style.position = "relative";
    svg.parentNode.appendChild(tip);

    function hide() {
      tip.hidden = true;
    }

    Array.prototype.forEach.call(svg.querySelectorAll(".chart__dot"), function (dot) {
      dot.addEventListener("pointerenter", function () {
        var date = formatDate(dot.getAttribute("data-date"));
        var value = dot.getAttribute("data-value") || "";
        var text = date;
        if (prefix) text += " \u00b7 " + prefix + " " + value + unit;
        else if (value) text += " \u00b7 " + value + unit;
        tip.textContent = text;
        tip.hidden = false;
      });
      dot.addEventListener("pointermove", function (event) {
        var rect = svg.parentNode.getBoundingClientRect();
        tip.style.left = event.clientX - rect.left + "px";
        tip.style.top = event.clientY - rect.top - 34 + "px";
      });
      dot.addEventListener("pointerleave", hide);
    });

    svg.addEventListener("pointerleave", hide);
  }

  function init() {
    Array.prototype.forEach.call(document.querySelectorAll("svg[data-zoomable]"), makeZoomable);
    Array.prototype.forEach.call(document.querySelectorAll("svg[data-tooltip-prefix]"), makeTooltip);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
