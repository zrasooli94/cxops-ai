(function () {
  "use strict";

  var script = document.currentScript;
  if (!script) {
    return;
  }

  var widgetKey = script.getAttribute("data-widget-key");
  if (!widgetKey || widgetKey.length < 8) {
    return;
  }

  var loaderOrigin = new URL(script.src, window.location.href).origin;

  var iframe = document.createElement("iframe");
  iframe.setAttribute(
    "src",
    loaderOrigin +
      "/chat/embed?key=" +
      encodeURIComponent(widgetKey)
  );
  iframe.setAttribute(
    "sandbox",
    "allow-scripts allow-forms allow-same-origin"
  );
  iframe.style.position = "fixed";
  iframe.style.right = "0";
  iframe.style.bottom = "0";
  iframe.style.width = "96px";
  iframe.style.height = "96px";
  iframe.style.border = "none";
  iframe.style.zIndex = "9999";
  iframe.style.background = "transparent";
  iframe.setAttribute(
    "allowtransparency",
    "true"
  );
  iframe.setAttribute("title", "Customer support chat");

  document.body.appendChild(iframe);

  var requestedWidth = null;
  var requestedHeight = null;

  // Only the actual widget iframe can request a frame resize. Origin alone
  // is insufficient when another window from the same origin is present.
  // Remember the logical size and re-clamp on rotation/keyboard resize, so
  // the customer never needs to close and reopen the panel after rotating.
  function applyViewportSize() {
    if (requestedWidth === null || requestedHeight === null) {
      return;
    }
    var availableWidth = window.innerWidth;
    var availableHeight = window.innerHeight;
    var visual = window.visualViewport;
    if (visual) {
      if (isFinite(visual.width) && visual.width > 0) {
        availableWidth = Math.min(availableWidth, visual.width);
      }
      if (isFinite(visual.height) && visual.height > 0) {
        availableHeight = Math.min(availableHeight, visual.height);
      }
      // On mobile the virtual keyboard can cover the fixed-position frame.
      // Lift the widget by the obscured bottom strip without moving the
      // host page or changing the conversation.
      if (isFinite(visual.offsetTop)) {
        var obscuredBottom = Math.max(
          0, window.innerHeight - visual.height - visual.offsetTop
        );
        iframe.style.bottom = obscuredBottom + "px";
      }
    }
    // Keep the established theme widths and 40px breathing room; only
    // clamp to the real host viewport. The iframe's own 96px width is NOT
    // a trustworthy viewport measurement.
    iframe.style.width = Math.max(
      56, Math.min(requestedWidth + 40, 520, availableWidth)
    ) + "px";
    iframe.style.height = Math.max(
      56, Math.min(requestedHeight + 40, 640, availableHeight)
    ) + "px";
  }

  window.addEventListener("message", function (event) {
    if (event.origin !== loaderOrigin || event.source !== iframe.contentWindow) {
      return;
    }
    var data = event.data;
    if (!data || data.type !== "cxops-embed:resize") {
      return;
    }
    var width = Number(data.width);
    var height = Number(data.height);
    if (!isFinite(width) || !isFinite(height) || width < 0 || height < 0) {
      return;
    }
    requestedWidth = width;
    requestedHeight = height;
    applyViewportSize();
  }, false);

  window.addEventListener("resize", applyViewportSize, false);
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", applyViewportSize, false);
    window.visualViewport.addEventListener("scroll", applyViewportSize, false);
  }
})();