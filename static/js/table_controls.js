(function () {
  function applyColumnState(table, key) {
    var controls = document.querySelectorAll('[data-column-controls="' + key + '"] [data-column-index]');
    controls.forEach(function (control) {
      var index = Number(control.dataset.columnIndex);
      var visible = control.checked;
      table.querySelectorAll("tr").forEach(function (row) {
        var cell = row.children[index];
        if (cell) {
          cell.hidden = !visible;
        }
      });
    });
  }

  function initColumnControls() {
    document.querySelectorAll("[data-column-table]").forEach(function (table) {
      var key = table.dataset.columnTable;
      var storageKey = "mobipos.columns." + key;
      var saved = {};
      try {
        saved = JSON.parse(localStorage.getItem(storageKey) || "{}");
      } catch (error) {
        saved = {};
      }
      var controls = document.querySelectorAll('[data-column-controls="' + key + '"] [data-column-index]');
      controls.forEach(function (control) {
        var columnKey = control.dataset.columnKey || control.dataset.columnIndex;
        if (Object.prototype.hasOwnProperty.call(saved, columnKey)) {
          control.checked = Boolean(saved[columnKey]);
        }
        control.addEventListener("change", function () {
          saved[columnKey] = control.checked;
          try {
            localStorage.setItem(storageKey, JSON.stringify(saved));
          } catch (error) {
            // Column visibility still applies for this page when browser storage is unavailable.
          }
          applyColumnState(table, key);
        });
      });
      applyColumnState(table, key);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initColumnControls);
  } else {
    initColumnControls();
  }
})();
