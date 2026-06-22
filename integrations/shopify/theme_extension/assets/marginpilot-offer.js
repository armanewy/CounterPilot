(function () {
  function ready(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  }

  ready(function () {
    document.querySelectorAll("[data-marginpilot-open]").forEach(function (button) {
      button.addEventListener("click", function () {
        var root = button.closest(".marginpilot-offer");
        var form = root && root.querySelector("[data-marginpilot-form]");
        if (form) {
          form.hidden = !form.hidden;
        }
      });
    });

    document.querySelectorAll("[data-marginpilot-form]").forEach(function (form) {
      form.addEventListener("submit", function (event) {
        var productField = form.querySelector("[name='product_gid']");
        event.preventDefault();
        form.dispatchEvent(new CustomEvent("marginpilot:offer-submitted", {
          bubbles: true,
          detail: {
            productGid: productField ? productField.value : null,
            offerAmount: form.querySelector("[name='offer_amount']").value
          }
        }));
      });
    });
  });
})();
