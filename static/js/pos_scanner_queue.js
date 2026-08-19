(function () {
  var queueKey = "mobipos.offlineInvoices";
  var deviceKey = "mobipos.deviceId";

  function deviceId() {
    var generated = window.crypto && window.crypto.randomUUID ? window.crypto.randomUUID() : "device-" + Date.now() + "-" + Math.random().toString(16).slice(2);
    try {
      var existing = localStorage.getItem(deviceKey);
      if (existing) return existing;
      localStorage.setItem(deviceKey, generated);
    } catch (error) {
      return generated;
    }
    return generated;
  }

  function csrfToken() {
    var meta = document.querySelector("meta[name='csrf-token']");
    if (meta && meta.content) return meta.content;
    var match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function loadQueue() {
    try {
      return JSON.parse(localStorage.getItem(queueKey) || "[]");
    } catch (error) {
      return [];
    }
  }

  function saveQueue(items) {
    try {
      localStorage.setItem(queueKey, JSON.stringify(items));
    } catch (error) {
      var status = document.querySelector("[data-offline-status]");
      if (status) status.textContent = "Browser storage is unavailable. Offline drafts cannot be saved on this device.";
      return false;
    }
    document.querySelectorAll("[data-offline-count]").forEach(function (node) {
      node.textContent = String(items.length);
    });
    return true;
  }

  function snapshotInvoice() {
    var saleLines = Array.prototype.slice.call(document.querySelectorAll("[data-cart-line]")).map(function (line) {
      return {
        product: line.dataset.productName || "",
        quantity: line.dataset.quantity || "",
        total: line.dataset.total || ""
      };
    });
    return {
      client_reference: "offline-" + Date.now() + "-" + Math.random().toString(16).slice(2),
      created_at: new Date().toISOString(),
      location: document.querySelector("[data-pos-location]")?.dataset.posLocation || "",
      cart_id: document.querySelector("[data-pos-cart-id]")?.dataset.posCartId || "",
      schema_version: 1,
      device_id: deviceId(),
      lines: saleLines
    };
  }

  function initOfflineQueue() {
    var saveButton = document.querySelector("[data-save-offline-invoice]");
    var syncButton = document.querySelector("[data-sync-offline-invoices]");
    var status = document.querySelector("[data-offline-status]");
    var endpoint = syncButton ? syncButton.dataset.syncOfflineInvoices : "";
    saveQueue(loadQueue());

    if (saveButton) {
      saveButton.addEventListener("click", function () {
        var queue = loadQueue();
        var snapshot = snapshotInvoice();
        if (!snapshot.cart_id || !snapshot.lines.length) {
          if (status) status.textContent = "Add an item to a live cart before saving a recovery draft.";
          return;
        }
        queue.push(snapshot);
        if (saveQueue(queue) && status) {
          status.textContent = "Recovery draft saved on this device without customer, payment, or IMEI details. Upload it for review when online.";
        }
      });
    }

    function syncQueue() {
      var queue = loadQueue();
      if (!endpoint || !queue.length) {
        if (status) status.textContent = queue.length ? "Recovery drafts are waiting to upload." : "No recovery drafts are waiting on this device.";
        return;
      }
      fetch(endpoint, {
        method: "POST",
        credentials: "same-origin",
        headers: {"Content-Type": "application/json", "X-CSRFToken": csrfToken()},
        body: JSON.stringify({invoices: queue})
      }).then(function (response) {
        return response.json().then(function (body) {
          return {ok: response.ok, body: body};
        });
      }).then(function (result) {
        if (result.ok && result.body.ok) {
          var accepted = result.body.accepted || [];
          var acceptedLookup = accepted.reduce(function (acc, reference) {
            acc[reference] = true;
            return acc;
          }, {});
          var retained = loadQueue().filter(function (invoice) {
            return !acceptedLookup[invoice.client_reference];
          });
          saveQueue(retained);
          if (status) {
            status.textContent = accepted.length + " recovery draft(s) uploaded for review; " + retained.length + " retained on this device.";
          }
        } else if (status) {
          status.textContent = result.body.error || "Recovery draft upload failed.";
        }
      }).catch(function () {
        if (status) status.textContent = "The server could not be reached. Recovery drafts remain on this device.";
      });
    }

    if (syncButton) syncButton.addEventListener("click", syncQueue);
    window.addEventListener("online", syncQueue);
  }

  function initCameraScanner() {
    var openButton = document.querySelector("[data-open-camera-scanner]");
    var modal = document.querySelector("[data-camera-scanner]");
    var video = document.querySelector("[data-camera-video]");
    var manualInput = document.querySelector("[data-camera-manual]");
    var target = document.querySelector("[data-scanner-target]") || document.getElementById("id_lookup");
    var errorMessage = modal ? modal.querySelector("[data-camera-error]") : null;
    var stream = null;
    var detector = null;
    var scanning = false;

    function closeScanner() {
      scanning = false;
      if (stream) {
        stream.getTracks().forEach(function (track) { track.stop(); });
        stream = null;
      }
      if (modal) modal.hidden = true;
    }

    function setValue(value) {
      if (target && value) {
        target.value = value;
        target.focus();
      }
      closeScanner();
    }

    function showCameraError(message) {
      if (errorMessage) {
        errorMessage.textContent = message;
        errorMessage.hidden = false;
      }
    }

    async function scanLoop() {
      if (!scanning || !detector || !video || video.readyState < 2) {
        if (scanning) requestAnimationFrame(scanLoop);
        return;
      }
      try {
        var codes = await detector.detect(video);
        if (codes.length) {
          setValue(codes[0].rawValue);
          return;
        }
      } catch (error) {
        // Keep the manual field available when the browser cannot decode frames.
      }
      if (scanning) requestAnimationFrame(scanLoop);
    }

    if (openButton && modal && video) {
      openButton.addEventListener("click", async function () {
        modal.hidden = false;
        if (errorMessage) errorMessage.hidden = true;
        try {
          if (!("BarcodeDetector" in window) || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            throw new Error("unsupported");
          }
          detector = new BarcodeDetector({formats: ["qr_code", "code_128", "ean_13", "ean_8", "upc_a", "upc_e"]});
          stream = await navigator.mediaDevices.getUserMedia({video: {facingMode: "environment"}});
          video.srcObject = stream;
          await video.play();
          scanning = true;
          scanLoop();
        } catch (error) {
          closeScanner();
          var message = error && error.name === "NotAllowedError"
            ? "Camera access was blocked. Allow camera access in your browser, or type the value manually."
            : error && error.name === "NotFoundError"
              ? "No camera was found. Type or paste the IMEI, serial number or barcode instead."
              : "Camera scanning is unavailable on this device. Type or paste the value manually.";
          modal.hidden = false;
          showCameraError(message);
        }
      });
      modal.querySelectorAll("[data-close-camera-scanner]").forEach(function (button) {
        button.addEventListener("click", closeScanner);
      });
      modal.querySelector("[data-use-camera-manual]")?.addEventListener("click", function () {
        setValue(manualInput ? manualInput.value.trim() : "");
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      initOfflineQueue();
      initCameraScanner();
    });
  } else {
    initOfflineQueue();
    initCameraScanner();
  }
})();
