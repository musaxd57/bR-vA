document.addEventListener("DOMContentLoaded", function () {
    const onayla = document.getElementById("confirmb");
    const dgr = document.getElementById("colorInput");
    const clearPut = document.getElementById("clearb");
    const tableBody = document.querySelector("#colors_table tbody"); // Tablo gövdesini seçiyoruz
    const statusEl = document.createElement("p");
    dgr.parentNode.insertBefore(statusEl, dgr.nextSibling);

    /**
     * RENKLERİ TABLOYA EKLEYEN FONKSİYON
     * Bu fonksiyon, hem sayfa yüklendiğinde hem de yeni renk eklendiğinde kullanılacak.
     * Bu sayede kod tekrarı önlenir ve tutarlı bir yapı sağlanır.
     */
    function addColorToTable(color) {
        const tr = document.createElement("tr");
        tr.dataset.code = color.code; // Satırı koduyla etiketleyelim

        // DİKKAT: Sil butonuna hem orijinal sınıfları hem de 'delete-btn' sınıfını ekliyoruz.
        tr.innerHTML = `
            <td>${BrivaDOM.escapeHtml(color.code)}</td>
            <td>${BrivaDOM.escapeHtml(color.name)}</td>
            <td>
                <button class="btn btn-danger btn-sm delete-btn" data-code="${BrivaDOM.escapeHtml(color.code)}">Sil</button>
            </td>
        `;
        tableBody.prepend(tr); // Yeni rengi tablonun en üstüne ekle
    }

    // --- SAYFA YÜKLENDİĞİNDE MEVCUT RENKLERİ GETİR ---
    async function fetchAndDisplayColors() {
        try {
            const response = await fetch('/get_colors');
            const data = await response.json();

            if (data.success) {
                tableBody.innerHTML = ''; // Tabloyu temizle
                data.colors.forEach(color => {
                    addColorToTable(color); // Her bir rengi tabloya ekle
                });
            }
        } catch (error) {
            console.error("Renkler getirilirken hata:", error);
            statusEl.textContent = "Renkler yüklenemedi.";
            statusEl.style.color = "red";
        }
    }

    // --- RENK EKLEME OLAYI ---
    onayla.addEventListener("click", async function () {
        const colorName = dgr.value.trim();

        if (!colorName) {
            statusEl.textContent = "Lütfen bir renk girin.";
            statusEl.style.color = "red";
            return;
        }

        try {
            onayla.disabled = true;
            onayla.textContent = "Kaydediliyor...";

            const response = await fetch("/add_color", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ color: colorName })
            });

            if (!response.ok) {
                throw new Error("Sunucu hatası: " + response.status);
            }

            const data = await response.json();
            
            // YENİ: Arayüze eklemek için artık standart fonksiyonumuzu kullanıyoruz.
            addColorToTable({ name: colorName, code: data.code });

            statusEl.textContent = "Başarıyla kaydedildi!";
            statusEl.style.color = "green";
            dgr.value = "";

        } catch (err) {
            console.error(err);
            statusEl.textContent = "Kayıt sırasında hata oluştu.";
            statusEl.style.color = "red";
        } finally {
            onayla.disabled = false;
            onayla.textContent = "Onayla";
        }
    });

    // --- RENK SİLME OLAYI (Event Delegation Yöntemi) ---
    // Artık tüm silme işlemleri için tek bir dinleyici yeterli.
    tableBody.addEventListener('click', async function(event) {
        // Tıklanan element 'delete-btn' sınıfına sahip bir butonsa...
        if (event.target && event.target.classList.contains('delete-btn')) {
            const button = event.target;
            const codeToDelete = button.dataset.code;
            // DÜZELTME: Silinecek olan element butonun içinde bulunduğu tüm satırdır ('tr').
            const rowToDelete = button.closest('tr');

            if (!confirm(`'${codeToDelete}' kodlu rengi silmek istediğinizden emin misiniz?`)) {
                return;
            }

            try {
                button.disabled = true;
                button.textContent = '...';

                const resp = await fetch(`/delete_color/${encodeURIComponent(codeToDelete)}`, {
                    method: "DELETE"
                });

                if (!resp.ok) throw new Error("Sunucu silme hatası: " + resp.status);
                
                // Başarılı silme sonrası satırı arayüzden kaldır.
                rowToDelete.remove();
                statusEl.textContent = "Renk başarıyla silindi.";
                statusEl.style.color = "green";

            } catch (err) {
                console.error(err);
                statusEl.textContent = "Silme sırasında hata oluştu.";
                statusEl.style.color = "red";
                button.disabled = false;
                button.textContent = 'Sil';
            }
        }
    });
    
    // --- TEMİZLEME BUTONU ---
    clearPut.addEventListener("click", function () {
        dgr.value = "";
        statusEl.textContent = "";
    });

    // Sayfa ilk açıldığında renkleri yükle
    fetchAndDisplayColors();
});
