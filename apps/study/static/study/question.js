// Active-time measurement and answer drafts for the question page.
//
// response_time_sec counts seconds while this tab is visible AND focused, from opening
// the question (or the previous submit) to this submit. It survives reloads through the
// draft. The server stores it as a client-side measurement (timing_source).
(function () {
  const form = document.getElementById("answer-form");
  if (!form) return;
  const answer = document.getElementById("answer");
  const timeField = document.getElementById("response-time");
  const timerLabel = document.getElementById("timer");
  const button = document.getElementById("submit-btn");
  const key = form.dataset.draftKey;

  const load = () => { try { return JSON.parse(localStorage.getItem(key)) || {}; } catch (_) { return {}; } };
  const save = (d) => { try { localStorage.setItem(key, JSON.stringify(d)); } catch (_) {} };
  const clear = () => { try { localStorage.removeItem(key); } catch (_) {} };

  const params = new URLSearchParams(location.search);
  if (params.has("saved")) {
    clear();
    const notice = document.getElementById("saved-notice");
    notice.textContent = "Оролдлого #" + params.get("saved") + " хадгалагдлаа.";
    notice.hidden = false;
    history.replaceState(null, "", location.pathname);
  }

  const draft = load();
  let seconds = Number.isFinite(draft.seconds) ? draft.seconds : 0;
  // A server re-render after an error already contains the answer; otherwise restore the draft.
  if (!answer.value && typeof draft.answer === "string") answer.value = draft.answer;

  const active = () => document.visibilityState === "visible" && document.hasFocus();
  const render = () => {
    timerLabel.textContent = "Идэвхтэй хугацаа: " + Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
  };
  render();
  setInterval(() => {
    if (!active()) return;
    seconds += 1;
    render();
    if (seconds % 5 === 0) save({ answer: answer.value, seconds });
  }, 1000);

  answer.addEventListener("input", () => save({ answer: answer.value, seconds }));
  form.addEventListener("submit", () => {
    timeField.value = String(seconds);
    save({ answer: answer.value, seconds });
    button.disabled = true;
    button.textContent = "Илгээж байна…";
  });
  // Re-enable when the page comes back from the back/forward cache.
  window.addEventListener("pageshow", () => {
    button.disabled = false;
    button.textContent = "Хариулт илгээх";
  });
})();
