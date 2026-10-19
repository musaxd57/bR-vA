document.addEventListener("DOMContentLoaded", () => {
    // --- DOM Elementleri ---
    const tableBody = document.getElementById("debt-table-body");
    const searchInput = document.getElementById("debt-search-input");
    
    const paymentModal = document.getElementById("payment-modal");
    const paymentForm = document.getElementById("payment-form");
    const cancelPaymentBtn = document.getElementById("cancel-payment");
    const modalCustomerName = document.getElementById("modal-customer-name");
    const customerIdInput = document.getElementById("payment-customer-id");
    const currentDebtDisplay = document.getElementById("current-debt-display");

    const historyModal = document.getElementById("history-modal");
    const historyCustomerName = document.getElementById("history-customer-name");
    const historyTableBody = document.getElementById("history-table-body");
    const closeHistoryBtn = document.getElementById("close-history-modal");

    let allCustomers = [];

    // --- Fonksiyonlar ---

    /**
     * Sunucudan tüm müşterilerin borç bilgilerini çeker ve tabloyu günceller.
     * Bu fonksiyon sayfa yüklendiğinde çalışır.
     */
    async function fetchCustomers() {
        try {
            const response = await fetch('/get_customers_with_debt');
            if (!response.ok) {
                showToast("Müşteri verileri sunucudan alınamadı.", "err");
                tableBody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: red;">Müşteriler yüklenemedi. Sunucu hatası: ${response.status}</td></tr>`;
                return;
            }
            const data = await response.json();
            if (data.success) {
                allCustomers = data.customers;
                renderTable(allCustomers);
            } else {
                showToast(data.message || "Bir hata oluştu.", "err");
            }
        } catch (error) {
            console.error("Müşteriler alınırken hata:", error);
            showToast("Sunucuya bağlanılamadı.", "err");
            tableBody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: red;">Sunucuya bağlanırken bir hata oluştu.</td></tr>`;
        }
    }

    /**
     * Verilen müşteri listesini HTML tablosunda gösterir.
     */
    function renderTable(customers) {
        tableBody.innerHTML = "";
        if (customers.length === 0) {
            tableBody.innerHTML = `<tr><td colspan="4" style="text-align: center; padding: 2rem;">Müşteri bulunamadı.</td></tr>`;
            return;
        }
        customers.forEach(customer => {
            const row = document.createElement("tr");
            const debtDetails = formatDebt(customer.debt);
            const hasDebt = customer.debt && Object.values(customer.debt).some(amount => amount > 0);
            
            const collectButtonHTML = hasDebt 
                ? `<button class="btn-primary btn-collect-payment" data-customer-id="${customer.id}" title="Tahsilat Yap" style="padding: 6px 12px;"><i class="ri-hand-coin-line"></i></button>`
                : '';
            
            const historyButtonHTML = `<button class="btn-secondary btn-view-history" data-customer-id="${customer.id}" title="İşlem Geçmişi" style="padding: 6px 12px;"><i class="ri-history-line"></i></button>`;

            row.innerHTML = `
                <td data-label="Müşteri Adı"><strong>${customer.name}</strong></td>
                <td data-label="Telefon">${customer.phone}</td>
                <td data-label="Borç Detayları">${debtDetails}</td>
                <td data-label="İşlemler" style="text-align: center; display: flex; gap: 8px; justify-content: center;">
                    ${collectButtonHTML}
                    ${historyButtonHTML}
                </td>
            `;
            tableBody.appendChild(row);
        });
    }

    /**
     * Borç nesnesini okunabilir bir HTML string'ine çevirir.
     */
    function formatDebt(debtObject) {
        if (!debtObject || Object.keys(debtObject).length === 0 || Object.values(debtObject).every(v => v <= 0)) {
            return `<span style="color: var(--success-color);">Borcu yok</span>`;
        }
        return Object.entries(debtObject)
            .filter(([, amount]) => amount > 0)
            .map(([currency, amount]) => 
                `<strong style="color: var(--danger-color);">${amount.toFixed(2)} ${currency}</strong>`
            ).join('<br>');
    }

    /**
     * Arama input'una yazılan değere göre müşteri listesini filtreler.
     */
    function filterCustomers() {
        const searchTerm = searchInput.value.toLowerCase().trim();
        const filtered = searchTerm
            ? allCustomers.filter(c => 
                c.name.toLowerCase().includes(searchTerm) || 
                c.phone.toLowerCase().includes(searchTerm))
            : allCustomers;
        renderTable(filtered);
    }
    
    /**
     * Tahsilat modal'ını açar ve ilgili müşteri bilgileriyle doldurur.
     */
    function openPaymentModal(customerId) {
        const customer = allCustomers.find(c => c.id === parseInt(customerId));
        if (!customer) {
            showToast("Müşteri bulunamadı!", "err");
            return;
        }
        modalCustomerName.textContent = customer.name;
        customerIdInput.value = customer.id;
        currentDebtDisplay.innerHTML = formatDebt(customer.debt);
        paymentModal.style.display = "flex";
        document.getElementById("payment-amount").focus();
    }

    /**
     * Tahsilat formu gönderildiğinde çalışır.
     */
    async function handlePaymentSubmit(event) {
        event.preventDefault();
        const formData = new FormData(paymentForm);
        const data = Object.fromEntries(formData.entries());
        try {
            const response = await fetch('/collect_payment', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });
            const result = await response.json();
            if (result.success) {
                showToast(result.message, "ok");
                closeModal(paymentModal)();
                fetchCustomers();
            } else {
                showToast(result.message || "Tahsilat kaydedilemedi.", "err");
            }
        } catch (error) {
            showToast("Sunucuya bağlanırken bir hata oluştu.", "err");
        }
    }
    
    /**
     * İşlem Geçmişi Modalını açar ve verileri çeker.
     */
    async function openHistoryModal(customerId) {
        const customer = allCustomers.find(c => c.id === parseInt(customerId));
        if (!customer) return;

        historyCustomerName.textContent = customer.name;
        historyTableBody.innerHTML = `<tr><td colspan="5" style="text-align:center;">Geçmiş yükleniyor...</td></tr>`;
        historyModal.style.display = 'flex';

        try {
            const response = await fetch(`/get_customer_debt_history/${customerId}`);
            const data = await response.json();
            if (data.success) {
                renderHistoryTable(data.history);
            } else {
                historyTableBody.innerHTML = `<tr><td colspan="5" style="text-align:center; color:red;">${data.message}</td></tr>`;
            }
        } catch (error) {
            historyTableBody.innerHTML = `<tr><td colspan="5" style="text-align:center; color:red;">Sunucu hatası!</td></tr>`;
        }
    }

    /**
     * Gelen işlem geçmişini tabloya render eder.
     */
    function renderHistoryTable(history) {
        if (history.length === 0) {
            historyTableBody.innerHTML = `<tr><td colspan="5" style="text-align:center;">Bu müşteri için işlem geçmişi bulunmuyor.</td></tr>`;
            return;
        }
        historyTableBody.innerHTML = history.map(t => {
            const isDebit = t.type === 'DEBIT';
            const typeText = isDebit ? 'Borç Eklendi' : 'Tahsilat Yapıldı';
            const amountClass = isDebit ? 'danger-color' : 'success-color';
            const amountSign = isDebit ? '-' : '+';
            const noteOrId = t.sale_id
                ? `Satış: <a href="/generate_transaction_pdf/${t.sale_id}" target="_blank" title="Satış Fişini Gör">${t.sale_id.substring(0, 8)}...</a>`
                : t.note;

            return `
                <tr>
                    <td data-label="Tarih">${t.date}</td>
                    <td data-label="İşlem Tipi" style="font-weight: 500; color: var(--${amountClass});">${typeText}</td>
                    <td data-label="Tutar" style="font-weight: 600;">${amountSign} ${t.amount.toFixed(2)} ${t.currency}</td>
                    <td data-label="Personel">${t.user}</td>
                    <td data-label="Açıklama">${noteOrId}</td>
                </tr>
            `;
        }).join('');
    }

    /**
     * Ekranda bildirim mesajı gösterir.
     */
    function showToast(msg, type = "ok") {
        const toast = document.querySelector("#toast");
        if (!toast) return;
        toast.textContent = msg;
        toast.style.background = type === "err" ? "#EF4444" : "#10B981";
        toast.style.display = "block";
        clearTimeout(toast._t);
        toast._t = setTimeout(() => (toast.style.display = "none"), 3500);
    }
    
    // --- Olay Dinleyicileri ---
    searchInput.addEventListener("input", filterCustomers);

    tableBody.addEventListener("click", (event) => {
        const button = event.target.closest("button");
        if (!button) return;
        
        const customerId = button.dataset.customerId;
        if (button.classList.contains("btn-collect-payment")) {
            openPaymentModal(customerId);
        } else if (button.classList.contains("btn-view-history")) {
            openHistoryModal(customerId);
        }
    });

    paymentForm.addEventListener("submit", handlePaymentSubmit);

    const closeModal = (modal) => () => {
        modal.style.display = 'none';
        if(modal === paymentModal) paymentForm.reset();
    };

    cancelPaymentBtn.addEventListener("click", closeModal(paymentModal));
    paymentModal.addEventListener("click", (e) => e.target === paymentModal && closeModal(paymentModal)());

    closeHistoryBtn.addEventListener("click", closeModal(historyModal));
    historyModal.addEventListener("click", (e) => e.target === historyModal && closeModal(historyModal)());

    // --- Sayfa İlk Yüklendiğinde ---
    fetchCustomers();
});