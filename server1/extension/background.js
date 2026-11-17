importScripts("config.js");

async function serverRequest(path, body) {
  const options = {signal: AbortSignal.timeout(body ? 65000 : 10000)};
  if (body) {
    options.method = "POST";
    options.headers = {"Content-Type": "application/json"};
    options.body = JSON.stringify(body);
  }
  const response = await fetch(CAPTCHA_SERVER1_CONFIG.serverUrl + path, options);
  const data = await response.json();
  if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (!message || !["server1_health", "server1_detect"].includes(message.action)) return false;
  const operation = message.action === "server1_health"
    ? serverRequest("/health") : serverRequest("/detect", {image: message.image});
  operation.then(data => respond({ok: true, data, serverUrl: CAPTCHA_SERVER1_CONFIG.serverUrl}))
    .catch(error => respond({ok: false, error: error.message, serverUrl: CAPTCHA_SERVER1_CONFIG.serverUrl}));
  return true;
});
