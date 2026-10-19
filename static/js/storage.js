document.addEventListener("DOMContentLoaded", () => {
  let globalOrderList = {}; 
  // ============================================================
  // 1. ELEMENT SEÇİMLERİ
  // ============================================================
  const stokTbody = document.querySelector(".stok-table tbody");
  const formEkle = document.querySelector("form[action='/product']");

    const toggleDiscount = document.getElementById("toggle-discount");
    const discountWrapper = document.getElementById("discount-input-wrapper");
    const inputDiscount = document.getElementById("global-discount-input");

  const formSepetSatis = document.getElementById("form-sepet-satis");
  const btnCreateOrder = document.getElementById("btn-create-order");

  // Hızlı Sorgu Elementleri
  const formSorguDetay = document.getElementById("formSorguDetay");
  const inputSorguDetay = document.getElementById("s-kod-detay");
  const sonucKutuDetay = document.getElementById("sorgu-sonuc-detay");

  // Satış & Sorgu Elementleri
  const formSorgu = document.getElementById("formSorgu");
  const sonucKutu = document.querySelector("#sorgu-sonuc");
  const kodInputSorgu = document.getElementById("s-kod");
  const searchInput = document.getElementById("search-input");
  const sortSelect = document.getElementById("sort-select");
  const shelfSelect = document.getElementById("shelf");
  const categorySelect = document.getElementById("categorySelect");
  const sizesContainer = document.getElementById("sizes-container");

  // Satış Formu
  const addToCartBtn = document.getElementById("add-to-cart-btn");
  const cartContainer = document.getElementById("shopping-cart-container");
  const formSatisEkle = document.getElementById("form-satis-ekle");
  const satisKodInput = document.getElementById("satis-kod");
  const satisAdetInput = document.getElementById("satis-adet");
  const totalPriceDisplay = document.getElementById("total-price-value");
  const currencyButtons = document.querySelectorAll(".currency-btn");
  const selectedCurrencyInput = document.getElementById("selected-currency");
  const selectedPriceInput = document.getElementById("selected-price");
  const satisIndirimInput = document.getElementById("satis-indirim");
  const yuzdelikIndirimInput = document.getElementById("discount");

  // Barkod
  const startScanBtn = document.getElementById("start-scan-btn");
  const stopScanBtn = document.getElementById("stop-scan-btn");
  const scannerContainer = document.getElementById("barcode-scanner-container");
  let html5QrCode;

  // Renk
  const colorSelect = document.getElementById("colorSelect");
  const addColorBtn = document.getElementById("addColorBtn"); 
  const selectedColorsContainer = document.getElementById("selectedColorsContainer"); 

  // Borç
  const addAsDebtCheckbox = document.getElementById("add-as-debt-checkbox");
  const paymentContainer = document.getElementById("payment-container");
  const amountPaidInput = document.getElementById("amount-paid");
  const debtCalculationDisplay = document.getElementById("debt-calculation-display");

  //tekli satış
  const saleTypeRadios = document.getElementsByName("saleType");
  const seriesInputGroup = document.getElementById("series-input-group");
  const singleSaleGroup = document.getElementById("single-sale-group");
  const singleSaleSizesContainer = document.getElementById("single-sale-sizes-container");
  const unitPriceDisplay = document.getElementById("unit-price-display");

  //gider
  const expenditureForm = document.getElementById('form-gider-ekle');
  const expenditureListBody = document.getElementById('expenditure-list-body');

  // Tablar
  const tabs = document.querySelectorAll(".tab-link");

  // Veri Deposu
  let customerDataMap = {}; 
  let allProducts = [];
  let productList = {};
  let shoppingCart = [];
  let activeSaleCurrency = 'TL'; 
  let selectedColors = []; 
  let CATEGORIES = [];

  let isSingleSaleMode = false; // Mod takibi için

  // ============================================================
  // 2. YARDIMCI FONKSİYONLAR
  // ============================================================

  // --- EKSİK OLAN TEMEL NİTELİK (ATTRIBUTE) YÜKLEME FONKSİYONLARI ---

async function loadAttributeDefinitions() {
    try {
        const res = await fetch('/get_attribute_defs');
        if (!res.ok) return;
        const data = await res.json();

        if (data.status === 'ok') {
            // 1. Yönetim panelindeki tabloyu çiz
            renderAttributeManagementList(data.attributes);

            if(typeof populateRuleDropdown === 'function') {
                populateRuleDropdown(data.attributes);
            }

            // 2. Ürün ekleme formundaki Dropdown'ları (Select) çiz
            renderProductFormInputs(data.attributes);

            // 3. Stoklar sekmesindeki filtre dropdown'ını güncelle
            const filterSelect = document.getElementById('filter-attr-select');
            if (filterSelect) {
                filterSelect.innerHTML = '<option value="">Özellik Seç</option>';
                data.attributes.forEach(attr => {
                    const option = document.createElement('option');
                    option.value = attr.name;
                    option.textContent = attr.name;
                    filterSelect.appendChild(option);
                });
            }
        }
    } catch (err) {
        console.error("Nitelikler yüklenemedi:", err);
    }
}

function renderAttributeManagementList(attributes) {
    const tbody = document.getElementById('attribute-list-body');
    if (!tbody) return;
    tbody.innerHTML = '';
    
    if (attributes.length === 0) {
        tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; color:#9CA3AF;">Henüz özel nitelik tanımlanmamış.</td></tr>';
        return;
    }

    attributes.forEach(attr => {
        const optionsHtml = (attr.options && attr.options.length > 0)
            ? attr.options.map(opt => {
                const label = typeof opt === 'object' ? opt.label : opt;
                const code = (typeof opt === 'object' && opt.code) ? `<span style="color:#6B7280; margin-left:4px; font-size:0.75rem;">[${opt.code}]</span>` : '';
                return `<span class="badge badge-gray" style="margin-right:4px; margin-bottom:4px; display:inline-block;">${label}${code}</span>`;
            }).join('')
            : '<span style="color:#9CA3AF; font-style:italic;">Seçenek yok</span>';

        const optionsStr = encodeURIComponent(JSON.stringify(attr.options || []));

        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td style="font-weight:600; color:#374151;">${attr.name}</td>
            <td>${optionsHtml}</td>
            <td style="text-align: right;">
                <div style="display:flex; justify-content:flex-end; gap:5px;">
                    <button class="btn-secondary" style="padding: 5px 8px; color: #3B82F6;"
                            onclick="openEditAttributeModal(${attr.id}, '${attr.name}', '${optionsStr}')" title="Düzenle">
                        <i class="ri-edit-line"></i>
                    </button>
                    <button class="btn-delete" style="padding: 5px 8px;"
                            onclick="deleteAttributeDef(${attr.id})" title="Sil">
                        <i class="ri-delete-bin-line"></i>
                    </button>
                </div>
            </td>
        `;
        tbody.appendChild(tr);
    });
}


function renderProductFormInputs(attributes) {
    const container = document.getElementById('dynamic-attributes-container');
    const wrapper = document.getElementById('custom-attributes-wrapper');
    if (!container) return;
    container.innerHTML = '';

    if (attributes.length > 0) {
        if(wrapper) wrapper.style.display = 'block';

        attributes.forEach(attr => {
            const div = document.createElement('div');
            div.className = "modern-input-group";

            // Seçenekleri oluştururken Kodları yanına ekliyoruz
            const optionsHtml = (attr.options ||[]).map(opt => {
                const label = typeof opt === 'object' ? opt.label : opt;
                const code = (typeof opt === 'object' && opt.code) ? ` [${opt.code}]` : '';
                
                // value="${label}" olarak kalmalı, kullanıcının gördüğü kısım ${label}${code} olacak
                return `<option value="${label}">${label}${code}</option>`; 
            }).join('');

            div.innerHTML = `
                <label style="display:block; font-size:0.8rem; font-weight:600; color:#6B7280; margin-bottom:5px;">
                    ${attr.name}
                </label>
                <div class="select-wrapper">
                    <select class="clean-select dynamic-attr-input" data-key="${attr.name}">
                        <option value="">-- Seçiniz --</option>
                        ${optionsHtml}
                    </select>
                    <i class="ri-arrow-down-s-line"></i>
                </div>
            `;
            container.appendChild(div);
        });
    } else {
        if(wrapper) wrapper.style.display = 'none';
    }
}

window.deleteAttributeDef = async function(id) {
    if (!confirm("Bu niteliği tamamen silmek istediğinize emin misiniz?")) return;

    try {
        const res = await fetch(`/delete_attribute_def/${id}`, { method: 'DELETE' });
        const result = await res.json();

        if (res.ok && result.status === 'ok') {
            showToast(result.message, "ok");
            loadAttributeDefinitions(); // Tabloyu yenile
        } else {
            showToast(result.message || "Silinemedi", "err");
        }
    } catch (err) {
        showToast("Sunucu hatası", "err");
    }
};

// ==========================================
// BARKOD KURAL YÖNETİCİSİ (BUILDER)
// ==========================================
let currentBarcodeRules =[];

// Kuralları Yükle
async function loadBarcodeRules() {
    try {
        const res = await fetch('/api/barcode_rules');
        const data = await res.json();
        if (data.status === 'ok') {
            currentBarcodeRules = data.rules ||[];
            renderBarcodeRules();
        }
    } catch (e) {
        console.error("Barkod kuralları yüklenemedi", e);
    }
}

function populateRuleDropdown(attributes) {
    const select = document.getElementById('rule-type-select');
    if (!select) return;

    select.innerHTML = `
        <option value="random">Rastgele Sayı</option>
        <option value="category">Kategori Kodu</option>
        <option value="color">Renk Kodu</option>
        <option value="static">Sabit Yazı / Sayı</option>
    `;

    attributes.forEach(attr => {
        select.innerHTML += `<option value="custom_attr" data-attrname="${attr.name}">Özel Nitelik: ${attr.name}</option>`;
    });
}
// Dropdown değiştiğinde Sabit Yazı inputunu göster/gizle
const ruleTypeSelect = document.getElementById('rule-type-select');
const ruleStaticInput = document.getElementById('rule-static-input');

if (ruleTypeSelect && ruleStaticInput) {
    ruleTypeSelect.addEventListener('change', () => {
        if (ruleTypeSelect.value === 'static') {
            ruleStaticInput.style.display = 'block';
            ruleStaticInput.focus();
        } else {
            ruleStaticInput.style.display = 'none';
            ruleStaticInput.value = '';
        }
    });
}

// Kuralları Ekrana Çiz
function renderBarcodeRules() {
    const container = document.getElementById('barcode-rules-container');
    if (!container) return;

    container.innerHTML = '';

    if (currentBarcodeRules.length === 0) {
        container.innerHTML = '<span style="color:#9CA3AF; font-size:0.85rem; font-style:italic;">Kuralsız Standart Format Kullanılıyor.</span>';
        return;
    }

    currentBarcodeRules.forEach((rule, index) => {
        let label = "";
        if (rule.type === 'random') label = "RASTGELE SAYI";
        else if (rule.type === 'color') label = "RENK KODU";
        else if (rule.type === 'category') label = "KATEGORİ";
        else if (rule.type === 'static') label = `SABİT: "${rule.value}"`;
        else if (rule.type === 'custom_attr') label = `${rule.attr_name.toUpperCase()}`;

        const box = document.createElement('div');
        box.style.display = 'inline-flex';
        box.style.alignItems = 'center';
        
        let borderStyle = rule.is_group ? '2px solid #3B82F6' : '2px dashed #F59E0B';
        let bgColor = rule.is_group ? '#EFF6FF' : '#FFFBEB';
        let textColor = rule.is_group ? '#1E40AF' : '#9A3412';
        let tooltipText = rule.is_group ? 'Ortak (Varyasyon Grubu)' : 'Varyasyona Özel';

        box.innerHTML = `
            <span style="background:${bgColor}; color:${textColor}; border:${borderStyle}; padding:8px 16px; border-radius:8px; font-weight:800; font-size:0.85rem; letter-spacing:0.5px; display:flex; align-items:center; gap:8px;" title="${tooltipText}">
                ${label}
                <i class="ri-close-circle-fill" style="cursor:pointer; font-size:1.2rem; opacity:0.5; transition:0.2s;" 
                   onclick="removeBarcodeRule(${index})" onmouseover="this.style.opacity='1'" onmouseout="this.style.opacity='0.5'"></i>
            </span>
        `;

        container.appendChild(box);

        if (index < currentBarcodeRules.length - 1) {
            const plus = document.createElement('span');
            plus.innerHTML = '<i class="ri-add-line" style="font-size:1.5rem; color:#9CA3AF; font-weight:bold; margin: 0 5px;"></i>';
            container.appendChild(plus);
        }
    });
}

window.removeBarcodeRule = function(index) {
    currentBarcodeRules.splice(index, 1);
    renderBarcodeRules();
};

// Yeni Kural Ekle Butonu
const btnAddRule = document.getElementById('btn-add-rule');
if (btnAddRule) {
    btnAddRule.addEventListener('click', () => {
        const select = document.getElementById('rule-type-select');
        const isGroup = document.getElementById('rule-is-group').checked;
        const selectedOption = select.options[select.selectedIndex];
        
        const ruleType = selectedOption.value;
        const attrName = selectedOption.getAttribute('data-attrname');

        const newRule = { type: ruleType, is_group: isGroup };

        // Sabit yazı seçildiyse içini doldur ve kontrol et
        if (ruleType === 'static') {
            const staticVal = ruleStaticInput.value.trim().toUpperCase();
            if (!staticVal) return showToast("Sabit değer boş olamaz!", "err");
            newRule.value = staticVal;
            ruleStaticInput.value = ''; // Ekledikten sonra temizle
        } 
        else if (ruleType === 'custom_attr') {
            newRule.attr_name = attrName;
        }

        if (ruleType === 'random' && currentBarcodeRules.some(r => r.type === 'random')) {
            return showToast("Rastgele Sayı kuralı zaten ekli!", "err");
        }

        currentBarcodeRules.push(newRule);
        renderBarcodeRules();
    });
}

// Kuralları Kaydet
const btnSaveRules = document.getElementById('btn-save-rules');
if (btnSaveRules) {
    btnSaveRules.addEventListener('click', async () => {
        if (currentBarcodeRules.length > 0 && !currentBarcodeRules.some(r => r.type === 'random')) {
            if(!confirm("Formatınızda 'Rastgele Sayı' yok. Ürün kodları çakışabilir. Devam edilsin mi?")) return;
        }

        const originalText = btnSaveRules.innerHTML;
        btnSaveRules.innerHTML = "Kaydediliyor...";
        btnSaveRules.disabled = true;

        try {
            const res = await fetch('/api/barcode_rules', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ rules: currentBarcodeRules })
            });
            const data = await res.json();
            
            if (res.ok) {
                showToast(data.message, "ok");
            } else {
                showToast("Kaydedilemedi", "err");
            }
        } catch (e) {
            showToast("Bağlantı hatası", "err");
        } finally {
            btnSaveRules.innerHTML = originalText;
            btnSaveRules.disabled = false;
        }
    });
}


    if (toggleDiscount) {
        toggleDiscount.addEventListener("change", () => {
            if (toggleDiscount.checked) {
                discountWrapper.classList.remove("hidden");
                inputDiscount.focus();
            } else {
                discountWrapper.classList.add("hidden");
                inputDiscount.value = ""; 
            }
            updateCartTotal();
        });
        // Input değişince hesapla
        if (inputDiscount) inputDiscount.addEventListener("input", updateCartTotal);
    }

    const toggleVat = document.getElementById("toggle-vat");
    const vatWrapper = document.getElementById("vat-input-wrapper");
    const inputVat = document.getElementById("vat-percentage-input");

    if (toggleVat) {
        toggleVat.addEventListener("change", () => {
            if (toggleVat.checked) {
                vatWrapper.classList.remove("hidden");
                // Varsayılan %10 yap
                if (!inputVat.value) inputVat.value = 10;
                inputVat.focus();
            } else {
                vatWrapper.classList.add("hidden");
                inputVat.value = "";
            }
            updateCartTotal();
        });
        // Input değişince hesapla
        if (inputVat) inputVat.addEventListener("input", updateCartTotal);
    }

// --- NİTELİK YÖNETİMİ: LİSTE (ARRAY) VE EKRANA ÇİZİM MANTIĞI ---
// --- NİTELİK YÖNETİMİ: LİSTE (ARRAY) VE EKRANA ÇİZİM MANTIĞI ---
let newAttrOptions = []; 
let editAttrOptions = []; 
// --- YENİ NİTELİK OLUŞTURMA ALANI ---
const btnAddNewOpt = document.getElementById('btn-add-new-opt');
const inputNewLabel = document.getElementById('new-opt-label');
const inputNewCode = document.getElementById('new-opt-code');

if (btnAddNewOpt && inputNewLabel) {
    const addOption = () => {
        const labelVal = inputNewLabel.value.trim();
        const codeVal = inputNewCode.value.trim().toUpperCase(); // "PMK"
        
        if (labelVal) {
            // Zaten aynı isimde var mı kontrol et
            const exists = newAttrOptions.some(opt => (typeof opt === 'object' ? opt.label : opt) === labelVal);
            if (!exists) {
                // SİSTEME OBJE OLARAK EKLİYORUZ (Asıl Olay Burada)
                newAttrOptions.push({ label: labelVal, code: codeVal });
                renderAttrOptions('new-opt-container', newAttrOptions, false);
                inputNewLabel.value = ''; 
                inputNewCode.value = ''; 
                inputNewLabel.focus(); 
            } else {
                showToast("Bu seçenek zaten eklendi", "err");
            }
        }
    };

    btnAddNewOpt.addEventListener('click', addOption);
    inputNewLabel.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addOption(); } });
    if(inputNewCode) inputNewCode.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addOption(); } });
}


// --- DÜZENLEME (EDIT) ALANI ---
window.openEditAttributeModal = function(id, name, optionsJsonStr) {
    document.getElementById('edit-attr-id').value = id;
    document.getElementById('edit-attr-name').value = name;
    
    // Gelen JSON stringini çözüp diziye aktar
    editAttrOptions = JSON.parse(decodeURIComponent(optionsJsonStr)) || [];
    
    // Diziyi etiket (badge) olarak ekrana çiz
    renderAttrOptions('edit-opt-container', editAttrOptions, true);
    
    document.getElementById('edit-opt-label').value = ''; 
    document.getElementById('edit-opt-code').value = ''; 
    document.getElementById('edit-attribute-modal').style.display = 'flex';
};
const btnAddEditOpt = document.getElementById('btn-add-edit-opt');
const inputEditLabel = document.getElementById('edit-opt-label');
const inputEditCode = document.getElementById('edit-opt-code');

if (btnAddEditOpt && inputEditLabel) {
    const addEditOption = () => {
        const labelVal = inputEditLabel.value.trim();
        const codeVal = inputEditCode.value.trim().toUpperCase(); // "PLY"
        
        if (labelVal) {
            const exists = editAttrOptions.some(opt => (typeof opt === 'object' ? opt.label : opt) === labelVal);
            if (!exists) {
                // DÜZENLEME MODUNDA OBJE OLARAK EKLİYORUZ
                editAttrOptions.push({ label: labelVal, code: codeVal });
                renderAttrOptions('edit-opt-container', editAttrOptions, true);
                inputEditLabel.value = '';
                inputEditCode.value = '';
                inputEditLabel.focus();
            } else {
                showToast("Bu seçenek zaten eklendi", "err");
            }
        }
    };

    btnAddEditOpt.addEventListener('click', addEditOption);
    inputEditLabel.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addEditOption(); }});
    if(inputEditCode) inputEditCode.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addEditOption(); }});
}

const formNitelikDuzenle = document.getElementById('form-nitelik-duzenle');
if (formNitelikDuzenle) {
    formNitelikDuzenle.addEventListener('submit', async (e) => {
        e.preventDefault();
        const id = document.getElementById('edit-attr-id').value;
        const name = document.getElementById('edit-attr-name').value.trim();
        
        if (!name) return showToast("Nitelik adı boş bırakılamaz", "err");

        try {
            const res = await fetch(`/update_attribute_def/${id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name, options: editAttrOptions })
            });
            const result = await res.json();
            if (res.ok) {
                showToast(result.message, "ok");
                document.getElementById('edit-attribute-modal').style.display = 'none';
                loadAttributeDefinitions(); 
            } else {
                showToast(result.message || "Güncellenemedi", "err");
            }
        } catch (err) {
            showToast("Sunucu hatası", "err");
        }
    });
}

// Seçenekleri etiket (badge) olarak ekrana çizen fonksiyon
function renderAttrOptions(containerId, optionsArray, isEditMode) {
    const container = document.getElementById(containerId);
    if(!container) return;
    container.innerHTML = '';
    
    if(optionsArray.length === 0) {
        container.innerHTML = '<span style="color:#9CA3AF; font-size:0.85rem; font-style:italic;">Seçenek eklenmedi.</span>';
        return;
    }

    optionsArray.forEach((opt, index) => {
        // Eski veriler string olabilir, yeni veriler objedir. Geriye dönük uyumluluk:
        const label = typeof opt === 'object' ? opt.label : opt;
        const code = typeof opt === 'object' && opt.code ? opt.code : '';

        const codeHtml = code ? `<strong style="color:#3B82F6; margin-left:4px;">[${code}]</strong>` : '';

        const span = document.createElement('span');
        span.className = 'badge badge-gray';
        span.style.display = 'flex';
        span.style.alignItems = 'center';
        span.style.gap = '6px';
        span.style.padding = '6px 12px';
        span.style.fontSize = '0.85rem';
        
        span.innerHTML = `
            ${label} ${codeHtml}
            <i class="ri-close-line" style="cursor:pointer; color:#EF4444; font-size:1.1rem; font-weight:bold; border-left:1px solid #ccc; padding-left:4px;" 
               onclick="removeAttrOption(${index}, ${isEditMode})"></i>
        `;
        container.appendChild(span);
    });
}

window.removeAttrOption = function(index, isEditMode) {
    if (isEditMode) {
        editAttrOptions.splice(index, 1);
        renderAttrOptions('edit-opt-container', editAttrOptions, true);
    } else {
        newAttrOptions.splice(index, 1);
        renderAttrOptions('new-opt-container', newAttrOptions, false);
    }
};



if (btnAddNewOpt && inputNewLabel) {
    const addOption = () => {
        const labelVal = inputNewLabel.value.trim();
        const codeVal = inputNewCode.value.trim().toUpperCase();
        
        if (labelVal) {
            // Zaten var mı kontrolü
            const exists = newAttrOptions.some(opt => (typeof opt === 'object' ? opt.label : opt) === labelVal);
            if (!exists) {
                newAttrOptions.push({ label: labelVal, code: codeVal });
                renderAttrOptions('new-opt-container', newAttrOptions, false);
                inputNewLabel.value = ''; 
                inputNewCode.value = ''; 
                inputNewLabel.focus(); 
            } else {
                showToast("Bu seçenek zaten eklendi", "err");
            }
        }
    };

    btnAddNewOpt.addEventListener('click', addOption);
    inputNewLabel.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addOption(); } });
    if(inputNewCode) inputNewCode.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addOption(); } });
}

