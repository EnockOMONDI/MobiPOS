(function () {
  var queueKey = "mobipos.offlineInvoices";

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
        queue.push(snapshotInvoice());
        if (saveQueue(queue) && status) {
          status.textContent = "Saved locally without customer or payment details. Revalidate online before completing the sale.";
        }
      });
    }

    function syncQueue() {
      var queue = loadQueue();
      if (!endpoint || !queue.length) {
        if (status) status.textContent = queue.length ? "Queue waiting for sync." : "No offline invoices waiting.";
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
            status.textContent = accepted.length + " offline draft(s) synced; " + retained.length + " retained for review.";
          }
        } else if (status) {
          status.textContent = result.body.error || "Offline invoice sync failed.";
        }
      }).catch(function () {
        if (status) status.textContent = "Still offline. Queue retained on this device.";
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
        if (!("BarcodeDetector" in window) || !navigator.mediaDevices) {
          return;
        }
        detector = new BarcodeDetector({formats: ["qr_code", "code_128", "ean_13", "ean_8", "upc_a", "upc_e"]});
        stream = await navigator.mediaDevices.getUserMedia({video: {facingMode: "environment"}});
        video.srcObject = stream;
        await video.play();
        scanning = true;
        scanLoop();
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
