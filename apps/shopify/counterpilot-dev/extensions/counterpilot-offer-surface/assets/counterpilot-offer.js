(function () {
  function ready(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  }

  function setStatus(form, message, isError) {
    var status = form.querySelector("[data-counterpilot-status]");
    if (!status) {
      return;
    }
    status.hidden = false;
    status.textContent = message;
    status.dataset.counterpilotError = isError ? "true" : "false";
  }

  function buildPayload(form) {
    var payload = {};
    var formData = new FormData(form);
    formData.forEach(function (value, key) {
      payload[key] = String(value);
    });
    return payload;
  }

  function parseJsonSafely(response) {
    return response.text().then(function (text) {
      if (!text) {
        return {};
      }
      try {
        return JSON.parse(text);
      } catch (error) {
        return {};
      }
    });
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
        var root = form.closest(".counterpilot-offer");
        var endpoint = root && root.getAttribute("data-counterpilot-offer-endpoint");
        var submitButton = form.querySelector("[type='submit']");
        event.preventDefault();
        if (!endpoint) {
          setStatus(form, "Offer endpoint is not configured.", true);
          return;
        }

        if (submitButton) {
          submitButton.disabled = true;
        }
        setStatus(form, "Submitting offer...", false);

        fetch(endpoint, {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "accept": "application/json",
            "content-type": "application/json"
          },
          body: JSON.stringify(buildPayload(form))
        })
          .then(function (response) {
            return parseJsonSafely(response).then(function (body) {
              if (!response.ok) {
                throw new Error(body.error || "Offer submission failed.");
              }
              return body;
            });
          })
          .then(function (body) {
            setStatus(form, "Offer submitted.", false);
            form.dispatchEvent(new CustomEvent("counterpilot:offer-submitted", {
              bubbles: true,
              detail: {
                transactionId: body.transaction_id,
                lifecycleState: body.lifecycle_state
              }
            }));
          })
          .catch(function (error) {
            setStatus(form, error.message || "Offer submission failed.", true);
          })
          .finally(function () {
            if (submitButton) {
              submitButton.disabled = false;
            }
          });
      });
    });
  });
})();