// Yeni Niteliği Kaydet
const formNitelikEkle = document.getElementById('form-nitelik-ekle');
if (formNitelikEkle) {
    formNitelikEkle.addEventListener('submit', async (e) => {
        e.preventDefault();
        const name = document.getElementById('attr-name').value.trim();
        if (!name) return showToast("Lütfen bir isim girin", "err");

        try {
            const res = await fetch('/add_attribute_def', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name, options: newAttrOptions }) 
            });
            const result = await res.json();
            if (res.ok) {
                showToast(result.message, "ok");
                document.getElementById('attr-name').value = ''; 
                newAttrOptions = []; 
                renderAttrOptions('new-opt-container', newAttrOptions, false); 
                loadAttributeDefinitions(); 
            } else {
                showToast(result.message || "Hata oluştu", "err");
            }
        } catch (err) {
            showToast("Sunucu hatası", "err");
        }
    });
}

// --- DÜZENLEME (EDIT) ALANI ---
window.openEditAttributeModal = function(id, name, optionsJsonStr) {
    document.getElementById('edit-attr-id').value = id;
    document.getElementById('edit-attr-name').value = name;
    
    editAttrOptions = JSON.parse(decodeURIComponent(optionsJsonStr)) || [];
    renderAttrOptions('edit-opt-container', editAttrOptions, true);
    
    document.getElementById('edit-opt-label').value = ''; 
    document.getElementById('edit-opt-code').value = ''; 
    document.getElementById('edit-attribute-modal').style.display = 'flex';
};



if (btnAddEditOpt && inputEditLabel) {
    const addEditOption = () => {
        const labelVal = inputEditLabel.value.trim();
        const codeVal = inputEditCode.value.trim().toUpperCase();
        
        if (labelVal) {
            const exists = editAttrOptions.some(opt => (typeof opt === 'object' ? opt.label : opt) === labelVal);
            if (!exists) {
                editAttrOptions.push({ label: labelVal, code: codeVal });
                renderAttrOptions('edit-opt-container', editAttrOptions, true);
                inputEditLabel.value = '';
                inputEditCode.value = '';
                inputEditLabel.focus();
            } else {
                showToast("Bu seçenek zaten eklendi", "err");
            }
        }
    };

    btnAddEditOpt.addEventListener('click', addEditOption);
    inputEditLabel.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addEditOption(); }});
    if(inputEditCode) inputEditCode.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); addEditOption(); }});
}


    // Inputlara yazı yazıldıkça hesapla
    if (inputDiscount) inputDiscount.addEventListener("input", updateCartTotal);
    if (inputVat) inputVat.addEventListener("input", updateCartTotal);


  if (formSepetSatis) {
    formSepetSatis.addEventListener("submit", (e) => {
      e.preventDefault();
    });
  }

    const productImageInput = document.getElementById('productImage');
    const uploadLabel = document.querySelector('.upload-label');
    const uploadZone = document.querySelector('.image-upload-zone');

    if (productImageInput && uploadLabel && uploadZone) {
        productImageInput.addEventListener('change', function(event) {
            const file = event.target.files[0];
            
            // Eğer dosya varsa
            if (file) {
                const reader = new FileReader();

                reader.onload = function(e) {
                    // Varsa eski önizlemeyi temizle
                    const existingPreview = uploadZone.querySelector('.preview-img-full');
                    if (existingPreview) existingPreview.remove();

                    // Yeni resim elementi oluştur
                    const img = document.createElement('img');
                    img.src = e.target.result;
                    img.classList.add('preview-img-full'); // CSS için sınıf
                    
                    // Resmi kutuya ekle
                    uploadZone.appendChild(img);
                    
                    // Arka plandaki "Yükle" yazısını ve ikonu gizle
                    uploadLabel.style.opacity = '0'; 
                }

                reader.readAsDataURL(file);
            } else {
                // Dosya seçimi iptal edildiyse temizle
                removePreview();
            }
        });
    }

    // Önizlemeyi Sıfırlama Yardımcı Fonksiyonu
    function removePreview() {
        const preview = document.querySelector('.preview-img-full');
        if (preview) preview.remove();
        if (uploadLabel) uploadLabel.style.opacity = '1';
        if (productImageInput) productImageInput.value = "";
    }

if (btnCreateOrder) {
    btnCreateOrder.addEventListener("click", async (e) => {
        e.preventDefault();
        e.stopPropagation();

        // 1. İşlem devam ediyorsa dur
        if (btnCreateOrder.disabled) return;

        const originalText = btnCreateOrder.innerHTML;

        // 2. Sepet boş mu kontrol et
        if (shoppingCart.length === 0) {
            return showToast("Sepet boş! Lütfen ürün ekleyin.", "err");
        }

        // 2.5
        
        const customerSelect = document.getElementById("customers");
        if (!customerSelect.value) {
            btnCreateOrder.disabled = false;
            btnCreateOrder.innerHTML = originalText;
            return showToast("Lütfen müşteri seçin.", "err");
        }

        // 3. UI Kilitleme (Çift tıklamayı önle)
        btnCreateOrder.disabled = true;
        btnCreateOrder.innerHTML = '<i class="ri-loader-4-line ri-spin"></i> İşleniyor...';

        try {
            // --- HESAPLAMA VE VERİ HAZIRLAMA ---

            // A. Ara Toplamı Bul
            let subTotal = 0;
            shoppingCart.forEach(item => {
                subTotal += (item.price * item.quantity);
            });

            // B. İndirim Değerini Al (Sadece tikliyse)
            const toggleDisc = document.getElementById("toggle-discount");
            const inputDisc = document.getElementById("global-discount-input");
            let discountVal = 0;
            if (toggleDisc && toggleDisc.checked && inputDisc) {
                discountVal = parseFloat(inputDisc.value) || 0;
            }

            // C. KDV Oranını Al (Sadece tikliyse)
            const toggleVat = document.getElementById("toggle-vat");
            const inputVat = document.getElementById("vat-percentage-input");
            let vatRate = 0;
            if (toggleVat && toggleVat.checked && inputVat) {
                vatRate = parseFloat(inputVat.value) || 0;
            }

            // D. Oranları Belirle
            // İndirim Oranı (Tutar üzerinden)
            const discountRatio = (subTotal > 0) ? (discountVal / subTotal) : 0;
            // KDV Çarpanı (Örn: %20 ise 1.20)
            const vatMultiplier = 1 + (vatRate / 100);

            // E. Backend'e gidecek sepeti hazırla (Orijinalini bozmamak için kopya alıyoruz)
            const cartToSend = JSON.parse(JSON.stringify(shoppingCart));

            cartToSend.forEach(item => {
                // 1. Önce bu ürüne düşen indirimi hesapla
                const itemTotalOriginal = item.price * item.quantity;
                const itemDiscount = itemTotalOriginal * discountRatio;

                // 2. KDV'yi Fiyata ve İndirime Yedir
                // Backend Formülü: (Fiyat * Adet) - İndirim
                // Bizim Hedefimiz: ((Fiyat * Adet) - İndirim) * (1 + KDV)
                // Matematiksel olarak: (Fiyat * (1+KDV)) - (İndirim * (1+KDV))
                
                item.price = item.price * vatMultiplier;      // Fiyatı KDV kadar şişir
                item.discount = itemDiscount * vatMultiplier; // İndirimi de KDV kadar şişir ki oran bozulmasın
            });

            // F. Notu Hazırla
            let noteVal = document.getElementById("satis-not").value || "";
            if (vatRate > 0) {
                noteVal += ` (KDV: %${vatRate} Dahil)`;
            }

            // G. Veri Paketini Oluştur
            const data = {
                items: cartToSend,
                customerName: document.getElementById("customers").value,
                notes: noteVal,
                is_debt: document.getElementById("add-as-debt-checkbox")?.checked || false,
                amount_paid: parseFloat(document.getElementById("amount-paid")?.value) || 0.0
            };

            // --- SERVER İSTEĞİ ---
            const res = await fetch("/create_order", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(data)
            });
            const result = await res.json();

            if (res.ok) {
                showToast(result.message, "ok");
            
                if (result.order_id) {
                    window.open(`/generate_order_pdf/${result.order_id}`, '_blank');
                }

                // Formu ve Sepeti Temizle
                document.getElementById("form-sepet-satis").reset();
                shoppingCart = []; // Sepeti boşalt
                renderCart(); // Görünümü güncelle (Boş sepet yazısı çıkar)
                
                // KDV ve İndirim alanlarını gizle/sıfırla
                if (toggleDisc) { toggleDisc.checked = false; toggleDisc.dispatchEvent(new Event('change')); }
                if (toggleVat) { toggleVat.checked = false; toggleVat.dispatchEvent(new Event('change')); }

                // Sipariş listesi sekmesi açıksa yenile
                if(typeof loadPendingOrders === 'function') {
                    loadPendingOrders();
                }
            } else {
                showToast(result.message, "err");
            }

        } catch (err) {
            console.error("Sipariş hatası:", err);
            showToast("Sunucu ile iletişim kurulamadı!", "err");
        } finally {
            // Butonu tekrar aktif et
            btnCreateOrder.disabled = false;
            btnCreateOrder.innerHTML = originalText;
        }
    });
  }
    
  if (addAsDebtCheckbox && paymentContainer) {
    
    // 1. Checkbox değiştiğinde (Tıklandığında)
    addAsDebtCheckbox.addEventListener("change", () => {
        if (addAsDebtCheckbox.checked) {
            // DÜZELTME: style.display yerine classList kullanıyoruz
            paymentContainer.classList.remove('hidden'); 
            
            // Nakit girişini sıfırla ve odaklan
            if(amountPaidInput) {
                amountPaidInput.value = "";
                amountPaidInput.focus(); // Kullanıcı kolaylığı için
            }
            // Hesaplamayı güncelle
            updateDebtCalculation();
        } else {
            // Kutucuk kaldırıldıysa gizle
            paymentContainer.classList.add('hidden');
            
            // Değerleri temizle
            if(amountPaidInput) amountPaidInput.value = "";
            if(debtCalculationDisplay) debtCalculationDisplay.textContent = "";
        }
    });

    // 2. Nakit tutar girildiğinde hesaplama yap
    if (amountPaidInput) {
        amountPaidInput.addEventListener("input", updateDebtCalculation);
    }
  }

  saleTypeRadios.forEach(radio => {
    radio.addEventListener('change', (e) => {
        isSingleSaleMode = (e.target.value === 'single');
        
        // Yeni eklediğimiz kutucuğun kapsayıcısı
        const manualSeriesWrapper = document.getElementById("manual-series-wrapper");

        if (isSingleSaleMode) {
              // --- TEKLİ SATIŞ MODU ---
              
              // 1. Seri adedi kutusunu GİZLE (Tekli satışta işe yaramaz)
              if (manualSeriesWrapper) manualSeriesWrapper.classList.add('hidden');

              // 2. Tekli satış alanını GÖSTER
              if (singleSaleGroup) singleSaleGroup.classList.remove('hidden');
              
              // 3. Eski seriesInputGroup varsa GİZLE (Sildiyseniz bu satıra gerek yok)
              if (seriesInputGroup) seriesInputGroup.classList.add('hidden');
              
              // Ürün seçiliyse inputları oluştur
              const code = satisKodInput.value;
              if (code && productList[code]) {
                  renderSingleSaleInputs(productList[code]);
              }
          } else {
              // --- SERİ SATIŞ MODU ---

              // 1. Seri adedi kutusunu GÖSTER
              if (manualSeriesWrapper) manualSeriesWrapper.classList.remove('hidden');

              // 2. Tekli satış alanını GİZLE
              if (singleSaleGroup) singleSaleGroup.classList.add('hidden');
              
              // 3. Eski seriesInputGroup varsa GÖSTER
              if (seriesInputGroup) seriesInputGroup.classList.remove('hidden');
          }
          // Fiyatı güncelle
          calculateSingleSalePrice();
      });
  });

  // --- YENİ: Tekli Satış İçin Beden Inputlarını Oluşturma ---
  function renderSingleSaleInputs(product) {
    singleSaleSizesContainer.innerHTML = "";
    unitPriceDisplay.textContent = "";

    // Yardımcı fonksiyon: Input oluşturucu
    const createInputWrapper = (label, stock, dataSize) => {
        const wrap = document.createElement("div");
        wrap.className = "single-size-input-wrapper";
        wrap.innerHTML = `
            <span class="single-size-label">${label}</span>
            <span class="single-size-stock">Stok: ${stock}</span>
            <input type="number" min="0" max="${stock}" class="single-size-val" data-size="${dataSize}" placeholder="Adet">
        `;
        
        // Fiyat hesaplama için dinleyici
        const input = wrap.querySelector('input');
        input.addEventListener('input', calculateSingleSalePrice);

        return wrap;
    };

    // 1. INPUTLARI OLUŞTUR
    // A) Bedensiz Ürün
    if (!product.sizes || product.sizes.length === 0) {
        singleSaleSizesContainer.innerHTML = "<p class='placeholder-text' style='margin-bottom:5px;'>Bedensiz Ürün</p>";
        const wrap = createInputWrapper("Adet", product.stock, "STD");
        singleSaleSizesContainer.appendChild(wrap);
    } 
    // B) Bedenli Ürün
    else {
        product.sizes.forEach(size => {
            const wrap = createInputWrapper(size.value, size.stock, size.value);
            singleSaleSizesContainer.appendChild(wrap);
        });
    }

const filterAttrValueInput = document.getElementById('filter-attr-value');
if (filterAttrValueInput) {
    // Kullanıcı kutuya her yazı yazdığında filtreleme çalışsın
    filterAttrValueInput.addEventListener('input', () => {
        applyFiltersAndSorting();
    });
}
    
    // 2. ÖZEL EKLEME BUTONU OLUŞTUR
    const actionBtn = document.createElement("button");
    actionBtn.type = "button"; 
    actionBtn.className = "btn-primary"; 
    
    // --- STİL AYARLARI (Genişlik Sorunu İçin) ---
    actionBtn.style.marginTop = "20px";
    actionBtn.style.width = "100%";       // Kapsayıcı ne kadarsa o kadar genişle
    actionBtn.style.gridColumn = "1 / -1"; // Eğer Grid kullanılıyorsa tüm kolonlara yayıl
    actionBtn.style.flexBasis = "100%";    // Eğer Flex kullanılıyorsa yeni satıra geç
    actionBtn.style.padding = "12px";      // Biraz daha dolgun görünsün
    actionBtn.style.fontSize = "1.1rem";   // Yazı biraz daha büyük olsun
    // ---------------------------------------------

    actionBtn.innerHTML = '<i class="ri-shopping-basket-2-fill"></i> Parçaları Sepete Ekle';
    
    actionBtn.addEventListener("click", () => {
        addToCartAction();
    });

    singleSaleSizesContainer.appendChild(actionBtn);

    // 3. İLK INPUTA ODAKLAN
    setTimeout(() => {
        const firstInput = singleSaleSizesContainer.querySelector('input');
        if (firstInput) firstInput.focus();
    }, 100);

    calculateSingleSalePrice();
  }

  // --- YENİ: Tekli Satış Fiyat Hesaplama ---
  function calculateSingleSalePrice() {
      if (!isSingleSaleMode) return;

      const code = satisKodInput.value;
      const product = productList[code];
      if (!product) return;

      const seriesPrice = parseFloat(selectedPriceInput.value) || 0;
      
      // Serideki toplam parça sayısını bul
      let totalItemsInSeries = 0;
      if (product.sizes) {
          totalItemsInSeries = product.sizes.reduce((acc, s) => acc + (s.number || 0), 0);
      }
      if (totalItemsInSeries === 0) totalItemsInSeries = 1; // 0'a bölünmeyi önle

      // Birim Fiyat (Beden başına)
      const unitPrice = seriesPrice / totalItemsInSeries;
      const currency = selectedCurrencyInput.value;

      unitPriceDisplay.textContent = `1 Adet Fiyatı: ${unitPrice.toFixed(2)} ${currency}`;
      
      // Toplamı hesaplamak için inputları topla (Bu sadece görsel bilgi içindir, asıl hesap sepete eklerken yapılır)
      // Ancak sepete ekleme mantığı kendi içinde hesapladığı için burayı sadece bilgi amaçlı bırakıyoruz.
  }

  function showToast(msg, type = "ok") {
    const toast = document.querySelector("#toast");
    if (!toast) return;
    toast.textContent = msg;
    toast.style.background = type === "err" ? "#EF4444" : "#10B981";
    toast.style.display = "block";
    clearTimeout(toast._t);
    toast._t = setTimeout(() => (toast.style.display = "none"), 3000);
  }

  // ============================================================
  // TABLO RENDER FONKSİYONU (ÇOKLU RAF DESTEKLİ)
  // ============================================================
  function renderTable(productsToDisplay) {
    stokTbody.innerHTML = "";
    
    if (!productsToDisplay || productsToDisplay.length === 0) {
        stokTbody.innerHTML = `<tr><td colspan="5" style="text-align:center; padding: 40px; color:#9CA3AF;">Listelenecek ürün yok.</td></tr>`;
        return;
    }

    // 1. Gruplama Mantığı (İlk 6 karaktere göre)
    const groups = new Map();

    productsToDisplay.forEach(p => {
        // Artık ilk 6 harfe bakmıyoruz. Veritabanındaki gerçek 'stock_id'yi kullanıyoruz.
        // Eğer çok eskiden eklenmiş ve stock_id'si olmayan bir ürünse fallback yapıyoruz.
        const baseCode = p.stock_id ? p.stock_id : (p.code.length >= 6 ? p.code.substring(0, 6) : p.code);
        
        if (!groups.has(baseCode)) {
            groups.set(baseCode, {
                baseCode: baseCode,
                name: p.name,
                category: p.category,
                totalStock: 0,
                variants: []
            });
        }
        
        const group = groups.get(baseCode);
        group.totalStock += (p.stock || 0);
        group.variants.push(p);
    });

    // 2. Ekrana Basma
    groups.forEach(group => {
        
        // --- SENARYO A: TEK ÜRÜN (Varyasyon Yok) ---
        if (group.variants.length === 1) {
            const p = group.variants[0];

            let shelfHtml = '<span style="color:#9CA3AF; font-size:0.85rem; font-style:italic;">Raf Yok</span>';
            if (p.shelves && Array.isArray(p.shelves) && p.shelves.length > 0) {
                shelfHtml = p.shelves.map(s => 
                    `<span style="display:inline-block; background:#FFF7ED; color:#C2410C; border:1px solid #FFEDD5; padding:2px 6px; border-radius:4px; font-size:0.8rem; margin-right:4px; margin-bottom:2px;">
                        <b>${s.code}</b>: ${s.quantity}
                     </span>`
                ).join('');
            }
            
            let totalIndividualParts = 0;
            if (p.shelves && Array.isArray(p.shelves)) {
                totalIndividualParts = p.shelves.reduce((sum, s) => sum + (parseInt(s.quantity) || 0), 0);
            }
            if (totalIndividualParts === 0 && p.sizes && Array.isArray(p.sizes)) {
                 totalIndividualParts = p.sizes.reduce((sum, s) => sum + (parseInt(s.stock) || 0), 0);
            }

            const tr = document.createElement("tr");
            tr.className = "product-row"; // Tekil satır sınıfı
            tr.dataset.id = p.id; 
            
            tr.innerHTML = `
                <td style="padding-left:24px;">
                    <div class="cell-product">
                        <span class="name">${p.name}</span>
                        <span class="code">#${p.code}</span>
                    </div>
                </td>
                <td>
                    <div style="display:flex; flex-direction:column; gap:4px;">
                        <span class="badge badge-gray" style="width:fit-content;">${p.category || '-'}</span>
                        <div style="display:flex; flex-wrap:wrap;">
                            ${shelfHtml}
                        </div>
                    </div>
                </td>
                <td>
                    <span style="color:#475467;">${p.color_id || '-'}</span>
                </td>
                <td>
                    <div style="display:flex; flex-direction:column;">
                        <div>
                            <span class="stock-text" style="font-size:1.1rem; color:#1F2937;">${p.stock}</span> 
                            <span class="stock-dim" style="font-size:0.8rem;">Seri</span>
                        </div>
                        <div style="font-size:0.8rem; color:#3B82F6; font-weight:600; margin-top:2px;">
                            (Top. ${totalIndividualParts} Parça)
                        </div>
                    </div>
                </td>
                <td style="padding-right:24px; text-align:right;">
                    <button class="btn-icon delete btn-delete" title="Sil" data-id="${p.id}">
                        <i class="ri-delete-bin-line"></i>
                    </button>
                </td>
            `;
            stokTbody.appendChild(tr);
        }
        
        // --- SENARYO B: GRUP (AKORDEON) ---
        else {
            const parentRow = document.createElement("tr");
            parentRow.className = "group-row"; // Grup başlık sınıfı
            // Tıklayınca aç/kapa yapması için fonksiyonu çağırıyoruz
            parentRow.onclick = function() { toggleStockGroup(this); };

            parentRow.innerHTML = `
                <td style="padding-left:24px;">
                    <div class="cell-product" style="flex-direction:row; align-items:center;">
                        <i class="ri-arrow-right-s-line chevron-icon" style="transition: transform 0.2s;"></i>
                        <div style="margin-left:8px;">
                            <span class="name">${group.name}</span>
                            <span class="code" style="color:#175CD3;">#${group.baseCode}*** • ${group.variants.length} Varyasyon</span>
                        </div>
                    </div>
                </td>
                <td><span class="badge badge-blue">Çoklu Varyasyon</span></td>
                <td><span style="font-size:0.85rem; color:#98A2B3; font-style:italic;">Detayda</span></td>
                <td><span class="stock-text">${group.totalStock}</span> <span class="stock-dim">Toplam</span></td>
                <td></td>
            `;
            stokTbody.appendChild(parentRow);

            // Detay Satırı (Başlangıçta Gizli)
            const detailRow = document.createElement("tr");
            detailRow.className = "detail-row";
            detailRow.style.display = "none"; // GİZLİ BAŞLA
            
            detailRow.innerHTML = `
                <td colspan="5" style="padding:0; background-color:#FAFAFA;">
                    <div class="inner-list" style="padding: 10px 40px;">
                        ${group.variants.map(p => {
                            let innerShelfHtml = '<span style="color:#ccc;">-</span>';
                            if (p.shelves && p.shelves.length > 0) {
                                innerShelfHtml = p.shelves.map(s => 
                                    `<span style="color:#C2410C; font-weight:600; font-size:0.85rem;">${s.code}: ${s.quantity}</span>`
                                ).join('<span style="color:#ccc; margin:0 5px;">|</span>');
                            }

                            return `
                            <div class="inner-item product-row-inner" data-id="${p.id}" style="display:grid; grid-template-columns: 1.5fr 1fr 1fr 1fr 0.5fr; align-items: center; padding: 12px 0; border-bottom: 1px dashed #E4E7EC; cursor:pointer;">
                                <span class="inner-code" style="font-family:monospace; font-weight:600; color:#344054;">#${p.code}</span>
                                <span class="badge badge-gray" style="width:fit-content;">${p.category}</span>
                                <span>Renk: <strong>${p.color_id}</strong></span>
                                <span>${innerShelfHtml}</span>
                                <div style="display:flex; justify-content:space-between; align-items:center;">
                                    <span>Stok: <strong>${p.stock}</strong></span>
                                    <button class="btn-icon delete btn-delete" data-id="${p.id}">
                                        <i class="ri-delete-bin-line"></i>
                                    </button>
                                </div>
                            </div>
                            `;
                        }).join('')}
                    </div>
                </td>
            `;
            stokTbody.appendChild(detailRow);
        }
    });
}

