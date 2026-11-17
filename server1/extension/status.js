const state = document.getElementById("server-state");
const link = document.getElementById("server-url");
link.textContent = CAPTCHA_SERVER1_CONFIG.serverUrl;
link.href = CAPTCHA_SERVER1_CONFIG.serverUrl + "/health";
document.getElementById("run-mode").textContent = CAPTCHA_SERVER1_CONFIG.mode === "fixture"
  ? "Chế độ: kiểm tra tích hợp trên trang mẫu local."
  : "Chế độ: chỉ chẩn đoán kết nối; không tự nhận diện ảnh.";
async function refresh() {
  try {
    const response = await chrome.runtime.sendMessage({action: "server1_health"});
    if (!response?.ok) throw new Error(`[${response?.code || "request_failed"}] ${response?.error || "Không nhận được phản hồi"}`);
    state.dataset.ready = "true";
    state.style.background = "#e0f3e6";
    state.textContent = `Server: đang chạy\nModel YOLO: ${response.data.model_loaded ? "đã nạp" : "chưa nạp"}\nAPI: ${response.serverUrl}/detect`;
    document.getElementById("model-error").textContent = response.data.model_error ? "Lỗi model: " + response.data.model_error : "";
  } catch (error) {
    state.dataset.ready = "false";
    state.style.background = "#ffe4e4";
    state.textContent = "Server chưa kết nối: " + error.message;
  }
  document.getElementById("last-check").textContent = "Lần kiểm tra gần nhất: " + new Date().toLocaleString("vi-VN", {timeZone: "Asia/Ho_Chi_Minh"});
}
refresh();
setInterval(refresh, 5000);
