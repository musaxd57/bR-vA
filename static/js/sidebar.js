document.addEventListener('DOMContentLoaded', function() {
    // --- MEVCUT KODLARINIZ (Toggle vb.) ---
    const toggleButton = document.getElementById('sidebar-toggle');
    const sidebar = document.getElementById('sidebar');

    if (toggleButton && sidebar) {
        toggleButton.addEventListener('click', function() {
            document.body.classList.toggle('sidebar-open');
        });

        document.addEventListener('click', function(event) {
            const isSidebarOpen = document.body.classList.contains('sidebar-open');
            const isClickInsideSidebar = sidebar.contains(event.target);
            const isClickOnToggleButton = toggleButton.contains(event.target);

            if (isSidebarOpen && !isClickInsideSidebar && !isClickOnToggleButton) {
                document.body.classList.remove('sidebar-open');
            }
        });
    }

    const dropdownToggles = document.querySelectorAll('.sidebar-dropdown-toggle');
    dropdownToggles.forEach(toggle => {
        toggle.addEventListener('click', function(event) {
            event.preventDefault();
            const parentItem = this.parentElement;
            const submenu = parentItem.querySelector('.sidebar-submenu');
            const isActive = this.classList.contains('active');

            document.querySelectorAll('.sidebar-dropdown-toggle').forEach(otherToggle => {
                if (otherToggle !== this) {
                    otherToggle.classList.remove('active');
                    const otherSubmenu = otherToggle.parentElement.querySelector('.sidebar-submenu');
                    if (otherSubmenu) otherSubmenu.style.maxHeight = null;
                }
            });

            if (!isActive) {
                this.classList.add('active');
                submenu.style.maxHeight = submenu.scrollHeight + "px";
            } else {
                this.classList.remove('active');
                submenu.style.maxHeight = null;
            }
        });
    });

    // --- YENİ: BİLDİRİM KONTROLÜ ---
    
    // Bu fonksiyon sunucudan okunmamış bildirim sayısını çeker
    async function checkNotifications() {
        try {
            // NOT: Buradaki URL'i kendi backend rotanızla değiştirmelisiniz.
            // Örn: app.py içinde @app.route('/api/get_unread_count') varsa.
            // Eğer böyle bir rota yoksa, test için aşağıda manuel sayı veriyoruz.
            
            // const response = await fetch('/api/get_unread_count');
            // const data = await response.json();
            // const count = data.count; 

            // TEST AMACIYLA MANUEL SAYI VERİYORUM:
            // Backend entegrasyonu yapılana kadar test etmek için burayı değiştirebilirsiniz.
            // Gerçekte bu veriyi backend'den almalısınız.
            // let count = 0; // Bildirim yok testi
            // let count = 5; // Bildirim var testi
            
            // Eğer Backend'den template ile veri basıyorsanız (örn: HTML içinde data attribute varsa)
            // const count = parseInt(document.body.getAttribute('data-unread-count') || 0);
            
            // --- SENARYO: Bir API endpoint'i çağırmak ---
            // Eğer endpointiniz yoksa bu fetch bloğunu yorum satırı yapıp test sayısını kullanın.
            const res = await fetch('/get_unread_count'); // Backend'de bu route olmalı
            if (!res.ok) return; // Hata varsa çık
            const data = await res.json();
            const count = data.count;

            updateNotificationUI(count);

        } catch (error) {
            console.error("Bildirim sayısı alınamadı:", error);
        }
    }

    function updateNotificationUI(count) {
        const dot = document.getElementById('toggle-notification-dot');
        const badge = document.getElementById('sidebar-notification-count');

        if (count > 0) {
            // Bildirim varsa göster
            if (dot) dot.style.display = 'block';
            if (badge) {
                badge.style.display = 'flex'; // Flex ortalama için
                badge.textContent = count > 99 ? '99+' : count;
            }
        } else {
            // Bildirim yoksa gizle
            if (dot) dot.style.display = 'none';
            if (badge) badge.style.display = 'none';
        }
    }

    // Sayfa yüklendiğinde kontrol et
    checkNotifications();

    // İsterseniz her 60 saniyede bir tekrar kontrol ettirebilirsiniz:
    // setInterval(checkNotifications, 60000);
});