// --- TIKLAMA OLAYI YÖNETİMİ (Click Listener) ---
stokTbody.addEventListener("click", async (e) => {
    const target = e.target;
    
    // 1. SİLME BUTONU
    const deleteBtn = target.closest(".btn-delete");
    if (deleteBtn) {
        e.stopPropagation(); // Diğer olayları durdur
        const productId = parseInt(deleteBtn.dataset.id, 10);
        if (confirm("Bu ürünü silmek istediğinizden emin misiniz?")) {
            try {
                const res = await fetch(`/product/delete/${productId}`, { method: "DELETE" });
                if (!res.ok) return showToast("Ürün silinemedi!", "err");
                await loadProducts();
                showToast("Ürün başarıyla silindi.");
            } catch {
                showToast("Sunucu hatası!", "err");
            }
        }
        return; 
    }

    // 2. GRUP BAŞLIĞI MI? (Eğer öyleyse yönlendirme yapma, return et)
    // Grup başlığındaki açma kapama işlemi "onclick" attribute'u ile handlediliyor.
    if (target.closest(".group-row")) {
        return; 
    }

    // 3. ÜRÜN DETAYINA YÖNLENDİRME
    // Tekli satır (.product-row) veya grup içindeki satır (.product-row-inner)
    const row = target.closest(".product-row") || target.closest(".product-row-inner");
    
    if (row) {
        const productId = row.dataset.id;
        if (productId) {
            window.location.href = `/product_detail/${productId}`; 
        }
    }
});

async function loadCustomAttributes() {
    const res = await fetch('/get_attribute_defs');
    const data = await res.json();
    const container = document.getElementById('custom-attributes-container');
    
    data.attributes.forEach(attr => {
        const html = `
            <div class="form-group">
                <label>${attr.name}</label>
                <input type="text" name="custom_${attr.name}" class="custom-attr-input" data-key="${attr.name}">
            </div>
        `;
        container.insertAdjacentHTML('beforeend', html);
    });
}


// --- GRUP AÇ/KAPA FONKSİYONU ---
// Bu fonksiyon window objesine atanmalı ki HTML'deki onclick erişebilsin
window.toggleStockGroup = function(row) {
    const nextRow = row.nextElementSibling; // Hemen altındaki tr (detay satırı)
    const icon = row.querySelector('.chevron-icon');

    if (nextRow && nextRow.classList.contains('detail-row')) {
        if (nextRow.style.display === 'none') {
            // Aç
            nextRow.style.display = 'table-row';
            row.style.backgroundColor = '#EFF6FF'; // Açıkken renk ver
            if(icon) icon.style.transform = 'rotate(90deg)';
        } else {
            // Kapat
            nextRow.style.display = 'none';
            row.style.backgroundColor = ''; // Rengi sıfırla
            if(icon) icon.style.transform = 'rotate(0deg)';
        }
    }
};
  
  function applyFiltersAndSorting() {
    let filteredProducts = [...allProducts];
    
    // A) Genel Arama
    if (searchInput) {
        const searchTerm = searchInput.value.toLowerCase().trim();
        if (searchTerm) {
            filteredProducts = filteredProducts.filter(p =>
                p.name.toLowerCase().includes(searchTerm) ||
                p.code.toLowerCase().includes(searchTerm)
            );
        }
    }

    // B) Seçili Özelliğe Göre Filtrele
    const attrSelect = document.getElementById('filter-attr-select');
    const attrInput = document.getElementById('filter-attr-value');

    if (attrSelect && attrInput && attrSelect.value && attrInput.value) {
        const key = attrSelect.value;
        const val = attrInput.value.toLowerCase().trim();

        filteredProducts = filteredProducts.filter(p => {
            if (!p.custom_attributes) return false;
            const productAttrVal = p.custom_attributes[key];
            return productAttrVal && String(productAttrVal).toLowerCase().includes(val);
        });
    }

    // -------------------------------------------

    // C) Sıralama
    if (sortSelect) {
        const sortValue = sortSelect.value;
        switch (sortValue) {
          case "name-asc": filteredProducts.sort((a, b) => a.name.localeCompare(b.name)); break;
          case "name-desc": filteredProducts.sort((a, b) => b.name.localeCompare(a.name)); break;
          case "stock-asc": filteredProducts.sort((a, b) => a.stock - b.stock); break;
          case "stock-desc": filteredProducts.sort((a, b) => b.stock - a.stock); break;
        }
    }
    
    renderTable(filteredProducts);
}
// ============================================================
  // SİPARİŞ TOPLAMA & DOĞRULAMA SİSTEMİ (WMS MANTIĞI)
  // ============================================================
  
  let currentPickingOrderId = null;
  let pickingDataList = []; // Çekilen listeyi burada tutacağız
  let verifiedItemsCount = 0;

  // DOM Elementleri
  const elPickShelfInp = document.getElementById("picker-shelf-input");
  const elPickProdInp = document.getElementById("picker-product-input");
  const elPickStatusMsg = document.getElementById("scan-status-msg");
  const elPickProgress = document.getElementById("picking-progress-text");
  const btnConfirmPicking = document.getElementById("btn-confirm-picking");

  // 1. Modalı Aç ve Verileri Hazırla
  // 1. Modalı Aç ve Verileri Hazırla
  window.openPickingModal = async function(orderId) {
      console.log("Picking Modal Açılıyor ID:", orderId);
      currentPickingOrderId = orderId;
      verifiedItemsCount = 0;
      pickingDataList = []; // Listeyi sıfırla

      const modal = document.getElementById('picking-modal');
      const tbody = document.getElementById('picking-list-body');
      
      // UI Sıfırlama
      modal.style.display = 'flex';
      tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:20px;">Liste yükleniyor...</td></tr>';
      
      resetScanInputs();
      updatePickingProgress();

      try {
          const res = await fetch(`/get_picking_list/${orderId}`);
          const data = await res.json();

          if (data.status === 'ok') {
              let localIdCounter = 0;

              // YENİ MANTIK: Ürünleri parçalamıyoruz (Patlatmıyoruz). Miktarıyla birlikte tek satır kalıyor.
              data.picking_list.forEach(item => {
                  pickingDataList.push({
                      ...item,
                      verified: false,
                      localId: localIdCounter++ 
                  });
              });

              renderPickingTable();
              setTimeout(() => elPickShelfInp.focus(), 500); // Otomatik odaklan
          } else {
              showToast(data.message, "err");
              modal.style.display = 'none';
          }
      } catch (e) {
          console.error(e);
          tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; color:red;">Bağlantı hatası.</td></tr>';
      }
  };

  // 2. Tabloyu Çiz (Hafızadaki veriye göre)
  function renderPickingTable() {
      const tbody = document.getElementById('picking-list-body');
      tbody.innerHTML = "";

      if (pickingDataList.length === 0) {
          tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;">Toplanacak ürün yok.</td></tr>';
          btnConfirmPicking.disabled = false;
          btnConfirmPicking.style.opacity = "1";
          btnConfirmPicking.style.cursor = "pointer";
          return;
      }

      pickingDataList.forEach(item => {
          let rowClass = "";
          let iconHtml = '<i class="ri-checkbox-blank-circle-line" style="color:#ccc; font-size:1.2rem;"></i>';

          if (item.status === 'missing') {
              rowClass = "background-color: #FEF2F2;"; // Kırmızımsı (Stok yok)
              iconHtml = '<i class="ri-error-warning-fill" style="color:#EF4444;"></i>';
          } else if (item.verified) {
              rowClass = "row-verified"; // Yeşil
              iconHtml = '<i class="ri-checkbox-circle-fill" style="color:#166534; font-size:1.2rem;"></i>';
          }

          // HTML: Miktar sütunu eklendi
          const html = `
              <tr class="${rowClass}" id="pick-row-${item.localId}">
                  <td style="text-align:center;">${iconHtml}</td>
                  <td>
                      <span style="background:#374151; color:#fff; padding:4px 8px; border-radius:4px; font-family:monospace; font-weight:bold;">
                          ${item.shelf}
                      </span>
                  </td>
                  <td>
                      <div style="font-weight:600;">${item.product_name}</div>
                      <div style="font-size:0.8rem; color:#6B7280; font-family:monospace;">${item.product_code}</div>
                  </td>
                  <td>${item.color}</td>
                  <td style="text-align:center; font-weight:700;">${item.size}</td>
                  <td style="text-align:center; font-weight:900; font-size:1.1rem; color:#3B82F6;">${item.quantity} Adet</td>
              </tr>
          `;
          tbody.insertAdjacentHTML('beforeend', html);
      });

      updatePickingProgress();
  }

  // 3. Input Olay Dinleyicileri (RAF -> ÜRÜN -> DOĞRULA)
  
  // A. RAF KODU GİRİLİRKEN
  if(elPickShelfInp) {
      elPickShelfInp.addEventListener("input", (e) => {
          const val = elPickShelfInp.value.trim().toUpperCase(); 
          if (!val) return;

          const hasItemsInShelf = pickingDataList.some(item => 
              item.shelf.toUpperCase() === val && !item.verified && item.status !== 'missing'
          );

          if (hasItemsInShelf) {
              elPickShelfInp.style.borderColor = "#10B981";
              elPickShelfInp.style.backgroundColor = "#F0FDF4";
              
              elPickProdInp.disabled = false;
              elPickProdInp.value = ""; 
              elPickProdInp.focus();    
              
              setStatusMsg(`Raf (${val}) doğrulandı. Şimdi ürünü okutun.`, "success");
          } else {
              elPickShelfInp.style.borderColor = "#D1D5DB";
              elPickShelfInp.style.backgroundColor = "#fff";
              setStatusMsg("Raf aranıyor...", "normal");
          }
      });
  }

  // B. ÜRÜN KODU GİRİLİRKEN
  if(elPickProdInp) {
      elPickProdInp.addEventListener("input", (e) => {
          const shelfVal = elPickShelfInp.value.trim().toUpperCase();
          const prodVal = elPickProdInp.value.trim(); 

          if (!prodVal) return;

          const targetItem = pickingDataList.find(item => 
              item.shelf.toUpperCase() === shelfVal && 
              item.product_code === prodVal && 
              !item.verified &&
              item.status !== 'missing'
          );

          if (targetItem) {
              // --- ÜRÜN EŞLEŞTİ: TEK OKUTMADA TÜM MİKTAR ONAYLANIR ---
              targetItem.verified = true; 
              
              renderPickingTable(); 
              
              elPickProdInp.value = "";
              elPickProdInp.disabled = true;
              
              elPickShelfInp.value = "";
              elPickShelfInp.style.borderColor = "#D1D5DB"; 
              elPickShelfInp.style.backgroundColor = "#fff";
              elPickShelfInp.focus(); 
              
              setStatusMsg(`SÜPER: ${targetItem.product_name} (${targetItem.quantity} Adet) toplandı. Sıradaki rafı girin.`, "success");
              
              checkCompletion();
          }
      });
  }

  function resetScanInputs() {
      if(elPickShelfInp) {
          elPickShelfInp.value = "";
          elPickShelfInp.style.borderColor = "#D1D5DB";
          elPickShelfInp.style.backgroundColor = "#fff";
          elPickShelfInp.focus();
      }
      if(elPickProdInp) {
          elPickProdInp.value = "";
          elPickProdInp.disabled = true;
      }
      setStatusMsg("Başlamak için raf kodunu yazın veya okutun.", "normal");
  }

  function setStatusMsg(msg, type) {
      if(!elPickStatusMsg) return;
      elPickStatusMsg.textContent = msg;
      if (type === "error") elPickStatusMsg.style.color = "#EF4444";
      else if (type === "success") elPickStatusMsg.style.color = "#10B981";
      else elPickStatusMsg.style.color = "#6B7280";
  }

  // İlerleme Çubuğunu/Yazısını Güncelle (Artık Adet Üzerinden Hesaplanıyor)
  function updatePickingProgress() {
      const validItems = pickingDataList.filter(i => i.status !== 'missing');
      
      // Toplam toplanması gereken ADET sayısı
      const totalQty = validItems.reduce((sum, i) => sum + i.quantity, 0);
      
      // Toplanan (doğrulanan) ADET sayısı
      const doneQty = validItems.filter(i => i.verified).reduce((sum, i) => sum + i.quantity, 0);

      verifiedItemsCount = doneQty; // Global değişkeni de güncelleyelim

      if(elPickProgress) elPickProgress.textContent = `${doneQty} / ${totalQty} Adet Toplandı`;

      if (totalQty > 0 && doneQty === totalQty) {
          btnConfirmPicking.disabled = false;
          btnConfirmPicking.style.opacity = "1";
          btnConfirmPicking.style.cursor = "pointer";
          btnConfirmPicking.innerHTML = '<i class="ri-check-double-line"></i> Siparişi Tamamla';
          btnConfirmPicking.classList.add("animate-pulse");
      } else {
          btnConfirmPicking.disabled = true;
          btnConfirmPicking.style.opacity = "0.5";
          btnConfirmPicking.style.cursor = "not-allowed";
      }
  }

  function checkCompletion() {
      const validItems = pickingDataList.filter(i => i.status !== 'missing');
      const totalQty = validItems.reduce((sum, i) => sum + i.quantity, 0);
      const doneQty = validItems.filter(i => i.verified).reduce((sum, i) => sum + i.quantity, 0);
      
      if (totalQty > 0 && doneQty === totalQty) {
          setStatusMsg("TÜM ÜRÜNLER TOPLANDI! Onaylayabilirsiniz.", "success");
      }
  }

  // Yardımcı: Modal ilk açıldığında veya resetlendiğinde
  function resetScanInputs() {
      if(elPickShelfInp) {
          elPickShelfInp.value = "";
          elPickShelfInp.style.borderColor = "#D1D5DB";
          elPickShelfInp.style.backgroundColor = "#fff";
          elPickShelfInp.focus();
      }
      if(elPickProdInp) {
          elPickProdInp.value = "";
          elPickProdInp.disabled = true;
      }
      setStatusMsg("Başlamak için raf kodunu yazın veya okutun.", "normal");
  }

  function setStatusMsg(msg, type) {
      if(!elPickStatusMsg) return;
      elPickStatusMsg.textContent = msg;
      if (type === "error") elPickStatusMsg.style.color = "#EF4444";
      else if (type === "success") elPickStatusMsg.style.color = "#10B981";
      else elPickStatusMsg.style.color = "#6B7280";
  }

  function shakeInput(element) {
      element.classList.add("input-error");
      setTimeout(() => element.classList.remove("input-error"), 300);
  }

  function playErrorSound() {
      // Basit bir beep sesi (tarayıcı izin verirse)
      // const audio = new Audio('https://actions.google.com/sounds/v1/alarms/beep_short.ogg');
      // audio.volume = 0.5;
      // audio.play().catch(e => {});
  }

  function updatePickingProgress() {
      const total = pickingDataList.filter(i => i.status !== 'missing').length;
      const done = pickingDataList.filter(i => i.verified).length;
      verifiedItemsCount = done;

      if(elPickProgress) elPickProgress.textContent = `${done} / ${total} Ürün Toplandı`;

      // Hepsi bittiyse butonu aç
      if (total > 0 && done === total) {
          btnConfirmPicking.disabled = false;
          btnConfirmPicking.style.opacity = "1";
          btnConfirmPicking.style.cursor = "pointer";
          btnConfirmPicking.innerHTML = '<i class="ri-check-double-line"></i> Siparişi Tamamla';
          btnConfirmPicking.classList.add("animate-pulse"); // Dikkat çeksin
      } else {
          btnConfirmPicking.disabled = true;
          btnConfirmPicking.style.opacity = "0.5";
          btnConfirmPicking.style.cursor = "not-allowed";
      }
  }

  function checkCompletion() {
      const total = pickingDataList.filter(i => i.status !== 'missing').length;
      const done = pickingDataList.filter(i => i.verified).length;
      
      if (total > 0 && done === total) {
          setStatusMsg("TÜM ÜRÜNLER TOPLANDI! Onaylayabilirsiniz.", "success");
      }
  }

  // 4. Onay Butonu Mantığı
  if (btnConfirmPicking) {
      btnConfirmPicking.addEventListener("click", async () => {
          if (!currentPickingOrderId) return;
          if (btnConfirmPicking.disabled) return;

          if (!confirm("Tüm ürünler doğrulandı. Stok düşülecek ve sipariş onaylanacak. Emin misiniz?")) return;

          btnConfirmPicking.innerHTML = "İşleniyor...";
          await manageOrder(currentPickingOrderId, 'approve');
          
          document.getElementById('picking-modal').style.display = 'none';
      });
  }

  // --- YENİ BİRLEŞTİRİLMİŞ SİPARİŞ YÖNETİMİ ---

  let currentOrderPage = 1;
    
    // Element Seçimleri
    const filterStatus = document.getElementById("filter-order-status");
    const filterCustomer = document.getElementById("filter-order-customer");
    const filterDate = document.getElementById("filter-order-date");
    const btnRefreshOrders = document.getElementById("btn-refresh-orders");
    const btnPrevPage = document.getElementById("btn-prev-page");
    const btnNextPage = document.getElementById("btn-next-page");
    const pageInfo = document.getElementById("page-info");
    const tbodyUnified = document.getElementById("unified-orders-body");

    // 1. Filtreleri Doldur (Müşteriler)
    async function loadOrderFilters() {
      if (!filterCustomer) return;
      try {
          const res = await fetch("/get_all_customers");
          const data = await res.json();
          if (data.success) {
              // Mevcut 'Tüm Müşteriler' seçeneğini koruyarak diğerlerini temizle
              // (Sadece ilk option kalacak şekilde resetleme)
              filterCustomer.innerHTML = '<option value="ALL">Tüm Müşteriler</option>';
              
              data.customers.forEach(c => {
                  const opt = document.createElement("option");
                  opt.value = c.name;
                  opt.textContent = c.name;
                  filterCustomer.appendChild(opt);
              });
          }
      } catch (err) {
          console.error("Müşteri filtresi yüklenemedi", err);
      }
  }

  // 2. Ana Yükleme Fonksiyonu (Unified)
  window.loadUnifiedOrders = async function(page = 1) {
      if (!tbodyUnified) return;
      
      currentOrderPage = page;
      tbodyUnified.innerHTML = '<tr><td colspan="8" style="text-align:center; padding:20px;"><i class="ri-loader-4-line ri-spin"></i> Yükleniyor...</td></tr>';

      // Filtre Değerlerini Al
      const status = filterStatus ? filterStatus.value : 'ALL';
      const customer = filterCustomer ? filterCustomer.value : 'ALL';
      const dateRange = filterDate ? filterDate.value : 'ALL';

      // URL Oluştur
      const url = `/get_orders_unified?page=${page}&status=${status}&customer=${encodeURIComponent(customer)}&date=${dateRange}`;

      try {
          const res = await fetch(url);
          const data = await res.json();

          tbodyUnified.innerHTML = "";

          if (!data.orders || data.orders.length === 0) {
              tbodyUnified.innerHTML = '<tr><td colspan="8" style="text-align:center; padding:20px; color:#9CA3AF;">Kriterlere uygun sipariş bulunamadı.</td></tr>';
              if(pageInfo) pageInfo.textContent = "Sayfa 0 / 0";
              if(btnPrevPage) btnPrevPage.disabled = true;
              if(btnNextPage) btnNextPage.disabled = true;
              return;
          }

          // Global listeyi güncelle (Detay modalı için gerekli)
          data.orders.forEach(o => { globalOrderList[o.id] = o; });

          data.orders.forEach(order => {
              // Badge Rengi ve Metni
              let statusBadge = "";
              let actionsHtml = "";

              // PDF İndirme Butonu (Ortak)
              // Tüm durumlarda PDF (LOD benzeri fiş) alınabilmesi için bu butonu hazırlıyoruz
              const pdfButton = `
                  <button class="btn-secondary" style="padding:5px 8px; color:#6366f1; border-color:#e0e7ff; background:#eef2ff;" 
                          onclick="window.open('/generate_order_pdf/${order.id}', '_blank')" title="Fiş İndir (PDF)">
                      <i class="ri-file-download-line"></i>
                  </button>
              `;

              if (order.status === 'PENDING') {
                  statusBadge = `<span style="background:#FEF3C7; color:#D97706; padding:4px 8px; border-radius:6px; font-size:0.75rem; font-weight:700; border:1px solid #FCD34D;">BEKLİYOR</span>`;
                  
                  // Bekleyen sipariş ise: Topla, Reddet, Detay, PDF
                  actionsHtml = `
                      <div style="display:flex; justify-content:flex-end; gap:5px;">
                          ${pdfButton} <!-- Yeni PDF Butonu -->
                          <button class="btn-secondary" style="background:#EFF6FF; color:#3B82F6; border:1px solid #BFDBFE; padding:5px 8px; font-size:0.8rem;" 
                                  onclick="openPickingModal(${order.id})" title="Topla ve Onayla">
                              <i class="ri-hand-coin-line"></i>
                          </button>
                          <button class="btn-delete" style="padding:5px 8px;" 
                                  onclick="manageOrder(${order.id}, 'reject')" title="Reddet">
                              <i class="ri-close-line"></i>
                          </button>
                          <button class="btn-secondary" style="padding:5px 8px;" onclick="viewOrderDetails(${order.id})" title="Detay">
                              <i class="ri-eye-line"></i>
                          </button>
                      </div>
                  `;
              } else if (order.status === 'APPROVED') {
                  statusBadge = `<span style="background:#DCFCE7; color:#166534; padding:4px 8px; border-radius:6px; font-size:0.75rem; font-weight:700; border:1px solid #BBF7D0;">ONAYLANDI</span>`;
                  
                  // Onaylı ise: PDF, Detay, Geri Al
                  actionsHtml = `
                      <div style="display:flex; justify-content:flex-end; gap:5px;">
                          ${pdfButton} <!-- Yeni PDF Butonu -->
                          <button class="btn-secondary" style="padding:5px 8px;" onclick="viewOrderDetails(${order.id})" title="Detay">
                              <i class="ri-eye-line"></i>
                          </button>
                          <button class="btn-secondary" style="background:#F3F4F6; color:#4B5563; padding:5px 8px; border:1px solid #E5E7EB;" 
                                  onclick="revertOrder(${order.id})" title="Geri Al (Bekleyene Çevir)">
                              <i class="ri-restart-line"></i>
                          </button>
                      </div>
                  `;
              } else { // REJECTED
                  statusBadge = `<span style="background:#FEE2E2; color:#991B1B; padding:4px 8px; border-radius:6px; font-size:0.75rem; font-weight:700; border:1px solid #FECACA;">REDDEDİLDİ</span>`;
                  
                  // Reddedilen ise: PDF, Detay, Geri Al
                  actionsHtml = `
                      <div style="display:flex; justify-content:flex-end; gap:5px;">
                          ${pdfButton} <!-- Yeni PDF Butonu -->
                          <button class="btn-secondary" style="padding:5px 8px;" onclick="viewOrderDetails(${order.id})" title="Detay">
                              <i class="ri-eye-line"></i>
                          </button>
                          <button class="btn-secondary" style="background:#F3F4F6; color:#4B5563; padding:5px 8px; border:1px solid #E5E7EB;" 
                                  onclick="revertOrder(${order.id})" title="Geri Al (Bekleyene Çevir)">
                              <i class="ri-restart-line"></i>
                          </button>
                      </div>
                  `;
              }

              const tr = document.createElement("tr");
              tr.id = `order-row-${order.id}`;
              tr.innerHTML = `
                  <td>${statusBadge}</td>
                  <td style="font-family:monospace; color:#6B7280;">#${order.id}</td>
                  <td>${order.creator}</td>
                  <td style="font-weight:600;">${order.customer}</td>
                  <td style="font-weight:bold; color:#3B82F6;">${order.total.toFixed(2)} ${order.currency}</td>
                  <td style="font-size:0.85rem; color:#4B5563;">${order.date}</td>
                  <td style="font-size:0.85rem; max-width:150px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${order.note || ''}">${order.note || '-'}</td>
                  <td>${actionsHtml}</td>
              `;
              tbodyUnified.appendChild(tr);
          });

          // Sayfalama Kontrollerini Güncelle
          if(pageInfo) pageInfo.textContent = `Sayfa ${data.current_page} / ${data.total_pages}`;
          if(btnPrevPage) btnPrevPage.disabled = (data.current_page <= 1);
          if(btnNextPage) btnNextPage.disabled = (data.current_page >= data.total_pages);

      } catch (err) {
          console.error("Sipariş listesi hatası:", err);
          tbodyUnified.innerHTML = '<tr><td colspan="8" style="text-align:center; color:red;">Veri alınırken hata oluştu.</td></tr>';
      }
  };

  // Event Listeners (Olay Dinleyicileri)
  if (filterStatus) filterStatus.addEventListener("change", () => loadUnifiedOrders(1));
  if (filterCustomer) filterCustomer.addEventListener("change", () => loadUnifiedOrders(1));
  if (filterDate) filterDate.addEventListener("change", () => loadUnifiedOrders(1));
  if (btnRefreshOrders) btnRefreshOrders.addEventListener("click", () => loadUnifiedOrders(1));

  if (btnPrevPage) btnPrevPage.addEventListener("click", () => loadUnifiedOrders(currentOrderPage - 1));
  if (btnNextPage) btnNextPage.addEventListener("click", () => loadUnifiedOrders(currentOrderPage + 1));

  // Tab'a tıklandığında yükle (HATA DÜZELTİLDİ: loadPendingOrders yerine yeni fonksiyonlar)
  document.querySelectorAll(".tab-link").forEach(tab => {
    tab.addEventListener("click", () => {
      if (tab.dataset.tab === "gelen-siparisler") {
        loadOrderFilters(); 
        loadUnifiedOrders(1); 
      }
    });
  });

  // İlk yüklemede de çalıştır (HATA DÜZELTİLDİ)
  if (window.location.hash === "#gelen-siparisler") {
    loadOrderFilters();
    loadUnifiedOrders(1);
  }

  // --- Global Fonksiyon Güncellemeleri ---
