document.addEventListener("DOMContentLoaded", () => {
    const container = document.getElementById("ai-widget-container");
    if (!container) return;

    const toggleBtn = document.getElementById("ai-toggle-btn");
    const chatWindow = document.getElementById("ai-chat-window");
    const closeBtn = document.getElementById("close-ai-btn");
    const sendBtn = document.getElementById("ai-send-btn");
    const input = document.getElementById("ai-user-input");
    const messagesArea = document.getElementById("ai-chat-messages");

    // Pencere Aç/Kapa
    function toggleChat() {
        chatWindow.classList.toggle("hidden");
        if (!chatWindow.classList.contains("hidden")) {
            setTimeout(() => input.focus(), 100);
        }
    }

    if(toggleBtn) toggleBtn.addEventListener("click", toggleChat);
    if(closeBtn) closeBtn.addEventListener("click", toggleChat);

    // Mesaj Ekleme Fonksiyonu
    function addMessage(text, sender) {
        const msgDiv = document.createElement("div");
        msgDiv.className = `message ${sender}-message`;
        
        // Product notes, user messages and model output are untrusted text.
        const content = document.createElement('div');
        content.className = 'message-content';
        content.style.whiteSpace = 'pre-wrap';
        content.textContent = String(text == null ? '' : text);
        msgDiv.appendChild(content);
        
        messagesArea.appendChild(msgDiv);
        scrollToBottom();
    }

    // Yükleniyor Göstergesi
    function showTyping() {
        const loaderDiv = document.createElement("div");
        loaderDiv.className = "message ai-message typing-loader-msg";
        loaderDiv.innerHTML = `
            <div class="message-content" style="display:flex; gap:5px; padding:15px;">
                <span style="animation:blink 1.4s infinite both; animation-delay:0s;">.</span>
                <span style="animation:blink 1.4s infinite both; animation-delay:0.2s;">.</span>
                <span style="animation:blink 1.4s infinite both; animation-delay:0.4s;">.</span>
            </div>
            <style>@keyframes blink { 0% {opacity:0.2;} 20% {opacity:1;} 100% {opacity:0.2;} }</style>
        `;
        messagesArea.appendChild(loaderDiv);
        scrollToBottom();
        return loaderDiv;
    }

    function removeTyping(loaderElement) {
        if (loaderElement) loaderElement.remove();
    }

    function scrollToBottom() {
        messagesArea.scrollTop = messagesArea.scrollHeight;
    }

    // Mesaj Gönderme
    async function handleSend() {
        const text = input.value.trim();
        if (!text) return;

        // Kullanıcı mesajını ekle
        addMessage(text, "user");
        input.value = "";

        // Yükleniyor...
        const loader = showTyping();

        try {
            const response = await fetch("/chat_ai", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message: text })
            });

            const data = await response.json();
            
            removeTyping(loader);

            if (data.ai_response) {
                // Display model output as text; no HTML interpretation.
                addMessage(data.ai_response, "ai");
                
                // Eğer sayfa yenilenmesi gerekiyorsa (stok eklendi vb.)
                if (typeof loadProducts === 'function') {
                    loadProducts();
                }
            } else if (data.error || data.message) {
                addMessage("⚠️ " + (data.message || data.error?.message || data.error), "ai");
            }

        } catch (error) {
            removeTyping(loader);
            console.error("AI Hatası:", error);
            addMessage("⚠️ Sunucu ile bağlantı kurulamadı.", "ai");
        }
    }

    if(sendBtn) sendBtn.addEventListener("click", handleSend);
    if(input) {
        input.addEventListener("keydown", (e) => {
            if (e.key === "Enter") handleSend();
        });
    }
});
