// ==================== GLOBAL DEĞİŞKENLER ====================
let users = [];
let companys = [];
let showingBayiler = false; // Şu an bayiler mi listeleniyor?
let currentPage = 1;
const rowsPerPage = 10;
let selectedUserIds = new Set(); // Seçili kullanıcı ID'lerini tutar

// ==================== DOM ELEMENTLERİ ====================
// Formlar
const mudurForm = document.querySelector(".yeni-mudur-formu");
const filterInput = document.getElementById("filterIsim");

// Tablo
const tableBody = document.querySelector(".ebody");
const pageNumbersContainer = document.getElementById("pageNumbersContainer");
const selectAllCheckbox = document.getElementById("selectAllUsers");

// İstatistikler
const statMudurs = document.getElementById("statMudurs");
const statBayis = document.getElementById("statBayis");
const statUsers = document.getElementById("statUsers");

// Bildirim Modali
const notifyModal = document.getElementById("notifyModal");
const btnOpenNotifyModal = document.getElementById("btnOpenNotifyModal");
const closeNotifyModalBtn = document.getElementById("closeNotifyModalBtn");
const notifyForm = document.getElementById("notifyForm");
const notifyMessageInput = document.getElementById("notifyMessage");
const selectedCountSpan = document.getElementById("selectedCount");
const notifyRecipientCountSpan = document.getElementById("notifyRecipientCount");

// Düzenleme Modali
const editUserModal = document.getElementById("editUserModal");
const editUserForm = document.getElementById("editUserForm");
const closeEditModalBtn = document.getElementById("closeModalBtn");
const editUserId = document.getElementById("editUserId");
const editUsername = document.getElementById("editUsername");
const editPassword = document.getElementById("editPassword");
const editRole = document.getElementById("editRole");
const editCompany = document.getElementById("editCompany");
const editPhone = document.getElementById("editPhone");


// ==================== BAŞLANGIÇ ====================
document.addEventListener("DOMContentLoaded", () => {
    fetchUsers();
    setupEventListeners();
});

// ==================== VERİ ÇEKME İŞLEMLERİ ====================
async function fetchUsers() {
    try {
        const res = await fetch("/getallusers");
        users = await res.json();
        // Veriler yenilendiğinde tabloyu çiz
        renderTable(users);
        updateStats(users);
    } catch (err) {
        console.error("Kullanıcılar alınamadı:", err);
        showMessage("Veri çekme hatası", "error");
    }
}

async function fetchCompanys() {
    try {
        const res = await fetch("/get_all_companys");
        companys = await res.json();
        renderTable(companys);
    } catch (err) {
        console.error("Bayiler alınamadı:", err);
    }
}

// ==================== TABLO & RENDER İŞLEMLERİ ====================

// Filtreleme Fonksiyonu
function getFilteredData() {
    const list = showingBayiler ? companys : users;
    const term = normalize(filterInput.value.trim());
    
    if (!term) return list;

    return list.filter(item => {
        // Kullanıcı ise username, bayi ise name üzerinden arama
        const val = showingBayiler ? item.name : item.username;
        return normalize(val).includes(term);
    });
}

// Tabloyu Çizme
function renderTable() {
    tableBody.innerHTML = "";
    const activeData = getFilteredData();

    // Sayfalama Hesapları
    const start = (currentPage - 1) * rowsPerPage;
    const end = start + rowsPerPage;
    const pageItems = activeData.slice(start, end);

    if (pageItems.length === 0) {
        tableBody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding: 2rem; color: #94a3b8;">Veri bulunamadı.</td></tr>`;
    } else {
        pageItems.forEach(item => {
            const tr = document.createElement("tr");

            if (showingBayiler) {
                // Bayi Listesi
                tr.innerHTML = `
                    <td>-</td>
                    <td>${item.name}</td>
                    <td colspan="3"><span class="badge">Bayi</span></td>
                    <td><button class="sil-btn" onclick="deleteCompany(${item.id})">Sil</button></td>
                `;
            } else {
                // Kullanıcı Listesi
                const isChecked = selectedUserIds.has(item.id) ? "checked" : "";
                
                tr.innerHTML = `
                    <td>
                        <input type="checkbox" class="custom-checkbox user-cb" 
                               data-id="${item.id}" ${isChecked}>
                    </td>
                    <td>${item.username}</td>
                    <td><span style="background:rgba(255,255,255,0.1); padding:2px 8px; border-radius:4px;">${item.role || '-'}</span></td>
                    <td>${item.company || '-'}</td>
                    <td>${item.phone || '-'}</td>
                    <td>
                        <button class="duzenle-btn" onclick='openEditUser(${JSON.stringify(item)})'><i class="ri-edit-line"></i></button>
                        <button class="sil-btn" onclick="deleteUser(${item.id})"><i class="ri-delete-bin-line"></i></button>
                    </td>
                `;
            }
            tableBody.appendChild(tr);
        });
    }

    // Checkbox Eventlerini Bağla
    if (!showingBayiler) {
        document.querySelectorAll(".user-cb").forEach(cb => {
            cb.addEventListener("change", (e) => {
                const uid = parseInt(e.target.dataset.id);
                if (e.target.checked) selectedUserIds.add(uid);
                else selectedUserIds.delete(uid);
                updateSelectionUI();
            });
        });
    }

    renderPagination(activeData.length);
    updateSelectionUI(); // Select All kutusunun durumunu güncellemek için
}