if (btnCreateOrder) {
    btnCreateOrder.addEventListener("click", async (e) => {
        e.preventDefault();
        e.stopPropagation();

        // 1. İşlem devam ediyorsa dur
        if (btnCreateOrder.disabled) return;

        const originalText = btnCreateOrder.innerHTML;

        // 2. Sepet boş mu kontrol et
        if (shoppingCart.length === 0) {
            return showToast("Sepet boş! Lütfen ürün ekleyin.", "err");
        }

        // 2.5
        
        const customerSelect = document.getElementById("customers");
        if (!customerSelect.value) {
            btnCreateOrder.disabled = false;
            btnCreateOrder.innerHTML = originalText;
            return showToast("Lütfen müşteri seçin.", "err");
        }

        // 3. UI Kilitleme (Çift tıklamayı önle)
        btnCreateOrder.disabled = true;
        btnCreateOrder.innerHTML = '<i class="ri-loader-4-line ri-spin"></i> İşleniyor...';

        try {
            // --- HESAPLAMA VE VERİ HAZIRLAMA ---

            // A. Ara Toplamı Bul
            let subTotal = 0;
            shoppingCart.forEach(item => {
                subTotal += (item.price * item.quantity);
            });

            // B. İndirim Değerini Al (Sadece tikliyse)
            const toggleDisc = document.getElementById("toggle-discount");
            const inputDisc = document.getElementById("global-discount-input");
            let discountVal = 0;
            if (toggleDisc && toggleDisc.checked && inputDisc) {
                discountVal = parseFloat(inputDisc.value) || 0;
            }

            // C. KDV Oranını Al (Sadece tikliyse)
            const toggleVat = document.getElementById("toggle-vat");
            const inputVat = document.getElementById("vat-percentage-input");
            let vatRate = 0;
            if (toggleVat && toggleVat.checked && inputVat) {
                vatRate = parseFloat(inputVat.value) || 0;
            }

            // D. Oranları Belirle
            // İndirim Oranı (Tutar üzerinden)
            const discountRatio = (subTotal > 0) ? (discountVal / subTotal) : 0;
            // KDV Çarpanı (Örn: %20 ise 1.20)
            const vatMultiplier = 1 + (vatRate / 100);

            // E. Backend'e gidecek sepeti hazırla (Orijinalini bozmamak için kopya alıyoruz)
            const cartToSend = JSON.parse(JSON.stringify(shoppingCart));

            cartToSend.forEach(item => {
                // 1. Önce bu ürüne düşen indirimi hesapla
                const itemTotalOriginal = item.price * item.quantity;
                const itemDiscount = itemTotalOriginal * discountRatio;

                // 2. KDV'yi Fiyata ve İndirime Yedir
                // Backend Formülü: (Fiyat * Adet) - İndirim
                // Bizim Hedefimiz: ((Fiyat * Adet) - İndirim) * (1 + KDV)
                // Matematiksel olarak: (Fiyat * (1+KDV)) - (İndirim * (1+KDV))
                
                item.price = item.price * vatMultiplier;      // Fiyatı KDV kadar şişir
                item.discount = itemDiscount * vatMultiplier; // İndirimi de KDV kadar şişir ki oran bozulmasın
            });

            // F. Notu Hazırla
            let noteVal = document.getElementById("satis-not").value || "";
            if (vatRate > 0) {
                noteVal += ` (KDV: %${vatRate} Dahil)`;
            }

            // G. Veri Paketini Oluştur
            const data = {
                items: cartToSend,
                customerName: document.getElementById("customers").value,
                notes: noteVal,
                is_debt: document.getElementById("add-as-debt-checkbox")?.checked || false,
                amount_paid: parseFloat(document.getElementById("amount-paid")?.value) || 0.0
            };

            // --- SERVER İSTEĞİ ---
            const res = await fetch("/create_order", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(data)
            });
            const result = await res.json();

            if (res.ok) {
                showToast(result.message, "ok");
            
                if (result.order_id) {
                    window.open(`/generate_order_pdf/${result.order_id}`, '_blank');
                }

                // Formu ve Sepeti Temizle
                document.getElementById("form-sepet-satis").reset();
                shoppingCart = []; // Sepeti boşalt
                renderCart(); // Görünümü güncelle (Boş sepet yazısı çıkar)
                
                // KDV ve İndirim alanlarını gizle/sıfırla
                if (toggleDisc) { toggleDisc.checked = false; toggleDisc.dispatchEvent(new Event('change')); }
                if (toggleVat) { toggleVat.checked = false; toggleVat.dispatchEvent(new Event('change')); }

                // Sipariş listesi sekmesi açıksa yenile
                if(typeof loadUnifiedOrders === 'function') {
                    loadUnifiedOrders(1);
                }
            } else {
                showToast(result.message, "err");
            }

        } catch (err) {
            console.error("Sipariş hatası:", err);
            showToast("Sunucu ile iletişim kurulamadı!", "err");
        } finally {
            // Butonu tekrar aktif et
            btnCreateOrder.disabled = false;
            btnCreateOrder.innerHTML = originalText;
        }
    });
  }
  // Manage Order (Onay/Ret) Sonrası Listeyi Yenile
  window.manageOrder = async function(orderId, action) {
    try {
        const res = await fetch("/manage_order", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ order_id: orderId, action: action })
        });
        const result = await res.json();

        if (res.ok) {
            showToast(result.message, "ok");
            
            // --- DOM MANİPÜLASYONU İLE ANLIK GÜNCELLEME ---
            // Sayfayı yenilemek yerine sadece ilgili satırı bulup değiştiriyoruz.
            
            const row = document.getElementById(`order-row-${orderId}`);
            if (row) {
                // Hücre 0: Durum Badge'i
                const statusCell = row.cells[0];
                // Hücre 7: Aksiyon Butonları (Son hücre)
                const actionCell = row.cells[row.cells.length - 1];

                if (action === 'reject') {
                    // Durumu Kırmızı Yap
                    statusCell.innerHTML = `<span style="background:#FEE2E2; color:#991B1B; padding:4px 8px; border-radius:6px; font-size:0.75rem; font-weight:700; border:1px solid #FECACA;">REDDEDİLDİ</span>`;
                } else if (action === 'approve') {
                    // Durumu Yeşil Yap
                    statusCell.innerHTML = `<span style="background:#DCFCE7; color:#166534; padding:4px 8px; border-radius:6px; font-size:0.75rem; font-weight:700; border:1px solid #BBF7D0;">ONAYLANDI</span>`;
                }

                // Butonları Değiştir (Artık sadece Detay ve Geri Al olmalı)
                actionCell.innerHTML = `
                    <div style="display:flex; justify-content:flex-end; gap:5px;">
                        <button class="btn-secondary" style="padding:5px 8px;" onclick="viewOrderDetails(${orderId})" title="Detay">
                            <i class="ri-eye-line"></i>
                        </button>
                        <button class="btn-secondary" style="background:#F3F4F6; color:#4B5563; padding:5px 8px; border:1px solid #E5E7EB;" 
                                onclick="revertOrder(${orderId})" title="Geri Al">
                            <i class="ri-restart-line"></i>
                        </button>
                    </div>
                `;
                
                // İsteğe bağlı: Satırın rengini anlık olarak hafifçe değiştirip geri alabiliriz (Visual feedback)
                row.style.backgroundColor = action === 'approve' ? '#ecfdf5' : '#fef2f2';
                setTimeout(() => { row.style.backgroundColor = ''; }, 1000);
            }

            // Stokları her ihtimale karşı arka planda güncelle (Ekran oynamaz)
            if(typeof loadProducts === 'function') {
                loadProducts(); 
            }
            
        } else {
            showToast(result.message, "err");
        }
    } catch (err) {
        showToast("İşlem sırasında hata oluştu!", "err");
        console.error("Manage Order Hatası:", err);
    }
};

  // Geri Alma (Revert) Fonksiyonu
  window.revertOrder = async function(orderId) {
      if(!confirm("Bu siparişin durumu 'Bekliyor' olarak değiştirilecek. Onaylıyor musunuz?")) return;
      
      try {
          const res = await fetch("/revert_order", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ order_id: orderId })
          });
          const result = await res.json();
          
          if (res.ok) {
              showToast(result.message, "ok");

              // --- DOM MANİPÜLASYONU (Anlık Güncelleme) ---
              const row = document.getElementById(`order-row-${orderId}`);
              
              if (row) {
                  // 1. Durumu "BEKLİYOR" (Sarı Badge) Yap
                  const statusCell = row.cells[0];
                  statusCell.innerHTML = `<span style="background:#FEF3C7; color:#D97706; padding:4px 8px; border-radius:6px; font-size:0.75rem; font-weight:700; border:1px solid #FCD34D;">BEKLİYOR</span>`;

                  // 2. Butonları Eski Haline (Topla, Reddet, Detay) Getir
                  const actionCell = row.cells[row.cells.length - 1];
                  actionCell.innerHTML = `
                      <div style="display:flex; justify-content:flex-end; gap:5px;">
                          <button class="btn-secondary" style="background:#EFF6FF; color:#3B82F6; border:1px solid #BFDBFE; padding:5px 8px; font-size:0.8rem;" 
                                  onclick="openPickingModal(${orderId})" title="Topla ve Onayla">
                              <i class="ri-hand-coin-line"></i>
                          </button>
                          <button class="btn-delete" style="padding:5px 8px;" 
                                  onclick="manageOrder(${orderId}, 'reject')" title="Reddet">
                              <i class="ri-close-line"></i>
                          </button>
                          <button class="btn-secondary" style="padding:5px 8px;" onclick="viewOrderDetails(${orderId})" title="Detay">
                              <i class="ri-eye-line"></i>
                          </button>
                      </div>
                  `;

                  // Görsel Efekt: Satırı kısa süreliğine sarı yap
                  row.style.backgroundColor = '#FFFBEB';
                  setTimeout(() => { row.style.backgroundColor = ''; }, 1000);
              }

              // 3. Stokları arka planda güncelle (Stok iadesi olmuş olabilir)
              if(typeof loadProducts === 'function') {
                  loadProducts(); 
              }

          } else {
              showToast(result.message, "err");
          }
      } catch (e) {
          showToast("İşlem hatası", "err");
          console.error(e);
      }
  };

  async function loadProducts() {
    try {
      const res = await fetch("/products");
      if (!res.ok) {
        showToast("Ürünler yüklenemedi!", "err");
        return;
      }
      allProducts = await res.json();
      applyFiltersAndSorting();
    } catch (err) {
      console.error("Ürünler yüklenirken hata:", err);
      showToast("Sunucuya bağlanılamadı!", "err");
    }
  }
  
  // Giderleri Yükle
  async function loadExpenditures() {
      if (!expenditureListBody) return;
      try {
          const res = await fetch('/get_expenditures');
          const data = await res.json();
          
          expenditureListBody.innerHTML = "";
          if (data.length === 0) {
              expenditureListBody.innerHTML = "<tr><td colspan='4' style='text-align:center;'>Henüz gider kaydı yok.</td></tr>";
              return;
          }

          data.forEach(item => {
              const tr = document.createElement('tr');
              tr.innerHTML = `
                  <td>${item.date}</td>
                  <td><span style="font-weight:bold; color:#EF4444;">-${item.amount.toFixed(2)} ${item.currency}</span></td>
                  <td style="max-width: 300px; word-wrap: break-word; white-space: normal;">${item.description}</td>
                  <td>${item.user}</td>
              `;
              expenditureListBody.appendChild(tr);
          });
      } catch (err) {
          console.error("Giderler yüklenemedi:", err);
      }
  }

  // Gider Ekleme İşlemi
  if (expenditureForm) {
      expenditureForm.addEventListener('submit', async (e) => {
          e.preventDefault();
          
          const amount = document.getElementById('expenditure-amount').value;
          const currency = document.getElementById('expenditure-currency').value;
          const description = document.getElementById('expenditure-desc').value;

          try {
              const res = await fetch('/add_expenditure', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ amount, currency, description })
              });
              const result = await res.json();

              if (res.ok) {
                  showToast(result.message, "ok");
                  expenditureForm.reset();
                  loadExpenditures(); // Listeyi yenile
              } else {
                  showToast(result.message, "err");
              }
          } catch (err) {
              showToast("Sunucu hatası!", "err");
          }
      });
  }


  async function loadShelves() {
    // Hem eski filtre kutusu (varsa) hem de yeni raf seçme kutusu
    const filterShelfSelect = document.getElementById("shelf"); 
    const actionShelfSelect = document.getElementById("raf-kodu-sec");

    try {
      const res = await fetch('/get_shelves');
      if (!res.ok) return;
      const data = await res.json();
      
      // Filtre (veya başka bir yerdeki eski kutu) için doldurma
      if (filterShelfSelect) {
          filterShelfSelect.innerHTML = `<option value="">Seçiniz</option>`;
      }
      
      // Depoya yerleştirme formundaki kutu için doldurma
      if (actionShelfSelect) {
          actionShelfSelect.innerHTML = `<option value="">Raf Seçiniz</option>`;
      }

      data.shelves.forEach(s => {
          // Eğer eski kutu varsa ona ekle
          if (filterShelfSelect) {
              const opt1 = document.createElement('option');
              opt1.value = s.code;
              opt1.textContent = s.code;
              filterShelfSelect.appendChild(opt1);
          }
          
          // Yeni kutu (Depoya Yerleştir Sekmesi) varsa ona ekle
          if (actionShelfSelect) {
              const opt2 = document.createElement('option');
              opt2.value = s.code;
              opt2.textContent = s.code;
              actionShelfSelect.appendChild(opt2);
          }
      });
    } catch (err) {
      console.error('Raflar yüklenemedi', err);
    }
  }

  async function loadColors() {
    try {
      const res = await fetch("/get_colors");
      if (!res.ok) return;
      const data = await res.json();
      colorSelect.innerHTML = '<option value="">Renk seçin</option>';
      data.colors.forEach(c => {
        const opt = document.createElement("option");
        opt.value = c.code;
        opt.textContent = c.name;
        colorSelect.appendChild(opt);
      });
    } catch (err) {
      console.error("Renkler yüklenemedi:", err);
    }
  }

  if (categorySelect) {
    categorySelect.addEventListener("change", () => {
        const catName = categorySelect.value;
        const catObj = CATEGORIES.find(c => c.name === catName);
        sizesContainer.innerHTML = ""; // Temizle

        if (!catObj || !catObj.sizes || catObj.sizes.length === 0) {
            sizesContainer.innerHTML = '<p class="placeholder-text-center">Bu kategoride beden tanımlı değil veya kategori seçilmedi.</p>';
            return;
        }

        // Başlık satırı (STOK BAŞLIĞI KALDIRILDI)
        const header = document.createElement("div");
        header.className = "size-item header";
        header.innerHTML = `
            <span class="size-label" style="text-align:center;">BEDEN</span>
            <span class="size-input-label" style="text-align:center;">SERİ İÇERİĞİ (ADET)</span>
        `;
        sizesContainer.appendChild(header);

        // Beden satırlarını oluştur (STOK INPUTU KALDIRILDI)
        catObj.sizes.forEach(sizeVal => {
            const row = document.createElement("div");
            row.className = "size-item-new"; 
            row.dataset.sizeValue = sizeVal; 

            row.innerHTML = `
                <span class="size-label">${sizeVal}</span>
                <input type="number" min="0" value="1" class="size-input-number" placeholder="Seride kaç adet var?">
            `;
            sizesContainer.appendChild(row);
        });
        
        // Totalleri hemen hesaplat
        if(typeof calculateTotals === 'function') calculateTotals();
    });
  }

function calculateTotals() {
    let totalInSeri = 0;
    // Güvenli element seçimi
    const sizesContainer = document.getElementById("sizes-container");
    if (sizesContainer) {
        const numberInputs = sizesContainer.querySelectorAll(".size-input-number");
        numberInputs.forEach(input => {
            const val = parseInt(input.value, 10);
            if (!isNaN(val)) totalInSeri += val;
        });
    }

    // Elementleri fonksiyonun içinde tanımlıyoruz (ReferenceError çözüm noktası)
    const totalSizesEl = document.getElementById("total-sizes");
    const totalProductsEl = document.getElementById("total-products");

    if (totalSizesEl) totalSizesEl.textContent = totalInSeri;
    if (totalProductsEl) totalProductsEl.textContent = "0"; // Artık depo stoğu manuel girilmediği için 0
}

  if (addColorBtn) {
    addColorBtn.addEventListener("click", () => {
        const colorCode = colorSelect.value;
        // Seçili option'ın text'ini (İsmini) al
        const colorName = colorSelect.options[colorSelect.selectedIndex]?.text; 

        if (!colorCode) return showToast("Lütfen bir renk seçin!", "err");

        // Zaten ekli mi kontrol et
        if (selectedColors.some(c => c.code === colorCode)) {
            return showToast("Bu renk zaten eklendi.", "err");
        }

        // Listeye ekle
        selectedColors.push({ code: colorCode, name: colorName });
        
        // Ekrana çiz
        renderSelectedColors(); 
        
        // Seçimi sıfırla
        colorSelect.value = "";
    });
  }

  async function loadCategories() {
    try {
      const res = await fetch("/get_categories");
      if (!res.ok) return;
      const data = await res.json();
      CATEGORIES = Array.isArray(data?.categories) ? data.categories : [];
      if (!categorySelect) return;
      categorySelect.innerHTML = '<option value="">Kategori seçin</option>';
      CATEGORIES.forEach(c => {
        const opt = document.createElement("option");
        opt.value = c.name;
        opt.textContent = c.name;
        categorySelect.appendChild(opt);
      });
    } catch (err) {
      console.error("Kategoriler yüklenemedi:", err);
    }
  }

  async function loadCustomers() {
    const customersSelect = document.getElementById("customers");
    if (!customersSelect) return;
    
    try {
        const res = await fetch("/get_all_customers");
        if (!res.ok) return;
        const data = await res.json();
        
        if (!data.success) return;
        
        customersSelect.innerHTML = '<option value="">Müşteri Seçin</option>';
        customerDataMap = {}; 

        data.customers.forEach(c => {
            customerDataMap[c.name] = c; 
            const opt = document.createElement("option");
            opt.value = c.name;
            opt.textContent = c.name;
            customersSelect.appendChild(opt);
        });
    } catch (err) {
        console.error("Müşteriler yüklenemedi:", err);
    }
  }

  // ============================================================
  // 3. MÜŞTERİ BORÇ GÖSTERİMİ & TAB YÖNETİMİ
  // ============================================================
  const customersSelectElement = document.getElementById("customers");
  if (customersSelectElement) {
      customersSelectElement.addEventListener("change", (e) => {
          const selectedName = e.target.value;
          const debtDisplay = document.getElementById("selected-customer-debt");
          if (!debtDisplay) return;

          if (!selectedName || !customerDataMap[selectedName]) {
              debtDisplay.textContent = "";
              return;
          }

          const customer = customerDataMap[selectedName];
          const debtData = customer.debt || {};
          
          let debtStrings = [];
          for (const [currency, amount] of Object.entries(debtData)) {
              if (amount > 0) {
                  debtStrings.push(`${parseFloat(amount).toFixed(2)} ${currency}`);
              }
          }

          if (debtStrings.length > 0) {
              debtDisplay.textContent = `Mevcut Borç: ${debtStrings.join(" / ")}`;
              debtDisplay.style.color = "#dc2626"; 
          } else {
              debtDisplay.textContent = "Mevcut Borç: Yok";
              debtDisplay.style.color = "#10b981"; 
          }
      });
  }

  tabs.forEach(tab => {
      tab.addEventListener("click", e => {
        e.preventDefault(); 
        const targetPanelId = tab.dataset.tab;
        window.location.hash = targetPanelId;
      });
  });

  if (formEkle) {
    formEkle.addEventListener("submit", async (e) => {
        e.preventDefault();

        // 1. Temel Doğrulamalar
        const name = document.getElementById("urun-adi").value.trim();
        const category = document.getElementById("categorySelect").value;

        if (!name || !category) {
            return showToast("Lütfen isim ve kategori alanlarını doldurun.", "err");
        }
        if (selectedColors.length === 0) {
            return showToast("Lütfen en az bir renk ekleyin.", "err");
        }

        // 2. Beden Verilerini Topla
        const sizesData = [];
        const sizeRows = document.querySelectorAll("#sizes-container .size-item-new");
        
        sizeRows.forEach(row => {
            const val = row.dataset.sizeValue;
            const num = parseInt(row.querySelector(".size-input-number").value) || 0;
            const stk = 0;
            
            // Sadece dolu olanları veya hepsi seride varsa hepsini ekle
            sizesData.push({ value: val, number: num, stock: stk });
        });

        // 3. Fiyat Verilerini Topla
        const currencyData = {};
        document.querySelectorAll("#price-container .price-input").forEach(inp => {
            const price = parseFloat(inp.value);
            if (!isNaN(price) && price > 0) {
                currencyData[inp.dataset.size] = price;
            }
        });


        // 5. FormData Oluştur (Resim dahil gönderim için)
        const formData = new FormData();
        formData.append("name", name);
        formData.append("category", category);
        formData.append("description", document.getElementById("description").value);

        // JSON verileri string olarak ekle
        formData.append("sizes", JSON.stringify(sizesData));
        formData.append("currency", JSON.stringify(currencyData));
        
        // Renk kodlarını (ID'lerini) gönder
        const colorIds = selectedColors.map(c => c.code);
        formData.append("colors", JSON.stringify(colorIds));

        const customAttributes = {};
        document.querySelectorAll('.dynamic-attr-input').forEach(selectInput => {
            const key = selectInput.getAttribute('data-key');
            const val = selectInput.value.trim(); // Dropdown'dan seçili olan değeri alır (Örn: "Pamuk")
            if (val) {
                customAttributes[key] = val;
            }
        });

        formData.append("custom_attributes", JSON.stringify(customAttributes));

        // Resim varsa ekle
        const fileInput = document.getElementById("productImage");
        if (fileInput && fileInput.files[0]) {
            formData.append("image", fileInput.files[0]);
        }

        // 6. Backend'e Gönder
        try {
            const res = await fetch("/product", {
                method: "POST",
                body: formData // Header eklemeyin, FormData otomatik ayarlar
            });

            const result = await res.json();
            if (res.ok) {
                showToast(result.message, "ok");
                
                // Formu Sıfırla
                formEkle.reset();
                selectedColors = [];
                renderSelectedColors();
                sizesContainer.innerHTML = '<p class="placeholder-text">Lütfen önce bir kategori seçin.</p>';
                document.getElementById("total-sizes").textContent = "0";
                document.getElementById("total-products").textContent = "0";
                removePreview(); 
                
                // Ürün listesini yenile
                await loadProducts();
            } else {
                showToast(result.message || "Hata oluştu", "err");
            }
        } catch (err) {
            console.error(err);
            showToast("Sunucu hatası!", "err");
        }
    });
  }

