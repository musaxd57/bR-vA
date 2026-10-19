const form        = document.getElementById("categoryForm");
const nameInput   = document.getElementById("categoryName");
const sizeInput   = document.getElementById("sizeInput");
const addBtn      = document.getElementById("addSizeBtn");
const listEl      = document.getElementById("sizeList");
const emptyHint   = document.getElementById("emptyHint");
const statusEl    = document.getElementById("formStatus");
const categoriesTbody = document.querySelector("#categories_table tbody");

// === DURUM / YARDIMCI ===
const sizes = new Set();                       
const norm  = (v) => (v || "").trim();
const asKey = (v) => norm(v).toUpperCase();
const fmtSizes = (arr) => (arr && arr.length ? arr.join(" / ") : "-");

function setStatus(msg = "", type = "") {
  statusEl.className = "status " + type;    
  statusEl.textContent = msg;
}

function renderChips() {
  listEl.innerHTML = "";
  const arr = Array.from(sizes);
  emptyHint.style.display = arr.length ? "none" : "block";

  arr.forEach((val) => {
    const li = document.createElement("li");
    li.className = "chip";
    li.textContent = val;
    li.title = "Kaldırmak için tıklayın";
    li.addEventListener("click", () => {
      sizes.delete(val);
      renderChips();
    });
    listEl.appendChild(li);
  });
}

// === BEDEN EKLEME ===
function addSize() {
  const value = asKey(sizeInput.value);
  if (!value)       return setStatus("Beden alanı boş olamaz.", "err");
  if (sizes.has(value)) 
    return setStatus('"' + value + '" zaten ekli.', "err");

  sizes.add(value);
  sizeInput.value = "";
  setStatus("");
  renderChips();
}

sizeInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    addSize();
  }
});
addBtn.addEventListener("click", addSize);

// === KATEGORİLERİ LİSTELE ===
async function loadCategories() {
  try {
    const res = await fetch("/get_categories");
    if (!res.ok) throw new Error("Liste alınamadı (HTTP " + res.status + ")");
    const data = await res.json();

    categoriesTbody.innerHTML = "";
    (data.categories || []).forEach((cat) => {
      addCategoryRow(cat);
    });
  } catch (err) {
    console.error(err);
    setStatus("Kategoriler yüklenemedi.", "err");
  }
}

// Tabloda tek satır ekleme
function addCategoryRow(cat) {
  const tr = document.createElement("tr");
  tr.dataset.id = cat.id;
  tr.innerHTML = `
    <td>${cat.name}</td>
    <td>${fmtSizes(cat.sizes || [])}</td>
    <td><button class="delete-cat-btn" data-id="${cat.id}">Sil</button></td>
  `;
  categoriesTbody.appendChild(tr);
}

// === KATEGORİ EKLE (FORM SUBMIT) ===
form.addEventListener("submit", async (e) => {
  e.preventDefault();

  const name = norm(nameInput.value);
  if (!name) {
    setStatus("Kategori adı boş olamaz.", "err");
    nameInput.focus();
    return;
  }

  const payload = {
    name,
    sizes: Array.from(sizes)
  };

  try {
    setStatus("Gönderiliyor…");
    const res = await fetch("/add_category", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(text || "Sunucu hatası (HTTP " + res.status + ")");
    }

    // --- DEĞİŞİKLİK BURADA BAŞLIYOR ---

    // Sunucudan gelen yanıtı al (sadece ID'yi içerdiğini varsayıyoruz)
    const responseData = await res.json(); // Örn: { "id": 123 }

    // Tabloya eklemek için tam bir nesne oluştur.
    // ID'yi sunucudan, name ve sizes'ı ise zaten bildiğimiz form verilerinden al.
    const newCategoryForTable = {
      id: responseData.id,
      name: name, // veya payload.name
      sizes: payload.sizes
    };

    addCategoryRow(newCategoryForTable); // ◀ tabloya bu tam nesneyi yaz

    // --- DEĞİŞİKLİK BURADA BİTİYOR ---

    // Formu temizle
    nameInput.value = "";
    sizes.clear();
    renderChips();
    setStatus("Kategori başarıyla eklendi.", "ok");
    
  } catch (err) {
    console.error(err);
    setStatus("Hata: " + err.message, "err");
  }
});

// Kategori silme event listener'ı (değişiklik yok)
categoriesTbody.addEventListener("click", async (e) => {
  if (!e.target.classList.contains("delete-cat-btn")) return;

  const id = e.target.dataset.id;
  if (!confirm("Bu kategoriyi silmek istediğinizden emin misiniz?")) return;

  try {
    const res = await fetch("/delete_category/" + id, { method: "DELETE" });
    if (!res.ok) throw new Error("Silinemedi (HTTP " + res.status + ")");

    e.target.closest("tr").remove();
    setStatus("Kategori silindi.", "ok");
  } catch (err) {
    console.error(err);
    setStatus("Kategori silinemedi.", "err");
  }
});

// Sayfa yüklendiğinde kategorileri getir
loadCategories();