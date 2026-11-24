"use strict";
document.getElementById("login").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = document.getElementById("error");
  err.textContent = "";
  const res = await fetch("/api/login", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password: document.getElementById("password").value }),
  });
  if (res.ok) { location.href = "/"; return; }
  let msg = "Wrong password.";
  try { msg = (await res.json()).detail || msg; } catch {}
  err.textContent = msg;
});