window.viewOrderDetails = function(orderId) {
    // Global listeden siparişi bul
    const order = globalOrderList[orderId];
    
    if (!order) {
        showToast("Sipariş verisi yüklenemedi (Sayfayı yenileyin).", "err");
        return;
    }

    const modal = document.getElementById("order-detail-modal");
    const content = document.getElementById("order-detail-content");
    
    // Tablo Başlığı
    let itemsHtml = `
        <table class="stok-table" style="margin-top:10px; width:100%;">
            <thead>
                <tr>
                    <th>Ürün</th>
                    <th>Tip</th>
                    <th>Detay</th>
                    <th>Fiyat</th>
                    <th>İndirim</th>
                </tr>
            </thead>
            <tbody>
    `;
    
    // Ürünleri Listele
    // Backend'den items bazen string bazen obje gelebilir, kontrol edelim:
    let itemsArray = order.items;
    if (typeof itemsArray === 'string') {
        try { itemsArray = JSON.parse(itemsArray); } catch(e) {}
    }

    if (Array.isArray(itemsArray)) {
        itemsArray.forEach(item => {
            let type = item.is_single ? "Parça Satış" : "Seri Satış";
            let detail = "";
            
            // Beden detayını oluştur
            if(item.is_single) {
                let sSizes = item.single_sizes || {};
                // Eğer string geldiyse parse et
                if (typeof sSizes === 'string') { try { sSizes = JSON.parse(sSizes); } catch(e){} }
                detail = Object.entries(sSizes).map(([k,v]) => `${k}:${v}`).join(', ');
            } else {
                detail = `${item.quantity} Seri`;
            }
            
            let priceVal = parseFloat(item.price || 0).toFixed(2);
            let discountVal = parseFloat(item.discount || 0).toFixed(2);
            let currency = item.currency || order.currency || "";
            
            itemsHtml += `
                <tr>
                    <td>${item.name || ''} <br><small style="color:#999">${item.code || ''}</small></td>
                    <td>${type}</td>
                    <td>${detail}</td>
                    <td>${priceVal} ${currency}</td>
                    <td>${discountVal}</td>
                </tr>
            `;
        });
    }
    
    itemsHtml += `</tbody></table>`;
    
    // Durum Yazısı
    let statusText = order.status || 'Beklemede';
    if (statusText === 'APPROVED') statusText = 'Onaylandı';
    if (statusText === 'REJECTED') statusText = 'Reddedildi';
    if (statusText === 'PENDING') statusText = 'Beklemede';

    // Modal İçeriğini Doldur
    content.innerHTML = `
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:20px; margin-bottom:20px; background:#f9fafb; padding:15px; border-radius:8px;">
            <div>
                <p><strong>Sipariş No:</strong> #${order.id}</p>
                <p><strong>Oluşturan:</strong> ${order.creator}</p>
                <p><strong>Müşteri:</strong> ${order.customer}</p>
                <p><strong>Tarih:</strong> ${order.date}</p>
            </div>
            <div style="text-align:right;">
                <p><strong>Durum:</strong> <span style="font-weight:bold;">${statusText}</span></p>
                <p style="font-size:1.4rem; font-weight:bold; color:#3B82F6; margin-top:5px;">
                    ${parseFloat(order.total).toFixed(2)} ${order.currency}
                </p>
                <p><small><strong>Not:</strong> ${order.note || '-'}</small></p>
            </div>
        </div>
        <h4 style="margin:10px 0; border-bottom:1px solid #eee; padding-bottom:5px;">Ürün İçeriği</h4>
        ${itemsHtml}
    `;
    
    // Modalı Göster
    modal.style.display = "flex";
  };


  // ============================================================
  // 4. HIZLI SORGU (FOTOĞRAFLI)
  // ============================================================
  if (formSorguDetay) {
      formSorguDetay.addEventListener("submit", async (e) => {
          e.preventDefault();
          const code = inputSorguDetay.value.trim();
          if (!code) return showToast("Lütfen bir kod girin!", "err");

          sonucKutuDetay.innerHTML = '<p style="text-align:center;">Aranıyor...</p>';

          try {
              const res = await fetch(`/query_product?code=${encodeURIComponent(code)}`);
              
              if (!res.ok) {
                  sonucKutuDetay.innerHTML = `<p class="sorgu-hata" style="color:red; text-align:center;">Ürün bulunamadı!</p>`;
                  inputSorguDetay.select(); 
                  return;
              }

              const data = await res.json();
              
              const seriIcerik = (data.sizes && data.sizes.length > 0)
                  ? data.sizes.map(item => `<span style="background:#e0f2fe; padding:2px 6px; border-radius:4px;">${item.number}x ${item.value}</span>`).join(' ')
                  : 'Tanımsız';
              
              const stokDetay = (data.sizes && data.sizes.length > 0)
                  ? data.sizes.map(item => `<b>${item.value}:</b> ${item.stock}`).join(' | ')
                  : 'Detay yok';

              // --- YENİ EKLENEN KISIM: SON SATIŞ HTML ---
              let lastSaleHtml = '';
              if (data.last_sale) {
                  lastSaleHtml = `
                    <div style="margin-top: 20px; background-color: #FFF7ED; border: 1px solid #FFEDD5; border-radius: 8px; padding: 15px;">
                        <h4 style="margin: 0 0 10px 0; color: #9A3412; font-size: 0.95rem; display:flex; align-items:center; gap:5px;">
                            <i class="ri-history-line"></i> Son Satış Bilgisi
                        </h4>
                        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px; font-size: 0.9rem; color: #431407;">
                            <div>
                                <span style="color: #9A3412; font-weight: 600;">Tarih:</span> ${data.last_sale.date}
                            </div>
                            <div>
                                <span style="color: #9A3412; font-weight: 600;">Müşteri:</span> ${data.last_sale.customer}
                            </div>
                            <div>
                                <span style="color: #9A3412; font-weight: 600;">Satan:</span> ${data.last_sale.seller}
                            </div>
                            <div>
                                <span style="color: #9A3412; font-weight: 600;">Detay:</span> 
                                <b>${data.last_sale.quantity}</b> Adet / 
                                <b>${parseFloat(data.last_sale.price).toFixed(2)}</b> ${data.last_sale.currency}
                            </div>
                        </div>
                    </div>
                  `;
              } else {
                  lastSaleHtml = `
                    <div style="margin-top: 20px; background-color: #F3F4F6; border: 1px solid #E5E7EB; border-radius: 8px; padding: 10px; text-align: center; color: #6B7280; font-style: italic;">
                        Bu ürün için henüz satış kaydı bulunmuyor.
                    </div>
                  `;
              }
              // ------------------------------------------

              let variantsHtml = '';
              if (data.variants && data.variants.length > 1) {
                  // ... (Varyasyon kodları aynı kalacak) ...
                  variantsHtml = `<div class="variants-section" style="margin-top:15px; padding-top:15px; border-top:1px solid #eee;">
                      <strong style="display:block; margin-bottom:10px;">Diğer Renkler:</strong>
                      <div class="variants-list" style="display:flex; gap:10px; flex-wrap:wrap;">`;
                  
                  data.variants.forEach(v => {
                      const activeStyle = v.is_current ? 'background:#3B82F6; color:white; border-color:#3B82F6;' : 'background:white;';
                      variantsHtml += `
                          <button type="button" class="variant-btn" style="${activeStyle} padding:8px 16px; border:1px solid #ccc; border-radius:6px; cursor:pointer;" 
                                  onclick="document.getElementById('s-kod-detay').value='${v.code}'; document.getElementById('formSorguDetay').dispatchEvent(new Event('submit'));">
                              <b>${v.color}</b> (${v.stock})
                          </button>
                      `;
                  });
                  variantsHtml += `</div></div>`;
              }

              let imageHtmlDetay = '';
              if (data.image_path) {
                  imageHtmlDetay = `
                    <div class="product-image-detail" style="text-align: center; margin-bottom: 20px; padding: 10px; background: #fff; border-radius: 8px; border: 1px solid #eee;">
                        <img src="/static/${data.image_path}" alt="${data.name}" style="max-width: 100%; max-height: 300px; object-fit: contain; border-radius: 6px;">
                    </div>
                  `;
              }

              sonucKutuDetay.innerHTML = `
                  ${imageHtmlDetay} 
                  
                  <div style="display:grid; grid-template-columns: 1fr 1fr; gap:20px; margin-bottom:20px;">
                      <div>
                          <h2 style="margin:0; color:#1F2937;">${data.name}</h2>
                          <p style="color:#6B7280; margin:5px 0;">Renk: <strong>${data.color || '-'}</strong></p>
                          <p style="color:#6B7280; margin:0;">Raf: <strong>${data.shelf || '-'}</strong></p>
                      </div>
                      <div style="text-align:right;">
                          <div style="font-size:1.5rem; font-weight:bold; color:#3B82F6;">
                              ${data.stock} <span style="font-size:1rem; color:#6B7280;">Adet (Seri)</span>
                          </div>
                      </div>
                  </div>

                  <div style="background:white; padding:15px; border-radius:8px; border:1px solid #E5E7EB; margin-bottom:15px;">
                      <p style="margin:0 0 10px 0;"><strong>Fiyatlar:</strong></p>
                      <div style="display:flex; gap:15px; flex-wrap:wrap;">
                          ${Object.entries(data.currency || {}).map(([key, val]) => 
                              `<div style="background:#F3F4F6; padding:8px 16px; border-radius:6px;">
                                  ${key}: <strong>${val}</strong>
                               </div>`
                          ).join('')}
                      </div>
                  </div>

                  <div style="margin-bottom:10px;"><strong>Seri İçeriği:</strong> <br> ${seriIcerik}</div>
                  <div><strong>Beden Bazlı Stok:</strong> <br> ${stokDetay}</div>

                  <!-- SON SATIŞ BİLGİSİNİ BURAYA EKLİYORUZ -->
                  ${lastSaleHtml}
                  
                  ${variantsHtml}
              `;
              
              inputSorguDetay.select();

          } catch (err) {
              console.error(err);
              sonucKutuDetay.innerHTML = `<p style="color:red;">Bağlantı hatası!</p>`;
          }
      });
  }

  let typingTimerDetay;                
  
  if (inputSorguDetay) {
    inputSorguDetay.addEventListener('input', () => {
        // Yazı yazılırken önceki zamanlayıcıyı iptal et
        clearTimeout(typingTimerDetay);
        
        // Eğer input boş değilse
        if (inputSorguDetay.value.trim()) {
            // 500ms (yarım saniye) bekle, yazma durduysa gönder
            typingTimerDetay = setTimeout(() => {
                if (formSorguDetay) {
                    // Submit olayını tetikle (Bu sayede yukarıdaki submit dinleyicisi çalışır)
                    formSorguDetay.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
                }
            }, 500); // 500ms bekleme süresi
        }
    });
  }

  // ============================================================
  // 5. SORGULA VE SAT (FOTOĞRAFLI)
  // ============================================================

  formSorgu.addEventListener("submit", async e => {
    e.preventDefault();
    const code = kodInputSorgu.value.trim();
    if (!code) return;

    try {
      const res = await fetch(`/query_product?code=${encodeURIComponent(code)}`);
      if (!res.ok) {
        sonucKutu.innerHTML = `<p class="sorgu-hata">Ürün bulunamadı!</p>`;
        if (formSatisEkle) formSatisEkle.reset();
        
        kodInputSorgu.value = "";
        kodInputSorgu.focus();
        return showToast("Ürün bulunamadı!", "err");
      }

      const data = await res.json();
      
      // --- HTML İÇERİĞİ OLUŞTURMA ---
      const seriIcerik = (data.sizes && data.sizes.length > 0)
        ? data.sizes.map(item => `${item.number} adet ${item.value}`).join(' / ')
        : 'Tanımsız';
      
      const stokDetay = (data.sizes && data.sizes.length > 0)
        ? data.sizes.map(item => `${item.value}: ${item.stock} adet`).join(', ')
        : 'Stok detayı yok';

      let variantsHtml = '';
      if (data.variants && data.variants.length > 1) {
          variantsHtml = `<div class="variants-section">
              <strong>Diğer Renk Seçenekleri:</strong>
              <div class="variants-list">`;
          
          data.variants.forEach(v => {
              const activeClass = v.is_current ? 'active-variant' : '';
              variantsHtml += `
                  <button type="button" class="variant-btn ${activeClass}" onclick="searchVariant('${v.code}')">
                      <span class="v-color">${v.color}</span>
                      <span class="v-stock">(${v.stock})</span>
                  </button>
              `;
          });
          variantsHtml += `</div></div>`;
      }
      
      let imageHtml = '';
      if (data.image_path) {
          imageHtml = `
            <div class="product-image-container" style="margin-bottom: 15px; text-align: center;">
                <img src="/static/${data.image_path}" alt="${data.name}" style="max-width: 400px; max-height: 400px; border-radius: 8px; border: 1px solid #E5E7EB;">
            </div>
          `;
      }
            
      sonucKutu.innerHTML = `
        ${imageHtml}
        <div class="product-header">
            <p><strong>Ürün Adı:</strong> ${data.name}</p>
            <p><strong>Renk:</strong> ${data.color || '-'}</p>
        </div>
        <div class="product-details-grid">
            <p><strong>Stok:</strong> ${data.stock} adet (Seri)</p>
            <p><strong>Fiyat:</strong> ${Object.entries(data.currency || {}).map(([key, val]) => `${val} ${key}`).join(' / ')}</p>
        </div>
        <p><strong>Seri İçeriği:</strong> ${seriIcerik}</p>
        <p><strong>Beden Stokları:</strong> ${stokDetay}</p>
        ${variantsHtml}
      `;

      // --- FORM VE STATE YÖNETİMİ ---
      satisKodInput.value = data.code;
      productList[data.code] = data;

      // 1. Mevcut ayarları hafızaya al
      const currentModeWasSingle = isSingleSaleMode;
      const manualSeriesInput = document.getElementById("manual-series-count");
      let savedSeriesCount = "1";
      if (manualSeriesInput) savedSeriesCount = manualSeriesInput.value;

      const manualOverrideInput = document.getElementById("manual-override-price");
      let savedOverridePrice = "";
      if (manualOverrideInput) savedOverridePrice = manualOverrideInput.value;

      // 2. Formu Sıfırla
      if (formSatisEkle) formSatisEkle.reset(); 
      
      // 3. Ayarları Geri Yükle
      satisKodInput.value = data.code; // Kod silinmişti, geri yaz
      if (manualSeriesInput) manualSeriesInput.value = savedSeriesCount;

      if (manualOverrideInput) manualOverrideInput.value = savedOverridePrice;

      if (currentModeWasSingle) {
          const singleRadio = document.querySelector('input[name="saleType"][value="single"]');
          if (singleRadio) singleRadio.checked = true;
          isSingleSaleMode = true;
          const manualSeriesWrapper = document.getElementById("manual-series-wrapper");
          if (manualSeriesWrapper) manualSeriesWrapper.classList.add('hidden');
      } else {
          const seriesRadio = document.querySelector('input[name="saleType"][value="series"]');
          if (seriesRadio) seriesRadio.checked = true;
          isSingleSaleMode = false;
          const manualSeriesWrapper = document.getElementById("manual-series-wrapper");
          if (manualSeriesWrapper) manualSeriesWrapper.classList.remove('hidden');
      }

      // --- FİYAT BİLGİSİNİ ZORLA (CRITICAL FIX) ---
      // Reset işlemi fiyat inputunu sildiği için, veriden okuyup manuel yazıyoruz.
      // activeSaleCurrency sayfa açılışında 'USD' yapılmıştı.
      
      selectedCurrencyInput.value = activeSaleCurrency; // Hidden inputa yaz
      
      // Butonların görselini güncelle
      currencyButtons.forEach(b => b.classList.remove("active"));
      const activeBtn = document.querySelector(`.currency-btn[data-currency="${activeSaleCurrency}"]`);
      if(activeBtn) activeBtn.classList.add("active");

      // Fiyatı veriden bul ve yaz
      if (data.currency && data.currency[activeSaleCurrency]) {
          const price = data.currency[activeSaleCurrency];
          selectedPriceInput.value = price; // Hidden inputa fiyatı yaz
          
          const display = document.getElementById("selected-price-display");
          if (display) display.textContent = `Seçilen Fiyat: ${parseFloat(price).toFixed(2)} ${activeSaleCurrency}`;
      } else {
          selectedPriceInput.value = ""; // Fiyat yoksa boşalt
      }

      // 4. OTOMATİK EKLEME MANTIĞI
      if (!isSingleSaleMode) {
          // Seri Satış Modu: Doğrudan ekle
          addToCartAction();
      } else {
          // Tekli satış modundaysa inputları hazırla
          renderSingleSaleInputs(data);
          showToast("Ürün bulundu. Adet girip ENTER tuşuna basın.", "ok");
          
          setTimeout(() => {
              const firstInput = document.querySelector('.single-size-val');
              if(firstInput) firstInput.focus();
          }, 100);
      }

      window.isTypingSearch = false;

    } catch (err) {
      console.error("Sorgulama hatası:", err);
      showToast("Sunucuya bağlanılamadı!", "err");
    }
  });




  // ============================================================
  // 6. DİĞER İŞLEVLER (SEPET, EKLEME, DÜZENLEME, SİLME)
  // ============================================================

  currencyButtons.forEach(btn => {
    btn.addEventListener("click", () => {
      const newCurrency = btn.dataset.currency;
      activeSaleCurrency = newCurrency;

      currencyButtons.forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      selectedCurrencyInput.value = newCurrency;

      let priceUpdateFailed = false;
      shoppingCart.forEach(item => {
        const productDetails = productList[item.code];
        if (productDetails && productDetails.currency && Number.isFinite(Number(productDetails.currency[newCurrency])) && productDetails.currency[newCurrency] != null) {
          const newPrice = Number(productDetails.currency[newCurrency]);
          if (item.is_single) {
            const seriesPieces = (productDetails.sizes || []).reduce((sum, size) => sum + Number(size.number || 0), 0);
            const batchPieces = Object.values(item.single_sizes || {}).reduce((sum, count) => sum + Number(count), 0);
            item.price = seriesPieces > 0 ? Number((newPrice * batchPieces / seriesPieces).toFixed(2)) : 0;
          } else {
            item.price = newPrice;
          }
          item.currency = newCurrency;
        } else {
          priceUpdateFailed = true;
          item.price = 0;
          item.currency = newCurrency;
        }
      });

      renderCart();

      if (priceUpdateFailed) {
          showToast("Uyarı: Sepetteki bazı ürünlerin yeni para biriminde fiyatı yok!", "err");
      }

      const productCode = satisKodInput.value.trim();
      if (productCode) {
          const product = productList[productCode];
          const display = document.getElementById("selected-price-display");

          if (product && product.currency && product.currency[newCurrency]) {
              const price = product.currency[newCurrency];
              selectedPriceInput.value = price;
              if (display) display.textContent = `Seçilen Fiyat: ${price.toFixed(2)} ${newCurrency}`;
          } else {
              selectedPriceInput.value = "";
              if (display) display.textContent = "Bu para birimi için fiyat tanımlı değil.";
          }
      }
      
      if (yuzdelikIndirimInput) {
        yuzdelikIndirimInput.dispatchEvent(new Event('input'));
      }
    });
  });

  const globalDiscountInput = document.getElementById("global-discount-input");
  if (globalDiscountInput) {
      globalDiscountInput.addEventListener("input", () => {
          updateCartTotal();
      });
  }

    function renderCart() {
        const tbody = document.getElementById("shopping-cart-container");
        const emptyMsg = document.getElementById("cart-empty-msg");
        
        tbody.innerHTML = "";
        
        // Sepet boş mu kontrolü
        if (shoppingCart.length === 0) {
            if(emptyMsg) emptyMsg.style.display = "block";
            updateCartTotal();
            return;
        }
        
        if(emptyMsg) emptyMsg.style.display = "none";

        shoppingCart.forEach((item, index) => {
            
            // --- 1. STOK VERİSİNİ VE DURUMUNU HESAPLA ---
            const productData = productList[item.code]; 
            
            let stockLabelText = ""; // Rozet içinde yazacak metin (Örn: XL: 5 Stok)
            let isInsufficient = false; // Stok yetersiz mi?
            let isLowStock = false; // Stok kritik mi? (5'in altı)

            if (productData) {
                if (item.is_single) {
                    // --- PARÇA SATIŞ MANTIĞI ---
                    // Sepete eklenen bedenleri (single_sizes) döngüye al
                    // Örn: item.single_sizes = { "XL": 3, "M": 2 }
                    
                    let stockInfoParts = [];
                    
                    // Seçilen her bir beden için kontrol yap
                    for (const [sizeName, qtyWanted] of Object.entries(item.single_sizes || {})) {
                        
                        // Ana veri listesinden bu bedenin gerçek stoğunu bul
                        const sizeData = productData.sizes ? productData.sizes.find(s => s.value === sizeName) : null;
                        const realStock = sizeData ? parseInt(sizeData.stock) : 0; // Eğer beden yoksa stok 0'dır

                        // Stok bilgisini metne ekle (Örn: "XL: 5")
                        stockInfoParts.push(`${sizeName}: ${realStock}`);

                        // KONTROL 1: İstenen > Gerçek Stok mu?
                        if (qtyWanted * item.quantity > realStock) {
                            isInsufficient = true;
                        }
                        
                        // KONTROL 2: Stok az mı?
                        if (realStock <= 5) {
                            isLowStock = true;
                        }
                    }

                    // Ekranda görünecek metni birleştir
                    // Sonuç: "XL: 5 | M: 10 Stok"
                    stockLabelText = stockInfoParts.join(" | ") + " Stok";

                } else {
                    // --- SERİ SATIŞ MANTIĞI ---
                    const seriStock = parseInt(productData.stock) || 0;
                    stockLabelText = `${seriStock} Seri Stok`;

                    // Seri adedi kontrolü
                    if (item.quantity > seriStock) {
                        isInsufficient = true;
                    }
                    if (seriStock <= 5) {
                        isLowStock = true;
                    }
                }
            } else {
                stockLabelText = "?";
            }

            // --- 2. GÖRSEL SINIFLARI BELİRLE ---
            
            // Satır sınıfı (Hata varsa kırmızı yanar)
            const rowClass = isInsufficient ? "row-error" : "";
            
            // Stok Badge (Kutu) Sınıfı
            let badgeClass = "cart-stock-badge";
            
            // Eğer yetersizse
            if (isInsufficient) {
                badgeClass += " badge-error"; 
                stockLabelText = `Yetersiz! (${stockLabelText})`; // "Yetersiz! (XL: 2 Stok)"
            } 
            // Eğer kritik seviyedeyse
            else if (isLowStock) {
                badgeClass += " low"; 
            }

            // Input Sınıfı (Hata varsa çerçeve kırmızı)
            const inputClass = isInsufficient ? "cart-qty-inp cart-item-qty-input input-error-border" : "cart-qty-inp cart-item-qty-input";

            // --- 3. HTML OLUŞTUR ---
            const tr = document.createElement("tr");
            tr.className = rowClass;

            // Ürün Detay Metni
            let detailInfo = "";
            if (item.is_single) {
                // Parça satışta detay: XL:100 (İstenen miktar)
                const parts = Object.entries(item.single_sizes || {}).map(([s, q]) => `<b>${s}</b>:${q}`);
                detailInfo = `<div style="font-size:0.8rem; color:#6B7280; margin-top:2px;"><i class="ri-t-shirt-line"></i> ${parts.join(', ')} (İstenen)</div>`;
            } else {
                detailInfo = `<div style="font-size:0.8rem; color:#6B7280; margin-top:2px;"><i class="ri-stack-line"></i> ${item.quantity} Tam Seri</div>`;
            }

            // Adet Inputu (Teklide devre dışı, Seride input)
            let qtyInputHtml = "";
            if (item.is_single) {
                qtyInputHtml = `<span style="font-weight:600; color:#374151;">Paket</span>`;
            } else {
                qtyInputHtml = `<input type="number" min="1" class="${inputClass}" 
                            data-index="${index}" value="${item.quantity}">`;
            }

            tr.innerHTML = `
                <td>
                    <div style="font-weight:600; color:#1F2937;">${item.name || "İsimsiz"}</div>
                    <div style="font-size:0.8rem; color:#9CA3AF;">#${item.code} ${item.color ? '• ' + item.color : ''}</div>
                    ${detailInfo}
                </td>
                <td>
                    <!-- STOK BİLGİSİ BURADA GÖSTERİLİYOR -->
                    <span class="${badgeClass}">
                        ${stockLabelText}
                    </span>
                </td>
                <td style="text-align:center;">
                    ${qtyInputHtml}
                </td>
                <td style="text-align:center;">
                    <input type="number" step="0.01" min="0" class="cart-price-inp cart-item-price-input" 
                        data-index="${index}" value="${item.price.toFixed(2)}">
                    <div style="font-size:0.7rem; color:#9CA3AF; margin-top:2px;">${item.currency}</div>
                </td>
                <td style="text-align:right;">
                    <strong class="item-row-total" style="color:#059669;">
                        ${(item.price * item.quantity).toFixed(2)} ${item.currency}
                    </strong>
                </td>
                <td style="text-align:center;">
                    <button type="button" class="btn-remove-item" data-index="${index}" style="color:#EF4444; background:none; border:none; cursor:pointer; font-size:1.2rem;">
                        <i class="ri-delete-bin-line"></i>
                    </button>
                </td>
            `;

            tbody.appendChild(tr);
        });

        updateCartTotal();
    }

  function updateDebtCalculation() {
    const addAsDebtCheckbox = document.getElementById("add-as-debt-checkbox");
    const debtCalculationDisplay = document.getElementById("debt-calculation-display");
    const cartTotalElement = document.getElementById("cart-total-value");
    const amountPaidInput = document.getElementById("amount-paid");

    // Eğer gerekli elementler yoksa veya veresiye seçili değilse işlem yapma
    if (!addAsDebtCheckbox || !addAsDebtCheckbox.checked || !debtCalculationDisplay || !cartTotalElement) {
        if (debtCalculationDisplay) debtCalculationDisplay.textContent = "";
        return;
    }

    // 1. Ekrandaki GÜNCEL SON TOPLAMI al (KDV ve İndirim dahil hali)
    // Örn: "26.40 USD" -> "26.40" ve "USD"
    const totalText = cartTotalElement.textContent.trim();
    const totalParts = totalText.split(' ');
    
    // Sayıyı ve para birimini ayrıştır
    let currentTotal = 0;
    let currentCurrency = "TL";

    if (totalParts.length > 0) {
        currentTotal = parseFloat(totalParts[0]);
        if (totalParts.length > 1) {
            currentCurrency = totalParts[1];
        }
    }

    // 2. Girilen Nakit Tutarını Al
    const paidAmount = parseFloat(amountPaidInput.value) || 0;

    // 3. Kalan Borcu Hesapla
    const remainingDebt = currentTotal - paidAmount;

    // 4. Sonucu Ekrana Yaz
    if (remainingDebt > 0.01) { // 0.01 tolerans (kuruş farkları için)
        debtCalculationDisplay.innerHTML = `
            <div style="display:flex; justify-content:space-between; font-size:0.85rem; color:#6B7280;">
                <span>Toplam Tutar:</span> <span>${currentTotal.toFixed(2)} ${currentCurrency}</span>
            </div>
            <div style="display:flex; justify-content:space-between; font-size:0.85rem; color:#10B981;">
                <span>Alınan Nakit:</span> <span>-${paidAmount.toFixed(2)} ${currentCurrency}</span>
            </div>
            <hr style="margin:5px 0; border-color:#d1fae5;">
            <div style="display:flex; justify-content:space-between; font-size:1rem; font-weight:700; color:#EF4444;">
                <span>Kalan (Veresiye):</span> <span>${remainingDebt.toFixed(2)} ${currentCurrency}</span>
            </div>
        `;
    } else if (remainingDebt < -0.01) {
        // Müşteri fazla para verdiyse (Para üstü)
        debtCalculationDisplay.innerHTML = `
            <span style="color:#F59E0B; font-weight:bold; display:block; text-align:center;">
                Para Üstü: ${Math.abs(remainingDebt).toFixed(2)} ${currentCurrency}
            </span>`;
    } else {
        // Tamamı ödeniyorsa
        debtCalculationDisplay.innerHTML = `
            <span style="color:#10B981; font-weight:bold; display:block; text-align:center;">
                Tamamı Nakit Ödenecek. (Borç Eklenmeyecek)
            </span>`;
    }
}

  function updateCartTotal() {
    let subTotal = 0; 
    const currency = shoppingCart.length > 0 ? shoppingCart[0].currency : 'TL';
    
    // 1. Ara Toplamı Hesapla
    shoppingCart.forEach(item => {
        subTotal += (item.price * item.quantity);
    });

    // 2. İndirimi Al
    let globalDiscount = 0;
    const toggleDisc = document.getElementById("toggle-discount");
    const inputDisc = document.getElementById("global-discount-input");
    
    if (toggleDisc && toggleDisc.checked && inputDisc) {
        globalDiscount = parseFloat(inputDisc.value) || 0;
        if (globalDiscount > subTotal) globalDiscount = subTotal;
    }
    
    // İndirimli Ara Tutar
    const afterDiscountTotal = subTotal - globalDiscount;

    // 3. KDV Hesapla (İndirimden sonraki tutar üzerinden)
    let vatAmount = 0;
    const toggleVat = document.getElementById("toggle-vat");
    const inputVat = document.getElementById("vat-percentage-input");

    if (toggleVat && toggleVat.checked && inputVat) {
        const vatRate = parseFloat(inputVat.value) || 0;
        if (vatRate > 0) {
            vatAmount = afterDiscountTotal * (vatRate / 100);
        }
    }

    // 4. Genel Toplam
    const grandTotal = afterDiscountTotal + vatAmount;

    // 5. Ekrana Yazdır
    const cartTotalElement = document.getElementById("cart-total-value");
    if (cartTotalElement) {
        cartTotalElement.textContent = `${grandTotal.toFixed(2)} ${currency}`;
    }

    // 6. Özet Kısmını Güncelle (Küçük yazılar)
    const summaryBox = document.getElementById("calculation-summary");
    if (summaryBox) {
        if (globalDiscount > 0 || vatAmount > 0) {
            summaryBox.style.display = "block";
            document.getElementById("summary-subtotal").textContent = `${subTotal.toFixed(2)} ${currency}`;
            
            const rowDisc = document.getElementById("row-summary-discount");
            if(globalDiscount > 0) {
                rowDisc.style.display = "flex";
                document.getElementById("summary-discount").textContent = `-${globalDiscount.toFixed(2)} ${currency}`;
            } else { rowDisc.style.display = "none"; }

            const rowVat = document.getElementById("row-summary-vat");
            if(vatAmount > 0) {
                rowVat.style.display = "flex";
                document.getElementById("summary-vat").textContent = `+${vatAmount.toFixed(2)} ${currency}`;
            } else { rowVat.style.display = "none"; }

        } else {
            summaryBox.style.display = "none";
        }
    }

    updateDebtCalculation(grandTotal, currency);
  }

  // ==========================================
  // BARKOD OKUYUCU ENTEGRASYONU
  // ==========================================

  // Peş peşe okumayı engellemek için kilit mekanizması
  let isScannerLocked = false; 

  if (startScanBtn && scannerContainer) {
    startScanBtn.addEventListener("click", () => {
      // 1. Tarayıcı alanını görünür yap
      scannerContainer.style.display = "block";

      if (!html5QrCode) {
        html5QrCode = new Html5Qrcode("barcode-scanner");
      }

      const config = { fps: 10, qrbox: { width: 250, height: 250 } };
      
      // 3. Kamerayı başlat
      html5QrCode.start(
        { facingMode: "environment" }, 
        config,
        (decodedText, decodedResult) => {
          // --- BAŞARILI OKUMA ---
          
          // Eğer kilitliyse işlem yapma (Aynı ürünü peş peşe eklemesin diye)
          if (isScannerLocked) return;

          console.log(`Barkod okundu: ${decodedText}`);
          
          // 1. Kilidi Aktif Et
          isScannerLocked = true;

          // 2. Sesi Çal (Opsiyonel: Okunduğunu hissettirmek için)
          // const audio = new Audio('/static/beep.mp3'); audio.play().catch(e=>{});

          // 3. Okunan kodu inputa yaz
          kodInputSorgu.value = decodedText;

          // 4. Formu gönder (Sepete Ekleme Çalışır)
          if (formSorgu) {
              formSorgu.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
          }

          // 5. KAMERAYI KAPATMIYORUZ.
          // Sadece görsel geri bildirim veriyoruz (Toast mesajı zaten çıkacak).
          
          // 6. Belirli bir süre sonra kilidi aç (Örn: 2 saniye sonra yeni ürün okuyabilir)
          setTimeout(() => {
              isScannerLocked = false;
              // Inputu temizle ki yeni kod geldiği anlaşılsın
              kodInputSorgu.value = ""; 
          }, 1000); 

        },
        (errorMessage) => {
          // Okuma hatalarını yoksay
        }
      ).catch(err => {
        console.error("Kamera başlatma hatası:", err);
        showToast("Kamera başlatılamadı.", "err");
        scannerContainer.style.display = "none";
      });
    });
  }

  // Kamerayı Kapat Butonu (Manuel Kapatma)
  if (stopScanBtn) {
    stopScanBtn.addEventListener("click", () => {
      if (html5QrCode) {
        html5QrCode.stop().then(() => {
          scannerContainer.style.display = "none";
          isScannerLocked = false; // Kilidi sıfırla
        }).catch(err => {
          console.log("Durdurma hatası:", err);
          scannerContainer.style.display = "none";
        });
      } else {
        scannerContainer.style.display = "none";
      }
    });
  }

  cartContainer.addEventListener("input", (e) => {
    const target = e.target;
    const index = target.dataset.index;
    const item = shoppingCart[index];

    // A) ADET DEĞİŞİKLİĞİ
    if (target.classList.contains("cart-item-qty-input")) {
        let newQty = parseInt(target.value);
        if (isNaN(newQty) || newQty < 1) newQty = 1;
        item.quantity = newQty;
    }
    
    // B) FİYAT DEĞİŞİKLİĞİ (YENİ EKLENEN KISIM)
    else if (target.classList.contains("cart-item-price-input")) {
        let newPrice = parseFloat(target.value);
        if (isNaN(newPrice) || newPrice < 0) newPrice = 0;
        item.price = newPrice;
    }

    // HER İKİ DURUMDA DA GÖRÜNTÜYÜ GÜNCELLE
    if (item) {
        // 1. O satırın altındaki küçük toplam yazısını güncelle
        const rowTotal = (item.price * item.quantity).toFixed(2);
        const rowTotalSpan = target.closest('tr')?.querySelector('.cart-row-total-text');
        if (rowTotalSpan) {
            rowTotalSpan.textContent = `Top: ${rowTotal} ${item.currency}`;
        }

        // 2. Genel toplamı güncelle
        updateCartTotal();
    // Adet değiştiğinde stok kontrolü için görseli yenile
        if (target.classList.contains("cart-item-qty-input")) {
             renderCart(); 
             // Dikkat: renderCart input odağını kaybettirebilir. 
             // Eğer odak kaybı sorun olursa renderCart yerine sadece class ekleme mantığı gerekir.
             // Ama en sağlamı renderCart'tır.
             
             // Odağı tekrar inputa vermek için (renderCart sonrası element yeniden oluşur):
             setTimeout(() => {
                 const newInput = document.querySelector(`.cart-item-qty-input[data-index="${index}"]`);
                 if(newInput) {
                     newInput.focus();
                     // İmleci sona al (kullanıcı deneyimi için)
                     /* newInput.setSelectionRange(newInput.value.length, newInput.value.length); */
                 }
             }, 0);
        }
    }
});

  // Yardımcı Fonksiyon: Sadece ilgili satırın toplamını günceller (Tüm sepeti render etmeden)
  function updateRowTotal(index, inputElement) {
      const item = shoppingCart[index];
      const rowTotal = (item.price * item.quantity).toFixed(2);
      
      // Inputun bulunduğu kartın içindeki toplam alanını bul
      const cartItemDiv = inputElement.closest('.cart-item');
      const totalSpan = cartItemDiv.querySelector('.item-row-total');
      
      if (totalSpan) {
          totalSpan.textContent = `${rowTotal} ${item.currency}`;
      }
  }

