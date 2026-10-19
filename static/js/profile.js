// /static/js/profile.js

// Diğer JS dosyalarındaki gibi bir bildirim fonksiyonu
function showToast(msg, type = "ok") {
  const toast = document.querySelector("#toast");
  if (!toast) return;
  toast.textContent = msg;
  toast.style.background = type === "err" ? "#EF4444" : "#10B981";
  toast.style.display = "block";
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (toast.style.display = "none"), 3000);
}

document.addEventListener("DOMContentLoaded", () => {
  // --- Element Seçimleri ---
  const profileForm = document.getElementById("profile-form");
  const passwordForm = document.getElementById("password-form");
  const usernameInput = document.getElementById("username");
  const phoneInput = document.getElementById("phone");

  // --- Fonksiyonlar ---

  // Sayfa yüklendiğinde mevcut kullanıcı bilgilerini çeker ve formları doldurur
  async function loadUserProfile() {
    try {
      const response = await fetch("/get_current_user_data");
      if (!response.ok) {
        showToast("Kullanıcı bilgileri alınamadı!", "err");
        return;
      }
      const data = await response.json();
      usernameInput.value = data.username || "";
      phoneInput.value = data.phone || "";
    } catch (error) {
      console.error("Profil yüklenirken hata:", error);
      showToast("Sunucuya bağlanılamadı!", "err");
    }
  }

  // Profil Bilgileri Formu Gönderildiğinde
  profileForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const newUsername = usernameInput.value.trim();
    const newPhone = phoneInput.value.trim();

    if (!newUsername) {
      showToast("Kullanıcı adı boş bırakılamaz!", "err");
      return;
    }

    try {
      const response = await fetch("/update_profile", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: newUsername,
          phone: newPhone,
        }),
      });
      const data = await response.json();
      if (response.ok) {
        showToast("Profil başarıyla güncellendi.", "ok");
        // Kullanıcı adı değiştiyse, sayfa yenilendiğinde sidebar'ın doğru görünmesi için
        // session'ın güncellenmesi backend'de yapılıyor.
      } else {
        showToast(data.message || "Güncelleme başarısız!", "err");
      }
    } catch (error) {
      console.error("Profil güncellenirken hata:", error);
      showToast("Sunucu hatası!", "err");
    }
  });

  // Şifre Değiştirme Formu Gönderildiğinde
  passwordForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const currentPassword = document.getElementById("current-password").value;
    const newPassword = document.getElementById("new-password").value;
    const confirmPassword = document.getElementById("confirm-password").value;

    if (!currentPassword || !newPassword || !confirmPassword) {
      showToast("Lütfen tüm şifre alanlarını doldurun!", "err");
      return;
    }

    if (newPassword !== confirmPassword) {
      showToast("Yeni şifreler eşleşmiyor!", "err");
      return;
    }

    try {
      const response = await fetch("/change_password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });
      const data = await response.json();
      if (response.ok) {
        showToast("Şifre başarıyla değiştirildi.", "ok");
        passwordForm.reset(); // Formu temizle
      } else {
        showToast(data.message || "Şifre değiştirilemedi!", "err");
      }
    } catch (error) {
      console.error("Şifre değiştirilirken hata:", error);
      showToast("Sunucu hatası!", "err");
    }
  });

  // --- Sayfa Yüklenince Başlat ---
  loadUserProfile();
});