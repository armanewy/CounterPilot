(function () {
  function ready(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  }

  ready(function () {
    document.querySelectorAll("[data-counterpilot-open]").forEach(function (button) {
      button.addEventListener("click", function () {
        var root = button.closest(".counterpilot-offer");
        var form = root && root.querySelector("[data-counterpilot-form]");
        if (form) {
          form.hidden = !form.hidden;
        }
      });
    });

    document.querySelectorAll("[data-counterpilot-form]").forEach(function (form) {
      form.addEventListener("submit", function (event) {
        var productField = form.querySelector("[name='product_gid']");
        event.preventDefault();
        form.dispatchEvent(new CustomEvent("counterpilot:offer-submitted", {
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
