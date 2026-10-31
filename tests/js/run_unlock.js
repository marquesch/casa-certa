"use strict";
// Usage: node run_unlock.js <shell.html> <passphrase> [remember] [--double] [--saved=VALUE] [--no-subtle]
// Prints { written, message, stored } as JSON after "submitting" the form (twice at once with --double).
// --saved=VALUE puts VALUE in localStorage before the script loads and does not submit (the auto-unlock path).
// --no-subtle removes crypto.subtle, as in a non-secure context.
const fs = require("node:fs");
const vm = require("node:vm");

const flags = process.argv.slice(2).filter((argument) => argument.startsWith("--"));
const [shellPath, passphrase, remember] = process.argv.slice(2).filter((argument) => !argument.startsWith("--"));
const savedFlag = flags.find((flag) => flag.startsWith("--saved="));
const html = fs.readFileSync(shellPath, "utf8");
const payload = /<script type="text\/plain" id="payload" data-iterations="(\d+)">([^<]*)<\/script>/.exec(html);
const code = /<script id="unlock-code">([\s\S]*?)<\/script>/.exec(html);
if (!payload || !code) throw new Error("not an unlock shell");

const handlers = {};
const element = (extra = {}) => ({
  value: "",
  checked: false,
  textContent: "",
  dataset: {},
  addEventListener(type, handler) {
    handlers[type] = handler;
  },
  ...extra,
});
const elements = {
  unlock: element(),
  passphrase: element({ value: passphrase }),
  remember: element({ checked: remember === "remember" }),
  message: element({ dataset: { wrong: "WRONG", busy: "BUSY" } }),
  payload: element({ textContent: payload[2], dataset: { iterations: payload[1] } }),
};
const written = [];
const storage = new Map();
if (savedFlag) storage.set("casa-certa-passphrase", savedFlag.slice("--saved=".length));
const sandbox = {
  document: {
    getElementById: (id) => elements[id],
    open() {},
    write: (text) => written.push(text),
    close() {},
  },
  localStorage: {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: (key) => storage.delete(key),
  },
  crypto: flags.includes("--no-subtle") ? {} : globalThis.crypto,
  atob,
  TextEncoder,
  TextDecoder,
  Uint8Array,
  ArrayBuffer,
  Number,
};
vm.runInNewContext(code[1], sandbox);

(async () => {
  if (savedFlag) {
    await new Promise((resolve) => setTimeout(resolve, 500)); // let the auto-unlock settle
  } else if (flags.includes("--double")) {
    await Promise.all([handlers.submit({ preventDefault() {} }), handlers.submit({ preventDefault() {} })]);
  } else {
    await handlers.submit({ preventDefault() {} });
  }
  console.log(JSON.stringify({ written, message: elements.message.textContent, stored: [...storage.keys()] }));
})();
