// Drafts are stored per user on this device; remove them on logout so the next
// person on a shared computer cannot read them.
document.querySelectorAll("form[data-clear-drafts]").forEach((form) => {
  form.addEventListener("submit", () => {
    try {
      Object.keys(localStorage)
        .filter((k) => k.startsWith("kl-draft:"))
        .forEach((k) => localStorage.removeItem(k));
    } catch (_) {}
  });
});