// Sayfalama
function renderPagination(totalItems) {
    pageNumbersContainer.innerHTML = "";
    const totalPages = Math.ceil(totalItems / rowsPerPage) || 1;

    // Önceki Buton
    document.getElementById("prevBtn").onclick = () => {
        if (currentPage > 1) { currentPage--; renderTable(); }
    };

    // Sonraki Buton
    document.getElementById("nextBtn").onclick = () => {
        if (currentPage < totalPages) { currentPage++; renderTable(); }
    };

    // Sayfa Numaraları
    for (let i = 1; i <= totalPages; i++) {
        const btn = document.createElement("button");
        btn.textContent = i;
        if (i === currentPage) btn.classList.add("active-page");
        btn.onclick = () => { currentPage = i; renderTable(); };
        pageNumbersContainer.appendChild(btn);
    }
}

// ==================== BİLDİRİM & SEÇİM MANTIĞI ====================

function updateSelectionUI() {
    const count = selectedUserIds.size;
    selectedCountSpan.textContent = count;
    notifyRecipientCountSpan.textContent = count;

    // Toplu işlem butonu görünürlüğü
    btnOpenNotifyModal.style.display = count > 0 ? "flex" : "none";

    // "Tümünü Seç" kutusunun durumu
    const allVisibleCheckboxes = document.querySelectorAll(".user-cb");
    if (allVisibleCheckboxes.length > 0) {
        const allChecked = Array.from(allVisibleCheckboxes).every(cb => cb.checked);
        selectAllCheckbox.checked = allChecked;
    } else {
        selectAllCheckbox.checked = false;
    }
}

// Tümünü Seç / Kaldır
selectAllCheckbox.addEventListener("change", (e) => {
    const isChecked = e.target.checked;
    const visibleData = getFilteredData(); // Sadece o an filtrelenenleri seç

    visibleData.forEach(item => {
        if (isChecked) selectedUserIds.add(item.id);
        else selectedUserIds.delete(item.id);
    });

    renderTable(); // Tabloyu yenile ki checkboxlar güncellensin
});

// Şablon Değişkeni Ekleme ({username} vb.)
window.insertVar = function(variable) {
    const input = notifyMessageInput;
    const start = input.selectionStart;
    const end = input.selectionEnd;
    const text = input.value;
    
    input.value = text.substring(0, start) + variable + text.substring(end);
    input.focus();
    input.setSelectionRange(start + variable.length, start + variable.length);
}

// Bildirim Gönderme
notifyForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    
    const payload = {
        user_ids: Array.from(selectedUserIds),
        title: document.getElementById("notifyTitle").value,
        type: document.getElementById("notifyType").value,
        message: notifyMessageInput.value
    };

    if (payload.user_ids.length === 0) return showMessage("Kullanıcı seçilmedi!", "error");

    try {
        const res = await fetch("/send_bulk_notification", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const result = await res.json();

        if (res.ok) {
            showMessage(result.message, "success");
            notifyModal.style.display = "none";
            notifyForm.reset();
            selectedUserIds.clear();
            renderTable(); // Seçimleri temizle
        } else {
            showMessage(result.message || "Hata", "error");
        }
    } catch (err) {
        console.error(err);
        showMessage("Sunucu hatası", "error");
    }
});


// ==================== CRUD İŞLEMLERİ ====================

// Müdür Ekle
mudurForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = {
        name: document.getElementById("mudurIsim").value.trim(),
        password: document.getElementById("mudurSifre").value.trim(),
        company: document.getElementById("mudurBayi").value.trim(),
        phone: document.getElementById("mudurphone").value.trim(),
        role: "manager"
    };

    if (!data.name || !data.password) return showMessage("Eksik bilgi!", "error");

    try {
        const res = await fetch("/add_manager", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data)
        });
        const result = await res.json();
        
        if (result.status === "ok") {
            showMessage("Müdür başarıyla eklendi", "success");
            users.push(result.user);
            renderTable();
            updateStats(users);
            mudurForm.reset();
        } else {
            showMessage(result.msg, "error");
        }
    } catch (err) {
        showMessage("Bağlantı hatası", "error");
    }
});

