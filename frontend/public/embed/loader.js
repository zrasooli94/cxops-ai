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

  document.body.appendChild(iframe);

  window.addEventListener(
    "message",
    function (event) {
      if (event.origin !== loaderOrigin) {
        return;
      }
      var data = event.data;
      if (!data || data.type !== "cxops-embed:resize") {
        return;
      }
      var width = Number(data.width);
      var height = Number(data.height);
      if (!isFinite(width) || !isFinite(height)) {
        return;
      }
      // The widget pins itself 20px inside its document corner, so the
      // embedded frame is that much larger and the margins create the gap.
      width = Math.max(56, Math.min(width + 40, 520));
      height = Math.max(56, Math.min(height + 40, 640));
      iframe.style.width = width + "px";
      iframe.style.height = height + "px";
    },
    false
  );
})();