// 3. Inputtan çıkınca görünümü tazele (Detay metinleri için)
cartContainer.addEventListener("change", (e) => {
    if (e.target.classList.contains("cart-item-qty-input") || e.target.classList.contains("cart-item-price-input")) {
        // renderCart(); // İsterseniz açabilirsiniz, ancak input odak kaybı olmaması için kapalı kalması daha iyi.
    }
});

  // 1. Silme İşlemi (Click)
  cartContainer.addEventListener("click", (e) => {
    const removeBtn = e.target.closest(".btn-remove-item");
    if (removeBtn) {
        const itemIndex = parseInt(removeBtn.dataset.index, 10);
        shoppingCart.splice(itemIndex, 1);
        renderCart();
        showToast("Ürün çıkarıldı.");
    }
});

    function addToCartAction() {
        const code = satisKodInput.value;
        const product = productList[code];
        
        if (!product) {
            return showToast("Hata: Ürün seçili değil.", "err");
        }

        const currency = activeSaleCurrency;
        
        // --- 1. FİYAT BELİRLEME MANTIĞI ---
        let finalPrice = 0;
        
        // Sabit Fiyat kutusunu kontrol et
        const overridePriceInput = document.getElementById("manual-override-price");
        if (overridePriceInput && overridePriceInput.value !== "" && parseFloat(overridePriceInput.value) > 0) {
            // Eğer elle fiyat girildiyse onu kullan
            finalPrice = parseFloat(overridePriceInput.value);
        } else {
            // Yoksa veritabanından gelen (veya seçili para birimindeki) fiyatı kullan
            finalPrice = parseFloat(selectedPriceInput.value) || 0;
        }

        if (!currency || finalPrice <= 0) {
            return showToast(`Fiyat bilgisi eksik!`, "err");
        }
        // -------------------------------------

        // Eklenecek ürünün taslağını oluştur
        let newItem = {
            code: product.code, 
            name: product.name, 
            color: product.color || '',
            currency: currency, 
            discount: 0,
            sizes_composition: product.sizes || []
        };

        // --- SENARYO 1: TEKLİ (PARÇA) SATIŞ ---
        if (isSingleSaleMode) {
            const inputs = document.querySelectorAll('.single-size-val');
            let totalSingleQty = 0;
            let selectedSizes = {};

            inputs.forEach(input => {
                const val = parseInt(input.value) || 0;
                if (val > 0) {
                    totalSingleQty += val;
                    const sizeName = input.getAttribute('data-size');
                    selectedSizes[sizeName] = val;
                }
            });

            if (totalSingleQty === 0) return showToast("Lütfen adet girin!", "err");

            // Fiyat Hesabı (Birim Fiyat)
            let totalItemsInSeries = 0;
            if (product.sizes) totalItemsInSeries = product.sizes.reduce((acc, s) => acc + (s.number || 0), 0);
            if (totalItemsInSeries === 0) totalItemsInSeries = 1;
            
            // finalPrice burada "Bir Serinin Fiyatı"dır.
            const unitPrice = finalPrice / totalItemsInSeries;
            const batchPrice = unitPrice * totalSingleQty; // Seçilen grubun toplam fiyatı

            newItem.quantity = 1; // 1 Grup
            newItem.unit = 'Parça';
            newItem.price = batchPrice; 
            newItem.is_single = true;
            newItem.single_sizes = selectedSizes;
            
            inputs.forEach(i => i.value = ''); // Temizle

        } 
        // --- SENARYO 2: SERİ SATIŞ ---
        else {
            // --- EKLENECEK ADETİ BELİRLE ---
            const manualInput = document.getElementById("manual-series-count");
            let eklenecekAdet = 1; // Varsayılan

            if (manualInput) {
                const val = parseInt(manualInput.value);
                // Kutu boş değilse ve 1'den büyükse o değeri al
                if (!isNaN(val) && val >= 1) {
                    eklenecekAdet = val;
                }
            }
            // ------------------------------

            newItem.quantity = eklenecekAdet;
            newItem.unit = 'Seri';
            newItem.price = finalPrice; // Birim seri fiyatı
            newItem.is_single = false;
            
            // Inputu temizle ve odaklan
            kodInputSorgu.value = "";
            kodInputSorgu.focus();
        }

        // --- BİRLEŞTİRME MANTIĞI (MERGE LOGIC) ---
        // Sepette "aynı nitelikte" ürün var mı kontrol et
        const existingItemIndex = shoppingCart.findIndex(item => {
            // 1. Temel özellikler aynı mı?
            const basicMatch = item.code === newItem.code && 
                            item.is_single === newItem.is_single &&
                            item.currency === newItem.currency;
            
            if (!basicMatch) return false;

            // 2. Fiyat aynı mı?
            if (Math.abs(item.price - newItem.price) > 0.01) return false;

            // 3. Tekli satışsa, seçilen beden kombinasyonu BİREBİR aynı mı?
            if (newItem.is_single) {
                const currentSizes = JSON.stringify(item.single_sizes);
                const newSizes = JSON.stringify(newItem.single_sizes);
                return currentSizes === newSizes;
            }

            return true;
        });

        if (existingItemIndex !== -1) {
            // VARSA: Adedini artır
            shoppingCart[existingItemIndex].quantity += newItem.quantity;
            showToast(`Sepetteki ürün adedi güncellendi (+${newItem.quantity})`);
        } else {
            // YOKSA: Yeni satır ekle
            shoppingCart.push(newItem);
            showToast(`${product.name} sepete eklendi.`);
        }
        
        renderCart();
    }

  stokTbody.addEventListener("click", async (e) => {
    const target = e.target;
    
    // 1. SİLME BUTONUNA TIKLANDI MI?
    // Eğer sil butonuna veya içindeki ikona tıklandıysa silme işlemini yap
    const deleteBtn = target.closest(".btn-delete");
    if (deleteBtn) {
        e.stopPropagation(); // Satır tıklamasını engelle
        const productId = parseInt(deleteBtn.dataset.id, 10);
        if (confirm("Bu ürünü silmek istediğinizden emin misiniz?")) {
            try {
                const res = await fetch(`/product/delete/${productId}`, { method: "DELETE" });
                if (!res.ok) return showToast("Ürün silinemedi!", "err");
                await loadProducts();
                showToast("Ürün başarıyla silindi.");
            } catch {
                showToast("Sunucu hatası!", "err");
            }
        }
        return; // İşlemi bitir, yönlendirme yapma
    }

    // 2. SATIRA TIKLANDI MI? (YÖNLENDİRME)
    // Tıklanan element bir "product-row" (tekli satır) veya "product-row-inner" (grup içi satır) mu?
    const row = target.closest(".product-row") || target.closest(".product-row-inner");
    
    // Eğer satır bulunduysa ve tıklanan yer "grup açma ikonu" veya "grup başlığı" değilse
    if (row && !target.closest(".group-row")) {
        const productId = row.dataset.id;
        if (productId) {
            // YÖNLENDİRME BURADA YAPILIYOR
            // Backend'de ürün detay sayfasının rotası neyse onu yazın.
            // Örnek: /product_detail/123 veya /product/edit/123
            window.location.href = `/product_detail/${productId}`; 
        }
    }
});

  // RESİM SİLME VE DEĞİŞTİRME EVENTLERİ
  const btnDeleteImage = document.getElementById("btn-delete-image");
  if (btnDeleteImage) {
      btnDeleteImage.addEventListener("click", () => {
          // Önizlemeyi gizle
          document.getElementById("current-image-container").style.display = "none";
          // Aksiyonu 'delete' olarak ayarla
          document.getElementById("edit-image-action").value = "delete";
          // Dosya seçimini temizle (kullanıcı vazgeçip silmek istemiş olabilir)
          document.getElementById("edit-product-image").value = ""; 
      });
  }

  const editFileInput = document.getElementById("edit-product-image");
  if (editFileInput) {
      editFileInput.addEventListener("change", () => {
          if (editFileInput.files.length > 0) {
              // Dosya seçildiyse aksiyon 'update' olur
              document.getElementById("edit-image-action").value = "update";
          } else {
              // Dosya seçimi iptal edildiyse ve resim zaten silinmediyse 'keep'e dön
              // (Basit mantık: dosya yoksa ve silme butonuna basılmadıysa koru)
              if (document.getElementById("current-image-container").style.display !== "none") {
                  document.getElementById("edit-image-action").value = "keep";
              }
          }
      });
  }


  // Diğer Eventler
  searchInput.addEventListener("input", applyFiltersAndSorting);
  sortSelect.addEventListener("change", applyFiltersAndSorting);

  function activateTabFromHash() {
    // 1. URL'deki hash'i al (örn: #mevcut-stoklar)
    let hash = window.location.hash.substring(1);

    // 2. Eğer hash yoksa, varsayılan olarak 'yeni-urun-ekle' sekmesini seç
    // (İsterseniz 'sorgula-ve-sat' veya başka bir id yapabilirsiniz)
    if (!hash) {
        hash = 'yeni-urun-ekle'; 
        // URL'i güncellemeden sadece görünümü açmak isterseniz alttaki satırı silebilirsiniz
        // window.location.hash = hash; 
    }

    // 3. İlgili sekmeyi ve paneli bul
    const targetTab = document.querySelector(`.tab-link[data-tab='${hash}']`);
    const targetPanel = document.getElementById(hash);

    if (targetTab && targetPanel) {
      // Önce hepsini pasif yap
      document.querySelectorAll('.tab-link').forEach(el => el.classList.remove('active'));
      document.querySelectorAll('.tab-panel').forEach(el => el.classList.remove('active')); // Eğer CSS'de display:none varsa bu sınıf display:block yapmalı

      // Hedefi aktif yap
      targetTab.classList.add('active');
      
      // Paneli aktif yap (CSS'e göre display: block veya flex olmalı)
      targetPanel.classList.add('active');
      // Ekstra güvenlik: stili doğrudan uygula (bazı CSS çakışmalarını önler)
      targetPanel.style.display = 'block'; 
      
      // Diğer panelleri gizle
      document.querySelectorAll('.tab-panel').forEach(el => {
          if (el.id !== hash) el.style.display = 'none';
      });
    }
  }
  
  // --- Sayfa Yüklendiğinde ---
  loadProducts();
  loadShelves();
  loadColors();
  loadCustomers();
  loadCategories();
  activateTabFromHash();
  loadExpenditures();
  loadOrderFilters();
  loadAttributeDefinitions(); 
  loadBarcodeRules();
  window.addEventListener('hashchange', activateTabFromHash);

  window.searchVariant = function(code) {
    const input = document.getElementById('s-kod');
    const form = document.getElementById('formSorgu');
    if(input && form) {
        input.value = code;
        form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
    }
  };

  // Yardımcılar (Render)
  function renderEditSizeInputs(product) {
      const container = document.getElementById("edit-sizes-container");
      container.innerHTML = "";
      if (!product || !Array.isArray(product.sizes) || product.sizes.length === 0) {
          container.innerHTML = "<p>Bu ürün için beden detayı bulunmuyor.</p>";
          return;
      }
      const header = document.createElement("div");
      header.className = "size-item header";
      header.innerHTML = `<span class="size-label">Beden</span><span class="size-input-label">Serideki Adet</span><span class="size-input-label">Depo Stoğu</span>`;
      container.appendChild(header);

      product.sizes.forEach(sz => {
          const wrap = document.createElement("div");
          wrap.className = "size-item-new";
          wrap.dataset.sizeValue = sz.value;
          const label = document.createElement("span");
          label.className = "size-label";
          label.textContent = sz.value;
          
          const createInput = (value, className) => {
              const input = document.createElement("input");
              input.type = "number";
              input.min = "0";
              input.value = value;
              input.className = className;
              return input;
          };
          const numberInput = createInput(sz.number || 0, "size-input-number-edit");
          const stockInput = createInput(sz.stock || 0, "size-input-stock-edit");
          wrap.append(label, numberInput, stockInput);
          container.appendChild(wrap);
      });
  }

