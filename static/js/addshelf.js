const shelfForm = document.getElementById('shelfForm');
    const newShelfInput = document.getElementById('newShelf');
    const shelvesTableBody = document.querySelector('#shelves_table tbody');

    // Rafları yükle
    async function loadShelves() {
      try {
        const res = await fetch('/get_shelves'); // backend endpoint
        if (!res.ok) return;
        const data = await res.json();
        shelvesTableBody.innerHTML = '';
        data.shelves.forEach(shelf => {
          const tr = document.createElement('tr');
          tr.innerHTML = `
            <td>${BrivaDOM.escapeHtml(shelf.code)}</td>
            <td>
              <button class="delete-btn" data-id="${BrivaDOM.escapeHtml(shelf.id)}">Sil</button>
            </td>
          `;
          shelvesTableBody.appendChild(tr);
        });
      } catch (err) {
        console.error('Raflar yüklenemedi', err);
      }
    }

    // Yeni raf ekleme
    shelfForm.addEventListener('submit', async e => {
      e.preventDefault();
      const shelfNo = newShelfInput.value;
      try {
        const res = await fetch('/add_shelf', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code: shelfNo })
        });
        if (!res.ok) throw new Error('Kaydedilemedi');
        newShelfInput.value = '';
        loadShelves();
        alert('Raf başarıyla kaydedildi ✔');
      } catch {
        alert('Raf kaydedilemedi!');
      }
    });

    // Raf silme
    shelvesTableBody.addEventListener('click', async e => {
      if (e.target.classList.contains('delete-btn')) {
        const id = e.target.dataset.id;
        if (!confirm('Bu rafı silmek istediğinizden emin misiniz?')) return;
        try {
          const res = await fetch(`/delete_shelf/${id}`, { method: "DELETE" });
          if (!res.ok) throw new Error('Silinemedi');
          loadShelves();
          alert('Raf başarıyla silindi ✔');
        } catch {
          alert('Raf silinemedi!');
        }
      }
    });

    // Sayfa açılır açılmaz rafları yükle
    loadShelves();