// Kullanıcı Sil
window.deleteUser = async function(id) {
    if (!confirm("Kullanıcı silinecek. Emin misiniz?")) return;
    try {
        const res = await fetch(`/deleteuser/${id}`, { method: "DELETE" });
        if (res.ok) {
            users = users.filter(u => u.id !== id);
            selectedUserIds.delete(id);
            renderTable();
            updateStats(users);
            showMessage("Kullanıcı silindi", "success");
        } else {
            showMessage("Silinemedi", "error");
        }
    } catch (err) { showMessage("Hata", "error"); }
};

// Kullanıcı Düzenleme Modalını Aç
window.openEditUser = function(user) {
    editUserId.value = user.id;
    editUsername.value = user.username;
    editRole.value = user.role || "";
    editCompany.value = user.company || "";
    editPhone.value = user.phone || "";
    editPassword.value = ""; 
    editUserModal.style.display = "flex";
};

// Kullanıcı Güncelle
editUserForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = editUserId.value;
    const data = {
        username: editUsername.value.trim(),
        role: editRole.value.trim(),
        company: editCompany.value.trim(),
        phone: editPhone.value.trim(),
        password: editPassword.value // Boşsa backend dikkate almaz
    };

    try {
        const res = await fetch(`/update_user/${id}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data)
        });
        const result = await res.json();

        if (res.ok) {
            showMessage("Güncellendi", "success");
            editUserModal.style.display = "none";
            fetchUsers(); // Listeyi yenile
        } else {
            showMessage(result.message || "Hata", "error");
        }
    } catch (err) {
        showMessage("Sunucu hatası", "error");
    }
});

// ==================== YARDIMCI FONKSİYONLAR ====================

function setupEventListeners() {
    // Filtreleme
    filterInput.addEventListener("input", () => {
        currentPage = 1;
        renderTable();
    });

    // Şifre Göster/Gizle
    const toggleBtn = document.getElementById("togglePassword");
    if(toggleBtn) {
        toggleBtn.addEventListener("click", () => {
            const input = document.getElementById("mudurSifre");
            input.type = input.type === "password" ? "text" : "password";
        });
    }

    // Modal Kapatma Tuşları
    closeNotifyModalBtn.onclick = () => notifyModal.style.display = "none";
    closeEditModalBtn.onclick = () => editUserModal.style.display = "none";
    btnOpenNotifyModal.onclick = () => notifyModal.style.display = "grid"; // Flex veya Grid ile ortala

    // Dışarı tıklama
    window.onclick = (e) => {
        if (e.target === notifyModal) notifyModal.style.display = "none";
        if (e.target === editUserModal) editUserModal.style.display = "none";
    };

    // Tablo Başlık Değişimi (İsim <-> Bayiler)
    const toggleHeader = document.getElementById("isimHeader");
    if(toggleHeader) {
        toggleHeader.addEventListener("click", () => {
            showingBayiler = !showingBayiler;
            currentPage = 1;
            selectedUserIds.clear(); // Mod değişince seçimleri temizle
            
            const span = toggleHeader.querySelector("span");
            if(showingBayiler) {
                span.textContent = "Bayiler";
                fetchCompanys();
                // Bayi modunda bildirim butonu ve checkboxları gizler
                btnOpenNotifyModal.style.display = "none";
            } else {
                span.textContent = "İsim";
                fetchUsers();
            }
        });
    }
}

function normalize(str) {
    if(!str) return "";
    return str.toLowerCase()
        .replace(/ı/g, 'i').replace(/ö/g, 'o').replace(/ü/g, 'u')
        .replace(/ş/g, 's').replace(/ç/g, 'c').replace(/ğ/g, 'g');
}

function updateStats(list) {
    const mudurCount = list.filter(u => u.role === "manager").length;
    // Unique company count
    const uniqueCompanies = new Set(list.map(u => u.company).filter(Boolean)).size;

    if(statMudurs) statMudurs.textContent = mudurCount;
    if(statBayis) statBayis.textContent = uniqueCompanies;
    if(statUsers) statUsers.textContent = list.length;
}

function showMessage(text, type = "success") {
    const div = document.createElement("div");
    div.innerHTML = `<i class="ri-${type === 'success' ? 'checkbox-circle' : 'error-warning'}-line"></i> ${text}`;
    Object.assign(div.style, {
        position: "fixed", top: "20px", right: "20px",
        padding: "1rem 1.5rem", borderRadius: "10px",
        background: type === "success" ? "#10b981" : "#ef4444",
        color: "white", fontWeight: "600",
        boxShadow: "0 10px 30px rgba(0,0,0,0.2)",
        zIndex: "9999", display: "flex", gap: "10px", alignItems: "center",
        animation: "slideIn 0.3s ease-out"
    });
    
    // CSS Keyframe animasyonu ekleyelim (JS ile style tag'ine de eklenebilir ama admin.css'te olması daha iyi)
    document.body.appendChild(div);
    setTimeout(() => div.remove(), 3000);
}

// Global LogOut
window.logOut = function() {
    window.location.href = "logout";
}