// 1. Nitelikleri Çekip Dropdown'ı Doldurma
async function initDynamicFilters() {
    try {
        const res = await fetch('/get_attribute_defs');
        const data = await res.json();
        
        const select = document.getElementById('filter-attr-select');
        const inputWrapper = document.getElementById('filter-attr-input-wrapper');
        const input = document.getElementById('filter-attr-value');
        
        if (data.status === 'ok' && data.attributes.length > 0) {
            // Dropdown'ı temizle ve doldur
            select.innerHTML = '<option value="">Özellik Seç</option>';
            
            data.attributes.forEach(attr => {
                const option = document.createElement('option');
                option.value = attr.name;
                option.textContent = attr.name;
                select.appendChild(option);
            });

            // Seçim değişince
            select.addEventListener('change', () => {
                if (select.value) {
                    // Bir özellik seçildiyse inputu göster ve odaklan
                    inputWrapper.style.display = 'block';
                    input.placeholder = `${select.value} ara...`;
                    input.focus();
                } else {
                    // Seçim kaldırıldıysa inputu gizle ve değeri temizle
                    inputWrapper.style.display = 'none';
                    input.value = '';
                    applyFiltersAndSorting(); // Filtreyi sıfırla
                }
            });

            // Inputa yazı yazılınca filtrele
            input.addEventListener('input', applyFiltersAndSorting);
        }
    } catch (e) {
        console.error("Filtreler yüklenemedi", e);
    }
}

const filterValInp = document.getElementById('filter-attr-value');
if (filterValInp) {
    // Kutunun içine her karakter yazıldığında filtreleme fonksiyonunu çalıştır
    filterValInp.addEventListener('input', applyFiltersAndSorting);
}

// 2. Filtreleme Mantığı
function applyFiltersAndSorting() {
    let filteredProducts = [...allProducts];
    
    // 1. ANA ARAMA (İsim ve Kod)
    if (searchInput) {
        const searchTerm = searchInput.value.toLowerCase().trim();
        if (searchTerm) {
            filteredProducts = filteredProducts.filter(p =>
                p.name.toLowerCase().includes(searchTerm) ||
                p.code.toLowerCase().includes(searchTerm)
            );
        }
    }

    // 2. ÖZELLİK FİLTRESİ (Dinamik Nitelikler)
    const attrSelect = document.getElementById('filter-attr-select');
    const attrInput = document.getElementById('filter-attr-value');

    if (attrSelect && attrInput && attrSelect.value && attrInput.value) {
        const key = attrSelect.value; // Örn: "Kumaş"
        const val = attrInput.value.toLowerCase().trim(); // Örn: "pamuk"

        filteredProducts = filteredProducts.filter(p => {
            if (!p.custom_attributes) return false;
            const productAttrVal = p.custom_attributes[key];
            return productAttrVal && String(productAttrVal).toLowerCase().includes(val);
        });
    }

    // 3. SIRALAMA (Mevcut mantık)
    if (sortSelect) {
        const sortValue = sortSelect.value;
        switch (sortValue) {
          case "name-asc": filteredProducts.sort((a, b) => a.name.localeCompare(b.name)); break;
          case "name-desc": filteredProducts.sort((a, b) => b.name.localeCompare(a.name)); break;
          case "stock-asc": filteredProducts.sort((a, b) => a.stock - b.stock); break;
          case "stock-desc": filteredProducts.sort((a, b) => b.stock - a.stock); break;
        }
    }
    
    renderTable(filteredProducts);
}

  function renderSelectedColors() {
    const container = document.getElementById("selectedColorsContainer");
    if (!container) return;
    
    container.innerHTML = "";
    if (selectedColors.length === 0) {
        container.innerHTML = `<p class="placeholder-text">Henüz renk eklenmedi.</p>`;
        return;
    }

    selectedColors.forEach((color, index) => {
        const tag = document.createElement("div");
        tag.className = "selected-item-tag";
        tag.innerHTML = `
            <span>${color.name}</span>
            <button type="button" class="btn-remove-color" data-index="${index}">&times;</button>
        `;
        container.appendChild(tag);
    });

    // Silme butonlarına olay ata
    container.querySelectorAll(".btn-remove-color").forEach(btn => {
        btn.addEventListener("click", (e) => {
            const idx = parseInt(e.target.dataset.index);
            selectedColors.splice(idx, 1);
            renderSelectedColors();
        });
    });
  }

  function renderSaleSizeInputs(product) {
      const container = document.getElementById("size-selection-container");
      container.innerHTML = ""; 
      if (product.sizes && Array.isArray(product.sizes) && product.sizes.length > 0) {
          product.sizes.forEach(sizeInfo => {
              const wrap = document.createElement("div");
              wrap.className = "size-item-sale";
              const label = document.createElement("label");
              label.className = "size-label";
              label.textContent = sizeInfo.value; 
              label.htmlFor = `sale-size-${sizeInfo.value}`;
              const input = document.createElement("input");
              input.type = "number";
              input.id = `sale-size-${sizeInfo.value}`;
              input.className = "size-quantity-input";
              input.min = "0";
              input.placeholder = "Adet";
              input.dataset.size = sizeInfo.value;
              wrap.append(label, input);
              container.appendChild(wrap);
          });
      } else {
          const wrap = document.createElement("div");
          wrap.className = "size-item-sale";
          const label = document.createElement("label");
          label.textContent = `Adet (Toplam Stok: ${product.stock})`;
          label.htmlFor = "sale-quantity-single";
          const input = document.createElement("input");
          input.type = "number";
          input.id = "sale-quantity-single";
          input.className = "size-quantity-input";
          input.min = "0";
          input.placeholder = "Adet";
          input.dataset.size = "bedensiz";
          wrap.append(label, input);
          container.appendChild(wrap);
      }
  }

const excelModal = document.getElementById('excel-import-modal');
const btnOpenExcel = document.getElementById('btn-open-excel-modal');
const excelFileInput = document.getElementById('excel-file-input');
const btnExcelNext = document.getElementById('btn-excel-next');
const btnCloseExcelX = document.getElementById('btn-close-excel-x'); // Yeni ID
const btnCancelExcel = document.getElementById('btn-cancel-excel');   // Yeni ID
let currentExcelFilename = null;

// Modal Açma
if (btnOpenExcel) {
    btnOpenExcel.addEventListener('click', () => {
        if(excelModal) {
            excelModal.style.display = 'flex';
            resetExcelModal();
        }
    });
}

// Modal Kapatma (X Butonu)
if (btnCloseExcelX) {
    btnCloseExcelX.addEventListener('click', () => {
        closeExcelModal();
    });
}

// Modal Kapatma (İptal Butonu)
if (btnCancelExcel) {
    btnCancelExcel.addEventListener('click', () => {
        closeExcelModal();
        resetExcelModal(); // İptal edince sıfırla
    });
}

function closeExcelModal() {
    if(excelModal) excelModal.style.display = 'none';
}

function resetExcelModal() {
    document.getElementById('excel-step-1').style.display = 'block';
    document.getElementById('excel-step-2').style.display = 'none';
    document.getElementById('excel-step-mapping').style.display = 'none'; // Mapping adımı
    document.getElementById('excel-step-3').style.display = 'none';
    
    // Inputu temizle
    if (excelFileInput) excelFileInput.value = "";
    
    // Dosya bilgi ekranını gizle ve sıfırla
    document.getElementById('file-info-wrapper').style.display = 'none';
    document.getElementById('upload-progress-container').style.display = 'none';
    document.getElementById('upload-progress-bar').style.width = '0%';
    
    // Butonu sıfırla ve GÖSTER
    if (btnExcelNext) {
        btnExcelNext.style.display = "inline-block"; // <--- DÜZELTME BURADA
        btnExcelNext.textContent = "Analiz Et";
        btnExcelNext.disabled = true;
        btnExcelNext.onclick = uploadStep1;
    }
    
    if (btnCancelExcel) {
        btnCancelExcel.textContent = "İptal";
        // Event listener'ları temizleyip yeniden atamak yerine klonlamak veya onclick kullanmak daha güvenli
        btnCancelExcel.onclick = () => {
            closeExcelModal();
            resetExcelModal();
        };
    }
}

function formatBytes(bytes, decimals = 2) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

if (excelFileInput) {
    excelFileInput.addEventListener('change', () => {
        const file = excelFileInput.files[0];
        
        if (file) {
            // Butonu aktif et
            if (btnExcelNext) btnExcelNext.disabled = false;
            
            // Dosya bilgilerini göster
            document.getElementById('file-info-wrapper').style.display = 'block';
            document.getElementById('selected-file-name').textContent = file.name;
            document.getElementById('selected-file-size').textContent = formatBytes(file.size);
            
            // Progress barı sıfırla (yeni dosya seçildi)
            document.getElementById('upload-progress-container').style.display = 'none';
            document.getElementById('upload-progress-bar').style.width = '0%';
        } else {
            // Dosya seçimi iptal edildiyse gizle
            document.getElementById('file-info-wrapper').style.display = 'none';
            if (btnExcelNext) btnExcelNext.disabled = true;
        }
    });
}

// Dosya seçilince butonu aktif et
if (excelFileInput) {
    excelFileInput.addEventListener('change', () => {
        if (excelFileInput.files.length > 0 && btnExcelNext) btnExcelNext.disabled = false;
    });
}

// ADIM 1: Dosyayı Yükle ve Sütunları Al
async function uploadStep1() {
    const file = excelFileInput.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);

    // UI Hazırlığı
    btnExcelNext.disabled = true;
    btnExcelNext.textContent = "Yükleniyor...";
    
    // Progress barı göster
    const progressContainer = document.getElementById('upload-progress-container');
    const progressBar = document.getElementById('upload-progress-bar');
    const percentText = document.getElementById('upload-percent-text');
    
    progressContainer.style.display = 'block';

    // XHR ile Yükleme (Progress Event için)
    const xhr = new XMLHttpRequest();
    
    xhr.upload.addEventListener("progress", (e) => {
        if (e.lengthComputable) {
            const percentComplete = Math.round((e.loaded / e.total) * 100);
            progressBar.style.width = percentComplete + '%';
            percentText.textContent = percentComplete + '%';
        }
    });

    xhr.onreadystatechange = function() {
        if (xhr.readyState === 4) { // İşlem bitti
            if (xhr.status === 200) {
                // Başarılı
                const data = JSON.parse(xhr.responseText);
                
                currentExcelFilename = data.filename;
                renderMappingUI(data.db_fields, data.excel_columns);
                
                // UI Geçişi (Yarım saniye bekle ki %100 görünsün)
                setTimeout(() => {
                    document.getElementById('excel-step-1').style.display = 'none';
                    document.getElementById('excel-step-2').style.display = 'block';
                    btnExcelNext.textContent = "İçe Aktarmayı Başlat";
                    btnExcelNext.disabled = false;
                    btnExcelNext.onclick = uploadStep2;
                }, 500);

            } else {
                // Hata
                let errorMsg = "Hata oluştu";
                try {
                    const errData = JSON.parse(xhr.responseText);
                    errorMsg = errData.error || errorMsg;
                } catch(e) {}
                showToast(errorMsg, "err");
                resetExcelModal();
            }
        }
    };

    xhr.open("POST", "/api/upload_excel_step1", true);
    xhr.send(formData);
}

// Eşleştirme Arayüzünü Çiz
function renderMappingUI(dbFields, excelColumns) {
    const container = document.querySelector('.mapping-grid');
    container.innerHTML = "";

    let optionsHtml = `<option value="">-- Seçiniz --</option>`;
    excelColumns.forEach(col => {
        optionsHtml += `<option value="${col}">${col}</option>`;
    });

    dbFields.forEach(field => {
        const row = document.createElement('div');
        row.style.gridColumn = "span 2";
        row.style.display = "flex";
        row.style.alignItems = "center";
        row.style.gap = "10px";
        row.style.borderBottom = "1px solid #eee";
        row.style.padding = "5px 0";
        
        row.innerHTML = `
            <div style="flex:1;">
                <span style="font-weight:600; font-size:0.9rem;">${field.label}</span>
                ${field.required ? '<span style="color:red">*</span>' : ''}
            </div>
            <div style="flex:1;">
                <select class="clean-select mapping-select" data-key="${field.key}" style="width:100%;">
                    ${optionsHtml}
                </select>
            </div>
        `;
        container.appendChild(row);
    });
    // Tüm select kutularını seç
    const allSelects = document.querySelectorAll('.mapping-select');

    allSelects.forEach(select => {
        select.addEventListener('change', () => {
            handleExclusiveSelects(allSelects);
        });
    });
}

function handleExclusiveSelects(allSelects) {
    // 1. Şu anda seçili olan tüm değerleri topla
    const selectedValues = new Set();
    allSelects.forEach(sel => {
        if (sel.value && sel.value !== "") {
            selectedValues.add(sel.value);
        }
    });

    // 2. Her bir select kutusunu gez
    allSelects.forEach(sel => {
        // O select kutusunun içindeki tüm optionları gez
        const options = sel.querySelectorAll('option');
        
        options.forEach(opt => {
            // Boş seçenek (-- Seçiniz --) her zaman açık kalsın
            if (opt.value === "") return;

            // Eğer bu opsiyonun değeri "selectedValues" listesinde varsa
            // VE bu opsiyon, şu an işlem yaptığımız select kutusunun kendi seçimi DEĞİLSE
            // O zaman bu opsiyonu kapat (disabled yap).
            if (selectedValues.has(opt.value) && sel.value !== opt.value) {
                opt.disabled = true;
                // Görsel olarak da belli edelim (Opsiyonel: Parantez içinde kullanıldı yazısı)
                // opt.textContent = opt.value + " (Seçildi)"; 
                opt.style.color = "#ccc"; // Gri yap
            } else {
                // Değilse aç
                opt.disabled = false;
                // opt.textContent = opt.value;
                opt.style.color = "#333"; // Normal renk
            }
        });
    });
}

// ADIM 2: Eşleştirmeyi Gönder ve Kaydet
async function uploadStep2() {
    const selects = document.querySelectorAll('.mapping-select');
    columnMappingData = {}; // Global değişkene ata
    
    selects.forEach(sel => {
        const key = sel.getAttribute('data-key');
        const val = sel.value;
        if (val) columnMappingData[key] = val;
    });

    if (!columnMappingData['db_name']) {
        return showToast("Lütfen 'Ürün Adı' alanını eşleştirin.", "err");
    }

    // Butonu yükleniyor yap
    const btn = document.getElementById('btn-excel-next');
    btn.disabled = true;
    btn.textContent = "Veriler Analiz Ediliyor...";

    try {
        const res = await fetch('/api/upload_excel_step2', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                filename: currentExcelFilename,
                mapping: columnMappingData
            })
        });
        const data = await res.json();

        if (res.ok) {
            const analysis = data.analysis;
            
            // Eğer eksik değer varsa "Value Mapping" ekranını aç
            if (analysis.missing_categories.length > 0 || 
                analysis.missing_shelves.length > 0 || 
                analysis.missing_colors.length > 0 ||
                analysis.missing_sizes.length > 0) { // <-- YENİ
                
                renderValueMappingUI(analysis);
                
                document.getElementById('excel-step-2').style.display = 'none';
                document.getElementById('excel-step-mapping').style.display = 'block';
                
                btn.textContent = "Kaydı Tamamla";
                btn.disabled = false;
                btn.onclick = uploadStep3; 
            } else {
                uploadStep3(); 
            }
        } else {
            showToast(data.message, "err");
            btn.disabled = false;
            btn.textContent = "Devam Et";
        }
    } catch (e) {
        showToast("Analiz hatası", "err");
        btn.disabled = false;
        btn.textContent = "Devam Et";
    }
}

function renderValueMappingUI(analysis) {
    const container = document.getElementById('value-mapping-container');
    container.innerHTML = "";
    
    const createSection = (title, missingItems, validItems, typeKey) => {
        if (missingItems.length === 0) return;
        
        let html = `<h4 style="margin:15px 0 10px 0; color:#374151; font-size:0.95rem; border-bottom:1px solid #eee; padding-bottom:5px;">${title}</h4>`;
        
        let options = `<option value="" disabled selected>-- Seçiniz --</option>`;
        validItems.forEach(item => {
            options += `<option value="${item}">${item}</option>`;
        });

        missingItems.forEach(missing => {
            html += `
                <div style="display:flex; align-items:center; margin-bottom:10px; gap:10px;">
                    <div style="flex:1; font-weight:600; color:#EF4444;">"${missing}"</div>
                    <div style="flex:0 auto;">➜</div>
                    <div style="flex:1;">
                        <select class="clean-select val-map-select" data-type="${typeKey}" data-source="${missing}" style="width:100%;">
                            ${options}
                        </select>
                    </div>
                </div>
            `;
        });
        container.insertAdjacentHTML('beforeend', html);
    };

    createSection("Bilinmeyen Kategoriler", analysis.missing_categories, analysis.valid_categories, "categories");
    createSection("Bilinmeyen Raflar", analysis.missing_shelves, analysis.valid_shelves, "shelves");
    createSection("Bilinmeyen Renkler", analysis.missing_colors, analysis.valid_colors, "colors");
    createSection("Bilinmeyen Bedenler", analysis.missing_sizes, analysis.valid_sizes, "sizes");
}

// ADIM 3: Final Kayıt
async function uploadStep3() {
    // Value Mapping verilerini topla
    // sizes objesini de ekliyoruz
    const valMapping = { categories: {}, shelves: {}, colors: {}, sizes: {} };
    const selects = document.querySelectorAll('.val-map-select');
    let allSelected = true;

    selects.forEach(sel => {
        const type = sel.getAttribute('data-type'); // categories, shelves, colors, SIZES
        const source = sel.getAttribute('data-source'); 
        const target = sel.value; 
        
        if (!target) allSelected = false;
        valMapping[type][source] = target;
    });

    if (!allSelected) {
        return showToast("Lütfen tüm kırmızı alanları eşleştirin.", "err");
    }

    // UI Güncelleme
    document.getElementById('excel-step-mapping').style.display = 'none';
    document.getElementById('excel-step-2').style.display = 'none'; // Eğer direkt geçiş olursa diye
    document.getElementById('excel-step-3').style.display = 'block';
    
    const btn = document.getElementById('btn-excel-next');
    btn.style.display = 'none'; // Butonu gizle

    try {
        const res = await fetch('/api/upload_excel_step3', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                filename: currentExcelFilename,
                col_mapping: columnMappingData, // Adım 2'den gelen
                val_mapping: valMapping         // Adım 2.5'ten gelen
            })
        });
        const result = await res.json();
        
        // ... (Sonuç ekranı işlemleri aynı: ikon gösterimi, metin güncelleme vb.) ...
        const loadingIcon = document.getElementById('import-loading-icon');
        const successIcon = document.getElementById('import-success-icon');
        const statusText = document.getElementById('import-status-text');
        const detailText = document.getElementById('import-result-detail');

        loadingIcon.style.display = 'none';

        if (res.ok && result.status === 'ok') {
            successIcon.style.display = 'inline-block';
            statusText.textContent = "Başarıyla Tamamlandı!";
            statusText.style.color = "#10B981";
            
            // --- YENİ: EKLENENLER LİSTESİ ---
            let successHtml = `<p style="margin-bottom:10px;">Toplam <b>${result.added}</b> ürün eklendi.</p>`;
            
            if (result.added_names && result.added_names.length > 0) {
                successHtml += `<div style="text-align:left; background:#F0FDF4; border:1px solid #BBF7D0; padding:10px; border-radius:8px; max-height:150px; overflow-y:auto;">
                    <ul style="margin:0; padding-left:20px; font-size:0.9rem; color:#166534;">`;
                
                result.added_names.forEach(name => {
                    successHtml += `<li>${name}</li>`;
                });
                
                successHtml += `</ul></div>`;
            }
            detailText.innerHTML = successHtml;
            // ---------------------------------

            if(typeof loadProducts === 'function') loadProducts();
            
        } else if (result.status === 'partial_error') {
            // ... (Kısmi hata bloğu aynı kalabilir veya oraya da eklenebilir) ...
             successIcon.className = "ri-alert-line";
             successIcon.style.color = "#F59E0B";
             successIcon.style.display = 'inline-block';
             statusText.textContent = "Tamamlandı (Hatalı Satırlar Var)";
             
             let errHtml = `<b>${result.added}</b> ürün eklendi.<br>`;
             
             // Eklenenleri burada da gösterelim (Opsiyonel)
             if (result.added_names && result.added_names.length > 0) {
                 errHtml += `<div style="text-align:left; margin-bottom:10px;"><small style="color:#10B981">Eklenenler: ${result.added_names.slice(0,3).join(", ")}...</small></div>`;
             }

             errHtml += `<strong>Hatalar:</strong><div style="text-align:left; max-height:100px; overflow-y:auto; background:#FEF2F2; border:1px solid #FECACA; padding:10px; font-size:0.85rem; color:#B91C1C;">`;
             result.errors.forEach(e => errHtml += `• ${e}<br>`);
             errHtml += `</div>`;
             
             detailText.innerHTML = errHtml;
             if(typeof loadProducts === 'function') loadProducts();
        } else {
             statusText.textContent = "Hata";
             detailText.textContent = result.message;
        }
        
        // Kapat butonu mantığı
        if(btnCancelExcel) {
             btnCancelExcel.textContent = "Kapat";
             btnCancelExcel.onclick = () => { closeExcelModal(); resetExcelModal(); };
        }

    } catch (e) {
        showToast("Sunucu hatası", "err");
    }
}

