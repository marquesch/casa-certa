"use strict";

(() => {
  const STORAGE_KEY = "casa-certa-passphrase";
  const SALT_BYTES = 16;
  const IV_BYTES = 12;
  const form = document.getElementById("unlock");
  const input = document.getElementById("passphrase");
  const remember = document.getElementById("remember");
  const message = document.getElementById("message");
  const payloadElement = document.getElementById("payload");
  const iterations = Number(payloadElement.dataset.iterations);

  async function decrypt(passphrase) {
    const raw = Uint8Array.from(atob(payloadElement.textContent.trim()), (character) => character.charCodeAt(0));
    const salt = raw.slice(0, SALT_BYTES);
    const iv = raw.slice(SALT_BYTES, SALT_BYTES + IV_BYTES);
    const data = raw.slice(SALT_BYTES + IV_BYTES);
    const secret = new TextEncoder().encode(passphrase.trim().normalize("NFKC"));
    const material = await crypto.subtle.importKey("raw", secret, "PBKDF2", false, ["deriveKey"]);
    const key = await crypto.subtle.deriveKey(
      { name: "PBKDF2", hash: "SHA-256", salt, iterations },
      material,
      { name: "AES-GCM", length: 256 },
      false,
      ["decrypt"],
    );
    return new TextDecoder().decode(await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, data));
  }

  // True while an attempt runs and after one succeeds, so the saved passphrase and a manual submit can never both
  // reach document.write (a second write would redeclare the page's top-level consts and break it).
  let unlocking = false;

  // "opened", "wrong" (the GCM check failed: wrong passphrase or modified payload) or "failed" (anything else, such as
  // WebCrypto being unavailable outside a secure context).
  async function unlock(passphrase, store) {
    unlocking = true;
    let html;
    try {
      html = await decrypt(passphrase);
    } catch (error) {
      unlocking = false;
      return error && error.name === "OperationError" ? "wrong" : "failed";
    }
    if (store) localStorage.setItem(STORAGE_KEY, passphrase);
    document.open();
    document.write(html);
    document.close();
    return "opened";
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (unlocking) return;
    message.textContent = message.dataset.busy;
    const result = await unlock(input.value, remember.checked);
    message.textContent = result === "opened" ? "" : message.dataset.wrong;
  });

  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved) {
    unlock(saved, false).then((result) => {
      if (result === "wrong") localStorage.removeItem(STORAGE_KEY);
    });
  }
})();
