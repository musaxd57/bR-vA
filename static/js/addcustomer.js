document.addEventListener("DOMContentLoaded", function () {
  const form = document.getElementById("savenewcustomer");
  const nameEl = document.getElementById("savecustomername");
  const phoneEl = document.getElementById("phone");
  const addressEl = document.getElementById("address");
  const descriptionEl = document.getElementById("description"); // açıklama (tax number aslında)
  const statusEl = document.getElementById("formStatus");
  const submitEl = document.getElementById("b89k07");
  const secretEl = document.getElementById("inputbolum");

  const tbody = document.querySelector("#customer_table tbody");

  // Telefon normalize
  function normalizePhone(input) {
    return input.replace(/\D/g, "");
  }

  function setStatus(msg, type = "info") {
    statusEl.textContent = msg;
    statusEl.className = type;
  }

  function toggleLoading(isLoading) {
    submitEl.disabled = isLoading;
    submitEl.textContent = isLoading ? "Kaydediliyor..." : "Ekle";
  }

  function addCustomerRow(customer) {
    const tr = document.createElement("tr");

    tr.innerHTML = `
      <td>${customer.name}</td>
      <td>${customer.phone}</td>
      <td>
        <button class="btn btn-danger btn-sm" data-id="${customer.id}">Sil</button>
      </td>
    `;

    tbody.appendChild(tr);
  }

  async function loadCustomers() {
    try {
      const response = await fetch("/get_all_customers");
      const data = await response.json();

      if (data.success) {
        tbody.innerHTML = "";
        data.customers.forEach(c => addCustomerRow(c));
      } else {
        setStatus("Müşteriler alınamadı.", "error");
      }
    } catch (err) {
      console.error(err);
      setStatus("Liste yüklenirken hata: " + err.message, "error");
    }
  }

  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    setStatus("", "info");

    const name = nameEl.value.trim();
    const phone = normalizePhone(phoneEl.value);
    const secret = secretEl.value.trim();

    const payLoad = {
      name,
      phone,
      secret
    };

    try {
      toggleLoading(true);

      const response = await fetch("/add_customer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payLoad)
      });

      if (!response.ok) throw new Error("Sunucu hatası: " + response.status);

      const data = await response.json();

      setStatus("Başarıyla kaydedildi!", "success");

      // Yeni eklenen kaydı tabloya da ekle
      if (data.customer) {
        addCustomerRow(data.customer);
      }

      form.reset();
    } catch (err) {
      console.error(err);
      setStatus("Kayıt sırasında hata oluştu: " + err.message, "error");
    } finally {
      toggleLoading(false);
    }
  });

  // --- TABLODA SİLME İŞLEMİ ---
  tbody.addEventListener("click", async function (event) {
    if (event.target && event.target.classList.contains("btn-danger")) {
      const button = event.target;
      const customerId = button.dataset.id;
      const row = button.closest("tr");

      if (!confirm('Bu müşteriyi silmek istediğinize emin misiniz?')) return;

      try {
        button.disabled = true;
        button.textContent = "...";

        const resp = await fetch(`/delete_customer/${customerId}`, {
          method: "DELETE"
        });

        if (!resp.ok) throw new Error("Backend silme hatası: " + resp.status);

        row.remove();
        setStatus("Müşteri silindi.", "success");
      } catch (err) {
        console.error(err);
        setStatus("Silme sırasında hata: " + err.message, "error");
        button.disabled = false;
        button.textContent = "Sil";
      }
    }
  });

  // Sayfa açıldığında mevcut müşterileri yükle
  loadCustomers();
});