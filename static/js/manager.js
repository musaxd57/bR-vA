function showToast(msg, type = "ok") {
    const toast = document.querySelector("#toast");
    if (!toast) return;
    toast.textContent = msg;
    toast.style.background = type === "err" ? "#dc3545" : "#43a047";
    toast.style.display = "block";
    clearTimeout(toast._t);
    toast._t = setTimeout(() => toast.style.display = "none", 2000);
}

document.addEventListener("DOMContentLoaded", () => {
    // ======================================================
    // 1. DEĞİŞKEN VE ELEMENT TANIMLAMALARI
    // ======================================================

    const salesTimeFilter = document.getElementById("sales-time-filter");

    // Çalışan Ekleme ve Temel Elementler
    const ekleBtn = document.querySelector(".calisan-ekle-btn");
    const isimInput = document.querySelector("#kullaniciAdi");
    const sifreInput = document.querySelector("#passwordInput");
    const tabloBody = document.querySelector("#employee-body");
    const eyeBtn = document.querySelector("#togglePassword");
    const logoutLink = document.querySelector(".logout-link");

    // Mesaj Modal Elementleri
    const messageModal = document.getElementById('message-modal');
    const closeMessageModalBtn = document.getElementById('close-message-modal-btn');
    const sendMessageBtn = document.getElementById('send-message-btn');
    const msgEmployeeName = document.getElementById('msg-employee-name');
    const msgUserIdInput = document.getElementById('msg-user-id');
    const msgTitleInput = document.getElementById('msg-title');
    const msgContentInput = document.getElementById('msg-content');
    const msgTypeInput = document.getElementById('msg-type');

    // İstatistik Elementleri
    const totalSalesCountEl = document.getElementById("total-sales-count");
    const totalProductsSoldEl = document.getElementById("total-products-sold");
    const topProductsCanvas = document.getElementById("top-products-chart");
    const topEmployeesList = document.getElementById("top-employees-list");

    // Satış Geçmişi Filtreleme
    const salesSearchInput = document.getElementById('sales-search-input');
    const startDateInput = document.getElementById('start-date-input');
    const endDateInput = document.getElementById('end-date-input');
    const clearFiltersBtn = document.getElementById('clear-filters-btn');
    const downloadLodBtn = document.getElementById('download-lod-btn');

    // İzinler Paneli
    const permissionsModal = document.getElementById('permissions-modal');
    const closeModalBtn = document.getElementById('close-modal-btn');
    const savePermissionsBtn = document.getElementById('save-permissions-btn');
    const modalEmployeeName = document.getElementById('modal-employee-name');
    const modalUserIdInput = document.getElementById('modal-user-id');

    // Tüm Satışlar Bölümü
    const allSalesBody = document.getElementById("all-sales-body");
    const paginationControls = document.getElementById("pagination-controls");
    let currentPage = 1;

    // Grafik ve Ürün Arama Elementleri
    const currencyChartWrapper = document.getElementById('currency-chart-wrapper');
    const productChartContainer = document.getElementById('product-chart-container');
    const productChartTitle = document.getElementById('product-chart-title');
    const productSearchInput = document.getElementById('product-search-input');
    const productDatalist = document.getElementById('product-list');
    const showProductChartBtn = document.getElementById('show-product-chart-btn');
    const resetChartsBtn = document.getElementById('reset-charts-btn');

    const tabs = document.querySelectorAll(".tab-link");
    let chartInstances = {};

    // ======================================================
    // 2. STATİK EVENT LISTENER'LAR
    // ======================================================

    // Sekme (Tab) Yönetimi
    tabs.forEach(tab => {
        tab.addEventListener("click", e => {
            e.preventDefault();
            window.location.hash = tab.dataset.tab;
        });
    });

    if (salesTimeFilter) {
        salesTimeFilter.addEventListener("change", (e) => {
            const selectedFilter = e.target.value;
            renderCombinedSalesChart(selectedFilter);
        });
    }

    // Mesaj Gönderme Butonu (Modal İçindeki)
    if (sendMessageBtn) {
        sendMessageBtn.addEventListener('click', async () => {
            const userId = msgUserIdInput.value;
            const title = msgTitleInput.value.trim();
            const message = msgContentInput.value.trim();
            const type = msgTypeInput.value;

            if (!title || !message) {
                showToast("Lütfen başlık ve mesaj içeriğini doldurun.", "err");
                return;
            }

            // Butonu pasif yap ve yükleniyor göster
            const originalText = sendMessageBtn.innerHTML;
            sendMessageBtn.disabled = true;
            sendMessageBtn.innerHTML = '<i class="ri-loader-4-line ri-spin"></i> Gönderiliyor...';

            try {
                const response = await fetch('/send_employee_message', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        user_id: userId,
                        title: title,
                        message: message,
                        type: type
                    })
                });

                const result = await response.json();

                if (response.ok) {
                    showToast("Mesaj başarıyla gönderildi.", "ok");
                    messageModal.classList.add('hidden');
                    // Formu temizle
                    msgTitleInput.value = "";
                    msgContentInput.value = "";
                } else {
                    showToast(result.message || "Mesaj gönderilemedi!", "err");
                }
            } catch (error) {
                console.error("Hata:", error);
                showToast("Sunucu hatası!", "err");
            } finally {
                // Butonu eski haline getir
                sendMessageBtn.disabled = false;
                sendMessageBtn.innerHTML = originalText;
            }
        });
    }
    document.addEventListener('click', async function(e) {
      // Satış detayı aç/kapa
      const detailsToggleBtn = e.target.closest('.details-toggle-btn');
      if (detailsToggleBtn) {
        const mainRow = detailsToggleBtn.closest('tr');
        const detailsRow = mainRow.nextElementSibling;
        if (detailsRow && detailsRow.classList.contains('sales-group-details')) {
            detailsRow.classList.toggle('hidden');
            detailsToggleBtn.textContent = detailsRow.classList.contains('hidden') ? 'Detayları Gör' : 'Kapat';
        }
      }

      // İşlem fişini (PDF) indir
      const transactionPdfBtn = e.target.closest('.download-transaction-btn');
      if (transactionPdfBtn) {
          const groupId = transactionPdfBtn.dataset.groupId;
          if (groupId) window.open(`/generate_transaction_pdf/${groupId}`, '_blank');
      }

      // Tekil satış fişini (PDF) indir
      const downloadButton = e.target.closest('.download-single-btn');
      if (downloadButton) {
          const saleId = downloadButton.dataset.saleId;
          if (saleId) window.open(`/generate_single_lod_pdf/${saleId}`, '_blank');
      }



      // İzin yönetme butonu
      const manageBtn = e.target.closest('#employee-body .izin-yonet-btn');
      if (manageBtn) {
          const userRow = manageBtn.closest('tr');
          const userId = userRow.dataset.id;
          const username = userRow.dataset.username;
          modalUserIdInput.value = userId;
          modalEmployeeName.textContent = username;
          try {
              const response = await fetch(`/get_user_permissions/${userId}`);
              if (!response.ok) { showToast('İzinler alınamadı!', 'err'); return; }
              const permissions = await response.json();
              for (const key in permissions) {
                  const checkbox = document.getElementById(`perm-${key}`);
                  if (checkbox) checkbox.checked = permissions[key];
              }
              permissionsModal.classList.remove('hidden');
          } catch (error) {
              console.error('İzinler çekilirken hata:', error);
              showToast('Sunucu hatası!', 'err');
          }
      }
  });

    // Modal Kapatma İşlemleri (Mesaj Modalı)
    if (closeMessageModalBtn) {
        closeMessageModalBtn.addEventListener('click', () => messageModal.classList.add('hidden'));
    }
    if (messageModal) {
        messageModal.addEventListener('click', (e) => {
            if (e.target === messageModal) messageModal.classList.add('hidden');
        });
    }

    // Modal Kapatma İşlemleri (İzinler Modalı)
    if (closeModalBtn) closeModalBtn.addEventListener('click', () => permissionsModal.classList.add('hidden'));
    if (permissionsModal) {
        permissionsModal.addEventListener('click', (e) => {
            if (e.target === permissionsModal) permissionsModal.classList.add('hidden');
        });
    }

    // İzinleri Kaydetme Butonu
    if (savePermissionsBtn) {
        savePermissionsBtn.addEventListener('click', async () => {
            const userId = modalUserIdInput.value;
            const permissionCheckboxes = permissionsModal.querySelectorAll('.permission-item input[type="checkbox"]');
            const updatedPermissions = {};
            permissionCheckboxes.forEach(checkbox => {
                updatedPermissions[checkbox.name] = checkbox.checked;
            });
            try {
                const response = await fetch(`/update_user_permissions/${userId}`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify(updatedPermissions)
                });
                const data = await response.json();
                if (response.ok) {
                    showToast(data.message || 'İzinler güncellendi.', 'ok');
                    permissionsModal.classList.add('hidden');
                } else {
                    showToast(data.message || 'Güncelleme başarısız!', 'err');
                }
            } catch (error) {
                console.error('İzinler güncellenirken hata:', error);
                showToast('Sunucu hatası!', 'err');
            }
        });
    }

    const customerSelect = document.getElementById('customer-filter-select');
    if (customerSelect) {
        customerSelect.addEventListener('change', () => {
            // Seçim değiştiğinde direkt filtreleme fonksiyonunu çağır
            fetchPaginatedSales(1); 
        });
    }

    // Grafik Butonları
    if (showProductChartBtn) {
        showProductChartBtn.addEventListener('click', () => {
            const productCode = productSearchInput.value;
            if (!productCode) {
                showToast('Lütfen bir ürün kodu veya adı girin.', 'err');
                return;
            }
            fetchProductSalesChart(productCode);
        });
    }

    if (resetChartsBtn) {
        resetChartsBtn.addEventListener('click', () => {
            productChartContainer.classList.add('hidden');
            currencyChartWrapper.classList.remove('hidden');
            productSearchInput.value = '';
        });
    }

    let searchTimeout;
    if (salesSearchInput) {
        salesSearchInput.addEventListener('input', () => {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                fetchPaginatedSales(1);
            }, 500); 
        });
    }

    // 2. Tarih Değişiklikleri (Anlık)
    if (startDateInput) {
        startDateInput.addEventListener('change', () => {
            fetchPaginatedSales(1);
        });
    }

    if (endDateInput) {
        endDateInput.addEventListener('change', () => {
            fetchPaginatedSales(1);
        });
    }

    // 3. Müşteri Seçimi (Zaten eklemiştik, kontrol edelim)
    if (customerSelect) {
        customerSelect.addEventListener('change', () => {
            fetchPaginatedSales(1);
        });
    }

    // Filtreleme Butonları
    if (clearFiltersBtn) clearFiltersBtn.addEventListener('click', () => {
        salesSearchInput.value = '';
        startDateInput.value = '';
        endDateInput.value = '';
        
        // Select'i sıfırla
        if(customerSelect) customerSelect.value = '';

        fetchPaginatedSales(1);
    });

    // Şifre Göster/Gizle
    if (eyeBtn && sifreInput) {
        eyeBtn.addEventListener("click", () => {
            sifreInput.type = sifreInput.type === "password" ? "text" : "password";
            eyeBtn.textContent = sifreInput.type === "password" ? "👁" : "🙈";
        });
    }

    // Çıkış Linki
    if (logoutLink) logoutLink.addEventListener("click", (e) => {
        e.preventDefault();
        location.href = "logout";
    });

    // Çalışan Ekleme Butonu
    if (ekleBtn) {
        ekleBtn.addEventListener("click", async () => {
            const isim = (isimInput.value || "").trim();
            const sifre = (sifreInput.value || "").trim();
            if (!isim || !sifre) {
                showToast("Lütfen tüm alanları doldurun!", "err");
                return;
            }
            try {
                const res = await fetch("/add_user_py", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify({
                        name: isim,
                        password: sifre,
                        role: "cashier"
                    })
                });
                const data = await res.json();
                if (res.ok) {
                    showToast("Çalışan eklendi.", "ok");
                    isimInput.value = "";
                    sifreInput.value = "";
                    fetchUsers();
                } else {
                    showToast(data.msg || "Ekleme hatası!", "err");
                }
            } catch (err) {
                console.error(err);
                showToast("Sunucu hatası!", "err");
            }
        });
    }

    // LOD Raporu İndirme
    if (downloadLodBtn) {
        downloadLodBtn.addEventListener('click', async () => {
            showToast("Rapor oluşturuluyor, lütfen bekleyin...", "ok");
            const params = new URLSearchParams();
            if (salesSearchInput.value.trim()) params.append('search', salesSearchInput.value.trim());
            if (startDateInput.value) params.append('start_date', startDateInput.value);
            if (endDateInput.value) params.append('end_date', endDateInput.value);
            try {
                const response = await fetch(`/generate_lod_pdf?${params.toString()}`);
                if (!response.ok) throw new Error('Rapor oluşturulamadı.');
                const contentDisposition = response.headers.get('Content-Disposition');
                let filename = "Satis_Raporu.pdf";
                if (contentDisposition) {
                    const filenameMatch = contentDisposition.match(/filename="?(.+)"?/);
                    if (filenameMatch.length > 1) filename = filenameMatch[1];
                }
                const blob = await response.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.style.display = 'none';
                a.href = url;
                a.download = filename;
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
                a.remove();
            } catch (error) {
                console.error("PDF indirme hatası:", error);
                showToast("Rapor indirilirken bir hata oluştu!", "err");
            }
        });
    }

    // ======================================================
    // 3. DİNAMİK EVENT DELEGATION (TABLO BUTONLARI)
    // ======================================================
    document.addEventListener('click', function(e) {

        // A. "Mesaj At" Butonu (Modalı açar)
        const msgBtn = e.target.closest('.btn-message');
        if (msgBtn) {
            const tr = msgBtn.closest('tr');
            const userId = tr.dataset.id;
            const username = tr.dataset.username;

            if (msgUserIdInput) msgUserIdInput.value = userId;
            if (msgEmployeeName) msgEmployeeName.textContent = username;

            // Formu temizle
            if (msgTitleInput) msgTitleInput.value = "";
            if (msgContentInput) msgContentInput.value = "";
            if (msgTypeInput) msgTypeInput.value = "info";

            if (messageModal) messageModal.classList.remove('hidden');
            return; // Diğer kontrollere girmesin
        }

        // B. Satış Detaylarını Aç/Kapa
        const mainRow = e.target.closest('.sales-group-main');
        if (mainRow) {
            // Eğer tıklanan yer bir buton ise detayları açma
            if (e.target.closest('button')) return;

            const detailsRow = mainRow.nextElementSibling;
            if (detailsRow && detailsRow.classList.contains('sales-group-details')) {
                detailsRow.classList.toggle('hidden');
                mainRow.classList.toggle('details-open');
            }
            return;
        }

        // C. Satış Fişi İndir Butonu
        const transactionPdfBtn = e.target.closest('.download-transaction-btn');
        if (transactionPdfBtn) {
            const groupId = transactionPdfBtn.dataset.groupId;
            if (groupId) window.open(`/generate_transaction_pdf/${groupId}`, '_blank');
            return;
        }

        // D. Çalışan Silme Butonu
        const deleteBtn = e.target.closest("#employee-body .sil-btn");
        if (deleteBtn) {
            const tr = deleteBtn.closest("tr");
            const userId = tr.dataset.id;
            if (!confirm(`'${tr.children[0].textContent}' adlı çalışanı silmek istediğinize emin misiniz?`)) return;
            fetch(`/deleteuser/${userId}`, {
                    method: "DELETE"
                })
                .then(res => {
                    if (res.ok) {
                        showToast("Çalışan silindi.", "ok");
                        tr.remove();
                    } else {
                        res.json().then(data => showToast(data.message || "Silme hatası!", "err"));
                    }
                })
                .catch(err => {
                    console.error(err);
                    showToast("Sunucu hatası!", "err");
                });
            return;
        }

        // E. İzin Yönetme Butonu
        const manageBtn = e.target.closest('#employee-body .izin-yonet-btn');
        if (manageBtn) {
            const userRow = manageBtn.closest('tr');
            const userId = userRow.dataset.id;
            const username = userRow.dataset.username;
            modalUserIdInput.value = userId;
            modalEmployeeName.textContent = username;
            // Önce tüm checkboxları temizle
            const allCheckboxes = permissionsModal.querySelectorAll('input[type="checkbox"]');
            allCheckboxes.forEach(cb => cb.checked = false);

            fetch(`/get_user_permissions/${userId}`)
                .then(async response => {
                    if (!response.ok) {
                        showToast('İzinler alınamadı!', 'err');
                        return;
                    }
                    const permissions = await response.json();
                    for (const key in permissions) {
                        const checkbox = document.getElementById(`perm-${key}`);
                        if (checkbox) checkbox.checked = permissions[key];
                    }
                    permissionsModal.classList.remove('hidden');
                })
                .catch(error => {
                    console.error('İzinler çekilirken hata:', error);
                    showToast('Sunucu hatası!', 'err');
                });
        }
    });


    // ======================================================
    // 4. FONKSİYONLAR
    // ======================================================

    function createOrUpdateChart(canvasId, chartType, labels, datasets, options = {}) {
        const ctx = document.getElementById(canvasId).getContext('2d');
        if (chartInstances[canvasId]) {
            chartInstances[canvasId].destroy();
        }
        chartInstances[canvasId] = new Chart(ctx, {
            type: chartType,
            data: {
                labels,
                datasets
            },
            options: options
        });
    }

    async function renderCombinedSalesChart(filterType = "weekly") {
        try {
            const response = await fetch(`/get_daily_sales_trend?filter=${filterType}`);
            if (!response.ok) throw new Error("Satış trendi verisi alınamadı.");
            const allChartData = await response.json();

            const currencyConfig = {
                TL: {
                    label: 'Ciro (TL)',
                    borderColor: '#3b82f6',
                    pointBackgroundColor: '#3b82f6'
                },
                USD: {
                    label: 'Ciro (USD)',
                    borderColor: '#10b981',
                    pointBackgroundColor: '#10b981'
                },
                EURO: {
                    label: 'Ciro (EUR)',
                    borderColor: '#f97316',
                    pointBackgroundColor: '#f97316'
                }
            };

            const datasets = [];
            for (const currency in allChartData) {
                if (currencyConfig[currency]) {
                    datasets.push({
                        label: currencyConfig[currency].label,
                        data: allChartData[currency].data,
                        borderColor: currencyConfig[currency].borderColor,
                        pointBackgroundColor: currencyConfig[currency].pointBackgroundColor,
                        tension: 0.4,
                        fill: false
                    });
                }
            }

            if (!allChartData.TL || !allChartData.TL.labels) return;

            const labels = allChartData.TL.labels;
            const options = {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: {
                        beginAtZero: true
                    }
                },
                plugins: {
                    tooltip: {
                        mode: 'index',
                        intersect: false
                    },
                    legend: {
                        position: 'top'
                    }
                },
                interaction: {
                    mode: 'index',
                    intersect: false
                },
            };

            createOrUpdateChart('currency-sales-chart', 'line', labels, datasets, options);

        } catch (error) {
            console.error("Grafikler çizilirken hata oluştu:", error);
        }
    }

    async function fetchProductSalesChart(productCode) {
        try {
            const response = await fetch(`/get_product_sales_trend?code=${productCode}`);
            if (!response.ok) throw new Error('Ürün verisi alınamadı.');
            const productData = await response.json();

            currencyChartWrapper.classList.add('hidden');
            productChartContainer.classList.remove('hidden');
            productChartTitle.textContent = `'${productData.product_name}' Son 30 Günlük Satış Adedi`;

            const productDataset = [{
                label: 'Satış Adedi',
                data: productData.data,
                borderColor: '#8b5cf6',
                pointBackgroundColor: '#8b5cf6',
                tension: 0.4,
                fill: true
            }];

            const options = {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: {
                            callback: value => `${value} adet`
                        }
                    }
                },
                plugins: {
                    legend: {
                        display: false
                    }
                }
            };

            createOrUpdateChart('product-sales-chart', 'line', productData.labels, productDataset, options);

        } catch (error) {
            console.error('Ürün grafiği hatası:', error);
            showToast('Bu ürün için grafik verisi bulunamadı veya bir hata oluştu.', 'err');
        }
    }

    function renderTopProductsChart(products) {
        if (!topProductsCanvas) return;

        if (!products || products.length === 0) {
            const ctx = topProductsCanvas.getContext('2d');
            if (chartInstances['top-products-chart']) chartInstances['top-products-chart'].destroy();
            ctx.clearRect(0, 0, topProductsCanvas.width, topProductsCanvas.height);
            ctx.textAlign = 'center';
            ctx.fillStyle = '#718096';
            ctx.fillText('Gösterilecek ürün verisi bulunamadı.', topProductsCanvas.width / 2, topProductsCanvas.height / 2);
            return;
        }

        const labels = products.map(p => p.name);
        const data = products.map(p => p.quantity);

        const datasets = [{
            label: 'Satış Miktarı',
            data: data,
            backgroundColor: ['#3b82f6', '#10b981', '#f97316', '#8b5cf6', '#ec4899'],
            borderColor: '#ffffff',
            borderWidth: 2,
            hoverOffset: 8
        }];

        const options = {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: {
                        padding: 15,
                        font: {
                            size: 12
                        }
                    }
                },
                tooltip: {
                    callbacks: {
                        label: (context) => `${context.label || ''}: ${context.parsed} adet`
                    }
                }
            },
            cutout: '65%'
        };
        createOrUpdateChart('top-products-chart', 'doughnut', labels, datasets, options);
    }

    async function populateCustomerSelect() {
        const selectBox = document.getElementById('customer-filter-select');
        // Eğer bu eleman sayfada yoksa (örn: başka sekmedeysek) dur
        if (!selectBox) return;

        try {
            const response = await fetch('/get_all_customers');
            const data = await response.json();
            
            if (response.ok && data.success) {
                // Listeyi temizle ama ilk seçeneği koru
                selectBox.innerHTML = '<option value="">Tüm Müşteriler</option>';

                data.customers.forEach(c => {
                    const option = document.createElement('option');
                    option.value = c.name; // Backend isme göre filtreliyor
                    option.textContent = c.name;
                    selectBox.appendChild(option);
                });
            } else {
                console.warn("Müşteri listesi çekilemedi:", data.message);
            }
        } catch (error) {
            console.error('Müşteri listesi hatası:', error);
        }
    }

    async function populateProductDatalist() {
        try {
            const response = await fetch('/products');
            if (!response.ok) return;
            const products = await response.json();
            productDatalist.innerHTML = '';
            products.forEach(p => {
                const option = document.createElement('option');
                option.value = p.code;
                option.textContent = p.name;
                productDatalist.appendChild(option);
            });
        } catch (error) {
            console.error('Ürün listesi alınamadı:', error);
        }
    }

    async function fetchSalesStats() {
        try {
            const res = await fetch("/get_sales_statistics");
            const data = await res.json();
            if (!res.ok) {
                console.error(data.message);
                return;
            }

            if (data.revenues) {
                data.revenues.forEach(r => {
                    const val = r.total.toLocaleString('tr-TR', {
                        minimumFractionDigits: 2,
                        maximumFractionDigits: 2
                    });
                    const currencyKey = r.currency.toLowerCase() === 'euro' ? 'eur' : r.currency.toLowerCase();
                    const el = document.getElementById(`revenue-${currencyKey}`);
                    if (el) el.textContent = `${{'TL':'₺','USD':'$','EURO':'€'}[r.currency]}${val}`;
                });
            }
            if (totalSalesCountEl) totalSalesCountEl.textContent = data.total_sales_count || 0;
            if (totalProductsSoldEl) totalProductsSoldEl.textContent = data.total_products_sold || 0;
            renderTopProductsChart(data.top_products);
            renderTopEmployeesList(data.top_employees);
        } catch (err) {
            console.error("İstatistikleri çekerken hata oluştu:", err);
        }
    }

    function renderTable(users) {
        if (!tabloBody) return;
        tabloBody.innerHTML = "";
        if (!users || users.length === 0) {
            tabloBody.innerHTML = '<tr><td colspan="3" class="empty-row">Henüz çalışan yok</td></tr>';
            return;
        }
        users.forEach(user => {
            const tr = document.createElement("tr");
            tr.dataset.id = user.id;
            tr.dataset.username = user.username;
            tr.innerHTML = `
          <td data-label="Adı">${user.username}</td>
          <td data-label="İzinler"><button class="izin-yonet-btn btn-secondary">İzinleri Yönet</button></td>
          <td data-label="İşlem">
              <button class="btn-message btn-primary" title="Mesaj Gönder"><i class="ri-message-2-line"></i></button>
              <button class="sil-btn btn-danger" title="Sil"><i class="ri-delete-bin-line"></i></button>
          </td>`;
            tabloBody.appendChild(tr);
        });
    }

    async function fetchUsers() {
        try {
            const res = await fetch("/get_employees");
            const data = await res.json();
            if (Array.isArray(data)) renderTable(data);
            else showToast("Çalışan verisi alınamadı!", "err");
        } catch (err) {
            console.error(err);
            showToast("Sunucu hatası!", "err");
        }
    }

    function renderAllSalesTable(salesGroups) {
        if (!allSalesBody) return;
        allSalesBody.innerHTML = "";
        if (!salesGroups || salesGroups.length === 0) {
            allSalesBody.innerHTML = '<tr><td colspan="5" class="empty-row">Satış kaydı bulunamadı.</td></tr>';
            return;
        }
        salesGroups.forEach(group => {
            const mainRow = document.createElement("tr");
            mainRow.className = 'sales-group-main';
            const totalAmountFormatted = Object.entries(group.totals)
                .map(([currency, total]) => `${total.toFixed(2)} ${currency}`).join(' + ');

            mainRow.innerHTML = `
            <td data-label="Müşteri">
                <div class="main-cell-content">
                    <i class="ri-arrow-down-s-line details-icon"></i>
                    <span>${group.customer_name}</span>
                </div>
            </td>
            <td data-label="Toplam Tutar"><strong>${totalAmountFormatted}</strong></td>
            <td data-label="Satan Kişi">${group.sold_by}</td>
            <td data-label="Tarih">${group.sale_date}</td>
            <td data-label="Fiş İndir" class="action-cell">
              <button class="btn btn-primary download-transaction-btn" data-group-id="${group.sale_group_id}" title="Bu işlem fişini PDF olarak indir">
                <i class="ri-file-download-line"></i>
              </button>
            </td>
        `;

            const detailsRow = document.createElement("tr");
            detailsRow.className = 'sales-group-details hidden';
            let detailsHtml = `
            <td colspan="5"><div class="details-content">
                    <h4>İşlem Detayları (ID: ...${group.sale_group_id.slice(-12)})</h4>
                    <table><thead><tr><th>Ürün</th><th>Renk</th><th>Adet (Seri)</th><th>Beden Dökümü</th><th>Birim Fiyat</th><th>Toplam</th></tr></thead><tbody>`;
            group.items.forEach(item => {
                const sizesBreakdownStr = item.sizes_breakdown ? item.sizes_breakdown.map(s => `${s.quantity} ${s.value}`).join(' / ') : 'N/A';
                detailsHtml += `<tr>
                    <td>${item.product_name} (${item.product_code})</td>
                    <td>${item.color_name}</td>
                    <td>${item.quantity}</td>
                    <td>${sizesBreakdownStr}</td>
                    <td>${item.price_at_sale.toFixed(2)} ${item.currency}</td>
                    <td>${item.total_price.toFixed(2)} ${item.currency}</td>
                </tr>`;
            });
            detailsHtml += '</tbody></table></div></td>';
            detailsRow.innerHTML = detailsHtml;
            allSalesBody.append(mainRow, detailsRow);
        });
    }

    function renderPagination(paginationData) {
        if (!paginationControls) return;
        paginationControls.innerHTML = "";
        const {
            current_page,
            total_pages,
            has_prev,
            has_next
        } = paginationData;
        if (total_pages <= 1) return;
        const prevButton = document.createElement("button");
        prevButton.textContent = "Önceki";
        prevButton.className = "page-btn";
        prevButton.disabled = !has_prev;
        prevButton.onclick = () => {
            if (currentPage > 1) fetchPaginatedSales(currentPage - 1);
        };
        const pageInfo = document.createElement("span");
        pageInfo.className = "page-info";
        pageInfo.textContent = `Sayfa ${current_page} / ${total_pages}`;
        const nextButton = document.createElement("button");
        nextButton.textContent = "Sonraki";
        nextButton.className = "page-btn";
        nextButton.disabled = !has_next;
        nextButton.onclick = () => {
            if (currentPage < total_pages) fetchPaginatedSales(currentPage + 1);
        };
        paginationControls.append(prevButton, pageInfo, nextButton);
    }

    async function fetchPaginatedSales(page = 1) {
        try {
            const params = new URLSearchParams({
                page
            });
            if (salesSearchInput.value.trim()) params.append('search', salesSearchInput.value.trim());
            if (startDateInput.value) params.append('start_date', startDateInput.value);
            if (endDateInput.value) params.append('end_date', endDateInput.value);
            if (customerSelect && customerSelect.value) {
                // Özel 'customer' parametresi olarak gönder
                params.append('customer', customerSelect.value);
            }
            const response = await fetch(`/all_sales?${params.toString()}`);
            const data = await response.json();
            if (!response.ok) {
                showToast(data.message || "Satış verileri alınamadı!", "err");
                return;
            }
            currentPage = data.current_page;
            renderAllSalesTable(data.sales_groups);
            renderPagination(data);
        } catch (error) {
            showToast("Sunucu hatası!", "err");
        }
    }

    function renderTopEmployeesList(employees) {
        if (!topEmployeesList) return;
        topEmployeesList.innerHTML = "";
        if (!employees || employees.length === 0) {
            topEmployeesList.innerHTML = `<div class="empty-state">Çalışan verisi bulunamadı.</div>`;
            return;
        }
        employees.forEach((employee, index) => {
            const item = document.createElement("div");
            item.className = `employee-rank-item rank-${index + 1}`;
            item.innerHTML = `<span class="rank-number">${index + 1}</span><div class="employee-info"><span class="username">${employee.username}</span><span class="sales-count">${employee.sales_count} satış</span></div>`;
            topEmployeesList.appendChild(item);
        });
    }

    function activateTabFromHash() {
        const hash = window.location.hash.substring(1) || 'gosterge-paneli'; // Varsayılan sekme
        const targetTab = document.querySelector(`.tab-link[data-tab='${hash}']`);
        const targetPanel = document.getElementById(hash);

        if (targetTab && targetPanel) {
            document.querySelectorAll('.tab-link.active').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-panel.active').forEach(p => p.classList.remove('active'));
            targetTab.classList.add('active');
            targetPanel.classList.add('active');
        }
    }

    // ======================================================
    // 5. BAŞLANGIÇ (INIT)
    // ======================================================
    fetchUsers();
    fetchSalesStats();
    renderCombinedSalesChart();
    fetchPaginatedSales(1);
    populateProductDatalist();
    activateTabFromHash();
    window.addEventListener('hashchange', activateTabFromHash);
    populateCustomerSelect();
});