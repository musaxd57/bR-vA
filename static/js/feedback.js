// static/js/feedback.js

document.addEventListener("DOMContentLoaded", () => {
  // Gerekli HTML elementlerini seç
  const feedbackForm = document.querySelector("form[action*='feedback']");
  const messageInput = document.getElementById("message");
  const submitButton = feedbackForm ? feedbackForm.querySelector("button[type='submit']") : null;

  // Elementler sayfada bulunamazsa, hatayı önlemek için kodu çalıştırma
  if (!feedbackForm || !messageInput || !submitButton) {
    return;
  }

  // Form gönderilmeye çalışıldığında bu fonksiyonu çalıştır
  feedbackForm.addEventListener("submit", (event) => {
    
    // 1. Mesaj alanındaki boşlukları temizle
    const trimmedMessage = messageInput.value.trim();

    // 2. Mesaj tamamen boş ise...
    if (trimmedMessage.length === 0) {
      // Formun gönderilmesini engelle
      event.preventDefault(); 
      
      // Kullanıcıyı uyar
      alert("Lütfen bir mesaj yazdığınızdan emin olun.");
      
      // İmleci tekrar metin alanına odakla
      messageInput.focus();
      return;
    }

    // 3. Form gönderimi geçerliyse, butonu devre dışı bırak
    // Bu, kullanıcının butona tekrar tekrar basmasını önler
    submitButton.disabled = true;
    submitButton.textContent = "Gönderiliyor...";
  });
});