// Code editor for CODE questions: CodeMirror on top of the answer textarea.
//
// The textarea stays the form field. Every edit is copied back into it and announced
// with an "input" event, so question.js keeps saving drafts and timing exactly as with
// the plain textarea. Without JavaScript (or if CodeMirror fails to load) the plain
// textarea is used.
(function () {
  const textarea = document.getElementById("answer");
  if (!textarea || !textarea.classList.contains("code") || !window.CodeMirror) return;

  const editor = window.CodeMirror.fromTextArea(textarea, {
    mode: textarea.dataset.mode || "python",
    theme: "kl",
    lineNumbers: true,
    indentUnit: 4,
    tabSize: 4,
    indentWithTabs: false,
    matchBrackets: true,
    autoCloseBrackets: true,
    viewportMargin: Infinity,
    extraKeys: {
      Tab: (cm) => (cm.somethingSelected() ? cm.indentSelection("add") : cm.replaceSelection("    ", "end")),
      "Shift-Tab": (cm) => cm.indentSelection("subtract"),
    },
  });

  editor.on("change", () => {
    editor.save();
    textarea.dispatchEvent(new Event("input"));
  });

  // The hidden textarea cannot show the browser's "required" bubble, so check here,
  // before question.js disables the submit button.
  textarea.required = false;
  window.addEventListener(
    "submit",
    (event) => {
      if (event.target !== textarea.form) return;
      editor.save();
      if (textarea.value.trim()) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      const notice = document.getElementById("saved-notice");
      notice.textContent = "Кодоо бичээд илгээнэ үү.";
      notice.className = "notice error";
      notice.hidden = false;
      editor.focus();
    },
    true,
  );
})();
