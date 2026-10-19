document.addEventListener("DOMContentLoaded", () => {
  
  // --- SEKME YÖNETİMİ ---
  const tabs = document.querySelectorAll(".tab-link");
  const panels = document.querySelectorAll(".tab-panel");

  tabs.forEach(tab => {
    tab.addEventListener("click", e => {
      e.preventDefault();
      tabs.forEach(t => t.classList.remove("active"));
      panels.forEach(p => p.classList.remove("active"));
      tab.classList.add("active");
      const targetPanel = document.getElementById(tab.dataset.tab);
      if (targetPanel) targetPanel.classList.add("active");
    });
  });

  function activateTabFromHash() {
    const hash = window.location.hash.substring(1);
    if (hash) {
      const targetTab = Array.from(tabs).find(tab => tab.dataset.tab === hash);
      if (targetTab) targetTab.click();
    }
  }
  
  // --- FONKSİYONLARI BAŞLAT ---
  setupColorManagement();
  setupCustomerManagement();
  setupShelfManagement();
  setupCategoryManagement();

  activateTabFromHash();
  window.addEventListener('hashchange', activateTabFromHash);

});


// ===================================================================
// 1. RENK YÖNETİMİ (DÜZELTİLDİ)
// ===================================================================
function setupColorManagement() {
    const confirmBtn = document.getElementById("confirmb");
    const colorInput = document.getElementById("colorInput");
    const colorCodeInput = document.getElementById("colorCodeInput"); // Yeni inputu yakalıyoruz
    const colorForm = document.getElementById("colorForm");
    const tableBody = document.querySelector("#colors_table tbody");
    
    if (!confirmBtn) return;

    const statusEl = document.createElement("p");
    statusEl.style.marginTop = "10px";
    statusEl.style.fontSize = "0.85rem";
    if (colorForm) colorForm.appendChild(statusEl);

    function addColorToTable(color) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td>
                <span style="background:#F3F4F6; padding:4px 8px; border-radius:6px; font-family:monospace; font-size:0.85rem; font-weight:700;">
                    ${BrivaDOM.escapeHtml(color.code)}
                </span>
            </td>
            <td style="font-weight:600; color:#111827;">${BrivaDOM.escapeHtml(color.name)}</td>
            <td class="text-right">
                <button class="btn-sm-delete delete-btn" data-code="${BrivaDOM.escapeHtml(color.code)}" title="Sil">
                    <i class="ri-delete-bin-line"></i>
                </button>
            </td>
        `;
        tableBody.prepend(tr);
    }

    async function fetchAndDisplayColors() {
        try {
            const response = await fetch('/get_colors');
            if (!response.ok) return;
            const data = await response.json();
            if (data.success) {
                tableBody.innerHTML = '';
                data.colors.forEach(color => addColorToTable(color));
            }
        } catch (error) { console.error("Renkler getirilirken hata:", error); }
    }

    // KAYDETME İŞLEMİ
    confirmBtn.addEventListener("click", async () => {
        const colorName = colorInput.value.trim();
        const colorCode = colorCodeInput.value.trim().toUpperCase(); // Kodu al ve büyük harfe çevir

        if (!colorName || !colorCode) return showToast("Lütfen hem Renk Adı hem de Kod girin!", "err");
        
        statusEl.textContent = "Kaydediliyor...";
        statusEl.style.color = "#6B7280";

        try {
            const response = await fetch("/add_color", {
                method: "POST", 
                headers: { "Content-Type": "application/json" }, 
                body: JSON.stringify({ color: colorName, code: colorCode }) // Kodu gönderiyoruz
            });
            
            if (!response.ok) {
                const data = await response.json();
                throw new Error(data.message || "Kaydedilemedi");
            }
            
            addColorToTable({ name: colorName, code: colorCode });
            colorInput.value = "";
            colorCodeInput.value = "";
            
            statusEl.textContent = "Renk başarıyla eklendi.";
            statusEl.style.color = "#10B981";
            setTimeout(() => statusEl.textContent = "", 3000);

        } catch (err) { 
            console.error(err); 
            statusEl.textContent = err.message || "Hata oluştu.";
            statusEl.style.color = "#EF4444";
        }
    });

    tableBody.addEventListener('click', async (event) => {
        const button = event.target.closest('.delete-btn');
        if (button) {
            const codeToDelete = button.dataset.code;
            if (!confirm(`'${codeToDelete}' kodlu rengi silmek istediğinizden emin misiniz?`)) return;
            try {
                const resp = await fetch(`/delete_color/${encodeURIComponent(codeToDelete)}`, { method: "DELETE" });
                if (!resp.ok) throw new Error("Sunucu silme hatası");
                button.closest('tr').remove();
            } catch (err) { console.error(err); }
        }
    });

    fetchAndDisplayColors();
}

// ===================================================================
// 2. MÜŞTERİ YÖNETİMİ
// ===================================================================
function setupCustomerManagement() {
    const form = document.getElementById("savenewcustomer");
    if (!form) return;
    
    const nameEl = document.getElementById("savecustomername");
    const phoneEl = document.getElementById("phone");
    const statusEl = document.getElementById("customerFormStatus");
    const tbody = document.querySelector("#customer_table tbody");

    // Tabloya yeni satır ekleyen yardımcı fonksiyon
    function addCustomerRow(customer) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td style="font-weight:600; color:#111827;">${BrivaDOM.escapeHtml(customer.name)}</td>
            <td style="color:#6B7280; font-family:monospace;">${BrivaDOM.escapeHtml(customer.phone)}</td>
            <td class="text-right">
                <button class="btn-sm-delete delete-btn" data-id="${BrivaDOM.escapeHtml(customer.id)}" title="Sil">
                    <i class="ri-delete-bin-line"></i>
                </button>
            </td>
        `;
        // Yeni eklenen müşteri listenin en başına gelsin istiyorsanız appendChild yerine prepend kullanabilirsiniz
        tbody.prepend(tr); 
    }

    // Müşterileri Sunucudan Çekme
    async function loadCustomers() {
        try {
            const response = await fetch("/get_all_customers");
            if (!response.ok) return;
            const data = await response.json();
            if (data.success) {
                tbody.innerHTML = "";
                // Veritabanından gelen sıraya göre ekler
                data.customers.forEach(c => tbody.appendChild(createRowElement(c)));
            }
        } catch (err) { console.error(err); }
    }
    
    // Satır HTML'ini oluşturan yardımcı fonksiyon (Kod tekrarını önlemek için)
    function createRowElement(customer) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td style="font-weight:600; color:#111827;">${BrivaDOM.escapeHtml(customer.name)}</td>
            <td style="color:#6B7280; font-family:monospace;">${BrivaDOM.escapeHtml(customer.phone)}</td>
            <td class="text-right">
                <button class="btn-sm-delete delete-btn" data-id="${BrivaDOM.escapeHtml(customer.id)}" title="Sil">
                    <i class="ri-delete-bin-line"></i>
                </button>
            </td>
        `;
        return tr;
    }

    // Form Gönderimi (Yeni Ekleme)
    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const payLoad = { name: nameEl.value.trim(), phone: phoneEl.value.replace(/\D/g, "") };
        
        if (statusEl) {
            statusEl.textContent = "Kaydediliyor...";
            statusEl.style.color = "#6B7280";
        }

        try {
            const response = await fetch("/add_customer", {
                method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payLoad)
            });
            if (!response.ok) throw new Error("Sunucu hatası");
            
            const data = await response.json();
            
            if (data.customer) {
                // EKRANA DİREKT ÇİZME (Sayfa yenilemeden ekler)
                addCustomerRow(data.customer);
                
                if (statusEl) {
                    statusEl.textContent = "Müşteri kaydedildi.";
                    statusEl.style.color = "#10B981"; // Yeşil
                    setTimeout(() => statusEl.textContent = "", 3000);
                }
                // Inputları temizle
                nameEl.value = "";
                phoneEl.value = "";
            }
        } catch (err) { 
            console.error(err);
            if (statusEl) {
                statusEl.textContent = "Hata oluştu.";
                statusEl.style.color = "#EF4444"; // Kırmızı
            }
        }
    });

    // --- SİLME İŞLEMİ VE EKRANDAN KALDIRMA ---
    tbody.addEventListener("click", async (event) => {
        // Tıklanan elementin 'delete-btn' sınıfına sahip olup olmadığını veya içinde olup olmadığını kontrol et
        const button = event.target.closest('.delete-btn');
        
        if (button) {
            const customerId = button.dataset.id;
            
            // Kullanıcı onayı
            if (!confirm('Bu müşteriyi silmek istediğinize emin misiniz?')) return;
            
            try {
                // Sunucuya silme isteği gönder
                const resp = await fetch(`/delete_customer/${customerId}`, { method: "DELETE" });
                
                if (!resp.ok) throw new Error("Backend silme hatası");
                
                // === KRİTİK BÖLÜM: DOM'DAN SİLME ===
                // Sayfayı yenilemeden, tıklanan butonun bulunduğu satırı (tr) bul ve kaldır.
                const row = button.closest("tr");
                if (row) {
                    row.remove();
                }
                // ===================================

            } catch (err) { 
                console.error(err);
                alert("Silme işlemi sırasında bir hata oluştu.");
            }
        }
    });

    // Telefon Formatlama (05XX XXX XX XX)
    if (phoneEl) {
        phoneEl.addEventListener("input", (e) => {
            let t = e.target.value.replace(/\D/g,"");
            if (t.startsWith("0")) t = t.substring(1);
            let l="";
            if(t.length>0) l=t.substring(0,3);
            if(t.length>3) l+=" "+t.substring(3,6);
            if(t.length>6) l+=" "+t.substring(6,8);
            if(t.length>8) l+=" "+t.substring(8,10);
            e.target.value = l;
        });
    }
    
    // Sayfa açılışında listeyi yükle
    loadCustomers();
}


// ===================================================================
// 3. RAF YÖNETİMİ
// ===================================================================
function setupShelfManagement() {
    const shelfForm = document.getElementById('shelfForm');
    if (!shelfForm) return;
    const newShelfInput = document.getElementById('newShelf');
    const shelvesTableBody = document.querySelector('#shelves_table tbody');

    async function loadShelves() {
      try {
        const res = await fetch('/get_shelves');
        if (!res.ok) return;
        const data = await res.json();
        shelvesTableBody.innerHTML = '';
        data.shelves.forEach(shelf => {
          const tr = document.createElement('tr');
          tr.innerHTML = `
            <td style="font-weight:700; color:#D97706; font-family:monospace;">${BrivaDOM.escapeHtml(shelf.code)}</td>
            <td class="text-right">
                <button class="btn-sm-delete delete-btn" data-id="${BrivaDOM.escapeHtml(shelf.id)}">
                    <i class="ri-delete-bin-line"></i>
                </button>
            </td>
          `;
          shelvesTableBody.appendChild(tr);
        });
      } catch (err) { console.error('Raflar yüklenemedi', err); }
    }

    shelfForm.addEventListener('submit', async e => {
      e.preventDefault();
      try {
        const res = await fetch('/add_shelf', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code: newShelfInput.value })
        });
        if (!res.ok) throw new Error('Kaydedilemedi');
        newShelfInput.value = '';
        loadShelves();
      } catch { alert('Raf kaydedilemedi!'); }
    });

    shelvesTableBody.addEventListener('click', async e => {
      const btn = e.target.closest('.delete-btn');
      if (btn) {
        const id = btn.dataset.id;
        if (!confirm('Bu rafı silmek istediğinizden emin misiniz?')) return;
        try {
          const res = await fetch(`/delete_shelf/${id}`, { method: "DELETE" });
          if (!res.ok) throw new Error('Silinemedi');
          loadShelves();
        } catch { alert('Raf silinemedi!'); }
      }
    });

    loadShelves();
}


// ===================================================================
// 4. KATEGORİ YÖNETİMİ
// ===================================================================
function setupCategoryManagement() {
    const form = document.getElementById("categoryForm");
    if(!form) return;
    
    const nameInput = document.getElementById("categoryName");
    const sizeInput = document.getElementById("sizeInput");
    const addBtn = document.getElementById("addSizeBtn");
    const listEl = document.getElementById("sizeList");
    const emptyHint = document.getElementById("emptyHint");
    // const statusEl = document.getElementById("categoryFormStatus"); // HTML'de yoksa kullanmıyoruz
    const categoriesTbody = document.querySelector("#categories_table tbody");
    const sizes = new Set();

    const renderChips = () => {
        listEl.innerHTML = "";
        if (sizes.size === 0) {
            if (emptyHint) emptyHint.style.display = "block";
        } else {
            if (emptyHint) emptyHint.style.display = "none";
            sizes.forEach((val) => {
                const li = document.createElement("li");
                li.className = "chip";
                li.textContent = val;
                li.onclick = () => { sizes.delete(val); renderChips(); };
                listEl.appendChild(li);
            });
        }
    };

    const addSize = () => {
        const value = sizeInput.value.trim().toUpperCase();
        if (value) {
            sizes.add(value);
            sizeInput.value = "";
            renderChips();
            sizeInput.focus();
        }
    };
    
    if (addBtn) addBtn.addEventListener("click", addSize);
    if (sizeInput) {
        sizeInput.addEventListener("keydown", (e) => { 
            if (e.key === "Enter") { 
                e.preventDefault(); // Form submit olmasın
                addSize(); 
            } 
        });
    }

    const addCategoryRow = (cat) => {
        const tr = document.createElement("tr");
        const sizesStr = (cat.sizes && cat.sizes.length) 
            ? cat.sizes.map(s => `<span style="background:#F1F5F9; padding:2px 6px; border-radius:4px; font-size:0.8rem; margin-right:4px;">${BrivaDOM.escapeHtml(s)}</span>`).join('')
            : '<span style="color:#9CA3AF;">-</span>';
            
        tr.innerHTML = `
            <td style="font-weight:600; color:#1F2937;">${BrivaDOM.escapeHtml(cat.name)}</td>
            <td>${sizesStr}</td>
            <td class="text-right">
                <button class="btn-sm-delete delete-btn" data-id="${BrivaDOM.escapeHtml(cat.id)}">
                    <i class="ri-delete-bin-line"></i>
                </button>
            </td>
        `;
        categoriesTbody.appendChild(tr);
    };

    async function loadCategories() {
        try {
            const res = await fetch("/get_categories");
            if (!res.ok) return;
            const data = await res.json();
            categoriesTbody.innerHTML = "";
            (data.categories || []).forEach(addCategoryRow);
        } catch (err) { console.error(err); }
    }

    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const payload = { name: nameInput.value.trim(), sizes: Array.from(sizes) };
        if (!payload.name) {
            alert("Lütfen kategori adı giriniz.");
            return;
        }
        try {
            const res = await fetch("/add_category", {
                method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
            });
            if (!res.ok) throw new Error("Sunucu hatası");
            const responseData = await res.json();
            addCategoryRow({ id: responseData.id, ...payload });
            form.reset();
            sizes.clear();
            renderChips();
        } catch (err) { console.error(err); }
    });

    categoriesTbody.addEventListener("click", async (e) => {
        const btn = e.target.closest('.delete-btn');
        if (btn) {
            const id = btn.dataset.id;
            if (!confirm("Bu kategoriyi silmek istediğinizden emin misiniz?")) return;
            try {
                const res = await fetch("/delete_category/" + id, { method: "DELETE" });
                if (!res.ok) throw new Error("Silinemedi");
                btn.closest("tr").remove();
            } catch (err) { console.error(err); }
        }
    });

    loadCategories();
}
