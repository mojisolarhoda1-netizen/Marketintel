// Small progressive enhancements. Everything works without JavaScript too.
document.addEventListener("DOMContentLoaded", () => {
  // Poll a running collection job, then reload.
  const poll = document.querySelector("[data-poll]");
  if (poll) {
    const url = poll.getAttribute("data-poll");
    const tick = () => fetch(url).then(r => r.json()).then(j => {
      if (j.status && j.status !== "running") location.reload(); else setTimeout(tick, 2500);
    }).catch(() => setTimeout(tick, 5000));
    setTimeout(tick, 2500);
  }
  // Copy buttons.
  document.querySelectorAll("[data-copy]").forEach(btn => btn.addEventListener("click", () => {
    const el = document.querySelector(btn.getAttribute("data-copy"));
    el.select();
    (navigator.clipboard ? navigator.clipboard.writeText(el.value) : Promise.reject()).catch(() => document.execCommand("copy"));
    const note = document.getElementById("copied"); if (note) note.textContent = "Copied. Paste it into a new Claude chat.";
  }));
  // Confirm destructive forms.
  document.querySelectorAll("form[data-confirm]").forEach(f => f.addEventListener("submit", e => {
    if (!window.confirm(f.getAttribute("data-confirm"))) e.preventDefault();
  }));
  // "Add to watchlist" picker on entity pages: the option value is the target form action.
  document.querySelectorAll("select[data-wl-select]").forEach(s => s.addEventListener("change", () => {
    if (!s.value) return; s.form.action = s.value; s.form.submit();
  }));
  // Auto-submit selects.
  document.querySelectorAll("select[data-autosubmit]").forEach(s => s.addEventListener("change", () => s.form.submit()));
  // Show a "working" message on slow searches so it never looks frozen.
  document.querySelectorAll("form[data-loading]").forEach(f => f.addEventListener("submit", () => {
    const b = f.querySelector("button.primary"); if (b) { b.disabled = true; b.textContent = f.dataset.loading; }
  }));
  // Load the AI strategy after the main results are on screen.
  const ai = document.getElementById("ai-strategy");
  if (ai && ai.dataset.src) {
    fetch(ai.dataset.src, {credentials: "same-origin"}).then(r => r.json()).then(d => {
      if (d.html) ai.innerHTML = d.html;
      else { ai.innerHTML = ""; const p = document.createElement("p"); p.className = "muted";
             p.textContent = (d.error || "AI unavailable") + " — use the Claude option below."; ai.appendChild(p); }
    }).catch(() => { ai.textContent = "Couldn't reach the AI. Use the Claude option below."; });
  }
});