// ============================================================
  // DEPOYA YERLEŞTİRME (BEDEN DETAYLI - YENİ SİSTEM)
  // ============================================================

  const tabDepo = document.querySelector('.tab-link[data-tab="depoya-yerlestir"]');
  const elRafUrunKodu = document.getElementById("raf-urun-kodu");
  const elRafKodu = document.getElementById("raf-kodu-sec");
  const elShelfSizesContainer = document.getElementById("shelf-sizes-container");
  const elRafIsimDisplay = document.getElementById("shelf-product-name-display");
  const elFormRafEkle = document.getElementById("form-raf-ekle");
  
  let currentProductSizes = []; // O an işlem yapılan ürünün beden listesi

  // 1. Sekme Odaklanması
  if (tabDepo && elRafUrunKodu) {
      tabDepo.addEventListener("click", () => {
          setTimeout(() => elRafUrunKodu.focus(), 100);
      });
  }

  // 2. Otomatik Arama ve Beden Inputlarını Oluşturma
  let rafSearchTimer;
  if (elRafUrunKodu) {
      elRafUrunKodu.addEventListener("input", () => {
          clearTimeout(rafSearchTimer);
          const code = elRafUrunKodu.value.trim();

          if (!code) {
              elRafIsimDisplay.textContent = "";
              elShelfSizesContainer.innerHTML = '<p style="text-align:center; color:#9CA3AF;">Önce ürün kodunu girin.</p>';
              return;
          }

          rafSearchTimer = setTimeout(async () => {
              try {
                  elRafIsimDisplay.innerHTML = '<i class="ri-loader-4-line ri-spin"></i> Aranıyor...';
                  const res = await fetch(`/query_product?code=${encodeURIComponent(code)}`);
                  
                  if (res.ok) {
                      const data = await res.json();
                      elRafIsimDisplay.innerHTML = `<i class="ri-check-line"></i> ${data.name}`;
                      elRafIsimDisplay.style.color = "#10B981";
                      
                      // Ürünün Bedenlerini Kaydet ve Inputları Oluştur
                      // data.sizes yapısı: [{"value": "S", ...}, {"value": "M", ...}]
                      currentProductSizes = data.sizes || [];
                      renderShelfSizeInputs(currentProductSizes);

                      if (elRafKodu) elRafKodu.focus();
                  } else {
                      elRafIsimDisplay.innerHTML = `<i class="ri-error-warning-line"></i> Ürün bulunamadı!`;
                      elRafIsimDisplay.style.color = "#EF4444";
                      elShelfSizesContainer.innerHTML = '<p style="text-align:center; color:#EF4444;">Ürün verisi alınamadı.</p>';
                  }
              } catch (e) {
                  elRafIsimDisplay.textContent = "Bağlantı hatası.";
              }
          }, 500);
      });
      
      // Enter ile Raf Koduna Geç
      elRafUrunKodu.addEventListener("keydown", (e) => {
          if (e.key === "Enter") {
              e.preventDefault();
              if (elRafKodu) elRafKodu.focus();
          }
      });
  }

  // Beden Inputlarını Oluşturan Fonksiyon
  function renderShelfSizeInputs(sizes) {
    const container = document.getElementById("shelf-sizes-container");
    container.innerHTML = "";
    
    // Eğer ürünün bedeni yoksa (Standart ürün)
    if (!sizes || sizes.length === 0) {
        container.innerHTML = `
          <div style="display:flex; align-items:center; justify-content:space-between; padding:10px; background:white; border-radius:6px; border:1px solid #eee;">
              <span style="font-weight:600; color:#374151;">Adet (STD)</span>
              <input type="number" class="clean-inp shelf-size-input" data-size="STD" min="1" placeholder="0" style="width:120px; text-align:center; font-weight:bold;">
          </div>
        `;
        return;
    }

    // Grid yapısı oluştur (Yan yana 2 kutu sığacak şekilde)
    const grid = document.createElement("div");
    grid.style.display = "grid";
    grid.style.gridTemplateColumns = "1fr 1fr";
    grid.style.gap = "10px";

    // Her beden için bir input oluştur
    sizes.forEach(s => {
        const wrapper = document.createElement("div");
        wrapper.style.display = "flex";
        wrapper.style.flexDirection = "column";
        wrapper.style.background = "#fff";
        wrapper.style.border = "1px solid #E5E7EB";
        wrapper.style.borderRadius = "6px";
        wrapper.style.padding = "8px";

        wrapper.innerHTML = `
          <span style="font-size:0.8rem; font-weight:700; color:#6B7280; margin-bottom:4px; text-align:center;">${s.value}</span>
          <input type="number" class="clean-inp shelf-size-input" data-size="${s.value}" min="0" placeholder="0" style="text-align:center; font-weight:bold; color:#1F2937;">
        `;
        grid.appendChild(wrapper);
    });

    container.appendChild(grid);
    
}

  // Raf Kodunda Enter -> İlk Beden Inputuna Odaklan
  if (elRafKodu) {
      elRafKodu.addEventListener("keydown", (e) => {
          if (e.key === "Enter") {
              e.preventDefault();
              const firstInput = elShelfSizesContainer.querySelector("input");
              if (firstInput) firstInput.focus();
          }
      });
  }

  // 3. Kaydetme İşlemi (Detaylı Dağılım)
  let isSubmittingShelf = false;

  if (elFormRafEkle) {
      elFormRafEkle.addEventListener("submit", async (e) => {
          e.preventDefault();
          if (isSubmittingShelf) return;

          const code = elRafUrunKodu.value.trim();
          const shelf = elRafKodu.value.trim();
          
          // Beden adetlerini topla
          const sizeInputs = elShelfSizesContainer.querySelectorAll(".shelf-size-input");
          let quantities = {};
          let totalQty = 0;

          sizeInputs.forEach(inp => {
              const val = parseInt(inp.value) || 0;
              if (val > 0) {
                  quantities[inp.dataset.size] = val;
                  totalQty += val;
              }
          });

          if (!code || !shelf || totalQty <= 0) {
              return showToast("Lütfen raf kodunu ve en az bir ürün adedini girin.", "err");
          }

          isSubmittingShelf = true;
          const btn = elFormRafEkle.querySelector("button[type='submit']");
          const originalText = btn.innerHTML;
          btn.disabled = true;
          btn.innerHTML = '<i class="ri-loader-4-line ri-spin"></i> Kaydediliyor...';

          try {
              const res = await fetch('/add_stock_to_shelf', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({
                      product_code: code,
                      shelf_code: shelf,
                      quantities: quantities // <-- ARTIK JSON GÖNDERİYORUZ
                  })
              });
              
              const result = await res.json();
              
              if (res.ok) {
                  showToast(result.message, "ok");
                  
                  // Formu temizle
                  elRafUrunKodu.value = "";
                  elRafIsimDisplay.textContent = "";
                  elShelfSizesContainer.innerHTML = '<p style="text-align:center; color:#9CA3AF;">Önce ürün kodunu girin.</p>';
                  
                  elRafUrunKodu.focus();
                  if(typeof loadProducts === 'function') loadProducts();
              } else {
                  showToast(result.message, "err");
              }
          } catch (err) {
              showToast("Sunucu hatası!", "err");
          } finally {
              isSubmittingShelf = false;
              btn.disabled = false;
              btn.innerHTML = originalText;
          }
      });
  }
// Mevcut Tüm Ürünleri Yeni Kurallara Göre Yeniden Kodlama İşlemi
const btnRegenerateAll = document.getElementById('btn-regenerate-all');
if (btnRegenerateAll) {
    btnRegenerateAll.addEventListener('click', async () => {
        const confirmMsg = "DİKKAT! \n\nSistemdeki tüm ürünlerin barkodları yeni kurallara göre BAŞTAN oluşturulacaktır.\n\nBu işlem geri alınamaz. Devam etmek istediğinize emin misiniz?";
        if (!confirm(confirmMsg)) return;

        const originalText = btnRegenerateAll.innerHTML;
        btnRegenerateAll.innerHTML = '<i class="ri-loader-4-line ri-spin"></i> Tüm Ürünler Güncelleniyor (Lütfen bekleyin)...';
        btnRegenerateAll.disabled = true;

        try {
            const res = await fetch('/api/regenerate_all_barcodes', { 
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            const data = await res.json();
            
            if (res.ok && data.status === 'ok') {
                showToast(data.message, "ok");
                if (typeof loadProducts === 'function') loadProducts();
            } else {
                showToast(data.message || "Güncelleme başarısız", "err");
            }
        } catch (e) {
            console.error(e);
            showToast("Sunucu ile bağlantı koptu veya işlem çok uzun sürdü.", "err");
        } finally {
            btnRegenerateAll.innerHTML = originalText;
            btnRegenerateAll.disabled = false;
        }
    });
}

  // ==========================================
  // ANLIK YAZARAK SORGULAMA (DEBOUNCE)
  // ==========================================
  let typingTimer;                // Zamanlayıcı değişkeni
  const doneTypingInterval = 500; // Bekleme süresi (ms)

  if (kodInputSorgu) {
    kodInputSorgu.addEventListener('input', () => {
        clearTimeout(typingTimer);
        
        if (kodInputSorgu.value.trim()) {
            typingTimer = setTimeout(() => {
                // Formu gönder (Yukarıdaki otomatik ekleme kodu çalışacak)
                if (formSorgu) {
                    formSorgu.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
                }
            }, doneTypingInterval);
        }
    });
  }
  const defaultCurrencyBtn = document.querySelector('.currency-btn[data-currency="USD"]');
  if (defaultCurrencyBtn) {
      defaultCurrencyBtn.click(); // USD butonuna tıkla ve aktif et
  }

  // Sekmelerin üzerinde fare tekerleği (Mouse Wheel) ile yatay kaydırma
  const tabsNav = document.querySelector('.tabs-navigation');
  if (tabsNav) {
      tabsNav.addEventListener('wheel', (e) => {
          if (e.deltaY !== 0) {
              e.preventDefault(); // Sayfanın aşağı kaymasını engelle
              tabsNav.scrollLeft += e.deltaY; // Menüyü sağa/sola kaydır
          }
      });
  }
// ============================================================
  // DİNAMİK ÜRÜN KODU ÖNİZLEME MOTORU
  // Barkod kuralları sekmesinden gelen 'currentBarcodeRules' dizisini okur.
  // ============================================================

  const previewCodeElement = document.getElementById('preview-product-code');
  const previewBox = document.querySelector('.generated-code-preview');

  function updateProductCodePreview() {
      if (!previewCodeElement || !previewBox) return;

      // Eğer kural ayarlanmamışsa varsayılan metin göster
      if (!currentBarcodeRules || currentBarcodeRules.length === 0) {
          previewCodeElement.textContent = "SABİT FORMAT YOK";
          return;
      }

      let finalCode = "";
      let allRequiredFilled = true;

      currentBarcodeRules.forEach(rule => {
          let part = "";

          if (rule.type === 'static') {
              // Sabit değer (Örn: "TP0")
              part = rule.value || "";
          } 
          else if (rule.type === 'random') {
              // Rastgele sayı (Önizlemede belli olması için XXXX koyuyoruz)
              part = "XXXX"; 
          } 
          else if (rule.type === 'category') {
              // Kategori Kodu
              const catSelect = document.getElementById('categorySelect');
              if (catSelect && catSelect.value) {
                  // CATEGORIES global dizisinden kategorinin kodunu bul
                  const catObj = CATEGORIES.find(c => c.name === catSelect.value);
                  if (catObj && catObj.code) {
                      part = catObj.code.replace(/\s+/g, '').toUpperCase();
                  } else {
                      // Kodu yoksa adının ilk 3 harfini al
                      part = catSelect.value.replace(/\s+/g, '').substring(0, 3).toUpperCase();
                  }
              } else {
                  allRequiredFilled = false;
              }
          } 
          else if (rule.type === 'color') {
              // Renk Kodu
              const colSelect = document.getElementById('colorSelect');
              if (colSelect && colSelect.value) {
                  part = colSelect.value.replace(/\s+/g, '').toUpperCase();
              } else {
                  allRequiredFilled = false;
              }
          } 
          else if (rule.type === 'custom_attr') {
              // Özel Nitelik Kodu (Örn: Kumaş Türü)
              const attrName = rule.attr_name;
              const dynamicSelect = document.querySelector(`.dynamic-attr-input[data-key="${attrName}"]`);
              
              if (dynamicSelect && dynamicSelect.value) {
                  // Seçilen option'ın metninden kodu çıkarıyoruz (Örn: "Pamuklu [02]" -> "02")
                  const selectedOptionText = dynamicSelect.options[dynamicSelect.selectedIndex].text;
                  const match = selectedOptionText.match(/\[(.*?)\]/);
                  
                  if (match && match[1]) {
                      part = match[1].replace(/\s+/g, '').toUpperCase();
                  } else {
                      part = dynamicSelect.value.replace(/\s+/g, '').substring(0, 3).toUpperCase();
                  }
              } else {
                  allRequiredFilled = false;
              }
          }

          // Hiçbir aracı (tire, boşluk vb.) olmadan birleştir
          finalCode += part;
      });

      // Ekrana yazdır
      previewCodeElement.textContent = finalCode || "---";

      // Eğer kuraldaki tüm seçimler yapıldıysa kutuyu yeşile çevir
      if (allRequiredFilled && finalCode.length > 0) {
          previewBox.classList.add('has-code');
      } else {
          previewBox.classList.remove('has-code');
      }
  }

  // Olay Dinleyicileri (Event Listeners) doğrudan ana bloğa eklenir
  if(categorySelect) categorySelect.addEventListener('change', updateProductCodePreview);
  if(colorSelect) colorSelect.addEventListener('change', updateProductCodePreview);

  // Dinamik oluşturulan özel nitelikler (Ek Özellikler) için Event Delegation kullanıyoruz
  document.body.addEventListener('change', (e) => {
      if (e.target && e.target.classList.contains('dynamic-attr-input')) {
          updateProductCodePreview();
      }
  });

  // Kurallar veya Ek özellikler ekrana çizildiğinde önizlemeyi anında tetikle
  const originalRenderBarcodeRules = renderBarcodeRules;
  renderBarcodeRules = function() {
      if(typeof originalRenderBarcodeRules === 'function') originalRenderBarcodeRules();
      updateProductCodePreview();
  };

  const originalRenderProductFormInputs = renderProductFormInputs;
  renderProductFormInputs = function(attributes) {
      if(typeof originalRenderProductFormInputs === 'function') originalRenderProductFormInputs(attributes);
      updateProductCodePreview();
  };
// ============================================================
// TERSİNE BARKOD ÇÖZÜMLEYİCİ (REVERSE PARSER)
// Okutulan barkodu kurallara göre parçalayıp formu doldurur
// ============================================================


    const quickBarcodeInput = document.getElementById('quick-barcode-input');
    const quickFeedback = document.getElementById('quick-barcode-feedback');

    if (!quickBarcodeInput) return;

    // Barkod okuyucu (tabanca) genellikle okuduktan sonra "Enter" tuşuna basar.
    quickBarcodeInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            const scannedCode = quickBarcodeInput.value.trim().toUpperCase();
            if (scannedCode) {
                parseAndFillForm(scannedCode);
            }
        }
    });

    // Kullanıcı elle yazarsa diye input dinleyicisi (Yarım saniye bekler)
    let parseTimer;
    quickBarcodeInput.addEventListener('input', () => {
        clearTimeout(parseTimer);
        const scannedCode = quickBarcodeInput.value.trim().toUpperCase();
        
        if (scannedCode.length > 3) {
            parseTimer = setTimeout(() => {
                parseAndFillForm(scannedCode);
            }, 600); // 600ms yazmayı bırakmasını bekle
        } else {
            quickFeedback.innerHTML = ""; // Input boşsa mesajı temizle
        }
    });

    // Çözümleme Fonksiyonu
    function parseAndFillForm(code) {
        if (!currentBarcodeRules || currentBarcodeRules.length === 0) {
            showFeedback("Barkod kuralları tanımlanmamış. Analiz edilemiyor.", "error");
            return;
        }

        let cursor = 0; // Metin içinde nerede olduğumuzu takip eden imleç
        let parsedData = {};
        let isValid = true;

        // Kuralları soldan sağa doğru tara
        for (const rule of currentBarcodeRules) {
            if (!isValid) break; // Kural bozuldusa döngüyü durdur

            // 1. SABİT YAZI KURALI
            if (rule.type === 'static') {
                const staticVal = rule.value.toUpperCase();
                if (code.startsWith(staticVal, cursor)) {
                    cursor += staticVal.length; // İmleci kaydır
                } else {
                    isValid = false; // Eşleşmedi, demek ki barkod hatalı
                }
            }
            // 2. RASTGELE SAYI KURALI (Veritabanınızda 4 haneli üretiliyor: 1000-9999)
            else if (rule.type === 'random') {
                const randomPart = code.substring(cursor, cursor + 4);
                if (/^\d{4}$/.test(randomPart)) {
                    cursor += 4;
                } else {
                    isValid = false;
                }
            }
            // 3. KATEGORİ KURALI
            else if (rule.type === 'category') {
                let matched = false;
                for (const cat of CATEGORIES) {
                    const catCode = (cat.code || cat.name.substring(0, 3)).replace(/\s+/g, '').toUpperCase();
                    if (code.startsWith(catCode, cursor)) {
                        parsedData.category = cat.name; // Seçilecek value
                        cursor += catCode.length;
                        matched = true;
                        break;
                    }
                }
                if (!matched) isValid = false;
            }
            // 4. RENK KURALI
            else if (rule.type === 'color') {
                let matched = false;
                const colorOptions = document.getElementById('colorSelect').options;
                for (let i = 0; i < colorOptions.length; i++) {
                    const optVal = colorOptions[i].value;
                    if (optVal) {
                        const colCode = optVal.replace(/\s+/g, '').toUpperCase();
                        if (code.startsWith(colCode, cursor)) {
                            parsedData.color = optVal;
                            cursor += colCode.length;
                            matched = true;
                            break;
                        }
                    }
                }
                if (!matched) isValid = false;
            }
            // 5. ÖZEL NİTELİK KURALI (Kumaş vb.)
            else if (rule.type === 'custom_attr') {
                let matched = false;
                const attrSelect = document.querySelector(`.dynamic-attr-input[data-key="${rule.attr_name}"]`);
                if (attrSelect) {
                    for (let i = 0; i < attrSelect.options.length; i++) {
                        const opt = attrSelect.options[i];
                        if (opt.value) {
                            // Metinden kodu çıkar: "Pamuklu [02]" -> "02"
                            const match = opt.text.match(/\[(.*?)\]/);
                            const attrCode = match ? match[1].replace(/\s+/g, '').toUpperCase() : opt.value.substring(0, 3).toUpperCase();
                            
                            if (code.startsWith(attrCode, cursor)) {
                                parsedData[rule.attr_name] = opt.value;
                                cursor += attrCode.length;
                                matched = true;
                                break;
                            }
                        }
                    }
                }
                if (!matched) isValid = false;
            }
        }

        // Bütün kurallar geçildi VE string'in sonuna gelindiyse (Arta kalan çöp veri yoksa)
        if (isValid && cursor === code.length) {
            applyDataToForm(parsedData);
            showFeedback(`<i class="ri-checkbox-circle-fill"></i> Barkod doğrulandı! Form otomatik dolduruldu.`, "success");
        } else {
            showFeedback(`<i class="ri-error-warning-fill"></i> Bu barkod sisteminizdeki kurallara veya tanımlara uymuyor!`, "error");
        }
    }

    // Çözümlenen verileri formdaki Select kutularına otomatik seçtir
    function applyDataToForm(data) {
        // Kategoriyi seç
        if (data.category) {
            const catSelect = document.getElementById('categorySelect');
            if (catSelect) {
                catSelect.value = data.category;
                catSelect.dispatchEvent(new Event('change')); // Bedenleri tetikle
            }
        }

        // Rengi seç ve listeye ekle
        if (data.color) {
            const colSelect = document.getElementById('colorSelect');
            if (colSelect) {
                colSelect.value = data.color;
                // Ekle butonuna otomatik tıkla (Eğer daha önce eklenmediyse)
                const addBtn = document.getElementById('addColorBtn');
                if (addBtn && !selectedColors.some(c => c.code === data.color)) {
                    addBtn.click();
                }
            }
        }

        // Ek özellikleri (Kumaş vb.) seç
        for (const key in data) {
            if (key !== 'category' && key !== 'color') {
                const attrSelect = document.querySelector(`.dynamic-attr-input[data-key="${key}"]`);
                if (attrSelect) {
                    attrSelect.value = data[key];
                    attrSelect.dispatchEvent(new Event('change')); // Alt alanda barkod önizlemesini tetikler
                }
            }
        }
    }

    // Geri bildirim mesajını boyar
    function showFeedback(msg, type) {
        if (type === "success") {
            quickFeedback.style.color = "#059669"; // Yeşil
            quickBarcodeInput.style.borderColor = "#10B981";
            quickBarcodeInput.style.backgroundColor = "#F0FDF4";
        } else {
            quickFeedback.style.color = "#DC2626"; // Kırmızı
            quickBarcodeInput.style.borderColor = "#EF4444";
            quickBarcodeInput.style.backgroundColor = "#FEF2F2";
        }
        quickFeedback.innerHTML = msg;
    }
});
