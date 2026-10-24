/* Hover readout for the MotorScope trend charts. */
(function () {
  "use strict";

  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  // "2026-10-04" -> "4 Oct 2026", without letting the browser shift it a day for UTC.
  function formatDate(iso) {
    var parts = (iso || "").split("-");
    if (parts.length < 3) return iso || "";
    var month = MONTHS[parseInt(parts[1], 10) - 1] || parts[1];
    return parseInt(parts[2], 10) + " " + month + " " + parts[0];
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
    Array.prototype.forEach.call(document.querySelectorAll("svg[data-tooltip-prefix]"), makeTooltip);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
