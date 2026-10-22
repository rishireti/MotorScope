/*
  Zoom and hover readout for the MotorScope trend charts.

  The charts are plain server-rendered SVG, so there is no charting library to lean on.
  This script upgrades any <svg data-zoomable> in place, the way a graphing calculator
  does: the frame never changes size, only the data inside it moves. The plotted line
  and its area live in a <g> that is scaled and shifted, while the axis labels stay a
  constant readable size on screen and are re-lettered to match the visible window, so
  zooming in shows finer values and nearer dates rather than the same picture magnified.

    * the mouse wheel zooms in and out around the pointer, and the - / + / reset buttons
      do the same around the centre, until individual points separate,
    * holding the mouse button down and dragging pans the graph under the cursor,
    * on charts carrying data-tooltip-prefix, hovering a point shows its date and value
      in a small floating readout instead of relying on the browser's native <title>.

  Everything is additive: without JavaScript the SVG renders exactly as sent, the native
  <title> still announces each point, and the buttons simply never appear.
*/
(function () {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var MAX_ZOOM = 10; // how many times closer than full size the view may get
  var ZOOM_STEP = 0.7; // scale multiplier when zooming out from a button
  var PAN_MARGIN = 0.25; // how far past the edge of the data the window may wander
  var clipCounter = 0;

  // "2026-10-04" -> "4 Oct 2026", without letting the browser shift it a day for UTC.
  function formatDate(iso) {
    var parts = (iso || "").split("-");
    if (parts.length < 3) return iso || "";
    var month = MONTHS[parseInt(parts[1], 10) - 1] || parts[1];
    return parseInt(parts[2], 10) + " " + month + " " + parts[0];
  }

  // A step of 1, 2 or 5 times a power of ten that keeps roughly `target` ticks in view.
  function niceStep(span, target) {
    if (!(span > 0)) return 0;
    var raw = span / target;
    var magnitude = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
    var norm = raw / magnitude;
    var multiple = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10;
    return multiple * magnitude;
  }

  function stepDecimals(step) {
    var exponent = Math.floor(Math.log(step) / Math.LN10 + 1e-9);
    return Math.max(0, -exponent);
  }

  function pad2(number) {
    return (number < 10 ? "0" : "") + number;
  }

  function round2(number) {
    return Math.round(number * 100) / 100;
  }

  function round3(number) {
    return Math.round(number * 1000) / 1000;
  }

  // Format a millisecond instant on its UTC clock, which is how the server wrote the
  // axis captions, so a chart reads the same in every timezone.
  function formatStamp(ms, withTime) {
    var date = new Date(ms);
    if (withTime) {
      return pad2(date.getUTCDate()) + " " + MONTHS[date.getUTCMonth()] + ", " +
        pad2(date.getUTCHours()) + ":" + pad2(date.getUTCMinutes());
    }
    return pad2(date.getUTCDate()) + " " + MONTHS[date.getUTCMonth()] + " " + date.getUTCFullYear();
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
    function attr(name, fallback) {
      var number = parseFloat(svg.getAttribute(name));
      return isFinite(number) ? number : fallback;
    }

    var padL = attr("data-pad-left", 0);
    var padT = attr("data-pad-top", 0);
    var innerW = attr("data-inner-w", 1);
    var innerH = attr("data-inner-h", 1);
    var yLow = attr("data-y-low", 0);
    var yHigh = attr("data-y-high", 100);
    var t0 = attr("data-t0", 0);
    var span = attr("data-span", 0);
    var decimals = attr("data-decimals", 0);
    var unit = svg.getAttribute("data-unit") || "";

    var plotRight = padL + innerW;
    var plotBottom = padT + innerH;

    var dataGroup = svg.querySelector(".chart__data");
    var frameGroup = svg.querySelector(".chart__frame");
    var frameHTML = frameGroup ? frameGroup.innerHTML : "";
    var xLabels = Array.prototype.slice.call(svg.querySelectorAll(".chart__x-label"));
    var xTexts = xLabels.map(function (label) { return label.textContent; });
    var dots = Array.prototype.slice.call(svg.querySelectorAll(".chart__dots .chart__dot")).map(function (el) {
      return {
        el: el,
        x: parseFloat(el.getAttribute("cx")),
        y: parseFloat(el.getAttribute("cy")),
      };
    });

    // The plotted data is clipped to the plot rectangle so a zoomed line can never
    // cover the axis gutter or spill outside the chart's frame.
    var clipId = "chart-clip-" + ++clipCounter;
    var defs = document.createElementNS(SVG_NS, "defs");
    var clip = document.createElementNS(SVG_NS, "clipPath");
    clip.setAttribute("id", clipId);
    clip.setAttribute("clipPathUnits", "userSpaceOnUse");
    var clipRect = document.createElementNS(SVG_NS, "rect");
    clipRect.setAttribute("x", padL);
    clipRect.setAttribute("y", padT);
    clipRect.setAttribute("width", innerW);
    clipRect.setAttribute("height", innerH);
    clip.appendChild(clipRect);
    defs.appendChild(clip);
    svg.insertBefore(defs, svg.firstChild);
    if (dataGroup) dataGroup.setAttribute("clip-path", "url(#" + clipId + ")");

    // The visible window, expressed in the chart's own coordinates. At k = 1 the
    // window is the whole plot rectangle and nothing is transformed.
    var k = 1;
    var wx = padL;
    var wy = padT;
    var frameDirty = false;

    // The value a given vertical position in the original drawing stands for.
    function valueAtY(origY) {
      return yLow + (1 - (origY - padT) / innerH) * (yHigh - yLow);
    }

    function redrawFrame(winW, winH, tx, ty) {
      var settled = Math.abs(k - 1) < 1e-6 && Math.abs(wx - padL) < 0.01 && Math.abs(wy - padT) < 0.01;
      if (settled) {
        // Back at the untouched view: hand the frame back to the server's markup,
        // which knows the metric's own decimal places.
        if (frameDirty && frameGroup) {
          frameGroup.innerHTML = frameHTML;
          xLabels.forEach(function (label, index) { label.textContent = xTexts[index]; });
          frameDirty = false;
        }
        return;
      }
      frameDirty = true;

      // Vertical axis: round ticks covering the values the window shows.
      var vTop = valueAtY(wy);
      var vBottom = valueAtY(wy + winH);
      var step = niceStep(vTop - vBottom, 4);
      if (frameGroup && step > 0) {
        var digits = stepDecimals(step);
        var first = Math.ceil(vBottom / step - 1e-9) * step;
        var count = Math.min(Math.floor((vTop - first) / step + 1e-9) + 1, 9);
        while (frameGroup.firstChild) frameGroup.removeChild(frameGroup.firstChild);
        for (var i = 0; i < count; i++) {
          var value = first + i * step;
          var origY = padT + innerH * (1 - (value - yLow) / (yHigh - yLow));
          var y = k * origY + ty;
          var line = document.createElementNS(SVG_NS, "line");
          line.setAttribute("class", "chart__gridline");
          line.setAttribute("x1", padL);
          line.setAttribute("x2", plotRight);
          line.setAttribute("y1", round2(y));
          line.setAttribute("y2", round2(y));
          frameGroup.appendChild(line);
          var label = document.createElementNS(SVG_NS, "text");
          label.setAttribute("class", "chart__axis-label chart__y-label");
          label.setAttribute("x", padL - 8);
          label.setAttribute("y", round2(y + 4));
          label.setAttribute("text-anchor", "end");
          label.textContent = value.toFixed(digits) + unit;
          frameGroup.appendChild(label);
        }
      }

      // Horizontal axis: the dates under the two edges of the window.
      if (span > 0 && xLabels.length) {
        var tLeft = t0 + ((wx - padL) / innerW) * span * 1000;
        var tRight = t0 + ((wx + winW - padL) / innerW) * span * 1000;
        var dL = new Date(tLeft);
        var dR = new Date(tRight);
        var sameDay = dL.getUTCDate() === dR.getUTCDate() &&
          dL.getUTCMonth() === dR.getUTCMonth() &&
          dL.getUTCFullYear() === dR.getUTCFullYear();
        xLabels[0].textContent = formatStamp(tLeft, sameDay);
        if (xLabels.length > 1) {
          xLabels[xLabels.length - 1].textContent = formatStamp(tRight, sameDay);
        }
      }
    }

    function apply() {
      k = Math.min(Math.max(k, 1), MAX_ZOOM);
      var winW = innerW / k;
      var winH = innerH / k;
      var marginX = innerW * PAN_MARGIN;
      var marginY = innerH * PAN_MARGIN;
      wx = Math.min(Math.max(wx, padL - marginX), plotRight - winW + marginX);
      wy = Math.min(Math.max(wy, padT - marginY), plotBottom - winH + marginY);

      var tx = padL - k * wx;
      var ty = padT - k * wy;
      if (dataGroup) {
        dataGroup.setAttribute(
          "transform",
          "translate(" + round3(tx) + " " + round3(ty) + ") scale(" + round3(k) + ")"
        );
      }

      // Dots keep their constant on-screen size, so they are re-placed rather than
      // carried along by the transform, and hidden when they leave the frame.
      dots.forEach(function (dot) {
        var sx = k * dot.x + tx;
        var sy = k * dot.y + ty;
        var outside = sx < padL || sx > plotRight || sy < padT || sy > plotBottom;
        dot.el.style.display = outside ? "none" : "";
        if (!outside) {
          dot.el.setAttribute("cx", round2(sx));
          dot.el.setAttribute("cy", round2(sy));
        }
      });

      redrawFrame(winW, winH, tx, ty);
    }

    function zoomAt(factor, cx, cy) {
      var next = Math.min(Math.max(k * factor, 1), MAX_ZOOM);
      if (Math.abs(next - k) < 1e-9) return;
      // The original point sitting under (cx, cy) stays where it is on screen.
      var origX = wx + (cx - padL) / k;
      var origY = wy + (cy - padT) / k;
      k = next;
      wx = origX - (cx - padL) / k;
      wy = origY - (cy - padT) / k;
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

    var centreX = padL + innerW / 2;
    var centreY = padT + innerH / 2;

    button("\u2212", "Zoom out", function () {
      zoomAt(ZOOM_STEP, centreX, centreY);
    });
    button("+", "Zoom in", function () {
      zoomAt(1 / ZOOM_STEP, centreX, centreY);
    });
    button("\u21ba", "Reset zoom", function () {
      k = 1;
      wx = padL;
      wy = padT;
      apply();
    });

    svg.parentNode.insertBefore(bar, svg.nextSibling);

    // --- wheel zoom around the pointer ----------------------------------
    svg.addEventListener("wheel", function (event) {
      event.preventDefault();
      var point = svgPoint(svg, event.clientX, event.clientY);
      var factor = Math.exp(-event.deltaY * 0.002);
      zoomAt(Math.min(Math.max(factor, 0.7), 1.4), point.x, point.y);
    }, { passive: false });

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
      var box = svg.viewBox.baseVal;
      var dx = (event.clientX - dragging.x) * (box.width / rect.width);
      var dy = (event.clientY - dragging.y) * (box.height / rect.height);
      dragging.x = event.clientX;
      dragging.y = event.clientY;
      wx -= dx / k;
      wy -= dy / k;
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

    apply();
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
